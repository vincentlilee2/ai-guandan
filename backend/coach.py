# 复盘 AI 教练：只点评 User 的出牌（含 PASS），按 tactics_data.json 策略分析。
# 与停用的 coach_client.py 不同：本模块是 async 版，供 FastAPI 端点按需调用，
# 不复用旧"分析整局写 coach_advice.json + AI 决策注入"的闭环。
#
# 设计要点（2026-08 重构）：
# - System prompt 只保留人设 + 静态规则/计分，不再倾倒整个策略库——
#   策略库的触发与执行发生在 AI 每轮出牌的 user_prompt 里，教练不需要再背一遍。
# - 每手 User 出牌单独构造一个 user prompt：只附该手触发的策略要点（见
#   _triggered_rules），避免把几十条互相矛盾的规则全部塞给模型。
# - 对"明显合理"的出牌不调用 LLM，直接跳过；只对"疑似违反触发策略"的手
#   调用 LLM 让教练确认并给意见，从而保证复盘的每一条都有实质问题。
import asyncio
import json
import re
from copy import deepcopy
from typing import List, Optional

from backend.llm_config import LLMConfigManager
from backend.tactics import TACTICS_DB

# 进程内存缓存：game_id -> 上次成功分析结果（history 是只读复盘源，不落盘）
_coach_cache: dict = {}

BIG_PLAY_MARKERS = ("王", "炸", "同花顺")

# 牌面 id 如 "S11-1" / "H15-0"，点数由 rank 数字映射；红桃2 特殊。
# 内部 rank（见 models.Rank）：3..13（3..K）、14=A、15=2、20=小王、21=大王
_RANK_LABELS = {
    3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10",
    11: "J", 12: "Q", 13: "K", 14: "A", 15: "2", 20: "小王", 21: "大王",
}
_SUIT_NAMES = {"D": "♦", "C": "♣", "H": "♥", "S": "♠", "J": "🃏"}

# LLM 判为「无问题」的 mistake 表述（子串匹配；刻意不含裸"合理"，避免误伤"不合理"）
_NO_PROBLEM_MARKERS = (
    "无明显错误",
    "没有问题",
    "没有明显错误",
    "无大问题",
    "基本合理",
)


def _flatten(node) -> str:
    """把 tactics_data.json 的异构结构拍平为文本：
    二元组 ["标题","正文"] 取正文；嵌套列表/字符串/dict 递归拼接。"""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "\n".join(_flatten(v) for v in node.values())
    if isinstance(node, (list, tuple)):
        # 二元组 [标题, 正文]（如 roles.leader 的每一条）→ 只取正文
        if len(node) == 2 and isinstance(node[0], str) and isinstance(node[1], str):
            return node[1]
        parts = [_flatten(item) for item in node]
        return "\n".join(p for p in parts if p)
    return str(node)


def _card_rank(cid: str) -> int:
    try:
        return int(cid.split("-")[0][1:])
    except (ValueError, IndexError):
        return 0


def _card_label(cid: str) -> str:
    """单张牌 id → 可读 '♠A'，红桃2 加 * 标万能（H15 → ♥2*）。"""
    r = _card_rank(cid)
    if cid.startswith("J"):
        return _RANK_LABELS.get(r, "王")
    return f"{_SUIT_NAMES.get(cid[0], '')}{_RANK_LABELS.get(r, str(r))}{'*' if cid.startswith('H15-') else ''}"


def _legible_hand(hand) -> str:
    """把 ['S14-1','H2-0'] 转成可读文本：'♠A ♥2*'（* 标红桃2万能）。"""
    if not hand:
        return "[]"
    return "[" + " ".join(_card_label(c) for c in hand) + "]"


def _played_desc(cards) -> str:
    """把一手牌浓缩为『n张·点数名』（'3张·8'），用于概览。"""
    if not cards:
        return ""
    cnt = _count_ranks(cards)
    top = max(cnt, key=cnt.get) if cnt else 0
    label = _RANK_LABELS.get(top, "?")
    return f"{len(cards)}张·{label}"


def _count_ranks(cards) -> dict:
    """cards -> {rank: count}（rank 用内部数字，3..13、14=A、15=2、20=小王、21=大王）。"""
    out = {}
    for cid in cards:
        r = _card_rank(cid)
        if r:
            out[r] = out.get(r, 0) + 1
    return out


def _wild_count(hand) -> int:
    """红桃2（逢人配）张数：H15-*。"""
    return sum(1 for c in (hand or []) if c.startswith("H15-"))


def _has_bomb(hand) -> bool:
    """手牌是否存在炸弹（4 张及以上同点，或用红桃2 补强的 4 炸）。"""
    if not hand:
        return False
    cnt = _count_ranks(hand)
    wilds = _wild_count(hand)
    for rank, c in cnt.items():
        if rank in (20, 21):  # 大小王不能配炸弹
            continue
        if c >= 4 or (c >= 3 and wilds >= 1):
            return True
    return False


def _hand_summary(hand) -> str:
    """生成手牌结构摘要：'对子:33 55 ｜ 三张:888 ｜ 炸弹:8888 ｜ 单张:2 大王'。"""
    if not hand:
        return "空"
    cnt = _count_ranks(hand)
    # 红桃2 是万能牌，单独标注，不当作固定点数结构
    wilds = _wild_count(hand)
    labels = {
        3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9", 10: "10",
        11: "J", 12: "Q", 13: "K", 14: "A", 15: "2", 20: "小王", 21: "大王",
    }
    pairs, triples, quads, singles = [], [], [], []
    for rank, c in sorted(cnt.items()):
        label = labels.get(rank, str(rank))
        if c >= 4:
            quads.append(label * c)
        elif c == 3:
            triples.append(label * 3)
        elif c == 2:
            pairs.append(label * 2)
        else:
            singles.append(label)
    segs = []
    if pairs:
        segs.append("对子:" + " ".join(pairs))
    if triples:
        segs.append("三张:" + " ".join(triples))
    if quads:
        segs.append("炸弹:" + " ".join(quads))
    if singles:
        segs.append("单张:" + " ".join(singles))
    if wilds:
        segs.append(f"红桃2:{wilds}张(万能)")
    return " ｜ ".join(segs)


# ---------------------------------------------------------------- 复盘数据提取


def _hand_before_at(data: dict, at_index: int) -> dict:
    """回放推导第 at_index 手之前的全场手牌（不含本手）。复用前端 replayHands 逻辑。"""
    snapshot = {p: list(cards) for p, cards in deepcopy(data.get("initial_hands", {})).items()}
    history = data.get("history", [])
    for i in range(min(at_index, len(history))):
        move = history[i]
        if not move or move.get("action") != "PLAY" or not isinstance(move.get("cards"), list):
            continue
        removal = set(move["cards"])
        snapshot[move.get("player")] = [c for c in snapshot.get(move.get("player"), []) if c not in removal]
    return snapshot


def _big_played_before(data: dict, at_index: int) -> list:
    """扫描第 at_index 手之前已出现的大牌描述（2/A/王/炸弹）。"""
    big = []
    history = data.get("history", [])
    for i in range(at_index):
        move = history[i]
        if not move or move.get("action") != "PLAY":
            continue
        desc = move.get("desc") or ""
        cards = move.get("cards") or []
        is_big = any(marker in desc for marker in BIG_PLAY_MARKERS)
        if not is_big:
            is_big = any(_card_rank(c) >= 14 for c in cards)
        if is_big:
            big.append({"player": move.get("player"), "desc": desc})
    return big


def _last_real_play_before(history: list, at_index: int) -> Optional[dict]:
    """第 at_index 手之前最近的一手『有效出牌』（PLAY，非 ROUND_END/PASS）。"""
    for i in range(at_index - 1, -1, -1):
        m = history[i]
        if m and m.get("action") == "PLAY":
            return m
    return None


def _opponent_min_remaining(remaining: dict, opponents: list) -> int:
    vals = [remaining.get(p, 99) for p in opponents]
    return min(vals) if vals else 99


def _teammate_of_user(data: dict) -> str:
    players = data.get("players", [])
    return "PartnerBot" if "PartnerBot" in players else (players[1] if len(players) > 1 else "队友")


def extract_user_moves(data: dict) -> list:
    """提取 User 每一手（PLAY 与 PASS 都取），附当手上下文，供教练逐手分析。"""
    history = data.get("history", [])
    players = data.get("players", [])
    user_moves = []
    for i, move in enumerate(history):
        if not move or move.get("player") != "User":
            continue
        action = move.get("action")
        if action not in ("PLAY", "PASS"):
            continue
        hand_before = _hand_before_at(data, i)
        remaining = {p: len(hand_before.get(p, [])) for p in players}
        if action == "PLAY" and isinstance(move.get("cards"), list):
            played_set = set(move["cards"])
            remaining["User"] = len([c for c in hand_before.get("User", []) if c not in played_set])
        recent = [
            {"player": m.get("player"), "action": m.get("action"), "desc": m.get("desc") or ""}
            for m in history[max(0, i - 6):i]
        ]
        user_moves.append({
            "move_index": i,
            "player": "User",
            "action": action,
            "cards": move.get("cards") or [],
            "desc": move.get("desc") or ("PASS" if action == "PASS" else action),
            "hand_before": hand_before.get("User", []),
            "remaining": remaining,
            "recent_moves": recent,
            "big_played": _big_played_before(data, i),
        })
    return user_moves


# ---------------------------------------------------------------- 策略触发匹配


# 跟牌时按"对手出的牌型"触发的 situational 规则 key
_FOLLOW_PATTERN_KEYS = {
    "单张": ("pad_strategy_single", "single_priority", "hold_big_single"),
    "对子": ("pad_strategy_pair",),
    "三张": ("pad_strategy_triple",),
    "三带二": ("pad_strategy_triple_pair", "avoid_big_pair_in_fullhouse"),
    "顺子": ("pad_strategy_straight",),
    "连对": ("pad_strategy_consecutive_pairs",),
    "钢板": ("pad_strategy_plate_preserve_takeover",),
}
_BOMB_FOLLOW_KEYS = ("bomb_no_split_follow_any", "bomb_split_guard")


def _situational_text(key: str) -> str:
    return _flatten(TACTICS_DB.get("situational", {}).get(key, ""))


def _triggered_rules(m: dict, data: dict, history: list) -> list:
    """按该手 User 出牌的局势筛选真正相关的策略 key，返回 [(section, key, 正文), ...]。"""
    keys: list = []
    players = data.get("players", [])
    opponents = [p for p in players if p != "User" and p != _teammate_of_user(data)]
    hand = m.get("hand_before") or []
    hand_len = len(hand)
    remaining = m.get("remaining") or {}
    opp_min = _opponent_min_remaining(remaining, opponents)

    is_opening_mid = hand_len >= 15
    is_endgame = hand_len <= 12

    last_play = _last_real_play_before(history, m.get("move_index", 0))
    is_leader = last_play is None or last_play.get("player") == "User"

    # ---- 1. 残局（对手牌数触发 end_game_counts）
    if is_endgame:
        ec = TACTICS_DB.get("end_game_counts", {})
        for k in ("1", "2", "3", "4", "5", "6"):
            if opp_min <= int(k):
                keys.append(("end_game_counts", k, _flatten(ec.get(k))))
                break
        keys.append(("end_game_counts", "general", _flatten(ec.get("general"))))

    # ---- 2. 首发 vs 跟牌
    if is_leader:
        leader_txt = _flatten(TACTICS_DB.get("roles", {}).get("leader"))
        if leader_txt:
            keys.append(("roles", "leader", leader_txt))
        if _has_bomb(hand):
            keys.append(("specials", "bombs", _flatten(TACTICS_DB.get("specials", {}).get("bombs"))))
        if is_endgame and opp_min in (1, 2):
            keys.append(("situational", "one_vs_one_two_cards_combo_finish",
                         _situational_text("one_vs_one_two_cards_combo_finish")))
    else:
        keys.append(("roles", "follow", _flatten(TACTICS_DB.get("roles", {}).get("follow"))))
        last_type = last_play.get("desc") or ""
        for t, tk in _FOLLOW_PATTERN_KEYS.items():
            if t in last_type:
                for k in tk:
                    keys.append(("situational", k, _situational_text(k)))
                break
        # 对手出普通牌型，手上有炸弹 → 拆炸弹警告
        is_bomb_follow = last_play and ("炸弹" not in last_type) and "同花顺" not in last_type
        if is_bomb_follow and _has_bomb(hand):
            for k in _BOMB_FOLLOW_KEYS:
                keys.append(("situational", k, _situational_text(k)))

    # ---- 3. 队友配合
    teammate = _teammate_of_user(data)
    if last_play and last_play.get("player") == teammate:
        keys.append(("teammate", "priority", _flatten(TACTICS_DB.get("teammate", {}).get("priority"))))
        keys.append(("teammate", "follow_small", _flatten(TACTICS_DB.get("teammate", {}).get("follow_small"))))

    # ---- 4. 控牌资源
    has_top_control = any(c >= 14 for c in (_count_ranks(hand).keys()))
    if has_top_control:
        keys.append(("control_value", "single_2_joker",
                     _flatten(TACTICS_DB.get("control_value", {}).get("single_2_joker"))))

    # ---- 5. 红桃2
    if _wild_count(hand) >= 1:
        keys.append(("specials", "red_heart_2", _flatten(TACTICS_DB.get("specials", {}).get("red_heart_2"))))

    # 去重（同 key 可能被多次触发）
    seen = set()
    out = []
    for sec, k, txt in keys:
        if (sec, k) in seen or not txt:
            continue
        seen.add((sec, k))
        out.append((sec, k, txt))
    return out


# ---------------------------------------------------------------- 规则筛选（是否疑似问题）


def _suspicious_score(m: dict, data: dict, history: list) -> int:
    """对一手 User 出牌做基于规则的粗筛，返回疑似问题得分（0-3），>=2 视为疑似错误。"""
    players = data.get("players", [])
    opponents = [p for p in players if p != "User" and p != _teammate_of_user(data)]
    hand = m.get("hand_before") or []
    action = m.get("action")
    desc = m.get("desc") or ""
    cards = m.get("cards") or []
    remaining = m.get("remaining") or {}
    hand_len = len(hand)
    opp_min = _opponent_min_remaining(remaining, opponents)
    score = 0

    last_play = _last_real_play_before(history, m.get("move_index", 0))
    is_leader = last_play is None or last_play.get("player") == "User"

    if action == "PASS":
        return 0

    # -- 1) 拆炸弹：手里某点数能组成炸弹，却只打出其中部分牌。
    #    能成炸：c>=4 自然炸弹；或 c>=3 且可用红桃2 补成 4 炸。
    #    红桃2 数量有限：w 张红桃2 最多给 w 个"最大的三张"补炸，且红桃2 常另有更优用法，
    #    因此只有"前三张(rank 最大)之列"被当成炸来保护；其余三张不视为炸弹。
    hand_cnt = _count_ranks(hand)
    wilds = _wild_count(hand)
    played_cnt = _count_ranks(cards)
    triple_ranks = sorted((r for r, c in hand_cnt.items() if c == 3 and r not in (20, 21, 15)), reverse=True)
    for r, c in hand_cnt.items():
        if r in (20, 21):
            continue
        if r == 15:
            continue  # 2 是控牌资源，策略明确鼓励拆 2 当单/对用，不算"拆炸弹"
        if c >= 4:
            can_bomb = True
        elif c == 3 and wilds >= 1 and r in triple_ranks[:wilds]:
            can_bomb = True
        else:
            can_bomb = False
        if not can_bomb:
            continue
        used = played_cnt.get(r, 0)
        if used == 0:
            continue
        if used >= 4:
            continue  # 整手打出了 4 张及以上炸弹（从更大炸弹拆 4 张出也算完整炸，不算"拆"）
        if used < c:
            score += 2
            return score

    # -- 2) 残局首发避同张数（对手剩<=3 且轮到首发）
    if is_leader and opp_min in (1, 2, 3) and len(cards) == opp_min:
        score += 1

    # -- 3) 三带二带大对子（对A/对2/对王）——从出牌结构判定三带二（三张+对子），
    #        引擎 desc 是「三K带对A」式样，不能靠"三带二"子串匹配
    if len(cards) >= 5 and len(played_cnt) == 2 and 3 in played_cnt.values() and 2 in played_cnt.values():
        pair_rank = next((r for r, c in played_cnt.items() if c == 2), None)
        if pair_rank is not None and pair_rank >= 14:  # 对A(14)/对2(15) 作踢脚牌
            score += 2

    # -- 4) 大牌当散牌清理（控牌资源浪费）
    if len(cards) == 1 and any(_card_rank(c) >= 14 for c in cards) and is_leader and hand_len >= 15:
        score += 1

    # -- 5) 对手仅剩1张且出小单张放走（小单张= <J 且非控制牌）
    if opp_min == 1 and len(cards) == 1:
        r = _card_rank(cards[0])
        if r < 11:  # J=11，小单张指 3-10
            score += 2

    return score


# ---------------------------------------------------------------- Prompt 构建


def _build_system_prompt() -> str:
    """教练 system prompt：只保留人设 + 计分/基础规则。策略触发不在教练侧。"""
    db = TACTICS_DB
    parts = [
        db["system_prompts"]["base"],
        "你是掼蛋复盘教练，只点评用户(User)的出牌。",
        db["system_prompts"].get("static_rules", ""),
    ]
    return "\n\n".join(p for p in parts if p)


def _build_move_prompt(m: dict, data: dict, triggered: list) -> str:
    """为单手 User 出牌构建 user prompt：具体局势 + 触发的策略要点。"""
    desc = m.get("desc") or ("PASS" if m.get("action") == "PASS" else m.get("action"))
    action_word = "PASS" if m.get("action") == "PASS" else "出牌"
    lines = [
        f"这一手是 User 的第 {m['move_index']} 手：User {action_word}：{desc}",
        f"当时 User 手牌: {_legible_hand(m.get('hand_before'))}",
        f"当时 User 手牌结构: {_hand_summary(m.get('hand_before') or [])}",
        f"当时全场剩余张数: {m.get('remaining')}",
        f"当时此前若干手: {m.get('recent_moves')}",
    ]
    if m.get("big_played"):
        lines.append(f"当时此前已出大牌: {m.get('big_played')}")
    if triggered:
        rules = "\n".join(f"- 【{sec}.{k}】{txt}" for sec, k, txt in triggered)
        lines.append("与该手局势直接相关的策略要点如下，请严格据此判断 User 这一手是否合理：")
        lines.append(rules)
    else:
        lines.append("（该手无明显触发的专项策略，按基础出牌原则判断。）")
    lines.append("请只判断这一手 User 的出牌是否合理。若该手明显违反上述策略，指出具体错误并给出可执行的指导意见；"
                 "若基本合理，回复：无问题。")
    return "\n".join(lines)


# ---------------------------------------------------------------- LLM / 组装


def _is_problem_review(review: dict) -> bool:
    """该点评是否揭示实质问题。mistake 为空或只是「合理/无明显错误」→ 无问题。"""
    mistake = (review.get("mistake") or "").strip()
    if not mistake:
        return False
    lowered = mistake.lower()
    return not any(marker.lower() in lowered for marker in _NO_PROBLEM_MARKERS)


def _filter_no_problem_reviews(reviews: list) -> tuple:
    """过滤无问题点评，返回 (有问题的点评列表, 是否有任何问题)。"""
    problems = [r for r in reviews if _is_problem_review(r)]
    return problems, bool(problems)


def parse_coach_review(content: str) -> list:
    """解析 LLM 返回的点评。兼容 ```json 代码块 / 纯 JSON 数组 / 带前缀文本。"""
    if not content:
        return []
    text = content.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    else:
        s, e = text.find("["), text.rfind("]")
        if s != -1 and e > s:
            text = text[s:e + 1]
    try:
        data = json.loads(text, strict=False)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = data.get("reviews") or data.get("advice") or []
    reviews = []
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict):
            continue
        reviews.append({
            "move_index": it.get("move_index", 0),
            "player": "User",
            "action": it.get("action", "PLAY"),
            "desc": it.get("desc", ""),
            "situation": it.get("situation", ""),
            "mistake": it.get("mistake", ""),
            "advice": it.get("advice", ""),
        })
    return reviews


async def call_coach_llm(user_prompt: str) -> list:
    """调 DeepSeek（OpenAI 兼容）分析单手。参照 ai_client 的重试范式。"""
    client = LLMConfigManager.get_async_client("COACH")
    if client is None:
        raise RuntimeError("COACH 未配置 API Key")
    completion_args = {
        "model": LLMConfigManager.get_model_name("COACH"),
        "messages": [
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": LLMConfigManager.get_temperature("COACH") or 0.2,
        "timeout": 30.0,
    }
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = await client.chat.completions.create(**completion_args)
            return parse_coach_review(resp.choices[0].message.content or "")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt == 2:
                break
            await asyncio.sleep(2)
    raise RuntimeError(f"教练分析失败: {last_err}")


async def build_coach_review(data: dict) -> dict:
    """组装 coach 响应：提取 User 手 → 规则筛选出疑似问题手 → 逐手 LLM 确认。"""
    game_id = data.get("game_id", "")
    if game_id and game_id in _coach_cache:
        return {**_coach_cache[game_id], "cached": True}
    user_moves = extract_user_moves(data)
    if not user_moves:
        return {"reviews": [], "cached": False, "message": "本局没有找到 User 的出牌记录"}
    history = data.get("history", [])

    # 先规则筛选：明显合理的手不调用 LLM，只对疑似违反策略的手让教练确认
    candidates = [m for m in user_moves if _suspicious_score(m, data, history) >= 2]

    reviews: list = []
    for m in candidates:
        triggered = _triggered_rules(m, data, history)
        user_prompt = _build_move_prompt(m, data, triggered)
        try:
            raw = await call_coach_llm(user_prompt)
        except Exception:
            continue  # 单手失败不阻断整局
        if raw:
            review = raw[0]
            review["move_index"] = m.get("move_index")
            review["desc"] = m.get("desc") or ("PASS" if m.get("action") == "PASS" else "PASS")
            reviews.append(review)

    # 所有候选手都失败/无点评时，给出友好提示而非空 reviews
    problems, has_problem = _filter_no_problem_reviews(reviews)
    if not candidates:
        message = "本轮您的出牌没有问题！"
    elif has_problem:
        message = ""
    else:
        message = "本局未发现明显出牌问题。"
    result = {
        "game_id": game_id,
        "cached": False,
        "reviews": problems,
        "message": message,
    }
    if game_id:
        _coach_cache[game_id] = result
    return result

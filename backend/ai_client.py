import asyncio
# game/backend/ai_client.py
import os
import json
import random
import time
import logging
from typing import Optional, Tuple, Union, List
from collections import Counter
from dotenv import load_dotenv
try:
    import httpx  # type: ignore
except Exception:  # pragma: no cover
    httpx = None
from .tactics import get_tactical_strategies, calculate_hand_optimization, TACTICS_DB
from backend.logger import get_logger

log = get_logger(__name__)
try:
    from .llm_config import LLMConfigManager
except Exception:  # pragma: no cover
    LLMConfigManager = None

load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 控制台调试输出开关：默认关闭，避免刷屏；需要时可设置环境变量开启。
# Windows 控制台编码/可读性问题也能借此规避。
DEBUG_AI = os.environ.get("GUANDAN_DEBUG_AI", "").strip() in ("1", "true", "True", "yes", "YES")

# [Add] 全局缓存，仅保留每个 AI 玩家最后一次决策的上下文，用于错误排查
# 结构: { role: { system_prompt: str, user_prompt: str, ai_response: str } }
# 注意：这是「进程级全局最近一次」兜底，不含 game_id 维度（历史兼容）。
# 真正按局隔离的缓存见 LAST_PROMPTS_BY_GAME。
LAST_AI_CONTEXTS = {}

# [Refactor 错误排查隔离] 按局隔离的 AI 决策上下文缓存
# 结构: { game_id: { role: { system_prompt, user_prompt, ai_response, move_index } } }
# 解决多玩家/多浏览器/网站部署时不同牌局互相覆盖的问题。
LAST_PROMPTS_BY_GAME = {}


def set_ai_context(game_id, role, ctx, move_index=None):
    """按局+角色写入最近一次决策上下文（覆盖式，仅保留该局该角色最近一次）。"""
    LAST_PROMPTS_BY_GAME.setdefault(game_id, {})[role] = ctx
    if move_index is not None:
        ctx["move_index"] = move_index


def get_ai_context(game_id, role):
    """按局+角色取决策上下文（严格按局隔离，不落回全局兜底）。"""
    g = LAST_PROMPTS_BY_GAME.get(game_id)
    if g and role in g:
        return g[role]
    return None


def clear_ai_contexts(game_id=None):
    """清空 AI 决策上下文缓存。
    - game_id 给定时只清该局（推荐，避免多局并发互相清空）；
    - 为 None 时全局清空（兼容旧调用 / 进程级重置）。
    """
    if game_id is None:
        LAST_AI_CONTEXTS.clear()
        LAST_PROMPTS_BY_GAME.clear()
        log.info("[AI] 已清空后台决策上下文缓存（全局）")
    else:
        LAST_PROMPTS_BY_GAME.pop(game_id, None)
        log.info(f"[AI] 已清空后台决策上下文缓存（局 {game_id}）")

def _dbg(msg: str) -> None:
    """调试输出：由 GUANDAN_DEBUG_AI / GUANDAN_LOG_LEVEL 控制。"""
    if DEBUG_AI:
        log.debug(msg)

PLAYERS_ORDER = ["User", "RightBot", "PartnerBot", "LeftBot"]

_LOCAL_FALLBACK_CFG = {
    "pass_when_following": True,
    "leader": {"keep_bomb": True, "control_when_opp_low": True, "single_only_control": True, "opp_low_threshold": 3},
    "following": {"yield_to_teammate": True, "no_yield_when_opp_low": True, "avoid_waste_wildcard": True},
    "sort": {"bomb_type_threshold": 20},
}
try:
    with open(os.path.join(os.path.dirname(__file__), "tactics_data.json"), encoding="utf-8") as _f:
        _td = json.load(_f)
    _LOCAL_FALLBACK_CFG = _td.get("local_fallback", _LOCAL_FALLBACK_CFG)
except Exception:
    pass

REMIND_SF = (
    "\n\n【提醒】你手牌含有同花顺(type=30, 顶级炸弹/王炸级)。"
    "同花顺是顶级炸弹，价值极高，严禁当普通顺子打出或清理；"
    "仅在需要控牌(对手≤3张)或拦截(下家即将走完)时才出，平时务必保留。"
)

def _type_keyword(s: str) -> str:
    """从 AI 描述里提取牌型关键词（先匹配更具体的，避免“三带二”被“三”误伤）。"""
    if "单张" in s: return "单张"
    if "对子" in s: return "对子"
    if "三带二" in s or "带对" in s or "带一对" in s: return "三带二"
    if "三张" in s: return "三张"
    if "炸弹" in s: return "炸弹"
    if "顺子" in s: return "顺子"
    if "连对" in s: return "连对"
    if "钢板" in s: return "钢板"
    if "PASS" in s or "放弃" in s: return "PASS"
    return "未知"


def _real_type_name(m_type) -> str:
    """真实合法选项的牌型名（type → 中文）。"""
    if m_type == 0: return "PASS"
    if m_type == 1: return "单张"
    if m_type == 2: return "对子"
    if m_type == 3: return "三张"
    if m_type == 4: return "三带二"
    if m_type == 5: return "顺子"
    if m_type == 6: return "连对"
    if m_type == 7: return "钢板"
    if m_type >= 20: return "炸弹"
    return "未知"


def _desc_matches_move(choice_desc: str, move: dict) -> bool:
    """宽松匹配：AI 描述与真实选项是否对应（防幻觉但不过度严格）。"""
    real_type = _real_type_name(move.get('type'))
    if _type_keyword(choice_desc) == real_type:
        return True
    if real_type in choice_desc:
        return True
    if move.get('desc') and move.get('desc') in choice_desc:
        return True
    if _type_keyword(choice_desc) == "未知":
        return True
    return False

class AIDecisionError(Exception):
    """LLM 调用/校验重试耗尽，无法给出合法决策时抛出，交由上层(game_engine)统一兜底。"""
    pass

def local_fallback_move(role, valid_moves, last_move=None, remaining_counts=None, hand_cards=None):
    """统一的本地兜底出牌策略(ai_client 与 game_engine 共用)。
    规则由 tactics_data.json 的 local_fallback 段配置(启动时加载到 _LOCAL_FALLBACK_CFG)。
    排序第一策略为「拦截优先」; 同花顺(type=30)是顶级炸弹, 默认保留, 仅控牌/拦截时出。
    hand_cards: 当前玩家手牌 card id 列表(可选)。提供时, 首发优先从系统推演的最优组合中
    选最小牌型出牌, 而非本地启发式, 避免把推荐组合(三带/对子/炸弹)拆开。
    返回 move id (int)。不含 asyncio.sleep，节奏由调用方控制。
    """
    cfg = _LOCAL_FALLBACK_CFG
    bomb_th = cfg.get("sort", {}).get("bomb_type_threshold", 20)
    opp_low = cfg.get("leader", {}).get("opp_low_threshold", 3)
    icfg = cfg.get("intercept", {})
    def cnt(m):
        return len(m.get("card_ids") or m.get("cards") or [])

    def is_bomb(m):
        return 1 if m["type"] >= bomb_th else 0

    def is_waste_wild(m):
        has_wild = "赖子" in m.get("desc", "") or any(c.startswith("H-15") for c in m.get("card_ids", []))
        if not has_wild:
            return False
        if m["type"] >= bomb_th:
            return False
        return True

    def _is_break_structure(m):
        """该 move 是否破坏了手牌结构(拆对子/三张/炸弹):
        - 单张: 同 rank 存在对子/三张/三带二/顺子/炸弹 → 拆结构
        - 对子/三张: 同 rank 存在炸弹(type>=bomb_th) → 打出它们是拆炸弹
        仅靠 valid_moves 即可推断, 无需完整手牌。"""
        r = m.get("rank") or 0
        t = m.get("type") or 0
        same_rank = [x for x in valid_moves if x is not m and (x.get("rank") or 0) == r]
        if t == 1:  # 单张
            for _m in same_rank:
                _t = _m.get("type", 0)
                if _t in (2, 3, 4, 5) or _t >= bomb_th:
                    return True
            return False
        # 对子(2)/三张(3) 与更高聚合: 同 rank 有炸弹时, 对子/三张即拆炸弹
        if t in (2, 3):
            for _m in same_rank:
                if _m.get("type", 0) >= bomb_th:
                    return True
            return False
        return False

    _RANK_ALIAS = {
        "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
        "J": 11, "Q": 12, "K": 13, "A": 14, "2": 15, "小王": 20, "大王": 21,
    }

    def _label_rank(label):
        """把 tactics details 里的标签(如 '22'/'QQ'/'A'/'小王'/'红桃2')转成点数; 失败返回 None。"""
        base = label.split("+", 1)[0]
        if base == "红桃2":
            return 15
        if base in _RANK_ALIAS:
            return _RANK_ALIAS[base]
        if base and base[0] in _RANK_ALIAS:
            return _RANK_ALIAS[base[0]]
        return None

    def _parse_combo_details(details):
        """解析最优组合 details 字符串 → 按推荐优先级(散牌→对子→三张→三带→顺子→连对→钢板→炸弹)
        排列的 rank 组列表。每组返回「move 的 rank 命中即视为该组」的 rank 集合:
        - 散牌/对子/三张/三带/炸弹: 单 rank 标签, 集合为标签点数
        - 顺子/连对/钢板: 连续 rank 段, 集合为该段覆盖的全部点数(匹配 move.rank=段顶端)
        如: '散牌:[A], 对子:[QQ], 三带:[22+红桃2JJ], 炸弹:[9999]'
        → [ (1,{14}), (2,{12}), (4,{2}), (20,{9}) ]"""
        import re as _re
        _ORDER = [(1, "散牌"), (2, "对子"), (3, "三张"), (4, "三带"),
                  (5, "顺子"), (6, "连对"), (7, "钢板"), (20, "炸弹")]
        segs = _re.findall(r"(散牌|对子|三张|三带|顺子|连对|钢板|炸弹)\s*:\s*\[([^\]]*)\]", details or "")
        by_name = {}
        for name, body in segs:
            items = [x.strip() for x in body.split(",") if x.strip()]
            ranks = set()
            if name in ("散牌", "对子", "三张"):
                for it in items:
                    r = _label_rank(it)
                    if r is not None:
                        ranks.add(r)
            elif name in ("三带", "炸弹"):
                # 组合形如 '22+红桃2JJ' / '55544' / 'JJJKK'：主体在前(可能含+红桃2)
                for it in items:
                    r = _label_rank(it.split("+", 1)[0])
                    if r is not None:
                        ranks.add(r)
            elif name in ("顺子", "连对", "钢板"):
                # 连续段标签(如 '34567' / '445566' / '333444', 可能带 +红桃2 后缀):
                # 覆盖整个 rank 段, 而 move.rank=段顶端
                for it in items:
                    run = it.split("+", 1)[0]
                    for tok in _re.findall(r"10|[3-9JQKA2]", run):
                        r = _label_rank(tok)
                        if r is not None:
                            ranks.add(r)
            by_name[name] = ranks
        return [(t, by_name.get(n, set())) for t, n in _ORDER]

    def _pick_smallest_recommended(real_moves, hand_cards):
        """从系统推演的最优组合里选「最小牌型」的合法 move。
        返回 move id; 无法解析/无匹配时返回 None(外层回退启发式)。"""
        try:
            _analysis, _rec, _extra = calculate_hand_optimization(hand_cards, return_all=True)
            _best = (_extra or {}).get("best") or {}
            details = _best.get("details") or ""
        except Exception:
            return None
        if not details:
            return None
        for mtype, ranks in _parse_combo_details(details):
            if not ranks:
                continue
            cand = [m for m in real_moves if m.get("type") == mtype and (m.get("rank") or 0) in ranks]
            if not cand:
                continue
            cand.sort(key=lambda m: (m.get("rank") or 0, m["type"]))
            return cand[0]["id"]
        return None

    def sort_key(m):
        # 炸弹最后出(最高位); 其次避免拆结构(孤张优先); 再按牌型/点数
        return (is_bomb(m), 1 if _is_break_structure(m) else 0, m["type"], m.get("rank") or 0)

    teammate = "User" if role == "PartnerBot" else ("RightBot" if role == "LeftBot" else "LeftBot")
    opps = ["LeftBot", "RightBot"] if role in ("User", "PartnerBot") else ["User", "PartnerBot"]
    any_opp_low = any(int(remaining_counts.get(o, 99)) <= opp_low for o in opps) if remaining_counts else False

    order = PLAYERS_ORDER
    next_opp = None
    try:
        nidx = (order.index(role) + 1) % len(order)
        nxt = order[nidx]
        next_opp = nxt if nxt != teammate else order[(nidx + 1) % len(order)]
    except Exception:
        next_opp = None
    next_opp_count = int(remaining_counts.get(next_opp, 99)) if (remaining_counts and next_opp) else 99

    has_pass = any(m.get("type") == 0 for m in valid_moves)
    is_leader = (last_move is None or last_move.get("type") == 0)
    real_moves = [m for m in valid_moves if m.get("type") != 0]
    if not real_moves:
        return 0

    intercept_on = icfg.get("enabled", True) and next_opp_count <= icfg.get("opp_hand_low_threshold", 3)

    if is_leader:
        # 优先：从系统推演的最优组合中选最小牌型(散牌→对子→三带→炸弹),
        # 避免本地启发式把推荐组合(对子/三带/炸弹)拆开, 例如把三带里的对J拆出来打。
        if hand_cards:
            _rec_id = _pick_smallest_recommended(real_moves, hand_cards)
            if _rec_id is not None:
                return _rec_id
        lcfg = cfg.get("leader", {})
        single_moves = [m for m in real_moves if m.get("type") == 1]
        if (lcfg.get("single_only_control", True) and lcfg.get("control_when_opp_low", True)
                and any_opp_low and single_moves and len(real_moves) == len(single_moves)):
            single_moves.sort(key=lambda m: m.get("rank") or 0, reverse=True)
            return single_moves[0]["id"]
        # 首发张数避同: 下家对手剩 N 张(≤阈值)时, 避免出张数==N 的牌
        if intercept_on and icfg.get("avoid_same_count_when_leading", True):
            filtered = [m for m in real_moves if cnt(m) != next_opp_count]
            if filtered:
                real_moves = filtered
        real_moves.sort(key=sort_key)
        return real_moves[0]["id"]

    # ===== 跟牌: 拦截优先(排序第一) =====
    last_player = (last_move.get("player") if last_move else "") or ""
    fcfg = cfg.get("following", {})
    lm_count = cnt(last_move) if last_move else 0
    lm_rank = last_move.get("rank", 0) if last_move else 0
    if intercept_on and lm_count == next_opp_count and lm_rank <= icfg.get("rank_keep_threshold", 13):
        non_bomb = [m for m in real_moves if m["type"] < bomb_th]
        if non_bomb:
            non_bomb.sort(key=lambda m: m.get("rank") or 0, reverse=True)
            return non_bomb[0]["id"]
        real_moves.sort(key=lambda m: (is_bomb(m), -(m.get("rank") or 0)))
        return real_moves[0]["id"]
    # 让牌(队友占优且对手不危险)
    if (fcfg.get("yield_to_teammate", True) and last_player == teammate
            and not (fcfg.get("no_yield_when_opp_low", True) and any_opp_low)):
        return 0
    # 跟牌有 PASS 则过牌(拦截未触发时)
    if cfg.get("pass_when_following", True) and has_pass:
        return 0
    better = [m for m in real_moves if not is_waste_wild(m)]
    pool = better if (fcfg.get("avoid_waste_wildcard", True) and better) else real_moves
    pool.sort(key=sort_key)
    return pool[0]["id"]


def set_ai_context(game_id, role, ctx, move_index=None):
    """按局+角色写入最近一次决策上下文（覆盖式，仅保留该局该角色最近一次）。"""
    LAST_PROMPTS_BY_GAME.setdefault(game_id, {})[role] = ctx
    if move_index is not None:
        ctx["move_index"] = move_index

def get_ai_context(game_id, role):
    """按局+角色取决策上下文；取不到则返回 None（不落回全局 LAST_AI_CONTEXTS，
    避免多局并发时跨局串档）。"""
    g = LAST_PROMPTS_BY_GAME.get(game_id)
    if g and role in g:
        return g[role]
    return None

def _dbg(msg: str) -> None:
    """调试输出：由 GUANDAN_DEBUG_AI / GUANDAN_LOG_LEVEL 控制。"""
    if DEBUG_AI:
        log.debug(msg)

PLAYERS_ORDER = ["User", "RightBot", "PartnerBot", "LeftBot"]

def _compute_next_leader_from_round_end(round_end_entry: dict) -> Tuple[Optional[str], Optional[str], bool]:
    """Return (winner, next_leader, winner_finished) inferred from a ROUND_END entry.

    Engine rule (see game_engine._handle_round_end):
    - next leader = winner if winner not finished
    - next leader = winner's opposite (idx+2) if winner finished
    """
    if not round_end_entry:
        return None, None, False

    winner = round_end_entry.get("winner")
    finished_list = round_end_entry.get("finished") or []
    winner_finished = bool(winner and winner in finished_list)

    if not winner or winner not in PLAYERS_ORDER:
        return winner, None, winner_finished

    winner_idx = PLAYERS_ORDER.index(winner)
    if winner_finished:
        return winner, PLAYERS_ORDER[(winner_idx + 2) % 4], True
    return winner, winner, False

def _get_last_round_end(history: list) -> Optional[dict]:
    if not history:
        return None
    for h in reversed(history):
        if (h.get("action") or "").upper() == "ROUND_END":
            return h
    return None

def get_ai_decision(role: str, hand_cards: list, last_move: dict, valid_moves: list, finished_players: list = None, history: list = None, remaining_counts: dict = None, hand_card_ids: list = None, analysis_snapshot: dict = None) -> int:
    setattr(get_ai_decision, '_fallback_reason', None)  # [方案1]
    """
    AI 决策入口
    last_move: 可能是 None (首发) 或 dict {player, type, rank, desc, ...}
    finished_players: 已出完牌的玩家列表
    history: 本局出牌历史 (可选，用于构建更详细的局势描述)
    remaining_counts: 各玩家剩余手牌数量 {player_name: count}
    """
    if finished_players is None:
        finished_players = []
    if history is None:
        history = []
    if remaining_counts is None:
        remaining_counts = {}

    # 0. 强行过滤非法组合：红桃2（赖子）在任何组合中禁止配王 (大王/小王)
    def is_invalid_wild_joker_global(m):
        m_type = (m.get('type') or 0)
        if m_type == 0: # 不过滤 PASS
            return False
            
        h_joker, h_rh2 = False, False
        for cid in (m.get('card_ids') or []):
            cid_s = str(cid).upper()
            if any(tok in cid_s for tok in ['J20', 'J21', 'JK', 'SMALL_JOKER', 'BIG_JOKER', '小王', '大王', 'S_JK', 'B_JK']):
                h_joker = True
            if any(tok in cid_s for tok in ['H15', 'H-15', 'H2', '♥2', 'H2-']):
                h_rh2 = True
        return h_joker and h_rh2

    valid_moves = [m for m in (valid_moves or []) if not is_invalid_wild_joker_global(m)]

    # 1. 调试日志：看看 Bot 到底有哪些选择
    # valid_moves[0] 通常是 PASS
    can_play_moves = [m for m in valid_moves if m['type'] != 0]
    _dbg(f"  [调试] {role} 可选牌型数: {len(can_play_moves)} (总选项: {len(valid_moves)})")

    # 2. 如果没有牌可出，直接返回 0 (PASS)
    if not can_play_moves:
        log.info(f"  > 没牌可出，只能 PASS")
        time.sleep(random.uniform(1, 3))
        return 0

    # --- 确定队友 ---
    teammate = "User" if role == "PartnerBot" else ("RightBot" if role == "LeftBot" else "LeftBot")

    def extract_current_round_entries(records: list) -> list:
        """截取最近一轮（自上次 ROUND_END 之后）的行动记录。"""
        if not records:
            return []
        last_round_idx = -1
        for idx in range(len(records) - 1, -1, -1):
            if records[idx].get('action') == 'ROUND_END':
                last_round_idx = idx
                break
        if last_round_idx == -1:
            return list(records)
        return records[last_round_idx + 1:]

    current_round_entries = extract_current_round_entries(history)

    def any_opponent_low_cards(threshold: int = 3) -> bool:
        """Return True if any relevant opponent (who can still act before teammate regains lead) has <= threshold cards.

        Used to bypass forced-PASS safety rules when opponents are close to finishing.
        """
        if not remaining_counts:
            return False
        try:
            rotation = ["User", "RightBot", "PartnerBot", "LeftBot"]
            if role not in rotation or teammate not in rotation:
                raise ValueError("role/teammate not in rotation")

            # find last PLAY in current round
            last_play_idx = None
            for idx in range(len(current_round_entries) - 1, -1, -1):
                if (current_round_entries[idx].get('action') or '').upper() == 'PLAY':
                    last_play_idx = idx
                    break

            passed_after_last_play = set()
            if last_play_idx is not None:
                for entry in current_round_entries[last_play_idx + 1:]:
                    action_upper = (entry.get('action') or '').upper()
                    if action_upper == 'PLAY':
                        break
                    if action_upper == 'PASS':
                        passed_after_last_play.add(entry.get('player'))

            # only consider opponents who will still act before teammate regains lead
            start = (rotation.index(role) + 1) % len(rotation)
            order = []
            i = start
            while True:
                name = rotation[i]
                order.append(name)
                if name == teammate:
                    break
                i = (i + 1) % len(rotation)
                if i == start:
                    break

            active_opponents = [
                p for p in order
                if p not in (role, teammate)
                and p not in (finished_players or [])
                and p not in passed_after_last_play
            ]
            return any(int(remaining_counts.get(o, 99)) <= threshold for o in active_opponents)
        except Exception:
            # fallback to global opponent check
            opps = ["LeftBot", "RightBot"] if role in ("User", "PartnerBot") else ["User", "PartnerBot"]
            try:
                return any(int(remaining_counts.get(o, 99)) <= threshold for o in opps if o not in (finished_players or []))
            except Exception:
                return False
    
    # --- 预处理：过滤掉浪费红桃2的选项 ---
    # 规则：如果存在“不含红桃2”的合法出牌，则移除所有“含红桃2且非炸弹/同花顺”的选项。
    # 这样 LLM 和本地策略都看不到这些愚蠢的选项。
    
    def contains_red_heart_2(m):
        desc = m.get('desc', '')
        if "赖子" in desc or "红桃2" in desc:
            return True
        for cid in (m.get('card_ids') or []):
            cid_upper = str(cid).upper()
            if "H-15" in cid_upper or "H15" in cid_upper or "H2" in cid_upper or "♥2" in cid_upper:
                return True
        return False

    def is_waste_wild(m):
        if not contains_red_heart_2(m):
            return False
        
        # 如果是炸弹(>=20)或同花顺(30)，不算浪费
        if m['type'] >= 20: return False
        
        # 如果是普通牌型(单张、对子、三张、顺子、连对、钢板、三带二等)，且用了红桃2，视为浪费
        return True

    # 只有在有其他选择时才过滤
    # 比如：我有 3, 4, H2。上家出 3。我有 4 (单张) 和 3+H2 (对子)。
    # 如果上家出 3，我可以用 4 管，也可以用 H2 管(单张)。
    # 如果上家出 33，我可以用 3+H2 管。这时候不能过滤，因为这是唯一解。
    # 但如果我有 44 和 3+H2。我应该优先用 44。
    
    # 简单策略：如果 valid_moves 里有不含赖子的牌能管上，那就把含赖子的普通牌删掉。
    # 但保护连对/钢板/三带二等强力组合，即使含赖子也不在此阶段过滤（留给tactics层判断）
    non_wild_moves = [m for m in can_play_moves if not is_waste_wild(m)]
    non_wild_only_singles = bool(non_wild_moves) and all((m.get('type') == 1) for m in non_wild_moves)
    if non_wild_moves:
        # 过滤掉浪费赖子的选项
        # 保留：1. PASS (type=0) 2. 不浪费赖子的牌 3. 炸弹/同花顺 4. 连对/钢板/三带二（强力组合）
        filtered_moves = []
        for m in valid_moves:
            if m['type'] == 0: 
                filtered_moves.append(m)
            elif m['type'] in [4, 6, 7]:  # 三带二/连对/钢板：保留以便tactics层判断
                filtered_moves.append(m)
            elif not is_waste_wild(m):
                filtered_moves.append(m)
            elif non_wild_only_singles:
                # 若非赖子选项只有单张，则保留“使用红桃2形成非单张”的选项
                if (m.get('type') or 0) != 1 and len(m.get('card_ids') or []) >= 2:
                    filtered_moves.append(m)
            # 如果是炸弹，is_waste_wild 返回 False，已经被上面包含了
        
        if len(filtered_moves) < len(valid_moves):
            log.info(f"  [Filter] 过滤掉 {len(valid_moves) - len(filtered_moves)} 个浪费红桃2的选项")
            valid_moves = filtered_moves
            # 重新计算 can_play_moves
            can_play_moves = [m for m in valid_moves if m['type'] != 0]

    # --- End Game Aggression (残局强制出牌) ---
    # 规则：如果手牌只剩 1-2 张，且轮到我跟牌（非队友出牌），只要能管上，必须管！
    # 此时 PASS 等于自杀。
    if last_move and last_move.get('player') != teammate and len(hand_cards) <= 2:
        if can_play_moves:
            log.info(f"  [EndGame] 手牌仅剩 {len(hand_cards)} 张，且非队友出牌，强制出牌！")
            # 策略：出最小的能管上的牌
            def sort_key(m):
                is_bomb = 1 if m['type'] >= 20 else 0
                return (is_bomb, m['type'], m['rank'])
            
            can_play_moves.sort(key=sort_key)
            time.sleep(random.uniform(1, 3))
            return can_play_moves[0]['id']

    # --- 安全守卫 (Safety Guard) ---
    # 强制规则：如果队友出了牌，且中间没人管（即 last_move 依然是队友），强制 PASS
    # 除非：
    # 1. 我能一波走完（斩杀）
    # 2. 队友出的是小牌（Q及以下），且我有合适的小牌（Q及以下）可以顺牌，且不是炸弹
    if last_move and last_move.get('player') == teammate:
        # 检查是否能斩杀
        current_hand_count = len(hand_cards)
        winning_move = None
        
        for m in can_play_moves:
            # 检查出牌数量是否等于手牌数量
            if len(m['card_ids']) == current_hand_count:
                winning_move = m
                break
        
        # 顺牌检查
        # 如果队友出的是单张/对子/... (Rank <= 12)
        teammate_rank = 0
        try:
             # 有些地方 rank 是对象
             r = last_move.get('rank')
             if isinstance(r, int): teammate_rank = r
             elif hasattr(r, 'value'): teammate_rank = int(r.value)
             elif isinstance(r, str) and r.isdigit(): teammate_rank = int(r)
        except: pass
        
        teammate_type = last_move.get('type', 0)
        
        can_pad = False
        # 扩展到所有基本牌型 (单/对/三/顺/连对)，且点数 <= 12 (Q)
        if teammate_rank <= 12 and teammate_type in [1, 2, 3, 5, 6, 7]:
            # 检查我是否有小牌可以跟 (Rank <= 12, 且非炸弹)
            small_follow_moves = []
            for m in can_play_moves:
                 mv_rank = 0
                 try:
                     mr = m.get('rank')
                     if isinstance(mr, int): mv_rank = mr
                     elif hasattr(mr, 'value'): mv_rank = int(mr.value)
                     elif isinstance(mr, str) and mr.isdigit(): mv_rank = int(mr)
                 except: pass
                 
                 if mv_rank <= 12 and m.get('type', 0) < 20:
                      small_follow_moves.append(m)
            
            if small_follow_moves:
                can_pad = True

        if winning_move:
            # -------------------------------------------------------------
            # [Fix] 炸弹斩杀特例：若队友牌大且对手安全，不强制斩杀
            # -------------------------------------------------------------
            should_skip_kill = False
            is_bomb_win = winning_move.get('type', 0) >= 20
            
            # 检查对手是否安全 (只要有一个对手<=6张就算不安全)
            # any_opponent_low_cards 默认 threshold=3，这里我们需要更宽松的判定(6)
            if is_bomb_win:
                threat_exists = any_opponent_low_cards(threshold=6)
                if not threat_exists:
                    # 检查队友出的牌是否够大 (值得让)
                    tm_rank_val = 0
                    try:
                        r = last_move.get('rank')
                        if isinstance(r, int): tm_rank_val = r
                        elif hasattr(r, 'value'): tm_rank_val = int(r.value)
                        elif isinstance(r, str) and r.isdigit(): tm_rank_val = int(r)
                    except: pass
                    
                    tm_type_val = last_move.get('type', 0)
                    
                    # 判定大牌：A(14)以上, 或炸弹
                    is_high_card = (tm_rank_val >= 14) or (tm_type_val >= 20)
                    
                    if is_high_card:
                        should_skip_kill = True

            if not should_skip_kill:
                log.info(f"  [Safety] 队友 ({teammate}) 出牌，但我能走完！直接压死获胜！")
                time.sleep(random.uniform(1, 3))
                return winning_move['id']
            else:
                log.info(f"  [Safety] 队友 ({teammate}) 出牌，我能炸弹斩杀，但判定对手安全且队友牌大，交给AI决策是否PASS")

        elif can_pad:
            log.info(f"  [Safety] 队友出小牌 ({teammate_rank})，我有小牌可顺，放行给 AI 决策")
            # 不强制 return 0，继续向下执行
            pass
        else:
            # [Fix] 判定是否真的有威胁：如果队友完牌且打出的牌型无法被小牌对手管住（如打出3张，对手剩1张），则视为无威胁 -> 强制PASS
            bypass_safety = False
            if any_opponent_low_cards(threshold=3):
                # 默认有威胁，除非证明无法管
                if teammate in (finished_players or []):
                    tm_count = len(last_move.get('card_ids') or [])
                    # 若 card_ids 未知，保守认为有威胁
                    if tm_count > 1:
                        # 检查所有潜在威胁对手
                        threat_exists = False
                        current_opponents = [p for p in remaining_counts if p != role and p != teammate and p not in (finished_players or [])]
                        
                        for op in current_opponents:
                            op_count = remaining_counts.get(op, 99)
                            if op_count <= 3:
                                # 威胁判定：对手若能掏出炸弹(>=4) 或 牌数足够跟(>=tm_count)，则视为威胁
                                if op_count >= 4 or op_count >= tm_count:
                                    threat_exists = True
                                    break
                        
                        if not threat_exists:
                            bypass_safety = True
                            log.info(f"  [Safety] 队友完牌且牌型({tm_count}张)压制小牌对手，无实质威胁 -> 恢复强制 PASS")

            if any_opponent_low_cards(threshold=3) and not bypass_safety:
                log.info(f"  [Safety] 对手≤3张，禁止强制PASS：交给AI决策")
                # 不强制 PASS，继续向下执行，让 LLM/策略决定是否必须拆牌拦截
                pass
            else:
                log.info(f"  [Safety] 队友 ({teammate}) 占优 (对手已Pass)，强制 PASS 让牌")
                time.sleep(random.uniform(1, 3))
                return 0

    # --- 拆牌保护 (Split Guard) ---
    # 规则1：首发不能拆牌打3 (除非只有单张3)
    # 规则2：上家打单张时，不能拆对子/三张/顺子来管 (除非没别的单张)

    # 基于 valid_moves 反推“每个点数在手里有几张”
    # PatternRecognizer 会为每张自然牌生成一个单张选项，因此这里可用于判断“是否真的在拆牌”。
    def _rank_value(val):
        if val is None:
            return None
        if hasattr(val, "value"):
            try:
                return int(val.value)
            except Exception:
                return None
        try:
            return int(val)
        except Exception:
            return None

    single_counts_by_rank = Counter(
        _rank_value(m.get('rank'))
        for m in can_play_moves
        if m.get('type') == 1 and _rank_value(m.get('rank')) is not None
    )

    pair_ranks_by_moves = {
        _rank_value(m.get('rank'))
        for m in can_play_moves
        if m.get('type') == 2 and _rank_value(m.get('rank')) is not None
    }
    triple_ranks_by_moves = {
        _rank_value(m.get('rank'))
        for m in can_play_moves
        if m.get('type') == 3 and _rank_value(m.get('rank')) is not None
    }
    bomb_ranks_by_moves = {
        _rank_value(m.get('rank'))
        for m in can_play_moves
        if m.get('type', 0) >= 20 and _rank_value(m.get('rank')) is not None
    }

    def is_truly_splitting_single_by_moves(rank_val: int) -> bool:
        """Return True only when playing a single of rank_val necessarily breaks an existing structure.

        With 2 decks, it is common to have (pair/triple/bomb) + extra spare cards of same rank.
        We treat those spares as NOT splitting.
        """
        norm_val = _rank_value(rank_val)
        if norm_val is None:
            return False
        cnt = int(single_counts_by_rank.get(norm_val, 0))
        if norm_val in bomb_ranks_by_moves:
            return cnt <= 4
        if norm_val in triple_ranks_by_moves:
            return cnt <= 3
        if norm_val in pair_ranks_by_moves:
            return cnt <= 2
        return False
    
    # 1. 首发保护
    is_leader = (last_move is None)
    if is_leader:
        # 检查是否有单张3
        # 假设 Rank.R3 = 3
        # 找到所有单张3的选项
        single_3_moves = [m for m in can_play_moves if m['type'] == 1 and m['rank'] == 3]
        
        if single_3_moves:
            # 检查手牌中3的总数
            # 我们可以通过 card_ids 来统计
            # 但这里我们没有直接的手牌对象列表，只有 hand_cards (str list)
            # 假设 hand_cards 格式如 "H3-0", "S3-1"
            count_3 = sum(1 for c in hand_cards if "3-" in c) # 简单判断，可能误判 13 (K)
            # 更严谨：解析 rank
            # 假设 models.py 中 Rank.R3=3, Rank.RK=13
            # 我们可以直接看 valid_moves 里有没有 对3, 三张3
            has_pair_3 = any(m['type'] == 2 and m['rank'] == 3 for m in can_play_moves)
            has_triple_3 = any(m['type'] == 3 and m['rank'] == 3 for m in can_play_moves)
            
            if has_pair_3 or has_triple_3:
                # 有对3或三张3，说明单张3是拆出来的
                # 过滤掉单张3
                # print(f"  🛡️ [SplitGuard] 首发禁止拆3！检测到多张3，过滤掉单张3选项以防拆牌")
                valid_moves = [m for m in valid_moves if not (m['type'] == 1 and m['rank'] == 3)]
                can_play_moves = [m for m in valid_moves if m['type'] != 0]

    # 2. 跟单张保护
    elif last_move and last_move['type'] == 1:
        # 上家打单张
        # 检查我的单张选项
        my_single_moves = [m for m in can_play_moves if m['type'] == 1]
        
        # 找出哪些单张是“孤牌” (即没有组成对子/三张/顺子的)
        # 这比较难判断，因为 valid_moves 只是结果。
        # 反向思考：如果我有 对4 (type=2, rank=4)，那么 单张4 (type=1, rank=4) 就是拆牌。
        
        # 旧逻辑只要“存在对子/三张”就把同点数单张视为拆牌，
        # 但两副牌会出现“对子/三张 + 额外单张”的情况，此时单张不一定在拆牌。
        safe_singles = []
        split_singles = []
        for m in my_single_moves:
            if is_truly_splitting_single_by_moves(m['rank']):
                split_singles.append(m)
            else:
                safe_singles.append(m)
                
        # 如果有 safe_singles，且其中有能管上 last_move 的
        # 那么就过滤掉 split_singles
        # 注意：safe_singles 里的牌不一定能管上 last_move (比如比 3 小的 2?)
        # 实际上 valid_moves 里的牌肯定都能管上 (除了 PASS)
        
        # 修正逻辑：必须确保 safe_singles 中至少有一个能管上 last_move
        # valid_moves 里的牌都是合法的（能管上的），所以只要 safe_singles 不为空，
        # 就说明我有能管上的孤牌。
        
        if safe_singles:
            # 只要有不拆牌的单张，优先不拆（但保留拆牌选项给后续 prompt 层做显式警告，避免“只剩PASS”的错觉）
            if split_singles:
                log.warning(f"  [SplitGuard] 跟单张优先不拆：检测到 {len(split_singles)} 个拆牌单张候选，将在提示中强警告")
        else:
            # 如果没有 safe_singles (即没有孤牌)，说明我只有拆牌选项。
            # 此时应该允许拆牌！
            # 之前的逻辑没有处理 else，默认就是允许所有 valid_moves (包括 split_singles)
            # 所以这里不需要做任何过滤，直接放行。
            log.info(f"  [SplitGuard] 没有孤牌单张，允许拆牌跟单！")

    # --- 炸弹拆牌检测 & 保护 ---
    bomb_ranks = set()
    wild_bomb_ranks = set()
    for m in can_play_moves:
        if m['type'] >= 20: # 炸弹
            bomb_ranks.add(m['rank'])
            if contains_red_heart_2(m):
                wild_bomb_ranks.add(m['rank'])

    # 注意：不要在这里“硬删除”拆炸弹单张。
    # 两副牌可能出现“炸弹点数仍有富余单张”，硬删会造成 LLM/策略误以为只有 PASS。

    if last_move and last_move.get('type') == 1:
        dbg_single = [m for m in valid_moves if m.get('type') == 1]
        if dbg_single:
            sample = ", ".join(f"{m.get('desc')}[id={m.get('id')}]" for m in dbg_single[:10])
            _dbg(f"  [调试] {role} 跟单张可用单张: {sample}" + (" ..." if len(dbg_single) > 10 else ""))

    # 3. 尝试调用 LLM (仅当 Client 可用时)
    client = None if (httpx is None or LLMConfigManager is None) else LLMConfigManager.get_client(role)
    if client:
        try:
            bomb_warning_section = ""
            if bomb_ranks:
                # 简单转换 Rank 显示
                rank_map = {11:'J', 12:'Q', 13:'K', 14:'A', 20:'小王', 21:'大王'}
                bomb_names = [str(rank_map.get(r, r)) for r in bomb_ranks]
                bomb_warning_section = f"14. **炸弹完整性警告**：检测到你手中有 {', '.join(bomb_names)} 的炸弹。严禁为了打小牌（如对子、三张）而拆开炸弹！除非这是最后一步能让你直接获胜。"

            # --- 手牌结构分析 (Hand Structure Analysis) ---
            # 统计手牌中各点数的数量，识别孤张和成型结构
            # hand_cards 格式: ['♣3', '♠3', '♦3', '♣4', ...]
            # 需要解析点数
            rank_counts = {}
            has_red_heart_2 = False
            red_heart_2_count = 0
            for c in hand_cards:
                # 检查红桃2
                if "♥2" in c or "H2" in c:
                    has_red_heart_2 = True
                    red_heart_2_count += 1
                    
                # 解析 rank
                # 假设格式: 花色+点数 (如 ♣3) 或 2-3字符
                # 移除花色符号
                val_str = c.replace('♣', '').replace('♠', '').replace('♦', '').replace('♥', '').replace('JK', '小王').replace('BQ', '大王')
                # 处理特殊字符
                if '小王' in c: val_str = '小王'
                elif '大王' in c: val_str = '大王'
                
                rank_counts[val_str] = rank_counts.get(val_str, 0) + 1
            
            rank_val_map = {'3':3, '4':4, '5':5, '6':6, '7':7, '8':8, '9':9, '10':10, 
                            'J':11, 'Q':12, 'K':13, 'A':14, '2':15, '小王':20, '大王':21}
            hand_rank_value_counts = {}
            for label, count in rank_counts.items():
                val = rank_val_map.get(label)
                if val is None:
                    try:
                        val = int(label)
                    except (TypeError, ValueError):
                        continue
                hand_rank_value_counts[val] = hand_rank_value_counts.get(val, 0) + count
            
            isolated_singles = []
            pairs = []
            triples = []
            bombs = []
            
            for r, count in rank_counts.items():
                if count == 1: isolated_singles.append(r)
                elif count == 2: pairs.append(r)
                elif count == 3: triples.append(r)
                elif count >= 4: bombs.append(r)
            
            # 区分孤张层级：小 (3-10)、中 (J-Q-K-A)、大 (2/王)
            small_singles = []
            medium_singles = []
            big_singles = []
            for s in isolated_singles:
                if s in {'3','4','5','6','7','8','9','10'}:
                    small_singles.append(s)
                elif s in {'J','Q','K','A'}:
                    medium_singles.append(s)
                elif s in {'2','小王','大王'}:
                    big_singles.append(s)
                else:
                    val = rank_val_map.get(s)
                    if val is None:
                        small_singles.append(s)
                    elif val <= 10:
                        small_singles.append(s)
                    elif 11 <= val <= 14:
                        medium_singles.append(s)
                    else:
                        big_singles.append(s)

            # 从 valid_moves 中提取高级牌型
            straights = set()
            consecutive_pairs = set()
            steel_plates = set()
            straight_flushes = set()
            
            for m in can_play_moves:
                desc = m['desc']
                m_type = m['type']
                if m_type == 5: # 顺子
                    straights.add(desc)
                elif m_type == 6: # 连对 (三连对等)
                    consecutive_pairs.add(desc)
                elif m_type == 7: # 钢板 (两个连续三张)
                    steel_plates.add(desc)
                elif m_type == 30: # 同花顺
                    straight_flushes.add(desc)

            # 排序 (简单字典序，不够完美但够用)
            # 理想排序: 2,3,4...10,J,Q,K,A,2,小王,大王
            # 这里不做复杂排序，直接列出
            
            # 精简版结构摘要（避免与“触发提醒/最优策略”重复）
            def _cap_list(items, limit=6):
                if not items:
                    return ""
                if len(items) <= limit:
                    return ", ".join(items)
                return ", ".join(items[:limit]) + f"...({len(items)})"

            summary_parts = []
            if has_red_heart_2:
                summary_parts.append("红桃2:有")

            natural_bomb_ranks = [r for r, c in rank_counts.items() if c >= 4]
            if natural_bomb_ranks:
                summary_parts.append(f"炸弹(自然):{_cap_list(sorted(natural_bomb_ranks, key=str))}")

            if has_red_heart_2:
                upgrade_bomb_candidates = [r for r, c in rank_counts.items() if c >= 3]
                if upgrade_bomb_candidates:
                    summary_parts.append(f"红桃2可升级炸弹:{len(upgrade_bomb_candidates)}种(仅1次)")

            if triples:
                summary_parts.append(f"三张:{_cap_list(triples)}")
            if pairs:
                summary_parts.append(f"对子:{_cap_list(pairs)}")
            all_singles = small_singles + medium_singles + big_singles
            if all_singles:
                summary_parts.append(f"孤张:{_cap_list(all_singles)}")

            structure_info = ""
            # --- 0. 确定牌局阶段 (Game Stage) ---
            # 规则：
            # 1. 开局阶段：开局至3轮出牌之前 或 某玩家手牌少于15张之前
            # 2. 中局阶段：第四轮开始至手牌最少的玩家手牌等于或小于9张
            # 3. 残局阶段：当有玩家手牌等于或小于8张直到牌局结束
            
            # 计算最小手牌数
            min_cards = 99
            if remaining_counts:
                min_cards = min(remaining_counts.values())
            
            # 估算轮数 (简单估算：历史记录数 / 4)
            moves_count = len([h for h in history if h.get('action') == 'PLAY']) if history else 0
            approx_rounds = moves_count // 3 # 粗略估计
            
            game_stage = "未知"
            stage_focus = ""
            
            if min_cards <= 6:
                game_stage = "残局阶段"
                stage_focus = "【当前最重视】：斩杀与拦截。每一手牌都可能是最后的机会，必须全力以赴，不再保留。"
            elif min_cards <= 15 or approx_rounds >= 3:
                game_stage = "中局阶段"
                stage_focus = "【当前最重视】：控牌与配合。开始动用炸弹争夺牌权，为队友创造机会或破坏对手节奏。"
            else:
                game_stage = "开局阶段"
                # 根据手牌强度自适应开局重心
                control_cards_count = hand_rank_value_counts.get(15, 0) + hand_rank_value_counts.get(20, 0) + hand_rank_value_counts.get(21, 0)
                num_bombs = len(natural_bomb_ranks) + (1 if (has_red_heart_2 and any(c >= 3 for c in rank_counts.values())) else 0)
                
                if num_bombs >= 1 and control_cards_count >= 3:
                    stage_focus = "【当前最重视】：主动控权清理散牌。你手牌强劲（多炸/有王），不应盲目观察；应通过大单张（Ace/2）或炸弹积极争夺球权，清理掉手中 7、8、9 等散牌。不要在有强大控场牌时选择 PASS。"
                else:
                    stage_focus = "【当前最重视】：观察与保留。优先顺出孤张和小对子，保留大牌和炸弹，不要轻易暴露实力。"

            if summary_parts:
                structure_info = "- **手牌结构摘要**: " + " | ".join(summary_parts) + "\n"
                
            hand_structure_section = ""
            padding_suggestion_text = ""
            if structure_info:
                hand_structure_section = f"\n{structure_info}"
                
                # --- 顺牌提示 (Cleanup Hint) ---
                # 如果上家是对手，且出了单张，且我有孤张能管上
                if last_move and last_move['type'] == 1 and last_move.get('player') != teammate:
                    last_rank = last_move.get('rank', 0)
                    # 检查是否有比 last_rank 大的孤张
                    # isolated_singles 是字符串列表，需要转换回 rank
                    # 简单起见，我们直接检查 valid_moves
                    # 找到所有 type=1 的 valid_moves
                    valid_singles = [m for m in valid_moves if m['type'] == 1]
                    
                    # 筛选出属于 isolated_singles 的 valid_singles
                    # 我们可以通过 desc 来匹配，或者 rank
                    # 假设 isolated_singles 里的名字和 desc 里的名字大致对应
                    # 更准确的是：如果 valid_single 的 rank 在 isolated_singles 的 rank 列表中
                    
                    # 重新构建 isolated_ranks 集合
                    isolated_ranks = set()
                    rank_map_rev = {'J':11, 'Q':12, 'K':13, 'A':14, '2':15, '小王':20, '大王':21}
                    for s in isolated_singles:
                        if s in rank_map_rev:
                            isolated_ranks.add(rank_map_rev[s])
                        elif s.isdigit():
                            isolated_ranks.add(int(s))
                            
                    padding_candidates = []
                    padding_ranks = []
                    for m in valid_singles:
                        if m['rank'] in isolated_ranks:
                            padding_candidates.append(m['desc'])
                            padding_ranks.append(m['rank'])

                    # 仅大孤张(2/王)时不提示顺牌；开局阶段避免用大孤张顺牌
                    only_big = bool(padding_ranks) and all(r in (15, 20, 21) for r in padding_ranks)
                    if padding_candidates and not (only_big or game_stage == "开局阶段" and only_big):
                        padding_suggestion_text = f"\n**顺牌建议**：上家出了单张，你手中有孤张 {', '.join(padding_candidates)} 可以管上。**请务必打出其中最小的一张**，不要PASS！这是清理废牌的绝佳机会。"

            # 开局/中局：对手出小单张时，如果存在不拆牌的单张可跟，禁止 LLM 选择 PASS。
            must_pad_single_candidates = []
            if last_move and last_move.get('player') != teammate and int(last_move.get('type', 0)) == 1 and game_stage in ("开局阶段", "中局阶段"):
                def is_truly_splitting_single_by_hand(rank_val: int) -> bool:
                    """Return True if playing a single of rank_val would break a meaningful structure in hand.

                    Based on whole-hand counts instead of can_play_moves (which may omit pairs/triples when following a single).
                    """
                    try:
                        rv = int(rank_val)
                    except Exception:
                        return False

                    cnt = int(hand_rank_value_counts.get(rv, 0))
                    if cnt >= 5:
                        # Still keep a 4-card bomb after playing one
                        return False
                    if cnt >= 4:
                        return True  # breaks a bomb
                    if cnt == 3:
                        return True  # breaks a triple
                    if cnt == 2:
                        return True  # breaks a pair
                    return False

                try:
                    lm_rank_val = int(last_move.get('rank', 0))
                except Exception:
                    lm_rank_val = 0

                # 小单张范围：Q(12)及以下
                if 1 <= lm_rank_val <= 12:
                    for m in valid_moves:
                        if int(m.get('type', 0)) != 1:
                            continue
                        try:
                            r = int(m.get('rank', 0))
                        except Exception:
                            continue
                        if r <= lm_rank_val:
                            continue
                        # 不用 2/王 来顺这种小牌（保留关键资源）
                        if r >= 15:
                            continue
                        if is_truly_splitting_single_by_hand(r):
                            continue
                        must_pad_single_candidates.append(m)

                    if must_pad_single_candidates:
                        best_pad = min(must_pad_single_candidates, key=lambda x: int(x.get('rank', 999)))
                        log.info(f"  [PadGuard] 对手小单张({lm_rank_val})，存在不拆牌可跟单张：优先禁止PASS，推荐 {best_pad.get('desc')}[id={best_pad.get('id')}]")

            relaxed_split_guard = ((has_red_heart_2 and len(triples) >= 2) or bool(bombs))

            moves_desc = []
            rank_map_display = {
                3: '3', 4: '4', 5: '5', 6: '6', 7: '7', 8: '8', 9: '9', 10: '10',
                11: 'J', 12: 'Q', 13: 'K', 14: 'A', 15: '2', 20: '小王', 21: '大王'
            }

            # 识别可用同花顺的牌组（用于“拆同花顺”警告）
            straight_flush_sets = []
            for mv in valid_moves:
                if int(mv.get('type') or 0) == 30:
                    sf_ids = mv.get('card_ids') or []
                    if sf_ids:
                        straight_flush_sets.append(set(sf_ids))
            
            def move_rank_counter(move):
                counter = Counter()
                for card in move.get('cards') or []:
                    rank_val = None
                    if hasattr(card, 'rank'):
                        rank_val = getattr(card.rank, 'value', card.rank)
                    elif isinstance(card, dict):
                        rank_val = card.get('rank')
                    else:
                        rank_val = getattr(card, 'value', None)

                    if isinstance(rank_val, str):
                        try:
                            rank_val = int(rank_val)
                        except ValueError:
                            continue
                    if hasattr(rank_val, 'value'):
                        rank_val = rank_val.value
                    if isinstance(rank_val, int):
                        counter[rank_val] += 1
                return counter

            def format_split_hint(rank_value, source_type: str) -> str:
                label = rank_map_display.get(rank_value, str(rank_value))
                if source_type == 'bomb':
                    return f"拆开炸弹{label}"
                if source_type == 'triple':
                    return f"拆开三张{label}"
                return ""

            def infer_move_structure_note(move):
                # [Fix] PASS (type=0) 没有 'rank' 键，直接返回空提示，避免 KeyError 导致整个 LLM 调用跳过（退化到本地策略）
                if not move or move.get('type') == 0:
                    return ""
                rank_val = move.get('rank')
                if rank_val is None:
                    return ""
                rank_str = rank_map_display.get(rank_val, str(rank_val))

                if move['type'] != 1:
                    # 非单张：目前重点标注三带二拆牌
                    if move['type'] == 4:
                        rank_counter = move_rank_counter(move)
                        pair_rank = next((r for r, cnt in rank_counter.items() if cnt == 2), None)
                        if pair_rank is not None:
                            total_available = hand_rank_value_counts.get(pair_rank, 0)
                            if total_available >= 4:
                                return format_split_hint(pair_rank, 'bomb')
                            if total_available == 3:
                                return format_split_hint(pair_rank, 'triple')
                    
                    # 检查对子拆牌
                    if move['type'] == 2:
                        if rank_str in triples: return f"拆三张{rank_str}"
                        if rank_str in bombs: return f"拆炸弹{rank_str}"
                    
                    # 检查三张拆牌
                    if move['type'] == 3:
                        if rank_str in bombs: return f"拆炸弹{rank_str}"

                    return ""
                
                structure_note = ""

                rank_to_label = {11: 'J', 12: 'Q', 13: 'K', 14: 'A', 15: '2', 20: '小王', 21: '大王'}
                label = rank_to_label.get(rank_val, str(rank_val))

                def belongs_to_group(group):
                    for r in group:
                        if r.isdigit():
                            try:
                                if int(r) == rank_val:
                                    return True
                            except ValueError:
                                continue
                        else:
                            if rank_to_label.get(rank_val, label) == r:
                                return True
                    return False

                if belongs_to_group(pairs):
                    structure_note = f"拆对{label}"
                elif belongs_to_group(triples):
                    structure_note = f"拆三张{label}"
                elif belongs_to_group(bombs):
                    structure_note = f"拆炸弹{label}"
                else:
                    structure_note = f"孤张{label}"

                return structure_note

            for m in valid_moves:
                desc = m['desc']
                structure_hint = infer_move_structure_note(m)

                non_wild_bomb_exists = any(
                    (mv.get('type') or 0) >= 20 and not contains_red_heart_2(mv)
                    for mv in valid_moves
                )
                
                # 不要把“拆牌”合法选项从 LLM 视野里隐藏：只做强标注。
                # 否则会出现“明明能用单张7跟牌，但 LLM 看到只有 PASS”的错觉。
                allow_split_joker_single = False
                if last_move and last_move.get('type') == 1:
                    try:
                        lm_rank_val = int(last_move.get('rank', 0))
                    except Exception:
                        lm_rank_val = 0

                    # 关键控牌场景：对手出单张2(15)或小王(20)时，允许展示“拆对王”的单张选项给LLM。
                    if lm_rank_val in (15, 20) and ("拆对小王" in structure_hint or "拆对大王" in structure_hint):
                        allow_split_joker_single = True

                hidden_by_split_filter = (
                    ("拆" in structure_hint)
                    and game_stage != "残局阶段"
                    and (not allow_split_joker_single)
                )

                # 优化显示：将数字 Rank 转换为 J,Q,K,A,2
                # 检查 desc 中是否包含数字 11-15, 20, 21
                # 更安全的方法是重新构建 desc，但 desc 包含牌型信息 (如 "三带二 3带4")
                # 我们可以简单替换
                for r_val, r_name in rank_map_display.items():
                    # 注意：要避免把 10 替换成 0 (如果简单 replace)
                    # 这里主要替换 "单张 11" -> "单张 J"
                    # "对子 15" -> "对子 2"
                    # 使用正则或简单替换 (加空格防止误伤)
                    if f" {r_val}" in desc:
                        desc = desc.replace(f" {r_val}", f" {r_name}")
                    elif f"-{r_val}" in desc: # 顺子 10-14 -> 10-A
                        desc = desc.replace(f"-{r_val}", f"-{r_name}")
                
                note = ""
                if m['type'] == 0:
                    note = " [放弃出牌]"
                elif m['type'] == 7:
                    note = " [钢板/非炸弹]"
                elif m['type'] >= 20:
                    note = " [炸弹]"
                elif m['type'] == 30:
                    note = " [同花顺/王炸级]"

                # 无论牌型是什么，只要该选项使用了红桃2，都应显式标注给LLM，避免误以为是“自然牌型”
                if contains_red_heart_2(m):
                    # 标注占用红桃2的数量
                    rh2_count = 0
                    for cid in (m.get('card_ids') or []):
                        cid_upper = str(cid).upper()
                        if "H-15" in cid_upper or "H15" in cid_upper or "H2" in cid_upper or "♥2" in cid_upper:
                            rh2_count += 1
                    if rh2_count >= 2:
                        note += " [占用两张红桃2]"
                    else:
                        note += " [占用一张红桃2]"
                    if (m.get('type') or 0) >= 20 and non_wild_bomb_exists:
                        note += " [WARN 红桃2炸弹有替代]"
                
                # 检查是否拆炸弹（结合数量：<=4 才是“必拆”）
                should_warn_split = (
                    m.get('type', 0) < 20
                    and m.get('rank') in bomb_ranks
                    and int(single_counts_by_rank.get(m.get('rank'), 0)) <= 4
                )
                if should_warn_split and m['rank'] in wild_bomb_ranks and relaxed_split_guard:
                    should_warn_split = False
                if should_warn_split:
                    note += " [WARN 拆炸弹! 慎用]"

                # 检查是否拆同花顺（非同花顺选项使用了同花顺牌组中的牌）
                if int(m.get('type') or 0) != 30 and straight_flush_sets:
                    mv_ids = set(m.get('card_ids') or [])
                    if mv_ids and any(mv_ids & sf_set for sf_set in straight_flush_sets):
                        note += " [WARN 拆同花顺! 慎用]"
                
                if structure_hint:
                    note += f" ({structure_hint})"

                if hidden_by_split_filter:
                    note += " [WARN 拆牌选项-开局/中局慎用]"

                moves_desc.append(f"ID {m['id']}: {desc}{note}")

            # Debug: 给LLM/本地策略的单张候选可见性（排查“有单7却只剩PASS”的错觉）
            dbg_single = [m for m in valid_moves if m.get('type') == 1]
            if dbg_single:
                sample = ", ".join(f"{m.get('desc')}[id={m.get('id')}]" for m in dbg_single[:8])
                _dbg(f"  [调试] {role} 单张候选(可见): {sample}" + (" ..." if len(dbg_single) > 8 else ""))
            
            moves_str = "\n".join(moves_desc)
            
            # 构造 table_info 字符串供 LLM 阅读
            table_info = "无 (首发)"
            is_teammate_move = False
            last_player_name = "None"
            if last_move:
                last_player_name = last_move['player']
                table_info = f"{last_player_name} 出了 {last_move['desc']}"
                if last_player_name == teammate:
                    is_teammate_move = True

            # current_round_entries already computed earlier
            teammate_round_status = "本轮还没有轮到队友出牌"
            if teammate in finished_players:
                teammate_round_status = "队友已出完牌"
            else:
                teammate_entries = [entry for entry in current_round_entries if entry.get('player') == teammate]
                if teammate_entries:
                    last_teammate_entry = teammate_entries[-1]
                    action_upper = (last_teammate_entry.get('action') or "").upper()
                    if action_upper == "PASS":
                        teammate_round_status = "PASS"
                    elif action_upper == "PLAY":
                        first_play_entry = next((entry for entry in current_round_entries if entry.get('action') == "PLAY"), None)
                        if first_play_entry and first_play_entry.get('player') == teammate:
                            teammate_round_status = "队友首发"
                        else:
                            teammate_round_status = "队友已出牌"
                # 若本轮还没有轮到队友，则保持默认文案

            teammate_is_leader = False
            is_self_round = False
            if current_round_entries:
                first_play_entry = next((entry for entry in current_round_entries if entry.get('action') == "PLAY"), None)
                if first_play_entry:
                    if first_play_entry.get('player') == teammate:
                        teammate_is_leader = True
                    elif first_play_entry.get('player') == role:
                        is_self_round = True

            teammate_passed_after_opponent = False
            if current_round_entries and last_move and teammate not in finished_players:
                last_play_idx = None
                last_play_entry = None
                for idx in range(len(current_round_entries) - 1, -1, -1):
                    entry = current_round_entries[idx]
                    if (entry.get('action') or "").upper() == "PLAY":
                        last_play_idx = idx
                        last_play_entry = entry
                        break
                if last_play_idx is not None and last_play_entry:
                    last_play_player = last_play_entry.get('player')
                    if last_play_player and last_play_player != teammate:
                        for entry in current_round_entries[last_play_idx + 1:]:
                            action_upper = (entry.get('action') or "").upper()
                            if action_upper == "PLAY":
                                break
                            if action_upper == "PASS" and entry.get('player') == teammate:
                                teammate_passed_after_opponent = True
                                break

            # 检查对手是否已出完牌
            opponents = ["LeftBot", "RightBot"] if role == "PartnerBot" else ["User", "PartnerBot"]
            
            # 临时修复：在 prompt 中增加对“接风”的强调
            is_leader = (last_move is None)
            
            # 队友状态
            teammate_info = f"{teammate}"
            if teammate in finished_players:
                teammate_info += " (已游/已出完牌)"
            
            # 局势描述
            finished_count = len(finished_players)
            teammate_finished = teammate in finished_players
            finished_names_str = f" (已出完: {', '.join(finished_players)})" if finished_players else ""

            if finished_count == 0:
                scenario_label = "2v2"
            elif finished_count == 1:
                scenario_label = "1v2" if teammate_finished else "2v1"
            elif finished_count == 2:
                scenario_label = "1v1"
            else:
                scenario_label = str(finished_count)

            game_status_desc = f"【{scenario_label}模式{finished_names_str}】"
            counts_summary = ""
            if remaining_counts:
                order = ["User", "RightBot", "PartnerBot", "LeftBot"]
                parts = [f"{p}:{remaining_counts[p]}张" for p in order if p in remaining_counts]
                if parts:
                    counts_summary = ", ".join(parts)

            if finished_players:
                if teammate in finished_players:
                    game_status_desc += " 你的队友已经安全上岸，现在你需要独自战斗，尽量不垫底！"
                else:
                    game_status_desc += " 你的队友还在场上，请全力配合！"
                if counts_summary:
                    game_status_desc += f" 各方目前剩余手牌（已扣除本轮已出的牌）：{counts_summary}。"
            else:
                if counts_summary:
                    game_status_desc += f" 暂无玩家出完牌。各方目前剩余手牌（已扣除本轮已出的牌）：{counts_summary}。"
                else:
                    game_status_desc += " 暂无玩家出完牌，牌局仍在进行中。"
            
            # --- 构建本轮出牌详情 (Round Context) ---
            # 我们需要从 history 中倒推，找到最近一次有人出牌（非PASS）的记录，作为本轮的开始？
            # 或者更简单：从 history 中找到最近一次 "首发" (即上一手是 PASS 且 pass_count=3，或者 history 为空)
            # 但 history 只是流水账。
            # 更好的方法是：倒序遍历 history，直到找到一个 "winner" (上一轮赢家) 或者 找到 3 个连续 PASS。
            # 实际上，last_move 已经告诉了我们当前桌面上最大的牌是谁出的。
            # 我们只需要展示：从 last_move 的出牌者开始，到现在的出牌情况。
            
            round_log = []
            first_play_entry = next((entry for entry in current_round_entries if entry.get('action') == "PLAY"), None)
            first_play_player = first_play_entry.get('player') if first_play_entry else None

            def format_player_label(name: str) -> str:
                if name == role:
                    return f"{name} (你)"
                if name == teammate:
                    return f"{name} (队友)"
                return name

            def describe_entry(entry: dict) -> str:
                if not entry:
                    return ""
                action_upper = (entry.get('action') or '').upper()
                if action_upper == 'PASS':
                    return "PASS"
                if action_upper == 'PLAY':
                    return entry.get('desc') or "出牌"
                return entry.get('desc') or entry.get('action') or "——"

            round_entries = [
                e for e in current_round_entries
                if (e.get('action') or '').upper() in ('PLAY', 'PASS')
            ]
            for entry in round_entries:
                name = entry.get('player')
                if not name:
                    continue
                label = format_player_label(name)
                status = describe_entry(entry)
                if entry is first_play_entry:
                    status = f"{status}（本轮首发）"
                elif name == first_play_player and (entry.get('action') or '').upper() == 'PLAY':
                    status = f"{status}（跟牌出牌）"
                round_log.append(f"{label}: {status}")

            # 末尾标注当前AI需要出牌
            if not round_entries or round_entries[-1].get('player') != role:
                suffix = "（本轮首发）" if not round_entries else ""
                round_log.append(f"{format_player_label(role)}: 轮到你出牌{suffix}")

            round_context_str = "\n".join(round_log)

            def _extract_player_analysis_snapshot(entries: list) -> Optional[dict]:
                if not entries:
                    return None
                for h in reversed(entries):
                    if not isinstance(h, dict):
                        continue
                    snapshot = h.get("player_analysis") or h.get("analysis_snapshot") or h.get("analysis")
                    if isinstance(snapshot, dict):
                        return snapshot
                return None

            def _format_player_analysis_detail(info) -> str:
                if info is None:
                    return ""
                if isinstance(info, str):
                    return info
                if isinstance(info, list):
                    return "；".join(str(x) for x in info if x is not None)
                if isinstance(info, dict):
                    if info.get("summary"):
                        return str(info.get("summary"))
                    if info.get("segments"):
                        return "；".join(str(x) for x in info.get("segments") if x is not None)
                return str(info)

            player_analysis_snapshot = analysis_snapshot or _extract_player_analysis_snapshot(history)
            player_analysis_lines = []
            if player_analysis_snapshot:
                try:
                    role_idx = PLAYERS_ORDER.index(role)
                except Exception:
                    role_idx = 0
                up_player = PLAYERS_ORDER[(role_idx - 1) % len(PLAYERS_ORDER)]
                down_player = PLAYERS_ORDER[(role_idx + 1) % len(PLAYERS_ORDER)]

                ordered_players = [up_player, role, down_player, teammate]
                for p in ordered_players:
                    if not p:
                        continue
                    detail = _format_player_analysis_detail(player_analysis_snapshot.get(p))
                    if not detail:
                        continue
                    if p == role:
                        label = f"我（{role}）"
                    elif p == teammate:
                        label = f"{p}（队友）"
                    elif p == up_player:
                        label = f"上家{p}（对手）"
                    elif p == down_player:
                        label = f"下家{p}（对手）"
                    else:
                        label = f"{p}（对手）"
                    player_analysis_lines.append(f"{label}：{detail}")

            player_analysis_str = "\n".join(player_analysis_lines) if player_analysis_lines else "暂无玩家出牌分析数据。"

            # --- 基于已出大牌统计的控牌提醒（2/王/A） ---
            def _extract_count(text: str, label: str) -> int:
                if not text:
                    return 0
                import re
                matches = re.findall(rf"{label}（(\d+)张）", text)
                if not matches:
                    return 0
                total = 0
                for val in matches:
                    try:
                        total += int(val)
                    except Exception:
                        continue
                return total

            jokers_out = 0
            twos_out = 0
            aces_out = 0
            small_jokers_out = 0
            big_jokers_out = 0
            if player_analysis_snapshot:
                snapshot_texts = []
                for v in player_analysis_snapshot.values():
                    detail = _format_player_analysis_detail(v)
                    if detail:
                        snapshot_texts.append(detail)
                merged = "；".join(snapshot_texts)
                small_jokers_out += _extract_count(merged, "小王")
                big_jokers_out += _extract_count(merged, "大王")
                jokers_out = small_jokers_out + big_jokers_out
                twos_out += _extract_count(merged, "2")
                aces_out += _extract_count(merged, "A")

            def _hand_count_rank(label: str) -> int:
                if not hand_cards: return 0
                cnt = 0
                for c in hand_cards:
                    s = str(c).replace('♣', '').replace('♠', '').replace('♦', '').replace('♥', '').replace('JK', '小王').replace('BQ', '大王')
                    if label == s or (label in s and label not in ['小王', '大王']): 
                        cnt += 1
                return cnt

            my_big_joker_count = _hand_count_rank('大王')
            my_small_joker_count = _hand_count_rank('小王')
            my_two_count = _hand_count_rank('2')
            my_ace_count = _hand_count_rank('A')

            has_big_joker = my_big_joker_count > 0
            has_small_joker = my_small_joker_count > 0
            has_two = my_two_count > 0
            has_ace = my_ace_count > 0

            # 修正统计逻辑：显示全场未出的大牌总数（含玩家手中）
            # “全场剩余” = 总量 - 已经打出的
            total_big_joker_remaining = 2 - big_jokers_out
            total_small_joker_remaining = 2 - small_jokers_out
            total_two_remaining = 8 - twos_out
            total_ace_remaining = 8 - aces_out

            # 判定其它玩家（对手+队友）手中是否还持有该牌
            # 逻辑：全场未出 - 我手中持有 > 0
            other_has_big_joker = total_big_joker_remaining > my_big_joker_count
            other_has_small_joker = total_small_joker_remaining > my_small_joker_count
            other_has_two = total_two_remaining > my_two_count
            other_has_ace = total_ace_remaining > my_ace_count

            # 全场剩余大牌统计 (包含你手中)
            key_cards_summary = f"全场剩余大牌统计（包含你手中）：大王: {max(0, total_big_joker_remaining)}张, 小王: {max(0, total_small_joker_remaining)}张, 2: {max(0, total_two_remaining)}张, A: {max(0, total_ace_remaining)}张。"
            player_analysis_str = (player_analysis_str + "\n" + key_cards_summary).strip()

            control_ready = {
                "大王": bool(has_big_joker),
                "小王": bool(has_small_joker and not other_has_big_joker),
                "2": bool(has_two and not other_has_big_joker and not other_has_small_joker),
                "A": bool(has_ace and not other_has_big_joker and not other_has_small_joker and not other_has_two),
            }

            control_hints = []
            # 只要其它人手里没有王了，2就是强控
            if (not other_has_big_joker and not other_has_small_joker):
                if has_two:
                    control_hints.append("提示：外界已无王牌（已出完或在你手中），当前【**单张2**】具备绝对控牌价值（对手只能炸弹管），应优先用来争取下一轮首发或逼对手交炸（但若需拆开唯一的大对子22，请慎重评估是否会失去后续防守能力）。")
                if has_small_joker and not other_has_big_joker:
                    control_hints.append("提示：外界已无大王（已出完或在你手中），当前【**小王**】具备绝对控牌价值（对手只能炸弹管）。")
            
            # A的逻辑同理
            if (not other_has_big_joker and not other_has_small_joker and not other_has_two):
                if has_ace:
                    control_hints.append("提示：外界已无王/2（已出完或在你手中），当前【**单张A**】具备绝对控牌价值，可用来争取下一轮首发或消耗对手炸弹。")

            if control_hints:
                player_analysis_str = (player_analysis_str + "\n" + "\n".join(control_hints)).strip()

            last_play_player = None
            if history:
                for h in reversed(history):
                    if h.get('action') == 'PLAY':
                        last_play_player = h.get('player')
                        break

            # --- 接风/首发权推断（以引擎 ROUND_END 规则为准，避免 LLM 自行脑补） ---
            last_round_end = _get_last_round_end(history)
            last_round_winner, inferred_next_leader, winner_finished = _compute_next_leader_from_round_end(last_round_end or {})

            # is_takeover：仅当“队友作为赢家且已完牌 -> 由你(对家)首发”时成立。
            is_takeover = bool(
                is_leader
                and inferred_next_leader == role
                and winner_finished
                and last_round_winner == teammate
            )

            leader_source_text = ""
            if is_leader:
                if last_round_end and last_round_winner and inferred_next_leader:
                    if winner_finished:
                        leader_source_text = f"系统接风：上一轮赢家 {last_round_winner} 已完牌 -> 由其对家 {inferred_next_leader} 首发"
                    else:
                        leader_source_text = f"系统首发：上一轮赢家 {last_round_winner} 未完牌 -> 赢家继续首发"
                else:
                    leader_source_text = "首发：尚无可用 ROUND_END 记录（开局或系统未记录）"
            
            # --- 获取 Coach 建议 ---
            coach_advice_str = ""
            try:
                advice_file = "coach_advice.json"
                if os.path.exists(advice_file):
                    with open(advice_file, 'r', encoding='utf-8') as f:
                        all_advice = json.load(f)
                    
                    # 筛选针对当前角色的建议
                    my_advice = [a for a in all_advice if a.get('player') == role]
                    if my_advice:
                        advice_texts = [f"- {a['mistake']} -> {a['advice']}" for a in my_advice]
                        coach_advice_str = "\n【教练指导 (历史教训)】\n" + "\n".join(advice_texts)
            except Exception as e:
                log.error(f"  [WARN] 读取 Coach 建议失败: {e}")

            # --- 残局战术 (End Game Strategy) ---
            # 已移至 tactics.py 处理

            # --- 动态构建核心战术 (Dynamic Strategy Construction) ---
            strategies = []
            
            # --- 1. 定义战术原则库 (Tactical Library) ---
            # 已移至 tactics.py

            # --- 2. 动态构建策略列表 ---
            hand_structure = {
                "isolated_singles": isolated_singles,
                "pairs": pairs,
                "triples": triples,
                "bombs": bombs
            }

            stage_info, stage_strategies, trigger_strategies, optimization_data = get_tactical_strategies(
                game_stage=game_stage,
                stage_focus=stage_focus,
                teammate=teammate,
                finished_players=finished_players,
                is_teammate_move=is_teammate_move,
                is_leader=is_leader,
                is_takeover=is_takeover,
                teammate_passed_after_opponent=teammate_passed_after_opponent,
                last_move=last_move,
                can_play_moves=can_play_moves,
                has_red_heart_2=has_red_heart_2,
                bombs=bombs,
                straight_flushes=straight_flushes,
                remaining_counts=remaining_counts,
                opponents=opponents,
                hand_structure=hand_structure,
                red_heart_2_count=red_heart_2_count,
                teammate_is_leader=teammate_is_leader,
                is_self_round=is_self_round,
                hand_cards=hand_cards,
                role=role,
                hand_card_ids=hand_card_ids,
                control_card_ready=control_ready
            )

            def format_strategy_list(items: list, prefix: str = None, bold: bool = False) -> str:
                if not items:
                    return ""
                if prefix is not None:
                    formatted = []
                    for title, content in items:
                        title_text = f"**{title}**" if bold else title
                        formatted.append(f"{prefix}{title_text}：{content}")
                    return "\n".join(formatted)
                return "\n".join([f"{idx}. {title}：{content}" for idx, (title, content) in enumerate(items, 1)])

            stage_title, stage_focus_text = stage_info if stage_info else ("未知阶段", "")
            stage_info_text = f"{stage_title} —— {stage_focus_text}".strip()

            stage_strategy_text = format_strategy_list(stage_strategies, prefix="-- ", bold=True) or "暂无针对性战术"

            trigger_parts = []
            trigger_list_text = format_strategy_list(trigger_strategies, prefix="-- ", bold=True)
            if trigger_list_text:
                trigger_parts.append(trigger_list_text)
            if bomb_warning_section.strip():
                has_bomb_trigger = any(
                    ("炸弹" in (title or "") or "炸弹" in (content or ""))
                    for title, content in (trigger_strategies or [])
                )
                if not has_bomb_trigger:
                    trigger_parts.append(bomb_warning_section.strip())
            trigger_strategy_text = "\n".join(trigger_parts) if trigger_parts else "暂无额外提醒"

            if hand_structure_section.strip():
                hand_structure_text = hand_structure_section.strip()
            else:
                hand_structure_text = ""

            optimization_title = ""
            optimization_text = ""
            optimization_scope = "高优先"
            if optimization_data:
                # Remove emoji for cleaner title if needed, or keep it
                optimization_title = optimization_data['title'].replace('🔢 ', '')
                optimization_text = optimization_data['content']
                optimization_scope = optimization_data.get('scope_label', optimization_scope)
            
            if padding_suggestion_text:
                optimization_text += f"\n{padding_suggestion_text}"

            wild_hint_line = ""
            if has_red_heart_2:
                if red_heart_2_count <= 1:
                    wild_hint_line = "提示：红桃2仅一张，所有带红桃2的选项互斥，本轮只能使用其中一种；使用后对应三张/对子/炸弹等需要红桃2配的组合牌型会被拆开。\n"
                else:
                    wild_hint_line = (
                        f"提示：红桃2共{red_heart_2_count}张，含红桃2的选项会占用红桃2资源。"
                        "尽量把两张红桃2分开用于两次炸弹/必要牌型，避免在同一牌型中同时占用2张；"
                        "除非必须使用、牌力非常富裕，或为了升级更大炸弹控牌。\n"
                    )

            hand_structure_line = f"\n{hand_structure_text}" if hand_structure_text else ""

            prompt = f"""
一、 我是{role}。我的队友是{teammate_info}。对手是{', '.join(opponents)}。
1、座位顺序: User -> RightBot -> PartnerBot -> LeftBot -> User ...
2、局势状态：{game_status_desc}
3、本轮出牌详情 (从旧到新)：
{round_context_str}
4、玩家出牌分析（以下为**已出的大牌/炸弹统计**，不代表玩家手里剩牌；请据此调整控牌策略）：
{player_analysis_str}
二、手牌情况：
1、手牌: {hand_cards}。{hand_structure_line}
2、合法选项：
{wild_hint_line}
{moves_str}
3、组牌最优策略{('（' + optimization_scope + '）') if optimization_scope else ''}：
{optimization_title}
{optimization_text}
三、牌局阶段及策略
1、当前牌局阶段：
{stage_info_text}
2、当前战术策略：
{stage_strategy_text}
3、牌局触发提醒：
{trigger_strategy_text}

四、输出 JSON 格式，包含三个字段：
- "thought": 你的战术思考过程（简短分析局势、队友意图、手牌优劣）。
- "id": 你选择的选项 ID (整数)。
- "desc": 你选择的选项描述 (字符串)。
- 格式铁律：只返回一个 JSON 对象，禁止使用 markdown 代码块(不要出现 ```)，禁止在 JSON 外添加任何文字。
- "thought" 必须是单行纯文本：不得包含换行符、回车符、双引号；如需分隔请用空格。
- 必须保证 JSON 完整闭合（以 }} 结尾），否则系统无法解析你的决策。
"""

            # --- System Prompt Construction ---
            # 优化：仅在首发时包含完整规则，跟牌时仅保留基础人设，节省 Token
            system_content = TACTICS_DB["system_prompts"]["base"]
            if is_leader:
                system_content += "\n\n" + TACTICS_DB["system_prompts"]["static_rules"]

            # Keep an immutable copy for retries/extra warnings.
            original_prompt = prompt

            # --- Retry Loop for Validation ---
            max_retries = 3
            final_choice = 0
            
            for attempt in range(max_retries):
                # 动态构建参数，处理不支持 temperature 的模型 (如 o1-preview)
                completion_args = {
                    "model": LLMConfigManager.get_model_name(role),
                    "messages": [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": prompt}
                    ]
                }
                
                temp = LLMConfigManager.get_temperature(role)
                if temp is not None:
                    completion_args["temperature"] = temp

                # 增加超时设置
                completion_args["timeout"] = 10.0

                try:
                    response = None
                    last_err = None
                    for conn_attempt in range(1, 5):
                        try:
                            import datetime as _dt
                            _t0 = time.time()
                            _ts = _dt.datetime.now().strftime("%H:%M:%S")
                            response = client.chat.completions.create(**completion_args)
                            _dt_s = time.time() - _t0
                            log.debug(f"  [LLM-TIMING] {role} conn_attempt={conn_attempt} 开始={_ts} 耗时={_dt_s:.2f}s 模型={LLMConfigManager.get_model_name(role)}")
                            break
                        except Exception as e:
                            last_err = e
                            # 超时直接跳出，进入本地后备逻辑
                            if httpx is not None and isinstance(e, getattr(httpx, "TimeoutException", tuple())):
                                log.info(f"  [Timeout] AI ({role}) 响应超时 (单次请求 {completion_args.get('timeout', 10)}s)")
                                raise

                            # 连接类错误：等待3秒后重试，最多3次
                            is_conn_error = False
                            if httpx is not None:
                                conn_types = tuple(
                                    t for t in (
                                        getattr(httpx, "ConnectError", None),
                                        getattr(httpx, "NetworkError", None),
                                        getattr(httpx, "RemoteProtocolError", None),
                                        getattr(httpx, "ConnectTimeout", None),
                                    ) if t is not None
                                )
                                if conn_types and isinstance(e, conn_types):
                                    is_conn_error = True

                            if not is_conn_error and "connection" in str(e).lower():
                                is_conn_error = True

                            if is_conn_error and conn_attempt < 4:
                                log.error(f"  [WARN] LLM 连接失败，{conn_attempt}/4，5秒后重试...")
                                time.sleep(5)
                                continue

                            # 自动降级：如果报错包含 temperature，尝试移除该参数重试
                            err_msg = str(e).lower()
                            if "temperature" in err_msg and ("unsupported" in err_msg or "parameter" in err_msg or "invalid" in err_msg or "400" in err_msg):
                                log.warning(f"  [WARN] [AutoFix] 模型不支持 temperature 参数，正在移除并重试...")
                                if "temperature" in completion_args:
                                    del completion_args["temperature"]
                                LLMConfigManager.disable_temperature(role)
                                response = client.chat.completions.create(**completion_args)
                                break

                            raise e

                    if response is None and last_err is not None:
                        raise last_err
                except Exception as e:
                    if httpx is not None and isinstance(e, getattr(httpx, "TimeoutException", tuple())):
                        # 跳出循环，触发后续的本地后备逻辑
                        break
                    raise e

                content = response.choices[0].message.content

                # [Add] 保存决策上下文到全局缓存 (覆盖旧记录)
                LAST_AI_CONTEXTS[role] = {
                    "system_prompt": system_content,
                    "user_prompt": prompt + (REMIND_SF if any(m.get("type") == 30 for m in valid_moves) else ""),
                    "ai_response": content
                }

                # --- 增强的 JSON 提取逻辑 ---
                json_str = content
                
                # 1. 移除 markdown 代码块标记 (兼容 ```json ... ```)
                if json_str.strip().startswith("```"):
                    # 找到第一个换行符
                    first_newline = json_str.find('\n')
                    if first_newline != -1:
                        json_str = json_str[first_newline+1:]
                
                if json_str.strip().endswith("```"):
                    # 找到最后一个换行符
                    last_newline = json_str.rfind('\n')
                    if last_newline != -1:
                        json_str = json_str[:last_newline]

                # 2. 寻找 JSON 对象
                start_index = json_str.find('{')
                end_index = json_str.rfind('}')
                
                if start_index != -1 and end_index != -1 and start_index < end_index:
                    json_str = json_str[start_index : end_index + 1]
                else:
                    # 如果找不到，就将整个内容作为尝试解析的对象，以防万一
                    json_str = json_str
                # --- 提取结束 ---

                content = json_str.strip()

                try:
                    result = json.loads(content)
                    choice_id = int(result.get("id", 0))
                    choice_desc = result.get("desc", "")
                    thought = result.get("thought", "无思考过程")
                    
                    # --- Validation Logic ---
                    # 1. Check if ID exists in valid_moves
                    selected_move = next((m for m in valid_moves if m['id'] == choice_id), None)
                    
                    if not selected_move:
                        log.warning(f"  [WARN] [Attempt {attempt+1}] AI 选择了不存在的 ID: {choice_id}")
                        prompt += f"\n\n错误：你选择的 ID {choice_id} 不在合法选项列表中。请重新选择一个存在的 ID。"
                        continue

                    # 0. Hard guard: opening/midgame small-single padding should not PASS
                    if selected_move.get('type') == 0 and must_pad_single_candidates:
                        best_pad = min(must_pad_single_candidates, key=lambda x: int(x.get('rank', 999)))
                        log.info(f"  [PadGuard] LLM 选择 PASS，但存在不拆牌单张可顺：强制改为 {best_pad.get('desc')} (ID {best_pad.get('id')})")
                        # [Log] 已经保存到本地 errorPlay，控制台不再保留完整 Prompt
                        time.sleep(random.uniform(1, 3))
                        return int(best_pad.get('id'))
                        
                    # 2. Check if Description matches (Anti-Hallucination)
                    # 允许模糊匹配，但关键类型必须一致
                    real_desc = selected_move['desc']
                    
                    # 简单检查：如果 AI 说是 "单张" 但实际是 "炸弹"，或者 "对子"
                    # 提取关键词（复用模块级 _type_keyword/_real_type_name）
                    def get_type_keyword(s):
                        return _type_keyword(s)

                    def get_real_type_name(m_type):
                        return _real_type_name(m_type)

                    ai_type = get_type_keyword(choice_desc)
                    real_type = get_real_type_name(selected_move['type'])
                    
                    # 宽松匹配逻辑：
                    # 1. 如果 ai_type 和 real_type 一致，通过
                    # 2. 如果 real_type 的名称直接出现在描述中，通过 (解决 "钢板(非炸弹)" 被识别为炸弹的问题)
                    # 2.5 如果 AI 的描述包含真实选项的 desc（允许附加说明），通过
                    # 3. 如果无法从描述中提取类型 (未知)，也默认通过，信任 ID
                    
                    is_match = False
                    if ai_type == real_type:
                        is_match = True
                    elif real_type in choice_desc:
                        is_match = True
                    elif real_desc and real_desc in choice_desc:
                        is_match = True
                    elif ai_type == "未知":
                        is_match = True

                    if not is_match:
                        attempt += 1
                        
                        # 分析错误原因：是否是"拆炸弹"导致的幻觉
                        error_reason = ""
                        if "拆炸弹" in real_desc or "含赖子" in real_desc:
                            error_reason = (
                                f"\n**核心问题**：你选择的 ID {choice_id} 描述为 '{real_desc}'，"
                                f"这是一个**拆炸弹/消耗赖子**的牌型，而你想出的是 '{choice_desc}'。"
                                f"\n请检查【合法选项】中是否有**不拆炸弹**的替代选项（例如：用现成的对子/三张/顺子，而不是从炸弹中拆出来的）。"
                            )
                        else:
                            error_reason = f"\n你想出 '{choice_desc}'，但 ID {choice_id} 实际是 '{real_desc}'，两者不匹配。"
                        
                        log.warning(f"  [WARN] [Attempt {attempt}] 幻觉检测: AI 想出 '{choice_desc}' ({ai_type}), 但 ID {choice_id} 是 '{real_desc}' ({real_type})")
                        
                        if attempt >= max_retries:
                            # 首发时不能PASS，需要第4次机会
                            is_leader = (last_move is None or last_move.get('type') == 0)
                            has_pass = any(m.get('type') == 0 for m in valid_moves)
                            
                            if is_leader and not has_pass:
                                log.warning(f"  [WARN] [最后机会] 首发不能PASS，给予第 {attempt + 1} 次尝试")
                                extra_warning = (
                                    f"\n\n**最后机会警告**：你已经连续 {attempt} 次选择了错误的牌型。"
                                    f"{error_reason}"
                                    f"\n\n**当前是首发，你必须出牌，不能PASS！**"
                                    f"\n请从【合法选项】中仔细选择一个**真正存在**的牌型ID，不要再出现幻觉。"
                                    f"\n建议：优先选择最小的单张/对子/三张，避免拆炸弹。"
                                )
                                prompt = original_prompt + extra_warning
                                max_retries += 1
                                continue
                            else:
                                # 非首发或有PASS选项：使用PASS或默认选项
                                log.warning(f"  [WARN] 重试次数耗尽，使用默认策略")
                                pass_move = next((m for m in valid_moves if m['type'] == 0), None)
                                if pass_move:
                                    log.info(f"  → 默认选择 PASS")
                                    time.sleep(random.uniform(1, 3))
                                    return pass_move['id']
                                
                                # 实在没办法：选择"最安全"的出牌
                                # 优先级：最小单张 > 最小对子 > 最小三张 > 其他
                                def rank_sort_key(rank_val):
                                    if rank_val is None:
                                        return 999
                                    r = int(rank_val)
                                    if r == 16 or r == 17:
                                        return r + 100
                                    elif r == 15:
                                        return 14.5
                                    return r
                                
                                safe_move = None
                                for move_type in [1, 2, 3]:
                                    candidates = [m for m in valid_moves if m.get('type') == move_type]
                                    if candidates:
                                        safe_move = min(candidates, key=lambda m: rank_sort_key(m.get('rank')))
                                        break
                                
                                if safe_move:
                                    log.info(f"  → 强制选择最安全出牌: {safe_move.get('desc')} (ID {safe_move['id']})")
                                    # 设置标记供game_engine使用
                                    get_ai_decision._force_play_warning = True

                                    setattr(get_ai_decision, '_fallback_reason', "大模型连续幻觉/校验重试耗尽，系统强制默认出牌")  # [方案1]
                                    time.sleep(random.uniform(1, 3))
                                    return safe_move['id']
                                
                                # 最后兜底
                                log.info(f"  → 强制选择第一个合法选项: {valid_moves[0].get('desc')}")
                                get_ai_decision._force_play_warning = True

                                setattr(get_ai_decision, '_fallback_reason', "大模型连续幻觉/校验重试耗尽，系统强制默认出牌")  # [方案1]
                                time.sleep(random.uniform(1, 3))
                                return valid_moves[0]['id']
                        
                        # 未到重试上限：重新构造prompt
                        extra_warning = (
                            f"\n\n**第 {attempt} 次警告**：{error_reason}"
                            f"\n请仔细核对【合法选项】中的牌型描述，确保你选择的 ID 对应的牌型是你真正想出的！"
                            f"\n你现在输出的 desc='{choice_desc}'，但该 desc 与 ID {choice_id} 不对应。"
                            f"\nID {choice_id} 的真实牌型是：'{real_desc}'。请从【合法选项】里原样复制你选中的那一行的完整 desc，"
                            f"保证 desc 里的牌型名称与 ID 完全对应，不要改写或脑补。"
                        )
                        prompt = original_prompt + extra_warning
                        continue
                    
                    # Validation Passed
                    import datetime as _dt2
                    _ts2 = _dt2.datetime.now().strftime("%H:%M:%S")
                    log.info(f"  [LLM] {role} [{_ts2}] 思考过程(len={len(thought) if thought else 0}): {thought}")
                    log.info(f"  > LLM 决策 ID: {choice_id} ({real_desc})")
                    return choice_id

                except json.JSONDecodeError:
                    import re as _re
                    _m_id = _re.search(r'"id"\s*:\s*(\d+)', content)
                    _m_desc = _re.search(r'"desc"\s*:\s*"([^"]*)"', content)
                    if _m_id:
                        result = {"id": int(_m_id.group(1)), "desc": _m_desc.group(1) if _m_desc else "", "thought": ""}
                        log.warning(f"  [WARN] JSON 严格解析失败，已正则兜底提取 id={result['id']} desc={result.get('desc')}")
                        _sm = next((m for m in valid_moves if m.get('id') == result['id']), None)
                        if _sm is not None and _desc_matches_move(result.get('desc', ''), _sm):
                            log.info(f"  > [JSON兜底] 采用正则提取的决策 ID: {result['id']} ({_sm.get('desc')})")
                            return int(result['id'])
                        prompt += (
                            f"\n\n错误：你输出的 JSON 无法被解析，提取出的 id={result.get('id')} "
                            f"或描述无效。请严格输出合法 JSON（含 id/desc/thought 三个字段），"
                            f"并确保 id 与 desc 都从【合法选项】中原样复制。"
                        )
                    else:
                        log.error(f"  [WARN] JSON 解析失败: {content}")
                        prompt += "\n\n错误：请输出合法的 JSON 格式。"
                except Exception as e:
                    log.warning(f"  [WARN] 验证过程出错: {e}")
                    break
            
            # If retries exhausted, fallback to local logic
            log.warning("  [WARN] 重试次数耗尽，切换到本地策略")
            pass

        except Exception as e:
            log.error(f"  [WARN] LLM 调用失败: {e}")
            # 失败后，掉落到下方的本地逻辑
            pass
            # try:
            #     result = json.loads(content)
            # ...

    # 4. 本地“贪心”策略 (Fallback)
    # 逻辑：永远打出最小的、合法的牌。
    log.info(f"  > 切换本地策略: 基础出牌")
    if getattr(get_ai_decision, '_fallback_reason', None) is None:
        setattr(get_ai_decision, '_fallback_reason', "大模型调用失败或超时，已切换本地贪心策略兜底（出最小合法牌）")  # [方案1]
    
    # 构造 table_info 字符串供本地逻辑使用 (兼容旧代码)
    table_info = "无 (首发)"
    if last_move:
        table_info = f"{last_move['player']} 出了 {last_move['desc']}"

    # 确定队友
    teammate = "User" if role == "PartnerBot" else ("RightBot" if role == "LeftBot" else "LeftBot")
    
    # 如果我是首发 (Last move is None or empty)，必须出第一张合法的牌
    if "无 (首发)" in table_info:
        # 这里的策略是：出最小的牌型 (通常是单张)
        # 假设 valid_moves[1] 是最小的单张/对子
        # 优先出单张、对子、三带二等小牌，保留炸弹
        # 【硬约束】同花顺(type=30)是顶级炸弹(王炸级)，严禁当普通顺子打出/清理；仅当需控牌(对手≤3张)或拦截(下家即将走完)时才出
        # valid_moves 通常包含所有组合，我们需要筛选
        
        # 简单排序：优先出非炸弹，且牌值最小的
        # 假设 valid_moves 里的 'rank' 是牌的大小，'type' 是牌型
        # 我们希望 type 小 (普通牌)，rank 小
        
        # 过滤掉 PASS (type=0)
        real_moves = [m for m in can_play_moves if m['type'] != 0]
        
        if not real_moves:
            return 0
            
        # 若对手剩牌≤3且我方只剩单张可首发（典型两轮出尽场景），应先出最大单张控权
        try:
            opps = ["LeftBot", "RightBot"] if role in ("User", "PartnerBot") else ["User", "PartnerBot"]
            any_opp_low = any(int(remaining_counts.get(o, 99)) <= 3 for o in opps) if remaining_counts else False
        except Exception:
            any_opp_low = False

        single_moves = [m for m in real_moves if m.get('type') == 1]
        if any_opp_low and single_moves and len(real_moves) == len(single_moves):
            # 只剩单张可首发，优先出最大单张
            single_moves.sort(key=lambda m: m.get('rank') or 0, reverse=True)
            time.sleep(random.uniform(1, 3))
            return single_moves[0]['id']

        # 排序键：
        # 1. 是否炸弹 (type >= 20 是炸弹，我们要后出) -> 0: 普通, 1: 炸弹
        # 2. 牌型大小 (type) -> 小的先出 (单张 < 对子 < ...)
        # 3. 牌面大小 (rank) -> 小的先出
        
        def sort_key(m):
            is_bomb = 1 if m['type'] >= 20 else 0
            return (is_bomb, m['type'], m['rank'])
            
        real_moves.sort(key=sort_key)
        best_move = real_moves[0]
        time.sleep(random.uniform(1, 3))
        return best_move['id']
    
    # 如果是跟牌
    else:
        # 检查是否是队友出的牌
        # table_info 格式: "{player_name} 出了 {desc}"
        last_player_name = table_info.split(" ")[0]
        
        if last_player_name == teammate:
            # 默认让牌，除非：
            # 1. 我能走完 (手牌数 == 出牌数)
            # 2. 队友出的牌很小 (比如单张 < 10)，且我有大牌可以接管 (暂不实现，太复杂)
            
            # 检查是否能走完
            # 我们需要知道当前手牌数。hand_cards 是字符串列表，len(hand_cards) 是手牌数
            # 遍历所有能出的牌，看有没有哪张牌打出去后手牌就空了
            # 注意：valid_moves 里的 cards 长度就是打出去的张数
            
            can_finish = False
            winning_move_id = 0
            
            current_hand_count = len(hand_cards)
            
            for m in can_play_moves:
                if m['type'] == 0: continue
                # m['cards'] 是 Card 对象列表，或者 m['card_ids'] 是 ID 列表
                # 我们用 len(m['card_ids'])
                if len(m['card_ids']) == current_hand_count:
                    can_finish = True
                    winning_move_id = m['id']
                    break
            
            if can_finish:
                log.info(f"  > 队友 ({teammate}) 出牌，但我能走完！直接压死获胜！")
                time.sleep(random.uniform(1, 3))
                return winning_move_id
            else:
                if any_opponent_low_cards(threshold=3):
                    log.info(f"  > [Safety] 对手≤3张，禁止自动PASS：继续决策")
                    # 继续往下选最小可管牌，避免放走对手
                    pass
                else:
                    log.info(f"  > 队友 ({teammate}) 此时占优，选择 PASS 让牌")
                    time.sleep(random.uniform(1, 3))
                    return 0
            
        # 简单策略：能管上就管，且出最小的那个
        # 同样需要排序，选最小的能管上的牌
        
        real_moves = [m for m in can_play_moves if m['type'] != 0]
        if not real_moves:
            time.sleep(random.uniform(1, 3))
            return 0
            
        # --- 过滤掉浪费红桃2的愚蠢操作 ---
        # 如果有不含红桃2的选项，就优先选不含红桃2的
        # 除非含红桃2的是炸弹/同花顺
        
        def is_waste_wild(m):
            # 检查是否包含红桃2 (ID以 H15 开头，或者 desc 包含 赖子)
            has_wild = "赖子" in m.get('desc', '') or any(cid.startswith("H-15") for cid in m.get('card_ids', []))
            if not has_wild: return False
            
            # 如果是炸弹(>=20)或同花顺(30)，不算浪费
            if m['type'] >= 20: return False
            
            # 如果是普通牌型(单张、对子、三张、顺子等)，且用了红桃2，视为浪费
            return True

        # 尝试找到不浪费赖子的选项
        better_moves = [m for m in real_moves if not is_waste_wild(m)]
        
        if better_moves:
            real_moves = better_moves
            log.info(f"  > [Smart] 已过滤掉 {len(can_play_moves) - len(real_moves)} 个浪费红桃2的选项")
        else:
            # 如果所有能管上的牌都必须用红桃2 (比如只有 3+H2 能管 22? 不可能，因为 3+H2 < 22)
            # 或者只有 H2 单张能管 A?
            # 这种情况下，如果实在没别的牌，也只能出了。
            # 但如果是为了管一个小对子而用 H2，不如 PASS。
            
            # 如果必须浪费赖子才能管，且对方出的牌不是关键牌（比如只是个小对子），选择 PASS
            # 简单判定：如果对方出的牌 rank < 10，且我们必须用赖子管，就 PASS
            if last_move and last_move.get('rank', 0) < 10:
                 log.info(f"  > [Smart] 必须用红桃2才能管小牌，选择 PASS 保留实力")
                 time.sleep(random.uniform(1, 3))
                 return 0

        # 跟牌时，valid_moves 应该已经是筛选过能管上的牌了 (由 game_engine 保证)
        # 所以我们只需要选其中最小的一个
        # 排序逻辑同上：尽量不出炸弹，尽量出小的
        
        def sort_key(m):
            is_bomb = 1 if m['type'] >= 20 else 0
            # 如果必须出炸弹才能管上，那就出最小的炸弹
            # 如果能出普通牌管上，就出最小的普通牌
            return (is_bomb, m['type'], m['rank'])
            
        real_moves.sort(key=sort_key)
        best_move = real_moves[0]
        return best_move['id']

##############################################################################
# v2.1 异步版：与上面同步版逻辑完全一致，仅把 client.chat 调用改为 await，
# time.sleep 改为 await asyncio.sleep。由脚本机械生成，后续若改同步版逻辑需同步。
##############################################################################

async def get_ai_decision_async(role: str, hand_cards: list, last_move: dict, valid_moves: list, finished_players: list = None, history: list = None, remaining_counts: dict = None, hand_card_ids: list = None, analysis_snapshot: dict = None, game_id: str = None) -> int:
    # [Fix] 删除 async 函数属性赋值：async 函数对象不允许设 _fallback_reason 属性(抛 AttributeError)；兜底原因改用 game_engine.AI_FALLBACK_REASON 字典
    """
    AI 决策入口
    last_move: 可能是 None (首发) 或 dict {player, type, rank, desc, ...}
    finished_players: 已出完牌的玩家列表
    history: 本局出牌历史 (可选，用于构建更详细的局势描述)
    remaining_counts: 各玩家剩余手牌数量 {player_name: count}
    game_id: 所属对局（给定时决策上下文按局隔离写入，避免多局并发串档）
    """
    if finished_players is None:
        finished_players = []
    if history is None:
        history = []
    if remaining_counts is None:
        remaining_counts = {}

    # 0. 强行过滤非法组合：红桃2（赖子）在任何组合中禁止配王 (大王/小王)
    def is_invalid_wild_joker_global(m):
        m_type = (m.get('type') or 0)
        if m_type == 0: # 不过滤 PASS
            return False
            
        h_joker, h_rh2 = False, False
        for cid in (m.get('card_ids') or []):
            cid_s = str(cid).upper()
            if any(tok in cid_s for tok in ['J20', 'J21', 'JK', 'SMALL_JOKER', 'BIG_JOKER', '小王', '大王', 'S_JK', 'B_JK']):
                h_joker = True
            if any(tok in cid_s for tok in ['H15', 'H-15', 'H2', '♥2', 'H2-']):
                h_rh2 = True
        return h_joker and h_rh2

    valid_moves = [m for m in (valid_moves or []) if not is_invalid_wild_joker_global(m)]

    # 1. 调试日志：看看 Bot 到底有哪些选择
    # valid_moves[0] 通常是 PASS
    can_play_moves = [m for m in valid_moves if m['type'] != 0]
    _dbg(f"  [调试] {role} 可选牌型数: {len(can_play_moves)} (总选项: {len(valid_moves)})")

    # 2. 如果没有牌可出，直接返回 0 (PASS)
    if not can_play_moves:
        log.info(f"  > 没牌可出，只能 PASS")
        await asyncio.sleep(random.uniform(1, 3))
        return 0

    # --- 确定队友 ---
    teammate = "User" if role == "PartnerBot" else ("RightBot" if role == "LeftBot" else "LeftBot")

    def extract_current_round_entries(records: list) -> list:
        """截取最近一轮（自上次 ROUND_END 之后）的行动记录。"""
        if not records:
            return []
        last_round_idx = -1
        for idx in range(len(records) - 1, -1, -1):
            if records[idx].get('action') == 'ROUND_END':
                last_round_idx = idx
                break
        if last_round_idx == -1:
            return list(records)
        return records[last_round_idx + 1:]

    current_round_entries = extract_current_round_entries(history)

    def any_opponent_low_cards(threshold: int = 3) -> bool:
        """Return True if any relevant opponent (who can still act before teammate regains lead) has <= threshold cards.

        Used to bypass forced-PASS safety rules when opponents are close to finishing.
        """
        if not remaining_counts:
            return False
        try:
            rotation = ["User", "RightBot", "PartnerBot", "LeftBot"]
            if role not in rotation or teammate not in rotation:
                raise ValueError("role/teammate not in rotation")

            # find last PLAY in current round
            last_play_idx = None
            for idx in range(len(current_round_entries) - 1, -1, -1):
                if (current_round_entries[idx].get('action') or '').upper() == 'PLAY':
                    last_play_idx = idx
                    break

            passed_after_last_play = set()
            if last_play_idx is not None:
                for entry in current_round_entries[last_play_idx + 1:]:
                    action_upper = (entry.get('action') or '').upper()
                    if action_upper == 'PLAY':
                        break
                    if action_upper == 'PASS':
                        passed_after_last_play.add(entry.get('player'))

            # only consider opponents who will still act before teammate regains lead
            start = (rotation.index(role) + 1) % len(rotation)
            order = []
            i = start
            while True:
                name = rotation[i]
                order.append(name)
                if name == teammate:
                    break
                i = (i + 1) % len(rotation)
                if i == start:
                    break

            active_opponents = [
                p for p in order
                if p not in (role, teammate)
                and p not in (finished_players or [])
                and p not in passed_after_last_play
            ]
            return any(int(remaining_counts.get(o, 99)) <= threshold for o in active_opponents)
        except Exception:
            # fallback to global opponent check
            opps = ["LeftBot", "RightBot"] if role in ("User", "PartnerBot") else ["User", "PartnerBot"]
            try:
                return any(int(remaining_counts.get(o, 99)) <= threshold for o in opps if o not in (finished_players or []))
            except Exception:
                return False
    
    # --- 预处理：过滤掉浪费红桃2的选项 ---
    # 规则：如果存在“不含红桃2”的合法出牌，则移除所有“含红桃2且非炸弹/同花顺”的选项。
    # 这样 LLM 和本地策略都看不到这些愚蠢的选项。
    
    def contains_red_heart_2(m):
        desc = m.get('desc', '')
        if "赖子" in desc or "红桃2" in desc:
            return True
        for cid in (m.get('card_ids') or []):
            cid_upper = str(cid).upper()
            if "H-15" in cid_upper or "H15" in cid_upper or "H2" in cid_upper or "♥2" in cid_upper:
                return True
        return False

    def is_waste_wild(m):
        if not contains_red_heart_2(m):
            return False
        
        # 如果是炸弹(>=20)或同花顺(30)，不算浪费
        if m['type'] >= 20: return False
        
        # 如果是普通牌型(单张、对子、三张、顺子、连对、钢板、三带二等)，且用了红桃2，视为浪费
        return True

    # 只有在有其他选择时才过滤
    # 比如：我有 3, 4, H2。上家出 3。我有 4 (单张) 和 3+H2 (对子)。
    # 如果上家出 3，我可以用 4 管，也可以用 H2 管(单张)。
    # 如果上家出 33，我可以用 3+H2 管。这时候不能过滤，因为这是唯一解。
    # 但如果我有 44 和 3+H2。我应该优先用 44。
    
    # 简单策略：如果 valid_moves 里有不含赖子的牌能管上，那就把含赖子的普通牌删掉。
    # 但保护连对/钢板/三带二等强力组合，即使含赖子也不在此阶段过滤（留给tactics层判断）
    non_wild_moves = [m for m in can_play_moves if not is_waste_wild(m)]
    non_wild_only_singles = bool(non_wild_moves) and all((m.get('type') == 1) for m in non_wild_moves)
    if non_wild_moves:
        # 过滤掉浪费赖子的选项
        # 保留：1. PASS (type=0) 2. 不浪费赖子的牌 3. 炸弹/同花顺 4. 连对/钢板/三带二（强力组合）
        filtered_moves = []
        for m in valid_moves:
            if m['type'] == 0: 
                filtered_moves.append(m)
            elif m['type'] in [4, 6, 7]:  # 三带二/连对/钢板：保留以便tactics层判断
                filtered_moves.append(m)
            elif not is_waste_wild(m):
                filtered_moves.append(m)
            elif non_wild_only_singles:
                # 若非赖子选项只有单张，则保留“使用红桃2形成非单张”的选项
                if (m.get('type') or 0) != 1 and len(m.get('card_ids') or []) >= 2:
                    filtered_moves.append(m)
            # 如果是炸弹，is_waste_wild 返回 False，已经被上面包含了
        
        if len(filtered_moves) < len(valid_moves):
            log.info(f"  [Filter] 过滤掉 {len(valid_moves) - len(filtered_moves)} 个浪费红桃2的选项")
            valid_moves = filtered_moves
            # 重新计算 can_play_moves
            can_play_moves = [m for m in valid_moves if m['type'] != 0]

    # --- End Game Aggression (残局强制出牌) ---
    # 规则：如果手牌只剩 1-2 张，且轮到我跟牌（非队友出牌），只要能管上，必须管！
    # 此时 PASS 等于自杀。
    if last_move and last_move.get('player') != teammate and len(hand_cards) <= 2:
        if can_play_moves:
            log.info(f"  [EndGame] 手牌仅剩 {len(hand_cards)} 张，且非队友出牌，强制出牌！")
            # 策略：出最小的能管上的牌
            def sort_key(m):
                is_bomb = 1 if m['type'] >= 20 else 0
                return (is_bomb, m['type'], m['rank'])
            
            can_play_moves.sort(key=sort_key)
            await asyncio.sleep(random.uniform(1, 3))
            return can_play_moves[0]['id']

    # --- 安全守卫 (Safety Guard) ---
    # 强制规则：如果队友出了牌，且中间没人管（即 last_move 依然是队友），强制 PASS
    # 除非：
    # 1. 我能一波走完（斩杀）
    # 2. 队友出的是小牌（Q及以下），且我有合适的小牌（Q及以下）可以顺牌，且不是炸弹
    if last_move and last_move.get('player') == teammate:
        # 检查是否能斩杀
        current_hand_count = len(hand_cards)
        winning_move = None
        
        for m in can_play_moves:
            # 检查出牌数量是否等于手牌数量
            if len(m['card_ids']) == current_hand_count:
                winning_move = m
                break
        
        # 顺牌检查
        # 如果队友出的是单张/对子/... (Rank <= 12)
        teammate_rank = 0
        try:
             # 有些地方 rank 是对象
             r = last_move.get('rank')
             if isinstance(r, int): teammate_rank = r
             elif hasattr(r, 'value'): teammate_rank = int(r.value)
             elif isinstance(r, str) and r.isdigit(): teammate_rank = int(r)
        except: pass
        
        teammate_type = last_move.get('type', 0)
        
        can_pad = False
        # 扩展到所有基本牌型 (单/对/三/顺/连对)，且点数 <= 12 (Q)
        if teammate_rank <= 12 and teammate_type in [1, 2, 3, 5, 6, 7]:
            # 检查我是否有小牌可以跟 (Rank <= 12, 且非炸弹)
            small_follow_moves = []
            for m in can_play_moves:
                 mv_rank = 0
                 try:
                     mr = m.get('rank')
                     if isinstance(mr, int): mv_rank = mr
                     elif hasattr(mr, 'value'): mv_rank = int(mr.value)
                     elif isinstance(mr, str) and mr.isdigit(): mv_rank = int(mr)
                 except: pass
                 
                 if mv_rank <= 12 and m.get('type', 0) < 20:
                      small_follow_moves.append(m)
            
            if small_follow_moves:
                can_pad = True

        if winning_move:
            # -------------------------------------------------------------
            # [Fix] 炸弹斩杀特例：若队友牌大且对手安全，不强制斩杀
            # -------------------------------------------------------------
            should_skip_kill = False
            is_bomb_win = winning_move.get('type', 0) >= 20
            
            # 检查对手是否安全 (只要有一个对手<=6张就算不安全)
            # any_opponent_low_cards 默认 threshold=3，这里我们需要更宽松的判定(6)
            if is_bomb_win:
                threat_exists = any_opponent_low_cards(threshold=6)
                if not threat_exists:
                    # 检查队友出的牌是否够大 (值得让)
                    tm_rank_val = 0
                    try:
                        r = last_move.get('rank')
                        if isinstance(r, int): tm_rank_val = r
                        elif hasattr(r, 'value'): tm_rank_val = int(r.value)
                        elif isinstance(r, str) and r.isdigit(): tm_rank_val = int(r)
                    except: pass
                    
                    tm_type_val = last_move.get('type', 0)
                    
                    # 判定大牌：A(14)以上, 或炸弹
                    is_high_card = (tm_rank_val >= 14) or (tm_type_val >= 20)
                    
                    if is_high_card:
                        should_skip_kill = True

            if not should_skip_kill:
                log.info(f"  [Safety] 队友 ({teammate}) 出牌，但我能走完！直接压死获胜！")
                await asyncio.sleep(random.uniform(1, 3))
                return winning_move['id']
            else:
                log.info(f"  [Safety] 队友 ({teammate}) 出牌，我能炸弹斩杀，但判定对手安全且队友牌大，交给AI决策是否PASS")

        elif can_pad:
            log.info(f"  [Safety] 队友出小牌 ({teammate_rank})，我有小牌可顺，放行给 AI 决策")
            # 不强制 return 0，继续向下执行
            pass
        else:
            # [Fix] 判定是否真的有威胁：如果队友完牌且打出的牌型无法被小牌对手管住（如打出3张，对手剩1张），则视为无威胁 -> 强制PASS
            bypass_safety = False
            if any_opponent_low_cards(threshold=3):
                # 默认有威胁，除非证明无法管
                if teammate in (finished_players or []):
                    tm_count = len(last_move.get('card_ids') or [])
                    # 若 card_ids 未知，保守认为有威胁
                    if tm_count > 1:
                        # 检查所有潜在威胁对手
                        threat_exists = False
                        current_opponents = [p for p in remaining_counts if p != role and p != teammate and p not in (finished_players or [])]
                        
                        for op in current_opponents:
                            op_count = remaining_counts.get(op, 99)
                            if op_count <= 3:
                                # 威胁判定：对手若能掏出炸弹(>=4) 或 牌数足够跟(>=tm_count)，则视为威胁
                                if op_count >= 4 or op_count >= tm_count:
                                    threat_exists = True
                                    break
                        
                        if not threat_exists:
                            bypass_safety = True
                            log.info(f"  [Safety] 队友完牌且牌型({tm_count}张)压制小牌对手，无实质威胁 -> 恢复强制 PASS")

            if any_opponent_low_cards(threshold=3) and not bypass_safety:
                log.info(f"  [Safety] 对手≤3张，禁止强制PASS：交给AI决策")
                # 不强制 PASS，继续向下执行，让 LLM/策略决定是否必须拆牌拦截
                pass
            else:
                log.info(f"  [Safety] 队友 ({teammate}) 占优 (对手已Pass)，强制 PASS 让牌")
                await asyncio.sleep(random.uniform(1, 3))
                return 0

    # --- 拆牌保护 (Split Guard) ---
    # 规则1：首发不能拆牌打3 (除非只有单张3)
    # 规则2：上家打单张时，不能拆对子/三张/顺子来管 (除非没别的单张)

    # 基于 valid_moves 反推“每个点数在手里有几张”
    # PatternRecognizer 会为每张自然牌生成一个单张选项，因此这里可用于判断“是否真的在拆牌”。
    def _rank_value(val):
        if val is None:
            return None
        if hasattr(val, "value"):
            try:
                return int(val.value)
            except Exception:
                return None
        try:
            return int(val)
        except Exception:
            return None

    single_counts_by_rank = Counter(
        _rank_value(m.get('rank'))
        for m in can_play_moves
        if m.get('type') == 1 and _rank_value(m.get('rank')) is not None
    )

    pair_ranks_by_moves = {
        _rank_value(m.get('rank'))
        for m in can_play_moves
        if m.get('type') == 2 and _rank_value(m.get('rank')) is not None
    }
    triple_ranks_by_moves = {
        _rank_value(m.get('rank'))
        for m in can_play_moves
        if m.get('type') == 3 and _rank_value(m.get('rank')) is not None
    }
    bomb_ranks_by_moves = {
        _rank_value(m.get('rank'))
        for m in can_play_moves
        if m.get('type', 0) >= 20 and _rank_value(m.get('rank')) is not None
    }

    def is_truly_splitting_single_by_moves(rank_val: int) -> bool:
        """Return True only when playing a single of rank_val necessarily breaks an existing structure.

        With 2 decks, it is common to have (pair/triple/bomb) + extra spare cards of same rank.
        We treat those spares as NOT splitting.
        """
        norm_val = _rank_value(rank_val)
        if norm_val is None:
            return False
        cnt = int(single_counts_by_rank.get(norm_val, 0))
        if norm_val in bomb_ranks_by_moves:
            return cnt <= 4
        if norm_val in triple_ranks_by_moves:
            return cnt <= 3
        if norm_val in pair_ranks_by_moves:
            return cnt <= 2
        return False
    
    # 1. 首发保护
    is_leader = (last_move is None)
    if is_leader:
        # 检查是否有单张3
        # 假设 Rank.R3 = 3
        # 找到所有单张3的选项
        single_3_moves = [m for m in can_play_moves if m['type'] == 1 and m['rank'] == 3]
        
        if single_3_moves:
            # 检查手牌中3的总数
            # 我们可以通过 card_ids 来统计
            # 但这里我们没有直接的手牌对象列表，只有 hand_cards (str list)
            # 假设 hand_cards 格式如 "H3-0", "S3-1"
            count_3 = sum(1 for c in hand_cards if "3-" in c) # 简单判断，可能误判 13 (K)
            # 更严谨：解析 rank
            # 假设 models.py 中 Rank.R3=3, Rank.RK=13
            # 我们可以直接看 valid_moves 里有没有 对3, 三张3
            has_pair_3 = any(m['type'] == 2 and m['rank'] == 3 for m in can_play_moves)
            has_triple_3 = any(m['type'] == 3 and m['rank'] == 3 for m in can_play_moves)
            
            if has_pair_3 or has_triple_3:
                # 有对3或三张3，说明单张3是拆出来的
                # 过滤掉单张3
                # print(f"  🛡️ [SplitGuard] 首发禁止拆3！检测到多张3，过滤掉单张3选项以防拆牌")
                valid_moves = [m for m in valid_moves if not (m['type'] == 1 and m['rank'] == 3)]
                can_play_moves = [m for m in valid_moves if m['type'] != 0]

    # 2. 跟单张保护
    elif last_move and last_move['type'] == 1:
        # 上家打单张
        # 检查我的单张选项
        my_single_moves = [m for m in can_play_moves if m['type'] == 1]
        
        # 找出哪些单张是“孤牌” (即没有组成对子/三张/顺子的)
        # 这比较难判断，因为 valid_moves 只是结果。
        # 反向思考：如果我有 对4 (type=2, rank=4)，那么 单张4 (type=1, rank=4) 就是拆牌。
        
        # 旧逻辑只要“存在对子/三张”就把同点数单张视为拆牌，
        # 但两副牌会出现“对子/三张 + 额外单张”的情况，此时单张不一定在拆牌。
        safe_singles = []
        split_singles = []
        for m in my_single_moves:
            if is_truly_splitting_single_by_moves(m['rank']):
                split_singles.append(m)
            else:
                safe_singles.append(m)
                
        # 如果有 safe_singles，且其中有能管上 last_move 的
        # 那么就过滤掉 split_singles
        # 注意：safe_singles 里的牌不一定能管上 last_move (比如比 3 小的 2?)
        # 实际上 valid_moves 里的牌肯定都能管上 (除了 PASS)
        
        # 修正逻辑：必须确保 safe_singles 中至少有一个能管上 last_move
        # valid_moves 里的牌都是合法的（能管上的），所以只要 safe_singles 不为空，
        # 就说明我有能管上的孤牌。
        
        if safe_singles:
            # 只要有不拆牌的单张，优先不拆（但保留拆牌选项给后续 prompt 层做显式警告，避免“只剩PASS”的错觉）
            if split_singles:
                log.warning(f"  [SplitGuard] 跟单张优先不拆：检测到 {len(split_singles)} 个拆牌单张候选，将在提示中强警告")
        else:
            # 如果没有 safe_singles (即没有孤牌)，说明我只有拆牌选项。
            # 此时应该允许拆牌！
            # 之前的逻辑没有处理 else，默认就是允许所有 valid_moves (包括 split_singles)
            # 所以这里不需要做任何过滤，直接放行。
            log.info(f"  [SplitGuard] 没有孤牌单张，允许拆牌跟单！")

    # --- 炸弹拆牌检测 & 保护 ---
    bomb_ranks = set()
    wild_bomb_ranks = set()
    for m in can_play_moves:
        if m['type'] >= 20: # 炸弹
            bomb_ranks.add(m['rank'])
            if contains_red_heart_2(m):
                wild_bomb_ranks.add(m['rank'])

    # 注意：不要在这里“硬删除”拆炸弹单张。
    # 两副牌可能出现“炸弹点数仍有富余单张”，硬删会造成 LLM/策略误以为只有 PASS。

    if last_move and last_move.get('type') == 1:
        dbg_single = [m for m in valid_moves if m.get('type') == 1]
        if dbg_single:
            sample = ", ".join(f"{m.get('desc')}[id={m.get('id')}]" for m in dbg_single[:10])
            _dbg(f"  [调试] {role} 跟单张可用单张: {sample}" + (" ..." if len(dbg_single) > 10 else ""))

    # 3. 尝试调用 LLM (仅当 Client 可用时)
    client = None if (httpx is None or LLMConfigManager is None) else LLMConfigManager.get_async_client(role)
    if client:
        try:
            bomb_warning_section = ""
            if bomb_ranks:
                # 简单转换 Rank 显示
                rank_map = {11:'J', 12:'Q', 13:'K', 14:'A', 20:'小王', 21:'大王'}
                bomb_names = [str(rank_map.get(r, r)) for r in bomb_ranks]
                bomb_warning_section = f"14. **炸弹完整性警告**：检测到你手中有 {', '.join(bomb_names)} 的炸弹。严禁为了打小牌（如对子、三张）而拆开炸弹！除非这是最后一步能让你直接获胜。"

            # --- 手牌结构分析 (Hand Structure Analysis) ---
            # 统计手牌中各点数的数量，识别孤张和成型结构
            # hand_cards 格式: ['♣3', '♠3', '♦3', '♣4', ...]
            # 需要解析点数
            rank_counts = {}
            has_red_heart_2 = False
            red_heart_2_count = 0
            for c in hand_cards:
                # 检查红桃2
                if "♥2" in c or "H2" in c:
                    has_red_heart_2 = True
                    red_heart_2_count += 1
                    
                # 解析 rank
                # 假设格式: 花色+点数 (如 ♣3) 或 2-3字符
                # 移除花色符号
                val_str = c.replace('♣', '').replace('♠', '').replace('♦', '').replace('♥', '').replace('JK', '小王').replace('BQ', '大王')
                # 处理特殊字符
                if '小王' in c: val_str = '小王'
                elif '大王' in c: val_str = '大王'
                
                rank_counts[val_str] = rank_counts.get(val_str, 0) + 1
            
            rank_val_map = {'3':3, '4':4, '5':5, '6':6, '7':7, '8':8, '9':9, '10':10, 
                            'J':11, 'Q':12, 'K':13, 'A':14, '2':15, '小王':20, '大王':21}
            hand_rank_value_counts = {}
            for label, count in rank_counts.items():
                val = rank_val_map.get(label)
                if val is None:
                    try:
                        val = int(label)
                    except (TypeError, ValueError):
                        continue
                hand_rank_value_counts[val] = hand_rank_value_counts.get(val, 0) + count
            
            isolated_singles = []
            pairs = []
            triples = []
            bombs = []
            
            for r, count in rank_counts.items():
                if count == 1: isolated_singles.append(r)
                elif count == 2: pairs.append(r)
                elif count == 3: triples.append(r)
                elif count >= 4: bombs.append(r)
            
            # 区分孤张层级：小 (3-10)、中 (J-Q-K-A)、大 (2/王)
            small_singles = []
            medium_singles = []
            big_singles = []
            for s in isolated_singles:
                if s in {'3','4','5','6','7','8','9','10'}:
                    small_singles.append(s)
                elif s in {'J','Q','K','A'}:
                    medium_singles.append(s)
                elif s in {'2','小王','大王'}:
                    big_singles.append(s)
                else:
                    val = rank_val_map.get(s)
                    if val is None:
                        small_singles.append(s)
                    elif val <= 10:
                        small_singles.append(s)
                    elif 11 <= val <= 14:
                        medium_singles.append(s)
                    else:
                        big_singles.append(s)

            # 从 valid_moves 中提取高级牌型
            straights = set()
            consecutive_pairs = set()
            steel_plates = set()
            straight_flushes = set()
            
            for m in can_play_moves:
                desc = m['desc']
                m_type = m['type']
                if m_type == 5: # 顺子
                    straights.add(desc)
                elif m_type == 6: # 连对 (三连对等)
                    consecutive_pairs.add(desc)
                elif m_type == 7: # 钢板 (两个连续三张)
                    steel_plates.add(desc)
                elif m_type == 30: # 同花顺
                    straight_flushes.add(desc)

            # 排序 (简单字典序，不够完美但够用)
            # 理想排序: 2,3,4...10,J,Q,K,A,2,小王,大王
            # 这里不做复杂排序，直接列出
            
            # 精简版结构摘要（避免与“触发提醒/最优策略”重复）
            def _cap_list(items, limit=6):
                if not items:
                    return ""
                if len(items) <= limit:
                    return ", ".join(items)
                return ", ".join(items[:limit]) + f"...({len(items)})"

            summary_parts = []
            if has_red_heart_2:
                summary_parts.append("红桃2:有")

            natural_bomb_ranks = [r for r, c in rank_counts.items() if c >= 4]
            if natural_bomb_ranks:
                summary_parts.append(f"炸弹(自然):{_cap_list(sorted(natural_bomb_ranks, key=str))}")

            if has_red_heart_2:
                upgrade_bomb_candidates = [r for r, c in rank_counts.items() if c >= 3]
                if upgrade_bomb_candidates:
                    summary_parts.append(f"红桃2可升级炸弹:{len(upgrade_bomb_candidates)}种(仅1次)")

            if triples:
                summary_parts.append(f"三张:{_cap_list(triples)}")
            if pairs:
                summary_parts.append(f"对子:{_cap_list(pairs)}")
            all_singles = small_singles + medium_singles + big_singles
            if all_singles:
                summary_parts.append(f"孤张:{_cap_list(all_singles)}")

            structure_info = ""
            # --- 0. 确定牌局阶段 (Game Stage) ---
            # 规则：
            # 1. 开局阶段：开局至3轮出牌之前 或 某玩家手牌少于15张之前
            # 2. 中局阶段：第四轮开始至手牌最少的玩家手牌等于或小于9张
            # 3. 残局阶段：当有玩家手牌等于或小于8张直到牌局结束
            
            # 计算最小手牌数
            min_cards = 99
            if remaining_counts:
                min_cards = min(remaining_counts.values())
            
            # 估算轮数 (简单估算：历史记录数 / 4)
            moves_count = len([h for h in history if h.get('action') == 'PLAY']) if history else 0
            approx_rounds = moves_count // 3 # 粗略估计
            
            game_stage = "未知"
            stage_focus = ""
            
            if min_cards <= 6:
                game_stage = "残局阶段"
                stage_focus = "【当前最重视】：斩杀与拦截。每一手牌都可能是最后的机会，必须全力以赴，不再保留。"
            elif min_cards <= 15 or approx_rounds >= 3:
                game_stage = "中局阶段"
                stage_focus = "【当前最重视】：控牌与配合。开始动用炸弹争夺牌权，为队友创造机会或破坏对手节奏。"
            else:
                game_stage = "开局阶段"
                # 根据手牌强度自适应开局重心
                control_cards_count = hand_rank_value_counts.get(15, 0) + hand_rank_value_counts.get(20, 0) + hand_rank_value_counts.get(21, 0)
                num_bombs = len(natural_bomb_ranks) + (1 if (has_red_heart_2 and any(c >= 3 for c in rank_counts.values())) else 0)
                
                if num_bombs >= 1 and control_cards_count >= 3:
                    stage_focus = "【当前最重视】：主动控权清理散牌。你手牌强劲（多炸/有王），不应盲目观察；应通过大单张（Ace/2）或炸弹积极争夺球权，清理掉手中 7、8、9 等散牌。不要在有强大控场牌时选择 PASS。"
                else:
                    stage_focus = "【当前最重视】：观察与保留。优先顺出孤张和小对子，保留大牌和炸弹，不要轻易暴露实力。"

            if summary_parts:
                structure_info = "- **手牌结构摘要**: " + " | ".join(summary_parts) + "\n"
                
            hand_structure_section = ""
            padding_suggestion_text = ""
            if structure_info:
                hand_structure_section = f"\n{structure_info}"
                
                # --- 顺牌提示 (Cleanup Hint) ---
                # 如果上家是对手，且出了单张，且我有孤张能管上
                if last_move and last_move['type'] == 1 and last_move.get('player') != teammate:
                    last_rank = last_move.get('rank', 0)
                    # 检查是否有比 last_rank 大的孤张
                    # isolated_singles 是字符串列表，需要转换回 rank
                    # 简单起见，我们直接检查 valid_moves
                    # 找到所有 type=1 的 valid_moves
                    valid_singles = [m for m in valid_moves if m['type'] == 1]
                    
                    # 筛选出属于 isolated_singles 的 valid_singles
                    # 我们可以通过 desc 来匹配，或者 rank
                    # 假设 isolated_singles 里的名字和 desc 里的名字大致对应
                    # 更准确的是：如果 valid_single 的 rank 在 isolated_singles 的 rank 列表中
                    
                    # 重新构建 isolated_ranks 集合
                    isolated_ranks = set()
                    rank_map_rev = {'J':11, 'Q':12, 'K':13, 'A':14, '2':15, '小王':20, '大王':21}
                    for s in isolated_singles:
                        if s in rank_map_rev:
                            isolated_ranks.add(rank_map_rev[s])
                        elif s.isdigit():
                            isolated_ranks.add(int(s))
                            
                    padding_candidates = []
                    padding_ranks = []
                    for m in valid_singles:
                        if m['rank'] in isolated_ranks:
                            padding_candidates.append(m['desc'])
                            padding_ranks.append(m['rank'])

                    # 仅大孤张(2/王)时不提示顺牌；开局阶段避免用大孤张顺牌
                    only_big = bool(padding_ranks) and all(r in (15, 20, 21) for r in padding_ranks)
                    if padding_candidates and not (only_big or game_stage == "开局阶段" and only_big):
                        padding_suggestion_text = f"\n**顺牌建议**：上家出了单张，你手中有孤张 {', '.join(padding_candidates)} 可以管上。**请务必打出其中最小的一张**，不要PASS！这是清理废牌的绝佳机会。"

            # 开局/中局：对手出小单张时，如果存在不拆牌的单张可跟，禁止 LLM 选择 PASS。
            must_pad_single_candidates = []
            if last_move and last_move.get('player') != teammate and int(last_move.get('type', 0)) == 1 and game_stage in ("开局阶段", "中局阶段"):
                def is_truly_splitting_single_by_hand(rank_val: int) -> bool:
                    """Return True if playing a single of rank_val would break a meaningful structure in hand.

                    Based on whole-hand counts instead of can_play_moves (which may omit pairs/triples when following a single).
                    """
                    try:
                        rv = int(rank_val)
                    except Exception:
                        return False

                    cnt = int(hand_rank_value_counts.get(rv, 0))
                    if cnt >= 5:
                        # Still keep a 4-card bomb after playing one
                        return False
                    if cnt >= 4:
                        return True  # breaks a bomb
                    if cnt == 3:
                        return True  # breaks a triple
                    if cnt == 2:
                        return True  # breaks a pair
                    return False

                try:
                    lm_rank_val = int(last_move.get('rank', 0))
                except Exception:
                    lm_rank_val = 0

                # 小单张范围：Q(12)及以下
                if 1 <= lm_rank_val <= 12:
                    for m in valid_moves:
                        if int(m.get('type', 0)) != 1:
                            continue
                        try:
                            r = int(m.get('rank', 0))
                        except Exception:
                            continue
                        if r <= lm_rank_val:
                            continue
                        # 不用 2/王 来顺这种小牌（保留关键资源）
                        if r >= 15:
                            continue
                        if is_truly_splitting_single_by_hand(r):
                            continue
                        must_pad_single_candidates.append(m)

                    if must_pad_single_candidates:
                        best_pad = min(must_pad_single_candidates, key=lambda x: int(x.get('rank', 999)))
                        log.info(f"  [PadGuard] 对手小单张({lm_rank_val})，存在不拆牌可跟单张：优先禁止PASS，推荐 {best_pad.get('desc')}[id={best_pad.get('id')}]")

            relaxed_split_guard = ((has_red_heart_2 and len(triples) >= 2) or bool(bombs))

            moves_desc = []
            rank_map_display = {
                3: '3', 4: '4', 5: '5', 6: '6', 7: '7', 8: '8', 9: '9', 10: '10',
                11: 'J', 12: 'Q', 13: 'K', 14: 'A', 15: '2', 20: '小王', 21: '大王'
            }

            # 识别可用同花顺的牌组（用于“拆同花顺”警告）
            straight_flush_sets = []
            for mv in valid_moves:
                if int(mv.get('type') or 0) == 30:
                    sf_ids = mv.get('card_ids') or []
                    if sf_ids:
                        straight_flush_sets.append(set(sf_ids))
            
            def move_rank_counter(move):
                counter = Counter()
                for card in move.get('cards') or []:
                    rank_val = None
                    if hasattr(card, 'rank'):
                        rank_val = getattr(card.rank, 'value', card.rank)
                    elif isinstance(card, dict):
                        rank_val = card.get('rank')
                    else:
                        rank_val = getattr(card, 'value', None)

                    if isinstance(rank_val, str):
                        try:
                            rank_val = int(rank_val)
                        except ValueError:
                            continue
                    if hasattr(rank_val, 'value'):
                        rank_val = rank_val.value
                    if isinstance(rank_val, int):
                        counter[rank_val] += 1
                return counter

            def format_split_hint(rank_value, source_type: str) -> str:
                label = rank_map_display.get(rank_value, str(rank_value))
                if source_type == 'bomb':
                    return f"拆开炸弹{label}"
                if source_type == 'triple':
                    return f"拆开三张{label}"
                return ""

            def infer_move_structure_note(move):
                # [Fix] PASS (type=0) 没有 'rank' 键，直接返回空提示，避免 KeyError 导致整个 LLM 调用跳过（退化到本地策略）
                if not move or move.get('type') == 0:
                    return ""
                rank_val = move.get('rank')
                if rank_val is None:
                    return ""
                rank_str = rank_map_display.get(rank_val, str(rank_val))

                if move['type'] != 1:
                    # 非单张：目前重点标注三带二拆牌
                    if move['type'] == 4:
                        rank_counter = move_rank_counter(move)
                        pair_rank = next((r for r, cnt in rank_counter.items() if cnt == 2), None)
                        if pair_rank is not None:
                            total_available = hand_rank_value_counts.get(pair_rank, 0)
                            if total_available >= 4:
                                return format_split_hint(pair_rank, 'bomb')
                            if total_available == 3:
                                return format_split_hint(pair_rank, 'triple')
                    
                    # 检查对子拆牌
                    if move['type'] == 2:
                        if rank_str in triples: return f"拆三张{rank_str}"
                        if rank_str in bombs: return f"拆炸弹{rank_str}"
                    
                    # 检查三张拆牌
                    if move['type'] == 3:
                        if rank_str in bombs: return f"拆炸弹{rank_str}"

                    return ""
                
                structure_note = ""

                rank_to_label = {11: 'J', 12: 'Q', 13: 'K', 14: 'A', 15: '2', 20: '小王', 21: '大王'}
                label = rank_to_label.get(rank_val, str(rank_val))

                def belongs_to_group(group):
                    for r in group:
                        if r.isdigit():
                            try:
                                if int(r) == rank_val:
                                    return True
                            except ValueError:
                                continue
                        else:
                            if rank_to_label.get(rank_val, label) == r:
                                return True
                    return False

                if belongs_to_group(pairs):
                    structure_note = f"拆对{label}"
                elif belongs_to_group(triples):
                    structure_note = f"拆三张{label}"
                elif belongs_to_group(bombs):
                    structure_note = f"拆炸弹{label}"
                else:
                    structure_note = f"孤张{label}"

                return structure_note

            for m in valid_moves:
                desc = m['desc']
                structure_hint = infer_move_structure_note(m)

                non_wild_bomb_exists = any(
                    (mv.get('type') or 0) >= 20 and not contains_red_heart_2(mv)
                    for mv in valid_moves
                )
                
                # 不要把“拆牌”合法选项从 LLM 视野里隐藏：只做强标注。
                # 否则会出现“明明能用单张7跟牌，但 LLM 看到只有 PASS”的错觉。
                allow_split_joker_single = False
                if last_move and last_move.get('type') == 1:
                    try:
                        lm_rank_val = int(last_move.get('rank', 0))
                    except Exception:
                        lm_rank_val = 0

                    # 关键控牌场景：对手出单张2(15)或小王(20)时，允许展示“拆对王”的单张选项给LLM。
                    if lm_rank_val in (15, 20) and ("拆对小王" in structure_hint or "拆对大王" in structure_hint):
                        allow_split_joker_single = True

                hidden_by_split_filter = (
                    ("拆" in structure_hint)
                    and game_stage != "残局阶段"
                    and (not allow_split_joker_single)
                )

                # 优化显示：将数字 Rank 转换为 J,Q,K,A,2
                # 检查 desc 中是否包含数字 11-15, 20, 21
                # 更安全的方法是重新构建 desc，但 desc 包含牌型信息 (如 "三带二 3带4")
                # 我们可以简单替换
                for r_val, r_name in rank_map_display.items():
                    # 注意：要避免把 10 替换成 0 (如果简单 replace)
                    # 这里主要替换 "单张 11" -> "单张 J"
                    # "对子 15" -> "对子 2"
                    # 使用正则或简单替换 (加空格防止误伤)
                    if f" {r_val}" in desc:
                        desc = desc.replace(f" {r_val}", f" {r_name}")
                    elif f"-{r_val}" in desc: # 顺子 10-14 -> 10-A
                        desc = desc.replace(f"-{r_val}", f"-{r_name}")
                
                note = ""
                if m['type'] == 0:
                    note = " [放弃出牌]"
                elif m['type'] == 7:
                    note = " [钢板/非炸弹]"
                elif m['type'] >= 20:
                    note = " [炸弹]"
                elif m['type'] == 30:
                    note = " [同花顺/王炸级]"

                # 无论牌型是什么，只要该选项使用了红桃2，都应显式标注给LLM，避免误以为是“自然牌型”
                if contains_red_heart_2(m):
                    # 标注占用红桃2的数量
                    rh2_count = 0
                    for cid in (m.get('card_ids') or []):
                        cid_upper = str(cid).upper()
                        if "H-15" in cid_upper or "H15" in cid_upper or "H2" in cid_upper or "♥2" in cid_upper:
                            rh2_count += 1
                    if rh2_count >= 2:
                        note += " [占用两张红桃2]"
                    else:
                        note += " [占用一张红桃2]"
                    if (m.get('type') or 0) >= 20 and non_wild_bomb_exists:
                        note += " [WARN 红桃2炸弹有替代]"
                
                # 检查是否拆炸弹（结合数量：<=4 才是“必拆”）
                should_warn_split = (
                    m.get('type', 0) < 20
                    and m.get('rank') in bomb_ranks
                    and int(single_counts_by_rank.get(m.get('rank'), 0)) <= 4
                )
                if should_warn_split and m['rank'] in wild_bomb_ranks and relaxed_split_guard:
                    should_warn_split = False
                if should_warn_split:
                    note += " [WARN 拆炸弹! 慎用]"

                # 检查是否拆同花顺（非同花顺选项使用了同花顺牌组中的牌）
                if int(m.get('type') or 0) != 30 and straight_flush_sets:
                    mv_ids = set(m.get('card_ids') or [])
                    if mv_ids and any(mv_ids & sf_set for sf_set in straight_flush_sets):
                        note += " [WARN 拆同花顺! 慎用]"
                
                if structure_hint:
                    note += f" ({structure_hint})"

                if hidden_by_split_filter:
                    note += " [WARN 拆牌选项-开局/中局慎用]"

                moves_desc.append(f"ID {m['id']}: {desc}{note}")

            # Debug: 给LLM/本地策略的单张候选可见性（排查“有单7却只剩PASS”的错觉）
            dbg_single = [m for m in valid_moves if m.get('type') == 1]
            if dbg_single:
                sample = ", ".join(f"{m.get('desc')}[id={m.get('id')}]" for m in dbg_single[:8])
                _dbg(f"  [调试] {role} 单张候选(可见): {sample}" + (" ..." if len(dbg_single) > 8 else ""))
            
            moves_str = "\n".join(moves_desc)
            
            # 构造 table_info 字符串供 LLM 阅读
            table_info = "无 (首发)"
            is_teammate_move = False
            last_player_name = "None"
            if last_move:
                last_player_name = last_move['player']
                table_info = f"{last_player_name} 出了 {last_move['desc']}"
                if last_player_name == teammate:
                    is_teammate_move = True

            # current_round_entries already computed earlier
            teammate_round_status = "本轮还没有轮到队友出牌"
            if teammate in finished_players:
                teammate_round_status = "队友已出完牌"
            else:
                teammate_entries = [entry for entry in current_round_entries if entry.get('player') == teammate]
                if teammate_entries:
                    last_teammate_entry = teammate_entries[-1]
                    action_upper = (last_teammate_entry.get('action') or "").upper()
                    if action_upper == "PASS":
                        teammate_round_status = "PASS"
                    elif action_upper == "PLAY":
                        first_play_entry = next((entry for entry in current_round_entries if entry.get('action') == "PLAY"), None)
                        if first_play_entry and first_play_entry.get('player') == teammate:
                            teammate_round_status = "队友首发"
                        else:
                            teammate_round_status = "队友已出牌"
                # 若本轮还没有轮到队友，则保持默认文案

            teammate_is_leader = False
            is_self_round = False
            if current_round_entries:
                first_play_entry = next((entry for entry in current_round_entries if entry.get('action') == "PLAY"), None)
                if first_play_entry:
                    if first_play_entry.get('player') == teammate:
                        teammate_is_leader = True
                    elif first_play_entry.get('player') == role:
                        is_self_round = True

            teammate_passed_after_opponent = False
            if current_round_entries and last_move and teammate not in finished_players:
                last_play_idx = None
                last_play_entry = None
                for idx in range(len(current_round_entries) - 1, -1, -1):
                    entry = current_round_entries[idx]
                    if (entry.get('action') or "").upper() == "PLAY":
                        last_play_idx = idx
                        last_play_entry = entry
                        break
                if last_play_idx is not None and last_play_entry:
                    last_play_player = last_play_entry.get('player')
                    if last_play_player and last_play_player != teammate:
                        for entry in current_round_entries[last_play_idx + 1:]:
                            action_upper = (entry.get('action') or "").upper()
                            if action_upper == "PLAY":
                                break
                            if action_upper == "PASS" and entry.get('player') == teammate:
                                teammate_passed_after_opponent = True
                                break

            # 检查对手是否已出完牌
            opponents = ["LeftBot", "RightBot"] if role == "PartnerBot" else ["User", "PartnerBot"]
            
            # 临时修复：在 prompt 中增加对“接风”的强调
            is_leader = (last_move is None)
            
            # 队友状态
            teammate_info = f"{teammate}"
            if teammate in finished_players:
                teammate_info += " (已游/已出完牌)"
            
            # 局势描述
            finished_count = len(finished_players)
            teammate_finished = teammate in finished_players
            finished_names_str = f" (已出完: {', '.join(finished_players)})" if finished_players else ""

            if finished_count == 0:
                scenario_label = "2v2"
            elif finished_count == 1:
                scenario_label = "1v2" if teammate_finished else "2v1"
            elif finished_count == 2:
                scenario_label = "1v1"
            else:
                scenario_label = str(finished_count)

            game_status_desc = f"【{scenario_label}模式{finished_names_str}】"
            counts_summary = ""
            if remaining_counts:
                order = ["User", "RightBot", "PartnerBot", "LeftBot"]
                parts = [f"{p}:{remaining_counts[p]}张" for p in order if p in remaining_counts]
                if parts:
                    counts_summary = ", ".join(parts)

            if finished_players:
                if teammate in finished_players:
                    game_status_desc += " 你的队友已经安全上岸，现在你需要独自战斗，尽量不垫底！"
                else:
                    game_status_desc += " 你的队友还在场上，请全力配合！"
                if counts_summary:
                    game_status_desc += f" 各方目前剩余手牌（已扣除本轮已出的牌）：{counts_summary}。"
            else:
                if counts_summary:
                    game_status_desc += f" 暂无玩家出完牌。各方目前剩余手牌（已扣除本轮已出的牌）：{counts_summary}。"
                else:
                    game_status_desc += " 暂无玩家出完牌，牌局仍在进行中。"
            
            # --- 构建本轮出牌详情 (Round Context) ---
            # 我们需要从 history 中倒推，找到最近一次有人出牌（非PASS）的记录，作为本轮的开始？
            # 或者更简单：从 history 中找到最近一次 "首发" (即上一手是 PASS 且 pass_count=3，或者 history 为空)
            # 但 history 只是流水账。
            # 更好的方法是：倒序遍历 history，直到找到一个 "winner" (上一轮赢家) 或者 找到 3 个连续 PASS。
            # 实际上，last_move 已经告诉了我们当前桌面上最大的牌是谁出的。
            # 我们只需要展示：从 last_move 的出牌者开始，到现在的出牌情况。
            
            round_log = []
            first_play_entry = next((entry for entry in current_round_entries if entry.get('action') == "PLAY"), None)
            first_play_player = first_play_entry.get('player') if first_play_entry else None

            def format_player_label(name: str) -> str:
                if name == role:
                    return f"{name} (你)"
                if name == teammate:
                    return f"{name} (队友)"
                return name

            def describe_entry(entry: dict) -> str:
                if not entry:
                    return ""
                action_upper = (entry.get('action') or '').upper()
                if action_upper == 'PASS':
                    return "PASS"
                if action_upper == 'PLAY':
                    return entry.get('desc') or "出牌"
                return entry.get('desc') or entry.get('action') or "——"

            round_entries = [
                e for e in current_round_entries
                if (e.get('action') or '').upper() in ('PLAY', 'PASS')
            ]
            for entry in round_entries:
                name = entry.get('player')
                if not name:
                    continue
                label = format_player_label(name)
                status = describe_entry(entry)
                if entry is first_play_entry:
                    status = f"{status}（本轮首发）"
                elif name == first_play_player and (entry.get('action') or '').upper() == 'PLAY':
                    status = f"{status}（跟牌出牌）"
                round_log.append(f"{label}: {status}")

            # 末尾标注当前AI需要出牌
            if not round_entries or round_entries[-1].get('player') != role:
                suffix = "（本轮首发）" if not round_entries else ""
                round_log.append(f"{format_player_label(role)}: 轮到你出牌{suffix}")

            round_context_str = "\n".join(round_log)

            def _extract_player_analysis_snapshot(entries: list) -> Optional[dict]:
                if not entries:
                    return None
                for h in reversed(entries):
                    if not isinstance(h, dict):
                        continue
                    snapshot = h.get("player_analysis") or h.get("analysis_snapshot") or h.get("analysis")
                    if isinstance(snapshot, dict):
                        return snapshot
                return None

            def _format_player_analysis_detail(info) -> str:
                if info is None:
                    return ""
                if isinstance(info, str):
                    return info
                if isinstance(info, list):
                    return "；".join(str(x) for x in info if x is not None)
                if isinstance(info, dict):
                    if info.get("summary"):
                        return str(info.get("summary"))
                    if info.get("segments"):
                        return "；".join(str(x) for x in info.get("segments") if x is not None)
                return str(info)

            player_analysis_snapshot = analysis_snapshot or _extract_player_analysis_snapshot(history)
            player_analysis_lines = []
            if player_analysis_snapshot:
                try:
                    role_idx = PLAYERS_ORDER.index(role)
                except Exception:
                    role_idx = 0
                up_player = PLAYERS_ORDER[(role_idx - 1) % len(PLAYERS_ORDER)]
                down_player = PLAYERS_ORDER[(role_idx + 1) % len(PLAYERS_ORDER)]

                ordered_players = [up_player, role, down_player, teammate]
                for p in ordered_players:
                    if not p:
                        continue
                    detail = _format_player_analysis_detail(player_analysis_snapshot.get(p))
                    if not detail:
                        continue
                    if p == role:
                        label = f"我（{role}）"
                    elif p == teammate:
                        label = f"{p}（队友）"
                    elif p == up_player:
                        label = f"上家{p}（对手）"
                    elif p == down_player:
                        label = f"下家{p}（对手）"
                    else:
                        label = f"{p}（对手）"
                    player_analysis_lines.append(f"{label}：{detail}")

            player_analysis_str = "\n".join(player_analysis_lines) if player_analysis_lines else "暂无玩家出牌分析数据。"

            # --- 基于已出大牌统计的控牌提醒（2/王/A） ---
            def _extract_count(text: str, label: str) -> int:
                if not text:
                    return 0
                import re
                matches = re.findall(rf"{label}（(\d+)张）", text)
                if not matches:
                    return 0
                total = 0
                for val in matches:
                    try:
                        total += int(val)
                    except Exception:
                        continue
                return total

            jokers_out = 0
            twos_out = 0
            aces_out = 0
            small_jokers_out = 0
            big_jokers_out = 0
            if player_analysis_snapshot:
                snapshot_texts = []
                for v in player_analysis_snapshot.values():
                    detail = _format_player_analysis_detail(v)
                    if detail:
                        snapshot_texts.append(detail)
                merged = "；".join(snapshot_texts)
                small_jokers_out += _extract_count(merged, "小王")
                big_jokers_out += _extract_count(merged, "大王")
                jokers_out = small_jokers_out + big_jokers_out
                twos_out += _extract_count(merged, "2")
                aces_out += _extract_count(merged, "A")

            def _hand_count_rank(label: str) -> int:
                if not hand_cards: return 0
                cnt = 0
                for c in hand_cards:
                    s = str(c).replace('♣', '').replace('♠', '').replace('♦', '').replace('♥', '').replace('JK', '小王').replace('BQ', '大王')
                    if label == s or (label in s and label not in ['小王', '大王']): 
                        cnt += 1
                return cnt

            my_big_joker_count = _hand_count_rank('大王')
            my_small_joker_count = _hand_count_rank('小王')
            my_two_count = _hand_count_rank('2')
            my_ace_count = _hand_count_rank('A')

            has_big_joker = my_big_joker_count > 0
            has_small_joker = my_small_joker_count > 0
            has_two = my_two_count > 0
            has_ace = my_ace_count > 0

            # 修正统计逻辑：显示全场未出的大牌总数（含玩家手中）
            # “全场剩余” = 总量 - 已经打出的
            total_big_joker_remaining = 2 - big_jokers_out
            total_small_joker_remaining = 2 - small_jokers_out
            total_two_remaining = 8 - twos_out
            total_ace_remaining = 8 - aces_out

            # 判定其它玩家（对手+队友）手中是否还持有该牌
            # 逻辑：全场未出 - 我手中持有 > 0
            other_has_big_joker = total_big_joker_remaining > my_big_joker_count
            other_has_small_joker = total_small_joker_remaining > my_small_joker_count
            other_has_two = total_two_remaining > my_two_count
            other_has_ace = total_ace_remaining > my_ace_count

            # 全场剩余大牌统计 (包含你手中)
            key_cards_summary = f"全场剩余大牌统计（包含你手中）：大王: {max(0, total_big_joker_remaining)}张, 小王: {max(0, total_small_joker_remaining)}张, 2: {max(0, total_two_remaining)}张, A: {max(0, total_ace_remaining)}张。"
            player_analysis_str = (player_analysis_str + "\n" + key_cards_summary).strip()

            control_ready = {
                "大王": bool(has_big_joker),
                "小王": bool(has_small_joker and not other_has_big_joker),
                "2": bool(has_two and not other_has_big_joker and not other_has_small_joker),
                "A": bool(has_ace and not other_has_big_joker and not other_has_small_joker and not other_has_two),
            }

            control_hints = []
            # 只要其它人手里没有王了，2就是强控
            if (not other_has_big_joker and not other_has_small_joker):
                if has_two:
                    control_hints.append("提示：外界已无王牌（已出完或在你手中），当前【**单张2**】具备绝对控牌价值（对手只能炸弹管），应优先用来争取下一轮首发或逼对手交炸（但若需拆开唯一的大对子22，请慎重评估是否会失去后续防守能力）。")
                if has_small_joker and not other_has_big_joker:
                    control_hints.append("提示：外界已无大王（已出完或在你手中），当前【**小王**】具备绝对控牌价值（对手只能炸弹管）。")
            
            # A的逻辑同理
            if (not other_has_big_joker and not other_has_small_joker and not other_has_two):
                if has_ace:
                    control_hints.append("提示：外界已无王/2（已出完或在你手中），当前【**单张A**】具备绝对控牌价值，可用来争取下一轮首发或消耗对手炸弹。")

            if control_hints:
                player_analysis_str = (player_analysis_str + "\n" + "\n".join(control_hints)).strip()

            last_play_player = None
            if history:
                for h in reversed(history):
                    if h.get('action') == 'PLAY':
                        last_play_player = h.get('player')
                        break

            # --- 接风/首发权推断（以引擎 ROUND_END 规则为准，避免 LLM 自行脑补） ---
            last_round_end = _get_last_round_end(history)
            last_round_winner, inferred_next_leader, winner_finished = _compute_next_leader_from_round_end(last_round_end or {})

            # is_takeover：仅当“队友作为赢家且已完牌 -> 由你(对家)首发”时成立。
            is_takeover = bool(
                is_leader
                and inferred_next_leader == role
                and winner_finished
                and last_round_winner == teammate
            )

            leader_source_text = ""
            if is_leader:
                if last_round_end and last_round_winner and inferred_next_leader:
                    if winner_finished:
                        leader_source_text = f"系统接风：上一轮赢家 {last_round_winner} 已完牌 -> 由其对家 {inferred_next_leader} 首发"
                    else:
                        leader_source_text = f"系统首发：上一轮赢家 {last_round_winner} 未完牌 -> 赢家继续首发"
                else:
                    leader_source_text = "首发：尚无可用 ROUND_END 记录（开局或系统未记录）"
            
            # --- 获取 Coach 建议 ---
            coach_advice_str = ""
            try:
                advice_file = "coach_advice.json"
                if os.path.exists(advice_file):
                    with open(advice_file, 'r', encoding='utf-8') as f:
                        all_advice = json.load(f)
                    
                    # 筛选针对当前角色的建议
                    my_advice = [a for a in all_advice if a.get('player') == role]
                    if my_advice:
                        advice_texts = [f"- {a['mistake']} -> {a['advice']}" for a in my_advice]
                        coach_advice_str = "\n【教练指导 (历史教训)】\n" + "\n".join(advice_texts)
            except Exception as e:
                log.error(f"  [WARN] 读取 Coach 建议失败: {e}")

            # --- 残局战术 (End Game Strategy) ---
            # 已移至 tactics.py 处理

            # --- 动态构建核心战术 (Dynamic Strategy Construction) ---
            strategies = []
            
            # --- 1. 定义战术原则库 (Tactical Library) ---
            # 已移至 tactics.py

            # --- 2. 动态构建策略列表 ---
            hand_structure = {
                "isolated_singles": isolated_singles,
                "pairs": pairs,
                "triples": triples,
                "bombs": bombs
            }

            stage_info, stage_strategies, trigger_strategies, optimization_data = get_tactical_strategies(
                game_stage=game_stage,
                stage_focus=stage_focus,
                teammate=teammate,
                finished_players=finished_players,
                is_teammate_move=is_teammate_move,
                is_leader=is_leader,
                is_takeover=is_takeover,
                teammate_passed_after_opponent=teammate_passed_after_opponent,
                last_move=last_move,
                can_play_moves=can_play_moves,
                has_red_heart_2=has_red_heart_2,
                bombs=bombs,
                straight_flushes=straight_flushes,
                remaining_counts=remaining_counts,
                opponents=opponents,
                hand_structure=hand_structure,
                red_heart_2_count=red_heart_2_count,
                teammate_is_leader=teammate_is_leader,
                is_self_round=is_self_round,
                hand_cards=hand_cards,
                role=role,
                hand_card_ids=hand_card_ids,
                control_card_ready=control_ready
            )

            def format_strategy_list(items: list, prefix: str = None, bold: bool = False) -> str:
                if not items:
                    return ""
                if prefix is not None:
                    formatted = []
                    for title, content in items:
                        title_text = f"**{title}**" if bold else title
                        formatted.append(f"{prefix}{title_text}：{content}")
                    return "\n".join(formatted)
                return "\n".join([f"{idx}. {title}：{content}" for idx, (title, content) in enumerate(items, 1)])

            stage_title, stage_focus_text = stage_info if stage_info else ("未知阶段", "")
            stage_info_text = f"{stage_title} —— {stage_focus_text}".strip()

            stage_strategy_text = format_strategy_list(stage_strategies, prefix="-- ", bold=True) or "暂无针对性战术"

            trigger_parts = []
            trigger_list_text = format_strategy_list(trigger_strategies, prefix="-- ", bold=True)
            if trigger_list_text:
                trigger_parts.append(trigger_list_text)
            if bomb_warning_section.strip():
                has_bomb_trigger = any(
                    ("炸弹" in (title or "") or "炸弹" in (content or ""))
                    for title, content in (trigger_strategies or [])
                )
                if not has_bomb_trigger:
                    trigger_parts.append(bomb_warning_section.strip())
            trigger_strategy_text = "\n".join(trigger_parts) if trigger_parts else "暂无额外提醒"

            if hand_structure_section.strip():
                hand_structure_text = hand_structure_section.strip()
            else:
                hand_structure_text = ""

            optimization_title = ""
            optimization_text = ""
            optimization_scope = "高优先"
            if optimization_data:
                # Remove emoji for cleaner title if needed, or keep it
                optimization_title = optimization_data['title'].replace('🔢 ', '')
                optimization_text = optimization_data['content']
                optimization_scope = optimization_data.get('scope_label', optimization_scope)
            
            if padding_suggestion_text:
                optimization_text += f"\n{padding_suggestion_text}"

            wild_hint_line = ""
            if has_red_heart_2:
                if red_heart_2_count <= 1:
                    wild_hint_line = "提示：红桃2仅一张，所有带红桃2的选项互斥，本轮只能使用其中一种；使用后对应三张/对子/炸弹等需要红桃2配的组合牌型会被拆开。\n"
                else:
                    wild_hint_line = (
                        f"提示：红桃2共{red_heart_2_count}张，含红桃2的选项会占用红桃2资源。"
                        "尽量把两张红桃2分开用于两次炸弹/必要牌型，避免在同一牌型中同时占用2张；"
                        "除非必须使用、牌力非常富裕，或为了升级更大炸弹控牌。\n"
                    )

            hand_structure_line = f"\n{hand_structure_text}" if hand_structure_text else ""

            prompt = f"""
一、 我是{role}。我的队友是{teammate_info}。对手是{', '.join(opponents)}。
1、座位顺序: User -> RightBot -> PartnerBot -> LeftBot -> User ...
2、局势状态：{game_status_desc}
3、本轮出牌详情 (从旧到新)：
{round_context_str}
4、玩家出牌分析（以下为**已出的大牌/炸弹统计**，不代表玩家手里剩牌；请据此调整控牌策略）：
{player_analysis_str}
二、手牌情况：
1、手牌: {hand_cards}。{hand_structure_line}
2、合法选项：
{wild_hint_line}
{moves_str}
3、组牌最优策略{('（' + optimization_scope + '）') if optimization_scope else ''}：
{optimization_title}
{optimization_text}
三、牌局阶段及策略
1、当前牌局阶段：
{stage_info_text}
2、当前战术策略：
{stage_strategy_text}
3、牌局触发提醒：
{trigger_strategy_text}

四、输出 JSON 格式，包含三个字段：
- "thought": 你的战术思考过程（简短分析局势、队友意图、手牌优劣）。
- "id": 你选择的选项 ID (整数)。
- "desc": 你选择的选项描述 (字符串)。
- 格式铁律：只返回一个 JSON 对象，禁止使用 markdown 代码块(不要出现 ```)，禁止在 JSON 外添加任何文字。
- "thought" 必须是单行纯文本：不得包含换行符、回车符、双引号；如需分隔请用空格。
- 必须保证 JSON 完整闭合（以 }} 结尾），否则系统无法解析你的决策。
"""

            # --- System Prompt Construction ---
            # 优化：仅在首发时包含完整规则，跟牌时仅保留基础人设，节省 Token
            system_content = TACTICS_DB["system_prompts"]["base"]
            if is_leader:
                system_content += "\n\n" + TACTICS_DB["system_prompts"]["static_rules"]

            # Keep an immutable copy for retries/extra warnings.
            original_prompt = prompt

            # --- Retry Loop for Validation ---
            max_retries = 3
            final_choice = 0
            
            for attempt in range(max_retries):
                # 动态构建参数，处理不支持 temperature 的模型 (如 o1-preview)
                completion_args = {
                    "model": LLMConfigManager.get_model_name(role),
                    "messages": [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": prompt}
                    ]
                }
                
                temp = LLMConfigManager.get_temperature(role)
                if temp is not None:
                    completion_args["temperature"] = temp

                # 增加超时设置
                completion_args["timeout"] = 10.0

                try:
                    response = None
                    last_err = None
                    for conn_attempt in range(1, 5):
                        try:
                            import datetime as _dt
                            _t0 = time.time()
                            _ts = _dt.datetime.now().strftime("%H:%M:%S")
                            response = await client.chat.completions.create(**completion_args)
                            _dt_s = time.time() - _t0
                            log.debug(f"  [LLM-TIMING] {role} conn_attempt={conn_attempt} 开始={_ts} 耗时={_dt_s:.2f}s 模型={LLMConfigManager.get_model_name(role)}")
                            break
                        except Exception as e:
                            last_err = e
                            # 超时直接跳出，进入本地后备逻辑
                            if httpx is not None and isinstance(e, getattr(httpx, "TimeoutException", tuple())):
                                log.info(f"  [Timeout] AI ({role}) 响应超时 (单次请求 {completion_args.get('timeout', 10)}s)")
                                raise

                            # 连接类错误：等待3秒后重试，最多3次
                            is_conn_error = False
                            if httpx is not None:
                                conn_types = tuple(
                                    t for t in (
                                        getattr(httpx, "ConnectError", None),
                                        getattr(httpx, "NetworkError", None),
                                        getattr(httpx, "RemoteProtocolError", None),
                                        getattr(httpx, "ConnectTimeout", None),
                                    ) if t is not None
                                )
                                if conn_types and isinstance(e, conn_types):
                                    is_conn_error = True

                            if not is_conn_error and "connection" in str(e).lower():
                                is_conn_error = True

                            if is_conn_error and conn_attempt < 4:
                                log.error(f"  [WARN] LLM 连接失败，{conn_attempt}/4，5秒后重试...")
                                await asyncio.sleep(5)
                                continue

                            # 自动降级：如果报错包含 temperature，尝试移除该参数重试
                            err_msg = str(e).lower()
                            if "temperature" in err_msg and ("unsupported" in err_msg or "parameter" in err_msg or "invalid" in err_msg or "400" in err_msg):
                                log.warning(f"  [WARN] [AutoFix] 模型不支持 temperature 参数，正在移除并重试...")
                                if "temperature" in completion_args:
                                    del completion_args["temperature"]
                                LLMConfigManager.disable_temperature(role)
                                response = await client.chat.completions.create(**completion_args)
                                break

                            raise e

                    if response is None and last_err is not None:
                        raise last_err
                except Exception as e:
                    if httpx is not None and isinstance(e, getattr(httpx, "TimeoutException", tuple())):
                        # 跳出循环，触发后续的本地后备逻辑
                        break
                    raise e

                content = response.choices[0].message.content

                # [Add] 保存决策上下文到缓存；有 game_id 时按局隔离写入（避免多局并发跨局串档），
                # 否则写全局兜底（兼容 sync get_ai_decision 等无局路径）。
                _ctx_write = {
                    "system_prompt": system_content,
                    "user_prompt": prompt + (REMIND_SF if any(m.get("type") == 30 for m in valid_moves) else ""),
                    "ai_response": content
                }
                if game_id:
                    set_ai_context(game_id, role, _ctx_write, len(history) if history else None)
                else:
                    LAST_AI_CONTEXTS[role] = _ctx_write

                # --- 增强的 JSON 提取逻辑 ---
                json_str = content
                
                # 1. 移除 markdown 代码块标记 (兼容 ```json ... ```)
                if json_str.strip().startswith("```"):
                    # 找到第一个换行符
                    first_newline = json_str.find('\n')
                    if first_newline != -1:
                        json_str = json_str[first_newline+1:]
                
                if json_str.strip().endswith("```"):
                    # 找到最后一个换行符
                    last_newline = json_str.rfind('\n')
                    if last_newline != -1:
                        json_str = json_str[:last_newline]

                # 2. 寻找 JSON 对象
                start_index = json_str.find('{')
                end_index = json_str.rfind('}')
                
                if start_index != -1 and end_index != -1 and start_index < end_index:
                    json_str = json_str[start_index : end_index + 1]
                else:
                    # 如果找不到，就将整个内容作为尝试解析的对象，以防万一
                    json_str = json_str
                # --- 提取结束 ---

                content = json_str.strip()

                try:
                    result = json.loads(content)
                    choice_id = int(result.get("id", 0))
                    choice_desc = result.get("desc", "")
                    thought = result.get("thought", "无思考过程")
                    
                    # --- Validation Logic ---
                    # 1. Check if ID exists in valid_moves
                    selected_move = next((m for m in valid_moves if m['id'] == choice_id), None)
                    
                    if not selected_move:
                        log.warning(f"  [WARN] [Attempt {attempt+1}] AI 选择了不存在的 ID: {choice_id}")
                        prompt += f"\n\n错误：你选择的 ID {choice_id} 不在合法选项列表中。请重新选择一个存在的 ID。"
                        continue

                    # 0. Hard guard: opening/midgame small-single padding should not PASS
                    if selected_move.get('type') == 0 and must_pad_single_candidates:
                        best_pad = min(must_pad_single_candidates, key=lambda x: int(x.get('rank', 999)))
                        log.info(f"  [PadGuard] LLM 选择 PASS，但存在不拆牌单张可顺：强制改为 {best_pad.get('desc')} (ID {best_pad.get('id')})")
                        # [Log] 已经保存到本地 errorPlay，控制台不再保留完整 Prompt
                        await asyncio.sleep(random.uniform(1, 3))
                        return int(best_pad.get('id'))
                        
                    # 2. Check if Description matches (Anti-Hallucination)
                    # 允许模糊匹配，但关键类型必须一致
                    real_desc = selected_move['desc']
                    
                    # 简单检查：如果 AI 说是 "单张" 但实际是 "炸弹"，或者 "对子"
                    # 提取关键词（复用模块级 _type_keyword/_real_type_name）
                    def get_type_keyword(s):
                        return _type_keyword(s)

                    def get_real_type_name(m_type):
                        return _real_type_name(m_type)

                    ai_type = get_type_keyword(choice_desc)
                    real_type = get_real_type_name(selected_move['type'])
                    
                    # 宽松匹配逻辑：
                    # 1. 如果 ai_type 和 real_type 一致，通过
                    # 2. 如果 real_type 的名称直接出现在描述中，通过 (解决 "钢板(非炸弹)" 被识别为炸弹的问题)
                    # 2.5 如果 AI 的描述包含真实选项的 desc（允许附加说明），通过
                    # 3. 如果无法从描述中提取类型 (未知)，也默认通过，信任 ID
                    
                    is_match = False
                    if ai_type == real_type:
                        is_match = True
                    elif real_type in choice_desc:
                        is_match = True
                    elif real_desc and real_desc in choice_desc:
                        is_match = True
                    elif ai_type == "未知":
                        is_match = True

                    if not is_match:
                        attempt += 1
                        
                        # 分析错误原因：是否是"拆炸弹"导致的幻觉
                        error_reason = ""
                        if "拆炸弹" in real_desc or "含赖子" in real_desc:
                            error_reason = (
                                f"\n**核心问题**：你选择的 ID {choice_id} 描述为 '{real_desc}'，"
                                f"这是一个**拆炸弹/消耗赖子**的牌型，而你想出的是 '{choice_desc}'。"
                                f"\n请检查【合法选项】中是否有**不拆炸弹**的替代选项（例如：用现成的对子/三张/顺子，而不是从炸弹中拆出来的）。"
                            )
                        else:
                            error_reason = f"\n你想出 '{choice_desc}'，但 ID {choice_id} 实际是 '{real_desc}'，两者不匹配。"
                        
                        log.warning(f"  [WARN] [Attempt {attempt}] 幻觉检测: AI 想出 '{choice_desc}' ({ai_type}), 但 ID {choice_id} 是 '{real_desc}' ({real_type})")
                        
                        if attempt >= max_retries:
                            # 首发时不能PASS，需要第4次机会
                            is_leader = (last_move is None or last_move.get('type') == 0)
                            has_pass = any(m.get('type') == 0 for m in valid_moves)
                            
                            if is_leader and not has_pass:
                                log.warning(f"  [WARN] [最后机会] 首发不能PASS，给予第 {attempt + 1} 次尝试")
                                extra_warning = (
                                    f"\n\n**最后机会警告**：你已经连续 {attempt} 次选择了错误的牌型。"
                                    f"{error_reason}"
                                    f"\n\n**当前是首发，你必须出牌，不能PASS！**"
                                    f"\n请从【合法选项】中仔细选择一个**真正存在**的牌型ID，不要再出现幻觉。"
                                    f"\n建议：优先选择最小的单张/对子/三张，避免拆炸弹。"
                                )
                                prompt = original_prompt + extra_warning
                                max_retries += 1
                                continue
                            else:
                                # 非首发或有PASS选项：使用PASS或默认选项
                                log.warning(f"  [WARN] 重试次数耗尽，使用默认策略")
                                pass_move = next((m for m in valid_moves if m['type'] == 0), None)
                                if pass_move:
                                    log.info(f"  → 默认选择 PASS")
                                    await asyncio.sleep(random.uniform(1, 3))
                                    return pass_move['id']
                                
                                # 实在没办法：选择"最安全"的出牌
                                # 优先级：最小单张 > 最小对子 > 最小三张 > 其他
                                def rank_sort_key(rank_val):
                                    if rank_val is None:
                                        return 999
                                    r = int(rank_val)
                                    if r == 16 or r == 17:
                                        return r + 100
                                    elif r == 15:
                                        return 14.5
                                    return r
                                
                                safe_move = None
                                for move_type in [1, 2, 3]:
                                    candidates = [m for m in valid_moves if m.get('type') == move_type]
                                    if candidates:
                                        safe_move = min(candidates, key=lambda m: rank_sort_key(m.get('rank')))
                                        break
                                
                                if safe_move:
                                    log.info(f"  → 强制选择最安全出牌: {safe_move.get('desc')} (ID {safe_move['id']})")
                                    # 设置标记供game_engine使用
                                    get_ai_decision._force_play_warning = True

                                    setattr(get_ai_decision, '_fallback_reason', "大模型连续幻觉/校验重试耗尽，系统强制默认出牌")  # [方案1]
                                    await asyncio.sleep(random.uniform(1, 3))
                                    return safe_move['id']
                                
                                # 最后兜底
                                log.info(f"  → 强制选择第一个合法选项: {valid_moves[0].get('desc')}")
                                get_ai_decision._force_play_warning = True

                                setattr(get_ai_decision, '_fallback_reason', "大模型连续幻觉/校验重试耗尽，系统强制默认出牌")  # [方案1]
                                await asyncio.sleep(random.uniform(1, 3))
                                return valid_moves[0]['id']
                        
                        # 未到重试上限：重新构造prompt
                        extra_warning = (
                            f"\n\n**第 {attempt} 次警告**：{error_reason}"
                            f"\n请仔细核对【合法选项】中的牌型描述，确保你选择的 ID 对应的牌型是你真正想出的！"
                            f"\n你现在输出的 desc='{choice_desc}'，但该 desc 与 ID {choice_id} 不对应。"
                            f"\nID {choice_id} 的真实牌型是：'{real_desc}'。请从【合法选项】里原样复制你选中的那一行的完整 desc，"
                            f"保证 desc 里的牌型名称与 ID 完全对应，不要改写或脑补。"
                        )
                        prompt = original_prompt + extra_warning
                        continue
                    
                    # Validation Passed
                    import datetime as _dt2
                    _ts2 = _dt2.datetime.now().strftime("%H:%M:%S")
                    log.info(f"  [LLM] {role} [{_ts2}] 思考过程(len={len(thought) if thought else 0}): {thought}")
                    log.info(f"  > LLM 决策 ID: {choice_id} ({real_desc})")
                    return choice_id

                except json.JSONDecodeError:
                    import re as _re
                    _m_id = _re.search(r'"id"\s*:\s*(\d+)', content)
                    _m_desc = _re.search(r'"desc"\s*:\s*"([^"]*)"', content)
                    if _m_id:
                        # 容错：thought 含裸换行导致严格解析失败时，用正则兜底提取 id/desc，避免浪费正确决策
                        result = {"id": int(_m_id.group(1)), "desc": _m_desc.group(1) if _m_desc else "", "thought": ""}
                        log.warning(f"  [WARN] JSON 严格解析失败，已正则兜底提取 id={result['id']} desc={result.get('desc')}")
                        # 正则已提取出可用决策：若 id 存在且描述与牌型匹配，直接采用，不再浪费一次请求重试。
                        # 否则（id 不存在 / 描述是幻觉）追加纠错信息后重试，让模型从合法选项里选真的。
                        _sm = next((m for m in valid_moves if m.get('id') == result['id']), None)
                        if _sm is not None and _desc_matches_move(result.get('desc', ''), _sm):
                            log.info(f"  > [JSON兜底] 采用正则提取的决策 ID: {result['id']} ({_sm.get('desc')})")
                            return int(result['id'])
                        prompt += (
                            f"\n\n错误：你输出的 JSON 无法被解析，提取出的 id={result.get('id')} "
                            f"或描述无效。请严格输出合法 JSON（含 id/desc/thought 三个字段），"
                            f"并确保 id 与 desc 都从【合法选项】中原样复制。"
                        )
                    else:
                        log.error(f"  [WARN] JSON 解析失败且无 id: {content}")
                        prompt += "\n\n错误：请输出合法的 JSON 格式。"
                except Exception as e:
                    log.warning(f"  [WARN] 验证过程出错: {e}")
                    break
            
            # If retries exhausted, fallback to local logic
            log.warning("  [WARN] 重试次数耗尽，切换到本地策略")
            pass

        except Exception as e:
            log.error(f"  [WARN] LLM 调用失败: {e}")
            # 失败后，掉落到下方的本地逻辑
            pass
            # try:
            #     result = json.loads(content)
            # ...

    # 4. LLM 重试耗尽 -> 抛明确异常，交由上层(game_engine)统一兜底(规则见 local_fallback_move)
    reason = "大模型调用失败或超时，已切换本地贪心策略兜底（出最小合法牌）"
    setattr(get_ai_decision, '_fallback_reason', reason)
    log.warning(f"  [WARN] LLM 决策重试耗尽，抛出 AIDecisionError 交由上层兜底: {reason}")
    raise AIDecisionError(reason)
    
    # 构造 table_info 字符串供本地逻辑使用 (兼容旧代码)
    table_info = "无 (首发)"
    if last_move:
        table_info = f"{last_move['player']} 出了 {last_move['desc']}"

    # 确定队友
    teammate = "User" if role == "PartnerBot" else ("RightBot" if role == "LeftBot" else "LeftBot")
    
    # 如果我是首发 (Last move is None or empty)，必须出第一张合法的牌
    if "无 (首发)" in table_info:
        # 这里的策略是：出最小的牌型 (通常是单张)
        # 假设 valid_moves[1] 是最小的单张/对子
        # 优先出单张、对子、三带二等小牌，保留炸弹
        # 【硬约束】同花顺(type=30)是顶级炸弹(王炸级)，严禁当普通顺子打出/清理；仅当需控牌(对手≤3张)或拦截(下家即将走完)时才出
        # valid_moves 通常包含所有组合，我们需要筛选
        
        # 简单排序：优先出非炸弹，且牌值最小的
        # 假设 valid_moves 里的 'rank' 是牌的大小，'type' 是牌型
        # 我们希望 type 小 (普通牌)，rank 小
        
        # 过滤掉 PASS (type=0)
        real_moves = [m for m in can_play_moves if m['type'] != 0]
        
        if not real_moves:
            return 0
            
        # 若对手剩牌≤3且我方只剩单张可首发（典型两轮出尽场景），应先出最大单张控权
        try:
            opps = ["LeftBot", "RightBot"] if role in ("User", "PartnerBot") else ["User", "PartnerBot"]
            any_opp_low = any(int(remaining_counts.get(o, 99)) <= 3 for o in opps) if remaining_counts else False
        except Exception:
            any_opp_low = False

        single_moves = [m for m in real_moves if m.get('type') == 1]
        if any_opp_low and single_moves and len(real_moves) == len(single_moves):
            # 只剩单张可首发，优先出最大单张
            single_moves.sort(key=lambda m: m.get('rank') or 0, reverse=True)
            await asyncio.sleep(random.uniform(1, 3))
            return single_moves[0]['id']

        # 排序键：
        # 1. 是否炸弹 (type >= 20 是炸弹，我们要后出) -> 0: 普通, 1: 炸弹
        # 2. 牌型大小 (type) -> 小的先出 (单张 < 对子 < ...)
        # 3. 牌面大小 (rank) -> 小的先出
        
        def sort_key(m):
            is_bomb = 1 if m['type'] >= 20 else 0
            return (is_bomb, m['type'], m['rank'])
            
        real_moves.sort(key=sort_key)
        best_move = real_moves[0]
        await asyncio.sleep(random.uniform(1, 3))
        return best_move['id']
    
    # 如果是跟牌
    else:
        # 检查是否是队友出的牌
        # table_info 格式: "{player_name} 出了 {desc}"
        last_player_name = table_info.split(" ")[0]
        
        if last_player_name == teammate:
            # 默认让牌，除非：
            # 1. 我能走完 (手牌数 == 出牌数)
            # 2. 队友出的牌很小 (比如单张 < 10)，且我有大牌可以接管 (暂不实现，太复杂)
            
            # 检查是否能走完
            # 我们需要知道当前手牌数。hand_cards 是字符串列表，len(hand_cards) 是手牌数
            # 遍历所有能出的牌，看有没有哪张牌打出去后手牌就空了
            # 注意：valid_moves 里的 cards 长度就是打出去的张数
            
            can_finish = False
            winning_move_id = 0
            
            current_hand_count = len(hand_cards)
            
            for m in can_play_moves:
                if m['type'] == 0: continue
                # m['cards'] 是 Card 对象列表，或者 m['card_ids'] 是 ID 列表
                # 我们用 len(m['card_ids'])
                if len(m['card_ids']) == current_hand_count:
                    can_finish = True
                    winning_move_id = m['id']
                    break
            
            if can_finish:
                log.info(f"  > 队友 ({teammate}) 出牌，但我能走完！直接压死获胜！")
                await asyncio.sleep(random.uniform(1, 3))
                return winning_move_id
            else:
                if any_opponent_low_cards(threshold=3):
                    log.info(f"  > [Safety] 对手≤3张，禁止自动PASS：继续决策")
                    # 继续往下选最小可管牌，避免放走对手
                    pass
                else:
                    log.info(f"  > 队友 ({teammate}) 此时占优，选择 PASS 让牌")
                    await asyncio.sleep(random.uniform(1, 3))
                    return 0
            
        # 简单策略：能管上就管，且出最小的那个
        # 同样需要排序，选最小的能管上的牌
        
        real_moves = [m for m in can_play_moves if m['type'] != 0]
        if not real_moves:
            await asyncio.sleep(random.uniform(1, 3))
            return 0
            
        # --- 过滤掉浪费红桃2的愚蠢操作 ---
        # 如果有不含红桃2的选项，就优先选不含红桃2的
        # 除非含红桃2的是炸弹/同花顺
        
        def is_waste_wild(m):
            # 检查是否包含红桃2 (ID以 H15 开头，或者 desc 包含 赖子)
            has_wild = "赖子" in m.get('desc', '') or any(cid.startswith("H-15") for cid in m.get('card_ids', []))
            if not has_wild: return False
            
            # 如果是炸弹(>=20)或同花顺(30)，不算浪费
            if m['type'] >= 20: return False
            
            # 如果是普通牌型(单张、对子、三张、顺子等)，且用了红桃2，视为浪费
            return True

        # 尝试找到不浪费赖子的选项
        better_moves = [m for m in real_moves if not is_waste_wild(m)]
        
        if better_moves:
            real_moves = better_moves
            log.info(f"  > [Smart] 已过滤掉 {len(can_play_moves) - len(real_moves)} 个浪费红桃2的选项")
        else:
            # 如果所有能管上的牌都必须用红桃2 (比如只有 3+H2 能管 22? 不可能，因为 3+H2 < 22)
            # 或者只有 H2 单张能管 A?
            # 这种情况下，如果实在没别的牌，也只能出了。
            # 但如果是为了管一个小对子而用 H2，不如 PASS。
            
            # 如果必须浪费赖子才能管，且对方出的牌不是关键牌（比如只是个小对子），选择 PASS
            # 简单判定：如果对方出的牌 rank < 10，且我们必须用赖子管，就 PASS
            if last_move and last_move.get('rank', 0) < 10:
                 log.info(f"  > [Smart] 必须用红桃2才能管小牌，选择 PASS 保留实力")
                 await asyncio.sleep(random.uniform(1, 3))
                 return 0

        # 跟牌时，valid_moves 应该已经是筛选过能管上的牌了 (由 game_engine 保证)
        # 所以我们只需要选其中最小的一个
        # 排序逻辑同上：尽量不出炸弹，尽量出小的
        
        def sort_key(m):
            is_bomb = 1 if m['type'] >= 20 else 0
            # 如果必须出炸弹才能管上，那就出最小的炸弹
            # 如果能出普通牌管上，就出最小的普通牌
            return (is_bomb, m['type'], m['rank'])
            
        real_moves.sort(key=sort_key)
        best_move = real_moves[0]
        return best_move['id']

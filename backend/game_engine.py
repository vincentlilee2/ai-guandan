# backend/game_engine.py
import random
import hashlib
import json
import os
import time
import asyncio
import threading
from pathlib import Path
from typing import List, Dict, Optional, Any
from .models import Card, Suit, Rank
from .rules import PatternRecognizer, Comparator, CardType
from .scoring import ScoreManager
from .ai_client import clear_ai_contexts, get_ai_decision_async, set_ai_context, get_ai_context, LAST_PROMPTS_BY_GAME
from backend.logger import get_logger
from backend.game_events import game_event_bus  # v2.4 SSE 推送

log = get_logger(__name__)

GAME_DIR = Path(__file__).resolve().parent.parent
HISTORY_DIR = GAME_DIR / "history"
LATEST_REPLAY_FILE = GAME_DIR / "game_history.json"
AI_FALLBACK_REASON = {}  # [Fix] 模块级兜底原因字典(按 role)，替代不可靠的函数对象属性(get_ai_decision_async._fallback_reason 在 async 函数上设属性会抛 AttributeError)

# 3.5 history/ 清理策略：避免无上限堆积（原 game 目录曾堆 511 个 JSON）
# 保留最近 N 个复盘文件，且超过 MAX_AGE_DAYS 天的删除
HISTORY_MAX_FILES = int(os.getenv("GAME_HISTORY_MAX_FILES", "200"))
HISTORY_MAX_AGE_DAYS = int(os.getenv("GAME_HISTORY_MAX_AGE_DAYS", "30"))
# v2.5：save_history / _cleanup_history_dir 写删共享文件加锁，避免并发结束多局交错写坏 game_history.json
# 用 RLock：_cleanup_history_dir 常被 save_history 持有锁期间调用（重入安全）
_history_lock = threading.RLock()


def _cleanup_history_dir() -> None:
    """按数量与年龄上限清理 history/ 旧复盘文件（保留最新）。"""
    with _history_lock:
        try:
            if not HISTORY_DIR.exists():
                return
            files = [p for p in HISTORY_DIR.glob("*.json") if p.is_file()]
            if not files:
                return
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)  # 新→旧
            now = time.time()
            age_limit = HISTORY_MAX_AGE_DAYS * 86400
            removed = 0
            for idx, p in enumerate(files):
                too_old = HISTORY_MAX_AGE_DAYS > 0 and (now - p.stat().st_mtime) > age_limit
                over_cap = idx >= HISTORY_MAX_FILES
                if too_old or over_cap:
                    try:
                        p.unlink()
                        removed += 1
                    except OSError:
                        pass
            if removed:
                log.info(f"[Replay] 已清理 {removed} 个过期复盘文件（保留 {len(files) - removed}）")
        except Exception as e:
            log.warning(f"[Replay] 清理 history 失败（忽略）: {e}")


class GuandanGame:
    def __init__(self, game_id: str):
        self.game_id = game_id
        self.players = ["User", "RightBot", "PartnerBot", "LeftBot"]
        self.hands: Dict[str, List[Card]] = {p: [] for p in self.players}
        self.current_turn_index = 0
        self.history = [] # 记录出牌历史
        self.last_move: Optional[Dict[str, Any]] = None # 桌面上最大的一手牌
        self.last_move_player_idx: int = -1 # 记录谁出的最后一手牌
        self.pass_count = 0   # 连续 PASS 次数
        self.score_manager = ScoreManager()
        self.finished_players = [] # 完牌顺序
        self.state = "waiting" # playing, finished
        self.final_result = None # 存储最终结算结果
        self.score_applied = False # 标记是否已计入总分
        self.initial_hands: Dict[str, List[str]] = {} # 记录初始手牌
        self.analysis_snapshot: Dict[str, str] = {} # 玩家出牌分析快照
        self.played_big_cards: Dict[str, Dict[str, Any]] = {}
        self._ai_processing = False # 防止并发执行 AI 逻辑
        self.seq = 0                     # 状态序列号，每次状态变更递增
        # v2.1 并发改造：每个 game 一个异步锁，保证 AI 回合中 await 让出控制权时
        # 其他协程不会同时修改同一局状态（数据竞争防护）
        try:
            self._ai_lock = asyncio.Lock()
        except Exception:
            self._ai_lock = None
        self.processed_request_ids: set = set()  # 已处理的请求ID（幂等去重）
        self._max_stored_request_ids = 20         # 最多保留最近20个请求ID

    def _bump_seq(self):
        """递增状态序列号"""
        self.seq += 1
        return self.seq

    def is_duplicate_request(self, request_id: str) -> bool:
        """检查请求ID是否已处理过（幂等性保护）"""
        return request_id in self.processed_request_ids if request_id else False

    def mark_request_processed(self, request_id: str):
        """标记请求ID已处理，超过上限时淘汰旧ID"""
        if not request_id:
            return
        self.processed_request_ids.add(request_id)
        # 按需淘汰：简单超过2倍上限时清理一半
        if len(self.processed_request_ids) > self._max_stored_request_ids * 2:
            keep = self._max_stored_request_ids
            self.processed_request_ids = set(list(self.processed_request_ids)[-keep:])

    def _reset_force_retry_counters(self):
        """每次成功出牌后重置强制重试计数"""
        for player in self.players:
            key = f"_force_retry_{player}"
            if hasattr(self, key):
                delattr(self, key)

    def start_game(self):
        # Reset game state
        clear_ai_contexts(self.game_id) # 每局开始清空本局 AI 上下文缓存（按局隔离，不影响其他并发局）
        self.finished_players = []
        self.history = []
        self.last_move = None
        self.last_move_player_idx = -1
        self.pass_count = 0
        self.final_result = None
        self.score_applied = False
        self.initial_hands = {}
        self.analysis_snapshot = {}
        self.played_big_cards = {}
        # self.score_manager = ScoreManager() # Keep score manager if it tracks cumulative? 
        # Assuming ScoreManager is stateless or per-game logic only.
        
        # 1. 初始化两副牌
        deck = []
        for deck_idx in range(2): # 0, 1
            for s in [Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES]:
                for r in Rank:
                    if r >= Rank.R_SMALL: continue # 跳过大小王
                    # 生成唯一ID: H3-0, H3-1
                    deck.append(Card(s, r, f"{s.value}{r.value}-{deck_idx}"))
            deck.append(Card(Suit.JOKER, Rank.R_SMALL, f"J20-{deck_idx}")) # 小王
            deck.append(Card(Suit.JOKER, Rank.R_BIG, f"J21-{deck_idx}")) # 大王
        
        # 2. 洗牌
        # 使用系统级随机源，避免可预测性
        random.SystemRandom().shuffle(deck)

        # 可选：调试洗牌结果（设置环境变量 SHUFFLE_DEBUG=1）
        if os.environ.get("SHUFFLE_DEBUG") == "1":
            top_ids = ",".join([c.id for c in deck[:10]])
            digest = hashlib.sha256(",".join([c.id for c in deck]).encode("utf-8")
            ).hexdigest()[:12]
            log.info(f"[ShuffleDebug] top10={top_ids} hash={digest}")
        
        # 3. 发牌 (每人27张)
        for i, p in enumerate(self.players):
            self.hands[p] = sorted(deck[i*27 : (i+1)*27], key=lambda c: c.rank)
            # 记录初始手牌 (只存ID)
            self.initial_hands[p] = [c.id for c in self.hands[p]]
            self.played_big_cards[p] = {
                "aces": 0,
                "twos": 0,
                "small_joker": 0,
                "big_joker": 0,
                "bombs": []
            }
            
        # 4. 确定首发 (找方片3)
        start_player_idx = self._find_starter_idx()
        self.current_turn_index = start_player_idx
        self.state = "playing"
        
        # 重置状态
        self.last_move = None
        self.last_move_player_idx = -1
        self.pass_count = 0
        self.finished_players = []
        self.history = []
        self._update_analysis_snapshot()
        
        return {
            "hand": [c.id for c in self.hands["User"]],
            "turn": self.players[start_player_idx]
        }

    def _update_analysis_snapshot(self):
        snapshot: Dict[str, str] = {}
        for p in self.players:
            info = (self.played_big_cards or {}).get(p) or {}
            segments = []
            aces = int(info.get("aces", 0) or 0)
            if aces:
                segments.append(f"A（{aces}张）")
            twos = int(info.get("twos", 0) or 0)
            if twos:
                segments.append(f"2（{twos}张）")
            small_joker = int(info.get("small_joker", 0) or 0)
            if small_joker:
                segments.append(f"小王（{small_joker}张）")
            big_joker = int(info.get("big_joker", 0) or 0)
            if big_joker:
                segments.append(f"大王（{big_joker}张）")
            bombs = info.get("bombs") or []
            if bombs:
                segments.append(f"炸弹（{len(bombs)}个：{', '.join(bombs)}）")
            snapshot[p] = "；".join(segments) if segments else "暂无关键大牌信息"

        self.analysis_snapshot = snapshot

    def _record_played_big_cards(self, player_name: str, selected: dict):
        if not player_name or player_name not in self.played_big_cards:
            return
        info = self.played_big_cards[player_name]
        cards = selected.get('cards') or []
        if not cards:
            return

        rank_counts: Dict[Rank, int] = {}
        wild_count = 0
        for c in cards:
            rank_counts[c.rank] = rank_counts.get(c.rank, 0) + 1
            if c.is_wild:
                wild_count += 1

        info["aces"] = int(info.get("aces", 0)) + rank_counts.get(Rank.RA, 0)
        info["twos"] = int(info.get("twos", 0)) + rank_counts.get(Rank.R2, 0)
        info["small_joker"] = int(info.get("small_joker", 0)) + rank_counts.get(Rank.R_SMALL, 0)
        info["big_joker"] = int(info.get("big_joker", 0)) + rank_counts.get(Rank.R_BIG, 0)

        if int(selected.get('type') or 0) >= 20:
            def rank_label(rv: Rank) -> str:
                if rv == Rank.RJ:
                    return "J"
                if rv == Rank.RQ:
                    return "Q"
                if rv == Rank.RK:
                    return "K"
                if rv == Rank.RA:
                    return "A"
                if rv == Rank.R2:
                    return "2"
                if rv == Rank.R_SMALL:
                    return "小王"
                if rv == Rank.R_BIG:
                    return "大王"
                return str(int(rv))

            major_rank = None
            major_count = 0
            for r, c in rank_counts.items():
                if r in (Rank.R_SMALL, Rank.R_BIG):
                    continue
                if c > major_count:
                    major_rank = r
                    major_count = c

            if major_rank is not None and major_count >= 3:
                base = "".join([rank_label(major_rank)] * major_count)
                if wild_count and major_rank != Rank.R2:
                    suffix = "红桃2" if wild_count == 1 else f"红桃2x{wild_count}"
                    desc = f"{base}+{suffix}"
                else:
                    desc = base
            else:
                desc = selected.get('desc') or "炸弹"

            info.setdefault("bombs", []).append(desc)

    def _find_starter_idx(self) -> int:
        # 第一局找方片3
        for idx, p in enumerate(self.players):
            for c in self.hands[p]:
                if c.suit == Suit.DIAMONDS and c.rank == Rank.R3:
                    return idx
        return 0 # 默认 User

    def trigger_ai_turn(self):
        """
        检查当前玩家是否是 Bot，如果是，自动执行出牌
        并递归调用直到轮到 User 或 游戏结束
        """
        if self._ai_processing:
            log.warning("[System] AI 正在处理中，跳过重复触发")
            return
            
        self._ai_processing = True
        from .ai_client import get_ai_decision, local_fallback_move, AIDecisionError  # [Fix] 提到函数顶部，供所有分支（含 PASS/winning）读取 _fallback_reason/_force_play_warning，避免 UnboundLocalError
        try:
            # --- 是 Bot，开始思考 ---
            while self.state == "playing":
                current_player = self.players[self.current_turn_index]
                ctx = None  # [Fix] 每手初始化，避免 PASS/winning/defer 分支未赋值导致 UnboundLocalError
                
                # 1. 获取合法移动
                moves = self.get_legal_moves_for_current_player()

                if current_player == "User":
                    # 优化：如果 User 只能 PASS，则自动 PASS
                    if len(moves) == 1 and moves[0]['type'] == 0:
                        log.info(f"[User] 无法管上，自动 PASS")
                        time.sleep(random.uniform(0.6, 1.2))
                        self.execute_move(current_player, 0, moves)
                        continue
                    else:
                        break # 轮到用户且有选择，停止自动运行
                
                # --- 是 Bot，开始思考 ---
                log.info(f"[AI] {current_player} 正在思考... (Turn Index: {self.current_turn_index})")
                
                # 2. 准备 Prompt 数据
                hand_desc = [str(c) for c in self.hands[current_player]]
                
                # 准备剩余手牌数量信息
                remaining_counts = {p: len(self.hands[p]) for p in self.players}

                # 提前计算队友和对手，供后续接风及慢打逻辑使用
                current_idx = self.players.index(current_player)
                teammate_idx = (current_idx + 2) % 4
                teammate = self.players[teammate_idx]
                opponents = [p for p in self.players if p not in (current_player, teammate)]

                # 3. 调用 AI (如果是 PASS 且只能 PASS，就不花钱调 API 了)
                # 优化：如果只剩一手牌且能出，直接出完
                # 但若存在多个“一手走完”选项：优先选择能带来更高炸弹翻倍/更强控牌的方案。
                winning_move = None
                current_hand_count = len(self.hands[current_player])
                finishing_moves = [
                    m for m in moves
                    if m.get('type') != 0 and len(m.get('cards') or []) == current_hand_count
                ]

                def _bomb_len_from_type(m_type: int) -> int:
                    # 约定：4炸=20, 5炸=30, 6炸=40 ...
                    if m_type is None:
                        return 0
                    try:
                        mt = int(m_type)
                    except Exception:
                        return 0
                    if mt < 20:
                        return 0
                    return (mt // 10) + 2

                def _bomb_mult(bomb_len: int) -> int:
                    if bomb_len >= 8:
                        return 8
                    if bomb_len == 7:
                        return 4
                    if bomb_len == 6:
                        return 2
                    return 1

                if finishing_moves:
                    def finish_sort_key(m):
                        bomb_len = _bomb_len_from_type(m.get('type'))
                        mult = _bomb_mult(bomb_len)
                        rank = 0
                        try:
                            rank = int(m.get('rank') or 0)
                        except Exception:
                            rank = 0
                        # 先最大化翻倍，再最大化炸弹张数，再点数，最后用ID稳定排序
                        return (mult, bomb_len, rank, int(m.get('id') or 0))

                    winning_move = max(finishing_moves, key=finish_sort_key)
                
                # === 优化：接风时的"团队配合优先"检查 ===
                # 判断是否应该为了接风而选择PASS（保留资源）
                should_defer_to_teammate = False
                if self.last_move and remaining_counts:
                    last_move_player = self.last_move.get('player')
                    
                    # 3. 判断是否是"接风场景"
                    #    条件：last_move 是队友出的 + 队友仍在场（未完牌）
                    #    说明：队友如果已完牌（finished），他不会再出牌，不能存在“让给队友接风”这种场景。
                    is_takeover_from_teammate = (
                        last_move_player == teammate
                        and teammate not in self.finished_players
                    )
                    
                    if is_takeover_from_teammate:
                        # 4. 使用 Comparator.is_takeover_worthy 判断是否值得接风
                        from .rules import Comparator
                        
                        next_player_idx = (current_idx + 1) % 4
                        next_player = self.players[next_player_idx]
                        next_player_count = remaining_counts.get(next_player, 99)

                        is_worthy = Comparator.is_takeover_worthy(
                            self.last_move,
                            next_player_count
                        )

                        # [战术守护] 队友报杀(剩<=3)且下家张数不足以构成该牌型时，强制让路
                        teammate_count = remaining_counts.get(teammate, 99)
                        last_move_cards_len = len(self.last_move.get('card_ids') or [])
                        # 规则：队友剩<=3张，下家张数不足以管上该牌型（且不考虑下家是否有炸弹，因为炸弹无法通过普通张数管上）
                        if teammate_count <= 3 and next_player_count < last_move_cards_len:
                            is_worthy = True
                            log.info(f"  [战术守护] 队友 {teammate} 报杀(剩{teammate_count}张)且牌型({self.last_move.get('desc')})安全，强制让路")

                        # [战术强化] 如果队友出的牌级别较高(Rank A及以上)且与其下家剩余张数不匹配，判定为值得接风(让路)
                        last_move_rank = self.last_move.get('rank', 0)
                        if last_move_rank >= 14 and last_move_cards_len != next_player_count:
                            is_worthy = True
                            log.info(f"  [战略配合] 队友 {teammate} 出了强牌({self.last_move.get('desc')})，张数与下家({next_player_count}张)不匹配，选择保留资源让路")

                        # 5. 判断 AI 是否能一手牌完牌（允许出牌斩杀）
                        can_kill_now = False
                        if winning_move:
                            # 如果剩一手牌能完牌，允许出（例如：队友出对A，AI手里只剩对2）
                            can_kill_now = True

                            # [Fix] 若只剩一手炸弹，且对手安全(>6张)，且队友出的是强牌(值得接风)，则不强制斩杀
                            # 让 LLM 决策是否保留炸弹用于后续接风 or 更高收益
                            is_bomb_win = winning_move.get('type', 0) >= 20
                            if is_bomb_win and is_worthy:
                                # 检查所有对手牌数
                                all_opp_safe = True
                                for op in opponents:
                                    if remaining_counts.get(op, 99) <= 6:
                                        all_opp_safe = False
                                        break
                                
                                if all_opp_safe:
                                    can_kill_now = False
                                    log.info(f"  [接风优化] 手持炸弹可斩杀，但队友牌大且对手安全，放弃自动斩杀，交给AI决策")
                        
                        # 6. 核心规则：
                        #    - 若值得接风 且 不能斩杀 -> 必须 PASS
                        #    - 若不值得接风（下家临界 或 队友牌不够大）-> 正常决策
                        if is_worthy and not can_kill_now:
                            should_defer_to_teammate = True
                            log.info(f"  [接风守卫] {current_player} 检测到队友 {teammate} 出了值得接风的大牌，选择 PASS 保留资源")
                            log.info(f"      队友出牌: {self.last_move.get('desc', '未知')}, 下家剩余: {next_player_count} 张")
                
                if should_defer_to_teammate:
                    selected_id = 0
                    log.info(f"  > 配合让路：队友控制权安全，强制 PASS")
                    time.sleep(random.uniform(0.8, 1.5))
                elif winning_move and not should_defer_to_teammate:
                    selected_id = winning_move['id']
                    log.info(f"  > 自动出牌：一手牌 ({winning_move['desc']}) 直接完牌")
                    # [Fix] 自动出牌增加随机延迟，避免瞬间出牌让玩家看不清
                    time.sleep(random.uniform(1.2, 2.0))
                elif len(moves) == 1 and moves[0]['type'] == 0:
                    selected_id = 0
                    log.info(f"  > 只能 PASS")
                    # [Fix] 只能 PASS 时也增加短延迟
                    time.sleep(random.uniform(0.6, 1.2))
                else:
                    # 传递完整的 last_move 对象给 AI，以便进行更精确的逻辑判断
                    LAST_PROMPTS_BY_GAME.setdefault(self.game_id, {}).pop(current_player, None)  # [Fix] 清掉该角色旧 ctx，避免本轮大模型失败/early-return 时残留上一手 prompt 导致 history 串档
                    ctx = None  # 初始化，AI 决策后填充；未走 get_ai_decision 的分支保持 None
                    hand_card_ids = [c.id for c in self.hands[current_player]]
                    selected_id = get_ai_decision(
                        current_player,
                        hand_desc,
                        self.last_move,
                        moves,
                        self.finished_players,
                        self.history,
                        remaining_counts,
                        hand_card_ids,
                        self.analysis_snapshot
                    )
                    
                    # [Refactor 错误排查隔离] 升维缓存：把刚生成的这手 prompt 按局+角色存
                    ctx = get_ai_context(self.game_id, current_player)
                    if ctx:
                        set_ai_context(self.game_id, current_player, dict(ctx), len(self.history))
                    
                    # 检测是否触发了强制出牌警告 (仅在调用了 AI 后检查)
                    if hasattr(get_ai_decision, '_force_play_warning') and get_ai_decision._force_play_warning:
                        # 记录到游戏状态
                        if not hasattr(self, 'ai_warnings'):
                            self.ai_warnings = []
                        self.ai_warnings.append({
                            'player': current_player,
                            'turn_index': self.current_turn_index,
                            'message': 'AI大模型连续出现幻觉，系统强制默认出牌！'
                        })
                        # 清除标记
                        get_ai_decision._force_play_warning = False
                        log.error(f"  [系统警告] {current_player} AI大模型发生错误，强制默认出牌！")

                # [方案1] 读取本地兜底原因（大模型失败/幻觉时由 ai_client 设置），传给 history
                _fb_reason = getattr(get_ai_decision, '_fallback_reason', None)
                if _fb_reason is not None:
                    get_ai_decision._fallback_reason = None  # 用完即清，避免影响后续手

                # 4. 执行出牌
                res = self.execute_move(current_player, selected_id, moves, ai_context=dict(ctx) if ctx else None, fallback_reason=_fb_reason)
                
                if isinstance(res, dict) and "error" in res:
                    log.error(f"  [AI Error] {current_player} 出牌导致错误: {res['error']}")
                    if selected_id == 0:
                        log.error("  [Critical] 强制 PASS 亦失败，跳出 AI 循环避免死循环")
                        break
                    log.info("  [Recovery] 尝试强制 PASS...")
                    selected_id = 0
                    continue

                # 如果游戏结束
                if self.state == "finished":
                    log.info("[Game] 游戏结束！")
                    return
        finally:
            self._ai_processing = False
            # [Fix] AI 处理锁释放时，重置本局所有 Bot 的 force-retry 计数，
            # 避免 force retry 计数永久累积到上限(max_retries)后该 Bot 再也无法被触发出牌（死锁卡死）。
            for k in list(vars(self)):
                if k.startswith("_force_retry_"):
                    setattr(self, k, 0)
            log.info(f"[System] {self.game_id} AI 处理锁已释放")

    def get_legal_moves_for_current_player(self):
        """获取当前玩家的可行操作"""
        player = self.players[self.current_turn_index]
        hand = self.hands[player]
        
        # 1. 生成所有组合
        all_moves = PatternRecognizer.get_legal_moves(hand)
        
        valid_moves = []
        
        # 2. 判断是否首发
        is_leader = (self.last_move is None)
        
        if is_leader:
            valid_moves = all_moves
        else:
            # 3. 必须管上
            # 总是允许 PASS
            valid_moves.append({"type": 0, "rank": 0, "desc": "PASS", "cards": [], "card_ids": []})

            # 防回归：同花顺应当能压制所有 5 张炸弹（规则：>5炸, <6炸）。
            # 若比较器/过滤逻辑未来被改坏，这里显式放行同花顺跟 5 炸，避免 User 被误判“只能 PASS”。
            last_type = self.last_move.get('type') if self.last_move else None
            last_cards = (self.last_move.get('cards') or []) if self.last_move else []
            last_card_ids = (self.last_move.get('card_ids') or []) if self.last_move else []
            last_len = len(last_cards) if last_cards else len(last_card_ids)
            # 仅认定“5张炸弹”本体：用长度判断更可靠，避免 type 值或存储结构变动造成回归
            last_is_five_bomb = (
                last_len == 5
                and last_type is not None
                and int(last_type) >= int(CardType.BOMB_4)
                and int(last_type) != int(CardType.STRAIGHT_FLUSH)
                and int(last_type) != int(CardType.KING_BOMB)
            )
            
            for move in all_moves:
                if Comparator.can_beat(self.last_move, move) or (
                    last_is_five_bomb and move.get('type') == CardType.STRAIGHT_FLUSH
                ):
                    valid_moves.append(move)
        
        # 4. 排序：将普通牌型放在前面，炸弹放在后面
        # 这样 AI (LLM) 在阅读选项时，会先看到普通牌，减少“首发扔炸弹”的概率
        # 排序规则：
        # 1. 是否炸弹 (type >= 20) -> 0: 普通, 1: 炸弹
        # 2. 牌型 (type) -> 小到大 (单张 < 对子 < ...)
        # 3. 牌值 (rank) -> 小到大
        def sort_key(m):
            # PASS (type=0) 应该放在哪里？
            # 如果是跟牌，PASS 通常放最后或最前？
            # 为了方便，我们把 PASS 放最后，或者 ID=0
            # 这里我们不特别处理 PASS，因为它 type=0，会被排在最前面
            # 但我们希望 AI 优先考虑出牌，所以也许把 PASS 放最后？
            # 暂时按 type 排序，PASS (0) 会在第一个。
            
            is_bomb = 1 if m['type'] >= 20 else 0
            return (is_bomb, m['type'], m['rank'])
            
        valid_moves.sort(key=sort_key)
                
        # 给每个选项编个号，方便AI选
        for idx, m in enumerate(valid_moves):
            m['id'] = idx
            
        return valid_moves

    def execute_move(self, player_name: str, move_id: int, legal_moves: List, ai_context=None, fallback_reason=None):
        if player_name != self.players[self.current_turn_index]:
            return {"error": "Not your turn"}
            
        if move_id < 0 or move_id >= len(legal_moves):
            return {"error": "Invalid move ID"}
            
        selected = legal_moves[move_id]
        
        # --- 处理 PASS ---
        if selected['type'] == 0: # PASS
            log.info(f"[Log] {player_name} 选择: PASS")
            self.pass_count += 1
            self.history.append({"player": player_name, "action": "PASS"})
            
            # 动态计算需要多少个 PASS 才能结束一轮
            # 规则：当所有其他在场玩家（active players）都 PASS 时，轮次结束
            active_player_count = 4 - len(self.finished_players)
            
            # 修正接风逻辑：
            # 如果当前最大牌的玩家已经走了（finished），则需要所有剩下的 active_player_count 人都 PASS
            # 如果当前最大牌的玩家还在（active），则需要 active_player_count - 1 人 PASS
            last_player_name = self.players[self.last_move_player_idx] if self.last_move_player_idx >= 0 else ""
            is_last_player_finished = last_player_name in self.finished_players
            
            if is_last_player_finished:
                pass_threshold = active_player_count
            else:
                pass_threshold = max(1, active_player_count - 1)
            
            if self.pass_count >= pass_threshold:
                # 一轮结束，结算接风
                self._handle_round_end()
            else:
                self._next_turn()
                
            self._bump_seq()
            self._reset_force_retry_counters()
            return {"status": "pass", "next": self.players[self.current_turn_index], "seq": self.seq}

        # --- 处理出牌 ---
        log.info(f"[Log] {player_name} 出牌: {selected['desc']} (Type: {selected['type']}, Rank: {selected['rank']})")
        self.pass_count = 0 # 重置PASS计数
        self.last_move = selected # 更新桌面最大牌
        self.last_move['player'] = player_name
        self.last_move_player_idx = self.current_turn_index
        
        # 记录历史（含 AI 决策 prompt，供错误排查 / 复盘精确保存 / 今后 AI 教练分析）
        # [Fix] 只使用 AI 出牌路径精确传入的 ai_context（该手大模型实生成的），
        # 不再兜底到 LAST_PROMPTS_BY_GAME / LAST_AI_CONTEXTS 的上一手残留——
        # 否则大模型失败/捷径分支(自动出牌/让路PASS)会复用上一手 prompt 造成 history 串档。
        hist_rec = {
            "player": player_name, 
            "action": "PLAY", 
            "cards": [c.id for c in selected['cards']],
            "desc": selected['desc']
        }
        _ctx = ai_context  # 仅用本手精确传入的 ctx；为 None 表示该手非大模型决策(系统自动/大模型失败)，不写 prompt
        if _ctx:
            hist_rec["system_prompt"] = _ctx.get("system_prompt", "")
            hist_rec["user_prompt"] = _ctx.get("user_prompt", "")
            hist_rec["ai_response"] = _ctx.get("ai_response", "")
        elif fallback_reason:
            # [方案1] 大模型失败/幻觉兜底手：无大模型 prompt，但记录系统决策说明，供教练分析且不串档
            hist_rec["system_note"] = fallback_reason
        self.history.append(hist_rec)
        
        # 扣除手牌
        played_ids = set(selected['card_ids'])
        self.hands[player_name] = [c for c in self.hands[player_name] if c.id not in played_ids]
        self._record_played_big_cards(player_name, selected)
        self._update_analysis_snapshot()
        
        # 记分检查 (炸弹)
        cards_obj = selected['cards']
        self.score_manager.record_bomb(cards_obj, selected['type'])
        
        # 检查完牌
        if not self.hands[player_name]:
            log.info(f"🎉 {player_name} 已出完牌！")
            self.finished_players.append(player_name)
            
            # 检查游戏结束条件 (双上 或 3人走完)
            if len(self.finished_players) >= 2:
                # 检查是否双上 (同队两人都走了)
                p1_idx = self.players.index(self.finished_players[0])
                p2_idx = self.players.index(self.finished_players[1])
                if p1_idx % 2 == p2_idx % 2:
                    self.state = "finished"
                    # 补全剩下的输家，确保 calculate_final_score 收到4人
                    losers = [p for p in self.players if p not in self.finished_players]
                    full_order = self.finished_players + losers
                    self.final_result = self.score_manager.calculate_final_score(full_order)
                    
                    # 添加剩余手牌信息
                    remaining = {}
                    for p in self.players:
                        if self.hands[p]:
                            remaining[p] = [c.id for c in self.hands[p]]
                    self.final_result["remaining_hands"] = remaining
                    
                    self.save_history()
                    self._bump_seq()
                    self._reset_force_retry_counters()
                    return {"status": "game_over", "result": self.final_result, "seq": self.seq}
            
            if len(self.finished_players) == 3: # 游戏结束
                self.state = "finished"
                # 补全剩下的输家
                losers = [p for p in self.players if p not in self.finished_players]
                full_order = self.finished_players + losers
                self.final_result = self.score_manager.calculate_final_score(full_order)
                
                # 添加剩余手牌信息
                remaining = {}
                for p in self.players:
                    if self.hands[p]:
                        remaining[p] = [c.id for c in self.hands[p]]
                self.final_result["remaining_hands"] = remaining
                
                self.save_history()
                self._bump_seq()
                self._reset_force_retry_counters()
                return {"status": "game_over", "result": self.final_result, "seq": self.seq}

        self._next_turn()
        self._bump_seq()
        self._reset_force_retry_counters()
        return {"status": "played", "move": selected, "next": self.players[self.current_turn_index], "seq": self.seq}

    def save_history(self):
        """保存游戏历史供 Coach 分析和复盘"""
        data = {
            "game_id": self.game_id,
            "players": self.players,
            "initial_hands": self.initial_hands,
            "winner_order": self.finished_players,
            "history": self.history,
            "result": self.final_result
        }
        # v2.5：并发结束多局时串行化文件写，避免交错写坏 game_history.json
        with _history_lock:
            try:
                # 1. 保存最新的 game_history.json (供 Coach 使用)
                LATEST_REPLAY_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(LATEST_REPLAY_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                log.info(f"[Replay] 游戏历史已保存至 {LATEST_REPLAY_FILE}")

                # 2. 保存到 history 文件夹 (供复盘使用)，文件名前缀加时间便于按先后次序排查
                from datetime import datetime
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                HISTORY_DIR.mkdir(parents=True, exist_ok=True)
                replay_file = HISTORY_DIR / f"{ts}_{self.game_id}.json"
                with open(replay_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                log.info(f"[Replay] 复盘数据已保存至 {replay_file}")

                # 3.5 写完后按数量/年龄上限清理旧复盘文件
                _cleanup_history_dir()
            
            # 尝试触发 Coach 分析 (异步或同步)
            # 为了不阻塞，这里可以只是打印提示，或者尝试 import 并运行
            # 考虑到这是 demo，我们尝试直接调用
            # [Coach Paused] 为提高响应速度，暂时关闭自动复盘。需手动触发或等待升级。
            # try:
            #     from .coach_client import analyze_game
            #     print("[Coach] 正在呼叫 Coach AI 进行复盘分析...")
            #     analyze_game(str(LATEST_REPLAY_FILE))
            # except Exception as e:
            #     print(f"[WARN] 无法自动触发 Coach 分析: {e}")
            except Exception as e:
                log.error(f"[WARN] 保存游戏历史失败: {e}")

    def _handle_round_end(self):
        """处理一轮结束（3人PASS），决定谁获得出牌权（接风逻辑）"""
        winner_idx = self.last_move_player_idx
        winner_name = self.players[winner_idx]
        
        # 记录轮次结束，便于前端/AI 区分“本轮”范围
        self.history.append({
            "action": "ROUND_END",
            "winner": winner_name,
            "finished": list(self.finished_players)
        })

        # 清空桌面
        self.last_move = None
        self.pass_count = 0
        
        # 检查赢家是否已出完牌（接风）
        log.info(f"🏁 [RoundEnd] Winner: {winner_name}, Finished: {self.finished_players}")
        if winner_name in self.finished_players:
            # 接风给对家 (索引 +2 mod 4)
            partner_idx = (winner_idx + 2) % 4
            self.current_turn_index = partner_idx
            log.info(f"🔄 接风！{winner_name} 已走，由对家 {self.players[partner_idx]} 接管")
        else:
            log.info(f"  > 赢家 {winner_name} 还在场，继续由其出牌")
            self.current_turn_index = winner_idx

    def _next_turn(self):
        # 轮转直到找到一个还没打完牌的玩家
        original = self.current_turn_index
        while True:
            self.current_turn_index = (self.current_turn_index + 1) % 4
            p = self.players[self.current_turn_index]
            if p not in self.finished_players:
                break
            if self.current_turn_index == original: # 防止死循环
                break

    def _find_starter(self):
        # Deprecated, use _find_starter_idx
        pass


##############################################################################
# v2.1 异步版 trigger_ai_turn_async
# 与同步版逻辑完全一致：锁只在 execute_move 一步包裹以保护状态变更，
# 其余 await 让出控制权，避免阻塞 anyio 线程池。由脚本机械生成。
##############################################################################

    async def trigger_ai_turn_async(self):
        """
        检查当前玩家是否是 Bot，如果是，自动执行出牌
        并递归调用直到轮到 User 或 游戏结束
        """
        if self._ai_processing:
            log.warning("[System] AI 正在处理中，跳过重复触发")
            return
            
        self._ai_processing = True
        from .ai_client import get_ai_decision, local_fallback_move, AIDecisionError  # [Fix] 提到函数顶部，供所有分支（含 PASS/winning）读取 _fallback_reason/_force_play_warning，避免 UnboundLocalError
        try:
            # --- 是 Bot，开始思考 ---
            while self.state == "playing":
                current_player = self.players[self.current_turn_index]
                ctx = None  # [Fix] 每手初始化，避免 PASS/winning/defer 分支未赋值导致 UnboundLocalError
                
                # 1. 获取合法移动
                moves = self.get_legal_moves_for_current_player()

                if current_player == "User":
                    # 优化：如果 User 只能 PASS，则自动 PASS
                    if len(moves) == 1 and moves[0]['type'] == 0:
                        log.info(f"[User] 无法管上，自动 PASS")
                        await asyncio.sleep(random.uniform(0.6, 1.2))
                        lk0 = getattr(self, '_ai_lock', None)
                        if lk0 is not None:
                            async with lk0:
                                self.execute_move(current_player, 0, moves)
                        else:
                            self.execute_move(current_player, 0, moves)
                        # v2.4：自动 PASS 也是状态变更，通知 SSE
                        game_event_bus.publish(self.game_id)
                        continue
                    else:
                        break # 轮到用户且有选择，停止自动运行
                
                # --- 是 Bot，开始思考 ---
                log.info(f"[AI] {current_player} 正在思考... (Turn Index: {self.current_turn_index})")
                
                # 2. 准备 Prompt 数据
                hand_desc = [str(c) for c in self.hands[current_player]]
                
                # 准备剩余手牌数量信息
                remaining_counts = {p: len(self.hands[p]) for p in self.players}

                # 提前计算队友和对手，供后续接风及慢打逻辑使用
                current_idx = self.players.index(current_player)
                teammate_idx = (current_idx + 2) % 4
                teammate = self.players[teammate_idx]
                opponents = [p for p in self.players if p not in (current_player, teammate)]

                # 3. 调用 AI (如果是 PASS 且只能 PASS，就不花钱调 API 了)
                # 优化：如果只剩一手牌且能出，直接出完
                # 但若存在多个“一手走完”选项：优先选择能带来更高炸弹翻倍/更强控牌的方案。
                winning_move = None
                current_hand_count = len(self.hands[current_player])
                finishing_moves = [
                    m for m in moves
                    if m.get('type') != 0 and len(m.get('cards') or []) == current_hand_count
                ]

                def _bomb_len_from_type(m_type: int) -> int:
                    # 约定：4炸=20, 5炸=30, 6炸=40 ...
                    if m_type is None:
                        return 0
                    try:
                        mt = int(m_type)
                    except Exception:
                        return 0
                    if mt < 20:
                        return 0
                    return (mt // 10) + 2

                def _bomb_mult(bomb_len: int) -> int:
                    if bomb_len >= 8:
                        return 8
                    if bomb_len == 7:
                        return 4
                    if bomb_len == 6:
                        return 2
                    return 1

                if finishing_moves:
                    def finish_sort_key(m):
                        bomb_len = _bomb_len_from_type(m.get('type'))
                        mult = _bomb_mult(bomb_len)
                        rank = 0
                        try:
                            rank = int(m.get('rank') or 0)
                        except Exception:
                            rank = 0
                        # 先最大化翻倍，再最大化炸弹张数，再点数，最后用ID稳定排序
                        return (mult, bomb_len, rank, int(m.get('id') or 0))

                    winning_move = max(finishing_moves, key=finish_sort_key)
                
                # === 优化：接风时的"团队配合优先"检查 ===
                # 判断是否应该为了接风而选择PASS（保留资源）
                should_defer_to_teammate = False
                if self.last_move and remaining_counts:
                    last_move_player = self.last_move.get('player')
                    
                    # 3. 判断是否是"接风场景"
                    #    条件：last_move 是队友出的 + 队友仍在场（未完牌）
                    #    说明：队友如果已完牌（finished），他不会再出牌，不能存在“让给队友接风”这种场景。
                    is_takeover_from_teammate = (
                        last_move_player == teammate
                        and teammate not in self.finished_players
                    )
                    
                    if is_takeover_from_teammate:
                        # 4. 使用 Comparator.is_takeover_worthy 判断是否值得接风
                        from .rules import Comparator
                        
                        next_player_idx = (current_idx + 1) % 4
                        next_player = self.players[next_player_idx]
                        next_player_count = remaining_counts.get(next_player, 99)

                        is_worthy = Comparator.is_takeover_worthy(
                            self.last_move,
                            next_player_count
                        )

                        # [战术守护] 队友报杀(剩<=3)且下家张数不足以构成该牌型时，强制让路
                        teammate_count = remaining_counts.get(teammate, 99)
                        last_move_cards_len = len(self.last_move.get('card_ids') or [])
                        # 规则：队友剩<=3张，下家张数不足以管上该牌型（且不考虑下家是否有炸弹，因为炸弹无法通过普通张数管上）
                        if teammate_count <= 3 and next_player_count < last_move_cards_len:
                            is_worthy = True
                            log.info(f"  [战术守护] 队友 {teammate} 报杀(剩{teammate_count}张)且牌型({self.last_move.get('desc')})安全，强制让路")

                        # [战术强化] 如果队友出的牌级别较高(Rank A及以上)且与其下家剩余张数不匹配，判定为值得接风(让路)
                        last_move_rank = self.last_move.get('rank', 0)
                        if last_move_rank >= 14 and last_move_cards_len != next_player_count:
                            is_worthy = True
                            log.info(f"  [战略配合] 队友 {teammate} 出了强牌({self.last_move.get('desc')})，张数与下家({next_player_count}张)不匹配，选择保留资源让路")

                        # 5. 判断 AI 是否能一手牌完牌（允许出牌斩杀）
                        can_kill_now = False
                        if winning_move:
                            # 如果剩一手牌能完牌，允许出（例如：队友出对A，AI手里只剩对2）
                            can_kill_now = True

                            # [Fix] 若只剩一手炸弹，且对手安全(>6张)，且队友出的是强牌(值得接风)，则不强制斩杀
                            # 让 LLM 决策是否保留炸弹用于后续接风 or 更高收益
                            is_bomb_win = winning_move.get('type', 0) >= 20
                            if is_bomb_win and is_worthy:
                                # 检查所有对手牌数
                                all_opp_safe = True
                                for op in opponents:
                                    if remaining_counts.get(op, 99) <= 6:
                                        all_opp_safe = False
                                        break
                                
                                if all_opp_safe:
                                    can_kill_now = False
                                    log.info(f"  [接风优化] 手持炸弹可斩杀，但队友牌大且对手安全，放弃自动斩杀，交给AI决策")
                        
                        # 6. 核心规则：
                        #    - 若值得接风 且 不能斩杀 -> 必须 PASS
                        #    - 若不值得接风（下家临界 或 队友牌不够大）-> 正常决策
                        if is_worthy and not can_kill_now:
                            should_defer_to_teammate = True
                            log.info(f"  [接风守卫] {current_player} 检测到队友 {teammate} 出了值得接风的大牌，选择 PASS 保留资源")
                            log.info(f"      队友出牌: {self.last_move.get('desc', '未知')}, 下家剩余: {next_player_count} 张")
                
                if should_defer_to_teammate:
                    selected_id = 0
                    log.info(f"  > 配合让路：队友控制权安全，强制 PASS")
                    await asyncio.sleep(random.uniform(0.8, 1.5))
                elif winning_move and not should_defer_to_teammate:
                    selected_id = winning_move['id']
                    log.info(f"  > 自动出牌：一手牌 ({winning_move['desc']}) 直接完牌")
                    # [Fix] 自动出牌增加随机延迟，避免瞬间出牌让玩家看不清
                    await asyncio.sleep(random.uniform(1.2, 2.0))
                elif len(moves) == 1 and moves[0]['type'] == 0:
                    selected_id = 0
                    log.info(f"  > 只能 PASS")
                    # [Fix] 只能 PASS 时也增加短延迟
                    await asyncio.sleep(random.uniform(0.6, 1.2))
                else:
                    # 传递完整的 last_move 对象给 AI，以便进行更精确的逻辑判断
                    LAST_PROMPTS_BY_GAME.setdefault(self.game_id, {}).pop(current_player, None)  # [Fix] 清掉该角色旧 ctx，避免本轮大模型失败/early-return 时残留上一手 prompt 导致 history 串档
                    ctx = None  # 初始化，AI 决策后填充；未走 get_ai_decision 的分支保持 None
                    hand_card_ids = [c.id for c in self.hands[current_player]]
                    # [Fix] 60s 硬时限：AI 调用异常时重试，单次调用用 wait_for 强制切断（内部多层重试也穿越不了），
                    # 总预算严格倒计时到 60s 才本地兜底（避免偶发抖动误兜底，也保证弹窗/兜底一定在 60s 内落地）
                    selected_id = None
                    _fb_reason_local = None
                    _try_deadline = time.monotonic() + 60.0
                    _call_timeout = 15.0  # 单次 get_ai_decision_async 的上限；正常响应一般 <10s，余量留给重试
                    _last_err = None
                    # [Fix C] 连续失败计数：真实故障往往是模型稳定输出坏 JSON/幻觉，同 prompt 重试 5 次也救不回，
                    # 与其空转满 60s 拖慢牌局，不如连续 2 次失败就提前本地兜底（约 30s）。
                    _consecutive_failures = 0
                    while selected_id is None and time.monotonic() < _try_deadline:
                        # 单次调用剩余预算：不足则不再发起新调用，直接走兜底
                        _remaining = _try_deadline - time.monotonic()
                        if _remaining <= 0:
                            break
                        _single_timeout = max(1.0, min(_call_timeout, _remaining))
                        try:
                            selected_id = await asyncio.wait_for(
                                get_ai_decision_async(
                                    current_player,
                                    hand_desc,
                                    self.last_move,
                                    moves,
                                    self.finished_players,
                                    self.history,
                                    remaining_counts,
                                    hand_card_ids,
                                    self.analysis_snapshot,
                                    self.game_id
                                ),
                                timeout=_single_timeout,
                            )
                            _consecutive_failures = 0
                            # [Refactor 错误排查隔离] 升维缓存：把刚生成的这手 prompt 按局+角色存
                            ctx = get_ai_context(self.game_id, current_player)
                            if ctx:
                                set_ai_context(self.game_id, current_player, dict(ctx), len(self.history))
                        except asyncio.TimeoutError as e:
                            # 单次调用超过预算：不 sleep，直接检查剩余时间决定重试还是兜底
                            _last_err = e
                            _consecutive_failures += 1
                            log.warning(f"  [AI Retry] {current_player} AI 调用超时({type(e).__name__})，60s 内重试")
                            if _consecutive_failures >= 2:
                                log.warning(f"  [AI Retry] {current_player} 连续 {_consecutive_failures} 次失败，提前本地兜底（避免整轮 60s 空转）")
                                break
                        except Exception as e:
                            _last_err = e
                            _consecutive_failures += 1
                            log.warning(f"  [AI Retry] {current_player} AI 调用异常({type(e).__name__})，60s 内重试: {e}")
                            if _consecutive_failures >= 2:
                                log.warning(f"  [AI Retry] {current_player} 连续 {_consecutive_failures} 次失败，提前本地兜底（避免整轮 60s 空转）")
                                break
                            await asyncio.sleep(2.0)
                    if selected_id is None:
                        # 60s 内始终失败 → 统一本地兜底(规则见 ai_client.local_fallback_move，与 ai_client 内部一致)
                        log.error(f"  [AI Fallback] {current_player} AI 调用失败，本地兜底出牌: {_last_err}")
                        _fb_reason_local = "大模型调用异常，本地策略兜底"
                        AI_FALLBACK_REASON[current_player] = _fb_reason_local
                        get_ai_decision._force_play_warning = False
                        selected_id = local_fallback_move(current_player, moves, self.last_move, remaining_counts, hand_card_ids)
                        ctx = None
                    
                    # 检测是否触发了强制出牌警告 (仅在调用了 AI 后检查)
                    if hasattr(get_ai_decision, '_force_play_warning') and get_ai_decision._force_play_warning:
                        # 记录到游戏状态
                        if not hasattr(self, 'ai_warnings'):
                            self.ai_warnings = []
                        self.ai_warnings.append({
                            'player': current_player,
                            'turn_index': self.current_turn_index,
                            'message': 'AI大模型连续出现幻觉，系统强制默认出牌！'
                        })
                        # 清除标记
                        get_ai_decision._force_play_warning = False
                        log.error(f"  [系统警告] {current_player} AI大模型发生错误，强制默认出牌！")

                # [方案1] 读取本地兜底原因（大模型失败/幻觉时由兜底分支写入 AI_FALLBACK_REASON 字典），传给 history
                _fb_reason = AI_FALLBACK_REASON.pop(current_player, None)
                if _fb_reason is not None:
                    AI_FALLBACK_REASON.pop(current_player, None)  # 用完即清，避免影响后续手

                # 4. 执行出牌（锁保护状态变更，await 让出控制权时其他协程无法改同一局）
                lk = getattr(self, '_ai_lock', None)
                if lk is not None:
                    async with lk:
                        res = self.execute_move(current_player, selected_id, moves, ai_context=dict(ctx) if ctx else None, fallback_reason=_fb_reason)
                else:
                    res = self.execute_move(current_player, selected_id, moves, ai_context=dict(ctx) if ctx else None, fallback_reason=_fb_reason)
                # v2.4：状态已变更，通知 SSE 订阅者
                game_event_bus.publish(self.game_id)
                # [Fix] 若本手是 AI 超时本地兜底，记录标志供前端弹提醒（"AI 返回超时，本次出牌采用本地策略"）
                if _fb_reason:
                    self._last_ai_fallback = {
                        "seq": len(self.history) - 1,
                        "reason": _fb_reason,
                        "ts": time.time(),
                    }

                if isinstance(res, dict) and "error" in res:
                    log.error(f"  [AI Error] {current_player} 出牌导致错误: {res['error']}")
                    if selected_id == 0:
                        log.error("  [Critical] 强制 PASS 亦失败，跳出 AI 循环避免死循环")
                        break
                    log.info("  [Recovery] 尝试强制 PASS...")
                    selected_id = 0
                    continue

                # 如果游戏结束
                if self.state == "finished":
                    log.info("[Game] 游戏结束！")
                    return
        finally:
            self._ai_processing = False
            # [Fix] AI 处理锁释放时，重置本局所有 Bot 的 force-retry 计数，
            # 避免 force retry 计数永久累积到上限(max_retries)后该 Bot 再也无法被触发出牌（死锁卡死）。
            for k in list(vars(self)):
                if k.startswith("_force_retry_"):
                    setattr(self, k, 0)
            log.info(f"[System] {self.game_id} AI 处理锁已释放")

    # ------------------------------------------------------------------
    # v2.5 Redis 持久化：对象 ↔ JSON dict 序列化
    # ------------------------------------------------------------------
    @staticmethod
    def _card_to_dict(c: Card) -> dict:
        return {"suit": c.suit.value, "rank": c.rank.value, "id": c.id}

    @staticmethod
    def _card_from_dict(d: dict) -> Card:
        return Card(suit=Suit(d["suit"]), rank=d["rank"], id=d.get("id") or "")

    @classmethod
    def _hands_to_dict(cls, hands: Dict[str, List[Card]]) -> Dict[str, list]:
        return {p: [cls._card_to_dict(c) for c in cards] for p, cards in hands.items()}

    @classmethod
    def _hands_from_dict(cls, d: Dict[str, list]) -> Dict[str, List[Card]]:
        return {p: [cls._card_from_dict(c) for c in cards] for p, cards in (d or {}).items()}

    @classmethod
    def _move_to_dict(cls, move) -> Optional[dict]:
        """last_move 含 Card 对象（cards 字段），需序列化为可 JSON 的 dict。"""
        if not move:
            return None
        m = dict(move)
        if isinstance(m.get("cards"), list):
            m["cards"] = [cls._card_to_dict(c) for c in m["cards"]]
        return m

    @classmethod
    def _move_from_dict(cls, m) -> Optional[dict]:
        if not m:
            return None
        m = dict(m)
        if isinstance(m.get("cards"), list):
            m["cards"] = [cls._card_from_dict(c) for c in m["cards"] if isinstance(c, dict)]
        return m

    def to_dict(self) -> dict:
        """序列化为可 JSON 存储的 dict（不含运行时态：_ai_lock/_ai_processing）。"""
        return {
            "game_id": self.game_id,
            "players": list(self.players),
            "hands": self._hands_to_dict(self.hands),
            "current_turn_index": self.current_turn_index,
            "history": list(self.history),
            "last_move": self._move_to_dict(self.last_move),
            "last_move_player_idx": self.last_move_player_idx,
            "pass_count": self.pass_count,
            "score_manager": self.score_manager.to_dict(),
            "finished_players": list(self.finished_players),
            "state": self.state,
            "final_result": self.final_result,
            "score_applied": bool(self.score_applied),
            "initial_hands": dict(self.initial_hands),
            "analysis_snapshot": dict(self.analysis_snapshot),
            "played_big_cards": dict(self.played_big_cards),
            "seq": self.seq,
            "processed_request_ids": sorted(self.processed_request_ids),
            "_last_ai_fallback": getattr(self, "_last_ai_fallback", None),
            "ai_warnings": getattr(self, "ai_warnings", None),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GuandanGame":
        game = cls(d["game_id"])
        game.players = list(d.get("players") or ["User", "RightBot", "PartnerBot", "LeftBot"])
        game.hands = cls._hands_from_dict(d.get("hands"))
        game.current_turn_index = int(d.get("current_turn_index", 0))
        game.history = list(d.get("history") or [])
        game.last_move = cls._move_from_dict(d.get("last_move"))
        game.last_move_player_idx = int(d.get("last_move_player_idx", -1))
        game.pass_count = int(d.get("pass_count", 0))
        game.score_manager = ScoreManager.from_dict(d.get("score_manager"))
        game.finished_players = list(d.get("finished_players") or [])
        game.state = d.get("state", "waiting")
        game.final_result = d.get("final_result")
        game.score_applied = bool(d.get("score_applied", False))
        game.initial_hands = dict(d.get("initial_hands") or {})
        game.analysis_snapshot = dict(d.get("analysis_snapshot") or {})
        game.played_big_cards = dict(d.get("played_big_cards") or {})
        game.seq = int(d.get("seq", 0))
        game.processed_request_ids = set(d.get("processed_request_ids") or [])
        if d.get("_last_ai_fallback") is not None:
            game._last_ai_fallback = d["_last_ai_fallback"]
        if d.get("ai_warnings") is not None:
            game.ai_warnings = list(d["ai_warnings"])
        # 运行时态重建：锁/处理标志不入序列化
        game._ai_processing = False
        try:
            game._ai_lock = asyncio.Lock()
        except Exception:
            game._ai_lock = None
        return game

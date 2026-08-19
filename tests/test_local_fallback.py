"""local_fallback_move 本地兜底出牌策略测试。

覆盖兜底策略在「首发/跟牌」下的选牌规则，重点回归：
- 首发有对子 + 孤张时，优先出孤张（不拆对子），避免完牌轮次增加。
  这是真实对局 bug：PartnerBot 剩 [33 4 7 888 10 QQ AA] 时，兜底错误拆对3出单张3。
- 首发提供 hand_cards 时，从系统推演的最优组合中选最小牌型，不拆推荐组合
  (三带/对子/炸弹)。真实对局 bug：4张9炸弹被拆成对9；随后三带里的对J也被拆。
"""
import pytest

from backend.models import Card
from backend.rules import PatternRecognizer
from backend.ai_client import local_fallback_move


def build_hand(id_rank):
    """按 {card_id: rank} 构造手牌，id 前缀同时作为花色。"""
    return [Card(cid[0], rank, cid) for cid, rank in id_rank.items()]


def legal(hand):
    moves = PatternRecognizer.get_legal_moves(hand)
    for idx, m in enumerate(moves):
        m["id"] = idx
    return moves


def picked(moves, sel):
    return next(m for m in moves if m["id"] == sel)


class TestLeaderFallback:
    def test_leader_prefers_singleton_over_pair(self):
        """首发有对3 + 孤张4：应出孤张4，而非拆对3出单张3。"""
        hand = build_hand({
            "C3-0": 3, "D3-1": 3, "S4-0": 4, "C7-0": 7,
            "C8-1": 8, "H8-1": 8, "S10-0": 10, "HQ-0": 12,
            "CQ-1": 12, "SA-0": 14, "HA-1": 14,
        })
        moves = legal(hand)
        sel = local_fallback_move("PartnerBot", moves, None, {"User": 15, "RightBot": 11, "LeftBot": 9})
        m = picked(moves, sel)
        assert m["rank"] == 4, f"应出孤张4, 实际选了 {m['desc']}"
        assert m["type"] == 1

    def test_leader_all_singletons_picks_min(self):
        """首发全是孤张：按 rank 升序出最小（3）。"""
        hand = build_hand({"H3-0": 3, "S4-0": 4, "H5-1": 5, "SA-0": 14, "HA-1": 14})
        moves = legal(hand)
        sel = local_fallback_move("LeftBot", moves, None, {"User": 20, "RightBot": 20, "PartnerBot": 20})
        m = picked(moves, sel)
        assert m["rank"] == 3

    def test_leader_pair_and_bomb_prefers_singleton(self):
        """首发有对子、炸弹、孤张5：应优先孤张5（不拆对子/炸弹）。"""
        hand = build_hand({
            "C3-0": 3, "D3-1": 3, "S4-0": 4, "H4-1": 4, "S5-0": 5,
            "C9-0": 9, "D9-1": 9, "S9-1": 9, "H9-0": 9, "C2-0": 15, "S2-1": 15,
        })
        moves = legal(hand)
        sel = local_fallback_move("RightBot", moves, None, {"User": 20, "LeftBot": 20, "PartnerBot": 20})
        m = picked(moves, sel)
        assert m["rank"] == 5

    def test_leader_must_not_split_bomb_pair(self):
        """回归: 对局 20260814_132637 中，兜底把 4张9 炸弹拆成对9 出。

        手牌(12张)与真实对局 idx82 完全一致：4张9 可组炸弹(4张9/三9带对),
        兜底选牌不得是 rank=9 的任何子牌(对9/三9/一张9 都是拆炸弹)。
        """
        hand = build_hand({
            "H9-0": 9, "C9-0": 9, "H9-1": 9, "S9-1": 9,     # 4张9 炸弹
            "C11-1": 11, "H11-1": 11,                       # 对J
            "C12-0": 12, "D12-0": 12,                       # 对Q
            "D15-0": 15, "S15-0": 15,                       # 对2
            "S14-0": 14,                                    # 孤张A
        })
        moves = legal(hand)
        sel = local_fallback_move("PartnerBot", moves, None, {"User": 15, "RightBot": 11, "LeftBot": 9})
        m = picked(moves, sel)
        assert m["rank"] != 9, f"不应拆9炸弹! 兜底出牌: {m['desc']} type={m['type']} rank={m['rank']}"

    def test_leader_with_hand_cards_picks_smallest_from_optimal_combo(self):
        """回归: 兜底首发应从系统推演的最优组合选最小牌型, 而非本地启发式。

        对局 20260814_132637 手牌 idx82：系统最优组合为
        '散牌:[A], 对子:[QQ], 三带:[22+红桃2JJ], 炸弹:[9999]'。
        最小的推荐牌型是「散牌 A」→ 应出 一张A；
        不得选 对9(拆9999)、对J(拆三带里的J)、对Q(拆对子组合)。
        """
        hand = build_hand({
            "H9-0": 9, "C9-0": 9, "H9-1": 9, "S9-1": 9,
            "C11-1": 11, "H11-1": 11,
            "C12-0": 12, "D12-0": 12,
            "H15-1": 15, "D15-0": 15, "S15-0": 15,   # 红桃2(赖子)+对2
            "S14-0": 14,
        })
        hand_cards = ["H9-0", "C9-0", "H9-1", "S9-1", "C11-1", "H11-1",
                      "C12-0", "D12-0", "H15-1", "D15-0", "S15-0", "S14-0"]
        moves = legal(hand)
        sel = local_fallback_move("PartnerBot", moves, None,
                                  {"User": 15, "RightBot": 11, "LeftBot": 9}, hand_cards)
        m = picked(moves, sel)
        assert m["type"] == 1 and m["rank"] == 14, f"应出散牌A, 实际 {m['desc']}"
        assert m["rank"] != 9, "不应拆9999炸弹"
        assert m["rank"] != 11, "不应拆三带里的J"

    def test_leader_hand_cards_prefers_smallest_singleton(self):
        """首发 4张9炸弹 + 孤张5：系统推荐 '散牌:[5]' → 出 一张5。"""
        hand = build_hand({
            "H9-0": 9, "C9-0": 9, "H9-1": 9, "S9-1": 9,
            "C5-0": 5,
        })
        moves = legal(hand)
        sel = local_fallback_move("PartnerBot", moves, None,
                                  {"User": 15, "RightBot": 11, "LeftBot": 9},
                                  ["H9-0", "C9-0", "H9-1", "S9-1", "C5-0"])
        m = picked(moves, sel)
        assert m["type"] == 1 and m["rank"] == 5, f"应出散牌5, 实际 {m['desc']}"

    def test_leader_hand_cards_recommends_triple(self):
        """首发 三张5 + 两套炸弹：系统推荐 '三张:[555], 炸弹:[...]' → 出 三张5。"""
        hand = build_hand({
            "C5-0": 5, "D5-1": 5, "S5-0": 5,
            "C9-0": 9, "D9-1": 9, "S9-0": 9, "H9-1": 9,
            "C7-0": 7, "D7-1": 7, "H7-0": 7, "S7-1": 7,
        })
        moves = legal(hand)
        sel = local_fallback_move("PartnerBot", moves, None,
                                  {"User": 15, "RightBot": 11, "LeftBot": 9},
                                  ["C5-0", "D5-1", "S5-0", "C9-0", "D9-1", "S9-0",
                                   "H9-1", "C7-0", "D7-1", "H7-0", "S7-1"])
        m = picked(moves, sel)
        assert m["type"] == 3 and m["rank"] == 5, f"应出三张5, 实际 {m['desc']}"

    def test_leader_hand_cards_unparseable_falls_back_to_heuristic(self):
        """hand_cards 无法被系统组合解析/匹配时，应回退到启发式(不崩溃)。"""
        hand = build_hand({
            "H9-0": 9, "C9-0": 9, "H9-1": 9, "S9-1": 9,
            "C11-1": 11, "H11-1": 11,
            "C12-0": 12, "D12-0": 12,
            "D15-0": 15, "S15-0": 15,
            "S14-0": 14,
        })
        moves = legal(hand)
        # hand_cards 含非法 id，calculate_hand_optimization 解析不出 → 应回退启发式(出非拆炸弹的对子/孤张)
        sel = local_fallback_move("PartnerBot", moves, None,
                                  {"User": 15, "RightBot": 11, "LeftBot": 9},
                                  ["badid-0", "alsobad-1"])
        m = picked(moves, sel)
        assert m["rank"] != 9, f"回退路径也不应拆9炸弹: {m['desc']}"


class TestFollowingFallback:
    def test_follow_with_pass_yields(self):
        """跟牌且可 PASS 时（非拦截、上家非队友）应过牌。"""
        moves = [
            {"id": 0, "type": 0, "rank": 0, "desc": "PASS", "cards": [], "card_ids": []},
            {"id": 1, "type": 2, "rank": 4, "desc": "对4", "cards": [], "card_ids": ["C4-0", "D4-1"]},
            {"id": 2, "type": 1, "rank": 14, "desc": "一张A", "cards": [], "card_ids": ["SA-0"]},
        ]
        last = {"type": 2, "rank": 3, "desc": "对3", "player": "LeftBot"}
        sel = local_fallback_move("PartnerBot", moves, last, {"User": 15, "RightBot": 11, "LeftBot": 9})
        assert sel == 0

    def test_follow_single_prefers_singleton(self):
        """跟单张(无 PASS 选项)时，有孤张5可用，不应拆对8。

        注: game_engine 跟牌通常带 PASS，此时 pass_when_following 会直接过牌；
        此处验证的是无 PASS(拦截等强推场景)下，排序仍优先孤张而非拆对。
        """
        hand = build_hand({"H5-0": 5, "C8-0": 8, "D8-1": 8, "SA-0": 14})
        moves = legal(hand)
        from backend.rules import Comparator
        last = {"type": 1, "rank": 4, "desc": "一张4", "player": "RightBot", "card_ids": ["S4-0"], "cards": []}
        beatable = [m for m in moves if Comparator.can_beat(last, m)]
        for idx, m in enumerate(beatable):
            m["id"] = idx
        sel = local_fallback_move("LeftBot", beatable, last, {"User": 15, "RightBot": 11, "PartnerBot": 9})
        m = picked(beatable, sel)
        assert m["rank"] == 5, f"应出孤张5, 实际选了 {m['desc']}"

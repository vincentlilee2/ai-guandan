"""tactics.py 触发式策略提醒测试。

覆盖本次改动的核心行为：
1. _dedupe_and_trim 能正确保留 TACTICS_DB(JSON list 格式) 的策略条目
   （回归：此前只接受 tuple，导致所有 JSON 触发策略被静默丢弃）
2. #18 红桃2升6+炸仅限头游确认时（三路分支）
3. #19 残局升炸：手牌≤6且持红桃2+可升级自然炸弹时触发
"""
import pytest

from backend.tactics import get_tactical_strategies


def _base_kwargs(**overrides):
    hand18 = ["H9-0", "C9-0", "H9-1", "S9-1", "D9-1", "H15-0", "S4-0"]
    kw = dict(
        game_stage="中局阶段",
        stage_focus="控牌与配合",
        teammate="te",
        finished_players=[],
        is_teammate_move=False,
        is_leader=True,
        is_takeover=False,
        teammate_passed_after_opponent=False,
        last_move=None,
        can_play_moves=[
            {"type": 40, "rank": 9, "desc": "6张9 (含赖子)",
             "card_ids": ["H9-0", "C9-0", "H9-1", "S9-1", "D9-1", "H15-0"]},
            {"type": 21, "rank": 9, "desc": "5张9", "card_ids": ["H9-0", "C9-0", "H9-1", "S9-1", "D9-1"]},
            {"type": 1, "rank": 4, "desc": "一张4", "card_ids": ["S4-0"]},
        ],
        has_red_heart_2=True,
        bombs=[],
        straight_flushes=[],
        remaining_counts={"te": 23, "u1": 11, "u2": 17, "me": 7},
        opponents=["u1", "u2"],
        hand_structure={"isolated_singles": ["红桃2", "4"], "pairs": [], "triples": [], "bombs": ["9"]},
        red_heart_2_count=1,
        hand_cards=hand18,
        hand_card_ids=hand18,
    )
    kw.update(overrides)
    return kw


def _titles(**kw):
    out = get_tactical_strategies(**kw)
    return [t for t, _ in out[2]]


class TestDedupePreservesJsonEntries:
    """回归：_dedupe_and_trim 必须保留 JSON(list) 格式的策略条目。"""

    def test_json_entry_survives_dedupe(self):
        titles = _titles(**_base_kwargs())
        # 5张9 + 红桃2，头游未确认 -> 应触发“红桃2升6+炸仅限确认头游时”
        assert any("红桃2升6+炸" in t for t in titles), \
            f"JSON 条目应在去重后保留，实际: {titles}"

    def test_inline_tuple_also_survives(self):
        titles = _titles(**_base_kwargs(finished_players=["te"]))
        assert any("头游时优先翻倍" in t for t in titles), f"实际: {titles}"


class TestRh2SixBombHeadWinConfirmed:
    """#18 红桃2升6+炸仅限确认头游时。"""

    def test_unconfirmed_head_win(self):
        """头游未确认 -> 提示“仅限确认头游时”，避免白送翻倍。"""
        titles = _titles(**_base_kwargs())
        assert any("红桃2升6+炸" in t and "仅限确认头游时" in t for t in titles), f"实际: {titles}"

    def test_teammate_finished_head_win(self):
        """队友已完牌头游 -> 提示“争胜/头游时优先翻倍”。"""
        titles = _titles(**_base_kwargs(finished_players=["te"]))
        assert any("头游时优先翻倍" in t for t in titles), f"实际: {titles}"

    def test_opponent_finished_head(self):
        """对手已头游 -> 提示“降倍提醒”，应拆6炸避免白送倍数。"""
        titles = _titles(**_base_kwargs(finished_players=["u1"]))
        assert any("降倍提醒" in t for t in titles), f"实际: {titles}"

    def test_no_upgradable_no_trigger(self):
        """无5张及以上可升级炸弹时，不触发6+炸相关提醒。"""
        hand = ["H9-0", "C9-0", "H15-0", "S4-0"]  # 对9 + 红桃2 + 4，无可升级炸
        titles = _titles(**_base_kwargs(
            hand_cards=hand,
            hand_card_ids=hand,
            can_play_moves=[
                {"type": 2, "rank": 9, "desc": "对9", "card_ids": ["H9-0", "C9-0"]},
                {"type": 1, "rank": 4, "desc": "一张4", "card_ids": ["S4-0"]},
                {"type": 1, "rank": 2, "desc": "一张2", "card_ids": ["H15-0"]},
            ],
        ))
        assert not any("红桃2升6+炸" in t or "头游时优先翻倍" in t or "降倍提醒" in t for t in titles), \
            f"不应触发6+炸提醒，实际: {titles}"


class TestEndgameKillBombUpgrade:
    """#19 残局升炸触发点。"""

    def test_endgame_upgradable_bomb_trigger(self):
        """残局(≤6张)持红桃2+可升级自然炸 -> 触发“残局斩杀：红桃2优先用于最大炸弹”。"""
        hand = ["H9-0", "C9-0", "S9-0", "D9-0", "H15-0", "S4-0"]  # 4x9 + 红桃2 + 4
        titles = _titles(**_base_kwargs(
            game_stage="残局阶段",
            hand_cards=hand,
            hand_card_ids=hand,
            remaining_counts={"te": 23, "u1": 11, "u2": 17, "me": 6},
            can_play_moves=[
                {"type": 40, "rank": 9, "desc": "5张9 (含赖子)",
                 "card_ids": ["H9-0", "C9-0", "S9-0", "D9-0", "H15-0"]},
                {"type": 4, "rank": 9, "desc": "三9带对4",
                 "card_ids": ["H9-0", "C9-0", "S9-0", "H15-0", "S4-0"]},
                {"type": 1, "rank": 4, "desc": "一张4", "card_ids": ["S4-0"]},
            ],
        ))
        assert any("残局斩杀" in t and "红桃2" in t for t in titles), f"实际: {titles}"

    def test_midgame_no_endgame_trigger(self):
        """中局阶段不应触发残局升炸提醒。"""
        hand = ["H9-0", "C9-0", "S9-0", "D9-0", "H15-0", "S4-0"]
        titles = _titles(**_base_kwargs(hand_cards=hand, hand_card_ids=hand))
        assert not any("残局斩杀" in t for t in titles), f"实际: {titles}"

    def test_no_natural_bomb_no_trigger(self):
        """残局但无自然炸弹(仅散牌)时，不触发升炸提醒。"""
        hand = ["H5-0", "H15-0", "S4-0"]  # 5 + 红桃2 + 4
        titles = _titles(**_base_kwargs(
            game_stage="残局阶段",
            hand_cards=hand,
            hand_card_ids=hand,
            remaining_counts={"te": 23, "u1": 11, "u2": 17, "me": 3},
            can_play_moves=[
                {"type": 1, "rank": 5, "desc": "一张5", "card_ids": ["H5-0"]},
                {"type": 1, "rank": 4, "desc": "一张4", "card_ids": ["S4-0"]},
                {"type": 1, "rank": 2, "desc": "一张2", "card_ids": ["H15-0"]},
            ],
        ))
        assert not any("残局斩杀" in t for t in titles), f"实际: {titles}"

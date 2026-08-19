"""scoring.py 单元测试：炸弹翻倍与终局结算。

规则要点（依据 backend/scoring.py 实现）：
- 只有 card_type >= 20（炸弹类）才可能计入翻倍，连对/钢板不算
- 仅 6 张及以上炸弹计入翻倍：6->x2, 7->x4, 8->x8（2**(n-5)）
- 四大天王（type=100）固定 x8
- 底分：双游 300 / 一三游 200 / 单游 100
- 封顶 MAX_SCORE = 2400
"""
import pytest

from backend.scoring import ScoreManager


def make_cards(n):
    """结算逻辑只用 len(cards)，用占位对象即可。"""
    return [object()] * n


class TestRecordBomb:
    def test_normal_types_ignored(self):
        sm = ScoreManager()
        # 单张/对子/三带二/顺子/连对/钢板 均 < 20，不记录
        for ctype, n in [(1, 1), (2, 2), (4, 5), (5, 5), (6, 6), (7, 6)]:
            sm.record_bomb(make_cards(n), ctype)
        assert sm.bomb_history == []
        assert sm.calculate_multiplier()[0] == 1

    def test_small_bombs_not_counted(self):
        """4/5 张炸虽是炸弹，但不足 6 张不翻倍。"""
        sm = ScoreManager()
        sm.record_bomb(make_cards(4), 20)
        sm.record_bomb(make_cards(5), 21)
        assert sm.bomb_history == []
        assert sm.calculate_multiplier()[0] == 1

    @pytest.mark.parametrize("n,expected", [(6, 2), (7, 4), (8, 8), (9, 16)])
    def test_big_bomb_multiplier(self, n, expected):
        sm = ScoreManager()
        sm.record_bomb(make_cards(n), 40)
        mult, details = sm.calculate_multiplier()
        assert mult == expected
        assert f"{n}张炸弹 x{expected}" in details

    def test_king_bomb(self):
        sm = ScoreManager()
        sm.record_bomb(make_cards(4), 100)
        mult, details = sm.calculate_multiplier()
        assert mult == 8
        assert details == ["天王炸 x8"]

    def test_multiplier_accumulates(self):
        """多个炸弹倍数相乘：6张(x2) * 7张(x4) = x8。"""
        sm = ScoreManager()
        sm.record_bomb(make_cards(6), 40)
        sm.record_bomb(make_cards(7), 41)
        assert sm.calculate_multiplier()[0] == 8


class TestFinalScore:
    def test_double_win(self):
        """双游：队友 1、2 名 -> 底分 300。"""
        r = ScoreManager().calculate_final_score(
            ["User", "PartnerBot", "LeftBot", "RightBot"]
        )
        assert r["info"]["type"] == "双游"
        assert r["info"]["base"] == 300
        assert r["scores"]["User"] == 300
        assert r["scores"]["PartnerBot"] == 300
        assert r["scores"]["LeftBot"] == -300
        assert r["scores"]["RightBot"] == -300

    def test_one_three_win(self):
        """一三游：队友 1、3 名 -> 底分 200。"""
        r = ScoreManager().calculate_final_score(
            ["User", "LeftBot", "PartnerBot", "RightBot"]
        )
        assert r["info"]["type"] == "一三游"
        assert r["scores"]["User"] == 200
        assert r["scores"]["PartnerBot"] == 200
        assert r["scores"]["LeftBot"] == -200

    def test_single_win(self):
        """单游：队友 1、4 名 -> 底分 100。"""
        r = ScoreManager().calculate_final_score(
            ["User", "LeftBot", "RightBot", "PartnerBot"]
        )
        assert r["info"]["type"] == "单游"
        assert r["scores"]["User"] == 100
        assert r["scores"]["PartnerBot"] == 100
        assert r["scores"]["LeftBot"] == -100

    def test_zero_sum(self):
        """任何结算下总分必须为 0（零和）。"""
        for order in (
            ["User", "PartnerBot", "LeftBot", "RightBot"],
            ["User", "LeftBot", "PartnerBot", "RightBot"],
            ["LeftBot", "RightBot", "User", "PartnerBot"],
        ):
            r = ScoreManager().calculate_final_score(order)
            assert sum(r["scores"].values()) == 0

    def test_opponent_team_wins(self):
        """对家队伍获胜时符号应相反。"""
        r = ScoreManager().calculate_final_score(
            ["LeftBot", "RightBot", "User", "PartnerBot"]
        )
        assert r["scores"]["LeftBot"] == 300
        assert r["scores"]["User"] == -300

    def test_multiplier_applied(self):
        """双游 300 * 6张炸(x2) = 600。"""
        sm = ScoreManager()
        sm.record_bomb(make_cards(6), 40)
        r = sm.calculate_final_score(["User", "PartnerBot", "LeftBot", "RightBot"])
        assert r["info"]["mult"] == 2
        assert r["scores"]["User"] == 600
        assert r["info"]["capped"] is False

    def test_score_capped(self):
        """300 * 8张(x8) * 8张(x8) = 19200 -> 封顶 2400。"""
        sm = ScoreManager()
        sm.record_bomb(make_cards(8), 42)
        sm.record_bomb(make_cards(8), 42)
        r = sm.calculate_final_score(["User", "PartnerBot", "LeftBot", "RightBot"])
        assert r["info"]["capped"] is True
        assert r["scores"]["User"] == 2400
        assert r["scores"]["LeftBot"] == -2400
        assert sum(r["scores"].values()) == 0

    def test_invalid_order_returns_none(self):
        assert ScoreManager().calculate_final_score(["User", "PartnerBot"]) is None

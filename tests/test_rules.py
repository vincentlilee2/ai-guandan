"""rules.py 单元测试：牌型识别与大小比较。

覆盖 PatternRecognizer.get_legal_moves 与 Comparator.can_beat，
包含掼蛋特有规则：逢人配（红桃2）、炸弹分级、同花顺夹在 5 炸与 6 炸之间。
"""
import pytest

from backend.models import Card, Rank, Suit
from backend.rules import CardType, Comparator, PatternRecognizer


def C(rank, suit=Suit.SPADES, uid=None):
    """构造一张牌；uid 用于避免同点同花时 id 冲突。"""
    c = Card(suit=suit, rank=rank)
    if uid is not None:
        c.id = f"{suit.value}-{int(rank)}-{uid}"
    return c


def types_of(hand):
    return {m["type"] for m in PatternRecognizer.get_legal_moves(hand)}


def find(hand, ctype):
    """取出指定牌型的所有候选。"""
    return [m for m in PatternRecognizer.get_legal_moves(hand) if m["type"] == ctype]


class TestBasicPatterns:
    def test_single(self):
        moves = PatternRecognizer.get_legal_moves([C(Rank.R5)])
        singles = [m for m in moves if m["type"] == CardType.SINGLE]
        assert len(singles) == 1
        assert singles[0]["rank"] == Rank.R5
        assert singles[0]["card_ids"] == [C(Rank.R5).id]

    def test_pair(self):
        hand = [C(Rank.R7, Suit.SPADES), C(Rank.R7, Suit.HEARTS)]
        pairs = find(hand, CardType.PAIR)
        assert len(pairs) >= 1
        assert pairs[0]["rank"] == Rank.R7

    def test_triplet(self):
        hand = [
            C(Rank.R9, Suit.SPADES),
            C(Rank.R9, Suit.HEARTS),
            C(Rank.R9, Suit.CLUBS),
        ]
        assert CardType.TRIPLET in types_of(hand)

    def test_triplet_pair(self):
        """三带二（葫芦）。"""
        hand = [
            C(Rank.R9, Suit.SPADES), C(Rank.R9, Suit.HEARTS), C(Rank.R9, Suit.CLUBS),
            C(Rank.R4, Suit.SPADES), C(Rank.R4, Suit.DIAMONDS),
        ]
        tp = find(hand, CardType.TRIPLET_PAIR)
        assert tp, "应识别出三带二"
        # 三带二比较点数取三张的点数
        assert tp[0]["rank"] == Rank.R9

    def test_straight_five(self):
        hand = [C(r, Suit.SPADES) for r in
                (Rank.R3, Rank.R4, Rank.R5, Rank.R6, Rank.R7)]
        assert CardType.STRAIGHT in types_of(hand)

    def test_four_cards_not_a_straight(self):
        """顺子必须恰好 5 张。"""
        hand = [C(r, Suit.SPADES) for r in (Rank.R3, Rank.R4, Rank.R5, Rank.R6)]
        assert CardType.STRAIGHT not in types_of(hand)

    def test_consecutive_pairs(self):
        """三连对：556677。"""
        hand = [
            C(Rank.R5, Suit.SPADES), C(Rank.R5, Suit.CLUBS),
            C(Rank.R6, Suit.SPADES), C(Rank.R6, Suit.CLUBS),
            C(Rank.R7, Suit.SPADES), C(Rank.R7, Suit.CLUBS),
        ]
        assert CardType.CONSECUTIVE_PAIRS in types_of(hand)

    def test_consecutive_triplets(self):
        """钢板：两连三张 555666。"""
        hand = [
            C(Rank.R5, Suit.SPADES), C(Rank.R5, Suit.CLUBS), C(Rank.R5, Suit.DIAMONDS),
            C(Rank.R6, Suit.SPADES), C(Rank.R6, Suit.CLUBS), C(Rank.R6, Suit.DIAMONDS),
        ]
        assert CardType.CONSECUTIVE_TRIPLETS in types_of(hand)


class TestBombs:
    def test_bomb_four(self):
        hand = [C(Rank.R8, s) for s in
                (Suit.SPADES, Suit.HEARTS, Suit.CLUBS, Suit.DIAMONDS)]
        assert CardType.BOMB_4 in types_of(hand)

    def test_bomb_five(self):
        hand = [C(Rank.R8, s, uid=i) for i, s in enumerate(
            (Suit.SPADES, Suit.HEARTS, Suit.CLUBS, Suit.DIAMONDS, Suit.SPADES))]
        assert CardType.BOMB_5 in types_of(hand)

    def test_king_bomb(self):
        """四大天王：两小王 + 两大王。"""
        hand = [
            C(Rank.R_SMALL, Suit.JOKER, uid=1), C(Rank.R_SMALL, Suit.JOKER, uid=2),
            C(Rank.R_BIG, Suit.JOKER, uid=3), C(Rank.R_BIG, Suit.JOKER, uid=4),
        ]
        assert CardType.KING_BOMB in types_of(hand)

    def test_straight_flush(self):
        hand = [C(r, Suit.HEARTS) for r in
                (Rank.R3, Rank.R4, Rank.R5, Rank.R6, Rank.R7)]
        assert CardType.STRAIGHT_FLUSH in types_of(hand)


class TestWildCard:
    """逢人配：红桃2 可当任意牌用。"""

    def test_heart_two_is_wild(self):
        assert C(Rank.R2, Suit.HEARTS).is_wild is True

    def test_other_two_not_wild(self):
        assert C(Rank.R2, Suit.SPADES).is_wild is False
        assert C(Rank.R5, Suit.HEARTS).is_wild is False

    def test_wild_completes_pair(self):
        """单张 K + 红桃2 应能凑成一对 K。"""
        hand = [C(Rank.RK, Suit.SPADES), C(Rank.R2, Suit.HEARTS)]
        pairs = find(hand, CardType.PAIR)
        ranks = {m["rank"] for m in pairs}
        assert Rank.RK in ranks, f"逢人配应能配出一对K，实际: {ranks}"

    def test_wild_completes_bomb(self):
        """三张 8 + 红桃2 应能凑成 4 张炸。"""
        hand = [
            C(Rank.R8, Suit.SPADES), C(Rank.R8, Suit.CLUBS), C(Rank.R8, Suit.DIAMONDS),
            C(Rank.R2, Suit.HEARTS),
        ]
        assert CardType.BOMB_4 in types_of(hand)


class TestWildJokerProhibited:
    """红桃2（逢人配）严禁与王牌（大王/小王）配对/组炸。

    规则：红桃2可配普通牌形成对/对/三/炸/顺等，但在任何组合中（包括炸弹）均禁止与王同时出现。
    """

    @staticmethod
    def _combos(hand):
        return [m for m in PatternRecognizer.get_legal_moves(hand)
                if m["type"] not in (CardType.SINGLE,)]

    def test_wild_small_joker_not_a_pair(self):
        """单张 小王 + 红桃2 不得凑成一对小王（非法）。"""
        hand = [C(Rank.R_SMALL, Suit.JOKER, uid=1), C(Rank.R2, Suit.HEARTS)]
        moves = self._combos(hand)
        # 只允许两个单张，不得出现含红桃2+王的组合
        assert moves == [], f"小王+红桃2 不应组成任何非单张牌型，实际: {[m['desc'] for m in moves]}"

    def test_wild_big_joker_not_a_pair(self):
        """单张 大王 + 红桃2 不得凑成一对大王（非法）。"""
        hand = [C(Rank.R_BIG, Suit.JOKER, uid=1), C(Rank.R2, Suit.HEARTS)]
        moves = self._combos(hand)
        assert moves == [], f"大王+红桃2 不应组成任何非单张牌型，实际: {[m['desc'] for m in moves]}"

    def test_wild_pair_jokers_not_a_triplet(self):
        """对小王 + 红桃2 不得凑成三张（小王）（非法）。"""
        hand = [
            C(Rank.R_SMALL, Suit.JOKER, uid=1), C(Rank.R_SMALL, Suit.JOKER, uid=2),
            C(Rank.R2, Suit.HEARTS),
        ]
        for m in self._combos(hand):
            assert "王" not in m["desc"] or "红桃2" not in m["desc"], \
                f"不应出现红桃2配王的组合: {m['desc']}"

    def test_wild_triple_jokers_not_a_bomb(self):
        """三张 小王 + 红桃2 不得凑成 4 张炸（非法）。"""
        hand = [
            C(Rank.R_SMALL, Suit.JOKER, uid=1), C(Rank.R_SMALL, Suit.JOKER, uid=2),
            C(Rank.R_SMALL, Suit.JOKER, uid=3), C(Rank.R2, Suit.HEARTS),
        ]
        assert CardType.BOMB_4 not in types_of(hand), "三小王+红桃2 不应组成炸弹"

    def test_wild_natural_pair_2_still_legal(self):
        """红桃2 与自然2 配对仍合法（222+红桃2 炸弹/对2 不受影响）。"""
        hand = [
            C(Rank.R2, Suit.SPADES), C(Rank.R2, Suit.CLUBS),
            C(Rank.R2, Suit.DIAMONDS), C(Rank.R2, Suit.HEARTS),
        ]
        assert CardType.BOMB_4 in types_of(hand), "三张2+红桃2 仍应能组4张2炸"
        pair_2 = [m for m in find(hand, CardType.PAIR) if m["rank"] == Rank.R2]
        assert pair_2, "自然2 对子仍应存在"

    def test_natural_joker_pair_still_legal(self):
        """自然对王（对小王/对大王）仍应存在。"""
        hand = [
            C(Rank.R_SMALL, Suit.JOKER, uid=1), C(Rank.R_SMALL, Suit.JOKER, uid=2),
        ]
        pairs = find(hand, CardType.PAIR)
        assert any(m["rank"] == Rank.R_SMALL for m in pairs), "自然对小王 应仍存在"


class TestTypeWeight:
    """牌型权重：四大天王 > 8炸 > 7炸 > 6炸 > 同花顺 > 5炸 > 4炸 > 普通。"""

    def test_weight_ordering(self):
        w = Comparator.get_type_weight
        normal = w(CardType.SINGLE, 1)
        b4 = w(CardType.BOMB_4, 4)
        b5 = w(CardType.BOMB_5, 5)
        sf = w(CardType.STRAIGHT_FLUSH, 5)
        b6 = w(CardType.BOMB_6, 6)
        b7 = w(CardType.BOMB_7, 7)
        b8 = w(CardType.BOMB_8, 8)
        king = w(CardType.KING_BOMB, 4)
        assert normal < b4 < b5 < sf < b6 < b7 < b8 < king


def move(mtype, rank, n=1):
    """构造一个用于比较的 move 字典（can_beat 只用到这些字段）。"""
    return {"type": int(mtype), "rank": int(rank), "card_ids": ["x"] * n, "cards": []}


class TestCanBeat:
    def test_first_move_always_allowed(self):
        assert Comparator.can_beat(None, move(CardType.SINGLE, Rank.R3)) is True

    def test_pass_cannot_beat(self):
        assert Comparator.can_beat(move(CardType.SINGLE, Rank.R3), move(0, 0)) is False

    def test_bigger_single_wins(self):
        assert Comparator.can_beat(
            move(CardType.SINGLE, Rank.R5), move(CardType.SINGLE, Rank.RK)
        ) is True

    def test_smaller_single_loses(self):
        assert Comparator.can_beat(
            move(CardType.SINGLE, Rank.RK), move(CardType.SINGLE, Rank.R5)
        ) is False

    def test_equal_rank_loses(self):
        """同点数压不过（必须严格大于）。"""
        assert Comparator.can_beat(
            move(CardType.SINGLE, Rank.R9), move(CardType.SINGLE, Rank.R9)
        ) is False

    def test_different_normal_types_cannot_beat(self):
        """对子压不了单张。"""
        assert Comparator.can_beat(
            move(CardType.SINGLE, Rank.R3), move(CardType.PAIR, Rank.RA, 2)
        ) is False

    def test_bomb_beats_normal(self):
        assert Comparator.can_beat(
            move(CardType.SINGLE, Rank.RA), move(CardType.BOMB_4, Rank.R3, 4)
        ) is True

    def test_normal_cannot_beat_bomb(self):
        assert Comparator.can_beat(
            move(CardType.BOMB_4, Rank.R3, 4), move(CardType.SINGLE, Rank.RA)
        ) is False

    def test_bigger_bomb_beats_smaller(self):
        assert Comparator.can_beat(
            move(CardType.BOMB_4, Rank.RA, 4), move(CardType.BOMB_5, Rank.R3, 5)
        ) is True

    def test_same_size_bomb_compares_rank(self):
        assert Comparator.can_beat(
            move(CardType.BOMB_4, Rank.R5, 4), move(CardType.BOMB_4, Rank.RK, 4)
        ) is True
        assert Comparator.can_beat(
            move(CardType.BOMB_4, Rank.RK, 4), move(CardType.BOMB_4, Rank.R5, 4)
        ) is False

    def test_king_bomb_beats_everything(self):
        for mv in (
            move(CardType.BOMB_8, Rank.RA, 8),
            move(CardType.STRAIGHT_FLUSH, Rank.RA, 5),
            move(CardType.SINGLE, Rank.RA),
        ):
            assert Comparator.can_beat(mv, move(CardType.KING_BOMB, 0, 4)) is True

    def test_nothing_beats_king_bomb(self):
        assert Comparator.can_beat(
            move(CardType.KING_BOMB, 0, 4), move(CardType.BOMB_8, Rank.RA, 8)
        ) is False


class TestStraightFlushRanking:
    """同花顺特殊位：> 5张炸，< 6张炸。"""

    @pytest.mark.parametrize("bomb_len,bomb_type", [(4, CardType.BOMB_4),
                                                    (5, CardType.BOMB_5)])
    def test_straight_flush_beats_small_bombs(self, bomb_len, bomb_type):
        assert Comparator.can_beat(
            move(bomb_type, Rank.RA, bomb_len),
            move(CardType.STRAIGHT_FLUSH, Rank.R3, 5),
        ) is True

    def test_straight_flush_loses_to_six_bomb(self):
        assert Comparator.can_beat(
            move(CardType.BOMB_6, Rank.R3, 6),
            move(CardType.STRAIGHT_FLUSH, Rank.RA, 5),
        ) is False

    def test_six_bomb_beats_straight_flush(self):
        assert Comparator.can_beat(
            move(CardType.STRAIGHT_FLUSH, Rank.RA, 5),
            move(CardType.BOMB_6, Rank.R3, 6),
        ) is True

    def test_small_bomb_cannot_beat_straight_flush(self):
        assert Comparator.can_beat(
            move(CardType.STRAIGHT_FLUSH, Rank.R3, 5),
            move(CardType.BOMB_4, Rank.RA, 4),
        ) is False

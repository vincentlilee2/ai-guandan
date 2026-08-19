"""
v2.5 Redis 持久化：GuandanGame 对象 ↔ JSON dict 序列化往返测试。

不依赖 redis-py / redis 服务，纯验证 to_dict()/from_dict() 的完整性：
hands（Card 反序列化）、history、seq、state、current_turn_index、
score_manager（bomb_history/teams）等关键字段一致，且运行时态（锁/处理标志）
正确重建。
"""
import asyncio
import json

from backend.game_engine import GuandanGame
from backend.models import Card, Suit, Rank


def _snapshot_fields(g):
    return {
        "game_id": g.game_id,
        "players": list(g.players),
        "hands": {p: sorted((c.suit.value, c.rank.value, c.id) for c in cards)
                  for p, cards in g.hands.items()},
        "current_turn_index": g.current_turn_index,
        "history": list(g.history),
        "last_move": g.last_move,
        "pass_count": g.pass_count,
        "finished_players": list(g.finished_players),
        "state": g.state,
        "final_result": g.final_result,
        "score_applied": g.score_applied,
        "initial_hands": dict(g.initial_hands),
        "seq": g.seq,
        "bomb_history": list(g.score_manager.bomb_history),
        "teams": dict(g.score_manager.teams),
        "processed_request_ids": sorted(g.processed_request_ids),
    }


def _round_trip(g):
    data = g.to_dict()
    payload = json.dumps(data, ensure_ascii=False)  # 模拟 Redis 字符串存取
    restored = GuandanGame.from_dict(json.loads(payload))
    return restored


def test_round_trip_after_start():
    g = GuandanGame("rt-1")
    g.start_game()
    assert g.hands["User"], "开局应有手牌"
    assert g.state == "playing"

    g2 = _round_trip(g)
    assert _snapshot_fields(g2) == _snapshot_fields(g), "开局态往返应完全一致"

    # 运行时态重建
    assert g2._ai_processing is False
    assert isinstance(g2._ai_lock, asyncio.Lock)
    # Card 反序列化后类型正确
    for p, cards in g2.hands.items():
        for c in cards:
            assert isinstance(c, Card)
            assert isinstance(c.suit, Suit)
            assert isinstance(c.rank, Rank)


def test_round_trip_after_moves_and_score():
    g = GuandanGame("rt-2")
    g.start_game()
    # 走一手当前回合玩家的合法出牌，制造 history / seq 变化
    player = g.players[g.current_turn_index]
    moves = g.get_legal_moves_for_current_player()
    real = [m for m in moves if m["type"] != 0]
    if real:
        res = g.execute_move(player, real[0]["id"], moves)
        assert "error" not in res, res

    g2 = _round_trip(g)
    assert _snapshot_fields(g2) == _snapshot_fields(g), "出牌后往返应完全一致"
    assert g2.seq == g.seq
    assert g2.history == g.history


def test_card_wild_and_joker_ids_round_trip():
    """含赖子（红桃2）与小王的牌型反序列化后保持 id 不变。"""
    from backend.game_engine import GuandanGame
    g = GuandanGame("rt-3")
    g.start_game()
    # 直接注入一张赖子 + 一张大王验证 id 保真
    g.hands["User"] = [
        Card(Suit.HEARTS, Rank.R2, "H2-0"),
        Card(Suit.JOKER, Rank.R_BIG, "J21-0"),
        Card(Suit.SPADES, Rank.RA, "SA-0"),
    ]
    g2 = _round_trip(g)
    ids = [c.id for c in g2.hands["User"]]
    assert ids == ["H2-0", "J21-0", "SA-0"], ids


def test_score_manager_state_round_trip():
    from backend.scoring import ScoreManager
    sm = ScoreManager()
    sm.record_bomb([Card(Suit.HEARTS, Rank.R3, "H3-0")], 20)  # 4炸不算
    assert sm.bomb_history == []
    sm.record_bomb([Card(Suit.HEARTS, Rank.R3, "H3-0"),
                    Card(Suit.HEARTS, Rank.R3, "H3-1"),
                    Card(Suit.HEARTS, Rank.R3, "H3-2"),
                    Card(Suit.HEARTS, Rank.R3, "H3-3"),
                    Card(Suit.HEARTS, Rank.R4, "H4-0"),
                    Card(Suit.HEARTS, Rank.R4, "H4-1")], 30)  # 6炸
    assert sm.bomb_history == [6]
    sm.teams["User"] = "TeamX"  # 模拟自定义队伍

    sm2 = ScoreManager.from_dict(json.loads(json.dumps(sm.to_dict())))
    assert sm2.bomb_history == [6]
    assert sm2.MAX_SCORE == sm.MAX_SCORE
    assert sm2.teams == sm.teams

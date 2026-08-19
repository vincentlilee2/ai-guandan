"""
v2.5 AI 决策上下文隔离回归测试。

目标：多局并发时，get_ai_context(game_id, role) 严格按局隔离：
  - 未知 game_id → 返回 None（不再落回全局 LAST_AI_CONTEXTS，避免跨局串档）
  - 已 set_ai_context 的 game_id → 返回该局该角色的上下文
  - 不同局的同角色上下文互不干扰
"""
from backend import ai_client


def _reset():
    ai_client.clear_ai_contexts(None)


def test_unknown_game_returns_none():
    _reset()
    # 先写入全局兜底（模拟旧 sync 路径残留），验证 get_ai_context 不再回退它
    ai_client.LAST_AI_CONTEXTS["RightBot"] = {"system_prompt": "global-sys", "user_prompt": "global-usr", "ai_response": "global-resp"}
    assert ai_client.get_ai_context("no-such-game", "RightBot") is None, \
        "未知局应返回 None，不得回退到全局 LAST_AI_CONTEXTS"


def test_known_game_returns_ctx():
    _reset()
    ctx = {"system_prompt": "s", "user_prompt": "u", "ai_response": "r"}
    ai_client.set_ai_context("game-a", "LeftBot", dict(ctx))
    got = ai_client.get_ai_context("game-a", "LeftBot")
    assert got is not None
    assert got["ai_response"] == "r"


def test_same_role_different_games_isolated():
    _reset()
    ai_client.set_ai_context("game-a", "RightBot", {"system_prompt": "a-sys", "user_prompt": "a-usr", "ai_response": "a-resp"})
    ai_client.set_ai_context("game-b", "RightBot", {"system_prompt": "b-sys", "user_prompt": "b-usr", "ai_response": "b-resp"})

    assert ai_client.get_ai_context("game-a", "RightBot")["ai_response"] == "a-resp"
    assert ai_client.get_ai_context("game-b", "RightBot")["ai_response"] == "b-resp"


def test_move_index_stored():
    _reset()
    ai_client.set_ai_context("game-c", "PartnerBot", {"system_prompt": "s"}, move_index=7)
    ctx = ai_client.get_ai_context("game-c", "PartnerBot")
    assert ctx.get("move_index") == 7


def test_clear_by_game_does_not_affect_others():
    _reset()
    ai_client.set_ai_context("game-x", "User", {"system_prompt": "x"})
    ai_client.set_ai_context("game-y", "User", {"system_prompt": "y"})
    ai_client.clear_ai_contexts("game-x")
    assert ai_client.get_ai_context("game-x", "User") is None
    assert ai_client.get_ai_context("game-y", "User") is not None

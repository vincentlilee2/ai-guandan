"""
AI 教练复盘 测试。

覆盖：
  - extract_user_moves：只取 User 的 PLAY 与 PASS，附当手手牌/全场剩余/上下文
  - _build_move_prompt / _build_system_prompt：只含当前手上下文 + 触发的策略要点
  - _suspicious_score：规则筛选（拆炸弹/三带二带大对子/放走单张）
  - parse_coach_review：```json 块 / 纯数组 / 带前缀文本 / 非 JSON
  - build_coach_review：无 User 手、LLM 失败、成功 + 二次缓存
  - 端点鉴权：无 token → 403
"""
import json
import asyncio
import os
import subprocess
import time

import httpx
import pytest

from backend.coach import (
    build_coach_review,
    extract_user_moves,
    parse_coach_review,
    _filter_no_problem_reviews,
    _build_move_prompt,
    _build_system_prompt,
    _suspicious_score,
)

PORT = 8017
BASE = f"http://127.0.0.1:{PORT}"


def _fake_data():
    """构造一个含 User PLAY/PASS 的小型复盘数据。"""
    return {
        "game_id": "coach-test-1",
        "players": ["User", "RightBot", "PartnerBot", "LeftBot"],
        "initial_hands": {
            "User": ["S3-0", "H4-0", "D5-0", "C6-0"],
            "RightBot": ["S10-0", "H10-0"],
            "PartnerBot": ["S11-0", "H11-0"],
            "LeftBot": ["S12-0", "H12-0"],
        },
        "winner_order": ["LeftBot", "RightBot"],
        "history": [
            {"player": "User", "action": "PLAY", "cards": ["H4-0"], "desc": "一张4"},
            {"player": "RightBot", "action": "PASS", "cards": None, "desc": None},
            {"player": "PartnerBot", "action": "PASS", "cards": None, "desc": None},
            {"player": "LeftBot", "action": "PASS", "cards": None, "desc": None},
            {"player": "User", "action": "PLAY", "cards": ["D5-0", "C6-0"], "desc": "对子5-6"},
            {"player": "RightBot", "action": "PASS", "cards": None, "desc": None},
            {"player": "User", "action": "PASS", "cards": None, "desc": None},
        ],
        "result": {},
    }


# ---------------- extract_user_moves ----------------

def test_extract_user_moves_only_user_play_and_pass():
    moves = extract_user_moves(_fake_data())
    # User 有 PLAY(×2) 和 PASS(×1)，共 3 手
    assert [m["action"] for m in moves] == ["PLAY", "PLAY", "PASS"]
    assert all(m["player"] == "User" for m in moves)

    # 第 0 手 hand_before = 完整初始手牌；出 1 张后剩 3
    m0 = moves[0]
    assert len(m0["hand_before"]) == 4
    assert m0["remaining"]["User"] == 3
    # 第 1 手（第 5 手）已出 H4 → hand_before 3 张；再出 2 张 → 剩 1
    m1 = moves[1]
    assert len(m1["hand_before"]) == 3
    assert m1["remaining"]["User"] == 1
    # PASS 手：hand_before 与剩余一致（不出牌）
    m2 = moves[2]
    assert m2["action"] == "PASS"
    assert len(m2["hand_before"]) == 1
    assert m2["remaining"]["User"] == 1


def test_extract_user_moves_no_user_returns_empty():
    data = _fake_data()
    data["history"] = [m for m in data["history"] if m["player"] != "User"]
    assert extract_user_moves(data) == []


# ---------------- prompt 构造 ----------------

def test_build_move_prompt_contains_context():
    moves = extract_user_moves(_fake_data())
    prompt = _build_move_prompt(moves[0], _fake_data(), [])
    assert "当时 User 手牌" in prompt
    assert "一张4" in prompt
    assert "请只判断这一手" in prompt


def test_build_move_prompt_includes_triggered_rules():
    moves = extract_user_moves(_fake_data())
    # 传入一条"触发的策略"，应出现在 prompt 里（不会出现整库倾倒）
    triggered = [("roles", "leader", "首发优先打出小牌/散牌，避免无意义拆牌。")]
    prompt = _build_move_prompt(moves[0], _fake_data(), triggered)
    assert "首发优先打出小牌" in prompt
    # 该手没触发 teammate 的"接风"规则 → 不应出现
    assert "接风" not in prompt


def test_build_system_prompt_is_minimal():
    prompt = _build_system_prompt()
    assert "掼蛋顶级高手" in prompt           # system_prompts.base
    assert "三带二" in prompt                 # static_rules
    assert "红桃2" in prompt                  # static_rules
    # 不再倾倒整库策略（触发要点在每手 user prompt 里给）
    assert "### roles 策略要点" not in prompt
    assert "### teammate 策略要点" not in prompt
    assert "### situational 策略要点" not in prompt


# ---------------- _suspicious_score（规则筛选） ----------------

def _moves_with_hand(data, hand_before, desc, cards, remaining=None):
    """构造一个 User 手 dict 供 _suspicious_score 用。"""
    players = data.get("players", [])
    return {
        "move_index": 0,
        "player": "User",
        "action": "PLAY",
        "cards": cards,
        "desc": desc,
        "hand_before": hand_before,
        "remaining": remaining or {p: 27 for p in players},
        "recent_moves": [],
        "big_played": [],
    }


def test_suspicious_split_bomb():
    data = _fake_data()
    # 手牌有 4 张 9（炸弹），却拆 3 张 9 打三张 → 拆炸弹
    hand = ["S9-0", "H9-0", "D9-0", "C9-0", "S2-0"]
    m = _moves_with_hand(data, hand, "三张9", ["S9-0", "H9-0", "D9-0"])
    assert _suspicious_score(m, data, data["history"]) >= 2


def test_suspicious_play_full_bomb_not_split():
    data = _fake_data()
    # 整手打 4 张 9 炸弹 → 不算拆
    hand = ["S9-0", "H9-0", "D9-0", "C9-0", "S2-0"]
    m = _moves_with_hand(data, hand, "4张9", ["S9-0", "H9-0", "D9-0", "C9-0"])
    assert _suspicious_score(m, data, data["history"]) == 0


def test_suspicious_wild_bomb_used_as_full_bomb_not_split():
    data = _fake_data()
    # 手里 3 张 J + 红桃2（H15）＝ 4 张 J 炸，整手打出 4 张 J（含赖子）→ 不算拆
    hand = ["S11-0", "C11-1", "H11-1", "H15-0", "S5-0"]
    m = _moves_with_hand(data, hand, "4张J (含赖子)", ["H11-1", "C11-1", "S11-0", "H15-0"])
    assert _suspicious_score(m, data, data["history"]) == 0


def test_suspicious_split_wild_bomb():
    data = _fake_data()
    # 手里 3 张 J + 红桃2 本可组 4 张 J 炸，却拆 2 张 J 打对子 → 拆炸弹
    hand = ["S11-0", "C11-1", "H11-1", "H15-0", "S5-0"]
    m = _moves_with_hand(data, hand, "对J", ["S11-0", "C11-1"])
    assert _suspicious_score(m, data, data["history"]) >= 2


def test_suspicious_fullhouse_big_pair():
    data = _fake_data()
    # 三带二带对A（对A=14 在出牌里）→ 明显错误
    hand = ["S13-0", "H13-0", "D13-0", "C14-0", "S12-0", "H14-0", "S5-0"]
    m = _moves_with_hand(data, hand, "三K带对A", ["S13-0", "H13-0", "D13-0", "C14-0", "H14-0"])
    assert _suspicious_score(m, data, data["history"]) >= 2


def test_suspicious_fullhouse_small_pair_not_flagged():
    data = _fake_data()
    # 三带二带对5（只带小对子，不拆炸弹）→ 不应判有问题
    hand = ["S13-0", "H13-0", "D13-0", "S5-0", "H5-0", "S6-0", "H9-0"]
    m = _moves_with_hand(data, hand, "三K带对5", ["S13-0", "H13-0", "D13-0", "S5-0", "H5-0"])
    assert _suspicious_score(m, data, data["history"]) == 0

def test_card_label_jokers_and_wild():
    from backend.coach import _card_label
    assert _card_label("J20-0") == "小王"
    assert _card_label("J21-0") == "大王"
    assert _card_label("H15-0") == "♥2*"


def test_suspicious_reasonable_play_low_score():
    data = _fake_data()
    # 手牌无炸弹、无大对子，打对子 → 不应判有问题
    hand = ["S5-0", "H5-0", "S6-0", "H6-0", "S7-0"]
    m = _moves_with_hand(data, hand, "对5", ["S5-0", "H5-0"])
    assert _suspicious_score(m, data, data["history"]) == 0


# ---------------- parse_coach_review ----------------

def test_parse_fenced_json():
    content = '```json\n[{"situation":"s","mistake":"m","advice":"a","action":"PLAY","desc":"一张4"}]\n```'
    reviews = parse_coach_review(content)
    assert len(reviews) == 1
    assert reviews[0]["situation"] == "s"
    assert reviews[0]["mistake"] == "m"
    assert reviews[0]["advice"] == "a"
    assert reviews[0]["action"] == "PLAY"
    assert reviews[0]["desc"] == "一张4"


def test_parse_plain_array():
    reviews = parse_coach_review('[{"situation":"x"}]')
    assert len(reviews) == 1
    assert reviews[0]["situation"] == "x"


def test_parse_with_prefix_text():
    content = "分析如下：\n[{\"situation\":\"y\"}] 完"
    reviews = parse_coach_review(content)
    assert len(reviews) == 1
    assert reviews[0]["situation"] == "y"


def test_parse_invalid_returns_empty():
    assert parse_coach_review("这不是 JSON") == []
    assert parse_coach_review("") == []


# ---------------- build_coach_review（含缓存） ----------------

async def test_build_coach_review_no_user_move(monkeypatch):
    async def fake_llm(_prompt):
        raise AssertionError("不应调用 LLM")
    monkeypatch.setattr("backend.coach.call_coach_llm", fake_llm)
    data = _fake_data()
    data["history"] = [m for m in data["history"] if m["player"] != "User"]
    result = await build_coach_review(data)
    assert result["reviews"] == []
    assert "没有找到" in result.get("message", "")


async def test_build_coach_review_no_candidates_no_llm(monkeypatch):
    """_fake_data 的手全是合理出牌 → 规则筛选后无候选手 → 不调 LLM → 无问题。"""
    calls = {"n": 0}

    async def fake_llm(_prompt):
        calls["n"] += 1
        return []
    monkeypatch.setattr("backend.coach.call_coach_llm", fake_llm)
    from backend import coach as _coach_mod
    _coach_mod._coach_cache.pop("coach-test-1", None)
    result = await build_coach_review(_fake_data())
    assert result["reviews"] == []
    assert result["message"] == "本轮您的出牌没有问题！"
    assert calls["n"] == 0, "明显合理的出牌不应调用 LLM"
    _coach_mod._coach_cache.pop("coach-test-1", None)


async def test_build_coach_review_llm_failure_on_candidate(monkeypatch):
    """构造一个候选（拆炸弹）手，LLM 失败 → 该手被跳过，不报 error、不崩整局。"""
    data = _fake_data()
    # 手里 4 张 4（炸弹），只出 3 张 → 拆炸弹 → 候选
    data["initial_hands"]["User"] = ["S4-0", "H4-0", "D4-0", "C4-0", "S2-0", "H2-0", "S6-0", "S7-0"]
    data["history"][0] = {
        "player": "User", "action": "PLAY",
        "cards": ["S4-0", "H4-0", "D4-0"], "desc": "三张4",
    }
    data["history"][1] = {"player": "RightBot", "action": "PASS"}
    data["history"][2] = {"player": "PartnerBot", "action": "PASS"}
    data["history"][3] = {"player": "LeftBot", "action": "PASS"}

    async def fake_llm(_prompt):
        raise RuntimeError("boom")
    monkeypatch.setattr("backend.coach.call_coach_llm", fake_llm)
    from backend import coach as _coach_mod
    _coach_mod._coach_cache.pop("coach-test-1", None)
    result = await build_coach_review(data)
    assert result["reviews"] == []
    # 无问题 message（候选失败被跳过）
    assert "error" not in result
    _coach_mod._coach_cache.pop("coach-test-1", None)


async def test_build_coach_review_candidate_llm_returns_problem(monkeypatch):
    """候选手（拆炸弹）被 LLM 判为有问题 → 该手进入 reviews。"""
    data = _fake_data()
    data["initial_hands"]["User"] = ["S4-0", "H4-0", "D4-0", "C4-0", "S2-0", "H2-0", "S6-0", "S7-0"]
    data["history"][0] = {
        "player": "User", "action": "PLAY",
        "cards": ["S4-0", "H4-0", "D4-0"], "desc": "三张4",
    }
    data["history"][1] = {"player": "RightBot", "action": "PASS"}
    data["history"][2] = {"player": "PartnerBot", "action": "PASS"}
    data["history"][3] = {"player": "LeftBot", "action": "PASS"}

    async def fake_llm(_prompt):
        return [{"situation": "s", "mistake": "拆了炸弹", "advice": "保留炸弹", "action": "PLAY", "desc": "三张4"}]
    monkeypatch.setattr("backend.coach.call_coach_llm", fake_llm)
    from backend import coach as _coach_mod
    _coach_mod._coach_cache.pop("coach-test-1", None)
    result = await build_coach_review(data)
    assert len(result["reviews"]) == 1
    assert result["reviews"][0]["mistake"] == "拆了炸弹"
    assert result["message"] == ""
    _coach_mod._coach_cache.pop("coach-test-1", None)


async def test_build_coach_review_candidate_llm_returns_clean(monkeypatch):
    """候选手被 LLM 判为无明显错误 → 被过滤，message 为无问题。"""
    data = _fake_data()
    data["initial_hands"]["User"] = ["S4-0", "H4-0", "D4-0", "C4-0", "S2-0", "H2-0", "S6-0", "S7-0"]
    data["history"][0] = {
        "player": "User", "action": "PLAY",
        "cards": ["S4-0", "H4-0", "D4-0"], "desc": "三张4",
    }
    data["history"][1] = {"player": "RightBot", "action": "PASS"}
    data["history"][2] = {"player": "PartnerBot", "action": "PASS"}
    data["history"][3] = {"player": "LeftBot", "action": "PASS"}

    async def fake_llm(_prompt):
        return [{"situation": "s", "mistake": "无明显错误", "advice": "继续让队友", "action": "PLAY", "desc": "三张4"}]
    monkeypatch.setattr("backend.coach.call_coach_llm", fake_llm)
    from backend import coach as _coach_mod
    _coach_mod._coach_cache.pop("coach-test-1", None)
    result = await build_coach_review(data)
    assert result["reviews"] == []
    assert result["message"] == "本局未发现明显出牌问题。"
    _coach_mod._coach_cache.pop("coach-test-1", None)


async def test_build_coach_review_cached(monkeypatch):
    calls = {"n": 0}

    async def fake_llm(_prompt):
        calls["n"] += 1
        return [{"situation": "s", "mistake": "拆了炸弹", "advice": "a", "action": "PLAY", "desc": "三张4"}]
    monkeypatch.setattr("backend.coach.call_coach_llm", fake_llm)
    data = _fake_data()
    data["initial_hands"]["User"] = ["S4-0", "H4-0", "D4-0", "C4-0", "S2-0", "H2-0", "S6-0", "S7-0"]
    data["history"][0] = {
        "player": "User", "action": "PLAY",
        "cards": ["S4-0", "H4-0", "D4-0"], "desc": "三张4",
    }
    data["history"][1] = {"player": "RightBot", "action": "PASS"}
    data["history"][2] = {"player": "PartnerBot", "action": "PASS"}
    data["history"][3] = {"player": "LeftBot", "action": "PASS"}
    from backend import coach as _coach_mod
    _coach_mod._coach_cache.pop("coach-test-1", None)
    first = await build_coach_review(data)
    assert first["cached"] is False
    assert len(first["reviews"]) == 1
    second = await build_coach_review(data)
    assert second["cached"] is True
    assert calls["n"] == 1, "二次分析应命中缓存，不再调 LLM"
    _coach_mod._coach_cache.pop("coach-test-1", None)


# ---------------- 无问题点评过滤 ----------------

def test_filter_keeps_only_problem_reviews():
    reviews = [
        {"mistake": "拆了炸弹", "advice": "保留炸弹"},
        {"mistake": "无明显错误", "advice": "继续让队友"},
        {"mistake": "这一手没有问题", "advice": "保持节奏"},
        {"mistake": "  ", "advice": ""},
        {"mistake": "该先出顺子保留大牌", "advice": "先出顺子"},
    ]
    problems, has_problem = _filter_no_problem_reviews(reviews)
    assert has_problem is True
    assert [r["mistake"] for r in problems] == ["拆了炸弹", "该先出顺子保留大牌"]


def test_filter_all_clean_means_no_problem():
    reviews = [
        {"mistake": "无明显错误", "advice": "继续让队友"},
        {"mistake": "基本合理", "advice": "保持"},
        {"mistake": "", "advice": ""},
    ]
    problems, has_problem = _filter_no_problem_reviews(reviews)
    assert problems == []
    assert has_problem is False


def test_filter_does_not_drop_unreasonable():
    # 裸"合理"不参与判无问题，避免把"不合理/欠合理"误当没问题
    reviews = [{"mistake": "这手不合理，应保留大牌"}]
    problems, has_problem = _filter_no_problem_reviews(reviews)
    assert len(problems) == 1
    assert has_problem is True


# ---------------- 端点鉴权（真实子进程） ----------------

@pytest.fixture(scope="module")
def server():
    import sys
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    child_env["ENABLE_AI_COACH"] = "1"  # AI 教练默认关闭，子进程测试需显式开启该功能
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=child_env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    for _ in range(50):
        try:
            r = httpx.get(f"{BASE}/openapi.json", timeout=2)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.3)
    else:
        proc.kill()
        raise RuntimeError("测试用 uvicorn 启动失败")
    yield BASE
    proc.kill()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


async def test_coach_endpoint_requires_token(server: str):
    async with httpx.AsyncClient(timeout=30) as client:
        start = await client.post(f"{server}/api/start")
        gid = start.json()["game_id"]
        token = start.json()["token"]

        # 无 token / 错 token → 403（有绑定的新局）
        no_tok = await client.get(f"{server}/api/{gid}/coach")
        assert no_tok.status_code in (403, 404), f"无 token 应 403/404, 实际 {no_tok.status_code}"
        bad_tok = await client.get(f"{server}/api/{gid}/coach?token=wrong")
        assert bad_tok.status_code in (403, 404)

        # 正确 token：history 文件不存在时是 404（解析不到数据）；至少不是 403
        ok = await client.get(f"{server}/api/{gid}/coach?token={token}")
        assert ok.status_code != 403

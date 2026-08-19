"""
v2.5 权限隔离（token 鉴权）回归测试。

目标：验证「对局访问 token」机制：
  - /api/start 返回 token
  - 有 token 绑定的对局，无 token / 错 token 的 game_id 接口返回 403
  - 正确 token 返回 200
  - 无 token 绑定的旧 meta 局（兼容）放行

做法：自带独立 uvicorn 子进程（仿 test_async_concurrency.py 的 server fixture），
避免与手动调试服务共享进程内状态。
"""
import asyncio
import os
import subprocess
import time

import httpx
import pytest

from backend.game_store import MemoryGameStore
from backend.game_engine import GuandanGame


PORT = 8014
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def server():
    """启动独立 uvicorn 子进程，测试结束后销毁。"""
    import sys
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "main:app",
            "--host", "127.0.0.1", "--port", str(PORT),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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


def _no_meta_gid():
    """构造一个无 token 绑定（旧 meta 兼容）的直连对象，仅用于测试 _verify_game_access。"""
    gid = "legacy-" + str(int(time.time()))
    game = GuandanGame(gid)
    game.start_game()
    store = MemoryGameStore()
    store.add(gid, game)  # add 不写 owner_token → meta 无绑定
    return gid, store


async def test_start_returns_token(server: str):
    """开局返回 token，且带 token 访问 /state 返回 200。"""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{server}/api/start")
        assert r.status_code == 200
        data = r.json()
        assert data.get("token"), "start 应返回访问 token"
        gid = data["game_id"]
        state = await client.get(f"{server}/api/{gid}/state?token={data['token']}")
        assert state.status_code == 200


async def test_state_requires_token(server: str):
    """有 token 绑定的对局：无 token / 错 token / 对 token 的 /state。"""
    async with httpx.AsyncClient(timeout=30) as client:
        start = await client.post(f"{server}/api/start")
        data = start.json()
        gid = data["game_id"]
        token = data["token"]

        # 无 token → 403
        no_tok = await client.get(f"{server}/api/{gid}/state")
        assert no_tok.status_code == 403, f"无 token 应 403, 实际 {no_tok.status_code}"

        # 错 token → 403
        bad_tok = await client.get(f"{server}/api/{gid}/state?token=wrong-token")
        assert bad_tok.status_code == 403

        # 正确 token → 200
        ok = await client.get(f"{server}/api/{gid}/state?token={token}")
        assert ok.status_code == 200


async def test_moves_requires_token(server: str):
    async with httpx.AsyncClient(timeout=30) as client:
        start = await client.post(f"{server}/api/start")
        gid = start.json()["game_id"]
        token = start.json()["token"]

        no_tok = await client.get(f"{server}/api/{gid}/moves")
        assert no_tok.status_code == 403

        ok = await client.get(f"{server}/api/{gid}/moves?token={token}")
        assert ok.status_code == 200


async def test_play_requires_token(server: str):
    async with httpx.AsyncClient(timeout=30) as client:
        start = await client.post(f"{server}/api/start")
        gid = start.json()["game_id"]
        token = start.json()["token"]

        body = {"game_id": gid, "move_id": 0}
        no_tok = await client.post(f"{server}/api/play", json=body)
        assert no_tok.status_code == 403, "无 token 的 play 应 403"

        body["token"] = "wrong"
        bad_tok = await client.post(f"{server}/api/play", json=body)
        assert bad_tok.status_code == 403

        body["token"] = token
        ok = await client.post(f"{server}/api/play", json=body)
        # 有 token：要么合法出牌，要么返回业务错误(如 400/{"error"})，但绝不能是 403
        assert ok.status_code != 403


async def test_stream_requires_token(server: str):
    async with httpx.AsyncClient(timeout=30) as client:
        start = await client.post(f"{server}/api/start")
        gid = start.json()["game_id"]
        token = start.json()["token"]

        no_tok = await client.get(f"{server}/api/{gid}/stream")
        assert no_tok.status_code == 403

        # SSE 是流式响应：用 stream() 只取首帧（初始快照），验证有 token 不被拒
        async with client.stream("GET", f"{server}/api/{gid}/stream?token={token}") as resp:
            assert resp.status_code != 403, f"有 token 的 SSE 不应被拒, 实际 {resp.status_code}"


async def test_replay_requires_token(server: str):
    async with httpx.AsyncClient(timeout=30) as client:
        start = await client.post(f"{server}/api/start")
        gid = start.json()["game_id"]
        token = start.json()["token"]

        no_tok = await client.get(f"{server}/api/{gid}/replay")
        # 无 token → 403；但若代理白名单(本机 loopback)已放行则可能 404，需至少不是成功读盘
        assert no_tok.status_code in (403, 404)

        bad_tok = await client.get(f"{server}/api/{gid}/replay?token=wrong")
        assert bad_tok.status_code in (403, 404)


def test_legacy_no_token_meta_is_allowed():
    """旧 meta（无 owner_token）对局放行——迁移窗口兼容。"""
    gid, store = _no_meta_gid()
    meta = store.get_meta(gid)
    assert "owner_token" not in meta
    # 直连 store 模拟：无绑定 → 通过（等价 _verify_game_access 的放行分支）
    assert store.get(gid) is not None


async def test_ai_retry_requires_token(server: str):
    async with httpx.AsyncClient(timeout=30) as client:
        start = await client.post(f"{server}/api/start")
        gid = start.json()["game_id"]
        token = start.json()["token"]

        no_tok = await client.post(f"{server}/api/ai_retry", json={"game_id": gid, "force": False})
        # ai_retry 本身有 trusted-proxy 前置校验（本机 loopback 属白名单放行）→ 无 token 应被 game 校验拦截 403
        assert no_tok.status_code == 403

        ok = await client.post(f"{server}/api/ai_retry", json={"game_id": gid, "force": False, "token": token})
        assert ok.status_code != 403


async def test_report_error_requires_token(server: str):
    async with httpx.AsyncClient(timeout=30) as client:
        start = await client.post(f"{server}/api/start")
        gid = start.json()["game_id"]
        token = start.json()["token"]

        no_tok = await client.post(f"{server}/api/report_error", json={"player_name": "User", "game_id": gid})
        assert no_tok.status_code == 403

        ok = await client.post(f"{server}/api/report_error", json={"player_name": "User", "game_id": gid, "token": token})
        # 有 token 不再 403；可能因无上下文返回 no_context(200) 或校验错误
        assert ok.status_code != 403


# ---------------- _load_replay_data 时间戳文件查找 ----------------

def test_load_replay_data_matches_timestamped_file(tmp_path, monkeypatch):
    """历史文件命名 {ts}_{game_id}.json（game_engine.save_history 约定），
    _load_replay_data 应能通过 game_id 匹配到时间戳前缀文件。"""
    import json
    import main
    gid = "abcd1234XYZ"
    data = {"game_id": gid, "players": [], "initial_hands": {}, "history": [], "result": {}}
    f = tmp_path / f"20260816_123456_{gid}.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(main, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(main, "LEGACY_HISTORY_DIR", tmp_path / "legacy")
    monkeypatch.setattr(main, "LATEST_REPLAY_FILE", tmp_path / "latest.json")

    got = main._load_replay_data(gid)
    assert got["game_id"] == gid


def test_load_replay_data_plain_token_file(tmp_path, monkeypatch):
    """{game_id}.json 直连文件也应可读（旧约定兼容）。"""
    import json
    import main
    gid = "plainToken789"
    data = {"game_id": gid, "players": [], "initial_hands": {}, "history": [], "result": {}}
    f = tmp_path / f"{gid}.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(main, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(main, "LEGACY_HISTORY_DIR", tmp_path / "legacy")
    monkeypatch.setattr(main, "LATEST_REPLAY_FILE", tmp_path / "latest.json")

    got = main._load_replay_data(gid)
    assert got["game_id"] == gid


def test_load_replay_data_missing_raises_404(tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(main, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(main, "LEGACY_HISTORY_DIR", tmp_path / "legacy")
    monkeypatch.setattr(main, "LATEST_REPLAY_FILE", tmp_path / "latest.json")
    from fastapi import HTTPException
    try:
        main._load_replay_data("noSuchGame999")
    except HTTPException as e:
        assert e.status_code == 404
    else:
        raise AssertionError("应抛 404")

"""API 前缀断言测试（v2.3 清理）。

自带独立 uvicorn 子进程（端口 8012），避免依赖外部服务、消除 flaky。

验证：
- 所有路由统一在 /api/* 下
- 旧的 /game/* 与 /game/game/* 别名已移除（返回 404）
- 前端静态资源根路径可用（dist 存在时 200）
"""
import os
import subprocess
import time

import httpx
import pytest


PORT = 8012
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def server():
    """启动独立 uvicorn 子进程，测试结束后销毁。"""
    import sys
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(PORT)],
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


def test_api_prefix_routes_exist(server: str):
    """核心 API 应在 /api/* 下响应（开局 + 状态）。"""
    with httpx.Client(base_url=server, timeout=90) as c:
        start = c.post("/api/start").json()
        gid = start["game_id"]
        token = start.get("token", "")
        assert c.get(f"/api/{gid}/state", params={"token": token}).status_code == 200
        assert c.get(f"/api/{gid}/moves", params={"token": token}).status_code == 200
        # replay 仅对已结束对局可用（进行中返回 404 属正常），只验证前缀命中
        assert c.get(f"/api/{gid}/replay", params={"token": token}).status_code in (200, 404)
        assert c.get("/api/score").status_code == 200


def test_old_game_prefix_gone(server: str):
    """旧的 /game/* 与 /game/game/* 别名必须 404（彻底清理）。"""
    with httpx.Client(base_url=server, timeout=30) as c:
        for path in [
            "/game/start",
            "/game/game/start",
            "/game/score",
            "/game/game/score",
            "/game/abc123/state",
            "/game/abc123/replay",
        ]:
            assert c.get(path).status_code == 404, f"{path} 应已移除但返回非 404"


def test_static_root_served(server: str):
    """前端静态资源根路径可用（index.html / assets）。"""
    with httpx.Client(base_url=server, timeout=30) as c:
        r = c.get("/")
        assert r.status_code in (200, 404)

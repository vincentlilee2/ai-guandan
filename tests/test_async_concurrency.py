"""
并发回归测试（v2.1 async 改造的核心价值证明）。

目标：验证「多个对局同时触发 AI 回合」时，FastAPI 事件循环不被阻塞——
即 /state 轮询请求不会因 AI 在 await LLM 而排队。

做法：本测试**自带一个独立的 uvicorn 子进程**（端口 8011），保证限流桶是
全新的，不与手动调试跑的服务相互污染（否则反复运行会触发 429 误报）。
统计 /state 在 AI 思考期间的响应延迟。若改造失败（同步阻塞），
并发对局的 /state 会出现秒级排队；改造成功后应保持毫秒级。
"""
import asyncio
import os
import subprocess
import time

import httpx
import pytest


PORT = 8011
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
    # 等待就绪
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


async def _start(client: httpx.AsyncClient) -> dict:
    r = await client.post(f"{BASE}/api/start")
    r.raise_for_status()
    data = r.json()
    return {"game_id": data["game_id"], "token": data.get("token", "")}


async def _state_latency(client: httpx.AsyncClient, game: dict) -> float:
    """连续打 /state，返回最大单次延迟（秒）。"""
    worst = 0.0
    for _ in range(5):
        t0 = time.monotonic()
        await client.get(f"{BASE}/api/{game['game_id']}/state?token={game['token']}")
        worst = max(worst, time.monotonic() - t0)
    return worst


async def test_concurrent_games_do_not_block_event_loop(server: str):
    """并发开 10 局并同时轮询，最慢的 /state 应在 2 秒内返回。"""
    N = 10
    async with httpx.AsyncClient(timeout=120) as client:
        # 并发开局：开局本身会触发 AI 回合（await LLM）
        games = await asyncio.gather(*[_start(client) for _ in range(N)])

        # 开局后立刻并发轮询所有对局的 /state
        latencies = await asyncio.gather(
            *[_state_latency(client, g) for g in games]
        )

    worst = max(latencies)
    print(f"\n  并发 {N} 局，最慢 /state 延迟 = {worst:.3f}s")
    # 若事件循环被阻塞，worst 会接近单局 AI 思考时间（可达数十秒）
    assert worst < 2.0, f"事件循环疑似被阻塞：最慢延迟 {worst:.2f}s"

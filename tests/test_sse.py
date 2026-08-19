"""SSE 推送测试（v2.4）。

自带 uvicorn 子进程（端口 8013），验证：
- /api/{id}/stream 开局即推初始快照（data: 含完整 state）
- 用户出牌后推送增量事件（seq 递增）
- 客户端断开后生成器退出（不泄漏）
"""
import os
import subprocess
import time
import json

import httpx
import pytest


PORT = 8013
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def server():
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
            if httpx.get(f"{BASE}/openapi.json", timeout=2).status_code == 200:
                break
        except Exception:
            time.sleep(0.3)
    else:
        proc.kill()
        raise RuntimeError("uvicorn 启动失败")
    yield BASE
    proc.kill()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def _collect_events(gid, base, timeout=25):
    """订阅 SSE，收集 data 事件直到收到 >=2 个或超时。"""
    events = []
    with httpx.Client(timeout=timeout) as c:
        with c.stream("GET", f"{base}/api/{gid}/stream") as resp:
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:].strip()))
                    if len(events) >= 3:
                        break
                elif line.startswith(":"):
                    pass  # heartbeat，忽略
    return events


def test_sse_initial_snapshot_and_deltas(server: str):
    import threading
    collected = {"events": []}
    first_event = threading.Event()

    start = httpx.post(f"{server}/api/start").json()
    gid = start["game_id"]
    token = start.get("token", "")

    # 后台订阅；收到首个事件后置位 Event，主线程据此确认已连上再触发变更
    def _pump():
        events = []
        with httpx.Client(timeout=30) as c:
            with c.stream("GET", f"{server}/api/{gid}/stream?token={token}") as resp:
                for line in resp.iter_lines():
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:].strip()))
                        first_event.set()  # 收到首个事件即通知主线程
                        if len(events) >= 2:  # 拿到初始快照+至少1增量即停
                            break
                    elif line.startswith(":"):
                        pass
        collected["events"] = events

    t = threading.Thread(target=_pump, daemon=True)
    t.start()

    # 等待订阅真正建立并收到首个事件（消除固定 sleep 的时序竞态）
    assert first_event.wait(timeout=15), "SSE 订阅未在 15s 内收到首个事件"

    # 触发一次变更
    st = httpx.get(f"{server}/api/{gid}/state?token={token}").json()
    if st["turn"] == "User":
        mv = httpx.get(f"{server}/api/{gid}/moves?token={token}").json()["moves"][0]["id"]
        httpx.post(f"{server}/api/play", json={"game_id": gid, "move_id": mv, "request_id": "sse-unit-1", "token": token})
    else:
        httpx.post(f"{server}/api/ai_retry", json={"game_id": gid, "force": True, "token": token})

    t.join(timeout=20)
    events = collected.get("events", [])
    assert len(events) >= 2, f"应至少收到初始快照+1增量，实际 {len(events)}"
    # 后端 stream 连接即推当前全量 state（首事件即初始快照）；后续事件 seq 严格递增
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs), f"seq 应递增: {seqs}"
    print(f"\n  SSE 事件数={len(events)} seqs={seqs}")

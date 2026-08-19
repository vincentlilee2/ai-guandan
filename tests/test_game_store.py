"""GameStore 单元测试（v2.2 抽象层）。

覆盖 MemoryGameStore 的接口契约与并发安全：
- add/get/remove/count 基本语义
- touch 更新 last_access
- cleanup_expired 按 TTL 淘汰
- 并发读写不丢数据（线程锁）
"""
import threading
import time

from backend.game_store import GameStore, MemoryGameStore


class _FakeGame:
    """极简替身，模拟 GuandanGame 的持有身份。"""
    def __init__(self, gid: str):
        self.game_id = gid


def test_add_get_remove_count():
    s = MemoryGameStore()
    assert s.count() == 0
    g = _FakeGame("g1")
    s.add("g1", g)
    assert s.count() == 1
    assert s.get("g1") is g
    assert s.get("nope") is None
    s.remove("g1")
    assert s.count() == 0
    assert s.get("g1") is None


def test_touch_updates_last_access():
    s = MemoryGameStore()
    s.add("g1", _FakeGame("g1"))
    # touch 把 last_access 刷新为当前时刻
    s.touch("g1")
    # 刚访问过的对局在 10 秒 TTL 窗口内不应被淘汰
    assert s.cleanup_expired(ttl_seconds=10) == 0
    assert s.count() == 1

    # 但若把它的 last_access 改到很久以前，则会被淘汰
    with s._lock:
        s._meta["g1"]["last_access"] = time.time() - 9999
    assert s.cleanup_expired(ttl_seconds=3600) == 1
    assert s.count() == 0


def test_cleanup_expired_by_ttl():
    s = MemoryGameStore()
    s.add("fresh", _FakeGame("fresh"))
    s.add("old", _FakeGame("old"))
    # 手动把 old 的 last_access 改到很久以前
    import backend.game_store as gs
    with s._lock:
        s._meta["old"]["last_access"] = time.time() - 9999
    removed = s.cleanup_expired(ttl_seconds=3600)
    assert removed == 1
    assert s.get("old") is None
    assert s.get("fresh") is not None
    assert s.count() == 1


def test_concurrent_access_no_loss():
    """多线程并发 add/remove/get 不应丢失或抛异常（验证线程锁有效）。"""
    s = MemoryGameStore()
    n = 200

    def writer(i):
        gid = f"g{i}"
        s.add(gid, _FakeGame(gid))
        # 读回确认存在
        assert s.get(gid) is not None
        s.touch(gid)
        s.get(gid)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert s.count() == n
    # 再并发删一半
    def remover(i):
        if i % 2 == 0:
            s.remove(f"g{i}")
    threads2 = [threading.Thread(target=remover, args=(i,)) for i in range(n)]
    for t in threads2:
        t.start()
    for t in threads2:
        t.join()
    assert s.count() == n // 2


def test_get_store_singleton_and_backend_env(monkeypatch):
    """get_store 返回单例，且环境变量切换后端（默认 memory）。"""
    import backend.game_store as gs
    monkeypatch.setenv("GAME_STORE_BACKEND", "memory")
    gs._STORE = None  # 重置单例以便测试
    a = gs.get_store()
    b = gs.get_store()
    assert a is b
    assert isinstance(a, MemoryGameStore)

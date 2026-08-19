"""
GameStore — 对局状态的持久化抽象（v2.2 并发改造）

为什么需要它：
    原 main.py 直接用模块级全局字典 `games = {}` / `game_meta = {}` 存放对局。
    这带来 P0-2 两个问题：
      1. 进程重启即丢失所有对局
      2. 无法横向扩展——开多个 worker 时请求落到不同进程就找不到对局

本模块定义统一的 GameStore 接口，并提供默认的内存实现。
未来要做多进程/多机部署时，只需新增一个 RedisGameStore 实现该接口，
main.py 无需改动（通过 `GAME_STORE_BACKEND` 环境变量切换）。

注意：GuandanGame 是带可变状态的对象，序列化成本较高。内存实现直接持有
对象引用（零序列化）；Redis 实现需自行处理序列化（预留接口方法 save/load）。
"""
import abc
import threading
import time
from typing import Dict, Optional

from backend.logger import get_logger

log = get_logger(__name__)


class GameStore(abc.ABC):
    """对局状态存储接口。

    所有访问都必须通过本接口，禁止在业务代码里直接持有全局 dict。
    """

    @abc.abstractmethod
    def add(self, game_id: str, game) -> None:
        """注册一个新对局。"""

    @abc.abstractmethod
    def get(self, game_id: str) -> Optional[object]:
        """按 id 取对局；不存在返回 None。"""


    @abc.abstractmethod
    def get_meta(self, game_id: str) -> dict:
        """取对局元数据（如 owner_token/created_at/last_access）；不存在返回空 dict。"""

    @abc.abstractmethod
    def set_meta(self, game_id: str, meta: dict) -> None:
        """合并写对局元数据（保留原有字段，更新传入字段）。"""

    @abc.abstractmethod
    def remove(self, game_id: str) -> None:
        """移除对局及其元数据。"""

    @abc.abstractmethod
    def count(self) -> int:
        """当前活跃对局数（用于并发上限判断）。"""

    @abc.abstractmethod
    def touch(self, game_id: str) -> None:
        """更新 last_access 时间戳（心跳）。"""

    @abc.abstractmethod
    def cleanup_expired(self, ttl_seconds: float) -> int:
        """清理超过 ttl 的过期对局，返回移除数量。"""


class MemoryGameStore(GameStore):
    """默认实现：进程内存字典。

    用 threading.Lock 保护 dict 的并发读写——
    原代码用裸 dict，在 async + create_task 续跑 AI 的并发场景下存在竞态
    隐患（dict 增删非原子，且 _cleanup_expired_games 遍历时可能被其他协程修改）。
    _lock 在每次变更时持有，保证安全。
    """

    def __init__(self):
        self._games: Dict[str, object] = {}
        self._meta: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def add(self, game_id: str, game) -> None:
        with self._lock:
            self._games[game_id] = game
            meta = self._meta.setdefault(
                game_id, {"created_at": time.time(), "last_access": time.time()}
            )
            meta["last_access"] = time.time()

    def get(self, game_id: str):
        with self._lock:
            return self._games.get(game_id)

    def get_meta(self, game_id: str) -> dict:
        with self._lock:
            meta = self._meta.get(game_id)
            return dict(meta) if meta else {}

    def set_meta(self, game_id: str, meta: dict) -> None:
        with self._lock:
            base = self._meta.setdefault(
                game_id, {"created_at": time.time(), "last_access": time.time()}
            )
            base.update(meta)
            base["last_access"] = time.time()

    def remove(self, game_id: str) -> None:
        with self._lock:
            self._games.pop(game_id, None)
            self._meta.pop(game_id, None)

    def count(self) -> int:
        with self._lock:
            return len(self._games)

    def touch(self, game_id: str) -> None:
        with self._lock:
            meta = self._meta.get(game_id)
            if meta is not None:
                meta["last_access"] = time.time()
            else:
                self._meta[game_id] = {"created_at": time.time(), "last_access": time.time()}

    def cleanup_expired(self, ttl_seconds: float) -> int:
        now = time.time()
        expired = []
        with self._lock:
            for gid, meta in self._meta.items():
                last_access = float(meta.get("last_access", 0))
                if now - last_access > ttl_seconds:
                    expired.append(gid)
            for gid in expired:
                self._games.pop(gid, None)
                self._meta.pop(gid, None)
        return len(expired)


# ---------------------------------------------------------------------------
# Store 单例：通过环境变量选择后端（预留 Redis 扩展点）
# ---------------------------------------------------------------------------
_STORE: Optional[GameStore] = None
_STORE_INIT_LOCK = threading.Lock()


def get_store() -> GameStore:
    """返回全局 GameStore 单例。

    当前仅支持 memory。未来新增 redis 实现后，在此处按
    GAME_STORE_BACKEND 环境变量返回对应实例即可（main.py 无需改动）。
    """
    global _STORE
    if _STORE is None:
        with _STORE_INIT_LOCK:
            if _STORE is None:
                backend_name = __import__("os").getenv("GAME_STORE_BACKEND", "memory").strip().lower()
                if backend_name == "memory":
                    _STORE = MemoryGameStore()
                elif backend_name == "redis":
                    try:
                        from backend.redis_game_store import RedisGameStore
                        _STORE = RedisGameStore()
                    except Exception as e:
                        log.error(f"[GameStore] Redis 后端初始化失败，回退到 memory: {e}")
                        _STORE = MemoryGameStore()
                else:
                    log.warning(f"[GameStore] 未知后端 '{backend_name}'，回退到 memory")
                    _STORE = MemoryGameStore()
                log.info(f"[GameStore] 已初始化后端: {backend_name}")
    return _STORE

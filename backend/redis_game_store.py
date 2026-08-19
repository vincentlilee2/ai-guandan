"""
RedisGameStore — GameStore 的 Redis 实现（v2.5 持久化）。

为什么需要它：
    MemoryGameStore 把对局对象放在进程内，systemd Restart=always 下任何重启
    都会丢失所有对局。切换到 Redis 后端后，对局状态可跨进程重启恢复。

实现约定：
    - 通过 GAME_STORE_BACKEND=redis 启用（见 game_store.get_store()）。
    - 仍为单 worker 部署：事件总线（game_events.py）是进程内的，Redis 只负责
      状态持久化；多 worker 横向扩展需另做 SSE 事件 pub/sub，不在本模块范围。
    - 序列化走 GuandanGame.to_dict()/from_dict()（不含运行时锁等）。
    - 依赖 redis-py；未安装时惰性报错回退 memory（仿 pymysql 可选依赖模式）。
"""
import json
import os
import time
from typing import Optional

from backend.logger import get_logger

log = get_logger(__name__)

_GAME_KEY = "guandan:game:{gid}"
_META_KEY = "guandan:meta:{gid}"


class RedisGameStore:
    """对局状态 Redis 存储。不继承 GameStore(abc)，避免 import 阶段强依赖 redis-py；
    实现同名字面接口，由 get_store() 按后端名实例化。
    """

    def __init__(self):
        self._redis = self._connect()

    def _connect(self):
        try:
            import redis  # 惰性导入：未安装时走回退
        except Exception:
            log.error("[GameStore] 未安装 redis-py，回退到内存后端。请 pip install redis")
            raise
        url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0").strip()
        client = redis.from_url(url, decode_responses=True)
        client.ping()  # 连接不通立刻暴露，避免运行期静默失败
        log.info(f"[GameStore] Redis 后端已连接: {url}")
        return client

    # ---- 对局本体 ----
    def add(self, game_id: str, game) -> None:
        data = game.to_dict()
        self._redis.set(_GAME_KEY.format(gid=game_id), json.dumps(data, ensure_ascii=False))
        meta = self.get_meta(game_id)
        meta.setdefault("created_at", time.time())
        meta["last_access"] = time.time()
        self.set_meta(game_id, meta)

    def get(self, game_id: str) -> Optional[object]:
        raw = self._redis.get(_GAME_KEY.format(gid=game_id))
        if not raw:
            return None
        try:
            from backend.game_engine import GuandanGame
            data = json.loads(raw)
            return GuandanGame.from_dict(data)
        except Exception as e:
            log.error(f"[GameStore] 反序列化对局 {game_id} 失败: {e}")
            return None

    def remove(self, game_id: str) -> None:
        self._redis.delete(_GAME_KEY.format(gid=game_id), _META_KEY.format(gid=game_id))

    def count(self) -> int:
        try:
            return int(self._redis.dbsize())
        except Exception:
            # 退化为扫描 game key（兼容非纯 key 共用实例）
            return len(self._redis.keys("guandan:game:*"))

    def touch(self, game_id: str) -> None:
        meta = self.get_meta(game_id)
        meta["last_access"] = time.time()
        self.set_meta(game_id, meta)

    # ---- 元数据（owner_token 等） ----
    def get_meta(self, game_id: str) -> dict:
        raw = self._redis.get(_META_KEY.format(gid=game_id))
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def set_meta(self, game_id: str, meta: dict) -> None:
        cur = self.get_meta(game_id)
        cur.update(meta)
        self._redis.set(_META_KEY.format(gid=game_id), json.dumps(cur, ensure_ascii=False))

    def cleanup_expired(self, ttl_seconds: float) -> int:
        now = time.time()
        removed = 0
        for key in self._redis.scan_iter("guandan:meta:*", count=200):
            try:
                meta = json.loads(self._redis.get(key))
                last_access = float(meta.get("last_access") or 0)
                if now - last_access > ttl_seconds:
                    gid = key.split(":", 2)[2]
                    self.remove(gid)
                    removed += 1
            except Exception:
                continue
        return removed

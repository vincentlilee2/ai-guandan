"""
游戏事件总线（v2.4 SSE 推送）。

为什么需要它：
    原前端靠递归 setTimeout 轮询 /api/{id}/state，AI 思考 90 秒时前端空转、
    且即便 seq 没变也要反复拉完整状态（带宽/CPU 浪费）。
    v2.4 引入 SSE：后端在对局状态变化时主动 push 给订阅的浏览器。

设计：
    - 每 game_id 一个 asyncio.Condition；publish 唤醒所有等待者
    - publish 用 call_soon_threadsafe 调度，兼容同步/异步调用上下文
      （user_play/ai_retry 是 async 路由；trigger_ai_turn_async 由 create_task
       在事件循环内跑；ai_client 内部是纯本地逻辑不直接 publish）
    - SSE 路由在「初始快照 + 每次状态变更」时推完整 state；空转变心跳保活

注意：事件总线是进程内的，与 GameStore 同生命周期（重启即丢订阅，前端
自然降级回轮询）。多 worker 场景需换成 Redis pub/sub —— 与 2.2 同理留作后续。
"""
import asyncio
import threading


class GameEventBus:
    """按 game_id 广播状态变更通知。"""

    def __init__(self) -> None:
        self._conditions: dict[str, asyncio.Condition] = {}
        self._lock = threading.Lock()

    def _get_cond(self, game_id: str) -> asyncio.Condition:
        with self._lock:
            cond = self._conditions.get(game_id)
            if cond is None:
                # Condition 必须在事件循环里创建；首个调用方在 loop 中
                cond = asyncio.Condition()
                self._conditions[game_id] = cond
            return cond

    def publish(self, game_id: str) -> None:
        """通知订阅者：game_id 状态已变更。线程/上下文安全。"""
        try:
            cond = self._get_cond(game_id)
        except Exception:
            return
        try:
            loop = asyncio.get_event_loop()
            # 必须在持有条件变量锁的协程里 notify，故用 run_coroutine_threadsafe
            # 调度一个 async with cond 的协程，跨线程也安全
            asyncio.run_coroutine_threadsafe(self._notify(cond), loop)
        except RuntimeError:
            # 没在运行的 loop（如脚本上下文），忽略
            pass

    @staticmethod
    async def _notify(cond: asyncio.Condition) -> None:
        async with cond:
            cond.notify_all()

    async def wait_for_update(self, game_id: str) -> None:
        """等待该 game_id 的下一次状态变更。"""
        cond = self._get_cond(game_id)
        async with cond:
            await cond.wait()

    def drop(self, game_id: str) -> None:
        """对局结束后清理条件变量，避免内存泄漏。"""
        with self._lock:
            self._conditions.pop(game_id, None)


# 全局单例
game_event_bus = GameEventBus()

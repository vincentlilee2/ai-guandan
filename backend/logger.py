"""统一日志配置。

用法：
    from backend.logger import get_logger
    log = get_logger(__name__)
    log.info("...")

日志级别由环境变量控制：
    GUANDAN_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR   （默认 INFO）
    GUANDAN_DEBUG_AI=1                            （等价于把级别降到 DEBUG）
"""
import logging
import os
import sys

_CONFIGURED = False


def _resolve_level() -> int:
    if os.environ.get("GUANDAN_DEBUG_AI", "").strip() in ("1", "true", "True", "yes", "YES"):
        return logging.DEBUG
    raw = os.environ.get("GUANDAN_LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, raw, logging.INFO)


def _configure_once() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger("guandan")
    root.setLevel(_resolve_level())
    # 避免重复添加 handler（uvicorn --reload 会重复导入模块）
    if not root.handlers:
        root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """返回 guandan 命名空间下的 logger。"""
    _configure_once()
    short = name.split(".")[-1]
    return logging.getLogger(f"guandan.{short}")

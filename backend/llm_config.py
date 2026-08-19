import os
import re
import httpx
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
from backend.logger import get_logger

log = get_logger(__name__)

load_dotenv()

# LLM 默认配置：优先 *_LLM_* 新命名；旧 *_GEMINI_* 仍作回退（向后兼容）
# 注意配置名虽叫 GEMINI，实际值可为任意 OpenAI 兼容端点（如 DeepSeek）
_LLM_DEFAULTS = {
    "api_key": os.getenv("LLM_API_KEY", os.getenv("GEMINI_API_KEY")),
    "base_url": os.getenv(
        "LLM_BASE_URL",
        os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    ),
    "model": os.getenv("LLM_MODEL_NAME", os.getenv("GEMINI_MODEL_NAME", "gemini-3-flash-preview")),
    "temperature": float(os.getenv("LLM_TEMPERATURE", os.getenv("GEMINI_TEMPERATURE", "0.1"))),
}


def _bot_env(prefix: str, defaults: dict) -> dict:
    """读取某 Bot 的配置：新前缀 *_LLM_* 优先，旧 *_GEMINI_* 兼容回退。"""
    return {
        "api_key": os.getenv(f"{prefix}_LLM_API_KEY", os.getenv(f"{prefix}_GEMINI_API_KEY")),
        "base_url": os.getenv(f"{prefix}_LLM_BASE_URL", os.getenv(f"{prefix}_GEMINI_BASE_URL", defaults["base_url"])),
        "model": os.getenv(f"{prefix}_LLM_MODEL", os.getenv(f"{prefix}_GEMINI_MODEL", defaults["model"])),
        "temperature": float(os.getenv(f"{prefix}_LLM_TEMPERATURE", os.getenv(f"{prefix}_GEMINI_TEMPERATURE", str(defaults["temperature"])))),
    }


def _coach_env(defaults: dict) -> dict:
    """COACH（复盘教练）配置：新 COACH_LLM_* 优先，旧 COACH_GEMINI_* / COACH_OPENAI_* 兼容回退。"""
    return {
        "api_key": os.getenv("COACH_LLM_API_KEY", os.getenv("COACH_GEMINI_API_KEY", os.getenv("COACH_OPENAI_API_KEY"))),
        "base_url": os.getenv("COACH_LLM_BASE_URL", os.getenv("COACH_GEMINI_BASE_URL", os.getenv("COACH_OPENAI_BASE_URL", os.getenv("OPENAI_BASE_URL", defaults["base_url"])))),
        "model": os.getenv("COACH_LLM_MODEL", os.getenv("COACH_GEMINI_MODEL", os.getenv("COACH_MODEL_NAME", defaults["model"]))),
        "temperature": float(os.getenv("COACH_LLM_TEMPERATURE", os.getenv("COACH_GEMINI_TEMPERATURE", os.getenv("COACH_TEMPERATURE", "0.2")))),
    }


class LLMConfigManager:
    """
    LLM 配置管理器
    负责管理不同 AI 玩家的大模型配置 (API Key, Base URL, Model Name 等)
    """
    
    _clients = {}
    
    # 默认配置 (从环境变量读取)
    DEFAULT_CONFIG = {
        "api_key": os.getenv("OPENAI_API_KEY"),
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "model": os.getenv("MODEL_NAME", "gpt-5-nano"),
        # "temperature": 0.1,
    }

    # 玩家特定配置：新前缀 *_LLM_* 优先，旧 *_GEMINI_* 作为兼容回退
    # COACH（复盘教练）也走同机制：读 COACH_LLM_*，回退 COACH_GEMINI_* → _LLM_DEFAULTS
    PLAYER_CONFIGS = {
        "PartnerBot": _bot_env("PARTNERBOT", _LLM_DEFAULTS),
        "LeftBot": _bot_env("LEFTBOT", _LLM_DEFAULTS),
        "RightBot": _bot_env("RIGHTBOT", _LLM_DEFAULTS),
        "COACH": _coach_env(_LLM_DEFAULTS),
    }

    @classmethod
    def get_config(cls, role: str) -> dict:
        """获取指定角色的完整配置"""
        config = cls.DEFAULT_CONFIG.copy()
        
        if role in cls.PLAYER_CONFIGS:
            override = cls.PLAYER_CONFIGS[role]
            for k, v in override.items():
                # 允许覆盖为 None (例如显式禁用 temperature)
                config[k] = v
        return config

    @classmethod
    def get_client(cls, role: str):
        """
        获取指定角色的 OpenAI Client 实例 (缓存)
        """
        if role not in cls._clients:
            config = cls.get_config(role)
            api_key = config.get("api_key")
            base_url = config.get("base_url")
            
            # 简单的有效性检查
            if api_key:
                try:
                    client = OpenAI(
                        api_key=api_key,
                        base_url=base_url
                    )
                    cls._clients[role] = client
                except Exception as e:
                    log.error(f"[WARN] [LLMConfig] 初始化 {role} 的 Client 失败: {e}")
                    cls._clients[role] = None
            else:
                # print(f"⚠️ [LLMConfig] {role} 未配置 API Key")
                cls._clients[role] = None
                
        return cls._clients.get(role)

    @classmethod
    def get_model_name(cls, role: str) -> str:
        """获取指定角色使用的模型名称"""
        return cls.get_config(role).get("model", "gpt-4o-mini")

    @classmethod
    def get_temperature(cls, role: str) -> float:
        """
        获取指定角色的 Temperature
        如果模型是 o1 系列 (不支持 temperature 参数)，则返回 None
        """
        config = cls.get_config(role)
        model = config.get("model", "").lower()
        
        # o1 系列模型不支持 temperature 参数 (不区分大小写)
        if model.startswith("o3"):
            return None
            
        return config.get("temperature", 0.1)

    @classmethod
    def disable_temperature(cls, role: str):
        """
        运行时禁用指定角色的 temperature 参数
        用于自动修复不支持 temperature 的模型报错
        """
        if role not in cls.PLAYER_CONFIGS:
            cls.PLAYER_CONFIGS[role] = {}
        
        # 显式设置为 None，get_config 会覆盖默认值
        cls.PLAYER_CONFIGS[role]["temperature"] = None
        log.info(f"ℹ️ [LLMConfig] 已为 {role} 永久禁用 temperature 参数")

    # ------------------------------------------------------------------
    # Async 客户端（v2.1 并发改造用）
    # 与上面的同步 client 并存：同步版保留给脚本/测试/降级路径使用，
    # 异步版供 FastAPI async 路由 await 调用，避免阻塞 anyio 线程池。
    # -----------------------------------------------------------------
    _async_clients = {}

    @classmethod
    def get_async_client(cls, role: str):
        """获取指定角色的 AsyncOpenAI 实例（缓存）。"""
        if role not in cls._async_clients:
            config = cls.get_config(role)
            api_key = config.get("api_key")
            base_url = config.get("base_url")
            if api_key:
                try:
                    from openai import AsyncOpenAI
                    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
                    cls._async_clients[role] = client
                except Exception as e:
                    log.error(f"[WARN] [LLMConfig] 初始化 {role} 的 AsyncClient 失败: {e}")
                    cls._async_clients[role] = None
            else:
                cls._async_clients[role] = None
        return cls._async_clients.get(role)



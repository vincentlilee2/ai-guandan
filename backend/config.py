# 功能开关配置页：集中管理项目可开关的功能。
# 约定：纯布尔常量，默认关闭；改后重启后端生效。
# 同一开关支持环境变量覆盖（'1'/'true'/'yes'/'on' → True），
# 用于容器化按需开启与测试注入（子进程 uvicorn 需走 env，monkeypatch 打不到子进程）。

import os


def _env_flag(name: str, default: bool) -> bool:
    """环境变量覆盖布尔开关：'1'/'true'/'yes'/'on' → True，其余 False；未设置用 default。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# 会员注册/登录整体开关：关闭时隐藏登录入口、auth 接口返回 403。
# 默认关闭会员登录（开源单机版）。如要连官网，在 .env 设 ENABLE_MEMBER_LOGIN=1 并配 MEMBER_SERVER_URL。
ENABLE_MEMBER_LOGIN = _env_flag("ENABLE_MEMBER_LOGIN", False)

# 复盘 AI 教练开关：关闭时复盘界面不显示教练入口、coach 接口返回 403
ENABLE_AI_COACH = _env_flag("ENABLE_AI_COACH", False)

# 会员服务器地址。默认留空 = 本地单机模式（账号存本地 userdata/，离线可跑）。
# 填官网 https://guandan.mgarden.org.cn 即开通官网会员（注册/登录/积分/胜率同步）。
# 留空 = 本地模式：账号数据存在本地 userdata/（现有 UserStore 兜底），离线可跑。
MEMBER_SERVER_URL = os.getenv("MEMBER_SERVER_URL", "").strip()

# 本实例是否为创作人官方网站（账号权威）。
# 官方实例必须设 =1：此时强制走「本地账号模式」，绝不把会员请求转发出去，
# 从根本上避免「远程转发绕一圈打回自己」的递归（服务器实测 10s 超时 + 限流耗尽）。
# 官方实例 .env 配：IS_OFFICIAL_SERVER=1（并建议同时把 MEMBER_SERVER_URL 留空）。
# 非官方（远程转发）实例不设，保持默认。
IS_OFFICIAL_SERVER = _env_flag("IS_OFFICIAL_SERVER", False)

# 今后更多设置加在这里（同一约定：纯布尔 + 可选环境变量覆盖）

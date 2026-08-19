"""
MemberClient — 会员系统远程客户端（httpx，异步转发官网）。

当 backend.config.MEMBER_SERVER_URL 非空（远程模式）时，本地的注册/登录/退出
转发到官网同一套 /api/auth/* 接口，会员局数/得分上报到 /api/member/*。
官网 = 唯一账号权威：账号数据只存官网，本地不解析 token，只持有官网签发的登录态。

错误约定：官网业务错误（非 2xx）映射为 UserStoreError(code, message)，
与本地 UserStore 抛错一致，路由层的 HTTPException 映射无需改动。
网络异常 → UserStoreError(503, "会员服务器连接失败，请稍后重试")。
"""
import asyncio
import json

import httpx

import backend.config
from backend.logger import get_logger
from backend.user_store import UserStoreError

log = get_logger(__name__)

_CONNECT_TIMEOUT = 3.0  # 官网不可达时快速失败（实测 10s 会让在线用户每请求卡 10 秒）


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=backend.config.MEMBER_SERVER_URL,
        timeout=_CONNECT_TIMEOUT,
    )


def _map_error(resp: httpx.Response) -> UserStoreError:
    """官网返回的业务错误 → UserStoreError（透传 detail 文案与 HTTP 状态码）。"""
    message = None
    try:
        body = resp.json()
        if isinstance(body, dict):
            message = body.get("detail") or body.get("message")
    except (ValueError, TypeError):
        body = None
    if not message:
        message = f"会员服务器返回错误（HTTP {resp.status_code}）"
    return UserStoreError(resp.status_code, str(message))


def _to_user_store_error(exc: Exception) -> UserStoreError:
    if isinstance(exc, httpx.TimeoutException):
        return UserStoreError(503, "会员服务器连接超时，请稍后重试")
    if isinstance(exc, httpx.HTTPError):
        return UserStoreError(503, "会员服务器连接失败，请稍后重试")
    return UserStoreError(503, "会员服务器连接失败，请稍后重试")


def _run(coro):
    """auth 路由在 run_in_executor 中调用同步方法；这里在 executor 内跑完整协程。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _aio_request(method: str, path: str, *, token: str | None = None, json_body: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with _client() as client:
            resp = await client.request(method, path, headers=headers, json=json_body)
    except httpx.HTTPError as exc:
        log.warning(f"[MemberClient] {method} {path} 网络异常: {exc.__class__.__name__}")
        raise _to_user_store_error(exc) from exc
    if resp.status_code >= 400:
        log.warning(f"[MemberClient] {method} {path} 官网返回 {resp.status_code}: {resp.text[:200]}")
        raise _map_error(resp)
    try:
        return resp.json()
    except (ValueError, TypeError):
        return {}


# ------------------------------------------------------------------ 公开接口（同步签名，供 run_in_executor 调用）


def register(nickname: str, email: str, password: str) -> dict:
    return _run(_aio_request("POST", "/api/auth/register", json_body={"nickname": nickname, "email": email, "password": password}))


def login(email: str, password: str) -> dict:
    return _run(_aio_request("POST", "/api/auth/login", json_body={"email": email, "password": password}))


def me(token: str) -> dict:
    return _run(_aio_request("GET", "/api/auth/me", token=token))


def logout(token: str) -> dict:
    return _run(_aio_request("POST", "/api/auth/logout", token=token))


def record_play(token: str) -> dict:
    return _run(_aio_request("POST", "/api/member/play-record", token=token))


def sync_scores(token: str, local_scores: dict) -> dict:
    return _run(_aio_request("POST", "/api/member/scores", token=token, json_body={"local_scores": local_scores}))

# main.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import json
import threading
from pathlib import Path
from backend.game_engine import GuandanGame
from backend.ai_client import LAST_AI_CONTEXTS, get_ai_context
from backend.coach import build_coach_review
from backend.rules import PatternRecognizer, Comparator
from backend.score_store import score_store, normalize_scores
from backend.user_store import user_store, UserStoreError
import backend.config
import backend.member_client as member_client
import logging
import time
import os
import secrets
import asyncio
from backend.logger import get_logger
from backend.game_events import game_event_bus

log = get_logger(__name__)

app = FastAPI()
cleanup_task = None

# 挂载前端构建产物 (如果存在)
# 假设前端 build 输出在 ui/dist
UI_DIST_DIR = Path(__file__).parent / "ui" / "dist"
if UI_DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=UI_DIST_DIR / "assets"), name="assets")
    app.mount("/sounds", StaticFiles(directory=UI_DIST_DIR / "sounds"), name="sounds")

    if (UI_DIST_DIR / "avatars").exists():
        app.mount("/avatars", StaticFiles(directory=UI_DIST_DIR / "avatars"), name="avatars")

    # 根路由返回 index.html
    @app.get("/")
    async def read_index():
        return FileResponse(UI_DIST_DIR / "index.html")

# 自定义日志过滤器：每10秒只打印一次 /state 接口的访问日志
class StateLogFilter(logging.Filter):
    def __init__(self, interval=20):
        super().__init__()
        self.last_log_time = 0
        self.interval = interval

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        # 识别 /state 接口的 GET 请求日志（增量轮询 URL 形如 /state?last_seq=N，不含裸 "/state HTTP"）
        if "GET /api/" in msg and "/state" in msg and " HTTP" in msg:
            now = time.time()
            if now - self.last_log_time > self.interval:
                self.last_log_time = now
                return True
            return False
        return True

@app.on_event("startup")
async def setup_logging():
    # 将过滤器添加到 uvicorn.access 日志记录器
    logging.getLogger("uvicorn.access").addFilter(StateLogFilter(interval=10))

    # 启动后台清理循环，低流量时也能稳定回收过期会话
    global cleanup_task
    if cleanup_task is None or cleanup_task.done():
        cleanup_task = asyncio.create_task(_periodic_cleanup_loop())


@app.on_event("shutdown")
async def shutdown_cleanup_task():
    global cleanup_task
    if cleanup_task is not None:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        cleanup_task = None

# 对局状态统一走 GameStore（v2.2 抽象）：默认内存实现，预留 Redis 扩展点。
# 不再使用模块级全局 dict，避免重启丢失 + 无法多 worker。
from backend.game_store import get_store
store = get_store()

# 轻量级内存限流桶: {"endpoint:ip": [timestamp, ...]}（限流非会话状态，独立保留）
rate_limit_buckets = {}
# v2.5：限流桶读改写加锁，避免并发请求交错导致计数丢失/越限
_rate_limit_lock = threading.Lock()

TRUSTED_PLAYER_NAMES = {"User", "RightBot", "PartnerBot", "LeftBot"}
DEFAULT_TRUSTED_PROXY_IPS = {"127.0.0.1", "::1", "localhost"}
trusted_proxy_ips_raw = os.getenv("GAME_TRUSTED_PROXY_IPS", "")
TRUSTED_PROXY_IPS = {
    ip.strip() for ip in trusted_proxy_ips_raw.split(",") if ip.strip()
} or DEFAULT_TRUSTED_PROXY_IPS
TRUSTED_PROXY_TOKEN = os.getenv("GAME_TRUSTED_PROXY_TOKEN", "").strip()
GAME_SESSION_TTL_SECONDS = int(os.getenv("GAME_SESSION_TTL_SECONDS", "7200"))
# 并发上限：每局 AI 回合会在 anyio 线程池中同步阻塞等待 LLM 返回
# （单次可达数十秒），线程池默认上限 40，因此默认值保守设为 20。
# 详见 README「并发与性能限制」。放大前请先改造为 async LLM 调用。
GAME_MAX_ACTIVE_SESSIONS = int(os.getenv("GAME_MAX_ACTIVE_SESSIONS", "20"))
GAME_CLEANUP_INTERVAL_SECONDS = int(os.getenv("GAME_CLEANUP_INTERVAL_SECONDS", "300"))


BASE_DIR = Path(__file__).resolve().parent
HISTORY_DIR = BASE_DIR / "history"
LEGACY_HISTORY_DIR = BASE_DIR.parent / "history"
LATEST_REPLAY_FILE = BASE_DIR / "game_history.json"

class PlayRequest(BaseModel):
    game_id: str
    move_id: Optional[int] = None
    card_ids: Optional[List[str]] = None
    request_id: Optional[str] = None  # 客户端生成的幂等请求ID
    token: Optional[str] = None       # 对局访问 token（开局时下发）


class ScoreSyncRequest(BaseModel):
    anon_id: Optional[str] = None
    local_scores: Optional[dict] = None


def _client_ip(request: Request) -> str:
    if request and request.client and request.client.host:
        return request.client.host
    return "unknown"


def _is_trusted_proxy_request(request: Request) -> bool:
    ip = _client_ip(request)
    if ip in TRUSTED_PROXY_IPS:
        return True

    if TRUSTED_PROXY_TOKEN:
        header_token = request.headers.get("X-Game-Proxy-Token", "")
        if header_token and secrets.compare_digest(header_token, TRUSTED_PROXY_TOKEN):
            return True

    return False


def _require_trusted_proxy(request: Request):
    if not _is_trusted_proxy_request(request):
        raise HTTPException(403, "Forbidden")


def _enforce_rate_limit(request: Request, endpoint: str, limit: int, window_seconds: int):
    ip = _client_ip(request)
    key = f"{endpoint}:{ip}"
    now = time.time()
    with _rate_limit_lock:
        timestamps = [
            ts for ts in rate_limit_buckets.get(key, [])
            if now - ts < window_seconds
        ]
        if len(timestamps) >= limit:
            raise HTTPException(429, "Too many requests")
        timestamps.append(now)
        rate_limit_buckets[key] = timestamps


def _create_game_id() -> str:
    # 使用高熵随机 ID，避免短 ID 被枚举
    return secrets.token_urlsafe(16)


async def _verify_game_access(game_id: str, token) -> bool:
    """校验对局访问 token。

    旧局/无绑定（meta 无 owner_token）→ 兼容放行；有绑定 → 必须 match。
    token 从 query/body 传来，可能为 None、"" 或 list。
    """
    meta = await asyncio.to_thread(store.get_meta, game_id)
    stored = (meta or {}).get("owner_token")
    if not stored:
        return True
    if isinstance(token, list):
        token = token[0] if token else None
    return bool(token) and secrets.compare_digest(str(stored), str(token))


async def _require_game_access(game_id: str, token):
    if not await _verify_game_access(game_id, token):
        raise HTTPException(403, "无权访问该对局")


async def _cleanup_expired_games():
    return await asyncio.to_thread(store.cleanup_expired, GAME_SESSION_TTL_SECONDS)


async def _periodic_cleanup_loop():
    interval = max(30, GAME_CLEANUP_INTERVAL_SECONDS)
    while True:
        removed_count = await _cleanup_expired_games()
        if removed_count > 0:
            log.info(f"[Cleanup] removed {removed_count} expired game session(s)")
        await asyncio.sleep(interval)


async def _touch_game(game_id: str):
    await asyncio.to_thread(store.touch, game_id)


async def _get_game_or_404(game_id: str):
    game = await asyncio.to_thread(store.get, game_id)
    if not game:
        raise HTTPException(404, "Game not found")
    await _touch_game(game_id)
    return game


def _get_user_id_from_request(request: Request) -> Optional[int]:
    # [独立版] 已剥离外部网关的会员登录：始终返回 None，走"未登录/匿名"分支，
    # 积分由前端 localStorage + 后端内存 ScoreStore 承担，不连 MySQL。
    return None


def _remote_mode() -> bool:
    """会员远程模式：开通会员登录且配置了官网地址，且本实例不是官网。

    防回环三保险：
      1. 官方实例（IS_OFFICIAL_SERVER=1）强制本地账号模式，绝不转发；
      2. MEMBER_SERVER_URL 指向本机（自引用检测）视为官网，走本地模式；
      3. 否则才远程转发官网。
    """
    if not (backend.config.ENABLE_MEMBER_LOGIN and backend.config.MEMBER_SERVER_URL):
        return False
    if backend.config.IS_OFFICIAL_SERVER:
        return False
    if _member_url_points_to_self(backend.config.MEMBER_SERVER_URL):
        return False
    return True


def _member_url_points_to_self(url: str) -> bool:
    """兜底：MEMBER_SERVER_URL 的主机若解析到本机（回环/本机网卡 IP），视为指向自己。"""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        if host in ("localhost", "127.0.0.1", "::1"):
            return True
        import socket
        target_ips = {info[4][0] for info in socket.getaddrinfo(host, 443, socket.AF_INET)}
        local_ips = set()
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            local_ips.add(info[4][0])
        local_ips.add("127.0.0.1")
        return bool(target_ips & local_ips)
    except Exception:
        return False


def _get_user_id_from_request_legacy(request: Request) -> Optional[int]:
    if not _is_trusted_proxy_request(request):
        return None

    if request is None:
        return None
    header_val = request.headers.get("X-User-Id")
    if header_val:
        try:
            return int(header_val)
        except (TypeError, ValueError):
            return None
    return None


def _resolve_cards_from_ids(game: GuandanGame, player: str, card_ids: List[str]):
    hand = game.hands.get(player) or []
    by_id = {c.id: c for c in hand}
    missing = [cid for cid in card_ids if cid not in by_id]
    if missing:
        raise HTTPException(400, f"Selected cards not in hand: {', '.join(missing)}")
    return [by_id[cid] for cid in card_ids]


def _derive_exact_move_from_selection(selected_cards):
    target_set = set(c.id for c in selected_cards)
    candidate_moves = PatternRecognizer.get_legal_moves(selected_cards)
    
    # [Fix] 优先选择 Rank 更大的匹配项
    # 例如：77QQH2，可以是 (Trip 7 + Pair Q, Rank 7) 或者 (Trip Q + Pair 7, Rank 12)
    # 我们希望默认选择 Rank 12 的那个。
    
    matched_candidates = [
        m for m in candidate_moves
        if m.get('type') != 0
        and len(m.get('card_ids', [])) == len(target_set)
        and set(m.get('card_ids', [])) == target_set
    ]
    
    if not matched_candidates:
        return None
        
    # 按 Rank 降序排，取第一个
    # 注意：Rank 比较需要统一化，这里通常是 int
    def _rank_key(m):
        r = m.get('rank')
        if hasattr(r, 'value'): return int(r.value)
        try: return int(r)
        except: return 0
        
    matched_candidates.sort(key=_rank_key, reverse=True)
    return matched_candidates[0]

@app.post("/api/start")
@app.post("/api/start")
async def start_game(request: Request):
    _enforce_rate_limit(request, "start_game", limit=20, window_seconds=60)

    if await asyncio.to_thread(store.count) >= GAME_MAX_ACTIVE_SESSIONS:
        raise HTTPException(503, "Too many active games, please retry later")

    # 会员 token 走可选 body {token}；兼容空 body（旧客户端 / 游客开局直接 POST 无 body）
    req_token = None
    try:
        payload = await request.json()
        if isinstance(payload, dict):
            req_token = payload.get("token")
    except Exception:
        pass

    game_id = _create_game_id()
    game = GuandanGame(game_id)
    info = game.start_game()
    await asyncio.to_thread(store.add, game_id, game)

    # v2.5 权限隔离：为对局生成专属访问 token，绑定到 meta；
    # 前端持有后，后续所有 game_id 接口均需携带（state/stream/moves/play/ai_retry/replay）。
    token = secrets.token_urlsafe(24)
    meta = {"owner_token": token}

    # 会员绑定：开局时校验前端传来的会员 session token，
    # 有效则把 user_id 写入 meta，对局结束时按此记录当日局数。
    # 会员登录开关关闭时不解析 token（前端此时也不会发）。
    # 远程模式：官网 token 只存 meta 供结束时上报，本地不解析。
    if backend.config.ENABLE_MEMBER_LOGIN and req_token:
        if _remote_mode():
            meta["member_token"] = req_token
        else:
            uid = user_store.resolve_user_id(req_token)
            if uid is not None:
                meta["user_id"] = uid

    await asyncio.to_thread(store.set_meta, game_id, meta)
    await _touch_game(game_id)

    # 如果首发不是用户，触发 AI
    if info['turn'] != "User":
        asyncio.create_task(game.trigger_ai_turn_async())

    return {"game_id": game_id, "my_hand": info['hand'], "current_turn": info['turn'], "token": token}

@app.get("/api/{game_id}/state")
@app.get("/api/{game_id}/state")
async def get_state(game_id: str, request: Request):
    await _require_game_access(game_id, request.query_params.get("token"))
    game = await _get_game_or_404(game_id)
    user_id = _get_user_id_from_request(request)
    logged_in = user_id is not None

    # 增量查询：前端传 last_seq，如果 seq 没变则返回空体节省带宽
    # 但以下情况必须返回完整状态：
    #   1. 游戏已结束（前端需要结算界面）
    #   2. 轮到 AI 出牌（前端需要头像转圈动画等状态）
    last_seq_str = request.query_params.get("last_seq")
    if last_seq_str is not None:
        try:
            last_seq = int(last_seq_str)
            # 任何时刻 seq 没变都走短路，不再区分 turn（AI 思考时也避免无意义完整渲染）
            # 但游戏结束时必须返回完整状态（含 result），否则前端结算弹窗永不出现
            if game.seq == last_seq and game.state != "finished":
                return {"seq": game.seq, "state": game.state, "turn": game.players[game.current_turn_index], "unchanged": True}
        except (ValueError, TypeError):
            pass

    return await _build_state(game, request)


async def _build_state(game, request: Request) -> dict:
    """构造给前端的完整对局状态（SSE 与 get_state 共用）。"""
    user_id = _get_user_id_from_request(request)
    logged_in = user_id is not None

    # 如果游戏结束且未结算，更新总分 + 记录会员当日局数（二者同分支同步结算）
    if game.state == "finished" and not game.score_applied:
        if game.final_result:
            if logged_in:
                score_store.add_scores(user_id, game.final_result.get("scores", {}))
            game.score_applied = True

        # 会员局数：按开局时绑定到 meta 的 user_id 记（非请求解析），play_counted 去重。
        # 会员登录开关关闭时不记局数（无账号体系）。
        # 远程模式：把官网 token 上报官网记局数/得分；本地模式：本地记账。
        # 上报失败只记日志降级，不阻塞结算（本地永不限制实际玩牌局数）。
        if backend.config.ENABLE_MEMBER_LOGIN:
            meta = await asyncio.to_thread(store.get_meta, game.game_id)
            if not meta.get("play_counted"):
                if _remote_mode():
                    token = meta.get("member_token")
                    if token:
                        try:
                            await _auth_run(member_client.record_play, token)
                        except UserStoreError as e:
                            log.warning(f"会员局数上报失败: {e.message}")
                        try:
                            await _auth_run(member_client.sync_scores, token, game.final_result.get("scores", {}) if game.final_result else {})
                        except UserStoreError as e:
                            log.warning(f"会员得分上报失败: {e.message}")
                    meta["play_counted"] = True
                    await asyncio.to_thread(store.set_meta, game.game_id, meta)
                else:
                    uid = meta.get("user_id")
                    if uid is not None:
                        user_store.record_play(uid)
                        if game.final_result:
                            score_store.add_scores(uid, game.final_result.get("scores", {}))
                        meta["play_counted"] = True
                        await asyncio.to_thread(store.set_meta, game.game_id, meta)

    # 构造给前端看的信息
    # recent_history 只返回玩家相关记录，但需要一个单调递增的序号供前端去重/补齐渲染。
    player_history = [h for h in game.history if h.get("player")]
    player_history_with_id = [{"_hid": idx, **h} for idx, h in enumerate(player_history)]
    history_len = len(player_history_with_id)
    # 正常对局只返回一个较大的尾巴；游戏结束时返回全部玩家历史，避免“最后几手没来得及渲染就弹结算”。
    if game.state == "finished":
        recent_history = player_history_with_id
    else:
        recent_history = player_history_with_id[-64:]

    return {
        "state": game.state, # playing, finished
        "seq": game.seq,
        "turn": game.players[game.current_turn_index],
        "last_move": game.last_move['desc'] if game.last_move else "None",
        "last_player": game.last_move['player'] if game.last_move else "",
        "my_hand": [c.id for c in game.hands["User"]],
        "bot_cards_count": {
            "RightBot": len(game.hands["RightBot"]),
            "PartnerBot": len(game.hands["PartnerBot"]),
            "LeftBot": len(game.hands["LeftBot"])
        },
        "analysis_snapshot": game.analysis_snapshot,
        "result": game.final_result, # 如果结束了，返回结果
        "total_scores": score_store.get_scores(user_id) if logged_in else None,
        "logged_in": logged_in,
        "user_id": user_id if logged_in else None,
        "history_len": history_len,
        "recent_history": recent_history,
        "_ai_processing": getattr(game, "_ai_processing", False),  # 精确卡死判定：AI 循环是否仍在进行（True 且 seq 不涨 = 真卡死）
        "last_ai_fallback": getattr(game, "_last_ai_fallback", None),  # 最近一次 AI 超时本地兜底（前端弹提醒："AI 返回超时，本次出牌采用本地策略"）
    }


@app.get("/api/{game_id}/stream")
async def stream_game(game_id: str, request: Request):
    """SSE 流：开局即推初始快照，之后每次状态变更推完整 state；空转变心跳。"""
    await _require_game_access(game_id, request.query_params.get("token"))
    game = await _get_game_or_404(game_id)

    async def event_generator():
        state = await _build_state(game, request)
        yield _sse_event(state)
        last_seq = game.seq
        while True:
            if await request.is_disconnected():
                break
            try:
                await asyncio.wait_for(
                    game_event_bus.wait_for_update(game_id),
                    timeout=_HEARTBEAT_INTERVAL,
                )
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            state = await _build_state(game, request)
            if state.get("seq") == last_seq and state.get("state") != "finished":
                continue
            last_seq = state.get("seq")
            yield _sse_event(state)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


_HEARTBEAT_INTERVAL = 20.0  # 秒，保活心跳间隔


# ---------------------------------------------------------------------------
# 会员注册/登录（v3：JSON 文件 UserStore，无第三方依赖）
# 注意：PBKDF2 单次约 50ms，会阻塞事件循环——所有 auth 路由均为
# async def + run_in_executor，把密码哈希放到线程池执行。
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    nickname: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


def _bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        token = header[len("Bearer "):].strip()
        return token or None
    return None


def _auth_run(fn, *args):
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, lambda: fn(*args))


@app.get("/api/config")
async def get_config():
    """功能开关：前端挂载时拉取，决定是否显示登录/AI 教练入口。"""
    return {
        "member_login_enabled": bool(backend.config.ENABLE_MEMBER_LOGIN),
        "ai_coach_enabled": bool(backend.config.ENABLE_AI_COACH),
    }


def _require_feature(enabled: bool):
    if not enabled:
        raise HTTPException(403, "该功能未开启")


@app.post("/api/auth/register")
async def register(req: RegisterRequest, request: Request):
    _require_feature(backend.config.ENABLE_MEMBER_LOGIN)
    _enforce_rate_limit(request, "auth_register", limit=5, window_seconds=60)
    try:
        if _remote_mode():
            return await _auth_run(member_client.register, req.nickname, req.email, req.password)
        return await _auth_run(user_store.register, req.nickname, req.email, req.password)
    except UserStoreError as e:
        raise HTTPException(e.code, e.message)


@app.post("/api/auth/login")
async def login(req: LoginRequest, request: Request):
    _require_feature(backend.config.ENABLE_MEMBER_LOGIN)
    _enforce_rate_limit(request, "auth_login", limit=5, window_seconds=60)
    try:
        if _remote_mode():
            return await _auth_run(member_client.login, req.email, req.password)
        return await _auth_run(user_store.login, req.email, req.password)
    except UserStoreError as e:
        raise HTTPException(e.code, e.message)


@app.get("/api/auth/me")
async def me(request: Request):
    _require_feature(backend.config.ENABLE_MEMBER_LOGIN)
    token = _bearer_token(request)
    try:
        if _remote_mode():
            return await _auth_run(member_client.me, token)
        info = await _auth_run(user_store.me, token)
    except UserStoreError as e:
        raise HTTPException(e.code, e.message)
    # 本地模式：补 total_scores（远程模式由官网 me 直接返回，响应同构）
    uid = user_store.resolve_user_id(token)
    info["total_scores"] = score_store.get_scores(uid) if uid is not None else None
    return info


@app.post("/api/auth/logout")
async def logout(request: Request):
    _require_feature(backend.config.ENABLE_MEMBER_LOGIN)
    token = _bearer_token(request)
    try:
        if _remote_mode():
            await _auth_run(member_client.logout, token)
        else:
            await _auth_run(user_store.logout, token)
    except UserStoreError as e:
        raise HTTPException(e.code, e.message)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 会员上报接口（供远程实例调用；官网实例跑本地模式时对外暴露）：
#   远程实例对局结束时，用官网签发的 token 调本接口记局数/累计得分。
# ---------------------------------------------------------------------------
class MemberScoreSyncRequest(BaseModel):
    local_scores: Optional[dict] = None


def _member_token_or_401(request: Request) -> int:
    token = _bearer_token(request)
    uid = user_store.resolve_user_id(token)
    if uid is None:
        raise HTTPException(401, "登录已失效，请重新登录")
    return uid


@app.post("/api/member/play-record")
async def member_play_record(request: Request):
    """记会员当日 1 局，返回 plays_today（本地模式 = 官网账号权威）。"""
    _require_feature(backend.config.ENABLE_MEMBER_LOGIN)
    _enforce_rate_limit(request, "member_play_record", limit=60, window_seconds=60)
    uid = _member_token_or_401(request)
    plays_today = await _auth_run(user_store.record_play, uid)
    return {"plays_today": plays_today}


@app.post("/api/member/scores")
async def member_scores(request: Request, body: MemberScoreSyncRequest):
    """累计会员得分（delta 累加），返回 total_scores。"""
    _require_feature(backend.config.ENABLE_MEMBER_LOGIN)
    _enforce_rate_limit(request, "member_scores", limit=60, window_seconds=60)
    uid = _member_token_or_401(request)
    local_scores = normalize_scores(body.local_scores)
    total_scores = await _auth_run(score_store.add_scores, uid, local_scores)
    return {"total_scores": total_scores}


@app.get("/api/score")
@app.get("/api/score")
async def get_score_state(request: Request):
    _require_trusted_proxy(request)

    user_id = _get_user_id_from_request(request)
    if user_id is None:
        return {"logged_in": False}
    return {
        "logged_in": True,
        "user_id": user_id,
        "total_scores": score_store.get_scores(user_id),
    }

@app.post("/api/score/sync")
@app.post("/api/score/sync")
async def sync_score_state(request: Request, body: ScoreSyncRequest):
    _require_trusted_proxy(request)
    _enforce_rate_limit(request, "score_sync", limit=30, window_seconds=60)

    user_id = _get_user_id_from_request(request)
    if user_id is None:
        raise HTTPException(401, "Unauthorized")
    local_scores = normalize_scores(body.local_scores)
    total_scores = score_store.add_scores(user_id, local_scores)
    return {
        "logged_in": True,
        "user_id": user_id,
        "total_scores": total_scores,
    }

def _load_replay_data(game_id: str) -> dict:
    """读取复盘数据（history 文件），供 replay / coach 等只读端点共享。"""
    if game_id == "latest":
        return _read_replay_file_or_404(LATEST_REPLAY_FILE, game_id)
    candidates = []
    candidates.append(HISTORY_DIR / f"{game_id}.json")
    candidates.append(LEGACY_HISTORY_DIR / f"{game_id}.json")
    # 历史文件命名是 {ts}_{game_id}.json（见 game_engine.save_history），按 game_id 模糊匹配
    for base in (HISTORY_DIR, LEGACY_HISTORY_DIR):
        if base and base.is_dir():
            candidates.extend(sorted(base.glob(f"*_{game_id}.json"), reverse=True))
    candidates.append(LATEST_REPLAY_FILE)
    for file_path in candidates:
        data = _read_replay_file_or_404(file_path, game_id)
        if data is not None:
            return data
    raise HTTPException(404, "未找到对应的复盘数据")


def _read_replay_file_or_404(file_path, game_id: str) -> Optional[dict]:
    """读取单个复盘文件；不存在返回 None，损坏抛 500，game_id 不匹配返回 None。"""
    if not file_path or not file_path.exists():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        raise HTTPException(500, "复盘数据已损坏，请重新完成一局游戏后再试")
    if game_id != "latest" and data.get("game_id") != game_id:
        return None
    return data


# 每手 history 里体积最大的 LLM prompt 字段——复盘界面只渲染 player/action/cards/desc，
# 接口返回前剥离以减小传输负载；磁盘文件与 /coach 仍保留完整 prompt。
_PROMPT_FIELDS = ("system_prompt", "user_prompt", "ai_response")


def _slim_replay(data: dict) -> dict:
    """/replay 只读瘦身：剥掉每手 history 的 LLM prompt 字段。"""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    hist = out.get("history")
    if isinstance(hist, list):
        out["history"] = [
            {k: v for k, v in m.items() if k not in _PROMPT_FIELDS}
            if isinstance(m, dict) else m
            for m in hist
        ]
    return out


@app.get("/api/{game_id}/replay")
@app.get("/api/{game_id}/replay")
async def get_replay(game_id: str, request: Request):
    await _require_game_access(game_id, request.query_params.get("token"))
    _enforce_rate_limit(request, "replay", limit=30, window_seconds=60)
    return _slim_replay(_load_replay_data(game_id))

@app.get("/api/{game_id}/coach")
@app.get("/api/{game_id}/coach")
async def get_coach_advice(game_id: str, request: Request):
    """复盘 AI 教练：只分析 User 出牌（含 PASS），按 tactics_data 策略点评。"""
    _require_feature(backend.config.ENABLE_AI_COACH)
    await _require_game_access(game_id, request.query_params.get("token"))
    _enforce_rate_limit(request, "coach", limit=5, window_seconds=60)
    data = _load_replay_data(game_id)
    return await build_coach_review(data)

@app.get("/api/{game_id}/moves")
@app.get("/api/{game_id}/moves")
async def get_my_moves(game_id: str, request: Request):
    """获取用户当前可以出的牌"""
    await _require_game_access(game_id, request.query_params.get("token"))
    game = await _get_game_or_404(game_id)
    
    # 只有轮到用户才计算
    if game.players[game.current_turn_index] != "User":
        return {"moves": []}
        
    moves = game.get_legal_moves_for_current_player()
    # 过滤掉对象，只留基础数据给前端
    safe_moves = []
    for m in moves:
        safe_moves.append({
            "id": m['id'],
            "desc": m['desc'],
            "card_ids": m['card_ids'],
            "type": int(m['type']),
            "rank": int(m['rank'])
        })
    return {"moves": safe_moves}

@app.post("/api/play")
@app.post("/api/play")
async def user_play(req: PlayRequest):
    await _require_game_access(req.game_id, req.token)
    game = await _get_game_or_404(req.game_id)

    # 0. 幂等性检查：如果 request_id 已处理过，直接返回当前状态
    if req.request_id and game.is_duplicate_request(req.request_id):
        log.info(f"[Idempotent] 重复请求 {req.request_id}，返回当前状态 seq={game.seq}")
        return {
            "status": "duplicate",
            "seq": game.seq,
            "message": "Request already processed"
        }
    
    # 1. 用户出牌
    legal_moves = game.get_legal_moves_for_current_player()

    # If card_ids is provided, derive the exact move from the selected cards and execute it.
    # This avoids issues where the backend only generated a representative move for duplicates
    # (e.g. having 3x3 but only one "pair 3" combination in legal_moves).
    if req.card_ids:
        if len(req.card_ids) != len(set(req.card_ids)):
            raise HTTPException(400, "Duplicate card_ids in request")

        selected_cards = _resolve_cards_from_ids(game, "User", req.card_ids)
        requested_move = _derive_exact_move_from_selection(selected_cards)
        if not requested_move:
            return {"error": "不是合法的牌型组合"}

        # Validate beat rules
        if game.last_move is not None:
            if not Comparator.can_beat(game.last_move, requested_move):
                reason = Comparator.get_beat_error(game.last_move, requested_move) or "你的牌不够大或类型不符"
                return {"error": reason}

        # Execute using the exact selected cards
        result = game.execute_move("User", 0, [requested_move])
    else:
        if req.move_id is None:
            raise HTTPException(400, "move_id is required when card_ids is not provided")
        result = game.execute_move("User", req.move_id, legal_moves)
    
    if "error" in result:
        return result
        
    # 标记请求已处理（幂等保护）
    if req.request_id:
        game.mark_request_processed(req.request_id)
        
    # 2. 用户出完后，后台触发 AI 循环
    game_event_bus.publish(req.game_id)  # v2.4：用户出牌即状态变更
    asyncio.create_task(game.trigger_ai_turn_async())
    
    return {
        "user_result": result,
        "seq": game.seq,
        "current_state": {
            "turn": game.players[game.current_turn_index],
            "last_move": game.last_move['desc'] if game.last_move else "None"
        }
    }

@app.post("/api/ai_retry")
@app.post("/api/ai_retry")
async def ai_retry(req: dict, request: Request):
    _require_trusted_proxy(request)
    _enforce_rate_limit(request, "ai_retry", limit=20, window_seconds=60)

    game_id = req.get("game_id")
    force = req.get("force", False)
    await _require_game_access(game_id, req.get("token"))
    game = await _get_game_or_404(game_id)
    
    # 检查当前是不是轮到 Bot
    current_player = game.players[game.current_turn_index]
    if current_player == "User":
        return {"status": "ignored", "reason": "Wait for user move"}
    
    # 强制重试：如果 AI 已在处理中，直接跳过（避免并发竞争损坏回合状态）
    if force:
        if game._ai_processing:
            log.warning(f"[Retry] {current_player} AI 已在处理中，跳过重复触发")
            return {"status": "already_processing", "reason": f"{current_player} AI 正在处理，请稍候"}
        retry_key = f"_force_retry_{current_player}"
        retry_count = getattr(game, retry_key, 0)
        if retry_count >= 2:
            return {"status": "max_retries", "reason": f"{current_player} 已达到最大重试次数"}
        setattr(game, retry_key, retry_count + 1)
        log.warning(f"[Retry-Force] 强制重试 {current_player} (第{retry_count + 1}次)")
    else:
        log.info(f"[Retry] 手动触发 Bot {current_player} 的决策逻辑")
    
    # 触发后台 AI 逻辑
    game_event_bus.publish(game_id)  # v2.4：重试触发即通知
    asyncio.create_task(game.trigger_ai_turn_async())
    return {"status": "retry_triggered", "player": current_player}

@app.post("/api/report_error")
@app.post("/api/report_error")
async def report_error(req: dict, request: Request):
    _require_trusted_proxy(request)
    _enforce_rate_limit(request, "report_error", limit=10, window_seconds=60)

    player_name = req.get("player_name")
    if not player_name:
        raise HTTPException(400, "player_name is required")
    if player_name not in TRUSTED_PLAYER_NAMES:
        raise HTTPException(400, "invalid player_name")

    game_id = req.get("game_id") or ""
    if game_id:
        await _require_game_access(game_id, req.get("token"))

    # 优先级：前端传来当前手 prompt（复盘精确）> 按局隔离缓存 > 全局兜底
    context = None
    if req.get("user_prompt") or req.get("ai_response") or req.get("system_prompt"):
        context = {
            "system_prompt": req.get("system_prompt", ""),
            "user_prompt": req.get("user_prompt", ""),
            "ai_response": req.get("ai_response", ""),
        }
    else:
        # 按局隔离取（精确对上这一局，不跨局）；取不到回退全局兜底
        context = get_ai_context(game_id, player_name) if game_id else LAST_AI_CONTEXTS.get(player_name)
    if not context:
        return {"status": "no_context", "message": f"没有找到 {player_name} 本局的出牌记录"}
    
    # 建立目录 game/errorPlay/
    error_dir = BASE_DIR / "errorPlay" / player_name
    error_dir.mkdir(parents=True, exist_ok=True)
    
    # 文件名: 20240217_153045.txt（带 game_id 后缀区分不同局）
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    gid_tag = game_id[:8] if game_id else "nogame"
    filename = f"{timestamp}_{gid_tag}.txt"
    file_path = error_dir / filename
    
    try:
        content = f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        content += f"Player: {player_name}\n"
        content += f"GameID: {game_id}\n"
        content += "=" * 50 + "\n"
        content += "[System Prompt]:\n"
        content += str(context.get("system_prompt", "")) + "\n\n"
        content += "=" * 50 + "\n"
        content += "[User Prompt]:\n"
        content += str(context.get("user_prompt", "")) + "\n\n"
        content += "=" * 50 + "\n"
        content += "[AI Response]:\n"
        content += str(context.get("ai_response", "")) + "\n"
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        log.error(f"[ErrorReport] 已成功记录 {player_name} 的出牌错误到: {file_path}")
        return {"status": "success", "file": f"{player_name}/{filename}"}
    except Exception as e:
        log.error(f"[ErrorReport] 写入文件失败: {str(e)}")
        raise HTTPException(500, f"Failed to save error report: {str(e)}")

# 启动命令: uvicorn main:app --host 127.0.0.1 --port 8002 --reload

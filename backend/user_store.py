"""
UserStore — 会员注册/登录 + 每日玩牌局数的 JSON 文件持久化（无第三方依赖）。

为什么用 JSON 文件：
    单 worker uvicorn 部署，无数据库；本模块用 users.json / sessions.json / plays.json
    三个文件 + threading.Lock + 临时文件 rename 原子写。

数据目录由环境变量 GAME_USER_DATA_DIR 指定（默认项目根），便于测试隔离。

密码安全：hashlib.pbkdf2_hmac('sha256', pwd, salt, 100_000)，salt 随机 16 字节，
与密码同存，验证时重算比对（用 hmac.compare_digest 防时序攻击）。不存明文。

登录态：secrets.token_urlsafe(32) 作 session token，sessions.json 记录
{token: {user_id, created_at, last_seen}}。

注意：PBKDF2 单次约 50ms 会阻塞事件循环——auth 路由必须用
async def + run_in_executor 调用本模块，勿在协程里直接调 register/login。
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import date
from pathlib import Path

from backend.logger import get_logger

log = get_logger(__name__)

NICKNAME_RE = re.compile(r"^[^\s]{2,20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PBKDF2_ITERATIONS = 100_000
MEMBER_DAILY_LIMIT = 20

# 用户数据的三个文件
_USERS_FILE = "users.json"
_SESSIONS_FILE = "sessions.json"
_PLAYS_FILE = "plays.json"

# session 有效期（单位：秒），过期后需重新登录
_SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 天


class UserStoreError(Exception):
    """业务错误基类，code 供路由映射 HTTP 状态。"""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _data_dir() -> Path:
    base = os.getenv("GAME_USER_DATA_DIR", "").strip()
    if base:
        return Path(base)
    return Path(__file__).resolve().parent.parent


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS).hex()


def _read_json(file_path: Path) -> dict:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def _write_json_atomic(file_path: Path, data: dict) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_suffix(file_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, file_path)


class UserStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self._dir = Path(data_dir) if data_dir else _data_dir()
        self._lock = threading.RLock()
        self._users_path = self._dir / _USERS_FILE
        self._sessions_path = self._dir / _SESSIONS_FILE
        self._plays_path = self._dir / _PLAYS_FILE
        self._users: dict = {}
        self._sessions: dict = {}
        self._plays: dict = {}
        self._load()

    def _load(self) -> None:
        self._users = _read_json(self._users_path)
        self._sessions = _read_json(self._sessions_path)
        self._plays = _read_json(self._plays_path)
        if self._users or self._sessions or self._plays:
            log.info(f"[UserStore] 已加载 {len(self._users)} 用户 / {len(self._sessions)} 会话 @ {self._dir}")

    # ------------------------------------------------------------------ 注册/登录
    def register(self, nickname: str, email: str, password: str) -> dict:
        nickname = (nickname or "").strip()
        email = (email or "").strip().lower()
        password = password or ""

        if not NICKNAME_RE.match(nickname):
            raise UserStoreError(400, "昵称需为 2-20 个字符且不含空格")
        if not EMAIL_RE.match(email):
            raise UserStoreError(400, "邮箱格式不正确")
        if len(password) < 6:
            raise UserStoreError(400, "密码至少需要 6 位")
        if email in self._users:
            raise UserStoreError(409, "该邮箱已注册，请直接登录")

        salt = secrets.token_bytes(16)
        pwd_hash = _hash_password(password, salt)
        user_id = _next_user_id(self._users)
        now = time.time()
        self._users[email] = {
            "id": user_id,
            "nickname": nickname,
            "email": email,
            "salt": salt.hex(),
            "password_hash": pwd_hash,
            "created_at": now,
            "email_verified": False,  # 邮箱验证预留：本期跳过
        }
        self._save_users()
        token = self._create_session(user_id)
        log.info(f"[UserStore] 新用户注册: {email} (id={user_id})")
        return {"token": token, "nickname": nickname, "email": email}

    def login(self, email: str, password: str) -> dict:
        email = (email or "").strip().lower()
        user = self._users.get(email)
        if not user or not _verify_password(user, password):
            raise UserStoreError(401, "邮箱或密码不正确")
        token = self._create_session(user["id"])
        log.info(f"[UserStore] 登录: {email}")
        return {"token": token, "nickname": user["nickname"], "email": email}

    def me(self, token: str) -> dict:
        uid = self.resolve_user_id(token)
        if uid is None:
            raise UserStoreError(401, "登录已失效，请重新登录")
        user = _find_user(self._users, uid)
        if user is None:
            raise UserStoreError(401, "登录已失效，请重新登录")
        return {
            "nickname": user["nickname"],
            "email": user["email"],
            "plays_today": self.get_daily_plays(uid),
            "limit": MEMBER_DAILY_LIMIT,
        }

    def logout(self, token: str) -> bool:
        with self._lock:
            existed = token in self._sessions
            if existed:
                del self._sessions[token]
                self._save_sessions()
        return existed

    # ---------------------------------------------------------------- session
    def _create_session(self, user_id: int) -> str:
        with self._lock:
            _prune_sessions(self._sessions)
            token = secrets.token_urlsafe(32)
            self._sessions[token] = {
                "user_id": user_id,
                "created_at": time.time(),
                "last_seen": time.time(),
            }
            self._save_sessions()
        return token

    def resolve_user_id(self, token) -> int | None:
        """校验 session token，返回 user_id；无效/过期返回 None。"""
        if not token:
            return None
        with self._lock:
            sess = self._sessions.get(token)
            if not sess:
                return None
            now = time.time()
            if now - float(sess.get("created_at", 0)) > _SESSION_TTL_SECONDS:
                del self._sessions[token]
                self._save_sessions()
                return None
            sess["last_seen"] = now
            return sess.get("user_id")

    # ------------------------------------------------------------------ 每日局数
    def record_play(self, user_id: int) -> int:
        today = date.today().isoformat()
        with self._lock:
            day = self._plays.setdefault(str(user_id), {})
            # 跨日自动归零：只保留今天
            if set(day.keys()) != {today}:
                day = {today: 0}
                self._plays[str(user_id)] = day
            day[today] = int(day.get(today, 0)) + 1
            self._save_plays()
            return day[today]

    def get_daily_plays(self, user_id: int) -> int:
        today = date.today().isoformat()
        with self._lock:
            day = self._plays.get(str(user_id), {})
            return int(day.get(today, 0))

    # ------------------------------------------------------------------ 持久化
    def _save_users(self) -> None:
        with self._lock:
            _write_json_atomic(self._users_path, self._users)

    def _save_sessions(self) -> None:
        with self._lock:
            _write_json_atomic(self._sessions_path, self._sessions)

    def _save_plays(self) -> None:
        with self._lock:
            _write_json_atomic(self._plays_path, self._plays)


def _next_user_id(users: dict) -> int:
    ids = [int(u.get("id", 0)) for u in users.values()]
    return max(ids, default=0) + 1


def _find_user(users: dict, user_id: int) -> dict | None:
    for u in users.values():
        if int(u.get("id", -1)) == int(user_id):
            return u
    return None


def _verify_password(user: dict, password: str) -> bool:
    try:
        salt = bytes.fromhex(user.get("salt", ""))
        expected = user.get("password_hash", "")
    except (TypeError, ValueError):
        return False
    actual = _hash_password(password, salt)
    return hmac.compare_digest(actual, expected)


def _prune_sessions(sessions: dict) -> None:
    now = time.time()
    expired = [
        tok for tok, s in sessions.items()
        if now - float(s.get("created_at", 0)) > _SESSION_TTL_SECONDS
    ]
    for tok in expired:
        del sessions[tok]


# 模块级单例：与 score_store / get_store 保持一致
user_store = UserStore()

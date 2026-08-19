"""
会员远程模式集成测试（FastAPI TestClient 进程内 + mock 官网响应）。

覆盖：
- _remote_mode() 判定（需 ENABLE_MEMBER_LOGIN=1 且 MEMBER_SERVER_URL 非空）；
- register/login 转发官网、不写本地 users.json；
- me 透传官网 total_scores；
- /api/start 远程模式 meta 存 member_token 而非 user_id；
- 对局结束调官网 record_play + sync_scores（play_counted 幂等）；
- 官网实例接口：/api/member/play-record、/api/member/scores 鉴权与记账。
"""
import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient

import main as main_mod
import backend.config
import backend.member_client as mc
import backend.score_store as score_store_mod
from backend.user_store import UserStore

TEST_DATA_DIR = "/tmp/gd-bin/member_remote_data"

# 官网响应队列（每个用例重置）
_OFFICIAL = []
_OFFICIAL_CALLS = []


class _OfficialResp:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._body = json_body if json_body is not None else {}
        self.text = str(self._body)

    def json(self):
        return self._body


class _OfficialClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, path, **kwargs):
        _OFFICIAL_CALLS.append({"method": method, "path": path, "kwargs": kwargs})
        if _OFFICIAL:
            return _OFFICIAL.pop(0)
        return _OfficialResp(200, {})


@pytest.fixture(autouse=True)
def _remote_env(monkeypatch):
    """开启远程模式：ENABLE_MEMBER_LOGIN=1 + MEMBER_SERVER_URL 指向假官网。"""
    monkeypatch.setattr(backend.config, "ENABLE_MEMBER_LOGIN", True)
    monkeypatch.setattr(backend.config, "MEMBER_SERVER_URL", "https://guandan.mgarden.org.cn")
    monkeypatch.setattr(mc, "_client", lambda: _OfficialClient())
    _OFFICIAL.clear()
    _OFFICIAL_CALLS.clear()

    # 每个用例独立临时数据目录 + 重建 user_store 单例 + 清空限流桶/内存得分
    if os.path.exists(TEST_DATA_DIR):
        shutil.rmtree(TEST_DATA_DIR)
    os.makedirs(TEST_DATA_DIR, exist_ok=True)
    main_mod.user_store = UserStore(data_dir=TEST_DATA_DIR)
    main_mod.rate_limit_buckets.clear()
    score_store_mod.score_store._memory.clear()
    yield


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _official_ok(body=None):
    _OFFICIAL.append(_OfficialResp(200, body if body is not None else {}))


def _force_finish(gid):
    game = main_mod.store.get(gid)
    assert game is not None, "game 不存在"
    while game.state != "finished":
        player = game.players[game.current_turn_index]
        moves = game.get_legal_moves_for_current_player()
        assert moves, f"{player} 无合法出牌，无法驱动到 finished"
        game.execute_move(player, 0, moves)
    assert game.state == "finished"


def test_remote_mode_flag():
    assert main_mod._remote_mode() is True


def test_official_server_forces_local_mode(monkeypatch):
    """官网实例（IS_OFFICIAL_SERVER=1）：即使 MEMBER_SERVER_URL 指向官网域名，
    也强制本地账号模式，绝不转发（防自调用递归）。"""
    monkeypatch.setattr(backend.config, "IS_OFFICIAL_SERVER", True)
    assert main_mod._remote_mode() is False

    # 注册直接写本地 users.json，不经过 member_client
    client = TestClient(main_mod.app)
    r = client.post("/api/auth/register", json={
        "nickname": "小明", "email": "m@x.com", "password": "secret1",
    })
    assert r.status_code == 200
    assert r.json()["token"]
    assert _OFFICIAL_CALLS == [], "官网实例不应有任何转发调用"
    assert os.path.exists(os.path.join(TEST_DATA_DIR, "users.json"))


def test_self_reference_url_forces_local_mode(monkeypatch):
    """自引用兜底：MEMBER_SERVER_URL 指向本机（localhost）视为官网，走本地模式。"""
    monkeypatch.setattr(backend.config, "IS_OFFICIAL_SERVER", False)
    monkeypatch.setattr(backend.config, "MEMBER_SERVER_URL", "http://localhost:8001")
    assert main_mod._remote_mode() is False

    client = TestClient(main_mod.app)
    r = client.post("/api/auth/register", json={
        "nickname": "小红", "email": "h@x.com", "password": "secret1",
    })
    assert r.status_code == 200
    assert _OFFICIAL_CALLS == [], "自引用不应触发转发"


def test_register_forwards_and_no_local_users():
    client = TestClient(main_mod.app)
    _official_ok({"token": "srv-token", "nickname": "小明", "email": "m@x.com"})
    r = client.post("/api/auth/register", json={
        "nickname": "小明", "email": "m@x.com", "password": "secret1",
    })
    assert r.status_code == 200
    assert r.json()["token"] == "srv-token"
    call = _OFFICIAL_CALLS[-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/auth/register"
    # 本地不写账号数据
    assert not os.path.exists(os.path.join(TEST_DATA_DIR, "users.json"))
    assert not os.path.exists(os.path.join(TEST_DATA_DIR, "sessions.json"))


def test_login_forwards_and_me_returns_total_scores():
    client = TestClient(main_mod.app)
    _official_ok({"token": "srv-token", "nickname": "小明", "email": "m@x.com"})
    r = client.post("/api/auth/login", json={"email": "m@x.com", "password": "secret1"})
    assert r.status_code == 200
    assert r.json()["token"] == "srv-token"

    _official_ok({
        "nickname": "小明", "email": "m@x.com",
        "plays_today": 7, "limit": 20,
        "total_scores": {"User": 100, "RightBot": 3, "PartnerBot": 5, "LeftBot": 2},
    })
    me = client.get("/api/auth/me", headers=_auth_headers("srv-token"))
    assert me.status_code == 200
    body = me.json()
    assert body["plays_today"] == 7
    assert body["total_scores"]["User"] == 100
    call = _OFFICIAL_CALLS[-1]
    assert call["path"] == "/api/auth/me"
    assert call["kwargs"]["headers"]["Authorization"] == "Bearer srv-token"


def test_official_error_passthrough():
    client = TestClient(main_mod.app)
    _OFFICIAL.append(_OfficialResp(401, {"detail": "邮箱或密码不正确"}))
    r = client.post("/api/auth/login", json={"email": "m@x.com", "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["detail"] == "邮箱或密码不正确"


def test_network_failure_returns_503(monkeypatch):
    import httpx
    class _Broken:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def request(self, method, path, **kwargs):
            raise httpx.ConnectError("conn refused")
    monkeypatch.setattr(mc, "_client", lambda: _Broken())
    client = TestClient(main_mod.app)
    r = client.post("/api/auth/login", json={"email": "m@x.com", "password": "secret1"})
    assert r.status_code == 503
    assert "会员服务器连接失败" in r.json()["detail"]


def test_start_stores_member_token_not_user_id():
    client = TestClient(main_mod.app)
    r = client.post("/api/start", json={"token": "srv-token"})
    assert r.status_code == 200
    gid = r.json()["game_id"]
    meta = main_mod.store.get_meta(gid)
    assert meta.get("member_token") == "srv-token"
    assert "user_id" not in meta


def test_finished_reports_to_official_once():
    client = TestClient(main_mod.app)
    _official_ok({"plays_today": 1})
    _official_ok({"total_scores": {"User": 1, "RightBot": 0, "PartnerBot": 0, "LeftBot": 0}})
    r = client.post("/api/start", json={"token": "srv-token"})
    gid = r.json()["game_id"]
    gtok = r.json()["token"]
    _force_finish(gid)
    st = client.get(f"/api/{gid}/state?token={gtok}")
    assert st.status_code == 200
    assert st.json()["state"] == "finished"

    paths = [c["path"] for c in _OFFICIAL_CALLS]
    assert "/api/member/play-record" in paths
    assert "/api/member/scores" in paths

    # 幂等：再次轮询不重复上报
    n_before = len(_OFFICIAL_CALLS)
    client.get(f"/api/{gid}/state?token={gtok}")
    assert len(_OFFICIAL_CALLS) == n_before


def test_member_endpoints_require_auth():
    client = TestClient(main_mod.app)
    assert client.post("/api/member/play-record").status_code == 401
    assert client.post("/api/member/scores", json={"local_scores": {"User": 1}}).status_code == 401


def test_member_endpoints_record_play_and_scores():
    """官网实例（本地模式）直接记账：注册 → play-record → scores。"""
    client = TestClient(main_mod.app)
    # 注册在远程模式会被转发官网；这里直连 store 造一个本地会话模拟官网实例
    store = main_mod.user_store
    user = store.register("官网用户", "web@x.com", "secret1")
    token = user["token"]

    r = client.post("/api/member/play-record", headers=_auth_headers(token))
    assert r.status_code == 200
    assert r.json()["plays_today"] == 1

    r2 = client.post("/api/member/scores", headers=_auth_headers(token), json={"local_scores": {"User": 5}})
    assert r2.status_code == 200
    assert r2.json()["total_scores"]["User"] == 5

    # 无效 token → 401
    assert client.post("/api/member/play-record", headers=_auth_headers("bad")).status_code == 401

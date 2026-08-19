"""
会员注册/登录 API 集成测试。

用 FastAPI 的 TestClient 进程内调用（与 main 共享 store/user_store），
可完整验证：注册→登录→me(plays_today)、登出、/api/start 绑定 user_id、
对局结束 record_play 只记一次。

用户数据写入临时目录，且每个用例前重建 user_store 单例，互不污染。
"""
import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient

import main as main_mod
from backend.user_store import UserStore
import backend.config

TEST_DATA_DIR = "/tmp/gd-bin/auth_api_data"


@pytest.fixture(autouse=True)
def _enable_member_login(monkeypatch):
    """会员登录开关打开 + 本地模式（清空官网 URL）：
    auth 路由默认 gate 关闭，本套件测试会员功能需显式开启；
    配置默认连官网，测试必须回落到本地 UserStore 才不依赖网络。"""
    monkeypatch.setattr(backend.config, "ENABLE_MEMBER_LOGIN", True)
    monkeypatch.setattr(backend.config, "MEMBER_SERVER_URL", "")
    yield


@pytest.fixture(autouse=True)
def fresh_user_store():
    """每个用例独立临时数据目录 + 重建 user_store 单例（替换 main 模块引用）。"""
    if os.path.exists(TEST_DATA_DIR):
        shutil.rmtree(TEST_DATA_DIR)
    os.makedirs(TEST_DATA_DIR, exist_ok=True)
    main_mod.user_store = UserStore(data_dir=TEST_DATA_DIR)
    # TestClient 都来自同一 IP，限流桶跨用例累计会误伤——每个用例清空
    main_mod.rate_limit_buckets.clear()
    yield


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _register(client, email="ming@test.com", nickname="小明", password="secret1"):
    r = client.post("/api/auth/register", json={
        "nickname": nickname, "email": email, "password": password,
    })
    assert r.status_code == 200, r.text
    return r.json()


def _force_finish(gid):
    """在进程内直接驱动对局到 finished，供 record_play 断言。"""
    from backend.game_store import get_store
    game = get_store().get(gid)
    assert game is not None, "game 不存在"
    while game.state != "finished":
        player = game.players[game.current_turn_index]
        moves = game.get_legal_moves_for_current_player()
        assert moves, f"{player} 无合法出牌，无法驱动到 finished"
        game.execute_move(player, 0, moves)
    assert game.state == "finished"


def test_register_login_me_logout():
    client = TestClient(main_mod.app)
    data = _register(client)
    token = data["token"]
    assert data["nickname"] == "小明"

    me = client.get("/api/auth/me", headers=_auth_headers(token))
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "ming@test.com"
    assert body["plays_today"] == 0
    assert body["limit"] == 20

    # 登出后 me → 401
    out = client.post("/api/auth/logout", headers=_auth_headers(token))
    assert out.status_code == 200
    assert client.get("/api/auth/me", headers=_auth_headers(token)).status_code == 401

    # 登录换新 token（邮箱大小写不敏感）
    login = client.post("/api/auth/login", json={"email": "MING@test.com", "password": "secret1"})
    assert login.status_code == 200
    assert login.json()["nickname"] == "小明"


def test_register_duplicate_and_bad_login():
    client = TestClient(main_mod.app)
    _register(client, email="dup@test.com")
    dup = client.post("/api/auth/register", json={
        "nickname": "小红", "email": "dup@test.com", "password": "secret2",
    })
    assert dup.status_code == 409
    bad = client.post("/api/auth/login", json={"email": "dup@test.com", "password": "wrong"})
    assert bad.status_code == 401


def test_register_validation():
    client = TestClient(main_mod.app)
    r = client.post("/api/auth/register", json={
        "nickname": "a", "email": "x@y.com", "password": "secret1",
    })
    assert r.status_code == 400


def test_start_binds_user_id():
    client = TestClient(main_mod.app)
    token = _register(client, email="player@test.com")["token"]
    uid = main_mod.user_store.resolve_user_id(token)

    r = client.post("/api/start", json={"token": token})
    assert r.status_code == 200
    gid = r.json()["game_id"]
    meta = main_mod.store.get_meta(gid)
    assert meta.get("user_id") == uid, "meta 应绑定会员 user_id"

    # 未登录开局 → meta 无 user_id
    r2 = client.post("/api/start")
    assert r2.status_code == 200
    assert "user_id" not in main_mod.store.get_meta(r2.json()["game_id"])


def test_start_with_invalid_token_ignored():
    client = TestClient(main_mod.app)
    r = client.post("/api/start", json={"token": "not-a-valid-session-token"})
    assert r.status_code == 200
    assert "user_id" not in main_mod.store.get_meta(r.json()["game_id"])


def test_finished_records_play_once():
    """会员对局结束 → plays_today+1；再次结算不重复计数（play_counted 幂等）。"""
    client = TestClient(main_mod.app)
    token = _register(client, email="count@test.com")["token"]
    uid = main_mod.user_store.resolve_user_id(token)

    r = client.post("/api/start", json={"token": token})
    gid = r.json()["game_id"]
    gtok = r.json()["token"]

    # 触发结算路径（_build_state 里 finished + 未结算时 record_play）
    _force_finish(gid)
    st = client.get(f"/api/{gid}/state?token={gtok}")
    assert st.status_code == 200
    assert st.json()["state"] == "finished"
    assert main_mod.user_store.get_daily_plays(uid) == 1

    # 再次轮询 → 幂等，不重复计数
    st2 = client.get(f"/api/{gid}/state?token={gtok}")
    assert st2.status_code == 200
    assert main_mod.user_store.get_daily_plays(uid) == 1

    # me 接口反映当日局数
    me = client.get("/api/auth/me", headers=_auth_headers(token))
    assert me.json()["plays_today"] == 1


def test_guest_finished_does_not_record_play():
    """游客对局结束不记录任何会员局数。"""
    client = TestClient(main_mod.app)
    r = client.post("/api/start")
    gid = r.json()["game_id"]
    gtok = r.json()["token"]
    _force_finish(gid)
    st = client.get(f"/api/{gid}/state?token={gtok}")
    assert st.status_code == 200
    assert st.json()["state"] == "finished"
    plays_file = os.path.join(TEST_DATA_DIR, "plays.json")
    if os.path.exists(plays_file):
        assert json.loads(open(plays_file, encoding="utf-8").read()) == {}

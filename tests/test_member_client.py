"""
member_client 远程客户端单测（mock httpx，不联网）。

验证：register/login/me/logout/record_play/sync_scores 的
URL、method、header、body、响应透传；官网业务错误映射为对应
UserStoreError（状态码 + detail）；网络异常 → 503。
"""
import pytest

import httpx
import backend.config
import backend.member_client as mc
from backend.user_store import UserStoreError

# 预置响应队列：每个用例通过 use_responses 替换
_RESPONSES = []


class _FakeResp:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.text = str(self._json_body)

    def json(self):
        return self._json_body


class _FakeClient:
    """记录请求参数并弹出预置响应的 AsyncClient 替身（共享模块级队列，跨请求顺序弹出）。"""

    captured = []

    def __init__(self, responses):
        self._responses = _RESPONSES  # 引用共享队列，保证多次请求按顺序弹

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, path, **kwargs):
        type(self).captured.append({"method": method, "path": path, "kwargs": kwargs})
        if self._responses:
            return self._responses.pop(0)
        return _FakeResp(200, {})


class _FailingClient(_FakeClient):
    async def request(self, method, path, **kwargs):
        raise httpx.ConnectError("conn refused")


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch):
    monkeypatch.setattr(backend.config, "MEMBER_SERVER_URL", "https://guandan.mgarden.org.cn")

    def make_client():
        return _FakeClient(_RESPONSES)

    monkeypatch.setattr(mc, "_client", make_client)
    _RESPONSES.clear()
    _FakeClient.captured.clear()
    yield


def _use(responses):
    _RESPONSES.extend(responses)


def test_register_forwards():
    _use([_FakeResp(200, {"token": "t1", "nickname": "小明", "email": "m@x.com"})])
    out = mc.register("小明", "m@x.com", "secret1")
    cap = _FakeClient.captured[-1]
    assert cap["method"] == "POST"
    assert cap["path"] == "/api/auth/register"
    assert cap["kwargs"]["json"] == {"nickname": "小明", "email": "m@x.com", "password": "secret1"}
    assert out == {"token": "t1", "nickname": "小明", "email": "m@x.com"}


def test_login_forwards_without_token_header():
    _use([_FakeResp(200, {"token": "t2", "nickname": "小明", "email": "m@x.com"})])
    out = mc.login("m@x.com", "secret1")
    cap = _FakeClient.captured[-1]
    assert cap["method"] == "POST"
    assert cap["path"] == "/api/auth/login"
    assert cap["kwargs"]["json"] == {"email": "m@x.com", "password": "secret1"}
    assert "Authorization" not in cap["kwargs"]["headers"]
    assert out["token"] == "t2"


def test_me_sends_bearer():
    _use([_FakeResp(200, {"nickname": "小明", "email": "m@x.com", "plays_today": 3, "limit": 20})])
    out = mc.me("tok-abc")
    cap = _FakeClient.captured[-1]
    assert cap["method"] == "GET"
    assert cap["path"] == "/api/auth/me"
    assert cap["kwargs"]["headers"]["Authorization"] == "Bearer tok-abc"
    assert out["plays_today"] == 3


def test_logout():
    _use([_FakeResp(200, {"ok": True})])
    out = mc.logout("tok-x")
    cap = _FakeClient.captured[-1]
    assert cap["method"] == "POST"
    assert cap["path"] == "/api/auth/logout"
    assert cap["kwargs"]["headers"]["Authorization"] == "Bearer tok-x"
    assert out == {"ok": True}


def test_record_play_and_sync_scores():
    _use([
        _FakeResp(200, {"plays_today": 5}),
        _FakeResp(200, {"total_scores": {"User": 12}}),
    ])
    assert mc.record_play("tok")["plays_today"] == 5
    cap = _FakeClient.captured[-1]
    assert cap["method"] == "POST"
    assert cap["path"] == "/api/member/play-record"

    out = mc.sync_scores("tok", {"User": 12})
    cap = _FakeClient.captured[-1]
    assert cap["path"] == "/api/member/scores"
    assert cap["kwargs"]["json"] == {"local_scores": {"User": 12}}
    assert out["total_scores"]["User"] == 12


def test_official_error_maps_to_user_store_error():
    _use([_FakeResp(401, {"detail": "邮箱或密码不正确"})])
    with pytest.raises(UserStoreError) as ei:
        mc.login("m@x.com", "wrong")
    assert ei.value.code == 401
    assert ei.value.message == "邮箱或密码不正确"


def test_official_409_maps_code():
    _use([_FakeResp(409, {"detail": "该邮箱已注册，请直接登录"})])
    with pytest.raises(UserStoreError) as ei:
        mc.register("小明", "dup@x.com", "secret1")
    assert ei.value.code == 409
    assert ei.value.message == "该邮箱已注册，请直接登录"


def test_network_error_maps_503(monkeypatch):
    monkeypatch.setattr(mc, "_client", lambda: _FailingClient([]))
    with pytest.raises(UserStoreError) as ei:
        mc.login("m@x.com", "secret1")
    assert ei.value.code == 503
    assert "会员服务器连接失败" in ei.value.message

"""UserStore 单元测试：注册/登录/me/logout、PBKDF2、重复邮箱、每日局数重置。

每个测试用独立临时数据目录（GAME_USER_DATA_DIR 语义），不污染真实数据。
"""
import json
import time

import pytest

from backend.user_store import UserStore, UserStoreError


@pytest.fixture()
def store(tmp_path):
    return UserStore(data_dir=tmp_path)


# ---------------------------------------------------------------- 注册
def test_register_creates_user_and_token(store, tmp_path):
    user = store.register("小明", "ming@example.com", "secret1")
    assert user["token"]
    assert user["nickname"] == "小明"
    assert user["email"] == "ming@example.com"

    # 持久化：文件里有 PBKDF2 哈希，绝不存明文
    users = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))
    record = users["ming@example.com"]
    assert record["password_hash"]
    assert record["password_hash"] != "secret1"
    assert "password" not in record
    assert record["email_verified"] is False  # 邮箱验证预留

    # 登录态可验证
    assert store.resolve_user_id(user["token"]) == record["id"]


def test_register_validation(store):
    with pytest.raises(UserStoreError) as e:
        store.register("a", "x@y.com", "secret1")  # 昵称太短
    assert e.value.code == 400
    with pytest.raises(UserStoreError) as e:
        store.register("昵称", "bad-email", "secret1")  # 邮箱格式
    assert e.value.code == 400
    with pytest.raises(UserStoreError) as e:
        store.register("昵称", "x@y.com", "123")  # 密码过短
    assert e.value.code == 400


def test_register_duplicate_email_conflict(store):
    store.register("小明", "dup@example.com", "secret1")
    with pytest.raises(UserStoreError) as e:
        store.register("小红", "dup@example.com", "secret2")
    assert e.value.code == 409
    # 邮箱大小写归一化后仍视为重复
    with pytest.raises(UserStoreError):
        store.register("小红", "DUP@example.com", "secret2")


def test_register_unique_ids(store):
    u1 = store.register("甲甲", "a@example.com", "secret1")
    u2 = store.register("乙乙", "b@example.com", "secret1")
    id1 = store.resolve_user_id(u1["token"])
    id2 = store.resolve_user_id(u2["token"])
    assert id1 != id2


# ---------------------------------------------------------------- 登录
def test_login_success_and_wrong_password(store):
    store.register("小明", "login@example.com", "secret1")
    user = store.login("login@example.com", "secret1")
    assert user["nickname"] == "小明"
    assert user["token"]

    with pytest.raises(UserStoreError) as e:
        store.login("login@example.com", "wrong-pass")
    assert e.value.code == 401
    with pytest.raises(UserStoreError) as e:
        store.login("not-exist@example.com", "secret1")
    assert e.value.code == 401


def test_pbkdf2_salt_makes_same_password_differ(store, tmp_path):
    store.register("甲甲", "s1@example.com", "same-pass")
    store2 = UserStore(data_dir=tmp_path)
    store2.register("乙乙", "s2@example.com", "same-pass")
    users = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))
    assert users["s1@example.com"]["password_hash"] != users["s2@example.com"]["password_hash"]


# ---------------------------------------------------------------- me / logout
def test_me_returns_profile_and_plays(store):
    user = store.register("小明", "me@example.com", "secret1")
    info = store.me(user["token"])
    assert info["nickname"] == "小明"
    assert info["email"] == "me@example.com"
    assert info["plays_today"] == 0
    assert info["limit"] == 20


def test_me_invalid_token(store):
    with pytest.raises(UserStoreError) as e:
        store.me("not-a-valid-token")
    assert e.value.code == 401
    with pytest.raises(UserStoreError):
        store.me(None)


def test_logout_invalidates_token(store):
    user = store.register("小明", "logout@example.com", "secret1")
    assert store.resolve_user_id(user["token"]) is not None
    assert store.logout(user["token"]) is True
    assert store.resolve_user_id(user["token"]) is None
    with pytest.raises(UserStoreError) as e:
        store.me(user["token"])
    assert e.value.code == 401


# ---------------------------------------------------------------- 每日局数
def test_record_and_get_daily_plays(store):
    user = store.register("小明", "plays@example.com", "secret1")
    uid = store.resolve_user_id(user["token"])
    for _ in range(3):
        store.record_play(uid)
    assert store.get_daily_plays(uid) == 3


def test_daily_reset_via_date_injection(store):
    """跨日自动归零：模拟系统日期前进一天后，计数从 0 重新累加。"""
    import backend.user_store as us_mod
    user = store.register("小明", "reset@example.com", "secret1")
    uid = store.resolve_user_id(user["token"])

    real_date = us_mod.date  # datetime.date（不可变 C 类型，整体替换模块内名字）

    class FakeDate:
        day = 1

        @classmethod
        def today(cls):
            return cls

        @classmethod
        def isoformat(cls):
            return f"2026-08-{cls.day:02d}"

    us_mod.date = FakeDate
    try:
        # 第一天记 5 局
        FakeDate.day = 1
        for _ in range(5):
            store.record_play(uid)
        assert store.get_daily_plays(uid) == 5

        # 第二天记 2 局 → 计数从 0 重算
        FakeDate.day = 2
        for _ in range(2):
            store.record_play(uid)
        assert store.get_daily_plays(uid) == 2
    finally:
        us_mod.date = real_date


def test_persistence_across_store_instances(store, tmp_path):
    user = store.register("小明", "persist@example.com", "secret1")
    uid = store.resolve_user_id(user["token"])
    store.record_play(uid)
    store.record_play(uid)

    # 重新实例化（模拟重启）→ 数据仍在
    store2 = UserStore(data_dir=tmp_path)
    assert store2.get_daily_plays(uid) == 2
    assert store2.login("persist@example.com", "secret1")["token"]

"""3.5 history/ 清理策略测试。"""
import os
import time
from pathlib import Path

import pytest


@pytest.fixture
def fake_history(tmp_path, monkeypatch):
    # 把 HISTORY_DIR 指向临时目录，并设很小的上限
    import backend.game_engine as ge
    monkeypatch.setattr(ge, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(ge, "HISTORY_MAX_FILES", 3)
    monkeypatch.setattr(ge, "HISTORY_MAX_AGE_DAYS", 1)  # 1 天，旧文件应超龄
    return tmp_path


def test_cleanup_by_count(fake_history):
    # 写 5 个文件，应只保留最新 3 个
    for i in range(5):
        f = fake_history / f"game{i}.json"
        f.write_text("{}", encoding="utf-8")
        # 让 mtime 递增，便于按时间排序
        os.utime(f, (time.time() + i, time.time() + i))
    from backend.game_engine import _cleanup_history_dir
    _cleanup_history_dir()
    remaining = list(fake_history.glob("*.json"))
    assert len(remaining) == 3, f"应保留 3 个，实际 {len(remaining)}"


def test_cleanup_by_age(fake_history):
    # 一个很旧的文件（超龄）+ 一个很新的文件
    old = fake_history / "old.json"
    old.write_text("{}", encoding="utf-8")
    os.utime(old, (time.time() - 100000, time.time() - 100000))
    new = fake_history / "new.json"
    new.write_text("{}", encoding="utf-8")
    os.utime(new, (time.time(), time.time()))
    from backend.game_engine import _cleanup_history_dir
    _cleanup_history_dir()
    remaining = [p.name for p in fake_history.glob("*.json")]
    assert "old.json" not in remaining, "超龄文件应被删"
    assert "new.json" in remaining, "新文件应保留"

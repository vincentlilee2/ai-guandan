from __future__ import annotations

from typing import Dict
import os
import threading

try:
    import pymysql
except Exception:  # pragma: no cover - optional dependency
    pymysql = None

DEFAULT_SCORES: Dict[str, int] = {
    "User": 0,
    "RightBot": 0,
    "PartnerBot": 0,
    "LeftBot": 0,
}


def normalize_scores(scores: Dict[str, int] | None) -> Dict[str, int]:
    base = dict(DEFAULT_SCORES)
    if not isinstance(scores, dict):
        return base
    for key in base.keys():
        value = scores.get(key)
        if value is None:
            continue
        try:
            base[key] = int(value)
        except (TypeError, ValueError):
            continue
    return base


class ScoreStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._memory: Dict[int, Dict[str, int]] = {}
        self._enabled = bool(
            os.getenv("GAME_DB_HOST")
            and os.getenv("GAME_DB_USER")
            and os.getenv("GAME_DB_NAME")
        )

        if pymysql is None:
            self._enabled = False

    def _connect(self):
        if not self._enabled:
            return None
        return pymysql.connect(
            host=os.getenv("GAME_DB_HOST", "127.0.0.1"),
            user=os.getenv("GAME_DB_USER", ""),
            password=os.getenv("GAME_DB_PASS", ""),
            db=os.getenv("GAME_DB_NAME", ""),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    def get_scores(self, user_id: int) -> Dict[str, int]:
        if not user_id:
            return dict(DEFAULT_SCORES)
        if not self._enabled:
            with self._lock:
                return dict(self._memory.get(user_id, DEFAULT_SCORES))

        conn = self._connect()
        if conn is None:
            return dict(DEFAULT_SCORES)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT score_user, score_rightbot, score_partnerbot, score_leftbot FROM game_scores WHERE user_id=%s",
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    return dict(DEFAULT_SCORES)
                return {
                    "User": int(row.get("score_user", 0)),
                    "RightBot": int(row.get("score_rightbot", 0)),
                    "PartnerBot": int(row.get("score_partnerbot", 0)),
                    "LeftBot": int(row.get("score_leftbot", 0)),
                }
        finally:
            conn.close()

    def upsert_scores(self, user_id: int, scores: Dict[str, int]) -> Dict[str, int]:
        scores = normalize_scores(scores)
        if not self._enabled:
            with self._lock:
                self._memory[user_id] = dict(scores)
            return dict(scores)

        conn = self._connect()
        if conn is None:
            return dict(scores)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO game_scores (user_id, score_user, score_rightbot, score_partnerbot, score_leftbot)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        score_user=VALUES(score_user),
                        score_rightbot=VALUES(score_rightbot),
                        score_partnerbot=VALUES(score_partnerbot),
                        score_leftbot=VALUES(score_leftbot)
                    """,
                    (
                        user_id,
                        scores["User"],
                        scores["RightBot"],
                        scores["PartnerBot"],
                        scores["LeftBot"],
                    ),
                )
            return dict(scores)
        finally:
            conn.close()

    def add_scores(self, user_id: int, delta_scores: Dict[str, int]) -> Dict[str, int]:
        delta_scores = normalize_scores(delta_scores)
        current = self.get_scores(user_id)
        updated = {
            "User": current["User"] + delta_scores["User"],
            "RightBot": current["RightBot"] + delta_scores["RightBot"],
            "PartnerBot": current["PartnerBot"] + delta_scores["PartnerBot"],
            "LeftBot": current["LeftBot"] + delta_scores["LeftBot"],
        }
        return self.upsert_scores(user_id, updated)


score_store = ScoreStore()

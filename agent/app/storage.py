"""Дедупликация уже отправленных постов в SQLite.

Только стандартная библиотека — модуль покрыт smoke-тестами.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class SeenStore:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
                key TEXT PRIMARY KEY,
                topic TEXT NOT NULL DEFAULT '',
                first_seen TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def is_seen(self, key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM posts WHERE key = ?", (key,)
            ).fetchone()
        return row is not None

    def mark_seen(self, key: str, topic: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO posts (key, topic, first_seen) VALUES (?, ?, ?)",
                (key, topic, now),
            )
            self._conn.commit()

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM posts").fetchone()
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

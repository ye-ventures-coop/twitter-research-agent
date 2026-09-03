"""Runtime-настройки агента. Хранятся в JSON, меняются на лету через Telegram-бота.

Только стандартная библиотека — модуль покрыт smoke-тестами.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from copy import deepcopy
from pathlib import Path

DEFAULTS: dict = {
    # Список тем: [{"name": "AI", "query": "нейросети, LLM, агенты"}]
    "topics": [],
    "min_likes": 50,
    "min_views": 10000,
    "min_reposts": 5,
    "max_posts_per_topic": 5,
    "interval_hours": 4,
    # Модель из grok2api (GET /v1/models). Для Web-пула: grok-chat-fast.
    "model": "grok-chat-fast",
}


class Settings:
    """Потокобезопасное хранилище настроек с атомарной записью на диск."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._data: dict = deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self._path.exists():
                return
            try:
                stored = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                # Не падаем: отодвигаем битый файл и стартуем с дефолтами.
                try:
                    os.replace(self._path, self._path.with_suffix(".broken"))
                except OSError:
                    pass
                return
            if isinstance(stored, dict):
                merged = deepcopy(DEFAULTS)
                merged.update({k: v for k, v in stored.items() if k in DEFAULTS})
                self._data = merged

    def save(self) -> None:
        with self._lock:
            data = deepcopy(self._data)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    def get(self, key: str, default=None):
        with self._lock:
            return deepcopy(self._data.get(key, default))

    def set(self, key: str, value) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"Неизвестная настройка: {key}")
        with self._lock:
            self._data[key] = value
        self.save()

    def snapshot(self) -> dict:
        with self._lock:
            return deepcopy(self._data)

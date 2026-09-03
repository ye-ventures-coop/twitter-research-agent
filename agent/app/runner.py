"""Ядро агента: цикл ресёрча по темам, дедупликация, отправка отчётов."""
from __future__ import annotations

import asyncio
import logging
import time

from . import prompts, researcher

log = logging.getLogger(__name__)


class ResearchRunner:
    """Выполняет ресёрч-прогон по всем темам и шлёт отчёты в Telegram.

    send_html — async-функция, принимающая список HTML-чанков.
    """

    def __init__(self, settings, grok, store, send_html):
        self._settings = settings
        self._grok = grok
        self._store = store
        self._send = send_html
        self._lock = asyncio.Lock()
        self.last_started: float | None = None
        self.last_finished: float | None = None
        self.last_summary: str = "ещё не запускался"

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def next_run_in_seconds(self) -> int:
        interval = int(self._settings.get("interval_hours", 4) * 3600)
        if self.last_finished is None:
            return 0
        return max(0, int(self.last_finished + interval - time.time()))

    async def run_once(self) -> str:
        if self._lock.locked():
            return "запуск уже идёт, дождитесь завершения"
        async with self._lock:
            self.last_started = time.time()
            try:
                summary = await self._do_run()
            except Exception as exc:  # страховка, чтобы планировщик не умер
                log.exception("research run failed")
                summary = f"❌ Ошибка ресёрча: {exc}"
                try:
                    await self._send([summary])
                except Exception:
                    log.exception("failed to send error notification")
            self.last_summary = summary
            self.last_finished = time.time()
            return summary

    async def _do_run(self) -> str:
        cfg = self._settings.snapshot()
        topics = cfg.get("topics") or []
        if not topics:
            msg = (
                "⚠️ Темы не настроены — ресёрч пропущен.\n"
                "Добавьте тему командой:\n/addtopic Название | поисковый запрос"
            )
            await self._send([msg])
            return "темы не настроены"

        total_new = 0
        for topic in topics:
            name = str(topic.get("name", "")).strip() or "без названия"
            posts = await self._research_topic(topic, cfg)
            fresh = []
            for post in posts:
                key = researcher.post_key(post)
                if not key or self._store.is_seen(key):
                    continue
                self._store.mark_seen(key, name)
                fresh.append(post)
            total_new += len(fresh)
            chunks = researcher.format_report(name, fresh)
            if chunks:
                await self._send(chunks)

        if total_new == 0:
            await self._send(["🔍 Ресёрч завершён: новых постов по вашим темам не найдено."])
        summary = f"новых постов: {total_new}"
        log.info("research finished: %s", summary)
        return summary

    async def _research_topic(self, topic: dict, cfg: dict) -> list[dict]:
        name = str(topic.get("name", "")).strip() or "без названия"
        query = str(topic.get("query", "")).strip() or name
        user_prompt = prompts.build_user_prompt(
            topic_name=name,
            query=query,
            min_likes=cfg["min_likes"],
            min_views=cfg["min_views"],
            min_reposts=cfg["min_reposts"],
            max_posts=cfg["max_posts_per_topic"],
        )
        log.info("researching topic %r with model %r", name, cfg["model"])
        try:
            raw = await self._grok.chat(cfg["model"], prompts.SYSTEM_PROMPT, user_prompt)
        except Exception as exc:
            log.exception("grok2api request failed for topic %r", name)
            await self._send([f"❌ Ошибка запроса к grok2api (тема «{name}»): {exc}"])
            return []
        posts = researcher.extract_json_array(raw)
        return [p for p in posts if researcher.passes_filters(p, cfg)]

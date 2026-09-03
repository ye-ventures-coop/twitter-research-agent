"""Точка входа агента: Telegram-бот (polling) + планировщик ресёрча."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from telegram import Update
from telegram.ext import Application

from .bot import register_handlers
from .grok_client import GrokClient
from .runner import ResearchRunner
from .settings import Settings
from .storage import SeenStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

SCHEDULER_TICK_SECONDS = 30


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задан TELEGRAM_BOT_TOKEN (см. .env.example)")

    chat_id = int(os.environ.get("TELEGRAM_CHAT_ID", "0") or 0)
    base_url = os.environ.get("GROK2API_BASE_URL", "http://grok2api:8000")
    api_key = os.environ.get("GROK2API_KEY", "").strip()
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))

    if not api_key:
        log.warning("GROK2API_KEY не задан — запросы к grok2api будут отклонены")

    settings = Settings(data_dir / "settings.json")
    store = SeenStore(data_dir / "seen.db")
    grok = GrokClient(base_url, api_key)

    app = Application.builder().token(token).build()

    async def send_html(chunks: list[str]) -> None:
        if not chat_id:
            log.warning("TELEGRAM_CHAT_ID не задан, отчёт не доставлен: %s", chunks[:1])
            return
        for chunk in chunks:
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                log.exception("failed to send telegram chunk")

    runner = ResearchRunner(settings, grok, store, send_html)

    async def scheduler_tick(context) -> None:
        if not runner.running and runner.next_run_in_seconds() == 0:
            log.info("scheduler: starting research run")
            asyncio.create_task(runner.run_once())

    register_handlers(app, settings, runner, store, chat_id)
    app.job_queue.run_repeating(
        scheduler_tick, interval=SCHEDULER_TICK_SECONDS, first=15
    )

    log.info(
        "agent started: grok2api=%s, chat_id=%s, interval=%s ч",
        base_url,
        chat_id or "НЕ ЗАДАН",
        settings.get("interval_hours"),
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

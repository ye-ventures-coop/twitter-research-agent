"""Режим CI (GitHub Actions): поднять провижининг grok2api, сделать один
прогон ресёрча, отправить отчёт в Telegram и завершиться.

Отличия от daemon-режима (main.py):
- нет Telegram-бота на polling: отправка через Bot API напрямую по HTTP;
- grok2api провижинится с нуля при каждом запуске (его БД эфемерна);
- состояние (настройки, база отправленных постов) лежит в STATE_DIR —
  в GitHub Actions это папка state/ репозитория, коммитится обратно.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx

from .grok_client import GrokClient
from .runner import ResearchRunner
from .settings import Settings
from .storage import SeenStore

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class ProvisionError(RuntimeError):
    pass


def _unwrap(envelope: dict) -> dict:
    """grok2api admin API оборачивает успешные ответы в {"data": ...}."""
    if isinstance(envelope, dict) and "data" in envelope:
        return envelope["data"]
    return envelope


async def wait_ready(client: httpx.AsyncClient, timeout_s: float = 300.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        try:
            resp = await client.get("/healthz", timeout=5)
            if resp.status_code == 200:
                log.info("grok2api готов")
                return
        except httpx.HTTPError:
            pass
        if asyncio.get_running_loop().time() > deadline:
            raise ProvisionError("grok2api не поднялся за отведённое время")
        await asyncio.sleep(3)


async def admin_login(client: httpx.AsyncClient, username: str, password: str) -> str:
    resp = await client.post(
        "/api/admin/v1/auth/login",
        json={"username": username, "password": password},
        timeout=30,
    )
    if resp.status_code != 200:
        raise ProvisionError(f"логин админа: HTTP {resp.status_code}: {resp.text[:300]}")
    data = _unwrap(resp.json())
    token = data.get("tokens", {}).get("accessToken")
    if not token:
        raise ProvisionError(f"логин админа: нет accessToken в ответе: {resp.text[:300]}")
    return token


def _parse_import_stream(body: str) -> dict:
    """Импорт возвращает event-stream; берём событие complete с итогами."""
    result: dict = {}
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                payload = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and (
                "created" in payload or "Created" in payload
            ):
                result = payload
    return result


def _num(payload: dict, key: str) -> int:
    for variant in (key, key[:1].upper() + key[1:]):
        value = payload.get(variant)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


async def import_sso(client: httpx.AsyncClient, access_token: str, sso: str) -> dict:
    """Импортирует SSO-токен в пул Grok Web. Синхронизация квоты/моделей
    выполняется внутри того же запроса (пайплайн ждёт завершения)."""
    resp = await client.post(
        "/api/admin/v1/accounts/web/import",
        headers={"Authorization": f"Bearer {access_token}"},
        files={"file": ("accounts.txt", sso.strip() + "\n", "text/plain")},
        timeout=300,
    )
    if resp.status_code != 200:
        raise ProvisionError(f"импорт SSO: HTTP {resp.status_code}: {resp.text[:300]}")
    result = _parse_import_stream(resp.text)
    log.info("импорт SSO завершён: %s", result or "(нет сводки в потоке)")
    if result and _num(result, "failed") > 0 and _num(result, "created") == 0:
        raise ProvisionError(f"импорт SSO не удался: {result}")
    return result


async def create_client_key(client: httpx.AsyncClient, access_token: str) -> str:
    resp = await client.post(
        "/api/admin/v1/client-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "research-agent"},
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise ProvisionError(f"client key: HTTP {resp.status_code}: {resp.text[:300]}")
    data = _unwrap(resp.json())
    secret = data.get("secret", "")
    if not secret:
        raise ProvisionError(f"client key: нет secret в ответе: {resp.text[:300]}")
    return secret


async def send_telegram(bot_token: str, chat_id: str, chunks: list[str]) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        for chunk in chunks:
            resp = await client.post(
                f"{TELEGRAM_API}/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            if resp.status_code != 200:
                log.error("telegram: HTTP %s: %s", resp.status_code, resp.text[:300])


async def main_async() -> int:
    base_url = os.environ.get("GROK2API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    admin_user = os.environ.get("G2A_ADMIN_USER", "admin")
    admin_pass = os.environ.get("G2A_ADMIN_PASSWORD", "")
    sso = os.environ.get("GROK_SSO", "").strip()
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    state_dir = Path(os.environ.get("STATE_DIR", "state"))

    missing = [
        name
        for name, value in (
            ("G2A_ADMIN_PASSWORD", admin_pass),
            ("GROK_SSO", sso),
            ("TELEGRAM_BOT_TOKEN", tg_token),
            ("TELEGRAM_CHAT_ID", tg_chat),
        )
        if not value
    ]
    if missing:
        log.error("не заданы переменные окружения: %s", ", ".join(missing))
        return 2

    settings = Settings(state_dir / "settings.json")
    store = SeenStore(state_dir / "seen.db")

    async with httpx.AsyncClient(base_url=base_url) as client:
        await wait_ready(client)
        access_token = await admin_login(client, admin_user, admin_pass)
        await import_sso(client, access_token, sso)
        client_key = await create_client_key(client, access_token)
    log.info("grok2api провижинен: аккаунт импортирован, ключ создан")

    grok = GrokClient(base_url, client_key)

    async def send(chunks: list[str]) -> None:
        await send_telegram(tg_token, tg_chat, chunks)

    runner = ResearchRunner(settings, grok, store, send)
    summary = await runner.run_once()
    log.info("прогон завершён: %s", summary)
    store.close()
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        code = asyncio.run(main_async())
    except ProvisionError as exc:
        log.error("провижининг grok2api: %s", exc)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()

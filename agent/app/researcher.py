"""Парсинг ответов Grok, фильтрация по критериям и форматирование отчётов.

Только стандартная библиотека — модуль покрыт smoke-тестами.
"""
from __future__ import annotations

import re
import json
from html import escape

MAX_TELEGRAM_CHUNK = 3900  # лимит Telegram 4096, берём с запасом


def _to_int(value) -> int:
    """Терпимо приводит метрику к int: 12300, "12.3K", "1,234" -> int."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        text = value.strip().lower().replace(",", "").replace(" ", "")
        multiplier = 1
        if text.endswith("k"):
            multiplier, text = 1_000, text[:-1]
        elif text.endswith("m"):
            multiplier, text = 1_000_000, text[:-1]
        match = re.match(r"^(\d+(?:\.\d+)?)$", text)
        if match:
            return int(float(match.group(1)) * multiplier)
        digits = re.sub(r"[^\d]", "", text)
        return int(digits) * multiplier if digits else 0
    return 0


def normalize_post(item: dict) -> dict:
    return {
        "author": str(item.get("author", "")).strip() or "unknown",
        "text": str(item.get("text", "")).strip(),
        "url": str(item.get("url", "")).strip(),
        "likes": _to_int(item.get("likes")),
        "reposts": _to_int(item.get("reposts")),
        "views": _to_int(item.get("views")),
        "posted_at": str(item.get("posted_at", "")).strip(),
        "summary": str(item.get("summary", "")).strip(),
    }


def extract_json_array(raw: str) -> list[dict]:
    """Вытаскивает JSON-массив постов из ответа модели.

    Терпимо к ```json-обёрткам и лишнему тексту вокруг массива.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [normalize_post(item) for item in data if isinstance(item, dict)]


def passes_filters(post: dict, cfg: dict) -> bool:
    """Локальная страховка поверх промпта.

    OR-логика: метрики от модели ненадёжны, поэтому достаточно,
    чтобы пост проходил хотя бы по одному порогу.
    """
    return (
        post["likes"] >= cfg["min_likes"]
        or post["views"] >= cfg["min_views"]
        or post["reposts"] >= cfg["min_reposts"]
    )


def post_key(post: dict) -> str:
    """Ключ дедупликации: URL, а если его нет — автор + начало текста."""
    if post["url"]:
        return post["url"]
    return f'{post["author"]}:{post["text"][:80]}'


def format_number(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def format_post(post: dict) -> str:
    lines = [f"<b>{escape(post['author'])}</b>"]
    if post["summary"]:
        lines.append(escape(post["summary"]))
    elif post["text"]:
        excerpt = post["text"][:250] + ("…" if len(post["text"]) > 250 else "")
        lines.append(escape(excerpt))
    stats = (
        f"❤️ {format_number(post['likes'])} · "
        f"🔁 {format_number(post['reposts'])} · "
        f"👁 {format_number(post['views'])}"
    )
    if post["posted_at"]:
        stats += f" · 🕒 {escape(post['posted_at'])}"
    lines.append(stats)
    if post["url"]:
        lines.append(f'<a href="{escape(post["url"], quote=True)}">Открыть пост</a>')
    return "\n".join(lines)


def format_report(topic_name: str, posts: list[dict]) -> list[str]:
    """Формирует список HTML-сообщений (чанки <= лимита Telegram)."""
    if not posts:
        return []
    header = f"🔍 <b>{escape(topic_name)}</b> — новых постов: {len(posts)}"
    blocks = [format_post(p) for p in posts]
    chunks: list[str] = []
    current = header
    for block in blocks:
        candidate = current + "\n\n" + block
        if len(candidate) > MAX_TELEGRAM_CHUNK:
            chunks.append(current)
            current = block
        else:
            current = candidate
    chunks.append(current)
    return chunks

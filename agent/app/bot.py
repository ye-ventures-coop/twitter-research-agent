"""Telegram-бот: управление темами, критериями и расписанием на лету."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

log = logging.getLogger(__name__)

HELP_TEXT = """<b>Команды агента</b>

📋 Темы:
/topics — список тем
/addtopic Название | поисковый запрос — добавить тему
/deltopic N — удалить тему номер N

🎯 Критерии:
/criteria — текущие пороги
/setlikes N — мин. лайков
/setviews N — мин. просмотров
/setreposts N — мин. репостов
/setmaxposts N — макс. постов на тему за прогон

⚙️ Расписание и модель:
/interval H — интервал в часах (например 4 или 0.5)
/model NAME — модель grok2api

▶️ Прочее:
/run — запустить ресёрч прямо сейчас
/status — статус агента
/help — эта справка"""


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%d.%m %H:%M:%S")


def _fmt_seconds(sec: int) -> str:
    hours, rem = divmod(sec, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def register_handlers(app: Application, settings, runner, store, chat_id: int) -> None:
    auth = filters.Chat(chat_id=chat_id) if chat_id else filters.ALL

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = "👋 Агент ресёрча X (Twitter) на связи.\n\n" + HELP_TEXT
        if not chat_id:
            text += (
                f"\n\n<b>Ваш chat_id:</b> <code>{update.effective_chat.id}</code>\n"
                "Впишите его в .env (TELEGRAM_CHAT_ID) и перезапустите агента, "
                "чтобы ограничить доступ только вами."
            )
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(HELP_TEXT, parse_mode="HTML")

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cfg = settings.snapshot()
        next_in = runner.next_run_in_seconds()
        text = (
            "📊 <b>Статус агента</b>\n\n"
            f"Тем: {len(cfg['topics'])}\n"
            f"Пороги (достаточно любого): ❤️ ≥ {cfg['min_likes']} · "
            f"👁 ≥ {cfg['min_views']} · 🔁 ≥ {cfg['min_reposts']}\n"
            f"Макс. постов на тему: {cfg['max_posts_per_topic']}\n"
            f"Интервал: каждые {cfg['interval_hours']} ч\n"
            f"Модель: <code>{cfg['model']}</code>\n"
            f"Состояние: {'⏳ идёт ресёрч' if runner.running else '💤 ожидание'}\n"
            f"Последний запуск: {_fmt_ts(runner.last_started)} ({runner.last_summary})\n"
            f"Следующий запуск: {'сейчас' if next_in == 0 else 'через ' + _fmt_seconds(next_in)}\n"
            f"Всего уникальных постов в базе: {store.count()}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_topics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        topics = settings.get("topics", [])
        if not topics:
            await update.message.reply_text(
                "Тем пока нет. Добавьте:\n/addtopic Название | поисковый запрос"
            )
            return
        lines = [f"{i}. <b>{t['name']}</b> — {t['query']}" for i, t in enumerate(topics, 1)]
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_addtopic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        raw = " ".join(context.args).strip()
        if not raw:
            await update.message.reply_text("Формат: /addtopic Название | поисковый запрос")
            return
        if "|" in raw:
            name, query = (p.strip() for p in raw.split("|", 1))
        else:
            name = query = raw
        if not name or not query:
            await update.message.reply_text("Название и запрос не должны быть пустыми.")
            return
        topics = settings.get("topics", [])
        topics.append({"name": name, "query": query})
        settings.set("topics", topics)
        await update.message.reply_text(f"✅ Тема «{name}» добавлена. Всего тем: {len(topics)}")

    async def cmd_deltopic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        topics = settings.get("topics", [])
        try:
            index = int(context.args[0])
        except (IndexError, ValueError):
            await update.message.reply_text("Формат: /deltopic N (номер из /topics)")
            return
        if not 1 <= index <= len(topics):
            await update.message.reply_text(f"Нет темы с номером {index}.")
            return
        removed = topics.pop(index - 1)
        settings.set("topics", topics)
        await update.message.reply_text(f"🗑 Тема «{removed['name']}» удалена.")

    async def cmd_criteria(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cfg = settings.snapshot()
        await update.message.reply_text(
            "🎯 <b>Текущие критерии</b>\n\n"
            f"Мин. лайков: {cfg['min_likes']} (/setlikes)\n"
            f"Мин. просмотров: {cfg['min_views']} (/setviews)\n"
            f"Мин. репостов: {cfg['min_reposts']} (/setreposts)\n"
            f"Макс. постов на тему: {cfg['max_posts_per_topic']} (/setmaxposts)\n"
            f"Интервал: {cfg['interval_hours']} ч (/interval)\n"
            f"Модель: {cfg['model']} (/model)",
            parse_mode="HTML",
        )

    async def cmd_setlikes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _set_int_ctx(update, context, "min_likes", "Мин. лайков")

    async def cmd_setviews(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _set_int_ctx(update, context, "min_views", "Мин. просмотров")

    async def cmd_setreposts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _set_int_ctx(update, context, "min_reposts", "Мин. репостов")

    async def cmd_setmaxposts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            value = int(context.args[0])
            if not 1 <= value <= 25:
                raise ValueError
        except (IndexError, ValueError):
            await update.message.reply_text("Нужно целое число от 1 до 25. Пример: /setmaxposts 5")
            return
        settings.set("max_posts_per_topic", value)
        await update.message.reply_text(f"✅ Макс. постов на тему: {value}")

    async def _set_int_ctx(update, context, key, label) -> None:
        try:
            value = int(context.args[0])
            if value < 0:
                raise ValueError
        except (IndexError, ValueError):
            await update.message.reply_text("Нужно целое число ≥ 0.")
            return
        settings.set(key, value)
        await update.message.reply_text(f"✅ {label}: {value}")

    async def cmd_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            hours = float(context.args[0].replace(",", "."))
            if not 0.25 <= hours <= 168:
                raise ValueError
        except (IndexError, ValueError):
            await update.message.reply_text("Интервал в часах, от 0.25 до 168. Пример: /interval 4")
            return
        settings.set("interval_hours", hours)
        await update.message.reply_text(
            f"✅ Интервал: каждые {hours} ч. Применится после текущего ожидания."
        )

    async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        model = " ".join(context.args).strip()
        if not model:
            await update.message.reply_text("Формат: /model grok-chat-fast")
            return
        settings.set("model", model)
        await update.message.reply_text(f"✅ Модель: {model}")

    async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if runner.running:
            await update.message.reply_text("⏳ Ресёрч уже идёт, дождитесь отчёта.")
            return
        await update.message.reply_text("🚀 Запускаю ресёрч, отчёт придёт по готовности…")
        asyncio.create_task(runner.run_once())

    app.add_handler(CommandHandler("start", cmd_start, filters=auth))
    app.add_handler(CommandHandler("help", cmd_help, filters=auth))
    app.add_handler(CommandHandler("status", cmd_status, filters=auth))
    app.add_handler(CommandHandler("topics", cmd_topics, filters=auth))
    app.add_handler(CommandHandler("addtopic", cmd_addtopic, filters=auth))
    app.add_handler(CommandHandler("deltopic", cmd_deltopic, filters=auth))
    app.add_handler(CommandHandler("criteria", cmd_criteria, filters=auth))
    app.add_handler(CommandHandler("setlikes", cmd_setlikes, filters=auth))
    app.add_handler(CommandHandler("setviews", cmd_setviews, filters=auth))
    app.add_handler(CommandHandler("setreposts", cmd_setreposts, filters=auth))
    app.add_handler(CommandHandler("setmaxposts", cmd_setmaxposts, filters=auth))
    app.add_handler(CommandHandler("interval", cmd_interval, filters=auth))
    app.add_handler(CommandHandler("model", cmd_model, filters=auth))
    app.add_handler(CommandHandler("run", cmd_run, filters=auth))

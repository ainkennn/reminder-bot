"""
handlers/callbacks.py – Inline button callbacks.
Handles: read:<task_id>
"""

import logging
from datetime import datetime

import pytz
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler

from config import TIMEZONE, THREAD_ID
from scheduler import fmt_dt
import db

logger = logging.getLogger(__name__)


async def handle_read(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("✅ Подтверждено!")

    task_id = int(query.data.split(":")[1])
    task    = db.get_task(task_id)

    if task is None:
        await query.message.reply_text("⚠️ Задача не найдена.")
        return

    if task["status"] == "read":
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    db.mark_task_read(task_id)

    try:
        await query.message.delete()
    except Exception:
        logger.warning("Could not delete reminder message for task %s", task_id)

    mention  = f"[{task['responsible_name']}](tg://user?id={task['responsible_id']})"
    title    = task["title"] or task["description"] or "—"
    dl_str   = fmt_dt(task["deadline"])

    status_text = (
        f"📋 *Задача подтверждена*\n\n"
        f"*Название:* {title}\n"
        f"*Описание:* {task['description'] or '—'}\n"
        f"*Ответственный:* {mention}\n"
        f"*Дедлайн:* {dl_str}\n"
        f"*Статус:* ✅ Прочитано"
    )

    await ctx.bot.send_message(
        chat_id=task["chat_id"],
        text=status_text,
        parse_mode="Markdown",
        message_thread_id=THREAD_ID,
    )
    logger.info("Task %s marked as read", task_id)


handlers = [
    CallbackQueryHandler(handle_read, pattern=r"^read:\d+$"),
]

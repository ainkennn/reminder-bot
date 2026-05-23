"""
handlers/callbacks.py – Inline button callbacks not tied to a conversation.

Handles: read:<task_id>
"""

import logging
from datetime import datetime

import pytz
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler

from config import TIMEZONE, THREAD_ID
import db

logger = logging.getLogger(__name__)


async def handle_read(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("✅ Подтверждено!")

    task_id = int(query.data.split(":")[1])
    task = db.get_task(task_id)

    if task is None:
        await query.message.reply_text("⚠️ Task not found.")
        return

    if task["status"] == "read":
        # Already acknowledged – just clean up the button silently
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    # Mark as read in DB
    db.mark_task_read(task_id)

    # Delete the reminder message
    try:
        await query.message.delete()
    except Exception:
        logger.warning("Could not delete reminder message for task %s", task_id)

    # Post final status message
    tz       = pytz.timezone(TIMEZONE)
    dt       = datetime.fromisoformat(task["deadline"]).astimezone(tz)
    dl_str   = dt.strftime("%Y-%m-%d %H:%M %Z")
    mention  = f"[{task['responsible_name']}](tg://user?id={task['responsible_id']})"

    status_text = (
        f"📋 *Task Acknowledged*\n\n"
        f"*Task:* {task['description']}\n"
        f"*Responsible:* {mention}\n"
        f"*Deadline:* {dl_str}\n"
        f"*Status:* ✅ Read"
    )

    await ctx.bot.send_message(
        chat_id=task["chat_id"],
        text=status_text,
        parse_mode="Markdown",
        message_thread_id=THREAD_ID,
    )
    logger.info("Task %s marked as read", task_id)


# ---------------------------------------------------------------------------
# Handler list (imported by bot.py)
# ---------------------------------------------------------------------------

handlers = [
    CallbackQueryHandler(handle_read, pattern=r"^read:\d+$"),
]

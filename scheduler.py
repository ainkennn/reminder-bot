"""
scheduler.py – APScheduler integration.
"""

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore
import pytz

from config import TIMEZONE, THREAD_ID
import db

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(
    jobstores={"default": MemoryJobStore()},
    timezone=pytz.timezone(TIMEZONE),
)

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

def fmt_dt(iso_str: str) -> str:
    tz = pytz.timezone(TIMEZONE)
    dt = datetime.fromisoformat(iso_str).astimezone(tz)
    return f"{dt.day} {MONTHS_RU[dt.month]} {dt.year}, {dt.strftime('%H:%M')}"


def start() -> None:
    if not _scheduler.running:
        _scheduler.start()
        logger.info("Scheduler started (tz=%s)", TIMEZONE)


def stop() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Job function
# ---------------------------------------------------------------------------

def _send_reminder(task_id: int, bot_token: str) -> None:
    import asyncio
    import telegram
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    task = db.get_task(task_id)
    if task is None or task["status"] != "pending":
        return

    bot     = telegram.Bot(token=bot_token)
    mention = f"[{task['responsible_name']}](tg://user?id={task['responsible_id']})"
    title   = task["title"] or task["description"] or "—"
    dl_str  = fmt_dt(task["deadline"])

    text = (
        f"⏰ *Напоминание*\n\n"
        f"📋 *Задача:* {title} (ID: {task_id})\n"
        f"👤 *Ответственный:* {mention}\n"
        f"📅 *Дедлайн:* {dl_str}\n\n"
        f"Отметить прочитанным?"
    )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Прочитано", callback_data=f"read:{task_id}")]]
    )

    async def _send():
        msg = await bot.send_message(
            chat_id=task["chat_id"],
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
            message_thread_id=THREAD_ID,
        )
        db.set_reminder_msg(task_id, msg.message_id)

    asyncio.run(_send())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def schedule_reminder(task_id: int, run_at: datetime, bot_token: str) -> None:
    tz = pytz.timezone(TIMEZONE)
    if run_at.tzinfo is None:
        run_at = tz.localize(run_at)

    job_id = f"reminder_{task_id}"
    _scheduler.add_job(
        _send_reminder,
        trigger="date",
        run_date=run_at,
        args=[task_id, bot_token],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=300,
    )
    logger.info("Scheduled reminder job %s at %s", job_id, run_at)


def reschedule_pending(bot_token: str) -> None:
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    for task in db.get_pending_tasks():
        try:
            run_at = datetime.fromisoformat(task["deadline"])
            if run_at.tzinfo is None:
                run_at = tz.localize(run_at)
            if run_at > now:
                schedule_reminder(task["id"], run_at, bot_token)
            else:
                logger.warning("Task %s deadline is in the past – skipping", task["id"])
        except Exception:
            logger.exception("Failed to reschedule task %s", task["id"])

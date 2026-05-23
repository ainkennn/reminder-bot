"""
scheduler.py – APScheduler integration.

One persistent BackgroundScheduler is created here.
bot.py starts it after the Application is built so the bot instance
can be passed in for sending messages.
"""

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore
import pytz

from config import TIMEZONE
import db

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(
    jobstores={"default": MemoryJobStore()},
    timezone=pytz.timezone(TIMEZONE),
)


def start() -> None:
    if not _scheduler.running:
        _scheduler.start()
        logger.info("Scheduler started (tz=%s)", TIMEZONE)


def stop() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Job function – runs inside the scheduler thread
# ---------------------------------------------------------------------------

def _send_reminder(task_id: int, bot_token: str) -> None:
    """Fetch the task and send the reminder message via the Telegram HTTP API."""
    import asyncio
    import telegram
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    task = db.get_task(task_id)
    if task is None or task["status"] != "pending":
        return

    bot = telegram.Bot(token=bot_token)

    mention = f"[{task['responsible_name']}](tg://user?id={task['responsible_id']})"
    text = (
        f"⏰ *Reminder*\n\n"
        f"📋 *Task:* {task['description']}\n"
        f"👤 *Responsible:* {mention}\n"
        f"📅 *Deadline:* {task['deadline']}\n\n"
        f"Please acknowledge this reminder."
    )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Read", callback_data=f"read:{task_id}")]]
    )

    async def _send():
        msg = await bot.send_message(
            chat_id=task["chat_id"],
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
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
        misfire_grace_time=300,  # fire up to 5 min late if bot was down
    )
    logger.info("Scheduled reminder job %s at %s", job_id, run_at)


def reschedule_pending(bot_token: str) -> None:
    """Re-queue reminders for tasks that survived a bot restart."""
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    tasks = db.get_pending_tasks()
    for task in tasks:
        try:
            run_at = datetime.fromisoformat(task["deadline"])
            if run_at.tzinfo is None:
                run_at = tz.localize(run_at)
            if run_at > now:
                schedule_reminder(task["id"], run_at, bot_token)
            else:
                logger.warning(
                    "Task %s deadline %s is in the past – skipping reschedule",
                    task["id"], task["deadline"],
                )
        except Exception:
            logger.exception("Failed to reschedule task %s", task["id"])

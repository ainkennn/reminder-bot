"""
bot.py – Entry point.

Run with:
    python bot.py
"""

import logging

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

import db
import scheduler as sched
from config import BOT_TOKEN, GROUP_ID, THREAD_ID
from handlers.admin import handlers as admin_handlers
from handlers.callbacks import handlers as callback_handlers
from handlers.tasks import task_conversation

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thread filter – only respond in the configured group thread
# ---------------------------------------------------------------------------

def _in_target_thread(update: Update) -> bool:
    """Return True if the message is in the configured group + thread."""
    if GROUP_ID is None:
        return True  # no restriction configured, allow all
    msg = update.effective_message
    if msg is None:
        return True  # callback queries etc. – let them through
    chat_id = update.effective_chat.id
    # Telegram supergroup IDs in the Bot API are -100<url_number>
    expected_chat = int(f"-100{GROUP_ID}") if GROUP_ID > 0 else GROUP_ID
    if chat_id != expected_chat:
        return False
    if THREAD_ID is not None:
        return msg.message_thread_id == THREAD_ID
    return True


thread_filter = filters.UpdateFilter(_in_target_thread)


# ---------------------------------------------------------------------------
# /start  /help
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "👋 *Team Reminder Bot*\n\n"
        "Commands:\n"
        "• /newtask – create a new task with a reminder\n"
        "• /listmembers – show the team roster\n"
        "• /cancel – cancel the current task flow\n\n"
        "_Admin only:_\n"
        "• /addmember `<id> <name> [role]` – add / update a team member\n"
        "• /delmember `<id>` – remove a team member",
        parse_mode="Markdown",
        message_thread_id=THREAD_ID,
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


# ---------------------------------------------------------------------------
# Post-init: set bot commands menu + reschedule surviving tasks
# ---------------------------------------------------------------------------

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("newtask",     "Create a new task"),
        BotCommand("listmembers", "Show team roster"),
        BotCommand("cancel",      "Cancel current task creation"),
        BotCommand("addmember",   "Admin: add/update a team member"),
        BotCommand("delmember",   "Admin: remove a team member"),
        BotCommand("help",        "Show help"),
    ])

    sched.start()
    sched.reschedule_pending(BOT_TOKEN)
    logger.info("Bot initialised and scheduler running")


async def post_shutdown(app: Application) -> None:
    sched.stop()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    db.init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Core commands – only in the target thread
    app.add_handler(CommandHandler("start", cmd_start, filters=thread_filter))
    app.add_handler(CommandHandler("help",  cmd_help,  filters=thread_filter))

    # Task creation conversation
    app.add_handler(task_conversation)

    # Admin commands
    for h in admin_handlers:
        app.add_handler(h)

    # Inline button callbacks (Read acknowledgment etc.)
    for h in callback_handlers:
        app.add_handler(h)

    logger.info("Starting polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()

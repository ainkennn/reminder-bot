"""
bot.py – Entry point.

Run with:
    python bot.py
"""

import logging

from telegram import BotCommand
from telegram.ext import Application, CommandHandler

import db
import scheduler as sched
from config import BOT_TOKEN
from handlers.admin import handlers as admin_handlers
from handlers.callbacks import handlers as callback_handlers
from handlers.tasks import task_conversation

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /start  /help
# ---------------------------------------------------------------------------

async def cmd_start(update, ctx):
    await update.message.reply_text(
        "👋 *Team Reminder Bot*\n\n"
        "Commands:\n"
        "• /newtask – create a new task with a reminder\n"
        "• /listmembers – show the team roster\n"
        "• /cancel – cancel the current task flow\n\n"
        "_Admin only:_\n"
        "• /addmember `<id> <name> [role]` – add / update a team member\n"
        "• /delmember `<id>` – remove a team member",
        parse_mode="Markdown",
    )


async def cmd_help(update, ctx):
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

    # Core commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))

    # Task creation conversation (must be before generic callback handlers)
    app.add_handler(task_conversation)

    # Admin commands
    for h in admin_handlers:
        app.add_handler(h)

    # Inline button callbacks (Read acknowledgment, etc.)
    for h in callback_handlers:
        app.add_handler(h)

    logger.info("Starting polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()

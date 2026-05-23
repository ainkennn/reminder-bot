"""
handlers/tasks.py – Guided task-creation conversation.

Conversation states
-------------------
ASK_DESC        → user types task description
ASK_DEADLINE    → user types deadline (date + time)
ASK_RESPONSIBLE → user picks a team member from inline keyboard
CONFIRM         → user confirms or cancels
"""

import logging
from datetime import datetime

import pytz
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import TIMEZONE
import db
import scheduler as sched

logger = logging.getLogger(__name__)

# Conversation state keys
ASK_DESC        = 0
ASK_DEADLINE    = 1
ASK_RESPONSIBLE = 2
CONFIRM         = 3

# context.user_data keys
_DESC        = "task_desc"
_DEADLINE    = "task_deadline"
_RESP_ID     = "task_resp_id"
_RESP_NAME   = "task_resp_name"
_MSG_IDS     = "task_msg_ids"   # list of message IDs to delete on confirm


def _track(ctx: ContextTypes.DEFAULT_TYPE, msg_id: int) -> None:
    ctx.user_data.setdefault(_MSG_IDS, []).append(msg_id)


async def _delete_tracked(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    for mid in ctx.user_data.get(_MSG_IDS, []):
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass
    ctx.user_data[_MSG_IDS] = []


def _clear_state(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (_DESC, _DEADLINE, _RESP_ID, _RESP_NAME, _MSG_IDS):
        ctx.user_data.pop(key, None)


# ---------------------------------------------------------------------------
# Step 0 – /newtask
# ---------------------------------------------------------------------------

async def cmd_newtask(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    members = db.get_all_members()
    if not members:
        await update.message.reply_text(
            "⚠️ No team members registered yet.\n"
            "Ask an admin to use /addmember first."
        )
        return ConversationHandler.END

    _clear_state(ctx)
    msg = await update.message.reply_text(
        "📝 *New Task*\n\nStep 1/3 – What is the task description?",
        parse_mode="Markdown",
    )
    _track(ctx, update.message.message_id)
    _track(ctx, msg.message_id)
    return ASK_DESC


# ---------------------------------------------------------------------------
# Step 1 – receive description
# ---------------------------------------------------------------------------

async def recv_description(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data[_DESC] = update.message.text.strip()
    _track(ctx, update.message.message_id)

    msg = await update.message.reply_text(
        "📅 Step 2/3 – When is the deadline?\n\n"
        "Please enter date and time in this format:\n`YYYY-MM-DD HH:MM`\n\n"
        f"_(Timezone: {TIMEZONE})_",
        parse_mode="Markdown",
    )
    _track(ctx, msg.message_id)
    return ASK_DEADLINE


# ---------------------------------------------------------------------------
# Step 2 – receive deadline
# ---------------------------------------------------------------------------

async def recv_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    _track(ctx, update.message.message_id)

    tz = pytz.timezone(TIMEZONE)
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
        dt = tz.localize(dt)
    except ValueError:
        msg = await update.message.reply_text(
            "❌ Invalid format. Please use `YYYY-MM-DD HH:MM` (e.g. `2025-12-31 09:00`).",
            parse_mode="Markdown",
        )
        _track(ctx, msg.message_id)
        return ASK_DEADLINE

    if dt <= datetime.now(tz):
        msg = await update.message.reply_text(
            "❌ Deadline must be in the future. Please try again."
        )
        _track(ctx, msg.message_id)
        return ASK_DEADLINE

    ctx.user_data[_DEADLINE] = dt.isoformat()

    # Build member selection keyboard
    members = db.get_all_members()
    keyboard = [
        [InlineKeyboardButton(
            f"{m['display_name']}" + (f" ({m['role']})" if m["role"] else ""),
            callback_data=f"pick_member:{m['telegram_id']}:{m['display_name']}"
        )]
        for m in members
    ]
    msg = await update.message.reply_text(
        "👤 Step 3/3 – Who is responsible?\n\nSelect a team member:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    _track(ctx, msg.message_id)
    return ASK_RESPONSIBLE


# ---------------------------------------------------------------------------
# Step 3 – pick responsible person (callback)
# ---------------------------------------------------------------------------

async def pick_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    _, tid_str, name = query.data.split(":", 2)
    ctx.user_data[_RESP_ID]   = int(tid_str)
    ctx.user_data[_RESP_NAME] = name
    _track(ctx, query.message.message_id)

    desc     = ctx.user_data[_DESC]
    deadline = datetime.fromisoformat(ctx.user_data[_DEADLINE])
    tz       = pytz.timezone(TIMEZONE)
    dl_str   = deadline.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
    mention  = f"[{name}](tg://user?id={tid_str})"

    summary = (
        f"📋 *Task Summary*\n\n"
        f"*Description:* {desc}\n"
        f"*Deadline:* {dl_str}\n"
        f"*Responsible:* {mention}\n\n"
        f"Confirm?"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data="task_confirm"),
        InlineKeyboardButton("❌ Cancel",  callback_data="task_cancel"),
    ]])

    msg = await query.message.reply_text(
        summary,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    _track(ctx, msg.message_id)
    return CONFIRM


# ---------------------------------------------------------------------------
# Step 4a – confirm
# ---------------------------------------------------------------------------

async def confirm_task(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id   = query.message.chat_id
    desc      = ctx.user_data[_DESC]
    deadline  = ctx.user_data[_DEADLINE]
    resp_id   = ctx.user_data[_RESP_ID]
    resp_name = ctx.user_data[_RESP_NAME]

    task_id = db.create_task(chat_id, desc, deadline, resp_id, resp_name)

    # Delete all intermediate messages
    await _delete_tracked(ctx, chat_id)

    # Post final summary (kept permanently)
    tz     = pytz.timezone(TIMEZONE)
    dt     = datetime.fromisoformat(deadline).astimezone(tz)
    dl_str = dt.strftime("%Y-%m-%d %H:%M %Z")
    mention = f"[{resp_name}](tg://user?id={resp_id})"

    final_text = (
        f"✅ *Task Created* (ID: {task_id})\n\n"
        f"📋 *Description:* {desc}\n"
        f"📅 *Deadline:* {dl_str}\n"
        f"👤 *Responsible:* {mention}\n"
        f"🔔 *Status:* Pending – reminder will fire at deadline."
    )
    sent = await ctx.bot.send_message(
        chat_id=chat_id,
        text=final_text,
        parse_mode="Markdown",
    )
    db.set_summary_msg(task_id, sent.message_id)

    # Schedule the reminder
    sched.schedule_reminder(task_id, dt, ctx.bot.token)

    _clear_state(ctx)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Step 4b – cancel
# ---------------------------------------------------------------------------

async def cancel_task(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    await _delete_tracked(ctx, chat_id)
    _clear_state(ctx)

    await ctx.bot.send_message(chat_id=chat_id, text="🚫 Task creation cancelled.")
    return ConversationHandler.END


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Allow /cancel at any point during the conversation."""
    chat_id = update.effective_chat.id
    await _delete_tracked(ctx, chat_id)
    _clear_state(ctx)
    await update.message.reply_text("🚫 Task creation cancelled.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# ConversationHandler (imported by bot.py)
# ---------------------------------------------------------------------------

task_conversation = ConversationHandler(
    entry_points=[CommandHandler("newtask", cmd_newtask)],
    states={
        ASK_DESC: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, recv_description)
        ],
        ASK_DEADLINE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, recv_deadline)
        ],
        ASK_RESPONSIBLE: [
            CallbackQueryHandler(pick_member, pattern=r"^pick_member:")
        ],
        CONFIRM: [
            CallbackQueryHandler(confirm_task, pattern=r"^task_confirm$"),
            CallbackQueryHandler(cancel_task,  pattern=r"^task_cancel$"),
        ],
    },
    fallbacks=[CommandHandler("cancel", cmd_cancel)],
    per_user=True,
    per_chat=True,
)

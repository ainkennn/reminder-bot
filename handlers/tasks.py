"""
handlers/tasks.py – Guided task-creation conversation (ru, 4 steps + edit flow).

States
------
ASK_TITLE       → user types task title
ASK_DESC        → user types description or skips
ASK_DEADLINE    → user types deadline
ASK_RESPONSIBLE → user picks team member
CONFIRM         → summary + confirm / edit
EDIT_MENU       → which field to edit?
"""

import logging
from datetime import datetime

import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import TIMEZONE, THREAD_ID
import db
import scheduler as sched

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------
ASK_TITLE       = 0
ASK_DESC        = 1
ASK_DEADLINE    = 2
ASK_RESPONSIBLE = 3
CONFIRM         = 4
EDIT_MENU       = 5

# ---------------------------------------------------------------------------
# user_data keys
# ---------------------------------------------------------------------------
_TITLE     = "task_title"
_DESC      = "task_desc"
_DEADLINE  = "task_deadline"
_RESP_ID   = "task_resp_id"
_RESP_NAME = "task_resp_name"
_MSG_IDS   = "task_msg_ids"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _track(ctx, msg_id):
    ctx.user_data.setdefault(_MSG_IDS, []).append(msg_id)


async def _delete_tracked(ctx, chat_id):
    for mid in ctx.user_data.get(_MSG_IDS, []):
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass
    ctx.user_data[_MSG_IDS] = []


def _clear_state(ctx):
    for key in (_TITLE, _DESC, _DEADLINE, _RESP_ID, _RESP_NAME, _MSG_IDS):
        ctx.user_data.pop(key, None)


def _back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back")]])


def _summary_text(ctx):
    tz     = pytz.timezone(TIMEZONE)
    dt     = datetime.fromisoformat(ctx.user_data[_DEADLINE]).astimezone(tz)
    dl_str = dt.strftime("%Y-%m-%d %H:%M")
    desc   = ctx.user_data.get(_DESC) or "—"
    return (
        f"📋 *Резюме*\n\n"
        f"*Название:* {ctx.user_data[_TITLE]}\n"
        f"*Описание:* {desc}\n"
        f"*Дедлайн:* {dl_str}\n"
        f"*Ответственный:* {ctx.user_data[_RESP_NAME]}"
    )


def _summary_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Утверждаем!", callback_data="task_confirm")],
        [InlineKeyboardButton("✏️ Изменить",   callback_data="task_edit")],
    ])


async def _show_summary(update_or_query, ctx, chat_id):
    """Send (or re-send) the summary message and track it."""
    text = _summary_text(ctx)
    if hasattr(update_or_query, 'message') and update_or_query.message:
        msg = await ctx.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=_summary_kb(),
            message_thread_id=THREAD_ID,
        )
    else:
        msg = await ctx.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=_summary_kb(),
            message_thread_id=THREAD_ID,
        )
    _track(ctx, msg.message_id)
    return msg


# ---------------------------------------------------------------------------
# Step 0 — /newtask
# ---------------------------------------------------------------------------

async def cmd_newtask(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not db.get_all_members():
        await update.message.reply_text(
            "⚠️ Нет участников команды.\nПопросите админа добавить через /addmember."
        )
        return ConversationHandler.END

    _clear_state(ctx)
    msg = await update.message.reply_text(
        "📝 *Новая задача*\n\nШаг 1/4 — Название задачи?",
        parse_mode="Markdown",
        message_thread_id=THREAD_ID,
    )
    _track(ctx, update.message.message_id)
    _track(ctx, msg.message_id)
    return ASK_TITLE


# ---------------------------------------------------------------------------
# Step 1 — receive title
# ---------------------------------------------------------------------------

async def recv_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data[_TITLE] = update.message.text.strip()
    _track(ctx, update.message.message_id)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬜ Оставить пустым", callback_data="desc_skip")],
        [InlineKeyboardButton("◀️ Назад",           callback_data="back_to_title")],
    ])
    msg = await update.message.reply_text(
        "📝 *Новая задача*\n\nШаг 2/4 — Описание задачи?",
        parse_mode="Markdown",
        reply_markup=keyboard,
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_DESC


# ---------------------------------------------------------------------------
# Step 2 — receive description (text or skip)
# ---------------------------------------------------------------------------

async def recv_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data[_DESC] = update.message.text.strip()
    _track(ctx, update.message.message_id)
    return await _ask_deadline(update, ctx)


async def cb_desc_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data[_DESC] = ""
    return await _ask_deadline(update, ctx)


async def cb_back_to_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    chat_id = update.effective_chat.id
    msg = await ctx.bot.send_message(
        chat_id=chat_id,
        text="📝 *Новая задача*\n\nШаг 1/4 — Название задачи?",
        parse_mode="Markdown",
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_TITLE


async def _ask_deadline(update, ctx) -> int:
    chat_id = update.effective_chat.id
    msg = await ctx.bot.send_message(
        chat_id=chat_id,
        text=(
            f"📅 *Новая задача*\n\nШаг 3/4 — Какой дедлайн?\n\n"
            f"Введите дату и время в формате:\n`ГГГГ-ММ-ДД ЧЧ:ММ`\n\n"
            f"_(Часовой пояс: {TIMEZONE})_"
        ),
        parse_mode="Markdown",
        reply_markup=_back_kb(),
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_DEADLINE


# ---------------------------------------------------------------------------
# Step 3 — receive deadline
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
            "❌ Неверный формат. Используйте `ГГГГ-ММ-ДД ЧЧ:ММ` (например `2025-12-31 09:00`).",
            parse_mode="Markdown",
            reply_markup=_back_kb(),
            message_thread_id=THREAD_ID,
        )
        _track(ctx, msg.message_id)
        return ASK_DEADLINE

    if dt <= datetime.now(tz):
        msg = await update.message.reply_text(
            "❌ Дедлайн должен быть в будущем. Попробуйте ещё раз.",
            reply_markup=_back_kb(),
            message_thread_id=THREAD_ID,
        )
        _track(ctx, msg.message_id)
        return ASK_DEADLINE

    ctx.user_data[_DEADLINE] = dt.isoformat()
    return await _ask_responsible(update, ctx)


# ---------------------------------------------------------------------------
# Step 4 — pick responsible
# ---------------------------------------------------------------------------

async def _ask_responsible(update, ctx) -> int:
    chat_id = update.effective_chat.id
    members = db.get_all_members()
    rows = [
        [InlineKeyboardButton(
            f"{m['display_name']}" + (f" ({m['role']})" if m["role"] else ""),
            callback_data=f"pick:{m['telegram_id']}:{m['display_name']}"
        )]
        for m in members
    ]
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_deadline")])
    msg = await ctx.bot.send_message(
        chat_id=chat_id,
        text="👤 *Новая задача*\n\nШаг 4/4 — Ответственный:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_RESPONSIBLE


async def cb_back_to_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return await _ask_deadline(update, ctx)


async def cb_pick_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, tid_str, name = query.data.split(":", 2)
    ctx.user_data[_RESP_ID]   = int(tid_str)
    ctx.user_data[_RESP_NAME] = name
    _track(ctx, query.message.message_id)

    await _show_summary(query, ctx, update.effective_chat.id)
    return CONFIRM


# ---------------------------------------------------------------------------
# Confirm / Edit
# ---------------------------------------------------------------------------

async def cb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id

    desc      = ctx.user_data.get(_DESC) or ""
    deadline  = ctx.user_data[_DEADLINE]
    resp_id   = ctx.user_data[_RESP_ID]
    resp_name = ctx.user_data[_RESP_NAME]
    title     = ctx.user_data[_TITLE]

    task_id = db.create_task(chat_id, desc, deadline, resp_id, resp_name, title)

    await _delete_tracked(ctx, chat_id)

    tz     = pytz.timezone(TIMEZONE)
    dt     = datetime.fromisoformat(deadline).astimezone(tz)
    dl_str = dt.strftime("%Y-%m-%d %H:%M")
    mention = f"[{resp_name}](tg://user?id={resp_id})"

    final_text = (
        f"✅ *Задача поставлена (ID: {task_id})*\n\n"
        f"*Название:* {title}\n"
        f"*Описание:* {desc or '—'}\n"
        f"*Дедлайн:* {dl_str}\n"
        f"*Ответственный:* {mention}"
    )
    sent = await ctx.bot.send_message(
        chat_id=chat_id,
        text=final_text,
        parse_mode="Markdown",
        message_thread_id=THREAD_ID,
    )
    db.set_summary_msg(task_id, sent.message_id)
    sched.schedule_reminder(task_id, dt, ctx.bot.token)

    _clear_state(ctx)
    return ConversationHandler.END


async def cb_edit_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Название",      callback_data="edit_title")],
        [InlineKeyboardButton("📄 Описание",      callback_data="edit_desc")],
        [InlineKeyboardButton("📅 Дедлайн",       callback_data="edit_deadline")],
        [InlineKeyboardButton("👤 Ответственный", callback_data="edit_responsible")],
    ])
    msg = await ctx.bot.send_message(
        chat_id=chat_id,
        text="✏️ *Что изменить?*",
        parse_mode="Markdown",
        reply_markup=keyboard,
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return EDIT_MENU


# Edit jumps — send user back to the right step, then return to summary after

async def cb_edit_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    chat_id = update.effective_chat.id
    msg = await ctx.bot.send_message(
        chat_id=chat_id,
        text="📝 Введите новое *название задачи*:",
        parse_mode="Markdown",
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    ctx.user_data["_after_edit"] = "summary"
    return ASK_TITLE


async def cb_edit_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    chat_id = update.effective_chat.id
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬜ Оставить пустым", callback_data="desc_skip")]
    ])
    msg = await ctx.bot.send_message(
        chat_id=chat_id,
        text="📄 Введите новое *описание задачи*:",
        parse_mode="Markdown",
        reply_markup=keyboard,
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    ctx.user_data["_after_edit"] = "summary"
    return ASK_DESC


async def cb_edit_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    chat_id = update.effective_chat.id
    msg = await ctx.bot.send_message(
        chat_id=chat_id,
        text=(
            f"📅 Введите новый *дедлайн*:\n\n"
            f"`ГГГГ-ММ-ДД ЧЧ:ММ`\n_(Часовой пояс: {TIMEZONE})_"
        ),
        parse_mode="Markdown",
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    ctx.user_data["_after_edit"] = "summary"
    return ASK_DEADLINE


async def cb_edit_responsible(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data["_after_edit"] = "summary"
    return await _ask_responsible(update, ctx)


# Override recv_title / recv_desc / recv_deadline to return to summary if editing

async def recv_title_smart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data[_TITLE] = update.message.text.strip()
    _track(ctx, update.message.message_id)

    if ctx.user_data.get("_after_edit") == "summary":
        ctx.user_data.pop("_after_edit", None)
        await _show_summary(update, ctx, update.effective_chat.id)
        return CONFIRM

    # Normal flow → go to desc
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬜ Оставить пустым", callback_data="desc_skip")],
        [InlineKeyboardButton("◀️ Назад",           callback_data="back_to_title")],
    ])
    msg = await update.message.reply_text(
        "📝 *Новая задача*\n\nШаг 2/4 — Описание задачи?",
        parse_mode="Markdown",
        reply_markup=keyboard,
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_DESC


async def recv_desc_smart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data[_DESC] = update.message.text.strip()
    _track(ctx, update.message.message_id)

    if ctx.user_data.get("_after_edit") == "summary":
        ctx.user_data.pop("_after_edit", None)
        await _show_summary(update, ctx, update.effective_chat.id)
        return CONFIRM

    return await _ask_deadline(update, ctx)


async def cb_desc_skip_smart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data[_DESC] = ""

    if ctx.user_data.get("_after_edit") == "summary":
        ctx.user_data.pop("_after_edit", None)
        await _show_summary(update, ctx, update.effective_chat.id)
        return CONFIRM

    return await _ask_deadline(update, ctx)


async def recv_deadline_smart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    _track(ctx, update.message.message_id)
    tz = pytz.timezone(TIMEZONE)

    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
        dt = tz.localize(dt)
    except ValueError:
        msg = await update.message.reply_text(
            "❌ Неверный формат. Используйте `ГГГГ-ММ-ДД ЧЧ:ММ`.",
            parse_mode="Markdown",
            message_thread_id=THREAD_ID,
        )
        _track(ctx, msg.message_id)
        return ASK_DEADLINE

    if dt <= datetime.now(tz):
        msg = await update.message.reply_text(
            "❌ Дедлайн должен быть в будущем. Попробуйте ещё раз.",
            message_thread_id=THREAD_ID,
        )
        _track(ctx, msg.message_id)
        return ASK_DEADLINE

    ctx.user_data[_DEADLINE] = dt.isoformat()

    if ctx.user_data.get("_after_edit") == "summary":
        ctx.user_data.pop("_after_edit", None)
        await _show_summary(update, ctx, update.effective_chat.id)
        return CONFIRM

    return await _ask_responsible(update, ctx)


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    await _delete_tracked(ctx, chat_id)
    _clear_state(ctx)
    await update.message.reply_text(
        "🚫 Создание задачи отменено.",
        message_thread_id=THREAD_ID,
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# ConversationHandler
# ---------------------------------------------------------------------------

task_conversation = ConversationHandler(
    entry_points=[CommandHandler("newtask", cmd_newtask)],
    states={
        ASK_TITLE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, recv_title_smart),
            CallbackQueryHandler(cb_back_to_title, pattern=r"^back_to_title$"),
        ],
        ASK_DESC: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, recv_desc_smart),
            CallbackQueryHandler(cb_desc_skip_smart, pattern=r"^desc_skip$"),
            CallbackQueryHandler(cb_back_to_title,   pattern=r"^back_to_title$"),
        ],
        ASK_DEADLINE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, recv_deadline_smart),
            CallbackQueryHandler(cb_back_to_deadline, pattern=r"^back$"),
        ],
        ASK_RESPONSIBLE: [
            CallbackQueryHandler(cb_pick_member,      pattern=r"^pick:"),
            CallbackQueryHandler(cb_back_to_deadline, pattern=r"^back_to_deadline$"),
            CallbackQueryHandler(cb_back_to_deadline, pattern=r"^back$"),
        ],
        CONFIRM: [
            CallbackQueryHandler(cb_confirm,   pattern=r"^task_confirm$"),
            CallbackQueryHandler(cb_edit_menu, pattern=r"^task_edit$"),
        ],
        EDIT_MENU: [
            CallbackQueryHandler(cb_edit_title,       pattern=r"^edit_title$"),
            CallbackQueryHandler(cb_edit_desc,        pattern=r"^edit_desc$"),
            CallbackQueryHandler(cb_edit_deadline,    pattern=r"^edit_deadline$"),
            CallbackQueryHandler(cb_edit_responsible, pattern=r"^edit_responsible$"),
        ],
    },
    fallbacks=[CommandHandler("cancel", cmd_cancel)],
    per_user=True,
    per_chat=True,
)

"""
handlers/tasks.py – Создание задачи (5 шагов + редактирование).

Шаги:
  1. Название
  2. Описание (или пропустить)
  3. Дедлайн
  4. Ответственный (команда или участник)
  5. Статус
  → Резюме → Подтвердить / Изменить
"""

import logging
from datetime import datetime

import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler,
    CommandHandler, MessageHandler, CallbackQueryHandler, filters,
)

from config import TIMEZONE, THREAD_ID
import db
import scheduler as sched

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Состояния диалога
# ---------------------------------------------------------------------------
ASK_TITLE       = 0
ASK_DESC        = 1
ASK_DEADLINE    = 2
ASK_RESPONSIBLE = 3
ASK_STATUS      = 4
CONFIRM         = 5
EDIT_MENU       = 6

# ---------------------------------------------------------------------------
# Ключи user_data
# ---------------------------------------------------------------------------
_TITLE      = "task_title"
_DESC       = "task_desc"
_DEADLINE   = "task_deadline"
_RESP_ID    = "task_resp_id"
_RESP_NAME  = "task_resp_name"
_STATUS     = "task_status"
_MSG_IDS    = "task_msg_ids"
_AFTER_EDIT = "_after_edit"

# ---------------------------------------------------------------------------
# Статусы
# ---------------------------------------------------------------------------
STATUSES = [
    ("🔵 В работе",      "в работе"),
    ("🟡 Согласование",  "согласование"),
    ("🔴 Риск",          "риск"),
    ("🟢 Согласовано",   "согласовано"),
]


# ---------------------------------------------------------------------------
# Вспомогательные функции
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
    for key in (_TITLE, _DESC, _DEADLINE, _RESP_ID, _RESP_NAME, _STATUS, _MSG_IDS, _AFTER_EDIT):
        ctx.user_data.pop(key, None)


def _back_kb(callback="back"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data=callback)]])


def _summary_text(ctx):
    from scheduler import fmt_dt
    tz      = pytz.timezone(TIMEZONE)
    dl_str  = fmt_dt(ctx.user_data[_DEADLINE])
    desc    = ctx.user_data.get(_DESC) or "—"
    status  = ctx.user_data.get(_STATUS) or "—"
    resp    = ctx.user_data[_RESP_NAME]
    return (
        f"📋 *Резюме*\n\n"
        f"*Название:* {ctx.user_data[_TITLE]}\n"
        f"*Описание:* {desc}\n"
        f"*Дедлайн:* {dl_str}\n"
        f"*Ответственный:* {resp}\n"
        f"*Статус:* {status}"
    )


def _summary_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Утверждаем!", callback_data="task_confirm")],
        [InlineKeyboardButton("✏️ Изменить",    callback_data="task_edit")],
    ])


async def _show_summary(ctx, chat_id):
    msg = await ctx.bot.send_message(
        chat_id=chat_id,
        text=_summary_text(ctx),
        parse_mode="Markdown",
        reply_markup=_summary_kb(),
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)


# ---------------------------------------------------------------------------
# Шаг 0 — /newtask
# ---------------------------------------------------------------------------

async def cmd_newtask(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not db.get_all_members():
        await update.message.reply_text(
            "⚠️ Нет участников команды.\nПопросите админа добавить через /addmember."
        )
        return ConversationHandler.END

    _clear_state(ctx)
    msg = await update.message.reply_text(
        "📝 *Новая задача*\n\nШаг 1/5 — Название задачи?",
        parse_mode="Markdown",
        message_thread_id=THREAD_ID,
    )
    _track(ctx, update.message.message_id)
    _track(ctx, msg.message_id)
    return ASK_TITLE


# ---------------------------------------------------------------------------
# Шаг 1 — Название
# ---------------------------------------------------------------------------

async def recv_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data[_TITLE] = update.message.text.strip()
    _track(ctx, update.message.message_id)

    if ctx.user_data.get(_AFTER_EDIT) == "summary":
        ctx.user_data.pop(_AFTER_EDIT)
        await _show_summary(ctx, update.effective_chat.id)
        return CONFIRM

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬜ Оставить пустым", callback_data="desc_skip")],
        [InlineKeyboardButton("◀️ Назад",           callback_data="back_to_title")],
    ])
    msg = await update.message.reply_text(
        "📝 *Новая задача*\n\nШаг 2/5 — Описание задачи?",
        parse_mode="Markdown",
        reply_markup=keyboard,
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_DESC


# ---------------------------------------------------------------------------
# Шаг 2 — Описание
# ---------------------------------------------------------------------------

async def recv_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data[_DESC] = update.message.text.strip()
    _track(ctx, update.message.message_id)

    if ctx.user_data.get(_AFTER_EDIT) == "summary":
        ctx.user_data.pop(_AFTER_EDIT)
        await _show_summary(ctx, update.effective_chat.id)
        return CONFIRM

    return await _ask_deadline(update, ctx)


async def cb_desc_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data[_DESC] = ""

    if ctx.user_data.get(_AFTER_EDIT) == "summary":
        ctx.user_data.pop(_AFTER_EDIT)
        await _show_summary(ctx, update.effective_chat.id)
        return CONFIRM

    return await _ask_deadline(update, ctx)


async def cb_back_to_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    msg = await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📝 *Новая задача*\n\nШаг 1/5 — Название задачи?",
        parse_mode="Markdown",
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_TITLE


# ---------------------------------------------------------------------------
# Шаг 3 — Дедлайн
# ---------------------------------------------------------------------------

async def _ask_deadline(update, ctx) -> int:
    msg = await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            f"📅 *Новая задача*\n\nШаг 3/5 — Какой дедлайн?\n\n"
            f"Введите дату и время в формате:\n`ГГГГ-ММ-ДД ЧЧ:ММ`\n\n"
            f"_(Часовой пояс: {TIMEZONE})_"
        ),
        parse_mode="Markdown",
        reply_markup=_back_kb("back_to_desc"),
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_DEADLINE


async def recv_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
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
            reply_markup=_back_kb("back_to_desc"),
            message_thread_id=THREAD_ID,
        )
        _track(ctx, msg.message_id)
        return ASK_DEADLINE

    if dt <= datetime.now(tz):
        msg = await update.message.reply_text(
            "❌ Дедлайн должен быть в будущем. Попробуйте ещё раз.",
            reply_markup=_back_kb("back_to_desc"),
            message_thread_id=THREAD_ID,
        )
        _track(ctx, msg.message_id)
        return ASK_DEADLINE

    ctx.user_data[_DEADLINE] = dt.isoformat()

    if ctx.user_data.get(_AFTER_EDIT) == "summary":
        ctx.user_data.pop(_AFTER_EDIT)
        await _show_summary(ctx, update.effective_chat.id)
        return CONFIRM

    return await _ask_responsible(update, ctx)


async def cb_back_to_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬜ Оставить пустым", callback_data="desc_skip")],
        [InlineKeyboardButton("◀️ Назад",           callback_data="back_to_title")],
    ])
    msg = await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📝 *Новая задача*\n\nШаг 2/5 — Описание задачи?",
        parse_mode="Markdown",
        reply_markup=keyboard,
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_DESC


# ---------------------------------------------------------------------------
# Шаг 4 — Ответственный
# ---------------------------------------------------------------------------

async def _ask_responsible(update, ctx) -> int:
    members = db.get_all_members()
    rows = [
        [InlineKeyboardButton(
            f"{m['display_name']}" + (f" ({m['role']})" if m["role"] else ""),
            callback_data=f"pick:{m['telegram_id']}:{m['display_name']}"
        )]
        for m in members
    ]
    # Постоянная кнопка "Команда"
    rows.append([InlineKeyboardButton("👥 Команда", callback_data="pick:0:Команда")])
    rows.append([InlineKeyboardButton("◀️ Назад",   callback_data="back_to_deadline")])

    msg = await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text="👤 *Новая задача*\n\nШаг 4/5 — Ответственный:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_RESPONSIBLE


async def cb_pick_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, tid_str, name = query.data.split(":", 2)
    ctx.user_data[_RESP_ID]   = int(tid_str)
    ctx.user_data[_RESP_NAME] = name
    _track(ctx, query.message.message_id)

    if ctx.user_data.get(_AFTER_EDIT) == "summary":
        ctx.user_data.pop(_AFTER_EDIT)
        await _show_summary(ctx, update.effective_chat.id)
        return CONFIRM

    return await _ask_status(update, ctx)


async def cb_back_to_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return await _ask_deadline(update, ctx)


# ---------------------------------------------------------------------------
# Шаг 5 — Статус
# ---------------------------------------------------------------------------

async def _ask_status(update, ctx) -> int:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"status:{value}")]
        for label, value in STATUSES
    ]
    rows.append([InlineKeyboardButton("⬜ Оставить пустым", callback_data="status_skip")])
    rows.append([InlineKeyboardButton("◀️ Назад",           callback_data="back_to_responsible")])

    msg = await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📝 *Новая задача*\n\nШаг 5/5 — Статус:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_STATUS


async def cb_pick_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    value = query.data.split(":", 1)[1]
    # Find the label for this value to store display text
    label = next((l for l, v in STATUSES if v == value), value)
    ctx.user_data[_STATUS] = label
    _track(ctx, query.message.message_id)

    if ctx.user_data.get(_AFTER_EDIT) == "summary":
        ctx.user_data.pop(_AFTER_EDIT)

    await _show_summary(ctx, update.effective_chat.id)
    return CONFIRM


async def cb_status_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data[_STATUS] = ""
    _track(ctx, update.callback_query.message.message_id)

    if ctx.user_data.get(_AFTER_EDIT) == "summary":
        ctx.user_data.pop(_AFTER_EDIT)

    await _show_summary(ctx, update.effective_chat.id)
    return CONFIRM


async def cb_back_to_responsible(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return await _ask_responsible(update, ctx)


# ---------------------------------------------------------------------------
# Подтверждение
# ---------------------------------------------------------------------------

async def cb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id

    title     = ctx.user_data[_TITLE]
    desc      = ctx.user_data.get(_DESC) or ""
    deadline  = ctx.user_data[_DEADLINE]
    resp_id   = ctx.user_data[_RESP_ID]
    resp_name = ctx.user_data[_RESP_NAME]
    status    = ctx.user_data.get(_STATUS) or ""

    task_id = db.create_task(chat_id, desc, deadline, resp_id, resp_name, title, status)
    await _delete_tracked(ctx, chat_id)

    from scheduler import fmt_dt
    dl_str = fmt_dt(deadline)

    # Mention only if real user (not Команда)
    if resp_id == 0:
        resp_display = "👥 Команда"
    else:
        resp_display = f"[{resp_name}](tg://user?id={resp_id})"

    final_text = (
        f"✅ *Задача поставлена (ID: {task_id})*\n\n"
        f"*Название:* {title}\n"
        f"*Описание:* {desc or '—'}\n"
        f"*Дедлайн:* {dl_str}\n"
        f"*Ответственный:* {resp_display}\n"
        f"*Статус:* {status or '—'}"
    )
    sent = await ctx.bot.send_message(
        chat_id=chat_id,
        text=final_text,
        parse_mode="Markdown",
        message_thread_id=THREAD_ID,
    )
    db.set_summary_msg(task_id, sent.message_id)

    tz = pytz.timezone(TIMEZONE)
    dt = datetime.fromisoformat(deadline).astimezone(tz)
    sched.schedule_reminder(task_id, dt, ctx.bot.token)

    _clear_state(ctx)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Меню редактирования
# ---------------------------------------------------------------------------

async def cb_edit_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Название",      callback_data="edit_title")],
        [InlineKeyboardButton("📄 Описание",      callback_data="edit_desc")],
        [InlineKeyboardButton("📅 Дедлайн",       callback_data="edit_deadline")],
        [InlineKeyboardButton("👤 Ответственный", callback_data="edit_responsible")],
        [InlineKeyboardButton("🔵 Статус",        callback_data="edit_status")],
    ])
    msg = await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✏️ *Что изменить?*",
        parse_mode="Markdown",
        reply_markup=keyboard,
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return EDIT_MENU


async def cb_edit_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data[_AFTER_EDIT] = "summary"
    msg = await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📝 Введите новое *название задачи*:",
        parse_mode="Markdown",
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_TITLE


async def cb_edit_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data[_AFTER_EDIT] = "summary"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬜ Оставить пустым", callback_data="desc_skip")]
    ])
    msg = await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📄 Введите новое *описание задачи*:",
        parse_mode="Markdown",
        reply_markup=keyboard,
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_DESC


async def cb_edit_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data[_AFTER_EDIT] = "summary"
    msg = await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📅 Введите новый *дедлайн*:\n\n`ГГГГ-ММ-ДД ЧЧ:ММ`\n_(Часовой пояс: {TIMEZONE})_",
        parse_mode="Markdown",
        message_thread_id=THREAD_ID,
    )
    _track(ctx, msg.message_id)
    return ASK_DEADLINE


async def cb_edit_responsible(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data[_AFTER_EDIT] = "summary"
    return await _ask_responsible(update, ctx)


async def cb_edit_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    ctx.user_data[_AFTER_EDIT] = "summary"
    return await _ask_status(update, ctx)


# ---------------------------------------------------------------------------
# Отмена
# ---------------------------------------------------------------------------

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await _delete_tracked(ctx, update.effective_chat.id)
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
            MessageHandler(filters.TEXT & ~filters.COMMAND, recv_title),
            CallbackQueryHandler(cb_back_to_title, pattern=r"^back_to_title$"),
        ],
        ASK_DESC: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, recv_desc),
            CallbackQueryHandler(cb_desc_skip,     pattern=r"^desc_skip$"),
            CallbackQueryHandler(cb_back_to_title, pattern=r"^back_to_title$"),
        ],
        ASK_DEADLINE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, recv_deadline),
            CallbackQueryHandler(cb_back_to_desc,  pattern=r"^back_to_desc$"),
            CallbackQueryHandler(cb_back_to_desc,  pattern=r"^back$"),
        ],
        ASK_RESPONSIBLE: [
            CallbackQueryHandler(cb_pick_member,      pattern=r"^pick:"),
            CallbackQueryHandler(cb_back_to_deadline, pattern=r"^back_to_deadline$"),
        ],
        ASK_STATUS: [
            CallbackQueryHandler(cb_pick_status,        pattern=r"^status:"),
            CallbackQueryHandler(cb_status_skip,        pattern=r"^status_skip$"),
            CallbackQueryHandler(cb_back_to_responsible, pattern=r"^back_to_responsible$"),
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
            CallbackQueryHandler(cb_edit_status,      pattern=r"^edit_status$"),
        ],
    },
    fallbacks=[CommandHandler("cancel", cmd_cancel)],
    per_user=True,
    per_chat=True,
)

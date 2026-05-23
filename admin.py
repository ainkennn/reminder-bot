"""
handlers/admin.py – Commands available to admins only.

Commands
--------
/addmember  <telegram_id> <display_name> [role]
/delmember  <telegram_id>
/listmembers
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from config import ADMIN_IDS
import db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Guard decorator
# ---------------------------------------------------------------------------

def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None or user.id not in ADMIN_IDS:
            await update.message.reply_text(
                "⛔ This command is restricted to admins."
            )
            return
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper


# ---------------------------------------------------------------------------
# /addmember
# ---------------------------------------------------------------------------

@admin_only
async def cmd_addmember(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /addmember <telegram_id> <display_name> [role]"""
    args = ctx.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: `/addmember <telegram_id> <display_name> [role]`",
            parse_mode="Markdown",
        )
        return

    try:
        tid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ `telegram_id` must be an integer.", parse_mode="Markdown")
        return

    name = args[1]
    role = " ".join(args[2:]) if len(args) > 2 else ""

    db.upsert_member(tid, name, role)
    role_str = f" _(role: {role})_" if role else ""
    await update.message.reply_text(
        f"✅ Member saved: *{name}*{role_str} (ID: `{tid}`)",
        parse_mode="Markdown",
    )
    logger.info("Admin %s upserted member %s (%s)", update.effective_user.id, tid, name)


# ---------------------------------------------------------------------------
# /delmember
# ---------------------------------------------------------------------------

@admin_only
async def cmd_delmember(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /delmember <telegram_id>"""
    args = ctx.args or []
    if not args:
        await update.message.reply_text("Usage: `/delmember <telegram_id>`", parse_mode="Markdown")
        return

    try:
        tid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ `telegram_id` must be an integer.", parse_mode="Markdown")
        return

    removed = db.delete_member(tid)
    if removed:
        await update.message.reply_text(f"🗑️ Member `{tid}` removed.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ No member with ID `{tid}` found.", parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /listmembers
# ---------------------------------------------------------------------------

@admin_only
async def cmd_listmembers(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    members = db.get_all_members()
    if not members:
        await update.message.reply_text("📋 No team members registered yet.\nUse /addmember to add some.")
        return

    lines = ["👥 *Team Members*\n"]
    for m in members:
        role_str = f" – _{m['role']}_" if m["role"] else ""
        lines.append(f"• *{m['display_name']}*{role_str} (`{m['telegram_id']}`)")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Handler list (imported by bot.py)
# ---------------------------------------------------------------------------

handlers = [
    CommandHandler("addmember",  cmd_addmember),
    CommandHandler("delmember",  cmd_delmember),
    CommandHandler("listmembers", cmd_listmembers),
]

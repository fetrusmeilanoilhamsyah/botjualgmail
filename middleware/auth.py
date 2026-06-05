"""
middleware/auth.py - Autentikasi Admin
"""
import logging
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes
from config import ADMIN_IDS

logger = logging.getLogger(__name__)


def admin_only(func):
    """Decorator: hanya admin yang bisa menjalankan fungsi ini."""
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            if update.callback_query:
                await update.callback_query.answer("⛔ Kamu bukan admin!", show_alert=True)
            elif update.message:
                await update.message.reply_text("⛔ Akses ditolak. Kamu bukan admin.")
            logger.warning("[auth] Akses ditolak untuk user %d", user.id if user else -1)
            return
        return await func(update, ctx, *args, **kwargs)
    return wrapper

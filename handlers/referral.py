"""
handlers/referral.py - Sistem Referral
- Link unik: t.me/bot?start=ref_USERID
- Bonus Rp500 per referral valid
- Anti-bot: jika ≥5 user daftar dalam 10 detik → ban
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database.db_async import adb
from config import REFERRAL_BONUS, BOT_USERNAME

logger = logging.getLogger(__name__)


def fmt_rupiah(n: int) -> str:
    return f"Rp{n:,.0f}".replace(",", ".")


async def show_referral(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = update.effective_user
    await q.answer()

    stats   = await adb.get_referral_stats(user.id)
    is_ban  = stats.get("referral_banned", 0)
    count   = stats.get("referral_count", 0)
    saldo   = await adb.get_saldo(user.id)

    bot_username = ctx.bot.username or BOT_USERNAME
    link = f"https://t.me/{bot_username}?start=ref_{user.id}"

    if is_ban:
        teks = (
            f"<tg-emoji emoji-id=\"6156448083916887235\">📋</tg-emoji> <b>REFERRAL DINONAKTIFKAN</b>\n\n"
            f"<blockquote>Akun referral Anda telah dinonaktifkan karena terdeteksi aktivitas mencurigakan (bot/spam).</blockquote>\n"
            f"Hubungi admin jika ini merupakan kesalahan."
        )
        kb = [[InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")]]
    else:
        teks = (
            f"<tg-emoji emoji-id=\"6156448083916887235\">📋</tg-emoji> <b>PROGRAM REFERRAL</b>\n\n"
            f"Dapatkan <b>{fmt_rupiah(REFERRAL_BONUS)}</b> untuk setiap teman yang mendaftar via link kamu!\n\n"
            f"<blockquote>• Total Ref : <b>{count} Orang</b>\n"
            f"• Saldo     : <b>{fmt_rupiah(saldo)}</b></blockquote>\n"
            f"<b>Link Referral Kamu:</b>\n"
            f"<code>{link}</code>\n\n"
            f"<i>Anti-bot aktif. Penyalahgunaan sistem akan mengakibatkan pemblokiran fitur referral otomatis.</i>"
        )
        kb = [
            [InlineKeyboardButton("Bagikan Link", url=f"https://t.me/share/url?url={link}&text=Beli+Gmail+murah+disini!", style="primary")],
            [InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")],
        ]

    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


def register(app):
    app.add_handler(CallbackQueryHandler(show_referral, pattern="^referral$"))

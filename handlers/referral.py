"""
handlers/referral.py - Sistem Referral
- Link unik: t.me/bot?start=ref_USERID
- Bonus Rp500 per referral valid
- Anti-bot: jika ≥5 user daftar dalam 10 detik → ban
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database import db
from config import REFERRAL_BONUS, BOT_USERNAME

logger = logging.getLogger(__name__)


def fmt_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")


async def show_referral(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = update.effective_user
    await q.answer()

    stats   = db.get_referral_stats(user.id)
    is_ban  = stats.get("referral_banned", 0)
    count   = stats.get("referral_count", 0)
    saldo   = db.get_saldo(user.id)

    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user.id}"

    if is_ban:
        teks = (
            "<b>Fitur Referral Dinonaktifkan</b>\n\n"
            "Akun referral kamu telah dinonaktifkan karena terdeteksi "
            "aktivitas mencurigakan (bot/spam).\n\n"
            "Jika ini kesalahan, hubungi admin."
        )
        kb = [[InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")]]
    else:
        teks = (
            "<b>Program Referral</b>\n\n"
            f"Dapatkan <b>{fmt_rupiah(REFERRAL_BONUS)}</b> untuk setiap teman "
            "yang mendaftar via link kamu!\n\n"
            f"<b>Link Referral Kamu:</b>\n"
            f"<code>{link}</code>\n\n"
            f"<b>Statistik:</b>\n"
            f"   Total referral: {count} orang\n"
            f"   Saldo kamu saat ini: {fmt_rupiah(saldo)}\n\n"
            "<i>Anti-bot aktif: jika terdeteksi spam, fitur akan dinonaktifkan otomatis.</i>"
        )
        kb = [
            [InlineKeyboardButton("Bagikan Link", url=f"https://t.me/share/url?url={link}&text=Beli+Gmail+murah+disini!", style="primary")],
            [InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")],
        ]

    await q.edit_message_text(teks, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(kb),
                               disable_web_page_preview=True)


def register(app):
    app.add_handler(CallbackQueryHandler(show_referral, pattern="^referral$"))

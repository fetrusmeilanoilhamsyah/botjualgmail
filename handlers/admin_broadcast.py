"""
handlers/admin_broadcast.py - Broadcast Pesan ke Semua User
Commands: /broadcast
"""
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from database import db
from middleware.auth import admin_only
from config import BROADCAST_DELAY

logger = logging.getLogger(__name__)


@admin_only
async def cmd_broadcast_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Mulai proses broadcast."""
    user = update.effective_user
    db.set_session(user.id, "admin_broadcast_preview", {})

    teks = (
        "Broadcast Pesan\n\n"
        "Ketik pesan yang ingin dikirim ke semua user.\n"
        "Mendukung: HTML bold, italic, kode.\n\n"
        "Contoh:\n"
        "<code>Halo semua! Ada promo stok baru hari ini</code>"
    )
    kb = [[InlineKeyboardButton("Batal", callback_data="admin_panel", style="danger")]]
    await update.message.reply_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_broadcast_preview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Preview pesan sebelum dikirim."""
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "admin_broadcast_preview":
        return

    pesan = update.message.text.strip()
    db.set_session(user.id, "admin_broadcast_confirm", {"pesan": pesan})

    total_user = db.get_total_users()
    preview = (
        f"Preview Broadcast\n\n"
        f"{'─'*30}\n"
        f"{pesan}\n"
        f"{'─'*30}\n\n"
        f"Akan dikirim ke: <b>{total_user} user</b>\n\n"
        "Kirim broadcast?"
    )
    kb = [
        [InlineKeyboardButton("YA, KIRIM SEKARANG", callback_data="admin_broadcast_execute", style="primary")],
        [InlineKeyboardButton("Edit Ulang",         callback_data="admin_broadcast_reedit", style="danger")],
        [InlineKeyboardButton("Batal",              callback_data="admin_panel", style="danger")],
    ]
    await update.message.reply_text(preview, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_broadcast_reedit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db.set_session(update.effective_user.id, "admin_broadcast_preview", {})
    await q.edit_message_text(
        "Ketik ulang pesan broadcast:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Batal", callback_data="admin_panel", style="danger")]])
    )


@admin_only
async def admin_broadcast_execute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Eksekusi broadcast ke semua user."""
    q       = update.callback_query
    user    = update.effective_user
    session = db.get_session(user.id)
    await q.answer("Mulai broadcast...")

    if session["state"] != "admin_broadcast_confirm":
        await q.edit_message_text("Session expired. Mulai ulang dengan /broadcast",
                                  reply_markup=InlineKeyboardMarkup([[
                                      InlineKeyboardButton("Kembali", callback_data="admin_panel", style="danger")
                                  ]]))
        return

    pesan = session["data"]["pesan"]
    db.clear_session(user.id)

    user_ids  = db.get_all_user_ids()
    total     = len(user_ids)
    sukses    = 0
    gagal     = 0

    await q.edit_message_text(f"Mengirim ke {total} user...\n\nMohon tunggu.")

    for uid in user_ids:
        try:
            await ctx.bot.send_message(chat_id=uid, text=pesan, parse_mode="HTML")
            sukses += 1
        except Exception as e:
            gagal += 1
            logger.debug("[broadcast] Gagal ke %d: %s", uid, e)
        await asyncio.sleep(BROADCAST_DELAY)

    db.log_broadcast(user.id, pesan, sukses, gagal)

    await q.message.reply_text(
        f"Broadcast Selesai!\n\n"
        f"Berhasil: {sukses} user\n"
        f"Gagal   : {gagal} user\n"
        f"Total   : {total} user",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Panel Admin", callback_data="admin_panel", style="primary")
        ]])
    )


def register(app):
    app.add_handler(CommandHandler("broadcast", cmd_broadcast_start))
    app.add_handler(CallbackQueryHandler(admin_broadcast_reedit,   pattern="^admin_broadcast_reedit$"))
    app.add_handler(CallbackQueryHandler(admin_broadcast_execute,  pattern="^admin_broadcast_execute$"))

"""
handlers/admin_broadcast.py - Broadcast Pesan ke Semua User
Commands: /broadcast
"""
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from database import db
from middleware.auth import admin_only
from config import BROADCAST_DELAY

logger = logging.getLogger(__name__)


@admin_only
async def cmd_broadcast_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Mulai proses broadcast."""
    user = update.effective_user

    # Hapus pesan command admin
    try:
        await update.message.delete()
    except Exception:
        pass

    teks = (
        "<b>Broadcast Pesan - Warung Gmail</b>\n\n"
        "Ketik pesan yang ingin dikirim ke seluruh pengguna.\n"
        "Mendukung tag HTML seperti: <b>tebal</b>, <i>miring</i>, <code>kode</code>.\n\n"
        "Silakan ketik pesan Anda:"
    )
    kb = [[InlineKeyboardButton("Batal", callback_data="admin_panel", style="danger")]]
    msg = await ctx.bot.send_message(chat_id=user.id, text=teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    db.set_session(user.id, "admin_broadcast_preview", {"menu_msg_id": msg.message_id})


@admin_only
async def admin_broadcast_preview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Preview pesan sebelum dikirim."""
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "admin_broadcast_preview":
        return

    # Hapus pesan input admin
    try:
        await update.message.delete()
    except Exception:
        pass

    pesan = update.message.text.strip()
    menu_msg_id = session["data"].get("menu_msg_id")

    db.set_session(user.id, "admin_broadcast_confirm", {"pesan": pesan, "menu_msg_id": menu_msg_id})

    total_user = db.get_total_users()
    preview = (
        f"<b>Preview Broadcast - Warung Gmail</b>\n\n"
        f"{'─'*30}\n"
        f"{pesan}\n"
        f"{'─'*30}\n\n"
        f"Target Penerima: <b>{total_user} User</b>\n\n"
        "Kirim broadcast sekarang?"
    )
    kb = [
        [InlineKeyboardButton("YA, KIRIM SEKARANG", callback_data="admin_broadcast_execute", style="primary")],
        [InlineKeyboardButton("Edit Ulang",         callback_data="admin_broadcast_reedit", style="danger")],
        [InlineKeyboardButton("Batal",              callback_data="admin_panel", style="danger")],
    ]

    if menu_msg_id:
        try:
            await ctx.bot.edit_message_text(
                chat_id=user.id,
                message_id=menu_msg_id,
                text=preview,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            return
        except Exception:
            pass

    msg = await ctx.bot.send_message(
        chat_id=user.id,
        text=preview,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    db.set_session(user.id, "admin_broadcast_confirm", {"pesan": pesan, "menu_msg_id": msg.message_id})


@admin_only
async def admin_broadcast_reedit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    user = update.effective_user
    db.set_session(user.id, "admin_broadcast_preview", {"menu_msg_id": q.message.message_id})
    await q.edit_message_text(
        "Ketik ulang pesan broadcast Anda:",
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
    menu_msg_id = session["data"].get("menu_msg_id")
    db.clear_session(user.id)

    user_ids  = db.get_all_user_ids()
    total     = len(user_ids)
    sukses    = 0
    gagal     = 0

    await q.edit_message_text(f"Mengirim pesan ke {total} user...\n\nMohon tunggu proses selesai.")

    for uid in user_ids:
        try:
            await ctx.bot.send_message(chat_id=uid, text=pesan, parse_mode="HTML")
            sukses += 1
        except Exception as e:
            gagal += 1
            logger.debug("[broadcast] Gagal ke %d: %s", uid, e)
        await asyncio.sleep(BROADCAST_DELAY)

    db.log_broadcast(user.id, pesan, sukses, gagal)

    teks_selesai = (
        f"<b>Broadcast Selesai!</b>\n\n"
        f"• Sukses: {sukses} user\n"
        f"• Gagal: {gagal} user\n"
        f"• Total: {total} user"
    )
    kb = [[InlineKeyboardButton("Panel Admin", callback_data="admin_panel", style="primary")]]

    if menu_msg_id:
        try:
            await ctx.bot.edit_message_text(
                chat_id=user.id,
                message_id=menu_msg_id,
                text=teks_selesai,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            return
        except Exception:
            pass
    await q.edit_message_text(teks_selesai, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


def register(app):
    app.add_handler(CommandHandler("broadcast", cmd_broadcast_start))
    app.add_handler(CallbackQueryHandler(admin_broadcast_reedit,   pattern="^admin_broadcast_reedit$"))
    app.add_handler(CallbackQueryHandler(admin_broadcast_execute,  pattern="^admin_broadcast_execute$"))

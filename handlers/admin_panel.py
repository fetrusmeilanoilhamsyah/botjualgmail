"""
handlers/admin_panel.py - Panel Utama Admin
Tombol terpusat untuk semua fitur admin
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from database.db_async import adb
from middleware.auth import admin_only

logger = logging.getLogger(__name__)


@admin_only
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _show_panel(update, ctx)


@admin_only
async def cb_admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await _show_panel(update, ctx)


async def _show_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = await adb.get_admin_stats()

    teks = (
        f"<b>Panel Admin - Bot Jual Gmail</b>\n\n"
        f"Total User: {s['total_user']:,}\n"
        f"Transaksi Hari Ini: {s['trx_hari_ini']}\n"
        f"Klaim Garansi Pending: {s['garansi_pending']}\n\n"
        "Pilih menu:"
    )

    kb = [
        [
            InlineKeyboardButton("Statistik",          callback_data="admin_stat", style="primary"),
            InlineKeyboardButton("Kelola Stok",         callback_data="admin_stok_refresh", style="primary"),
        ],
        [
            InlineKeyboardButton("Paket & Harga",       callback_data="admin_paket", style="primary"),
            InlineKeyboardButton("Klaim Garansi",       callback_data="admin_garansi_list", style="primary"),
        ],
        [
            InlineKeyboardButton("Broadcast",           callback_data="admin_broadcast_start_cb", style="primary"),
        ],
        [InlineKeyboardButton("Menu Utama Bot",         callback_data="menu_utama", style="danger")],
    ]

    markup = InlineKeyboardMarkup(kb)

    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, markup)


def register(app):
    app.add_handler(CommandHandler("admin",  cmd_admin))
    app.add_handler(CommandHandler("panel",  cmd_admin))
    app.add_handler(CallbackQueryHandler(cb_admin_panel, pattern="^admin_panel$"))

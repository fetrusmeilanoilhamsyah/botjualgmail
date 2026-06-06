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
        f"<b>⚙️ CONTROL PANEL ADMIN</b>\n\n"
        f"<blockquote>• Total User  : <b>{s['total_user']:,}</b>\n"
        f"• Trx Hari Ini: <b>{s['trx_hari_ini']}</b>\n"
        f"• Garansi Pend: <b>{s['garansi_pending']}</b></blockquote>"
    )

    kb = [
        [
            InlineKeyboardButton(
                "Statistik",
                callback_data="admin_stat",
                style="primary",
                icon_custom_emoji_id="5244837092042750681"
            ),
            InlineKeyboardButton(
                "Kelola Stok",
                callback_data="admin_stok_refresh",
                style="primary",
                icon_custom_emoji_id="6156673548225090260"
            ),
        ],
        [
            InlineKeyboardButton(
                "Paket & Harga",
                callback_data="admin_paket",
                style="primary",
                icon_custom_emoji_id="6156923364997862692"
            ),
            InlineKeyboardButton(
                "Klaim Garansi",
                callback_data="admin_garansi_list",
                style="primary",
                icon_custom_emoji_id="6158892349805040268"
            ),
        ],
        [
            InlineKeyboardButton(
                "Broadcast (Kirim Pesan)",
                callback_data="admin_broadcast_start_cb",
                style="primary",
                icon_custom_emoji_id="6159148926856336305"
            ),
        ],
        [
            InlineKeyboardButton(
                "Menu Utama Bot",
                callback_data="menu_utama",
                style="danger",
                icon_custom_emoji_id="6003735582495216112"
            )
        ],
    ]

    markup = InlineKeyboardMarkup(kb)

    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, markup)


def register(app):
    app.add_handler(CommandHandler("admin",  cmd_admin))
    app.add_handler(CommandHandler("panel",  cmd_admin))
    app.add_handler(CallbackQueryHandler(cb_admin_panel, pattern="^admin_panel$"))

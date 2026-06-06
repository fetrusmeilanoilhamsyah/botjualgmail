"""
handlers/admin_stat.py - Dashboard Statistik Admin
Commands: /stat, /stat_admin
Panel: callback_data=admin_stat
"""
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from database.db_async import adb
from middleware.auth import admin_only

logger = logging.getLogger(__name__)


def fmt_rupiah(n: int) -> str:
    return f"Rp{n:,.0f}".replace(",", ".")


def fmt_dt(iso_str: str) -> str:
    try:
        return datetime.fromisoformat(iso_str).strftime("%d %b %Y %H:%M")
    except Exception:
        return str(iso_str)[:16]


@admin_only
async def cmd_stat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _show_stat(update, ctx)


@admin_only
async def cb_stat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("🔄 Memuat statistik...")
    await _show_stat(update, ctx)


async def _show_stat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s   = await adb.get_admin_stats()
    now = datetime.now().strftime("%d %b %Y %H:%M")

    teks = (
        f"<b>📊 STATISTIK SISTEM</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Total User      :</b> {s['total_user']:,}\n"
        f"📦 <b>Stok Global     :</b> {s['stok_tersedia']:,} Pcs\n\n"
        f"💳 <b>FINANSIAL</b>\n"
        f"• Saldo Beredar   : {fmt_rupiah(s['total_saldo'])}\n"
        f"• Topup Hari Ini  : {fmt_rupiah(s['topup_hari_ini'])}\n"
        f"• Omset Hari Ini  : {fmt_rupiah(s['omset_hari_ini'])}\n"
        f"• Total Omset     : {fmt_rupiah(s['omset_total'])}\n\n"
        f"🛒 <b>TRANSAKSI & GARANSI</b>\n"
        f"• Trx Hari Ini    : {s['trx_hari_ini']:,}\n"
        f"• Total Trx       : {s['total_trx']:,}\n"
        f"• Klaim Pending   : {s['garansi_pending']:,}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>🕒 Update: {now}</i>"
    )

    kb = [
        [
            InlineKeyboardButton("Refresh",        callback_data="admin_stat", style="primary"),
            InlineKeyboardButton("Panel Admin",     callback_data="admin_panel", style="danger"),
        ]
    ]


    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


def register(app):
    app.add_handler(CommandHandler(["stat", "stat_admin"], cmd_stat))
    app.add_handler(CallbackQueryHandler(cb_stat, pattern="^admin_stat$"))

"""
handlers/admin_stat.py - Dashboard Statistik Admin
Commands: /stat, /stat_admin
Panel: callback_data=admin_stat
"""
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from database import db
from middleware.auth import admin_only

logger = logging.getLogger(__name__)


def fmt_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")


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
    s   = db.get_admin_stats()
    now = datetime.now().strftime("%d %b %Y %H:%M")

    stok_lines = "\n".join(
        f"   • {row['nama']}: {row['tersedia']} akun"
        for row in s["stok"]
    ) or "   (tidak ada data)"

    teks = (
        f"<b>Dashboard Admin</b>\n"
        f"Update: {now}\n\n"

        f"<b>User</b>\n"
        f"   Total: {s['total_user']:,} user\n\n"

        f"<b>Keuangan</b>\n"
        f"   Saldo beredar: {fmt_rupiah(s['total_saldo'])}\n"
        f"   Topup hari ini: {fmt_rupiah(s['topup_hari_ini'])}\n"
        f"   Omset hari ini: {fmt_rupiah(s['omset_hari_ini'])}\n"
        f"   Omset total: {fmt_rupiah(s['omset_total'])}\n\n"

        f"<b>Transaksi</b>\n"
        f"   Pembelian hari ini: {s['trx_hari_ini']}\n"
        f"   Total pembelian: {s['total_trx']}\n\n"

        f"<b>Garansi</b>\n"
        f"   Klaim pending: {s['garansi_pending']}\n\n"

        f"<b>Stok Tersedia</b>\n"
        f"{stok_lines}"
    )

    kb = [
        [
            InlineKeyboardButton("Refresh",        callback_data="admin_stat", style="primary"),
            InlineKeyboardButton("Klaim Garansi",  callback_data="admin_garansi_list", style="primary"),
        ],
        [
            InlineKeyboardButton("Kelola Stok",    callback_data="admin_stok_refresh", style="primary"),
            InlineKeyboardButton("Broadcast",       callback_data="admin_broadcast_start_cb", style="primary"),
        ],
        [InlineKeyboardButton("Panel Admin",       callback_data="admin_panel", style="danger")],
    ]

    reply_target = update.message if update.message else update.callback_query.message
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
            )
        except Exception:
            await reply_target.reply_text(
                teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
            )
    else:
        await reply_target.reply_text(
            teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
        )


def register(app):
    app.add_handler(CommandHandler(["stat", "stat_admin"], cmd_stat))
    app.add_handler(CallbackQueryHandler(cb_stat, pattern="^admin_stat$"))

"""
handlers/riwayat.py - Riwayat Beli Gmail & Riwayat Mutasi Saldo
"""
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database import db

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


def fmt_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")


def fmt_dt(iso_str: str) -> str:
    """Format ISO datetime ke string cantik."""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d %b %Y %H:%M")
    except Exception:
        return iso_str[:16] if iso_str else "-"


# ── RIWAYAT BELI ─────────────────────────────────────────────────────────────

async def show_riwayat_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = update.effective_user
    await q.answer()

    # Halaman dari callback data (riwayat_beli:2 → halaman 2)
    parts = q.data.split(":")
    page  = int(parts[1]) if len(parts) > 1 else 0

    riwayat = db.get_riwayat_beli(user.id, limit=100)

    if not riwayat:
        await q.edit_message_text(
            "📋 <b>Riwayat Pembelian</b>\n\n"
            "Kamu belum pernah melakukan pembelian.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🛒 Beli Sekarang", callback_data="beli_paket"),
                InlineKeyboardButton("🏠 Menu", callback_data="menu_utama"),
            ]])
        )
        return

    total   = len(riwayat)
    start   = page * PAGE_SIZE
    end     = min(start + PAGE_SIZE, total)
    items   = riwayat[start:end]

    teks = f"📋 <b>Riwayat Pembelian</b> (hal. {page+1}/{(total-1)//PAGE_SIZE+1})\n\n"

    kb = []
    for r in items:
        status_emoji = {"aktif": "🟢", "klaim_garansi": "🟡", "selesai": "⚪"}.get(r["status"], "⚪")
        teks += (
            f"{status_emoji} <b>#{r['id']}</b> – {r['paket_nama']}\n"
            f"   💰 {fmt_rupiah(r['harga_bayar'])}  |  🗓️ {fmt_dt(r['created_at'])}\n"
            f"   🛡️ Garansi: {fmt_dt(r['garansi_until'])}  |  Status: {r['status']}\n"
        )
        kb.append([InlineKeyboardButton(
            f"👁️ Lihat Akun #{r['id']}",
            callback_data=f"lihat_akun:{r['id']}"
        )])

    # Navigasi
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Sebelumnya", callback_data=f"riwayat_beli:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton("Berikutnya ▶️", callback_data=f"riwayat_beli:{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama")])

    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def lihat_akun(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan detail akun dari pembelian."""
    q            = update.callback_query
    user         = update.effective_user
    pembelian_id = int(q.data.split(":", 1)[1])
    await q.answer()

    detail = db.get_detail_pembelian(pembelian_id, user.id)
    if not detail:
        await q.answer("❌ Data tidak ditemukan.", show_alert=True)
        return

    akun_list = detail.get("akun_list", [])
    teks = (
        f"📧 <b>Detail Pesanan #{pembelian_id}</b>\n"
        f"📦 Paket: {detail['paket_nama']}\n"
        f"💰 Harga: {fmt_rupiah(detail['harga_bayar'])}\n"
        f"🗓️ Tanggal: {fmt_dt(detail['created_at'])}\n"
        f"🛡️ Garansi s/d: {fmt_dt(detail['garansi_until'])}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📧 <b>DATA AKUN GMAIL:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
    )

    for i, a in enumerate(akun_list, 1):
        teks += f"\n🔹 <b>Akun #{i}</b>\n"
        teks += f"   📧 Email   : <code>{a['email']}</code>\n"
        teks += f"   🔑 Password: <code>{a['password']}</code>\n"
        if a.get("recovery"):
            teks += f"   🔄 Recovery: <code>{a['recovery']}</code>\n"
        if a.get("tgl_buat"):
            teks += f"   📅 Dibuat  : {a['tgl_buat']}\n"
        if a.get("catatan"):
            teks += f"   📝 Catatan : {a['catatan']}\n"

    kb = [[
        InlineKeyboardButton("🔙 Riwayat Beli", callback_data="riwayat_beli"),
        InlineKeyboardButton("🏠 Menu", callback_data="menu_utama"),
    ]]
    if detail.get("status") == "aktif":
        now_iso = datetime.now().isoformat()
        if detail.get("garansi_until", "") > now_iso:
            kb.insert(0, [InlineKeyboardButton(
                "🛡️ Klaim Garansi", callback_data=f"pilih_garansi:{pembelian_id}"
            )])

    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


# ── RIWAYAT MUTASI ───────────────────────────────────────────────────────────

TIPE_ICON = {
    "topup":          "💳",
    "beli":           "🛒",
    "referral":       "👥",
    "refund_garansi": "🔄",
    "manual":         "✏️",
}


async def show_riwayat_mutasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = update.effective_user
    await q.answer()

    parts = q.data.split(":")
    page  = int(parts[1]) if len(parts) > 1 else 0

    mutasi = db.get_riwayat_mutasi(user.id, limit=100)

    if not mutasi:
        await q.edit_message_text(
            "📊 <b>Riwayat Mutasi</b>\n\nBelum ada transaksi.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama")
            ]])
        )
        return

    total = len(mutasi)
    start = page * PAGE_SIZE
    end   = min(start + PAGE_SIZE, total)
    items = mutasi[start:end]

    teks = f"📊 <b>Riwayat Mutasi</b> (hal. {page+1}/{(total-1)//PAGE_SIZE+1})\n\n"
    for m in items:
        icon    = TIPE_ICON.get(m["tipe"], "📋")
        masuk   = m["jumlah"] > 0
        sign    = "+" if masuk else ""
        warna   = "🟢" if masuk else "🔴"
        teks += (
            f"{icon} {warna} <b>{sign}{fmt_rupiah(abs(m['jumlah']))}</b>\n"
            f"   📝 {m['keterangan']}\n"
            f"   📅 {fmt_dt(m['created_at'])}  |  Saldo: {fmt_rupiah(m['saldo_sesudah'])}\n\n"
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Sebelumnya", callback_data=f"riwayat_mutasi:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton("Berikutnya ▶️", callback_data=f"riwayat_mutasi:{page+1}"))
    kb = []
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama")])

    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


def register(app):
    app.add_handler(CallbackQueryHandler(show_riwayat_beli,   pattern="^riwayat_beli"))
    app.add_handler(CallbackQueryHandler(lihat_akun,          pattern="^lihat_akun:"))
    app.add_handler(CallbackQueryHandler(show_riwayat_mutasi, pattern="^riwayat_mutasi"))

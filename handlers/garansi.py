"""
handlers/garansi.py - Klaim Garansi 24 Jam
Flow:
  1. User klik "Klaim Garansi"
  2. Tampilkan daftar pembelian yang masih aktif garansinya
  3. User pilih pembelian → ketik alasan
  4. Klaim dicatat, admin dinotif
  5. Admin proses via /garansi_proses
"""
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database import db
from config import ADMIN_CONTACT, ADMIN_NOTIF_CHAT

logger = logging.getLogger(__name__)


def fmt_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")


async def show_garansi_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan pembelian yang masih dalam masa garansi."""
    q    = update.callback_query
    user = update.effective_user
    await q.answer()

    riwayat = db.get_riwayat_beli(user.id, limit=50)
    now_iso = datetime.now().isoformat()

    # Filter: hanya yang status 'aktif' dan garansi belum habis
    valid = [
        r for r in riwayat
        if r["status"] == "aktif" and r["garansi_until"] > now_iso
    ]

    if not valid:
        await q.edit_message_text(
            "<b>Tidak Ada Garansi Aktif</b>\n\n"
            "Tidak ada pembelian yang masih dalam masa garansi (24 jam).\n\n"
            f"Jika ada masalah, hubungi admin: {ADMIN_CONTACT}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
            ]])
        )
        return

    teks = "<b>Klaim Garansi</b>\n\nPilih pembelian yang ingin diklaim:\n"
    kb   = []
    for r in valid:
        sisa_jam = (datetime.fromisoformat(r["garansi_until"]) - datetime.now()).seconds // 3600
        label    = f"#{r['id']} – {r['paket_nama']} (sisa ~{sisa_jam}j)"
        kb.append([InlineKeyboardButton(label, callback_data=f"pilih_garansi:{r['id']}", style="primary")])

    kb.append([InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")])
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def pilih_garansi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User memilih pembelian untuk diklaim."""
    q            = update.callback_query
    user         = update.effective_user
    pembelian_id = int(q.data.split(":", 1)[1])
    await q.answer()

    detail = db.get_detail_pembelian(pembelian_id, user.id)
    if not detail:
        await q.edit_message_text("Pembelian tidak ditemukan.")
        return

    if detail["status"] != "aktif":
        await q.edit_message_text(
            "Pembelian ini sudah diklaim garansinya atau sudah selesai.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Kembali", callback_data="garansi", style="primary")
            ]])
        )
        return

    now_iso = datetime.now().isoformat()
    if detail["garansi_until"] <= now_iso:
        await q.edit_message_text(
            "Masa garansi pembelian ini sudah habis.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
            ]])
        )
        return

    db.set_session(user.id, "waiting_garansi_alasan", {"pembelian_id": pembelian_id})

    teks = (
        f"<b>Klaim Garansi</b>\n\n"
        f"Paket: <b>{detail['paket_nama']}</b>\n"
        f"ID Pesanan: #{pembelian_id}\n\n"
        "Jelaskan masalah yang kamu alami:\n"
        "(contoh: akun tidak bisa login, password salah, dll)\n\n"
        "Ketik alasanmu:"
    )
    kb = [[InlineKeyboardButton("Batal", callback_data="garansi", style="danger")]]
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def handle_garansi_alasan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Terima alasan klaim dari user."""
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "waiting_garansi_alasan":
        return

    alasan       = update.message.text.strip()
    pembelian_id = session["data"]["pembelian_id"]
    db.clear_session(user.id)

    if len(alasan) < 5:
        await update.message.reply_text(
            "Alasan terlalu singkat. Tolong jelaskan masalah yang kamu alami."
        )
        return

    garansi_id = db.create_garansi(pembelian_id, user.id, alasan)

    if garansi_id is None:
        await update.message.reply_text(
            "Gagal membuat klaim garansi.\n"
            "Kemungkinan:\n"
            "• Garansi sudah kadaluarsa\n"
            "• Sudah ada klaim aktif untuk pesanan ini\n"
            "• Pesanan tidak ditemukan\n\n"
            f"Hubungi admin: {ADMIN_CONTACT}"
        )
        return

    await update.message.reply_text(
        f"<b>Klaim Garansi Terkirim!</b>\n\n"
        f"ID Klaim: #{garansi_id}\n"
        f"ID Pesanan: #{pembelian_id}\n"
        f"Alasan: {alasan}\n\n"
        "Admin akan memproses klaim kamu segera.\n"
        f"Jika mendesak, hubungi: {ADMIN_CONTACT}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="primary")
        ]])
    )

    # Notif admin
    try:
        user_info = db.get_user(user.id)
        notif = (
            f"<b>KLAIM GARANSI BARU</b>\n\n"
            f"User: {user.full_name} (@{user.username or '-'}) [<code>{user.id}</code>]\n"
            f"ID Klaim: #{garansi_id}\n"
            f"ID Pesanan: #{pembelian_id}\n"
            f"Alasan: {alasan}\n\n"
            f"Gunakan /garansi_list untuk melihat semua klaim."
        )
        await ctx.bot.send_message(chat_id=ADMIN_NOTIF_CHAT, text=notif, parse_mode="HTML")
    except Exception as e:
        logger.debug("[garansi] Gagal notif admin: %s", e)


def register(app):
    app.add_handler(CallbackQueryHandler(show_garansi_menu, pattern="^garansi$"))
    app.add_handler(CallbackQueryHandler(pilih_garansi,     pattern="^pilih_garansi:"))

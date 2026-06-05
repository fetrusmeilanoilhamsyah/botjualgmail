"""
handlers/beli.py - Beli Akun Gmail
Flow: Pilih paket → konfirmasi → potong saldo → kirim akun
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database import db
from config import ADMIN_CONTACT, ADMIN_NOTIF_CHAT

logger = logging.getLogger(__name__)


def fmt_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")


async def show_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan daftar paket aktif dengan stok tersedia."""
    q = update.callback_query
    await q.answer()

    paket_list = db.get_paket_aktif()
    if not paket_list:
        await q.edit_message_text(
            "😔 Belum ada paket tersedia saat ini.\n"
            f"Hubungi admin: {ADMIN_CONTACT}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama", style="danger")
            ]])
        )
        return

    saldo = db.get_saldo(update.effective_user.id)
    teks  = (
        f"🛒 <b>Beli Akun Gmail</b>\n\n"
        f"💰 Saldo kamu: <b>{fmt_rupiah(saldo)}</b>\n\n"
        "Pilih paket:\n"
    )

    keyboard = []
    for p in paket_list:
        stok     = p["stok_tersedia"]
        emoji    = "✅" if stok >= p["kuantitas"] else "❌"
        label    = f"{emoji} {p['nama']} — {fmt_rupiah(p['harga'])} (stok: {stok})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"konfirmasi_beli:{p['id']}", style="success")])

    keyboard.append([InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama", style="danger")])
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def konfirmasi_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan konfirmasi sebelum beli."""
    q        = update.callback_query
    user     = update.effective_user
    paket_id = int(q.data.split(":", 1)[1])
    await q.answer()

    paket = db.get_paket_by_id(paket_id)
    if not paket:
        await q.edit_message_text("❌ Paket tidak ditemukan.")
        return

    saldo = db.get_saldo(user.id)

    if paket["stok_tersedia"] < paket["kuantitas"]:
        await q.edit_message_text(
            f"❌ <b>Stok habis!</b>\n\n"
            f"Paket <b>{paket['nama']}</b> sementara tidak tersedia.\n"
            f"Coba lagi nanti atau pilih paket lain.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Pilih Paket Lain", callback_data="beli_paket", style="primary")],
                [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama", style="danger")],
            ])
        )
        return

    cukup = saldo >= paket["harga"]
    status_saldo = "✅ Saldo cukup" if cukup else f"❌ Saldo kurang (kurang {fmt_rupiah(paket['harga'] - saldo)})"

    teks = (
        f"🛒 <b>Konfirmasi Pembelian</b>\n\n"
        f"📦 Paket  : <b>{paket['nama']}</b>\n"
        f"💰 Harga  : <b>{fmt_rupiah(paket['harga'])}</b>\n"
        f"📊 Kuantitas: {paket['kuantitas']} akun\n"
        f"🛡️ Garansi: 24 jam\n\n"
        f"💳 Saldo kamu: {fmt_rupiah(saldo)}\n"
        f"📌 Status: {status_saldo}\n\n"
        "Lanjutkan pembelian?"
    )

    if cukup:
        keyboard = [
            [InlineKeyboardButton("✅ BELI SEKARANG", callback_data=f"eksekusi_beli:{paket_id}", style="success")],
            [InlineKeyboardButton("🔙 Batal", callback_data="beli_paket", style="danger")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("💳 Top Up Saldo", callback_data="topup", style="success")],
            [InlineKeyboardButton("🔙 Pilih Paket Lain", callback_data="beli_paket", style="danger")],
        ]

    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def eksekusi_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Proses pembelian:
    1. Ambil stok ATOMIC
    2. Kurangi saldo ATOMIC
    3. Catat pembelian
    4. Kirim akun ke user
    5. Notif admin
    """
    q        = update.callback_query
    user     = update.effective_user
    paket_id = int(q.data.split(":", 1)[1])
    await q.answer("⏳ Memproses...")

    paket = db.get_paket_by_id(paket_id)
    if not paket:
        await q.edit_message_text("❌ Paket tidak ditemukan.")
        return

    # Double check saldo
    saldo = db.get_saldo(user.id)
    if saldo < paket["harga"]:
        await q.edit_message_text(
            f"❌ Saldo tidak cukup.\n\n"
            f"💰 Saldo kamu: {fmt_rupiah(saldo)}\n"
            f"💸 Harga: {fmt_rupiah(paket['harga'])}\n\n"
            f"Silakan top up terlebih dahulu.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💳 Top Up", callback_data="topup", style="success"),
                InlineKeyboardButton("🏠 Menu", callback_data="menu_utama", style="danger"),
            ]])
        )
        return

    # Ambil stok ATOMIC
    akun_list = db.ambil_stok(paket_id, paket["kuantitas"])
    if akun_list is None:
        await q.edit_message_text(
            "❌ <b>Stok habis!</b>\n\nMaaf, stok baru saja habis. Coba lagi nanti.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Paket Lain", callback_data="beli_paket", style="primary"),
                InlineKeyboardButton("🏠 Menu", callback_data="menu_utama", style="danger"),
            ]])
        )
        return

    # Kurangi saldo ATOMIC
    result = db.kurangi_saldo(
        user.id, paket["harga"], "beli",
        f"Beli {paket['nama']}"
    )
    if result is None:
        # Race condition: saldo kurang setelah cek ulang → kembalikan stok
        for akun in akun_list:
            try:
                with db.get_connection() as conn:
                    conn.execute("UPDATE stok_gmail SET terjual=0, terjual_at=NULL WHERE id=?", (akun["id"],))
                    conn.commit()
            except Exception:
                pass
        await q.edit_message_text(
            "❌ Saldo tidak cukup saat proses. Silakan coba lagi.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama", style="danger")
            ]])
        )
        return

    # Tandai stok → terjual_ke user
    stok_ids = [a["id"] for a in akun_list]
    db.tandai_stok_terjual_ke(stok_ids, user.id)

    # Catat pembelian
    pembelian_id = db.create_pembelian(
        user_id=user.id,
        paket_id=paket_id,
        harga_bayar=paket["harga"],
        jumlah_akun=paket["kuantitas"],
        stok_ids=stok_ids
    )

    # Kirim akun ke user via DM (pesan terpisah, lebih private)
    akun_teks = _format_akun(akun_list)
    teks_kirim = (
        f"✅ <b>Pembelian Berhasil!</b>\n\n"
        f"📦 Paket: <b>{paket['nama']}</b>\n"
        f"💰 Harga: {fmt_rupiah(paket['harga'])}\n"
        f"💳 Saldo tersisa: {fmt_rupiah(result['saldo_sesudah'])}\n"
        f"🛡️ Garansi: 24 jam dari sekarang\n"
        f"📌 ID Pesanan: #{pembelian_id}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 <b>DATA AKUN GMAIL:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{akun_teks}\n\n"
        f"⚠️ <b>Simpan data ini dengan aman!</b>\n"
        f"Jangan bagikan ke siapapun."
    )

    try:
        await q.edit_message_text(teks_kirim, parse_mode="HTML")
    except Exception:
        await q.message.reply_text(teks_kirim, parse_mode="HTML")

    # Notif ke admin
    try:
        notif = (
            f"🛒 <b>PEMBELIAN BARU</b>\n\n"
            f"👤 User: {user.full_name} (@{user.username or '-'}) [<code>{user.id}</code>]\n"
            f"📦 Paket: {paket['nama']}\n"
            f"💰 Harga: {fmt_rupiah(paket['harga'])}\n"
            f"📌 ID: #{pembelian_id}"
        )
        await ctx.bot.send_message(chat_id=ADMIN_NOTIF_CHAT, text=notif, parse_mode="HTML")
    except Exception as e:
        logger.debug("[beli] Gagal notif admin: %s", e)


def _format_akun(akun_list: list) -> str:
    lines = []
    for i, a in enumerate(akun_list, 1):
        baris = f"🔹 <b>Akun #{i}</b>\n"
        baris += f"   📧 Email   : <code>{a['email']}</code>\n"
        baris += f"   🔑 Password: <code>{a['password']}</code>"
        if a.get("recovery"):
            baris += f"\n   🔄 Recovery: <code>{a['recovery']}</code>"
        if a.get("tgl_buat"):
            baris += f"\n   📅 Dibuat  : {a['tgl_buat']}"
        if a.get("catatan"):
            baris += f"\n   📝 Catatan : {a['catatan']}"
        lines.append(baris)
    return "\n\n".join(lines)


def register(app):
    app.add_handler(CallbackQueryHandler(show_paket,       pattern="^beli_paket$"))
    app.add_handler(CallbackQueryHandler(konfirmasi_beli,  pattern="^konfirmasi_beli:"))
    app.add_handler(CallbackQueryHandler(eksekusi_beli,    pattern="^eksekusi_beli:"))

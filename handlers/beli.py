"""
handlers/beli.py - Beli Akun Gmail
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
            "Belum ada paket tersedia saat ini.\n"
            f"Hubungi admin: {ADMIN_CONTACT}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
            ]])
        )
        return

    saldo = db.get_saldo(update.effective_user.id)
    teks  = (
        f"<b>Beli Akun Gmail</b>\n\n"
        f"Saldo kamu: <b>{fmt_rupiah(saldo)}</b>\n\n"
        "Pilih paket di bawah ini:"
    )

    keyboard = []
    temp_row = []
    for p in paket_list:
        label = f"{p['kuantitas']} Akun — {fmt_rupiah(p['harga'])}"
        temp_row.append(InlineKeyboardButton(label, callback_data=f"konfirmasi_beli:{p['id']}", style="primary"))
        if len(temp_row) == 2:
            keyboard.append(temp_row)
            temp_row = []
    if temp_row:
        keyboard.append(temp_row)

    # Tombol Custom Quantity
    keyboard.append([InlineKeyboardButton("Beli Jumlah Custom", callback_data="beli_custom", style="primary")])
    keyboard.append([InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")])
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def konfirmasi_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    user     = update.effective_user
    paket_id = int(q.data.split(":", 1)[1])
    await q.answer()

    paket = db.get_paket_by_id(paket_id)
    if not paket:
        await q.edit_message_text("Paket tidak ditemukan.")
        return

    saldo = db.get_saldo(user.id)

    if paket["stok_tersedia"] < paket["kuantitas"]:
        await q.edit_message_text(
            f"<b>Stok habis!</b>\n\n"
            f"Paket {paket['nama']} sementara tidak tersedia.\n"
            f"Coba lagi nanti atau pilih paket lain.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Pilih Paket Lain", callback_data="beli_paket", style="primary")],
                [InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")],
            ])
        )
        return

    cukup = saldo >= paket["harga"]
    status_saldo = "Saldo cukup" if cukup else f"Saldo kurang (kurang {fmt_rupiah(paket['harga'] - saldo)})"

    teks = (
        f"<b>Konfirmasi Pembelian</b>\n\n"
        f"Paket: <b>{paket['nama']}</b>\n"
        f"Harga: <b>{fmt_rupiah(paket['harga'])}</b>\n"
        f"Kuantitas: {paket['kuantitas']} akun\n"
        f"Garansi: 24 jam\n\n"
        f"Saldo kamu: {fmt_rupiah(saldo)}\n"
        f"Status: {status_saldo}\n\n"
        "Lanjutkan pembelian?"
    )

    if cukup:
        keyboard = [
            [InlineKeyboardButton("BELI SEKARANG", callback_data=f"eksekusi_beli:{paket_id}", style="primary")],
            [InlineKeyboardButton("Batal", callback_data="beli_paket", style="danger")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("Top Up Saldo", callback_data="topup", style="primary")],
            [InlineKeyboardButton("Pilih Paket Lain", callback_data="beli_paket", style="danger")],
        ]

    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_beli_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    teks = (
        "<b>Beli Jumlah Custom</b>\n\n"
        f"Harga satuan: <b>{fmt_rupiah(db.get_harga_satuan())}</b>\n"
        f"Total stok tersedia: <b>{db.get_stok_count()} akun</b>\n\n"
        "Ketik jumlah akun Gmail yang ingin kamu beli (angka saja, contoh: <code>7</code>):"
    )
    kb = [[InlineKeyboardButton("Batal", callback_data="beli_paket", style="danger")]]
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    db.set_session(update.effective_user.id, "waiting_beli_kuantitas", {})


async def handle_beli_kuantitas_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "waiting_beli_kuantitas":
        return

    text = update.message.text.strip().replace(".", "").replace(",", "")
    try:
        qty = int(text)
        if qty <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("Format salah. Ketik angka jumlah akun saja (contoh: 7).")
        return

    total_stok = db.get_stok_count()
    if total_stok < qty:
        await update.message.reply_text(
            f"Stok kurang. Hanya ada {total_stok} akun tersedia saat ini.\n"
            "Silakan masukkan jumlah yang lebih kecil.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Pilih Paket", callback_data="beli_paket", style="primary")
            ]])
        )
        return

    db.clear_session(user.id)
    db.set_session(user.id, "waiting_beli_custom_confirm", {"qty": qty})

    harga_satuan = db.get_harga_satuan()
    total_harga = qty * harga_satuan
    saldo = db.get_saldo(user.id)

    cukup = saldo >= total_harga
    status_saldo = "Saldo cukup" if cukup else f"Saldo kurang (kurang {fmt_rupiah(total_harga - saldo)})"

    teks = (
        f"<b>Konfirmasi Pembelian Custom</b>\n\n"
        f"Jumlah Akun: <b>{qty} akun</b>\n"
        f"Harga Satuan: <b>{fmt_rupiah(harga_satuan)}</b>\n"
        f"Total Harga: <b>{fmt_rupiah(total_harga)}</b>\n"
        f"Garansi: 24 jam\n\n"
        f"Saldo kamu: {fmt_rupiah(saldo)}\n"
        f"Status: {status_saldo}\n\n"
        "Lanjutkan pembelian?"
    )

    if cukup:
        kb = [
            [InlineKeyboardButton("BELI SEKARANG", callback_data="eksekusi_beli_custom", style="primary")],
            [InlineKeyboardButton("Batal", callback_data="beli_paket", style="danger")]
        ]
    else:
        kb = [
            [InlineKeyboardButton("Top Up Saldo", callback_data="topup", style="primary")],
            [InlineKeyboardButton("Batal", callback_data="beli_paket", style="danger")]
        ]

    await update.message.reply_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def eksekusi_beli_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = update.effective_user
    session = db.get_session(user.id)
    await q.answer("Memproses...")

    if session["state"] != "waiting_beli_custom_confirm":
        await q.edit_message_text(
            "Sesi berakhir. Silakan coba lagi.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Beli Gmail", callback_data="beli_paket", style="primary")
            ]])
        )
        return

    qty = session["data"]["qty"]
    db.clear_session(user.id)

    harga_satuan = db.get_harga_satuan()
    total_harga = qty * harga_satuan

    saldo = db.get_saldo(user.id)
    if saldo < total_harga:
        await q.edit_message_text(
            f"Saldo tidak cukup.\n\n"
            f"Saldo kamu: {fmt_rupiah(saldo)}\n"
            f"Total Harga: {fmt_rupiah(total_harga)}\n\n"
            "Silakan top up terlebih dahulu.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Top Up", callback_data="topup", style="primary"),
                InlineKeyboardButton("Batal", callback_data="beli_paket", style="danger"),
            ]])
        )
        return

    # Ambil stok global
    akun_list = db.ambil_stok(1, qty)
    if akun_list is None:
        await q.edit_message_text(
            "Stok habis!\n\nMaaf, stok baru saja habis. Coba lagi nanti.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Pilih Paket", callback_data="beli_paket", style="primary"),
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger"),
            ]])
        )
        return

    # Potong saldo
    result = db.kurangi_saldo(user.id, total_harga, "beli", f"Beli {qty} Gmail Custom")
    if result is None:
        # Rollback stock
        for akun in akun_list:
            try:
                with db.get_connection() as conn:
                    conn.execute("UPDATE stok_gmail SET terjual=0, terjual_at=NULL WHERE id=?", (akun["id"],))
                    conn.commit()
            except Exception:
                pass
        await q.edit_message_text(
            "Saldo tidak cukup saat proses. Silakan coba lagi.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
            ]])
        )
        return

    stok_ids = [a["id"] for a in akun_list]
    db.tandai_stok_terjual_ke(stok_ids, user.id)

    pembelian_id = db.create_pembelian(
        user_id=user.id,
        paket_id=1,
        harga_bayar=total_harga,
        jumlah_akun=qty,
        stok_ids=stok_ids
    )

    akun_teks = _format_akun(akun_list)
    teks_kirim = (
        f"Pembelian Berhasil!\n\n"
        f"Jumlah: <b>{qty} Akun</b>\n"
        f"Total Harga: {fmt_rupiah(total_harga)}\n"
        f"Saldo tersisa: {fmt_rupiah(result['saldo_sesudah'])}\n"
        f"Garansi: 24 jam dari sekarang\n"
        f"ID Pesanan: #{pembelian_id}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"DATA AKUN GMAIL:\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{akun_teks}\n\n"
        f"Simpan data ini dengan aman!\n"
        f"Jangan bagikan ke siapapun."
    )

    try:
        await q.edit_message_text(teks_kirim, parse_mode="HTML")
    except Exception:
        await q.message.reply_text(teks_kirim, parse_mode="HTML")

    # Notif admin
    try:
        notif = (
            f"PEMBELIAN CUSTOM BARU\n\n"
            f"User: {user.full_name} (@{user.username or '-'}) [<code>{user.id}</code>]\n"
            f"Kuantitas: {qty} Gmail\n"
            f"Total Harga: {fmt_rupiah(total_harga)}\n"
            f"ID: #{pembelian_id}"
        )
        await ctx.bot.send_message(chat_id=ADMIN_NOTIF_CHAT, text=notif, parse_mode="HTML")
    except Exception as e:
        logger.debug("[beli] Gagal notif admin: %s", e)


async def eksekusi_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    user     = update.effective_user
    paket_id = int(q.data.split(":", 1)[1])
    await q.answer("Memproses...")

    paket = db.get_paket_by_id(paket_id)
    if not paket:
        await q.edit_message_text("Paket tidak ditemukan.")
        return

    saldo = db.get_saldo(user.id)
    if saldo < paket["harga"]:
        await q.edit_message_text(
            f"Saldo tidak cukup.\n\n"
            f"Saldo kamu: {fmt_rupiah(saldo)}\n"
            f"Harga: {fmt_rupiah(paket['harga'])}\n\n"
            f"Silakan top up terlebih dahulu.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Top Up", callback_data="topup", style="primary"),
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger"),
            ]])
        )
        return

    # Ambil stok pooled
    akun_list = db.ambil_stok(paket_id, paket["kuantitas"])
    if akun_list is None:
        await q.edit_message_text(
            "Stok habis!\n\nMaaf, stok baru saja habis. Coba lagi nanti.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Pilih Paket Lain", callback_data="beli_paket", style="primary"),
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger"),
            ]])
        )
        return

    result = db.kurangi_saldo(user.id, paket["harga"], "beli", f"Beli {paket['nama']}")
    if result is None:
        # Rollback stock
        for akun in akun_list:
            try:
                with db.get_connection() as conn:
                    conn.execute("UPDATE stok_gmail SET terjual=0, terjual_at=NULL WHERE id=?", (akun["id"],))
                    conn.commit()
            except Exception:
                pass
        await q.edit_message_text(
            "Saldo tidak cukup saat proses. Silakan coba lagi.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
            ]])
        )
        return

    stok_ids = [a["id"] for a in akun_list]
    db.tandai_stok_terjual_ke(stok_ids, user.id)

    pembelian_id = db.create_pembelian(
        user_id=user.id,
        paket_id=paket_id,
        harga_bayar=paket["harga"],
        jumlah_akun=paket["kuantitas"],
        stok_ids=stok_ids
    )

    akun_teks = _format_akun(akun_list)
    teks_kirim = (
        f"Pembelian Berhasil!\n\n"
        f"Paket: <b>{paket['nama']}</b>\n"
        f"Harga: {fmt_rupiah(paket['harga'])}\n"
        f"Saldo tersisa: {fmt_rupiah(result['saldo_sesudah'])}\n"
        f"Garansi: 24 jam dari sekarang\n"
        f"ID Pesanan: #{pembelian_id}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"DATA AKUN GMAIL:\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{akun_teks}\n\n"
        f"Simpan data ini dengan aman!\n"
        f"Jangan bagikan ke siapapun."
    )

    try:
        await q.edit_message_text(teks_kirim, parse_mode="HTML")
    except Exception:
        await q.message.reply_text(teks_kirim, parse_mode="HTML")

    # Notif admin
    try:
        notif = (
            f"PEMBELIAN BARU\n\n"
            f"User: {user.full_name} (@{user.username or '-'}) [<code>{user.id}</code>]\n"
            f"Paket: {paket['nama']}\n"
            f"Harga: {fmt_rupiah(paket['harga'])}\n"
            f"ID: #{pembelian_id}"
        )
        await ctx.bot.send_message(chat_id=ADMIN_NOTIF_CHAT, text=notif, parse_mode="HTML")
    except Exception as e:
        logger.debug("[beli] Gagal notif admin: %s", e)


def _format_akun(akun_list: list) -> str:
    lines = []
    for i, a in enumerate(akun_list, 1):
        baris = f"• <b>Akun #{i}</b>\n"
        baris += f"   Email   : <code>{a['email']}</code>\n"
        baris += f"   Password: <code>{a['password']}</code>"
        if a.get("recovery"):
            baris += f"\n   Recovery: <code>{a['recovery']}</code>"
        if a.get("tgl_buat"):
            baris += f"\n   Dibuat  : {a['tgl_buat']}"
        if a.get("catatan"):
            baris += f"\n   Catatan : {a['catatan']}"
        lines.append(baris)
    return "\n\n".join(lines)


def register(app):
    app.add_handler(CallbackQueryHandler(show_paket,           pattern="^beli_paket$"))
    app.add_handler(CallbackQueryHandler(show_beli_custom,     pattern="^beli_custom$"))
    app.add_handler(CallbackQueryHandler(eksekusi_beli_custom, pattern="^eksekusi_beli_custom$"))
    app.add_handler(CallbackQueryHandler(konfirmasi_beli,      pattern="^konfirmasi_beli:"))
    app.add_handler(CallbackQueryHandler(eksekusi_beli,        pattern="^eksekusi_beli:"))

"""
handlers/admin_stok.py - Kelola Stok Gmail (Panel Admin)
"""
import logging
from io import StringIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from database import db
from middleware.auth import admin_only

logger = logging.getLogger(__name__)


def fmt_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")


@admin_only
async def cmd_stok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan ringkasan stok + opsi kelola."""
    with db.get_connection() as conn:
        total_tersedia = conn.execute("SELECT COUNT(*) FROM stok_gmail WHERE terjual=0").fetchone()[0]
        total_terjual  = conn.execute("SELECT COUNT(*) FROM stok_gmail WHERE terjual=1").fetchone()[0]
    harga_satuan = db.get_harga_satuan()

    teks = (
        "<b>Kelola Stok Gmail (Database Umum)</b>\n\n"
        f"• Total Tersedia : <b>{total_tersedia:,} Akun</b>\n"
        f"• Total Terjual  : <b>{total_terjual:,} Akun</b>\n"
        f"• Harga Satuan   : <b>{fmt_rupiah(harga_satuan)} / Gmail</b>\n"
    )

    kb = [
        [InlineKeyboardButton("Tambah Stok (Upload .txt)", callback_data="admin_upload_stok", style="primary")],
        [InlineKeyboardButton("Input Manual 1 Akun", callback_data="admin_input_manual", style="primary")],
        [InlineKeyboardButton("Kelola Paket & Harga", callback_data="admin_paket", style="primary")],
        [InlineKeyboardButton("Refresh", callback_data="admin_stok_refresh", style="primary")],
    ]
    reply_target = update.message or update.callback_query.message
    await reply_target.reply_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_stok_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Di-refresh")
    with db.get_connection() as conn:
        total_tersedia = conn.execute("SELECT COUNT(*) FROM stok_gmail WHERE terjual=0").fetchone()[0]
        total_terjual  = conn.execute("SELECT COUNT(*) FROM stok_gmail WHERE terjual=1").fetchone()[0]
    harga_satuan = db.get_harga_satuan()

    teks = (
        "<b>Kelola Stok Gmail (Database Umum)</b>\n\n"
        f"• Total Tersedia : <b>{total_tersedia:,} Akun</b>\n"
        f"• Total Terjual  : <b>{total_terjual:,} Akun</b>\n"
        f"• Harga Satuan   : <b>{fmt_rupiah(harga_satuan)} / Gmail</b>\n"
    )
    kb = [
        [InlineKeyboardButton("Tambah Stok (Upload .txt)", callback_data="admin_upload_stok", style="primary")],
        [InlineKeyboardButton("Input Manual 1 Akun", callback_data="admin_input_manual", style="primary")],
        [InlineKeyboardButton("Kelola Paket & Harga", callback_data="admin_paket", style="primary")],
        [InlineKeyboardButton("Refresh", callback_data="admin_stok_refresh", style="primary")],
    ]
    import telegram
    try:
        from datetime import datetime
        now_str = datetime.now().strftime("%H:%M:%S")
        teks_update = f"{teks}\n<i>Terakhir di-refresh: {now_str}</i>"
        await q.edit_message_text(teks_update, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    except telegram.error.BadRequest as e:
        if "Message is not modified" not in str(e):
            raise


@admin_only
async def admin_upload_stok_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Minta admin mengupload file .txt untuk stok umum."""
    q = update.callback_query
    await q.answer()

    db.set_session(update.effective_user.id, "admin_waiting_stok_file", {"paket_id": 1})

    teks = (
        "<b>Upload Stok Gmail (Database Umum)</b>\n\n"
        "Format file .txt (satu akun per baris):\n"
        "<code>email|password|recovery|tgl_buat|catatan</code>\n\n"
        "Contoh:\n"
        "<code>test@gmail.com|Pass123!|recover@gmail.com|2024-01-15|fresh-id</code>\n\n"
        "Field minimal: email|password\n\n"
        "Silakan kirimkan file .txt Anda sekarang."
    )
    kb = [[InlineKeyboardButton("Batal", callback_data="admin_stok_refresh", style="danger")]]
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_upload_pilih_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Stub: di-skip karena bypass langsung ke start."""
    pass


@admin_only
async def admin_terima_stok_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Terima file .txt stok dari admin."""
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "admin_waiting_stok_file":
        return

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".txt"):
        await update.message.reply_text("Kirim file .txt ya.")
        return

    paket_id = 1
    db.clear_session(user.id)

    msg = await update.message.reply_text("Memproses file...")

    tg_file = await doc.get_file()
    content = await tg_file.download_as_bytearray()
    lines   = content.decode("utf-8", errors="ignore").splitlines()

    ok, dup = db.bulk_add_stok(paket_id, lines)

    await msg.edit_text(
        f"Stok berhasil ditambahkan!\n\n"
        f"Berhasil: {ok} akun\n"
        f"Duplikat (skip): {dup} akun\n"
        f"Total diproses: {len(lines)} baris",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Lihat Stok", callback_data="admin_stok_refresh", style="primary"),
            InlineKeyboardButton("Panel", callback_data="admin_panel", style="danger"),
        ]])
    )


@admin_only
async def admin_input_manual_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Input manual 1 akun untuk database umum."""
    q = update.callback_query
    await q.answer()

    db.set_session(update.effective_user.id, "admin_input_manual_akun", {"paket_id": 1})
    teks = (
        "<b>Input Manual Akun Gmail (Database Umum)</b>\n\n"
        "Format: <code>email|password|recovery|tgl_buat|catatan</code>\n"
        "Minimal: email|password\n\n"
        "Contoh:\n"
        "<code>test@gmail.com|Pass123!|rec@gmail.com|2024-01-01|fresh</code>\n\n"
        "Ketik detail akun sekarang:"
    )
    kb = [[InlineKeyboardButton("Batal", callback_data="admin_stok_refresh", style="danger")]]
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_manual_pilih_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Stub: di-skip karena bypass langsung ke start."""
    pass


@admin_only
async def admin_terima_manual_akun(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "admin_input_manual_akun":
        return

    paket_id = 1
    db.clear_session(user.id)

    parts = update.message.text.strip().split("|")
    if len(parts) < 2:
        await update.message.reply_text(
            "Format salah. Minimal: email|password"
        )
        return

    email    = parts[0].strip()
    password = parts[1].strip()
    recovery = parts[2].strip() if len(parts) > 2 else ""
    tgl_buat = parts[3].strip() if len(parts) > 3 else ""
    catatan  = parts[4].strip() if len(parts) > 4 else ""

    if not email or "@" not in email:
        await update.message.reply_text("Email tidak valid.")
        return

    success = db.add_stok_gmail(paket_id, email, password, recovery, tgl_buat, catatan)

    if success:
        stok_now = db.get_stok_count()
        await update.message.reply_text(
            f"Akun berhasil ditambahkan!\n\n"
            f"Email: {email}\n"
            f"Total stok tersedia: {stok_now} akun",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Tambah Lagi", callback_data="admin_input_manual", style="primary"),
                InlineKeyboardButton("Panel", callback_data="admin_panel", style="danger"),
            ]])
        )
    else:
        await update.message.reply_text(
            f"Email {email} sudah ada di database (duplikat)."
        )


# ── Paket & Harga ──────────────────────────────────────────────────────────

@admin_only
async def admin_paket_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    harga_satuan = db.get_harga_satuan()
    paket_list = db.get_all_paket()
    teks = (
        "Kelola Paket & Harga\n\n"
        f"Harga Satuan Saat Ini: <b>{fmt_rupiah(harga_satuan)} / Gmail</b>\n\n"
        "Daftar Paket Aktif:\n"
    )

    for p in paket_list:
        aktif = "[Aktif]" if p["aktif"] else "[Nonaktif]"
        teks += f"\n{aktif} #{p['id']} {p['nama']} - {fmt_rupiah(p['harga'])} | stok: {p['stok_tersedia']}"

    kb = [
        [InlineKeyboardButton("Edit Harga Satuan", callback_data="admin_edit_harga_satuan", style="primary")],
        [InlineKeyboardButton("Toggle Status Paket", callback_data="admin_toggle_paket_list", style="primary")],
        [InlineKeyboardButton("Panel Admin", callback_data="admin_panel", style="danger")]
    ]
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_edit_harga_satuan_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db.set_session(update.effective_user.id, "admin_edit_harga_satuan", {})
    await q.edit_message_text(
        "Edit Harga Satuan Gmail\n\n"
        f"Harga saat ini: {fmt_rupiah(db.get_harga_satuan())}\n\n"
        "Ketik harga baru per 1 Gmail (angka saja, contoh: 4500):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Batal", callback_data="admin_paket", style="danger")]])
    )


@admin_only
async def admin_terima_harga_satuan_baru(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "admin_edit_harga_satuan":
        return

    db.clear_session(user.id)
    text = update.message.text.strip().replace(".", "").replace(",", "")
    try:
        harga = int(text)
        if harga <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("Format salah. Masukkan angka positif.")
        return

    db.update_harga_satuan(harga)
    await update.message.reply_text(
        "Harga satuan berhasil diperbarui!\n\n"
        f"Harga Baru: {fmt_rupiah(harga)} / Gmail\n"
        "Harga seluruh paket otomatis diperbarui.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Kembali", callback_data="admin_paket", style="primary")
        ]])
    )


@admin_only
async def admin_toggle_paket_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    paket_list = db.get_all_paket()
    teks = "Pilih paket untuk mengubah status aktif/nonaktif:"
    kb = []
    for p in paket_list:
        status = "Aktif" if p["aktif"] else "Nonaktif"
        label = f"#{p['id']} {p['nama']} ({status})"
        kb.append([InlineKeyboardButton(label, callback_data=f"admin_toggle_paket:{p['id']}", style="primary")])
    kb.append([InlineKeyboardButton("Kembali", callback_data="admin_paket", style="danger")])
    await q.edit_message_text(teks, reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_toggle_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    paket_id = int(q.data.split(":", 1)[1])
    await q.answer()

    db.toggle_paket_aktif(paket_id)
    paket = db.get_paket_by_id(paket_id)
    status = "AKTIF" if paket["aktif"] else "NONAKTIF"
    await q.answer(f"Paket {paket['nama']} -> {status}", show_alert=True)
    await admin_toggle_paket_list(update, ctx)


def register(app):
    app.add_handler(CommandHandler("stok", cmd_stok))
    app.add_handler(CallbackQueryHandler(admin_stok_refresh,          pattern="^admin_stok_refresh$"))
    app.add_handler(CallbackQueryHandler(admin_upload_stok_start,     pattern="^admin_upload_stok$"))
    app.add_handler(CallbackQueryHandler(admin_upload_pilih_paket,    pattern="^admin_upload_ke:"))
    app.add_handler(CallbackQueryHandler(admin_input_manual_start,    pattern="^admin_input_manual$"))
    app.add_handler(CallbackQueryHandler(admin_manual_pilih_paket,    pattern="^admin_manual_ke:"))
    app.add_handler(CallbackQueryHandler(admin_paket_menu,            pattern="^admin_paket$"))
    app.add_handler(CallbackQueryHandler(admin_edit_harga_satuan_start, pattern="^admin_edit_harga_satuan$"))
    app.add_handler(CallbackQueryHandler(admin_toggle_paket_list,     pattern="^admin_toggle_paket_list$"))
    app.add_handler(CallbackQueryHandler(admin_toggle_paket,          pattern="^admin_toggle_paket:"))

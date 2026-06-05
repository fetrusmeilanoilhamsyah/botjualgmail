"""
handlers/admin_stok.py - Kelola Stok Gmail (Panel Admin)
Fitur:
  - Upload .txt stok (format: email|password|recovery|tgl_buat|catatan)
  - Input manual 1 akun
  - Lihat ringkasan stok per paket
  - Hapus stok
Commands: /stok
"""
import logging
from io import StringIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters
)

from database import db
from middleware.auth import admin_only

logger = logging.getLogger(__name__)


def fmt_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")


@admin_only
async def cmd_stok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan ringkasan stok + opsi kelola."""
    summary = db.get_stok_summary()

    teks = "📦 <b>Kelola Stok Gmail</b>\n\n"
    for s in summary:
        aktif_label = "✅" if s["aktif"] else "❌"
        teks += (
            f"{aktif_label} <b>{s['nama']}</b> — {fmt_rupiah(s['harga'])}\n"
            f"   📊 Tersedia: {s['tersedia']} | Terjual: {s['terjual']}\n"
        )

    kb = [
        [InlineKeyboardButton("➕ Tambah Stok (Upload .txt)", callback_data="admin_upload_stok")],
        [InlineKeyboardButton("✏️ Input Manual 1 Akun", callback_data="admin_input_manual")],
        [InlineKeyboardButton("⚙️ Kelola Paket & Harga", callback_data="admin_paket")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_stok_refresh")],
    ]
    reply_target = update.message or update.callback_query.message
    await reply_target.reply_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_stok_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("🔄 Di-refresh")
    summary = db.get_stok_summary()
    teks = "📦 <b>Kelola Stok Gmail</b>\n\n"
    for s in summary:
        aktif_label = "✅" if s["aktif"] else "❌"
        teks += (
            f"{aktif_label} <b>{s['nama']}</b> — {fmt_rupiah(s['harga'])}\n"
            f"   📊 Tersedia: {s['tersedia']} | Terjual: {s['terjual']}\n"
        )
    kb = [
        [InlineKeyboardButton("➕ Tambah Stok (Upload .txt)", callback_data="admin_upload_stok")],
        [InlineKeyboardButton("✏️ Input Manual 1 Akun", callback_data="admin_input_manual")],
        [InlineKeyboardButton("⚙️ Kelola Paket & Harga", callback_data="admin_paket")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_stok_refresh")],
    ]
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_upload_stok_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Minta admin memilih paket untuk upload stok."""
    q = update.callback_query
    await q.answer()

    paket_list = db.get_all_paket()
    teks = (
        "📤 <b>Upload Stok Gmail</b>\n\n"
        "Format file .txt (satu akun per baris):\n"
        "<code>email|password|recovery|tgl_buat|catatan</code>\n\n"
        "Contoh:\n"
        "<code>test@gmail.com|Pass123!|recover@gmail.com|2024-01-15|fresh-id</code>\n\n"
        "Field minimal: <code>email|password</code>\n\n"
        "Pilih paket tujuan:"
    )
    kb = [[InlineKeyboardButton(f"{p['nama']}", callback_data=f"admin_upload_ke:{p['id']}")]
          for p in paket_list]
    kb.append([InlineKeyboardButton("❌ Batal", callback_data="admin_panel")])
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_upload_pilih_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin memilih paket → tunggu file."""
    q        = update.callback_query
    paket_id = int(q.data.split(":", 1)[1])
    await q.answer()

    paket = db.get_paket_by_id(paket_id)
    if not paket:
        await q.edit_message_text("❌ Paket tidak ditemukan.")
        return

    db.set_session(update.effective_user.id, "admin_waiting_stok_file", {"paket_id": paket_id})

    await q.edit_message_text(
        f"📤 Upload file .txt untuk paket <b>{paket['nama']}</b>\n\n"
        "Kirimkan file .txt sekarang.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Batal", callback_data="admin_panel")
        ]])
    )


@admin_only
async def admin_terima_stok_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Terima file .txt stok dari admin."""
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "admin_waiting_stok_file":
        return

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".txt"):
        await update.message.reply_text("❌ Kirim file .txt ya.")
        return

    paket_id = session["data"]["paket_id"]
    db.clear_session(user.id)

    paket = db.get_paket_by_id(paket_id)

    msg = await update.message.reply_text("⏳ Memproses file...")

    tg_file = await doc.get_file()
    content = await tg_file.download_as_bytearray()
    lines   = content.decode("utf-8", errors="ignore").splitlines()

    ok, dup = db.bulk_add_stok(paket_id, lines)

    await msg.edit_text(
        f"✅ <b>Stok berhasil ditambahkan!</b>\n\n"
        f"📦 Paket: {paket['nama']}\n"
        f"✅ Berhasil: {ok} akun\n"
        f"⚠️ Duplikat (skip): {dup} akun\n"
        f"📊 Total diproses: {len(lines)} baris",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📦 Lihat Stok", callback_data="admin_stok_refresh"),
            InlineKeyboardButton("🏠 Panel", callback_data="admin_panel"),
        ]])
    )


@admin_only
async def admin_input_manual_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Input manual 1 akun — pilih paket dulu."""
    q = update.callback_query
    await q.answer()

    paket_list = db.get_all_paket()
    teks = (
        "✏️ <b>Input Manual Akun Gmail</b>\n\n"
        "Pilih paket:"
    )
    kb = [[InlineKeyboardButton(p["nama"], callback_data=f"admin_manual_ke:{p['id']}")]
          for p in paket_list]
    kb.append([InlineKeyboardButton("❌ Batal", callback_data="admin_panel")])
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_manual_pilih_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    paket_id = int(q.data.split(":", 1)[1])
    await q.answer()

    paket = db.get_paket_by_id(paket_id)
    if not paket:
        await q.edit_message_text("❌ Paket tidak ditemukan.")
        return

    db.set_session(update.effective_user.id, "admin_input_manual_akun", {"paket_id": paket_id})
    await q.edit_message_text(
        f"✏️ Input akun untuk paket <b>{paket['nama']}</b>\n\n"
        "Format: <code>email|password|recovery|tgl_buat|catatan</code>\n"
        "Minimal: <code>email|password</code>\n\n"
        "Contoh:\n"
        "<code>test@gmail.com|Pass123!|rec@gmail.com|2024-01-01|fresh</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Batal", callback_data="admin_panel")
        ]])
    )


@admin_only
async def admin_terima_manual_akun(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "admin_input_manual_akun":
        return

    paket_id = session["data"]["paket_id"]
    db.clear_session(user.id)

    parts = update.message.text.strip().split("|")
    if len(parts) < 2:
        await update.message.reply_text(
            "❌ Format salah. Minimal: <code>email|password</code>",
            parse_mode="HTML"
        )
        return

    email    = parts[0].strip()
    password = parts[1].strip()
    recovery = parts[2].strip() if len(parts) > 2 else ""
    tgl_buat = parts[3].strip() if len(parts) > 3 else ""
    catatan  = parts[4].strip() if len(parts) > 4 else ""

    if not email or "@" not in email:
        await update.message.reply_text("❌ Email tidak valid.")
        return

    success = db.add_stok_gmail(paket_id, email, password, recovery, tgl_buat, catatan)
    paket   = db.get_paket_by_id(paket_id)

    if success:
        stok_now = db.get_stok_count(paket_id)
        await update.message.reply_text(
            f"✅ Akun berhasil ditambahkan!\n\n"
            f"📦 Paket: {paket['nama']}\n"
            f"📧 Email: {email}\n"
            f"📊 Stok tersedia: {stok_now} akun",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("➕ Tambah Lagi", callback_data=f"admin_manual_ke:{paket_id}"),
                InlineKeyboardButton("🏠 Panel", callback_data="admin_panel"),
            ]])
        )
    else:
        await update.message.reply_text(
            f"⚠️ Email <code>{email}</code> sudah ada di database (duplikat).",
            parse_mode="HTML"
        )


# ── Paket & Harga ──────────────────────────────────────────────────────────

@admin_only
async def admin_paket_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    paket_list = db.get_all_paket()
    teks = "⚙️ <b>Kelola Paket & Harga</b>\n\nPilih paket untuk edit:\n"

    for p in paket_list:
        aktif = "✅" if p["aktif"] else "❌"
        teks += f"\n{aktif} #{p['id']} {p['nama']} — {fmt_rupiah(p['harga'])} | stok: {p['tersedia']}"

    kb = [[InlineKeyboardButton(f"✏️ Edit #{p['id']} {p['nama']}", callback_data=f"admin_edit_paket:{p['id']}")]
          for p in paket_list]
    kb.append([InlineKeyboardButton("🏠 Panel Admin", callback_data="admin_panel")])
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_edit_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    paket_id = int(q.data.split(":", 1)[1])
    await q.answer()

    paket = db.get_paket_by_id(paket_id)
    if not paket:
        await q.edit_message_text("❌ Paket tidak ditemukan.")
        return

    db.set_session(update.effective_user.id, "admin_edit_harga", {"paket_id": paket_id})
    await q.edit_message_text(
        f"✏️ <b>Edit Paket #{paket_id}</b>\n\n"
        f"Nama: {paket['nama']}\n"
        f"Harga saat ini: {fmt_rupiah(paket['harga'])}\n\n"
        "Ketik harga baru (angka saja, contoh: <code>25000</code>):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔛 Toggle Aktif/Nonaktif", callback_data=f"admin_toggle_paket:{paket_id}")],
            [InlineKeyboardButton("❌ Batal", callback_data="admin_paket")],
        ])
    )


@admin_only
async def admin_terima_harga_baru(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "admin_edit_harga":
        return

    paket_id = session["data"]["paket_id"]
    db.clear_session(user.id)

    text = update.message.text.strip().replace(".", "").replace(",", "")
    try:
        harga = int(text)
        if harga < 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Format salah. Masukkan angka.")
        return

    db.update_paket_harga(paket_id, harga)
    paket = db.get_paket_by_id(paket_id)
    await update.message.reply_text(
        f"✅ Harga berhasil diperbarui!\n\n"
        f"📦 Paket: {paket['nama']}\n"
        f"💰 Harga baru: {fmt_rupiah(harga)}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Kelola Paket", callback_data="admin_paket"),
        ]])
    )


@admin_only
async def admin_toggle_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    paket_id = int(q.data.split(":", 1)[1])
    await q.answer()

    db.toggle_paket_aktif(paket_id)
    paket = db.get_paket_by_id(paket_id)
    status = "✅ AKTIF" if paket["aktif"] else "❌ NONAKTIF"
    await q.answer(f"Paket {paket['nama']} → {status}", show_alert=True)
    await admin_paket_menu(update, ctx)


def register(app):
    app.add_handler(CommandHandler("stok", cmd_stok))
    app.add_handler(CallbackQueryHandler(admin_stok_refresh,          pattern="^admin_stok_refresh$"))
    app.add_handler(CallbackQueryHandler(admin_upload_stok_start,     pattern="^admin_upload_stok$"))
    app.add_handler(CallbackQueryHandler(admin_upload_pilih_paket,    pattern="^admin_upload_ke:"))
    app.add_handler(CallbackQueryHandler(admin_input_manual_start,    pattern="^admin_input_manual$"))
    app.add_handler(CallbackQueryHandler(admin_manual_pilih_paket,    pattern="^admin_manual_ke:"))
    app.add_handler(CallbackQueryHandler(admin_paket_menu,            pattern="^admin_paket$"))
    app.add_handler(CallbackQueryHandler(admin_edit_paket,            pattern="^admin_edit_paket:"))
    app.add_handler(CallbackQueryHandler(admin_toggle_paket,          pattern="^admin_toggle_paket:"))
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("txt"), admin_terima_stok_file
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, admin_terima_manual_akun
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, admin_terima_harga_baru
    ))

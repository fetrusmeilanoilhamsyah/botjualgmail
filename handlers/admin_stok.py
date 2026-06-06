"""
handlers/admin_stok.py - Kelola Stok Gmail (Panel Admin)
"""
import logging
from io import StringIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from database import db
from database.db_async import adb
from middleware.auth import admin_only

logger = logging.getLogger(__name__)


def fmt_rupiah(n: int) -> str:
    return f"Rp{n:,.0f}".replace(",", ".")


def fmt_short_rupiah(n: int) -> str:
    if n >= 1000000:
        val = n / 1000000
        if val.is_integer():
            return f"{int(val)} Jt"
        return f"{val:,.1f}".replace(".", ",").replace(",0", "") + " Jt"
    elif n >= 1000:
        val = n / 1000
        if val.is_integer():
            return f"{int(val)}K"
        formatted = f"{val:,.1f}".replace(".", ",")
        if formatted.endswith(",0"):
            formatted = formatted[:-2]
        return f"{formatted}K"
    return str(n)


@admin_only
async def cmd_stok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan ringkasan stok + opsi kelola."""
    try:
        await update.message.delete()
    except Exception:
        pass

    stok_totals = await adb.get_stok_totals()
    total_tersedia = stok_totals["tersedia"]
    total_terjual  = stok_totals["terjual"]
    harga_satuan = await adb.get_harga_satuan()

    teks = (
        "<b>📦 KELOLA STOK GMAIL</b>\n\n"
        f"<blockquote>• Total Tersedia : <b>{total_tersedia:,} Pcs</b>\n"
        f"• Total Terjual  : <b>{total_terjual:,} Pcs</b>\n"
        f"• Harga Satuan   : <b>{fmt_rupiah(harga_satuan)} / Gmail</b></blockquote>"
    )

    kb = [
        [InlineKeyboardButton("Tambah Stok (Upload .txt)", callback_data="admin_upload_stok", style="primary")],
        [InlineKeyboardButton("Input Manual 1 Akun", callback_data="admin_input_manual", style="primary")],
        [InlineKeyboardButton("Kelola Paket & Harga", callback_data="admin_paket", style="primary")],
        [
            InlineKeyboardButton("Refresh", callback_data="admin_stok_refresh", style="primary"),
            InlineKeyboardButton("Kembali", callback_data="admin_panel", style="danger")
        ],
    ]
    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


@admin_only
async def admin_stok_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Di-refresh")
    stok_totals = await adb.get_stok_totals()
    total_tersedia = stok_totals["tersedia"]
    total_terjual  = stok_totals["terjual"]
    harga_satuan = await adb.get_harga_satuan()

    teks = (
        "<b>📦 KELOLA STOK GMAIL</b>\n\n"
        f"<blockquote>• Total Tersedia : <b>{total_tersedia:,} Pcs</b>\n"
        f"• Total Terjual  : <b>{total_terjual:,} Pcs</b>\n"
        f"• Harga Satuan   : <b>{fmt_rupiah(harga_satuan)} / Gmail</b></blockquote>"
    )
    kb = [
        [InlineKeyboardButton("Tambah Stok (Upload .txt)", callback_data="admin_upload_stok", style="primary")],
        [InlineKeyboardButton("Input Manual 1 Akun", callback_data="admin_input_manual", style="primary")],
        [InlineKeyboardButton("Kelola Paket & Harga", callback_data="admin_paket", style="primary")],
        [
            InlineKeyboardButton("Refresh", callback_data="admin_stok_refresh", style="primary"),
            InlineKeyboardButton("Kembali", callback_data="admin_panel", style="danger")
        ],
    ]
    import telegram
    try:
        from datetime import datetime
        now_str = datetime.now().strftime("%H:%M:%S")
        teks_update = (
            f"{teks}\n"
            f"<i>🕒 Update: {now_str}</i>"
        )
        from handlers.start import kirim_atau_edit_menu
        await kirim_atau_edit_menu(update, ctx, teks_update, InlineKeyboardMarkup(kb))
    except telegram.error.BadRequest as e:
        if "Message is not modified" not in str(e):
            raise


@admin_only
async def admin_upload_stok_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Minta admin mengupload file .txt untuk stok umum."""
    q = update.callback_query
    await q.answer()

    db.set_session(update.effective_user.id, "admin_waiting_stok_file", {"paket_id": 1, "menu_msg_id": q.message.message_id})

    teks = (
        "<b>📤 UPLOAD FILE STOK</b>\n\n"
        "Kirim file <code>.txt</code> dengan format (satu akun per baris):\n"
        "<blockquote><code>email|password|recovery|tgl_buat|catatan</code></blockquote>\n"
        "<b>Contoh:</b>\n"
        "<blockquote><code>budi@gmail.com|pass123|rec@gmail.com|2026-06-06|fresh</code></blockquote>\n"
        "• Minimal field: <code>email|password</code>\n\n"
        "Silakan kirimkan file .txt Anda sekarang..."
    )
    kb = [[InlineKeyboardButton("Batal", callback_data="admin_stok_refresh", style="danger")]]
    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


@admin_only
async def admin_upload_pilih_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pass


@admin_only
async def admin_terima_stok_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Terima file .txt stok dari admin."""
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "admin_waiting_stok_file":
        return

    # Hapus pesan upload file admin
    try:
        await update.message.delete()
    except Exception:
        pass

    paket_id = 1
    menu_msg_id = session["data"].get("menu_msg_id")
    db.clear_session(user.id)

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".txt"):
        teks_err = "Kirim file .txt ya. Pengunggahan dibatalkan."
        kb = [[InlineKeyboardButton("Kembali", callback_data="admin_stok_refresh", style="danger")]]
        if menu_msg_id:
            try:
                from handlers.start import edit_menu_caption_or_text
                await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_err, InlineKeyboardMarkup(kb))
                return
            except Exception:
                pass
        await ctx.bot.send_message(chat_id=user.id, text=teks_err, reply_markup=InlineKeyboardMarkup(kb))
        return

    msg = None
    if menu_msg_id:
        try:
            from handlers.start import edit_menu_caption_or_text
            msg = await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, "Memproses file...", None)
        except Exception:
            pass
    if not msg:
        msg = await ctx.bot.send_message(chat_id=user.id, text="Memproses file...")

    tg_file = await doc.get_file()
    content = await tg_file.download_as_bytearray()
    lines   = content.decode("utf-8", errors="ignore").splitlines()

    ok, dup = await adb.bulk_add_stok(paket_id, lines)

    teks_res = (
        f"<b>✅ UPLOAD STOK BERHASIL</b>\n\n"
        f"<blockquote>• Berhasil    : <b>{ok:,} Akun</b>\n"
        f"• Duplikat    : <b>{dup:,} Akun</b> (di-skip)\n"
        f"• Total Baris : <b>{len(lines):,} Baris</b></blockquote>"
    )
    kb = [[
        InlineKeyboardButton("Lihat Stok", callback_data="admin_stok_refresh", style="primary"),
        InlineKeyboardButton("Panel", callback_data="admin_panel", style="danger"),
    ]]
    from handlers.start import edit_menu_caption_or_text
    await edit_menu_caption_or_text(ctx, user.id, msg.message_id if hasattr(msg, "message_id") else menu_msg_id, teks_res, InlineKeyboardMarkup(kb))


@admin_only
async def admin_input_manual_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Input manual 1 akun untuk database umum."""
    q = update.callback_query
    await q.answer()

    db.set_session(update.effective_user.id, "admin_input_manual_akun", {"paket_id": 1, "menu_msg_id": q.message.message_id})
    teks = (
        "<b>✍️ INPUT MANUAL AKUN</b>\n\n"
        "Ketik detail akun dengan format:\n"
        "<blockquote><code>email|password|recovery|tgl_buat|catatan</code></blockquote>\n"
        "<b>Contoh:</b>\n"
        "<blockquote><code>budi@gmail.com|pass123|rec@gmail.com|2026-06-06|fresh</code></blockquote>\n"
        "• Minimal field: <code>email|password</code>\n\n"
        "Silakan ketik detail akun sekarang..."
    )
    kb = [[InlineKeyboardButton("Batal", callback_data="admin_stok_refresh", style="danger")]]
    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


@admin_only
async def admin_manual_pilih_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pass


@admin_only
async def admin_terima_manual_akun(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "admin_input_manual_akun":
        return

    # Hapus input text admin
    try:
        await update.message.delete()
    except Exception:
        pass

    paket_id = 1
    menu_msg_id = session["data"].get("menu_msg_id")
    db.clear_session(user.id)

    parts = update.message.text.strip().split("|")
    if len(parts) < 2:
        teks_err = "Format salah. Minimal: email|password"
        kb = [[InlineKeyboardButton("Kembali", callback_data="admin_stok_refresh", style="danger")]]
        if menu_msg_id:
            try:
                from handlers.start import edit_menu_caption_or_text
                await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_err, InlineKeyboardMarkup(kb))
                return
            except Exception:
                pass
        await ctx.bot.send_message(chat_id=user.id, text=teks_err, reply_markup=InlineKeyboardMarkup(kb))
        return

    email    = parts[0].strip()
    password = parts[1].strip()
    recovery = parts[2].strip() if len(parts) > 2 else ""
    tgl_buat = parts[3].strip() if len(parts) > 3 else ""
    catatan  = parts[4].strip() if len(parts) > 4 else ""

    if not email or "@" not in email:
        teks_err = "Email tidak valid."
        kb = [[InlineKeyboardButton("Kembali", callback_data="admin_stok_refresh", style="danger")]]
        if menu_msg_id:
            try:
                from handlers.start import edit_menu_caption_or_text
                await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_err, InlineKeyboardMarkup(kb))
                return
            except Exception:
                pass
        await ctx.bot.send_message(chat_id=user.id, text=teks_err, reply_markup=InlineKeyboardMarkup(kb))
        return

    success = await adb.add_stok_gmail(paket_id, email, password, recovery, tgl_buat, catatan)

    if success:
        stok_now = await adb.get_stok_count()
        teks_res = (
            f"<b>✅ AKUN BERHASIL DITAMBAHKAN</b>\n\n"
            f"<blockquote>• Email      : <code>{email}</code>\n"
            f"• Total Stok : <b>{stok_now:,} Akun</b></blockquote>"
        )
        kb = [[
            InlineKeyboardButton("Tambah Lagi", callback_data="admin_input_manual", style="primary"),
            InlineKeyboardButton("Kelola Stok", callback_data="admin_stok_refresh", style="danger"),
            InlineKeyboardButton("Panel Admin", callback_data="admin_panel", style="danger"),
        ]]
        if menu_msg_id:
            try:
                from handlers.start import edit_menu_caption_or_text
                await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_res, InlineKeyboardMarkup(kb))
                return
            except Exception:
                pass
        await ctx.bot.send_message(chat_id=user.id, text=teks_res, reply_markup=InlineKeyboardMarkup(kb))
    else:
        teks_res = (
            f"<b>⚠️ AKUN DUPLIKAT</b>\n\n"
            f"<blockquote>Email <code>{email}</code> sudah terdaftar di database.</blockquote>"
        )
        kb = [[InlineKeyboardButton("Kembali", callback_data="admin_stok_refresh", style="danger")]]
        if menu_msg_id:
            try:
                from handlers.start import edit_menu_caption_or_text
                await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_res, InlineKeyboardMarkup(kb))
                return
            except Exception:
                pass
        await ctx.bot.send_message(chat_id=user.id, text=teks_res, reply_markup=InlineKeyboardMarkup(kb))


# ── Paket & Harga ──────────────────────────────────────────────────────────

@admin_only
async def admin_paket_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    harga_satuan = await adb.get_harga_satuan()
    paket_list = await adb.get_all_paket()
    teks = (
        f"<b>🏷️ KELOLA PAKET & HARGA</b>\n\n"
        f"<blockquote>• Harga Satuan : <b>{fmt_rupiah(harga_satuan)} / Gmail</b></blockquote>\n"
        f"<b>Daftar Paket:</b>\n"
    )

    for p in paket_list:
        status = "🟢" if p["aktif"] else "🔴"
        teks += (
            f"<b>#{p['id']} {p['nama']}</b> ({status})\n"
            f"<blockquote>• Harga      : <b>{fmt_rupiah(p['harga'])}</b></blockquote>\n\n"
        )

    kb = [
        [InlineKeyboardButton("Edit Harga Satuan", callback_data="admin_edit_harga_satuan", style="primary")],
        [InlineKeyboardButton("Toggle Status Paket", callback_data="admin_toggle_paket_list", style="primary")],
        [
            InlineKeyboardButton("Kembali ke Stok", callback_data="admin_stok_refresh", style="danger"),
            InlineKeyboardButton("Panel Admin", callback_data="admin_panel", style="danger")
        ]
    ]
    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


@admin_only
async def admin_edit_harga_satuan_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db.set_session(update.effective_user.id, "admin_edit_harga_satuan", {"menu_msg_id": q.message.message_id})
    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(
        update, ctx,
        f"<b>💰 EDIT HARGA SATUAN</b>\n\n"
        f"<blockquote>• Harga Saat Ini : <b>{fmt_rupiah(await adb.get_harga_satuan())}</b></blockquote>\n"
        f"Ketik harga baru per 1 Gmail (hanya angka, contoh: <code>4500</code>):",
        InlineKeyboardMarkup([[InlineKeyboardButton("Batal", callback_data="admin_paket", style="danger")]])
    )


@admin_only
async def admin_terima_harga_satuan_baru(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "admin_edit_harga_satuan":
        return

    # Hapus input text admin
    try:
        await update.message.delete()
    except Exception:
        pass

    menu_msg_id = session["data"].get("menu_msg_id")
    db.clear_session(user.id)

    text = update.message.text.strip().replace(".", "").replace(",", "")
    try:
        harga = int(text)
        if harga <= 0:
            raise ValueError()
    except ValueError:
        teks_err = "Format salah. Masukkan angka positif."
        kb = [[InlineKeyboardButton("Batal", callback_data="admin_paket", style="danger")]]
        if menu_msg_id:
            try:
                from handlers.start import edit_menu_caption_or_text
                await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_err, InlineKeyboardMarkup(kb))
                return
            except Exception:
                pass
        await ctx.bot.send_message(chat_id=user.id, text=teks_err, reply_markup=InlineKeyboardMarkup(kb))
        return

    await adb.update_harga_satuan(harga)
    teks_res = (
        f"<b>✅ HARGA SELESAI DIPERBARUI</b>\n\n"
        f"<blockquote>• Harga Baru   : <b>{fmt_rupiah(harga)} / Gmail</b>\n"
        f"• Penyesuaian  : <b>Paket otomatis diperbarui</b></blockquote>"
    )
    kb = [[InlineKeyboardButton("Kembali", callback_data="admin_paket", style="danger")]]
    if menu_msg_id:
        try:
            from handlers.start import edit_menu_caption_or_text
            await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_res, InlineKeyboardMarkup(kb))
            return
        except Exception:
            pass
    await ctx.bot.send_message(chat_id=user.id, text=teks_res, reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def admin_toggle_paket_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    paket_list = await adb.get_all_paket()
    teks = (
        f"<b>⚙️ STATUS AKTIF PAKET</b>\n\n"
        f"Silakan pilih paket di bawah untuk mengaktifkan atau menonaktifkannya:"
    )
    kb = []
    for p in paket_list:
        status = "Aktif" if p["aktif"] else "Nonaktif"
        label = f"#{p['id']} {p['nama']} ({status})"
        kb.append([InlineKeyboardButton(label, callback_data=f"admin_toggle_paket:{p['id']}", style="primary")])
    kb.append([InlineKeyboardButton("Kembali", callback_data="admin_paket", style="danger")])
    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


@admin_only
async def admin_toggle_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    paket_id = int(q.data.split(":", 1)[1])
    await q.answer()

    await adb.toggle_paket_aktif(paket_id)
    paket = await adb.get_paket_by_id(paket_id)
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

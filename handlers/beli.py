"""
handlers/beli.py - Beli Akun Gmail
"""
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database import db
from database.db_async import adb
from config import ADMIN_CONTACT, ADMIN_NOTIF_CHATS

logger = logging.getLogger(__name__)

# Lock untuk mencegah spam klik ganda pada saat proses pembelian
_pending_purchases = set()


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


async def show_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan daftar paket aktif dengan stok tersedia."""
    q = update.callback_query
    await q.answer()

    paket_list = await adb.get_paket_aktif()
    from handlers.start import kirim_atau_edit_menu
    if not paket_list:
        await kirim_atau_edit_menu(
            update, ctx,
            "Katalog Paket Kosong\n\n"
            "Saat ini belum ada paket Gmail yang aktif.\n"
            f"Silakan hubungi admin untuk informasi lebih lanjut: {ADMIN_CONTACT}",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
            ]])
        )
        return

    saldo = await adb.get_saldo(update.effective_user.id)
    teks  = (
        f"<b>Katalog Gmail - Warung Gmail</b>\n"
        f"Saldo Aktif: <b>{fmt_rupiah(saldo)}</b>\n\n"
        f"Pilih salah satu paket akun fresh di bawah ini:"
    )

    keyboard = []
    temp_row = []
    for p in paket_list:
        label = f"{p['kuantitas']} Pcs — {fmt_short_rupiah(p['harga'])}"
        temp_row.append(InlineKeyboardButton(label, callback_data=f"konfirmasi_beli:{p['id']}", style="primary"))
        if len(temp_row) == 2:
            keyboard.append(temp_row)
            temp_row = []
    if temp_row:
        keyboard.append(temp_row)

    # Tombol Custom Quantity
    keyboard.append([InlineKeyboardButton("Beli Jumlah Custom", callback_data="beli_custom", style="primary")])
    keyboard.append([InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")])
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(keyboard))


async def konfirmasi_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    user     = update.effective_user
    paket_id = int(q.data.split(":", 1)[1])
    await q.answer()

    paket = await adb.get_paket_by_id(paket_id)
    if not paket:
        from handlers.start import edit_menu_caption_or_text
        await edit_menu_caption_or_text(ctx, user.id, q.message.message_id, "Paket tidak ditemukan.", None)
        return

    saldo = await adb.get_saldo(user.id)

    if paket["stok_tersedia"] < paket["kuantitas"]:
        from handlers.start import kirim_atau_edit_menu
        await kirim_atau_edit_menu(
            update, ctx,
            f"<b>Stok Tidak Mencukupi</b>\n\n"
            f"Maaf, stok untuk paket {paket['nama']} saat ini tidak mencukupi.\n"
            f"Silakan pilih paket lain atau hubungi admin.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("Pilih Paket Lain", callback_data="beli_paket", style="danger")],
                [InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")],
            ])
        )
        return

    cukup = saldo >= paket["harga"]
    status_saldo = "Saldo mencukupi" if cukup else f"Saldo kurang {fmt_short_rupiah(paket['harga'] - saldo)} ({fmt_rupiah(paket['harga'] - saldo)})"

    teks = (
        f"<b>Konfirmasi Order - Warung Gmail</b>\n\n"
        f"Item: <b>{paket['nama']}</b>\n"
        f"Harga: <b>{fmt_short_rupiah(paket['harga'])}</b> ({fmt_rupiah(paket['harga'])})\n"
        f"Masa Garansi: 24 Jam (ganti baru)\n"
        f"Saldo Anda: {fmt_rupiah(saldo)}\n"
        f"Status: {status_saldo}\n\n"
        f"Lanjutkan pembayaran menggunakan saldo?"
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

    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(keyboard))


async def show_beli_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    teks = (
        "<b>Beli Custom Quantity - Warung Gmail</b>\n\n"
        f"Rate: <b>{fmt_short_rupiah(await adb.get_harga_satuan())}</b> per pcs\n"
        f"Stok Ready: <b>{await adb.get_stok_count()} pcs</b>\n\n"
        "Masukkan jumlah akun yang ingin dibeli (angka saja, min 1):"
    )
    kb = [[InlineKeyboardButton("Batal", callback_data="beli_paket", style="danger")]]
    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))
    db.set_session(update.effective_user.id, "waiting_beli_kuantitas", {"menu_msg_id": q.message.message_id})


async def handle_beli_kuantitas_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "waiting_beli_kuantitas":
        return

    # Hapus pesan input user
    try:
        await update.message.delete()
    except Exception:
        pass

    menu_msg_id = session["data"].get("menu_msg_id")

    text = update.message.text.strip().replace(".", "").replace(",", "")
    try:
        qty = int(text)
        if qty <= 0:
            raise ValueError()
    except ValueError:
        teks_err = (
            "<b>Format input salah!</b>\n\n"
            "Masukkan jumlah akun menggunakan angka saja (contoh: 7):"
        )
        kb = [[InlineKeyboardButton("Batal", callback_data="beli_paket", style="danger")]]
        from handlers.start import edit_menu_caption_or_text
        if menu_msg_id:
            await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_err, InlineKeyboardMarkup(kb))
            return
        await update.message.reply_text(teks_err, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return

    total_stok = await adb.get_stok_count()
    if total_stok < qty:
        teks_err = (
            f"<b>Stok tidak mencukupi!</b>\n\n"
            f"Stok tersedia saat ini hanya {total_stok} pcs.\n"
            "Silakan masukkan jumlah kuantitas yang lebih kecil:"
        )
        kb = [
            [InlineKeyboardButton("Pilih Paket", callback_data="beli_paket", style="primary")],
            [InlineKeyboardButton("Batal", callback_data="beli_paket", style="danger")]
        ]
        from handlers.start import edit_menu_caption_or_text
        if menu_msg_id:
            await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_err, InlineKeyboardMarkup(kb))
            return
        await update.message.reply_text(teks_err, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return

    db.clear_session(user.id)
    db.set_session(user.id, "waiting_beli_custom_confirm", {"qty": qty, "menu_msg_id": menu_msg_id})

    harga_satuan = await adb.get_harga_satuan()
    total_harga = qty * harga_satuan
    saldo = await adb.get_saldo(user.id)

    cukup = saldo >= total_harga
    status_saldo = "Saldo mencukupi" if cukup else f"Saldo kurang {fmt_short_rupiah(total_harga - saldo)} ({fmt_rupiah(total_harga - saldo)})"

    teks = (
        f"<b>Konfirmasi Custom Order - Warung Gmail</b>\n\n"
        f"Kuantitas: <b>{qty} Pcs</b>\n"
        f"Total Biaya: <b>{fmt_short_rupiah(total_harga)}</b> ({fmt_rupiah(total_harga)})\n"
        f"Garansi: 24 Jam\n"
        f"Saldo Anda: {fmt_rupiah(saldo)}\n"
        f"Status: {status_saldo}\n\n"
        f"Lanjutkan pembayaran menggunakan saldo?"
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

    from handlers.start import edit_menu_caption_or_text
    if menu_msg_id:
        await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks, InlineKeyboardMarkup(kb))
        return
    await ctx.bot.send_message(
        chat_id=user.id,
        text=teks,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def eksekusi_beli_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = update.effective_user
    session = db.get_session(user.id)

    user_id = user.id
    if user_id in _pending_purchases:
        await q.answer("Pembelian sedang diproses, mohon tunggu...", show_alert=True)
        return
    _pending_purchases.add(user_id)

    try:
        await q.answer("Memproses...")

        from handlers.start import kirim_atau_edit_menu
        if session["state"] != "waiting_beli_custom_confirm":
            await kirim_atau_edit_menu(
                update, ctx,
                "Sesi berakhir. Silakan coba lagi dari katalog.",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("Beli Gmail", callback_data="beli_paket", style="primary")
                ]])
            )
            return

        qty = session["data"]["qty"]
        db.clear_session(user.id)

        harga_satuan = await adb.get_harga_satuan()
        total_harga = qty * harga_satuan

        saldo = await adb.get_saldo(user.id)
        if saldo < total_harga:
            await kirim_atau_edit_menu(
                update, ctx,
                f"<b>Saldo Tidak Mencukupi</b>\n\n"
                f"Saldo Anda: {fmt_rupiah(saldo)}\n"
                f"Total Biaya: {fmt_rupiah(total_harga)}\n\n"
                "Silakan top up terlebih dahulu sebelum melakukan transaksi.",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("Top Up", callback_data="topup", style="primary"),
                    InlineKeyboardButton("Batal", callback_data="beli_paket", style="danger"),
                ]])
            )
            return

        # Ambil stok global
        from handlers.start import kirim_atau_edit_menu
        akun_list = await adb.ambil_stok(jumlah=qty)
        if akun_list is None:
            await kirim_atau_edit_menu(
                update, ctx,
                "<b>Stok Habis!</b>\n\nMaaf, stok baru saja terjual habis. Silakan coba beberapa saat lagi.",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("Pilih Paket", callback_data="beli_paket", style="primary"),
                    InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger"),
                ]])
            )
            return

        # Potong saldo
        result = await adb.kurangi_saldo(user.id, total_harga, "beli", f"Beli {qty} Gmail Custom")
        if result is None:
            # Rollback stock
            await adb.rollback_stok([a["id"] for a in akun_list])
            await kirim_atau_edit_menu(
                update, ctx,
                "Gagal memotong saldo. Silakan ulangi transaksi Anda.",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
                ]])
            )
            return

        stok_ids = [a["id"] for a in akun_list]
        await adb.tandai_stok_terjual_ke(stok_ids, user.id)

        pembelian_id = await adb.create_pembelian(
            user_id=user.id,
            paket_id=99,
            harga_bayar=total_harga,
            jumlah_akun=qty,
            stok_ids=stok_ids
        )

        # Check quantity and message size
        use_file_delivery = (qty > 5) or (len(_format_akun(akun_list)) > 3000)

        if use_file_delivery:
            teks_kirim = (
                f"<b>Transaksi Sukses - Warung Gmail</b>\n\n"
                f"No. Invoice: <code>#{pembelian_id}</code>\n"
                f"Kuantitas: <b>{qty} Pcs</b>\n"
                f"Total Harga: <b>{fmt_short_rupiah(total_harga)}</b>\n"
                f"Sisa Saldo: <b>{fmt_rupiah(result['saldo_sesudah'])}</b>\n"
                f"Garansi: 24 Jam (s/d {(datetime.now() + timedelta(hours=24)).strftime('%d/%m/%Y %H:%M')} WIB)\n\n"
                f"Karena jumlah pembelian yang besar, data akun lengkap telah dikirim dalam file <b>Gmail_Order_{pembelian_id}.txt</b> di bawah ini."
            )
        else:
            akun_teks = _format_akun(akun_list)
            teks_kirim = (
                f"<b>Transaksi Sukses - Warung Gmail</b>\n\n"
                f"No. Invoice: <code>#{pembelian_id}</code>\n"
                f"Kuantitas: <b>{qty} Pcs</b>\n"
                f"Total Harga: <b>{fmt_short_rupiah(total_harga)}</b>\n"
                f"Sisa Saldo: <b>{fmt_rupiah(result['saldo_sesudah'])}</b>\n"
                f"Garansi: 24 Jam (s/d {(datetime.now() + timedelta(hours=24)).strftime('%d/%m/%Y %H:%M')} WIB)\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>DATA AKUN GMAIL</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{akun_teks}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Simpan baik-baik data akun di atas. Garansi berlaku 24 jam untuk kegagalan login pertama."
            )

        # Hapus banner photo untuk hasil pembelian agar tidak melebihi batas karakter caption
        try:
            await q.message.delete()
        except Exception:
            pass
        await ctx.bot.send_message(chat_id=user.id, text=teks_kirim, parse_mode="HTML")

        if use_file_delivery:
            import io
            # Build clean TXT file
            txt_lines = [
                f"==================================================",
                f"DATA AKUN GMAIL - INVOICE #{pembelian_id}",
                f"==================================================\n",
                "FORMAT IMPOR (Email|Password|Recovery):",
                "--------------------------------------------------"
            ]
            for a in akun_list:
                rec = a.get("recovery") or ""
                txt_lines.append(f"{a['email']}|{a['password']}|{rec}")
            txt_lines.append("--------------------------------------------------\n")
            txt_lines.append("DETAIL AKUN:")
            txt_lines.append("--------------------------------------------------")
            for i, a in enumerate(akun_list, 1):
                txt_lines.append(f"#{i}")
                txt_lines.append(f"Email   : {a['email']}")
                txt_lines.append(f"Password: {a['password']}")
                if a.get("recovery"):
                    txt_lines.append(f"Recovery: {a['recovery']}")
                if a.get("tgl_buat"):
                    txt_lines.append(f"Dibuat  : {a['tgl_buat']}")
                if a.get("catatan"):
                    txt_lines.append(f"Catatan : {a['catatan']}")
                txt_lines.append("")
            txt_lines.append("--------------------------------------------------")
            txt_lines.append("Terima kasih telah berbelanja di Warung Gmail.")
            txt_lines.append("==================================================")
            
            txt_content = "\n".join(txt_lines)
            bio = io.BytesIO(txt_content.encode("utf-8"))
            bio.name = f"Gmail_Order_{pembelian_id}.txt"
            
            try:
                await ctx.bot.send_document(
                    chat_id=user.id,
                    document=bio,
                    filename=f"Gmail_Order_{pembelian_id}.txt",
                    caption=f"Detail Akun Gmail Invoice #{pembelian_id}"
                )
            except Exception as e:
                logger.error("[beli] Gagal mengirim dokumen akun: %s", e)
                await ctx.bot.send_message(
                    chat_id=user.id,
                    text="Gagal mengirim file data akun. Silakan hubungi admin untuk bantuan."
                )

        # Notif admin
        try:
            notif = (
                f"PEMBELIAN CUSTOM BARU\n\n"
                f"User: {user.full_name} (@{user.username or '-'}) [<code>{user.id}</code>]\n"
                f"Kuantitas: {qty} Gmail\n"
                f"Total Harga: {fmt_rupiah(total_harga)}\n"
                f"ID: #{pembelian_id}"
            )
            for chat_id in ADMIN_NOTIF_CHATS:
                try:
                    await ctx.bot.send_message(chat_id=chat_id, text=notif, parse_mode="HTML")
                except Exception as e:
                    logger.warning("[beli] Gagal notif admin %d: %s", chat_id, e)
        except Exception as e:
            logger.debug("[beli] Gagal notif admin: %s", e)

        # Kirim ke live transaction feed
        try:
            from handlers.live_tx import send_live_tx, censor_name, censor_id
            from config import BOT_USERNAME
            c_name = censor_name(user.full_name)
            c_uid = censor_id(user.id)
            live_teks = (
                f"<b>#Invoice_{pembelian_id} Purchase Completed</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>User</b>: {c_name} [<code>{c_uid}</code>]\n"
                f"📦 <b>Item</b>: {qty} Akun Gmail\n"
                f"💰 <b>Total</b>: {fmt_short_rupiah(total_harga)} ({fmt_rupiah(total_harga)})\n"
                f"🕐 <b>Masa Garansi</b>: 24 Jam\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"➡️ Beli Gmail Otomatis @{BOT_USERNAME}"
            )
            await send_live_tx(ctx.bot, live_teks)
        except Exception as e:
            logger.warning("[beli] Gagal kirim live tx: %s", e)
    finally:
        _pending_purchases.discard(user_id)


async def eksekusi_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    user     = update.effective_user
    paket_id = int(q.data.split(":", 1)[1])

    user_id = user.id
    if user_id in _pending_purchases:
        await q.answer("Pembelian sedang diproses, mohon tunggu...", show_alert=True)
        return
    _pending_purchases.add(user_id)

    try:
        await q.answer("Memproses...")

        from handlers.start import kirim_atau_edit_menu
        paket = await adb.get_paket_by_id(paket_id)
        if not paket:
            await kirim_atau_edit_menu(
                update, ctx,
                "Paket tidak ditemukan.",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("Pilih Paket Lain", callback_data="beli_paket", style="danger")
                ]])
            )
            return

        saldo = await adb.get_saldo(user.id)
        if saldo < paket["harga"]:
            await kirim_atau_edit_menu(
                update, ctx,
                f"<b>Saldo Tidak Mencukupi</b>\n\n"
                f"Saldo Anda: {fmt_rupiah(saldo)}\n"
                f"Harga Paket: {fmt_rupiah(paket['harga'])}\n\n"
                "Silakan top up terlebih dahulu sebelum melakukan transaksi.",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("Top Up", callback_data="topup", style="primary"),
                    InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger"),
                ]])
            )
            return

        # Ambil stok per paket
        akun_list = await adb.ambil_stok(jumlah=paket["kuantitas"], paket_id=paket_id)
        if akun_list is None:
            await kirim_atau_edit_menu(
                update, ctx,
                "<b>Stok Habis!</b>\n\nMaaf, stok baru saja terjual habis. Silakan coba beberapa saat lagi.",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("Pilih Paket Lain", callback_data="beli_paket", style="danger"),
                    InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger"),
                ]])
            )
            return

        result = await adb.kurangi_saldo(user.id, paket["harga"], "beli", f"Beli {paket['nama']}")
        if result is None:
            # Rollback stock
            await adb.rollback_stok([a["id"] for a in akun_list])
            await kirim_atau_edit_menu(
                update, ctx,
                "Gagal memotong saldo. Silakan ulangi transaksi Anda.",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
                ]])
            )
            return

        stok_ids = [a["id"] for a in akun_list]
        await adb.tandai_stok_terjual_ke(stok_ids, user.id)

        pembelian_id = await adb.create_pembelian(
            user_id=user.id,
            paket_id=paket_id,
            harga_bayar=paket["harga"],
            jumlah_akun=paket["kuantitas"],
            stok_ids=stok_ids
        )

        # Check quantity and message size
        use_file_delivery = (paket["kuantitas"] > 5) or (len(_format_akun(akun_list)) > 3000)

        if use_file_delivery:
            teks_kirim = (
                f"<b>Transaksi Sukses - Warung Gmail</b>\n\n"
                f"No. Invoice: <code>#{pembelian_id}</code>\n"
                f"Paket: <b>{paket['nama']}</b>\n"
                f"Total Harga: <b>{fmt_short_rupiah(paket['harga'])}</b>\n"
                f"Sisa Saldo: <b>{fmt_rupiah(result['saldo_sesudah'])}</b>\n"
                f"Garansi: 24 Jam (s/d {(datetime.now() + timedelta(hours=24)).strftime('%d/%m/%Y %H:%M')} WIB)\n\n"
                f"Karena jumlah pembelian yang besar, data akun lengkap telah dikirim dalam file <b>Gmail_Order_{pembelian_id}.txt</b> di bawah ini."
            )
        else:
            akun_teks = _format_akun(akun_list)
            teks_kirim = (
                f"<b>Transaksi Sukses - Warung Gmail</b>\n\n"
                f"No. Invoice: <code>#{pembelian_id}</code>\n"
                f"Paket: <b>{paket['nama']}</b>\n"
                f"Total Harga: <b>{fmt_short_rupiah(paket['harga'])}</b>\n"
                f"Sisa Saldo: <b>{fmt_rupiah(result['saldo_sesudah'])}</b>\n"
                f"Garansi: 24 Jam (s/d {(datetime.now() + timedelta(hours=24)).strftime('%d/%m/%Y %H:%M')} WIB)\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>DATA AKUN GMAIL</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{akun_teks}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Simpan baik-baik data akun di atas. Garansi berlaku 24 jam untuk kegagalan login pertama."
            )

        # Hapus banner photo untuk hasil pembelian agar tidak melebihi batas karakter caption
        try:
            await q.message.delete()
        except Exception:
            pass
        await ctx.bot.send_message(chat_id=user.id, text=teks_kirim, parse_mode="HTML")

        if use_file_delivery:
            import io
            # Build clean TXT file
            txt_lines = [
                f"==================================================",
                f"DATA AKUN GMAIL - INVOICE #{pembelian_id}",
                f"==================================================\n",
                "FORMAT IMPOR (Email|Password|Recovery):",
                "--------------------------------------------------"
            ]
            for a in akun_list:
                rec = a.get("recovery") or ""
                txt_lines.append(f"{a['email']}|{a['password']}|{rec}")
            txt_lines.append("--------------------------------------------------\n")
            txt_lines.append("DETAIL AKUN:")
            txt_lines.append("--------------------------------------------------")
            for i, a in enumerate(akun_list, 1):
                txt_lines.append(f"#{i}")
                txt_lines.append(f"Email   : {a['email']}")
                txt_lines.append(f"Password: {a['password']}")
                if a.get("recovery"):
                    txt_lines.append(f"Recovery: {a['recovery']}")
                if a.get("tgl_buat"):
                    txt_lines.append(f"Dibuat  : {a['tgl_buat']}")
                if a.get("catatan"):
                    txt_lines.append(f"Catatan : {a['catatan']}")
                txt_lines.append("")
            txt_lines.append("--------------------------------------------------")
            txt_lines.append("Terima kasih telah berbelanja di Warung Gmail.")
            txt_lines.append("==================================================")
            
            txt_content = "\n".join(txt_lines)
            bio = io.BytesIO(txt_content.encode("utf-8"))
            bio.name = f"Gmail_Order_{pembelian_id}.txt"
            
            try:
                await ctx.bot.send_document(
                    chat_id=user.id,
                    document=bio,
                    filename=f"Gmail_Order_{pembelian_id}.txt",
                    caption=f"Detail Akun Gmail Invoice #{pembelian_id}"
                )
            except Exception as e:
                logger.error("[beli] Gagal mengirim dokumen akun: %s", e)
                await ctx.bot.send_message(
                    chat_id=user.id,
                    text="Gagal mengirim file data akun. Silakan hubungi admin untuk bantuan."
                )

        # Notif admin
        try:
            notif = (
                f"PEMBELIAN BARU\n\n"
                f"User: {user.full_name} (@{user.username or '-'}) [<code>{user.id}</code>]\n"
                f"Paket: {paket['nama']}\n"
                f"Harga: {fmt_rupiah(paket['harga'])}\n"
                f"ID: #{pembelian_id}"
            )
            for chat_id in ADMIN_NOTIF_CHATS:
                try:
                    await ctx.bot.send_message(chat_id=chat_id, text=notif, parse_mode="HTML")
                except Exception as e:
                    logger.warning("[beli] Gagal notif admin %d: %s", chat_id, e)
        except Exception as e:
            logger.debug("[beli] Gagal notif admin: %s", e)

        # Kirim ke live transaction feed
        try:
            from handlers.live_tx import send_live_tx, censor_name, censor_id
            from config import BOT_USERNAME
            c_name = censor_name(user.full_name)
            c_uid = censor_id(user.id)
            live_teks = (
                f"<b>#Invoice_{pembelian_id} Purchase Completed</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>User</b>: {c_name} [<code>{c_uid}</code>]\n"
                f"📦 <b>Paket</b>: {paket['nama']}\n"
                f"💰 <b>Total</b>: {fmt_short_rupiah(paket['harga'])} ({fmt_rupiah(paket['harga'])})\n"
                f"🕐 <b>Masa Garansi</b>: 24 Jam\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"➡️ Beli Gmail Otomatis @{BOT_USERNAME}"
            )
            await send_live_tx(ctx.bot, live_teks)
        except Exception as e:
            logger.warning("[beli] Gagal kirim live tx: %s", e)
    finally:
        _pending_purchases.discard(user_id)


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

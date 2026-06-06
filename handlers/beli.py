"""
handlers/beli.py - Beli Akun Gmail
"""
import logging
import asyncio
import uuid
import io
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database import db
from database.db_async import adb
from config import ADMIN_CONTACT, ADMIN_NOTIF_CHATS, PAKASIR_ENABLED
from handlers.topup import _buat_order_pakasir_async, generate_qr_bytes

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

    paket_list, saldo = await asyncio.gather(
        adb.get_paket_aktif(),
        adb.get_saldo(update.effective_user.id)
    )
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
    teks  = (
        f"<tg-emoji emoji-id=\"5260587686304956325\">🌐</tg-emoji> <b>KATALOG GMAIL</b>\n\n"
        f"<blockquote>• Saldo    : <b>{fmt_rupiah(saldo)}</b></blockquote>\n"
        f"Pilih salah satu paket akun fresh di bawah ini:"
    )

    keyboard = []
    temp_row = []
    for p in paket_list:
        label = f"{p['kuantitas']} Pcs — {fmt_short_rupiah(p['harga'])}"
        temp_row.append(
            InlineKeyboardButton(
                label,
                callback_data=f"konfirmasi_beli:{p['id']}",
                style="primary",
                icon_custom_emoji_id="6156923364997862692"
            )
        )
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

    paket, saldo = await asyncio.gather(
        adb.get_paket_by_id(paket_id),
        adb.get_saldo(user.id)
    )
    if not paket:
        from handlers.start import edit_menu_caption_or_text
        await edit_menu_caption_or_text(ctx, user.id, q.message.message_id, "Paket tidak ditemukan.", None)
        return

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
        f"<tg-emoji emoji-id=\"5260587686304956325\">🌐</tg-emoji> <b>KONFIRMASI ORDER</b>\n\n"
        f"<blockquote>• Item     : <b>{paket['nama']}</b>\n"
        f"• Total    : <b>{fmt_rupiah(paket['harga'])}</b>\n"
        f"• Saldo    : <b>{fmt_rupiah(saldo)}</b>\n"
        f"• Status   : <b>{status_saldo}</b></blockquote>\n"
        f"Lanjutkan pembayaran menggunakan saldo?"
    )

    if cukup:
        keyboard = [
            [InlineKeyboardButton("BELI SEKARANG", callback_data=f"eksekusi_beli:{paket_id}", style="primary")],
            [InlineKeyboardButton("Batal", callback_data="beli_paket", style="danger")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("Bayar via QRIS", callback_data=f"bayar_qris_paket:{paket_id}", style="primary")],
            [InlineKeyboardButton("Top Up Saldo", callback_data="topup", style="primary")],
            [InlineKeyboardButton("Pilih Paket Lain", callback_data="beli_paket", style="danger")],
        ]

    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(keyboard))


async def show_beli_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    harga_satuan, total_stok = await asyncio.gather(
        adb.get_harga_satuan(),
        adb.get_stok_count()
    )
    teks = (
        f"<tg-emoji emoji-id=\"5260587686304956325\">🌐</tg-emoji> <b>BELI CUSTOM</b>\n\n"
        f"<blockquote>• Rate     : <b>{fmt_rupiah(harga_satuan)} / Pcs</b>\n"
        f"• Stok     : <b>{total_stok:,} Pcs</b></blockquote>\n"
        f"Ketik jumlah Gmail yang ingin Anda beli (contoh: <code>15</code>):"
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

    harga_satuan, saldo = await asyncio.gather(
        adb.get_harga_satuan(),
        adb.get_saldo(user.id)
    )
    total_harga = qty * harga_satuan

    cukup = saldo >= total_harga
    status_saldo = "Saldo mencukupi" if cukup else f"Saldo kurang {fmt_short_rupiah(total_harga - saldo)} ({fmt_rupiah(total_harga - saldo)})"

    teks = (
        f"<tg-emoji emoji-id=\"5260587686304956325\">🌐</tg-emoji> <b>KONFIRMASI CUSTOM ORDER</b>\n\n"
        f"<blockquote>• Qty      : <b>{qty} Pcs</b>\n"
        f"• Total    : <b>{fmt_rupiah(total_harga)}</b>\n"
        f"• Saldo    : <b>{fmt_rupiah(saldo)}</b>\n"
        f"• Status   : <b>{status_saldo}</b></blockquote>\n"
        f"Lanjutkan pembayaran menggunakan saldo?"
    )

    if cukup:
        kb = [
            [InlineKeyboardButton("BELI SEKARANG", callback_data="eksekusi_beli_custom", style="primary")],
            [InlineKeyboardButton("Batal", callback_data="beli_paket", style="danger")]
        ]
    else:
        kb = [
            [InlineKeyboardButton("Bayar via QRIS", callback_data=f"bayar_qris_custom:{qty}", style="primary")],
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

        harga_satuan, saldo = await asyncio.gather(
            adb.get_harga_satuan(),
            adb.get_saldo(user.id)
        )
        total_harga = qty * harga_satuan
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
                f"<b>✅ TRANSAKSI SUKSES</b>\n\n"
                f"<blockquote>• Invoice  : <code>#{pembelian_id}</code>\n"
                f"• Qty      : <b>{qty} Pcs</b>\n"
                f"• Total    : <b>{fmt_rupiah(total_harga)}</b>\n"
                f"• Saldo    : <b>{fmt_rupiah(result['saldo_sesudah'])}</b>\n"
                f"• Garansi  : <b>24 Jam</b> (s/d {(datetime.now() + timedelta(hours=24)).strftime('%d/%m/%Y %H:%M')} WIB)</blockquote>\n"
                f"Karena jumlah pembelian besar, data akun lengkap dikirim via file txt."
            )
        else:
            akun_teks = _format_akun(akun_list)
            teks_kirim = (
                f"<b>✅ TRANSAKSI SUKSES</b>\n\n"
                f"<blockquote>• Invoice  : <code>#{pembelian_id}</code>\n"
                f"• Qty      : <b>{qty} Pcs</b>\n"
                f"• Total    : <b>{fmt_rupiah(total_harga)}</b>\n"
                f"• Saldo    : <b>{fmt_rupiah(result['saldo_sesudah'])}</b>\n"
                f"• Garansi  : <b>24 Jam</b> (s/d {(datetime.now() + timedelta(hours=24)).strftime('%d/%m/%Y %H:%M')} WIB)</blockquote>\n"
                f"<b>DATA AKUN GMAIL:</b>\n"
                f"{akun_teks}\n\n"
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
                f"<tg-emoji emoji-id=\"5260587686304956325\">🌐</tg-emoji> <b>PURCHASE COMPLETED</b>\n\n"
                f"<blockquote>• Invoice  : <code>#{pembelian_id}</code>\n"
                f"• User     : {c_name} [<code>{c_uid}</code>]\n"
                f"• Item     : <b>{qty} Pcs Gmail</b>\n"
                f"• Total    : <b>{fmt_rupiah(total_harga)}</b>\n"
                f"• Garansi  : <b>24 Jam</b></blockquote>\n"
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
        paket, saldo = await asyncio.gather(
            adb.get_paket_by_id(paket_id),
            adb.get_saldo(user.id)
        )
        if not paket:
            await kirim_atau_edit_menu(
                update, ctx,
                "Paket tidak ditemukan.",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("Pilih Paket Lain", callback_data="beli_paket", style="danger")
                ]])
            )
            return
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
                f"<b>✅ TRANSAKSI SUKSES</b>\n\n"
                f"<blockquote>• Invoice  : <code>#{pembelian_id}</code>\n"
                f"• Paket    : <b>{paket['nama']}</b>\n"
                f"• Total    : <b>{fmt_rupiah(paket['harga'])}</b>\n"
                f"• Saldo    : <b>{fmt_rupiah(result['saldo_sesudah'])}</b>\n"
                f"• Garansi  : <b>24 Jam</b> (s/d {(datetime.now() + timedelta(hours=24)).strftime('%d/%m/%Y %H:%M')} WIB)</blockquote>\n"
                f"Karena jumlah pembelian besar, data akun lengkap dikirim via file txt."
            )
        else:
            akun_teks = _format_akun(akun_list)
            teks_kirim = (
                f"<b>✅ TRANSAKSI SUKSES</b>\n\n"
                f"<blockquote>• Invoice  : <code>#{pembelian_id}</code>\n"
                f"• Paket    : <b>{paket['nama']}</b>\n"
                f"• Total    : <b>{fmt_rupiah(paket['harga'])}</b>\n"
                f"• Saldo    : <b>{fmt_rupiah(result['saldo_sesudah'])}</b>\n"
                f"• Garansi  : <b>24 Jam</b> (s/d {(datetime.now() + timedelta(hours=24)).strftime('%d/%m/%Y %H:%M')} WIB)</blockquote>\n"
                f"<b>DATA AKUN GMAIL:</b>\n"
                f"{akun_teks}\n\n"
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
                f"<tg-emoji emoji-id=\"5260587686304956325\">🌐</tg-emoji> <b>PURCHASE COMPLETED</b>\n\n"
                f"<blockquote>• Invoice  : <code>#{pembelian_id}</code>\n"
                f"• User     : {c_name} [<code>{c_uid}</code>]\n"
                f"• Item     : <b>{paket['nama']}</b>\n"
                f"• Total    : <b>{fmt_rupiah(paket['harga'])}</b>\n"
                f"• Garansi  : <b>24 Jam</b></blockquote>\n"
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


_pending_direct_checks = set()
_pending_direct_batal = set()


async def handle_bayar_qris_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    paket_id = int(q.data.split(":", 1)[1])
    await q.answer("Memproses invoice...")

    paket = await adb.get_paket_by_id(paket_id)
    if not paket:
        await q.answer("Paket tidak ditemukan.", show_alert=True)
        return

    # Cek stok
    if paket["stok_tersedia"] < paket["kuantitas"]:
        await q.answer("Stok paket ini sudah habis/tidak mencukupi.", show_alert=True)
        return

    user = update.effective_user
    amount = paket["harga"]
    order_id = f"DIR-{user.id}-{paket_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"

    await proses_buat_qris_direct(update, ctx, order_id, amount, f"Beli {paket['nama']}", user)


async def handle_bayar_qris_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    qty = int(q.data.split(":", 1)[1])
    await q.answer("Memproses invoice...")

    # Cek stok
    total_stok = await adb.get_stok_count()
    if total_stok < qty:
        await q.answer(f"Stok tidak mencukupi. Hanya tersedia {total_stok} pcs.", show_alert=True)
        return

    user = update.effective_user
    harga_satuan = await adb.get_harga_satuan()
    amount = qty * harga_satuan
    order_id = f"CST-{user.id}-{qty}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"

    await proses_buat_qris_direct(update, ctx, order_id, amount, f"Beli {qty} Gmail Custom", user)


async def proses_buat_qris_direct(update: Update, ctx: ContextTypes.DEFAULT_TYPE, order_id: str, amount: int, item_name: str, user):
    from config import PAKASIR_ENABLED
    from handlers.start import edit_menu_caption_or_text

    msg = update.callback_query.message
    await edit_menu_caption_or_text(ctx, user.id, msg.message_id, "Membuatkan QR Code... Mohon tunggu.", None)

    # Buat order Pakasir
    if PAKASIR_ENABLED:
        order_data = await _buat_order_pakasir_async(order_id, amount, user.id)
    else:
        order_data = None

    if PAKASIR_ENABLED and order_data is None:
        await edit_menu_caption_or_text(
            ctx, user.id, msg.message_id,
            "Gagal membuat QR Code. Silakan coba beberapa saat lagi atau hubungi admin.",
            None
        )
        return

    if PAKASIR_ENABLED and order_data:
        payment_number = order_data.get("payment_number", "")
        total_payment  = order_data.get("total_payment", amount)
        expired_at     = order_data.get("expired_at", "~15 menit")

        try:
            from datetime import timezone, timedelta
            jakarta_tz = timezone(timedelta(hours=7))
            exp_dt_jakarta = datetime.now(jakarta_tz) + timedelta(minutes=5)
            readable_exp = exp_dt_jakarta.strftime("%d/%m/%Y %H:%M") + " WIB (5 Menit)"
        except Exception:
            readable_exp = "5 Menit"

        qr_img = generate_qr_bytes(payment_number)

        teks = (
            f"<tg-emoji emoji-id=\"5260587686304956325\">🌐</tg-emoji> <b>INVOICE PEMBELIAN GMAIL</b>\n\n"
            f"<blockquote>• Item     : <b>{item_name}</b>\n"
            f"• Total    : <b>{fmt_rupiah(total_payment)}</b>\n"
            f"• Order ID : <code>{order_id}</code>\n"
            f"• Batas    : <b>{readable_exp}</b></blockquote>\n"
            "Scan QRIS di atas menggunakan e-wallet atau m-banking Anda.\n"
            "Akun Gmail akan otomatis dikirimkan ke chat ini setelah pembayaran sukses terverifikasi."
        )
        kb = [
            [
                InlineKeyboardButton("Cek Status Bayar", callback_data=f"cek_direct:{order_id}", style="primary"),
                InlineKeyboardButton("Batalkan", callback_data=f"batal_direct:{order_id}", style="danger")
            ]
        ]

        # Kirim foto QR terlebih dahulu sebelum menghapus pesan loading
        sent_msg = await ctx.bot.send_photo(
            chat_id=user.id,
            photo=qr_img,
            caption=teks,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(kb)
        )

        try:
            await msg.delete()
        except Exception:
            pass

        # Simpan ke DB dengan ID pesan yang baru
        await adb.create_topup(
            user_id=user.id,
            order_id=order_id,
            jumlah=amount,
            qr_chat_id=sent_msg.chat.id,
            qr_message_id=sent_msg.message_id,
        )
    else:
        # Mode manual
        from config import ADMIN_CONTACT
        teks = (
            f"<tg-emoji emoji-id=\"5260587686304956325\">🌐</tg-emoji> <b>PEMBELIAN GMAIL MANUAL</b>\n\n"
            f"<blockquote>• Item     : <b>{item_name}</b>\n"
            f"• Total    : <b>{fmt_rupiah(amount)}</b>\n"
            f"• Order ID : <code>{order_id}</code></blockquote>\n"
            f"Hubungi admin untuk verifikasi pembayaran.\n"
            f"Kontak Admin: <b>{ADMIN_CONTACT}</b>"
        )
        kb = [[InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")]]
        
        # Simpan ke DB
        await adb.create_topup(
            user_id=user.id,
            order_id=order_id,
            jumlah=amount,
            qr_chat_id=msg.chat.id,
            qr_message_id=msg.message_id,
        )
        await edit_menu_caption_or_text(ctx, user.id, msg.message_id, teks, InlineKeyboardMarkup(kb))


async def cek_direct(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    order_id = q.data.split(":", 1)[1]

    if order_id in _pending_direct_checks:
        await q.answer("⏳ Status sedang diperiksa. Mohon tunggu...", show_alert=True)
        return
    _pending_direct_checks.add(order_id)

    # ✅ Jawab callback query DULUAN agar spinner loading langsung berhenti/toast muncul
    await q.answer("🔍 Memeriksa status...")

    try:
        topup = await adb.get_topup(order_id)
        if not topup:
            try:
                await q.edit_message_caption(
                    caption="❌ Data transaksi pembelian tidak ditemukan.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
                    ]])
                )
            except Exception:
                pass
            return

        status = topup["status"]

        if status == "pending":
            from handlers.topup import _cek_status_pakasir_async
            txn = await _cek_status_pakasir_async(order_id, topup["jumlah"])
            if txn and txn.get("status") == "completed":
                was_updated = await adb.complete_topup_if_pending(order_id)
                if was_updated:
                    # Tambah saldo user
                    await adb.tambah_saldo(
                        user_id=topup["user_id"],
                        jumlah=topup["jumlah"],
                        tipe="topup",
                        keterangan="Top up via QRIS (Beli Langsung)",
                        ref_id=order_id
                    )
                    # Jalankan eksekusi direct purchase
                    await eksekusi_direct_purchase(ctx.bot, order_id, topup["user_id"], topup["jumlah"])
                    status = "completed"
            elif txn and txn.get("status") in ("expired", "cancelled"):
                status = txn.get("status")
                await adb.update_topup_status(order_id, status)

        if status == "completed":
            try:
                await q.message.delete()
            except Exception:
                pass
        elif status in ("expired", "cancelled"):
            status_teks = "Kadaluarsa" if status == "expired" else "Dibatalkan"
            try:
                await q.edit_message_caption(
                    caption=(
                        f"<b>❌ PEMBELIAN {status_teks.upper()}</b>\n\n"
                        f"<blockquote>QR Code sudah tidak berlaku. Silakan lakukan pembelian ulang.</blockquote>"
                    ),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("Katalog Gmail", callback_data="beli_paket", style="primary"),
                        InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger"),
                    ]])
                )
            except Exception:
                pass
        else:
            await q.answer("⏳ Pembayaran belum diterima. Silakan selesaikan pembayaran QRIS Anda.", show_alert=True)
    finally:
        _pending_direct_checks.discard(order_id)


async def batal_direct(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    order_id = q.data.split(":", 1)[1]

    if order_id in _pending_direct_batal:
        await q.answer("⏳ Proses pembatalan sedang berjalan...", show_alert=True)
        return
    _pending_direct_batal.add(order_id)

    # ✅ Jawab callback query DULUAN
    await q.answer("❌ Membatalkan...")

    try:
        await adb.update_topup_status(order_id, "cancelled")
        
        try:
            await q.edit_message_caption(
                caption=(
                    f"<b>❌ PEMBELIAN DIBATALKAN</b>\n\n"
                    f"<blockquote>Transaksi pembelian Anda berhasil dibatalkan.</blockquote>"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
                ]])
            )
        except Exception:
            pass
    finally:
        _pending_direct_batal.discard(order_id)


async def eksekusi_direct_purchase(bot, order_id: str, user_id: int, amount: int):
    # Lock pembelian untuk mencegah double-execution
    if user_id in _pending_purchases:
        return
    _pending_purchases.add(user_id)

    # Inisialisasi variabel untuk fail-safe rollback stok
    akun_list = None
    pembelian_sukses = False

    try:
        parts = order_id.split("-")
        is_paket = parts[0] == "DIR"

        if is_paket:
            paket_id = int(parts[2])
            paket = await adb.get_paket_by_id(paket_id)
            if not paket:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"<b>Pembayaran Berhasil!</b>\n\n"
                        f"Nominal <b>{fmt_rupiah(amount)}</b> telah ditambahkan ke saldo Anda.\n\n"
                        f"⚠️ Namun, paket yang Anda beli tidak ditemukan di database. Saldo Anda tetap aman di akun."
                    ),
                    parse_mode="HTML"
                )
                return

            if paket["stok_tersedia"] < paket["kuantitas"]:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"<b>Pembayaran Berhasil!</b>\n\n"
                        f"Nominal <b>{fmt_rupiah(amount)}</b> telah ditambahkan ke saldo Anda.\n\n"
                        f"⚠️ Namun, saat pembayaran diverifikasi, stok untuk paket <b>{paket['nama']}</b> saat ini tidak mencukupi/habis. "
                        f"Saldo Anda aman di akun. Silakan gunakan saldo ini untuk membeli kembali setelah stok diisi."
                    ),
                    parse_mode="HTML"
                )
                return

            # Ambil stok per paket
            akun_list = await adb.ambil_stok(jumlah=paket["kuantitas"], paket_id=paket_id)
            if akun_list is None:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"<b>Pembayaran Berhasil!</b>\n\n"
                        f"Nominal <b>{fmt_rupiah(amount)}</b> telah ditambahkan ke saldo Anda.\n\n"
                        f"⚠️ Namun, saat pembayaran diverifikasi, stok untuk paket <b>{paket['nama']}</b> baru saja terjual habis. "
                        f"Saldo Anda aman di akun. Silakan gunakan saldo ini untuk membeli kembali setelah stok diisi."
                    ),
                    parse_mode="HTML"
                )
                return

            # Potong saldo (Bug #5: gunakan 'amount' yang dibayar user daripada paket['harga'] dinamis)
            result = await adb.kurangi_saldo(user_id, amount, "beli", f"Beli {paket['nama']}")
            if result is None:
                raise Exception("Gagal memotong saldo (saldo tidak cukup atau db error)")

            stok_ids = [a["id"] for a in akun_list]
            await adb.tandai_stok_terjual_ke(stok_ids, user_id)

            pembelian_id = await adb.create_pembelian(
                user_id=user_id,
                paket_id=paket_id,
                harga_bayar=amount,  # Bug #5: gunakan amount
                jumlah_akun=paket["kuantitas"],
                stok_ids=stok_ids
            )
            pembelian_sukses = True

            use_file_delivery = (paket["kuantitas"] > 5) or (len(_format_akun(akun_list)) > 3000)
            if use_file_delivery:
                teks_kirim = (
                    f"<b>✅ TRANSAKSI SUKSES</b>\n\n"
                    f"<blockquote>• Invoice  : <code>#{pembelian_id}</code>\n"
                    f"• Paket    : <b>{paket['nama']}</b>\n"
                    f"• Total    : <b>{fmt_rupiah(amount)}</b>\n"
                    f"• Saldo    : <b>{fmt_rupiah(result['saldo_sesudah'])}</b>\n"
                    f"• Garansi  : <b>24 Jam</b> (s/d {(datetime.now() + timedelta(hours=24)).strftime('%d/%m/%Y %H:%M')} WIB)</blockquote>\n"
                    f"Karena jumlah pembelian besar, data akun lengkap dikirim via file txt."
                )
            else:
                akun_teks = _format_akun(akun_list)
                teks_kirim = (
                    f"<b>✅ TRANSAKSI SUKSES</b>\n\n"
                    f"<blockquote>• Invoice  : <code>#{pembelian_id}</code>\n"
                    f"• Paket    : <b>{paket['nama']}</b>\n"
                    f"• Total    : <b>{fmt_rupiah(amount)}</b>\n"
                    f"• Saldo    : <b>{fmt_rupiah(result['saldo_sesudah'])}</b>\n"
                    f"• Garansi  : <b>24 Jam</b> (s/d {(datetime.now() + timedelta(hours=24)).strftime('%d/%m/%Y %H:%M')} WIB)</blockquote>\n"
                    f"<b>DATA AKUN GMAIL:</b>\n"
                    f"{akun_teks}\n\n"
                    f"Simpan baik-baik data akun di atas. Garansi berlaku 24 jam untuk kegagalan login pertama."
                )

            await bot.send_message(chat_id=user_id, text=teks_kirim, parse_mode="HTML")

            if use_file_delivery:
                await send_txt_file_delivery(bot, user_id, pembelian_id, akun_list)

            await notify_admin_and_live_tx(bot, user_id, pembelian_id, amount, paket["nama"], paket["kuantitas"])

        else:
            qty = int(parts[2])
            total_stok = await adb.get_stok_count()
            # Bug #5: gunakan amount (harga yang dibayar user saat invoice dibuat) bukan dihitung ulang dari harga satuan saat ini
            total_harga = amount
            if total_stok < qty:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"<b>Pembayaran Berhasil!</b>\n\n"
                        f"Nominal <b>{fmt_rupiah(amount)}</b> telah ditambahkan ke saldo Anda.\n\n"
                        f"⚠️ Namun, saat pembayaran diverifikasi, stok ready ({total_stok} pcs) kurang dari jumlah pembelian ({qty} pcs). "
                        f"Saldo Anda aman di akun."
                    ),
                    parse_mode="HTML"
                )
                return

            akun_list = await adb.ambil_stok(jumlah=qty)
            if akun_list is None:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"<b>Pembayaran Berhasil!</b>\n\n"
                        f"Nominal <b>{fmt_rupiah(amount)}</b> telah ditambahkan ke saldo Anda.\n\n"
                        f"⚠️ Namun, saat pembayaran diverifikasi, stok akun baru saja terjual habis. "
                        f"Saldo Anda aman di akun."
                    ),
                    parse_mode="HTML"
                )
                return

            result = await adb.kurangi_saldo(user_id, total_harga, "beli", f"Beli {qty} Gmail Custom")
            if result is None:
                raise Exception("Gagal memotong saldo (saldo tidak cukup atau db error)")

            stok_ids = [a["id"] for a in akun_list]
            await adb.tandai_stok_terjual_ke(stok_ids, user_id)

            pembelian_id = await adb.create_pembelian(
                user_id=user_id,
                paket_id=99,
                harga_bayar=total_harga,
                jumlah_akun=qty,
                stok_ids=stok_ids
            )
            pembelian_sukses = True

            use_file_delivery = (qty > 5) or (len(_format_akun(akun_list)) > 3000)
            if use_file_delivery:
                teks_kirim = (
                    f"<b>✅ TRANSAKSI SUKSES</b>\n\n"
                    f"<blockquote>• Invoice  : <code>#{pembelian_id}</code>\n"
                    f"• Qty      : <b>{qty} Pcs</b>\n"
                    f"• Total    : <b>{fmt_rupiah(total_harga)}</b>\n"
                    f"• Saldo    : <b>{fmt_rupiah(result['saldo_sesudah'])}</b>\n"
                    f"• Garansi  : <b>24 Jam</b> (s/d {(datetime.now() + timedelta(hours=24)).strftime('%d/%m/%Y %H:%M')} WIB)</blockquote>\n"
                    f"Karena jumlah pembelian besar, data akun lengkap dikirim via file txt."
                )
            else:
                akun_teks = _format_akun(akun_list)
                teks_kirim = (
                    f"<b>✅ TRANSAKSI SUKSES</b>\n\n"
                    f"<blockquote>• Invoice  : <code>#{pembelian_id}</code>\n"
                    f"• Qty      : <b>{qty} Pcs</b>\n"
                    f"• Total    : <b>{fmt_rupiah(total_harga)}</b>\n"
                    f"• Saldo    : <b>{fmt_rupiah(result['saldo_sesudah'])}</b>\n"
                    f"• Garansi  : <b>24 Jam</b> (s/d {(datetime.now() + timedelta(hours=24)).strftime('%d/%m/%Y %H:%M')} WIB)</blockquote>\n"
                    f"<b>DATA AKUN GMAIL:</b>\n"
                    f"{akun_teks}\n\n"
                    f"Simpan baik-baik data akun di atas. Garansi berlaku 24 jam untuk kegagalan login pertama."
                )

            await bot.send_message(chat_id=user_id, text=teks_kirim, parse_mode="HTML")

            if use_file_delivery:
                await send_txt_file_delivery(bot, user_id, pembelian_id, akun_list)

            await notify_admin_and_live_tx(bot, user_id, pembelian_id, total_harga, f"{qty} Akun Gmail Custom", qty)

    except Exception as e:
        logger.exception("[beli] Error dalam eksekusi_direct_purchase: %s", e)
        # Bug #1: Kembalikan status stok (rollback) jika stok terlanjur diambil tapi proses pembelian gagal sebelum tercatat
        if akun_list and not pembelian_sukses:
            try:
                stok_ids = [a["id"] for a in akun_list]
                await adb.rollback_stok(stok_ids)
                logger.info("[beli] Berhasil rollback stok untuk %d akun: %s", len(stok_ids), stok_ids)
            except Exception as rollback_err:
                logger.error("[beli] Gagal rollback stok saat recovery: %s", rollback_err)

        # Kirim notifikasi kegagalan ke admin untuk pemulihan manual
        try:
            for admin_chat_id in ADMIN_NOTIF_CHATS:
                try:
                    await bot.send_message(
                        chat_id=admin_chat_id,
                        text=(
                            f"🚨 <b>GAGAL EKSEKUSI PEMBELIAN LANGSUNG</b>\n\n"
                            f"• Order ID: <code>{order_id}</code>\n"
                            f"• User ID: <code>{user_id}</code>\n"
                            f"• Nominal: <b>Rp {amount:,}</b>\n"
                            f"• Error: <code>{str(e)}</code>\n\n"
                            f"<i>Saldo pengguna mungkin sudah ditambahkan, tetapi produk gagal dikirim otomatis. Mohon periksa secara manual.</i>"
                        ),
                        parse_mode="HTML"
                    )
                except Exception as inner_err:
                    logger.error("Failed sending failed purchase notification to admin %s: %s", admin_chat_id, inner_err)
        except Exception as admin_err:
            logger.error("Failed to notify admins of direct purchase failure: %s", admin_err)
    finally:
        _pending_purchases.discard(user_id)


async def send_txt_file_delivery(bot, user_id: int, pembelian_id: int, akun_list: list):
    import io
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
        await bot.send_document(
            chat_id=user_id,
            document=bio,
            filename=f"Gmail_Order_{pembelian_id}.txt",
            caption=f"Detail Akun Gmail Invoice #{pembelian_id}"
        )
    except Exception as e:
        logger.error("[beli] Gagal mengirim dokumen akun: %s", e)


async def notify_admin_and_live_tx(bot, user_id: int, pembelian_id: int, harga: int, item_name: str, qty: int):
    try:
        user_row = await adb.get_user(user_id)
        full_name = dict(user_row).get("full_name", "Pengguna") if user_row else "Pengguna"
        username = dict(user_row).get("username", "-") if user_row else "-"
        notif = (
            f"PEMBELIAN BARU (QRIS DIRECT)\n\n"
            f"User: {full_name} (@{username}) [<code>{user_id}</code>]\n"
            f"Item: {item_name}\n"
            f"Harga: {fmt_rupiah(harga)}\n"
            f"ID: #{pembelian_id}"
        )
        for chat_id in ADMIN_NOTIF_CHATS:
            try:
                await bot.send_message(chat_id=chat_id, text=notif, parse_mode="HTML")
            except Exception as e:
                logger.warning("[beli] Gagal notif admin %d: %s", chat_id, e)
    except Exception as e:
        logger.debug("[beli] Gagal notif admin: %s", e)

    try:
        from handlers.live_tx import send_live_tx, censor_name, censor_id
        from config import BOT_USERNAME
        user_row = await adb.get_user(user_id)
        full_name = dict(user_row).get("full_name", "Pengguna") if user_row else "Pengguna"
        c_name = censor_name(full_name)
        c_uid = censor_id(user_id)
        live_teks = (
            f"<tg-emoji emoji-id=\"5260587686304956325\">🌐</tg-emoji> <b>PURCHASE COMPLETED (QRIS)</b>\n\n"
            f"<blockquote>• Invoice  : <code>#{pembelian_id}</code>\n"
            f"• User     : {c_name} [<code>{c_uid}</code>]\n"
            f"• Item     : <b>{item_name}</b>\n"
            f"• Total    : <b>{fmt_rupiah(harga)}</b>\n"
            f"• Garansi  : <b>24 Jam</b></blockquote>\n"
            f"➡️ Beli Gmail Otomatis @{BOT_USERNAME}"
        )
        await send_live_tx(bot, live_teks)
    except Exception as e:
        logger.warning("[beli] Gagal kirim live tx: %s", e)


def register(app):
    app.add_handler(CallbackQueryHandler(show_paket,           pattern="^beli_paket$"))
    app.add_handler(CallbackQueryHandler(show_beli_custom,     pattern="^beli_custom$"))
    app.add_handler(CallbackQueryHandler(eksekusi_beli_custom, pattern="^eksekusi_beli_custom$"))
    app.add_handler(CallbackQueryHandler(konfirmasi_beli,      pattern="^konfirmasi_beli:"))
    app.add_handler(CallbackQueryHandler(eksekusi_beli,        pattern="^eksekusi_beli:"))
    app.add_handler(CallbackQueryHandler(handle_bayar_qris_paket, pattern="^bayar_qris_paket:"))
    app.add_handler(CallbackQueryHandler(handle_bayar_qris_custom, pattern="^bayar_qris_custom:"))
    app.add_handler(CallbackQueryHandler(cek_direct,           pattern="^cek_direct:"))
    app.add_handler(CallbackQueryHandler(batal_direct,         pattern="^batal_direct:"))

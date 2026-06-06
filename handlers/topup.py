"""
handlers/topup.py - Top Up Saldo via QRIS Pakasir
"""
import logging
import uuid
import io
import qrcode
import aiohttp
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database import db
from database.db_async import adb
from config import (
    PAKASIR_ENABLED, PAKASIR_SLUG, PAKASIR_API_KEY,
    PAKASIR_SANDBOX, TOPUP_MIN, TOPUP_MAX, ADMIN_CONTACT
)

logger = logging.getLogger(__name__)

# Lock sets to prevent duplicate clicks / database conflicts
_pending_topup_checks = set()
_pending_batal = set()


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


def generate_qr_bytes(data: str) -> io.BytesIO:
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def _buat_order_pakasir_async(order_id: str, amount: int, user_id: int) -> dict | None:
    if not PAKASIR_ENABLED or not PAKASIR_API_KEY:
        return None
    url = "https://app.pakasir.com/api/transactioncreate/qris"
    payload = {
        "project":  PAKASIR_SLUG,
        "api_key":  PAKASIR_API_KEY,
        "order_id": order_id,
        "amount":   amount,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
                if r.status == 200 and data.get("payment"):
                    return data["payment"]
                logger.error("[topup] Pakasir error: %s", data)
                return None
    except Exception as e:
        logger.error("[topup] Pakasir request gagal: %s", e)
        return None


async def _cek_status_pakasir_async(order_id: str, amount: int) -> dict | None:
    if not PAKASIR_ENABLED or not PAKASIR_API_KEY:
        return None
    url = "https://app.pakasir.com/api/transactiondetail"
    params = {
        "project":  PAKASIR_SLUG,
        "api_key":  PAKASIR_API_KEY,
        "order_id": order_id,
        "amount":   amount,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
                if r.status == 200 and data.get("transaction"):
                    return data["transaction"]
        return None
    except Exception as e:
        logger.error("[topup] Pakasir status check gagal: %s", e)
        return None


async def show_topup_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    teks = (
        f"<tg-emoji emoji-id=\"5364075889669718872\">💵</tg-emoji> <b>TOP UP SALDO</b>\n\n"
        f"<blockquote>• Min Top Up : <b>{fmt_rupiah(TOPUP_MIN)}</b>\n"
        f"• Max Top Up : <b>{fmt_rupiah(TOPUP_MAX)}</b></blockquote>\n"
        f"Silakan pilih nominal preset di bawah:"
    )
    kb = [
        [
            InlineKeyboardButton("1K", callback_data="topup_nominal:1000", style="primary", icon_custom_emoji_id="6159157637050011672"),
            InlineKeyboardButton("5K", callback_data="topup_nominal:5000", style="primary", icon_custom_emoji_id="6159157637050011672"),
            InlineKeyboardButton("10K", callback_data="topup_nominal:10000", style="primary", icon_custom_emoji_id="6159157637050011672"),
        ],
        [
            InlineKeyboardButton("15K", callback_data="topup_nominal:15000", style="primary", icon_custom_emoji_id="6159157637050011672"),
            InlineKeyboardButton("20K", callback_data="topup_nominal:20000", style="primary", icon_custom_emoji_id="6159157637050011672"),
            InlineKeyboardButton("25K", callback_data="topup_nominal:25000", style="primary", icon_custom_emoji_id="6159157637050011672"),
        ],
        [
            InlineKeyboardButton("30K", callback_data="topup_nominal:30000", style="primary", icon_custom_emoji_id="6159157637050011672"),
            InlineKeyboardButton("50K", callback_data="topup_nominal:50000", style="primary", icon_custom_emoji_id="6159157637050011672"),
            InlineKeyboardButton("100K", callback_data="topup_nominal:100000", style="primary", icon_custom_emoji_id="6159157637050011672"),
        ],
        [
            InlineKeyboardButton("200K", callback_data="topup_nominal:200000", style="primary", icon_custom_emoji_id="6159157637050011672"),
            InlineKeyboardButton("500K", callback_data="topup_nominal:500000", style="primary", icon_custom_emoji_id="6159157637050011672"),
            InlineKeyboardButton("1 Juta", callback_data="topup_nominal:1000000", style="primary", icon_custom_emoji_id="6159157637050011672"),
        ],
        [
            InlineKeyboardButton("Nominal Manual", callback_data="topup_manual", style="primary"),
            InlineKeyboardButton("Batal", callback_data="menu_utama", style="danger")
        ]
    ]
    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


async def show_topup_manual_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    teks = (
        f"<tg-emoji emoji-id=\"5364075889669718872\">💵</tg-emoji> <b>TOP UP NOMINAL MANUAL</b>\n\n"
        f"<blockquote>• Minimum  : <b>{fmt_rupiah(TOPUP_MIN)}</b>\n"
        f"• Maksimum : <b>{fmt_rupiah(TOPUP_MAX)}</b></blockquote>\n"
        f"Ketik nominal top up (angka saja, contoh: <code>15000</code>):"
    )
    kb = [[InlineKeyboardButton("Batal", callback_data="topup", style="danger")]]
    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))
    db.set_session(update.effective_user.id, "waiting_topup_amount", {"menu_msg_id": q.message.message_id})


async def handle_topup_nominal_click(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    amount = int(q.data.split(":", 1)[1])
    await q.answer(f"Memproses top up {fmt_short_rupiah(amount)}...")
    await proses_topup_order(update, ctx, amount)


async def handle_topup_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "waiting_topup_amount":
        return

    # Hapus pesan input user
    try:
        await update.message.delete()
    except Exception:
        pass

    menu_msg_id = session["data"].get("menu_msg_id")

    text = update.message.text.strip().replace(".", "").replace(",", "")
    try:
        amount = int(text)
    except ValueError:
        teks_err = (
            "<b>Format input salah!</b>\n\n"
            "Masukkan nominal angka saja (contoh: 50000):"
        )
        kb = [[InlineKeyboardButton("Batal", callback_data="topup", style="danger")]]
        from handlers.start import edit_menu_caption_or_text
        if menu_msg_id:
            await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_err, InlineKeyboardMarkup(kb))
            return
        await ctx.bot.send_message(chat_id=user.id, text=teks_err, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return

    if amount < TOPUP_MIN:
        teks_err = (
            f"<b>Nominal terlalu kecil!</b>\n\n"
            f"Minimum top up adalah <b>{fmt_rupiah(TOPUP_MIN)}</b>.\n"
            "Masukkan nominal kembali:"
        )
        kb = [[InlineKeyboardButton("Batal", callback_data="topup", style="danger")]]
        from handlers.start import edit_menu_caption_or_text
        if menu_msg_id:
            await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_err, InlineKeyboardMarkup(kb))
            return
        await ctx.bot.send_message(chat_id=user.id, text=teks_err, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return

    if amount > TOPUP_MAX:
        teks_err = (
            f"<b>Nominal terlalu besar!</b>\n\n"
            f"Maksimum top up adalah <b>{fmt_rupiah(TOPUP_MAX)}</b>.\n"
            "Masukkan nominal kembali:"
        )
        kb = [[InlineKeyboardButton("Batal", callback_data="topup", style="danger")]]
        from handlers.start import edit_menu_caption_or_text
        if menu_msg_id:
            await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_err, InlineKeyboardMarkup(kb))
            return
        await ctx.bot.send_message(chat_id=user.id, text=teks_err, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return

    db.clear_session(user.id)
    await proses_topup_order(update, ctx, amount, menu_msg_id)


async def proses_topup_order(update: Update, ctx: ContextTypes.DEFAULT_TYPE, amount: int, menu_msg_id: int = None):
    user = update.effective_user
    order_id = f"TU-{user.id}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

    is_callback = update.callback_query is not None
    msg = None

    from handlers.start import edit_menu_caption_or_text
    if is_callback:
        msg = update.callback_query.message
        await edit_menu_caption_or_text(ctx, user.id, msg.message_id, "Membuatkan QR Code... Mohon tunggu.", None)
    else:
        if menu_msg_id:
            msg = await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, "Membuatkan QR Code... Mohon tunggu.", None)
        if not msg:
            msg = await ctx.bot.send_message(chat_id=user.id, text="Membuatkan QR Code... Mohon tunggu.")

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
            f"<tg-emoji emoji-id=\"5364075889669718872\">💵</tg-emoji> <b>INVOICE TOP UP</b>\n\n"
            f"<blockquote>• Order ID : <code>{order_id}</code>\n"
            f"• Nominal  : <b>{fmt_rupiah(amount)}</b>\n"
            f"• Total    : <b>{fmt_rupiah(total_payment)}</b>\n"
            f"• Batas    : <b>{readable_exp}</b></blockquote>\n"
            f"Scan QRIS di atas untuk membayar.\n"
            f"Saldo masuk otomatis setelah terverifikasi."
        )
        kb = [
            [
                InlineKeyboardButton("Cek Status Bayar", callback_data=f"cek_topup:{order_id}", style="primary"),
                InlineKeyboardButton("Batalkan", callback_data=f"batal_topup:{order_id}", style="danger")
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
        teks = (
            f"<tg-emoji emoji-id=\"5364075889669718872\">💵</tg-emoji> <b>TOP UP MANUAL</b>\n\n"
            f"<blockquote>• Order ID : <code>{order_id}</code>\n"
            f"• Nominal  : <b>{fmt_rupiah(amount)}</b></blockquote>\n"
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
        from handlers.start import edit_menu_caption_or_text
        await edit_menu_caption_or_text(ctx, user.id, msg.message_id, teks, InlineKeyboardMarkup(kb))


async def cek_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    order_id = q.data.split(":", 1)[1]

    # Anti-Spam Check
    if order_id in _pending_topup_checks:
        await q.answer("Status sedang diperiksa. Mohon tunggu sebentar...", show_alert=True)
        return
    _pending_topup_checks.add(order_id)

    try:
        await q.answer("Memeriksa status...")

        topup = await adb.get_topup(order_id)
        if not topup:
            try:
                await q.message.delete()
            except Exception:
                pass
            await ctx.bot.send_message(
                chat_id=q.from_user.id,
                text="Data transaksi top up tidak ditemukan.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
                ]])
            )
            return

        status = topup["status"]

        if status == "pending":
            txn = await _cek_status_pakasir_async(order_id, topup["jumlah"])
            if txn and txn.get("status") == "completed":
                was_updated = await adb.complete_topup_if_pending(order_id)
                if was_updated:
                    result = await adb.tambah_saldo(topup["user_id"], topup["jumlah"], "topup", "Top up via QRIS", ref_id=order_id)
                    status = "completed"
                    
                    # Kirim ke live transaction feed
                    try:
                        from handlers.live_tx import send_live_tx, censor_name, censor_id
                        from config import BOT_USERNAME
                        u = await adb.get_user(topup["user_id"])
                        c_name = censor_name(u["full_name"] if u else "Pengguna")
                        c_uid = censor_id(topup["user_id"])
                        
                        live_teks = (
                            f"<tg-emoji emoji-id=\"6156906412761946453\">💵</tg-emoji> <b>TOP UP COMPLETED</b>\n\n"
                            f"<blockquote>• Order ID : <code>{order_id}</code>\n"
                            f"• User     : {c_name} [<code>{c_uid}</code>]\n"
                            f"• Nominal  : <b>{fmt_rupiah(topup['jumlah'])}</b>\n"
                            f"• Metode   : <b>QRIS Otomatis</b></blockquote>\n"
                            f"➡️ Top Up Saldo Otomatis @{BOT_USERNAME}"
                        )
                        await send_live_tx(ctx.bot, live_teks)
                    except Exception as e:
                        logger.warning("[topup] Gagal kirim live tx: %s", e)
            elif txn and txn.get("status") in ("expired", "cancelled"):
                status = txn.get("status")
                await adb.update_topup_status(order_id, status)

        if status == "completed":
            saldo = await adb.get_saldo(topup["user_id"])
            
            # Kirim pesan notifikasi sukses terlebih dahulu
            await ctx.bot.send_message(
                chat_id=topup["user_id"],
                text=(
                    f"<b>✅ TOP UP BERHASIL</b>\n\n"
                    f"<blockquote>• Jumlah : <b>{fmt_rupiah(topup['jumlah'])}</b>\n"
                    f"• Saldo  : <b>{fmt_rupiah(saldo)}</b></blockquote>"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
                ]])
            )
            try:
                await q.message.delete()
            except Exception:
                pass

        elif status in ("expired", "cancelled"):
            status_teks = "Kadaluarsa" if status == "expired" else "Dibatalkan"
            
            # Kirim pesan status gagal terlebih dahulu
            await ctx.bot.send_message(
                chat_id=topup["user_id"],
                text=(
                    f"<b>❌ PEMBAYARAN {status_teks.upper()}</b>\n\n"
                    f"<blockquote>QR Code sudah tidak berlaku. Silakan ajukan top up baru.</blockquote>"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Top Up Lagi", callback_data="topup", style="primary"),
                    InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger"),
                ]])
            )
            try:
                await q.message.delete()
            except Exception:
                pass
        else:
            await q.answer("Pembayaran belum diterima. Silakan selesaikan pembayaran QRIS Anda.", show_alert=True)
    finally:
        _pending_topup_checks.discard(order_id)


async def batal_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    order_id = q.data.split(":", 1)[1]

    # Anti-Spam Check
    if order_id in _pending_batal:
        await q.answer("Proses pembatalan sedang berjalan...", show_alert=True)
        return
    _pending_batal.add(order_id)

    try:
        await q.answer("Membatalkan...")
        await adb.update_topup_status(order_id, "cancelled")
        
        # Kirim pesan konfirmasi batal terlebih dahulu
        await ctx.bot.send_message(
            chat_id=q.from_user.id,
            text=(
                f"<b>❌ TOP UP DIBATALKAN</b>\n\n"
                f"<blockquote>Transaksi top up Anda berhasil dibatalkan.</blockquote>"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
            ]])
        )
        try:
            await q.message.delete()
        except Exception:
            pass
    finally:
        _pending_batal.discard(order_id)


def register(app):
    app.add_handler(CallbackQueryHandler(show_topup_menu,          pattern="^topup$"))
    app.add_handler(CallbackQueryHandler(show_topup_manual_input,  pattern="^topup_manual$"))
    app.add_handler(CallbackQueryHandler(handle_topup_nominal_click, pattern="^topup_nominal:"))
    app.add_handler(CallbackQueryHandler(cek_topup,                pattern="^cek_topup:"))
    app.add_handler(CallbackQueryHandler(batal_topup,              pattern="^batal_topup:"))

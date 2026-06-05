"""
handlers/topup.py - Top Up Saldo via QRIS Pakasir
"""
import logging
import uuid
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database import db
from config import (
    PAKASIR_ENABLED, PAKASIR_SLUG, PAKASIR_API_KEY,
    PAKASIR_SANDBOX, TOPUP_MIN, TOPUP_MAX
)

logger = logging.getLogger(__name__)


def fmt_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")


def _buat_order_pakasir(order_id: str, amount: int, user_id: int) -> dict | None:
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
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if resp.status_code == 200 and data.get("payment"):
            return data["payment"]
        logger.error("[topup] Pakasir error: %s", data)
        return None
    except Exception as e:
        logger.error("[topup] Pakasir request gagal: %s", e)
        return None


def _cek_status_pakasir(order_id: str, amount: int) -> dict | None:
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
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if resp.status_code == 200 and data.get("transaction"):
            return data["transaction"]
        return None
    except Exception as e:
        logger.error("[topup] Pakasir status check gagal: %s", e)
        return None


async def show_topup_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    teks = (
        "<b>Top Up Saldo</b>\n\n"
        f"Minimum top up: <b>{fmt_rupiah(TOPUP_MIN)}</b>\n"
        f"Maksimum top up: <b>{fmt_rupiah(TOPUP_MAX)}</b>\n\n"
        "Silakan pilih nominal top up:"
    )
    kb = [
        [
            InlineKeyboardButton("Rp 1.000 (1k)", callback_data="topup_nominal:1000", style="primary"),
            InlineKeyboardButton("Rp 5.000 (5k)", callback_data="topup_nominal:5000", style="primary"),
        ],
        [
            InlineKeyboardButton("Rp 10.000 (10k)", callback_data="topup_nominal:10000", style="primary"),
            InlineKeyboardButton("Rp 20.000 (20k)", callback_data="topup_nominal:20000", style="primary"),
            InlineKeyboardButton("Rp 50.000 (50k)", callback_data="topup_nominal:50000", style="primary"),
        ],
        [
            InlineKeyboardButton("Nominal Manual", callback_data="topup_manual", style="primary")
        ],
        [
            InlineKeyboardButton("Batal", callback_data="menu_utama", style="danger")
        ]
    ]
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def show_topup_manual_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    teks = (
        "<b>Top Up Nominal Manual</b>\n\n"
        f"Minimum: <b>{fmt_rupiah(TOPUP_MIN)}</b>\n"
        f"Maksimum: <b>{fmt_rupiah(TOPUP_MAX)}</b>\n\n"
        "Ketik nominal yang ingin kamu top up (angka saja, contoh: <code>15000</code>):"
    )
    kb = [[InlineKeyboardButton("Batal", callback_data="topup", style="danger")]]
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    db.set_session(update.effective_user.id, "waiting_topup_amount", {})


async def handle_topup_nominal_click(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    amount = int(q.data.split(":", 1)[1])
    await q.answer(f"Memproses top up {fmt_rupiah(amount)}...")
    await proses_topup_order(update, ctx, amount)


async def handle_topup_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "waiting_topup_amount":
        return

    text = update.message.text.strip().replace(".", "").replace(",", "")
    try:
        amount = int(text)
    except ValueError:
        await update.message.reply_text(
            "Format salah. Ketik angka saja, contoh: <code>50000</code>",
            parse_mode="HTML"
        )
        return

    if amount < TOPUP_MIN:
        await update.message.reply_text(
            f"Minimum top up adalah <b>{fmt_rupiah(TOPUP_MIN)}</b>.",
            parse_mode="HTML"
        )
        return

    if amount > TOPUP_MAX:
        await update.message.reply_text(
            f"Maksimum top up adalah <b>{fmt_rupiah(TOPUP_MAX)}</b>.",
            parse_mode="HTML"
        )
        return

    db.clear_session(user.id)
    await proses_topup_order(update, ctx, amount)


async def proses_topup_order(update: Update, ctx: ContextTypes.DEFAULT_TYPE, amount: int):
    user = update.effective_user
    order_id = f"TU-{user.id}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

    is_callback = update.callback_query is not None

    if is_callback:
        msg = update.callback_query.message
        await msg.edit_text("Membuatkan QR Code... Mohon tunggu.")
    else:
        msg = await update.message.reply_text("Membuatkan QR Code... Mohon tunggu.")

    # Buat order Pakasir
    if PAKASIR_ENABLED:
        order_data = _buat_order_pakasir(order_id, amount, user.id)
    else:
        order_data = None

    if PAKASIR_ENABLED and order_data is None:
        await msg.edit_text("Gagal membuat QR Code. Coba lagi atau hubungi admin.")
        return

    # Simpan ke DB
    db.create_topup(
        user_id=user.id,
        order_id=order_id,
        jumlah=amount,
        qr_chat_id=msg.chat.id,
        qr_message_id=msg.message_id,
    )

    if PAKASIR_ENABLED and order_data:
        payment_url  = order_data.get("payment_url", "")
        expired_at   = order_data.get("expired_at", "~15 menit")

        teks = (
            f"<b>Top Up Saldo</b>\n\n"
            f"Nominal: <b>{fmt_rupiah(amount)}</b>\n"
            f"Order ID: <code>{order_id}</code>\n"
            f"Berlaku s/d: {expired_at}\n\n"
            f"Klik untuk bayar via QRIS: <a href='{payment_url}'>Bayar</a>\n\n"
            "Setelah pembayaran dikonfirmasi, saldo otomatis bertambah."
        )
        kb = [
            [InlineKeyboardButton("Bayar Sekarang", url=payment_url, style="primary")],
            [InlineKeyboardButton("Cek Status Bayar", callback_data=f"cek_topup:{order_id}", style="primary")],
            [InlineKeyboardButton("Batalkan", callback_data=f"batal_topup:{order_id}", style="danger")],
        ]
    else:
        # Mode manual
        teks = (
            f"<b>Top Up Saldo (Manual)</b>\n\n"
            f"Nominal: <b>{fmt_rupiah(amount)}</b>\n"
            f"Order ID: <code>{order_id}</code>\n\n"
            "Hubungi admin untuk melakukan top up manual.\n"
            f"Admin: @{ctx.bot_data.get('admin_contact', 'admin')}"
        )
        kb = [[InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")]]

    await msg.edit_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb),
                        disable_web_page_preview=True)


async def cek_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Memeriksa status...")

    order_id = q.data.split(":", 1)[1]
    topup    = db.get_topup(order_id)

    if not topup:
        await q.edit_message_text("Data topup tidak ditemukan.")
        return

    status = topup["status"]

    if status == "pending":
        txn = _cek_status_pakasir(order_id, topup["jumlah"])
        if txn and txn.get("status") == "completed":
            was_updated = db.complete_topup_if_pending(order_id)
            if was_updated:
                result = db.tambah_saldo(topup["user_id"], topup["jumlah"], "topup", "Top up via QRIS", ref_id=order_id)
                status = "completed"
        elif txn and txn.get("status") in ("expired", "cancelled"):
            status = txn.get("status")
            db.update_topup_status(order_id, status)

    if status == "completed":
        saldo = db.get_saldo(topup["user_id"])
        await q.edit_message_text(
            f"Top Up Berhasil!\n\n"
            f"Nominal: {fmt_rupiah(topup['jumlah'])}\n"
            f"Saldo saat ini: <b>{fmt_rupiah(saldo)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="primary")
            ]])
        )
    elif status == "expired":
        await q.edit_message_text(
            "Pembayaran Kadaluarsa\n\nQR Code sudah tidak berlaku. Silakan buat top up baru.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Top Up Lagi", callback_data="topup", style="primary"),
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger"),
            ]])
        )
    else:
        await q.answer("Pembayaran belum masuk. Silakan selesaikan pembayaran terlebih dahulu.", show_alert=True)


async def batal_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Topup dibatalkan.")
    order_id = q.data.split(":", 1)[1]
    db.update_topup_status(order_id, "cancelled")
    await q.edit_message_text(
        "Top up dibatalkan.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="primary")
        ]])
    )


def register(app):
    app.add_handler(CallbackQueryHandler(show_topup_menu,          pattern="^topup$"))
    app.add_handler(CallbackQueryHandler(show_topup_manual_input,  pattern="^topup_manual$"))
    app.add_handler(CallbackQueryHandler(handle_topup_nominal_click, pattern="^topup_nominal:"))
    app.add_handler(CallbackQueryHandler(cek_topup,                pattern="^cek_topup:"))
    app.add_handler(CallbackQueryHandler(batal_topup,              pattern="^batal_topup:"))

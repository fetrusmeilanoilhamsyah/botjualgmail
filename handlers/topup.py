"""
handlers/topup.py - Top Up Saldo via QRIS Pakasir
"""
import logging
import uuid
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from database import db
from config import (
    PAKASIR_ENABLED, PAKASIR_SLUG, PAKASIR_API_KEY,
    PAKASIR_SANDBOX, TOPUP_MIN, TOPUP_MAX
)

logger = logging.getLogger(__name__)

PAKASIR_BASE = "https://app.pakasir.com/api/v1"


def fmt_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")


def _buat_order_pakasir(order_id: str, amount: int, user_id: int) -> dict | None:
    """
    Buat order QRIS ke Pakasir.
    Return: {"payment_url": ..., "qr_code": ..., "expired_at": ...} atau None.
    """
    if not PAKASIR_ENABLED or not PAKASIR_API_KEY:
        return None
    url = f"{PAKASIR_BASE}/order"
    headers = {
        "Authorization": f"Bearer {PAKASIR_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "order_id":    order_id,
        "amount":      amount,
        "description": f"Top Up Saldo – User {user_id}",
        "project":     PAKASIR_SLUG,
        "sandbox":     PAKASIR_SANDBOX,
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        data = resp.json()
        if resp.status_code in (200, 201) and data.get("data"):
            return data["data"]
        logger.error("[topup] Pakasir error: %s", data)
        return None
    except Exception as e:
        logger.error("[topup] Pakasir request gagal: %s", e)
        return None


# ── Handler: Tombol Top Up ──────────────────────────────────────────────────

async def show_topup_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    teks = (
        "💳 <b>Top Up Saldo</b>\n\n"
        f"Minimum top up: <b>{fmt_rupiah(TOPUP_MIN)}</b>\n"
        f"Maksimum top up: <b>{fmt_rupiah(TOPUP_MAX)}</b>\n\n"
        "Ketik nominal yang ingin kamu top up (contoh: <code>50000</code>):"
    )
    kb = [[InlineKeyboardButton("❌ Batal", callback_data="menu_utama", style="danger")]]
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

    db.set_session(update.effective_user.id, "waiting_topup_amount", {})


async def handle_topup_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Terima input nominal top up dari user."""
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "waiting_topup_amount":
        return

    text = update.message.text.strip().replace(".", "").replace(",", "")
    try:
        amount = int(text)
    except ValueError:
        await update.message.reply_text(
            "❌ Format salah. Ketik angka saja, contoh: <code>50000</code>",
            parse_mode="HTML"
        )
        return

    if amount < TOPUP_MIN:
        await update.message.reply_text(
            f"❌ Minimum top up adalah <b>{fmt_rupiah(TOPUP_MIN)}</b>.",
            parse_mode="HTML"
        )
        return

    if amount > TOPUP_MAX:
        await update.message.reply_text(
            f"❌ Maksimum top up adalah <b>{fmt_rupiah(TOPUP_MAX)}</b>.",
            parse_mode="HTML"
        )
        return

    db.clear_session(user.id)

    # Buat order ID unik
    order_id = f"TU-{user.id}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

    msg = await update.message.reply_text(
        "⏳ Membuatkan QR Code... Mohon tunggu."
    )

    # Buat order Pakasir
    if PAKASIR_ENABLED:
        order_data = _buat_order_pakasir(order_id, amount, user.id)
    else:
        order_data = None

    if PAKASIR_ENABLED and order_data is None:
        await msg.edit_text(
            "❌ Gagal membuat QR Code. Coba lagi atau hubungi admin."
        )
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
            f"💳 <b>Top Up Saldo</b>\n\n"
            f"💰 Nominal: <b>{fmt_rupiah(amount)}</b>\n"
            f"📋 Order ID: <code>{order_id}</code>\n"
            f"⏰ Berlaku s/d: {expired_at}\n\n"
            f"🔗 <a href='{payment_url}'>Klik untuk bayar via QRIS</a>\n\n"
            "Setelah pembayaran dikonfirmasi, saldo otomatis bertambah ✅"
        )
        kb = [
            [InlineKeyboardButton("💸 Bayar Sekarang", url=payment_url, style="success")],
            [InlineKeyboardButton("🔄 Cek Status Bayar", callback_data=f"cek_topup:{order_id}", style="success")],
            [InlineKeyboardButton("❌ Batalkan", callback_data=f"batal_topup:{order_id}", style="danger")],
        ]
    else:
        # Mode manual
        teks = (
            f"💳 <b>Top Up Saldo (Manual)</b>\n\n"
            f"💰 Nominal: <b>{fmt_rupiah(amount)}</b>\n"
            f"📋 Order ID: <code>{order_id}</code>\n\n"
            "Hubungi admin untuk melakukan top up manual.\n"
            f"Admin: @{ctx.bot_data.get('admin_contact', 'admin')}"
        )
        kb = [[InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama", style="danger")]]

    await msg.edit_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb),
                        disable_web_page_preview=True)


async def cek_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Cek status topup secara manual (user klik tombol)."""
    q = update.callback_query
    await q.answer("Memeriksa status...")

    order_id = q.data.split(":", 1)[1]
    topup    = db.get_topup(order_id)

    if not topup:
        await q.edit_message_text("❌ Data topup tidak ditemukan.")
        return

    if topup["status"] == "completed":
        saldo = db.get_saldo(topup["user_id"])
        await q.edit_message_text(
            f"✅ <b>Top Up Berhasil!</b>\n\n"
            f"💰 Nominal: {fmt_rupiah(topup['jumlah'])}\n"
            f"💳 Saldo saat ini: <b>{fmt_rupiah(saldo)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama", style="success")
            ]])
        )
    elif topup["status"] == "expired":
        await q.edit_message_text(
            "⏰ <b>Pembayaran Kadaluarsa</b>\n\nQR Code sudah tidak berlaku. Silakan buat top up baru.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💳 Top Up Lagi", callback_data="topup", style="success"),
                InlineKeyboardButton("🏠 Menu", callback_data="menu_utama", style="danger"),
            ]])
        )
    else:
        await q.answer("⏳ Pembayaran belum masuk. Silakan selesaikan pembayaran terlebih dahulu.", show_alert=True)


async def batal_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Topup dibatalkan.")
    order_id = q.data.split(":", 1)[1]
    db.update_topup_status(order_id, "cancelled")
    await q.edit_message_text(
        "❌ Top up dibatalkan.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama", style="success")
        ]])
    )


def register(app):
    app.add_handler(CallbackQueryHandler(show_topup_menu, pattern="^topup$"))
    app.add_handler(CallbackQueryHandler(cek_topup,       pattern="^cek_topup:"))
    app.add_handler(CallbackQueryHandler(batal_topup,     pattern="^batal_topup:"))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_topup_input
    ))

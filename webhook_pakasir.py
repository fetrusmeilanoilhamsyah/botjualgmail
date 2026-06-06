"""
webhook_pakasir.py - Webhook Pakasir untuk Bot Jual Gmail

Endpoint: POST /webhook/gmail
Port: 8083 (BERBEDA dari botcv yang pakai 8081)

Setup nginx (jika pakai domain):
  location /webhook/gmail {
      proxy_pass http://127.0.0.1:8083/webhook/gmail;
  }
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime
from functools import partial
from typing import Optional

from aiohttp import web

logger = logging.getLogger(__name__)

# Rate limiting
_rate_cache: dict = defaultdict(list)
MAX_REQ_PER_MIN   = 20


def _rate_ok(ip: str) -> bool:
    now    = time.time()
    window = [t for t in _rate_cache[ip] if now - t < 60]
    _rate_cache[ip] = window
    if len(window) >= MAX_REQ_PER_MIN:
        return False
    _rate_cache[ip].append(now)
    return True


def _verify_hmac(raw_body: bytes, signature: str, secret: str) -> bool:
    """Verifikasi HMAC-SHA256 dari Pakasir."""
    if not secret or not signature:
        return not secret  # jika secret tidak diset, skip verifikasi
    try:
        expected = hmac.new(
            key=secret.encode(),
            msg=raw_body,
            digestmod=hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature.lower())
    except Exception as e:
        logger.error("[Webhook-gmail] HMAC verification failed: %s", e)
        return False


async def _safe_eksekusi_direct_purchase(bot, order_id: str, user_id: int, amount: int):
    """
    Wrapper aman untuk eksekusi_direct_purchase agar jika terjadi uncaught exception
    di background task, kita tetap mencatat log dan mengirim alert darurat ke admin (Bug #3).
    """
    try:
        from handlers.beli import eksekusi_direct_purchase
        await eksekusi_direct_purchase(bot, order_id, user_id, amount)
    except Exception as e:
        logger.exception("[Webhook-gmail] Uncaught exception in eksekusi_direct_purchase: %s", e)
        try:
            from config import ADMIN_NOTIF_CHATS
            for admin_chat_id in ADMIN_NOTIF_CHATS:
                try:
                    await bot.send_message(
                        chat_id=admin_chat_id,
                        text=(
                            f"🚨 <b>FATAL CRASH WINDOW (BACKGROUND TASK)</b>\n\n"
                            f"• Order ID: <code>{order_id}</code>\n"
                            f"• User ID: <code>{user_id}</code>\n"
                            f"• Nominal: <b>Rp {amount:,}</b>\n"
                            f"• Detail: <code>{str(e)}</code>\n\n"
                            f"<i>Crash terjadi di background task. Saldo user mungkin sudah bertambah tapi akun belum terkirim. Mohon cek database segera!</i>"
                        ),
                        parse_mode="HTML"
                    )
                except Exception as inner_err:
                    logger.error("Failed sending fatal alert to admin %s: %s", admin_chat_id, inner_err)
        except Exception as admin_err:
            logger.error("Failed to notify admins of fatal direct purchase crash: %s", admin_err)


async def handle_pakasir_webhook(request: web.Request) -> web.Response:
    """
    POST /webhook/gmail
    Payload Pakasir:
    {
        "order_id": "TU-123456-...",
        "amount": 50000,
        "status": "completed",
        "completed_at": "2026-06-05T..."
    }
    """
    try:
        ip = request.remote or "unknown"
        if not _rate_ok(ip):
            logger.warning("[Webhook-gmail] Rate limit: %s", ip)
            return web.Response(status=429, text="Too Many Requests")

        try:
            raw_body = await request.read()
        except Exception:
            return web.Response(status=400, text="Cannot read body")

        signature = request.headers.get("X-Pakasir-Signature", "").strip()

        try:
            payload = json.loads(raw_body)
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        order_id     = payload.get("order_id", "")
        amount       = payload.get("amount")
        status       = payload.get("status")
        completed_at = payload.get("completed_at")

        logger.info("[Webhook-gmail] Received: order=%s status=%s ip=%s", order_id, status, ip)

        if not all([order_id, amount, status]):
            return web.Response(status=400, text="Missing required fields")

        # Proses order_id topup (TU-), direct paket (DIR-), dan custom (CST-)
        if not (order_id.startswith("TU-") or order_id.startswith("DIR-") or order_id.startswith("CST-")):
            logger.warning("[Webhook-gmail] Order ID tidak dikenal: %s", order_id)
            return web.Response(status=200, text="OK")

        from database.db_async import adb

        # Ambil data topup
        topup = await adb.get_topup(order_id)
        if not topup:
            logger.warning("[Webhook-gmail] Topup tidak ditemukan: %s", order_id)
            return web.Response(status=200, text="Order not found")

        # Verifikasi HMAC
        secret = os.getenv("PAKASIR_WEBHOOK_SECRET", "")
        if secret and not _verify_hmac(raw_body, signature, secret):
            logger.error("[Webhook-gmail] HMAC gagal untuk order=%s", order_id)
            return web.Response(status=400, text="Signature invalid")

        # Verifikasi amount
        if int(amount) != topup["jumlah"]:
            logger.error(
                "[Webhook-gmail] Amount mismatch: expected=%d got=%d order=%s",
                topup["jumlah"], amount, order_id
            )
            return web.Response(status=400, text="Amount mismatch")

        # Idempoten
        if topup["status"] == "completed":
            return web.Response(status=200, text="Already processed")

        if status == "completed":
            # ATOMIC: hanya update jika masih pending
            was_updated = await adb.complete_topup_if_pending(
                order_id=order_id,
                completed_at=completed_at or datetime.now().isoformat()
            )

            if not was_updated:
                logger.info("[Webhook-gmail] Double-process skip: %s", order_id)
                return web.Response(status=200, text="Already processed")

            # Tambah saldo user
            keterangan = "Top up via QRIS (Beli Langsung)" if not order_id.startswith("TU-") else "Top up via QRIS"
            result = await adb.tambah_saldo(
                user_id=topup["user_id"],
                jumlah=topup["jumlah"],
                tipe="topup",
                keterangan=keterangan,
                ref_id=order_id
            )

            logger.info(
                "[Webhook-gmail] Topup berhasil: user=%s jumlah=%s order=%s",
                topup["user_id"], topup["jumlah"], order_id
            )

            # Hapus QR lama & notif user
            bot = request.app.get("bot")

            if bot:
                # Hapus pesan QR
                qr_chat_id    = topup.get("qr_chat_id")
                qr_message_id = topup.get("qr_message_id")
                if qr_chat_id and qr_message_id:
                    try:
                        asyncio.create_task(bot.delete_message(chat_id=qr_chat_id, message_id=qr_message_id))
                    except Exception as e:
                        logger.debug("[Webhook-gmail] Gagal hapus QR: %s", e)

                if not order_id.startswith("TU-"):
                    asyncio.create_task(_safe_eksekusi_direct_purchase(bot, order_id, topup["user_id"], topup["jumlah"]))
                else:
                    # Notif user
                    def fmt_rupiah(n):
                        return f"Rp{n:,.0f}".replace(",", ".")

                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("Beli Gmail Sekarang", callback_data="beli_paket", style="primary"),
                        InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger"),
                    ]])

                    notif_text = (
                        f"<b>Top Up Berhasil!</b>\n\n"
                        f"Nominal: <b>{fmt_rupiah(topup['jumlah'])}</b>\n"
                        f"Saldo sekarang: <b>{fmt_rupiah(result['saldo_sesudah'])}</b>\n\n"
                        "Yuk, beli akun Gmail sekarang!"
                    )

                    try:
                        asyncio.create_task(bot.send_message(
                            chat_id=topup["user_id"],
                            text=notif_text,
                            parse_mode="HTML",
                            reply_markup=kb
                        ))
                    except Exception as e:
                        logger.warning("[Webhook-gmail] Gagal notif user: %s", e)

                    # Kirim ke live transaction feed
                    try:
                        from config import CHANNEL_LIVE_TX, BOT_USERNAME
                        from handlers.live_tx import censor_name, censor_id
                        user_row = await adb.get_user(topup["user_id"])
                        c_name = censor_name(user_row["full_name"] if user_row else "Pengguna")
                        c_uid = censor_id(topup["user_id"])
                        
                        def fmt_short_rupiah(n):
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

                        live_teks = (
                            f"<b>#{order_id} Top Up Completed</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━\n"
                            f"👤 <b>User</b>: {c_name} [<code>{c_uid}</code>]\n"
                            f"💰 <b>Nominal</b>: {fmt_short_rupiah(topup['jumlah'])} ({fmt_rupiah(topup['jumlah'])})\n"
                            f"🗂️ <b>Metode</b>: QRIS Otomatis\n"
                            f"━━━━━━━━━━━━━━━━━━━━━\n"
                            f"➡️ Top Up Saldo @{BOT_USERNAME}"
                        )
                        if bot and CHANNEL_LIVE_TX:
                            asyncio.create_task(bot.send_message(
                                chat_id=CHANNEL_LIVE_TX,
                                text=live_teks,
                                parse_mode="HTML"
                            ))
                    except Exception as e:
                        logger.warning("[Webhook-gmail] Gagal kirim live tx: %s", e)

        else:
            # expired / cancelled
            await adb.update_topup_status(order_id, status)
            logger.info("[Webhook-gmail] Status update: %s → %s", order_id, status)

        return web.Response(status=200, text="OK")

    except Exception as e:
        logger.exception("[Webhook-gmail] Error tidak terduga: %s", e)
        return web.Response(status=500, text="Internal Server Error")


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "botjualgmail-webhook"})


def create_webhook_app(bot=None, main_loop=None) -> web.Application:
    app = web.Application()
    if bot:
        app["bot"] = bot
    if main_loop:
        app["main_loop"] = main_loop
    app.router.add_post("/webhook/gmail",  handle_pakasir_webhook)
    app.router.add_get("/health",          handle_health)
    return app

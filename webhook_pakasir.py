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
    expected = hmac.new(
        secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    try:
        return hmac.compare_digest(expected, signature.lower())
    except Exception:
        return False


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

        # Hanya proses order_id yang diawali "TU-" (format topup kita)
        if not order_id.startswith("TU-"):
            logger.warning("[Webhook-gmail] Order ID tidak dikenal: %s", order_id)
            return web.Response(status=200, text="OK")

        from database import db

        loop = asyncio.get_running_loop()

        # Ambil data topup
        topup = await loop.run_in_executor(None, db.get_topup, order_id)
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
            was_updated = await loop.run_in_executor(
                None,
                partial(
                    db.complete_topup_if_pending,
                    order_id=order_id,
                    completed_at=completed_at or datetime.now().isoformat()
                )
            )

            if not was_updated:
                logger.info("[Webhook-gmail] Double-process skip: %s", order_id)
                return web.Response(status=200, text="Already processed")

            # Tambah saldo user
            result = await loop.run_in_executor(
                None,
                partial(
                    db.tambah_saldo,
                    user_id=topup["user_id"],
                    jumlah=topup["jumlah"],
                    tipe="topup",
                    keterangan=f"Top up via QRIS",
                    ref_id=order_id
                )
            )

            logger.info(
                "[Webhook-gmail] Topup berhasil: user=%s jumlah=%s order=%s",
                topup["user_id"], topup["jumlah"], order_id
            )

            # Hapus QR lama & notif user
            bot       = request.app.get("bot")
            main_loop = request.app.get("main_loop")

            if bot:
                # Hapus pesan QR
                qr_chat_id    = topup.get("qr_chat_id")
                qr_message_id = topup.get("qr_message_id")
                if qr_chat_id and qr_message_id:
                    try:
                        if main_loop and main_loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                bot.delete_message(chat_id=qr_chat_id, message_id=qr_message_id),
                                main_loop
                            )
                    except Exception as e:
                        logger.debug("[Webhook-gmail] Gagal hapus QR: %s", e)

                # Notif user
                def fmt_rupiah(n):
                    return f"Rp {n:,.0f}".replace(",", ".")

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
                    if main_loop and main_loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            bot.send_message(
                                chat_id=topup["user_id"],
                                text=notif_text,
                                parse_mode="HTML",
                                reply_markup=kb
                            ),
                            main_loop
                        )
                except Exception as e:
                    logger.warning("[Webhook-gmail] Gagal notif user: %s", e)

                # Kirim ke live transaction feed (tanpa emoji, nama & ID disensor)
                try:
                    from config import CHANNEL_LIVE_TX
                    from handlers.live_tx import censor_name, censor_id
                    user_row = db.get_user(topup["user_id"])
                    c_name = censor_name(user_row["full_name"] if user_row else "Pengguna")
                    c_uid = censor_id(topup["user_id"])
                    
                    live_teks = (
                        "LIVE TOP UP\n\n"
                        f"Nominal: {fmt_rupiah(topup['jumlah'])}\n"
                        f"Metode: QRIS Otomatis\n"
                        f"User: {c_name} [{c_uid}]\n"
                        "Status: Sukses"
                    )
                    if bot and CHANNEL_LIVE_TX:
                        if main_loop and main_loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                bot.send_message(
                                    chat_id=CHANNEL_LIVE_TX,
                                    text=live_teks,
                                    parse_mode="HTML"
                                ),
                                main_loop
                            )
                except Exception as e:
                    logger.warning("[Webhook-gmail] Gagal kirim live tx: %s", e)

        else:
            # expired / cancelled
            await loop.run_in_executor(None, db.update_topup_status, order_id, status)
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


def start_webhook_server_thread(port: int = 8083, bot=None, main_loop=None):
    """Jalankan webhook di thread terpisah."""
    import threading

    def _run():
        asyncio.run(_run_server(port, bot, main_loop))

    t = threading.Thread(target=_run, daemon=True, name="webhook-gmail")
    t.start()
    logger.info("[Webhook-gmail] Thread dimulai di port %d", port)
    return t


async def _run_server(port: int, bot=None, main_loop=None):
    app    = create_webhook_app(bot, main_loop)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("[Webhook-gmail] Berjalan di port %d", port)
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await runner.cleanup()

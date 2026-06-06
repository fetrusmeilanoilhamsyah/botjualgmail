"""
main.py - Bot Jual Gmail
Entry point: registrasi semua handlers, scheduler, webhook Pakasir

Jalankan: python main.py
"""
import asyncio
import logging
import os
import sys
from datetime import datetime

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder, Defaults

# Monkeypatching removed since native telegram v22.7 supports styles directly.

# ── Patch CallbackQuery.answer to prevent duplicate network calls ──
import telegram
import time
from collections import OrderedDict

original_answer = telegram.CallbackQuery.answer
_answered_queries = OrderedDict()  # id -> timestamp

async def patched_answer(self, *args, **kwargs):
    now = time.monotonic()
    if self.id in _answered_queries:
        return
    _answered_queries[self.id] = now
    
    # Hapus entri > 60 detik (query Telegram expire 30 detik)
    cutoff = now - 60
    while _answered_queries and next(iter(_answered_queries.values())) < cutoff:
        _answered_queries.popitem(last=False)
        
    try:
        return await original_answer(self, *args, **kwargs)
    except Exception as e:
        # Catch and ignore errors if already answered or expired
        if "query is old" not in str(e).lower() and "already answered" not in str(e).lower():
            logger = logging.getLogger(__name__)
            logger.debug("CallbackQuery.answer error: %s", e)
telegram.CallbackQuery.answer = patched_answer

# ── Logging Setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)

# Suppress noisy loggers
for noisy in ("httpx", "telegram.ext.Updater", "apscheduler"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def main():
    from dotenv import load_dotenv
    load_dotenv()

    from config import BOT_TOKEN, ADMIN_IDS, WEBHOOK_PORT, HEALTH_PORT, USE_LOCAL_BOT_API, LOCAL_BOT_API_PORT
    from database.db import init_db
    from webhook_pakasir import start_webhook_server_thread

    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN tidak diset di .env!")
        sys.exit(1)

    os.makedirs("logs", exist_ok=True)

    # ── Init Database ──────────────────────────────────────────────────────────
    logger.info("🗄️ Inisialisasi database...")
    init_db()

    # ── Bangun Aplikasi Bot ────────────────────────────────────────────────────
    logger.info("📋 Build aplikasi bot...")

    builder = ApplicationBuilder().token(BOT_TOKEN)

    # Gunakan telelokal jika diaktifkan (share dengan botcv, tidak mengganggu)
    if USE_LOCAL_BOT_API:
        local_url      = f"http://127.0.0.1:{LOCAL_BOT_API_PORT}/bot"
        local_file_url = f"http://127.0.0.1:{LOCAL_BOT_API_PORT}/file/bot"
        builder = (
            builder.base_url(local_url)
            .base_file_url(local_file_url)
            .local_mode(True)
        )
        logger.info("🔌 Telelokal aktif: %s", local_url)
    else:
        logger.info("🌐 Menggunakan Telegram API resmi")

    # Set up high concurrency parameters matching botcv's performance
    app: Application = (
        builder
        .concurrent_updates(128)
        .connection_pool_size(128)
        .pool_timeout(30)
        .read_timeout(20)
        .write_timeout(20)
        .connect_timeout(10)
        .build()
    )

    # ── Register Handlers ──────────────────────────────────────────────────────
    logger.info("📋 Meregistrasi handlers...")

    # URUTAN PENTING: handlers lebih spesifik harus duluan
    # Admin handlers daftar sebelum user handlers untuk callback priority

    from handlers import (
        admin_panel,
        admin_stat,
        admin_stok,
        admin_broadcast,
        admin_garansi,
        start,
        topup,
        beli,
        referral,
        garansi,
        riwayat,
    )

    # Admin handlers
    admin_panel.register(app)
    admin_stat.register(app)
    admin_stok.register(app)
    admin_broadcast.register(app)
    admin_garansi.register(app)

    # User handlers
    start.register(app)
    topup.register(app)
    beli.register(app)
    referral.register(app)
    garansi.register(app)
    riwayat.register(app)

    # ── Admin Broadcast Start dari Panel ──────────────────────────────────────
    from telegram.ext import CallbackQueryHandler
    from database import db
    from database.db_async import adb
    from middleware.auth import admin_only

    @admin_only
    async def admin_broadcast_start_cb(update, ctx):
        q = update.callback_query
        await q.answer()
        db.set_session(update.effective_user.id, "admin_broadcast_preview", {"menu_msg_id": q.message.message_id})
        from handlers.start import kirim_atau_edit_menu
        await kirim_atau_edit_menu(
            update, ctx,
            "Broadcast Pesan\n\nKetik pesan yang ingin dikirim ke semua user.\nMendukung HTML format.",
            __import__("telegram").InlineKeyboardMarkup([[
                __import__("telegram").InlineKeyboardButton("Batal", callback_data="admin_panel", style="danger")
            ]])
        )

    app.add_handler(CallbackQueryHandler(admin_broadcast_start_cb, pattern="^admin_broadcast_start_cb$"))
    app.add_handler(CallbackQueryHandler(admin_broadcast_start_cb, pattern="^admin_broadcast_start$"))

    # ── Admin Isi Saldo Manual ────────────────────────────────────────────────
    @admin_only
    async def admin_isi_saldo_start(update, ctx):
        q = update.callback_query
        await q.answer()
        db.set_session(update.effective_user.id, "admin_isi_saldo", {"menu_msg_id": q.message.message_id})
        from handlers.start import kirim_atau_edit_menu
        await kirim_atau_edit_menu(
            update, ctx,
            "<b>Isi Saldo User - Warung Gmail</b>\n\n"
            "Format: <code>USER_ID JUMLAH KETERANGAN</code>\n\n"
            "Contoh: <code>1234567 50000 Bonus admin</code>",
            __import__("telegram").InlineKeyboardMarkup([[
                __import__("telegram").InlineKeyboardButton("Batal", callback_data="admin_panel", style="danger")
            ]])
        )

    app.add_handler(CallbackQueryHandler(admin_isi_saldo_start, pattern="^admin_isi_saldo$"))

    from telegram.ext import MessageHandler, filters as tg_filters

    @admin_only
    async def admin_proses_isi_saldo(update, ctx):
        user    = update.effective_user
        session = db.get_session(user.id)
        if session["state"] != "admin_isi_saldo":
            return

        # Hapus input text admin
        try:
            await update.message.delete()
        except Exception:
            pass

        menu_msg_id = session["data"].get("menu_msg_id")
        db.clear_session(user.id)
        try:
            parts = update.message.text.strip().split(maxsplit=2)
            uid   = int(parts[0])
            jml   = int(parts[1])
            ket   = parts[2] if len(parts) > 2 else "Manual admin"
        except (ValueError, IndexError):
            teks_err = "Format salah. Contoh: <code>1234567 50000 Bonus</code>"
            kb = [[InlineKeyboardButton("Batal", callback_data="admin_panel", style="danger")]]
            if menu_msg_id:
                try:
                    from handlers.start import edit_menu_caption_or_text
                    await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_err, InlineKeyboardMarkup(kb))
                    return
                except Exception:
                    pass
            await ctx.bot.send_message(chat_id=user.id, text=teks_err, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
            return

        result = await adb.tambah_saldo(uid, jml, "manual", ket, ref_id=f"admin_{user.id}")
        teks_res = (
            f"Saldo berhasil ditambahkan!\n\n"
            f"User ID: {uid}\n"
            f"Ditambah: Rp {jml:,}\n"
            f"Saldo baru: Rp {result['saldo_sesudah']:,}"
        )
        kb = [[InlineKeyboardButton("Panel Admin", callback_data="admin_panel", style="success")]]

        if menu_msg_id:
            try:
                from handlers.start import edit_menu_caption_or_text
                await edit_menu_caption_or_text(ctx, user.id, menu_msg_id, teks_res, InlineKeyboardMarkup(kb))
            except Exception:
                await ctx.bot.send_message(chat_id=user.id, text=teks_res, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await ctx.bot.send_message(chat_id=user.id, text=teks_res, reply_markup=InlineKeyboardMarkup(kb))

        try:
            await ctx.bot.send_message(
                chat_id=uid,
                text=f"Saldo kamu bertambah Rp {jml:,}\nKeterangan: {ket}\nSaldo: Rp {result['saldo_sesudah']:,}"
            )
        except Exception:
            pass


    # ── Centralized Routers ──────────────────────────────────────────────────
    async def central_text_router(update, ctx):
        user = update.effective_user
        if not user:
            return
        session = db.get_session(user.id)
        state = session.get("state")
        if not state:
            return

        from handlers import (
            topup,
            beli,
            garansi,
            admin_broadcast,
            admin_stok,
            admin_garansi,
        )

        if state == "waiting_topup_amount":
            await topup.handle_topup_input(update, ctx)
        elif state == "waiting_beli_kuantitas":
            await beli.handle_beli_kuantitas_input(update, ctx)
        elif state == "waiting_garansi_alasan":
            await garansi.handle_garansi_alasan(update, ctx)
        elif state == "admin_broadcast_preview":
            await admin_broadcast.admin_broadcast_preview(update, ctx)
        elif state == "admin_isi_saldo":
            await admin_proses_isi_saldo(update, ctx)
        elif state == "admin_input_manual_akun":
            await admin_stok.admin_terima_manual_akun(update, ctx)
        elif state == "admin_edit_harga_satuan":
            await admin_stok.admin_terima_harga_satuan_baru(update, ctx)
        elif state == "admin_tolak_garansi_alasan":
            await admin_garansi.admin_terima_alasan_tolak(update, ctx)

    async def central_document_router(update, ctx):
        user = update.effective_user
        if not user:
            return
        session = db.get_session(user.id)
        state = session.get("state")
        if not state:
            return

        from handlers import admin_stok

        if state == "admin_waiting_stok_file":
            await admin_stok.admin_terima_stok_file(update, ctx)

    app.add_handler(MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, central_text_router))
    app.add_handler(MessageHandler(tg_filters.Document.ALL, central_document_router))

    # ── Bot Commands ───────────────────────────────────────────────────────────
    async def post_init(application: Application):
        await application.bot.set_my_commands([
            BotCommand("start",       "Menu utama"),
            BotCommand("stat",        "Statistik (admin)"),
            BotCommand("broadcast",   "Broadcast pesan (admin)"),
            BotCommand("stok",        "Kelola stok (admin)"),
            BotCommand("garansi_list","Daftar klaim garansi (admin)"),
            BotCommand("admin",       "Panel admin"),
        ])
        logger.info("✅ Bot commands berhasil diset")

        # Preload banner cache
        try:
            from handlers.start import _load_banner_cache_on_startup
            await _load_banner_cache_on_startup(application.bot)
        except Exception as e:
            logger.error("Failed to load banner cache on startup: %s", e)

        # Start webhook Pakasir on main event loop
        try:
            from webhook_pakasir import create_webhook_app
            from aiohttp import web
            loop = asyncio.get_running_loop()
            app = create_webhook_app(application.bot, loop)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
            await site.start()
            logger.info("🔗 Webhook Pakasir aktif di port %d (main event loop)", WEBHOOK_PORT)
        except Exception as e:
            logger.error("Failed to start Webhook server on main event loop: %s", e)


    app.post_init = post_init

    # ── Scheduler (APScheduler) ───────────────────────────────────────────────
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler(timezone="Asia/Jakarta")

        # Expire topup lama setiap 5 menit
        scheduler.add_job(
            lambda: __import__("database.db", fromlist=["expire_old_topups"]).expire_old_topups(20),
            "interval", minutes=5, id="expire_topups"
        )

        async def post_init_with_scheduler(application: Application):
            await post_init(application)
            scheduler.start()
            logger.info("⏰ Scheduler dimulai")

        app.post_init = post_init_with_scheduler

    except ImportError:
        logger.warning("⚠️ APScheduler tidak terinstal, scheduler dinonaktifkan.")

    # ── Error Handler ─────────────────────────────────────────────────────────
    async def error_handler(update, ctx):
        logger.error("[main] Error: %s", ctx.error, exc_info=ctx.error)
        if update and update.effective_user:
            try:
                target = update.message or (update.callback_query.message if update.callback_query else None)
                if target:
                    await target.reply_text("⚠️ Terjadi error. Coba lagi nanti.")
            except Exception:
                pass

    app.add_error_handler(error_handler)

    # ── Run ───────────────────────────────────────────────────────────────────
    logger.info("🤖 Bot Jual Gmail mulai polling...")
    logger.info("   Admin IDs: %s", ADMIN_IDS)
    logger.info("   Webhook port: %d", WEBHOOK_PORT)

    app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()

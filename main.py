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

# ── Monkeypatch InlineKeyboardButton untuk mendukung warna/style (telelokal) ──
import telegram
class StyledInlineKeyboardButton(telegram.InlineKeyboardButton):
    __slots__ = ("style", "icon_custom_emoji_id")
    def __init__(self, text, style=None, icon_custom_emoji_id=None, **kwargs):
        super().__init__(text=text, **kwargs)
        self._frozen = False
        self.style = style
        self.icon_custom_emoji_id = icon_custom_emoji_id
        self._frozen = True

telegram.InlineKeyboardButton = StyledInlineKeyboardButton

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
        local_url      = f"http://localhost:{LOCAL_BOT_API_PORT}/bot"
        local_file_url = f"http://localhost:{LOCAL_BOT_API_PORT}/file/bot"
        builder = builder.base_url(local_url).base_file_url(local_file_url)
        logger.info("🔌 Telelokal aktif: %s", local_url)
    else:
        logger.info("🌐 Menggunakan Telegram API resmi")

    app: Application = builder.build()

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
    from middleware.auth import admin_only

    @admin_only
    async def admin_broadcast_start_cb(update, ctx):
        from handlers.admin_broadcast import cmd_broadcast_start
        q = update.callback_query
        await q.answer()
        db.set_session(update.effective_user.id, "admin_broadcast_preview", {})
        await q.edit_message_text(
            "📢 <b>Broadcast Pesan</b>\n\nKetik pesan yang ingin dikirim ke semua user.\nMendukung HTML format.",
            parse_mode="HTML",
            reply_markup=__import__("telegram").InlineKeyboardMarkup([[
                __import__("telegram").InlineKeyboardButton("❌ Batal", callback_data="admin_panel")
            ]])
        )

    app.add_handler(CallbackQueryHandler(admin_broadcast_start_cb, pattern="^admin_broadcast_start_cb$"))
    app.add_handler(CallbackQueryHandler(admin_broadcast_start_cb, pattern="^admin_broadcast_start$"))

    # ── Admin Isi Saldo Manual ────────────────────────────────────────────────
    @admin_only
    async def admin_isi_saldo_start(update, ctx):
        q = update.callback_query
        await q.answer()
        db.set_session(update.effective_user.id, "admin_isi_saldo", {})
        await q.edit_message_text(
            "💰 <b>Isi Saldo User</b>\n\n"
            "Format: <code>USER_ID JUMLAH KETERANGAN</code>\n\n"
            "Contoh: <code>1234567 50000 Bonus admin</code>",
            parse_mode="HTML",
            reply_markup=__import__("telegram").InlineKeyboardMarkup([[
                __import__("telegram").InlineKeyboardButton("❌ Batal", callback_data="admin_panel")
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
        db.clear_session(user.id)
        try:
            parts = update.message.text.strip().split(maxsplit=2)
            uid   = int(parts[0])
            jml   = int(parts[1])
            ket   = parts[2] if len(parts) > 2 else "Manual admin"
        except (ValueError, IndexError):
            await update.message.reply_text("❌ Format salah. Contoh: <code>1234567 50000 Bonus</code>", parse_mode="HTML")
            return
        result = db.tambah_saldo(uid, jml, "manual", ket, ref_id=f"admin_{user.id}")
        await update.message.reply_text(
            f"✅ Saldo berhasil ditambahkan!\n\n"
            f"👤 User ID: {uid}\n"
            f"💰 Ditambah: Rp {jml:,}\n"
            f"💳 Saldo baru: Rp {result['saldo_sesudah']:,}"
        )
        try:
            await ctx.bot.send_message(
                chat_id=uid,
                text=f"✅ Saldo kamu bertambah Rp {jml:,}\nKeterangan: {ket}\nSaldo: Rp {result['saldo_sesudah']:,}"
            )
        except Exception:
            pass

    app.add_handler(MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, admin_proses_isi_saldo))

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

        # Start webhook Pakasir
        loop = asyncio.get_event_loop()
        start_webhook_server_thread(
            port=WEBHOOK_PORT,
            bot=application.bot,
            main_loop=loop
        )
        logger.info("🔗 Webhook Pakasir aktif di port %d", WEBHOOK_PORT)

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

"""
handlers/start.py - Menu utama & /start
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from database import db
from config import ADMIN_IDS, ADMIN_CONTACT

logger = logging.getLogger(__name__)

MENU_UTAMA = "menu_utama"


def fmt_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    query = update.callback_query

    # Referral dari deep link: /start ref_12345
    if ctx.args and ctx.args[0].startswith("ref_") and not query:
        ref_id_str = ctx.args[0][4:]
        try:
            referrer_id = int(ref_id_str)
            db.upsert_user(user.id, user.username or "", user.full_name or "")
            if db.set_referral(user.id, referrer_id):
                # Referral valid → bonus ke referrer (anti spam dicek di sini)
                await _proses_referral_bonus(ctx, referrer_id, user)
        except (ValueError, Exception) as e:
            logger.warning("[start] referral error: %s", e)

    db.upsert_user(user.id, user.username or "", user.full_name or "")
    await _show_menu(update, ctx)


async def _show_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user      = update.effective_user
    user_data = db.get_user(user.id)
    saldo     = user_data["saldo"] if user_data else 0
    stats     = db.get_store_stats()

    is_admin = user.id in ADMIN_IDS

    teks = (
        f"               <b>« G M A I L   S T O R E »</b>\n\n"
        f"Halo <b>{user.first_name}</b>! Selamat datang di Gmail Store.\n"
        f"Penyedia akun Gmail berkualitas, fresh, dan bergaransi 24 jam.\n\n"
        f"📊 <b>STATISTIK TOKO</b>\n"
        f"• Akun Terjual : <b>{stats['akun_terjual']:,} Akun</b>\n"
        f"• Stok Tersedia: <b>{stats['stok_tersedia']:,} Akun</b>\n"
        f"• Total User   : <b>{stats['total_user']:,} Pengguna</b>\n\n"
        f"💰 <b>SALDO KAMU</b>\n"
        f"• Saldo: <b>{fmt_rupiah(saldo)}</b>\n\n"
        f"Silakan pilih menu di bawah ini:"
    )

    keyboard = [
        [
            InlineKeyboardButton("💳 Top Up Saldo", callback_data="topup", style="success"),
            InlineKeyboardButton("🛒 Beli Gmail",   callback_data="beli_paket", style="success"),
        ],
        [
            InlineKeyboardButton("👥 Referral",      callback_data="referral", style="primary"),
            InlineKeyboardButton("🛡️ Klaim Garansi", callback_data="garansi", style="danger"),
        ],
        [
            InlineKeyboardButton("📋 Riwayat Beli",   callback_data="riwayat_beli", style="primary"),
            InlineKeyboardButton("📊 Riwayat Mutasi", callback_data="riwayat_mutasi", style="primary"),
        ],
        [
            InlineKeyboardButton("💬 Chat Admin", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}", style="primary"),
            InlineKeyboardButton("👤 Info Akun",  callback_data="info_akun", style="primary"),
        ],
    ]

    if is_admin:
        keyboard.append([
            InlineKeyboardButton("⚙️ PANEL ADMIN", callback_data="admin_panel", style="success"),
        ])

    markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(
                teks, parse_mode="HTML", reply_markup=markup
            )
        except Exception:
            await update.callback_query.message.reply_text(
                teks, parse_mode="HTML", reply_markup=markup
            )
    else:
        await update.message.reply_text(teks, parse_mode="HTML", reply_markup=markup)


async def info_akun(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = update.effective_user
    await q.answer()

    u = db.get_user(user.id)
    if not u:
        await q.edit_message_text("❌ Akun tidak ditemukan.")
        return

    ref_stats = db.get_referral_stats(user.id)
    saldo     = fmt_rupiah(u["saldo"])
    teks = (
        f"👤 <b>Info Akun</b>\n\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"👤 Nama: {user.full_name or '-'}\n"
        f"📌 Username: @{user.username or '-'}\n\n"
        f"💰 Saldo: <b>{saldo}</b>\n"
        f"👥 Total Referral: {ref_stats['referral_count']} orang\n"
        f"📅 Bergabung: {u['joined_at'][:10]}\n"
    )

    kb = [[InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama", style="danger")]]
    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def _proses_referral_bonus(ctx: ContextTypes.DEFAULT_TYPE, referrer_id: int, new_user):
    """Beri bonus saldo ke referrer, dengan deteksi bot/spam."""
    import time
    from config import REFERRAL_BONUS, REFERRAL_SPAM_WINDOW, REFERRAL_SPAM_THRESHOLD

    # Cek sudah banned
    if db.is_referral_banned(referrer_id):
        return

    # Track waktu referral masuk (anti-bot)
    key = f"ref_times_{referrer_id}"
    now = time.time()
    times = ctx.bot_data.get(key, [])
    times = [t for t in times if now - t < REFERRAL_SPAM_WINDOW]
    times.append(now)
    ctx.bot_data[key] = times

    if len(times) >= REFERRAL_SPAM_THRESHOLD:
        # Deteksi bot — ban referral referrer
        db.ban_referral(referrer_id)
        logger.warning("[referral] User %d dideteksi spam referral → BANNED", referrer_id)
        try:
            await ctx.bot.send_message(
                chat_id=referrer_id,
                text=(
                    "⚠️ <b>Fitur referral kamu dinonaktifkan!</b>\n\n"
                    "Kami mendeteksi aktivitas mencurigakan: "
                    f"{len(times)} akun mendaftar dalam {REFERRAL_SPAM_WINDOW} detik.\n\n"
                    "Jika ini kesalahan, hubungi admin."
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass
        return

    # Tambah saldo bonus ke referrer
    db.increment_referral_count(referrer_id)
    db.tambah_saldo(
        referrer_id, REFERRAL_BONUS, "referral",
        f"Referral dari {new_user.full_name or new_user.id}",
        ref_id=str(new_user.id)
    )

    try:
        await ctx.bot.send_message(
            chat_id=referrer_id,
            text=(
                f"🎉 <b>Bonus Referral!</b>\n\n"
                f"Temanmu <b>{new_user.full_name or 'seseorang'}</b> baru saja bergabung.\n"
                f"💰 Kamu mendapat bonus <b>Rp {REFERRAL_BONUS:,}</b>!\n\n"
                f"Terus sebarkan linkmu untuk dapat lebih banyak bonus 🚀"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.debug("[referral] Gagal notif referrer %d: %s", referrer_id, e)


def register(app):
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cmd_start,   pattern="^menu_utama$"))
    app.add_handler(CallbackQueryHandler(info_akun,   pattern="^info_akun$"))

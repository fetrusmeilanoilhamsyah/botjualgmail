"""
handlers/start.py - Menu utama & /start
"""
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from database import db
from config import ADMIN_IDS, ADMIN_CONTACT, CHANNEL_LIVE_TX

logger = logging.getLogger(__name__)

MENU_UTAMA = "menu_utama"
BANNER_PATH = "BANNERBOTGMAIL.png"


def fmt_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")


async def kirim_atau_edit_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE, teks: str, markup: InlineKeyboardMarkup):
    """
    Helper untuk mengirim atau mengedit menu dengan menyertakan banner.
    Jika banner ada, akan dikirim/di-edit sebagai foto (banner tetap nempel).
    Jika banner tidak ada, fallback ke teks biasa.
    """
    user = update.effective_user
    q = update.callback_query
    
    banner_file_id = None
    if os.path.exists(BANNER_PATH):
        try:
            current_mtime = str(int(os.path.getmtime(BANNER_PATH)))
        except Exception:
            current_mtime = ""
            
        cached_file_id = db.get_setting("banner_file_id")
        cached_mtime = db.get_setting("banner_mtime")
        
        if cached_file_id and cached_mtime == current_mtime:
            banner_file_id = cached_file_id
        else:
            # Upload pertama kali langsung sebagai menu
            try:
                logger.info("Uploading banner as menu...")
                with open(BANNER_PATH, "rb") as f:
                    if q:
                        try:
                            await q.message.delete()
                        except Exception:
                            pass
                        sent = await ctx.bot.send_photo(
                            chat_id=user.id,
                            photo=f,
                            caption=teks,
                            parse_mode="HTML",
                            reply_markup=markup
                        )
                    else:
                        sent = await update.message.reply_photo(
                            photo=f,
                            caption=teks,
                            parse_mode="HTML",
                            reply_markup=markup
                        )
                banner_file_id = sent.photo[-1].file_id
                db.set_setting("banner_file_id", banner_file_id)
                db.set_setting("banner_mtime", current_mtime)
                return
            except Exception as e:
                logger.error("Failed to upload banner: %s", e)
                banner_file_id = None

    if banner_file_id and len(teks) <= 1000:
        if q:
            await q.answer()
            if q.message.photo:
                # Edit caption secara inline (sangat cepat, banner tetap nempel!)
                try:
                    await q.edit_message_caption(caption=teks, parse_mode="HTML", reply_markup=markup)
                except Exception:
                    # Fallback jika gagal edit
                    try:
                        await q.message.delete()
                    except Exception:
                        pass
                    await ctx.bot.send_photo(chat_id=user.id, photo=banner_file_id, caption=teks, parse_mode="HTML", reply_markup=markup)
            else:
                # Pesan lama adalah teks, hapus dan kirim foto banner baru
                try:
                    await q.message.delete()
                except Exception:
                    pass
                await ctx.bot.send_photo(chat_id=user.id, photo=banner_file_id, caption=teks, parse_mode="HTML", reply_markup=markup)
        else:
            # Command /start atau input non-callback
            await ctx.bot.send_photo(chat_id=user.id, photo=banner_file_id, caption=teks, parse_mode="HTML", reply_markup=markup)
    else:
        # Fallback teks biasa jika banner tidak ada
        if q:
            await q.answer()
            if q.message.photo:
                try:
                    await q.message.delete()
                except Exception:
                    pass
                await ctx.bot.send_message(chat_id=user.id, text=teks, parse_mode="HTML", reply_markup=markup)
            else:
                try:
                    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=markup)
                except Exception:
                    await ctx.bot.send_message(chat_id=user.id, text=teks, parse_mode="HTML", reply_markup=markup)
        else:
            await update.message.reply_text(teks, parse_mode="HTML", reply_markup=markup)


async def edit_menu_caption_or_text(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, teks: str, markup: InlineKeyboardMarkup):
    """
    Helper untuk mengedit pesan menu yang mungkin berupa foto (banner) atau teks biasa.
    """
    try:
        return await ctx.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=teks,
            parse_mode="HTML",
            reply_markup=markup
        )
    except Exception:
        try:
            return await ctx.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=teks,
                parse_mode="HTML",
                reply_markup=markup
            )
        except Exception:
            try:
                return await ctx.bot.send_message(
                    chat_id=chat_id,
                    text=teks,
                    parse_mode="HTML",
                    reply_markup=markup
                )
            except Exception:
                return None


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
    
    user_data = db.get_user(user.id)
    saldo     = user_data["saldo"] if user_data else 0
    stats     = db.get_store_stats()

    is_admin = user.id in ADMIN_IDS

    teks = (
        f"               <b>« WARUNG GMAIL »</b>\n\n"
        f"Halo <b>{user.first_name}</b>! Selamat datang di Warung Gmail.\n"
        f"Penyedia akun Gmail berkualitas, fresh, dan bergaransi 24 jam.\n\n"
        f"<b>STATISTIK TOKO</b>\n"
        f"• Akun Terjual : <b>{stats['akun_terjual']:,} Akun</b>\n"
        f"• Stok Tersedia: <b>{stats['stok_tersedia']:,} Akun</b>\n"
        f"• Total User   : <b>{stats['total_user']:,} Pengguna</b>\n\n"
        f"<b>SALDO KAMU</b>\n"
        f"• Saldo: <b>{fmt_rupiah(saldo)}</b>\n\n"
        f"Silakan pilih menu di bawah ini:"
    )

    admin_contacts = [c.strip() for c in ADMIN_CONTACT.split(",")]
    channel_url = f"https://t.me/{CHANNEL_LIVE_TX.lstrip('@')}" if CHANNEL_LIVE_TX else "https://t.me/warunggmail"

    keyboard = [
        [
            InlineKeyboardButton("Top Up Saldo", callback_data="topup", style="success"),
            InlineKeyboardButton("Beli Gmail",   callback_data="beli_paket", style="success"),
        ],
        [
            InlineKeyboardButton("Referral",      callback_data="referral", style="success"),
            InlineKeyboardButton("Klaim Garansi", callback_data="garansi", style="success"),
        ],
        [
            InlineKeyboardButton("Riwayat Beli",   callback_data="riwayat_beli", style="success"),
            InlineKeyboardButton("Riwayat Mutasi", callback_data="riwayat_mutasi", style="success"),
        ],
    ]

    contact_row = []
    if len(admin_contacts) == 1:
        contact_row.append(InlineKeyboardButton("Chat Admin", url=f"https://t.me/{admin_contacts[0].lstrip('@')}", style="success"))
    else:
        for idx, contact in enumerate(admin_contacts, 1):
            contact_row.append(InlineKeyboardButton(f"Chat Admin {idx}", url=f"https://t.me/{contact.lstrip('@')}", style="success"))

    if len(contact_row) == 1:
        contact_row.append(InlineKeyboardButton("Live Transaksi", url=channel_url, style="success"))
        keyboard.append(contact_row)
        keyboard.append([InlineKeyboardButton("Info Akun", callback_data="info_akun", style="success")])
    else:
        keyboard.append(contact_row)
        keyboard.append([
            InlineKeyboardButton("Live Transaksi", url=channel_url, style="success"),
            InlineKeyboardButton("Info Akun", callback_data="info_akun", style="success")
        ])

    if is_admin:
        keyboard.append([
            InlineKeyboardButton("PANEL ADMIN", callback_data="admin_panel", style="danger"),
        ])

    markup = InlineKeyboardMarkup(keyboard)
    await kirim_atau_edit_menu(update, ctx, teks, markup)


async def info_akun(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = update.effective_user

    u = db.get_user(user.id)
    if not u:
        await q.answer("Akun tidak ditemukan.")
        return

    ref_stats = db.get_referral_stats(user.id)
    saldo     = fmt_rupiah(u["saldo"])
    teks = (
        f"<b>Info Akun</b>\n\n"
        f"ID: <code>{user.id}</code>\n"
        f"Nama: {user.full_name or '-'}\n"
        f"Username: @{user.username or '-'}\n\n"
        f"Saldo: <b>{saldo}</b>\n"
        f"Total Referral: {ref_stats['referral_count']} orang\n"
        f"Bergabung: {u['joined_at'][:10]}\n"
    )

    kb = [[InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")]]
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


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
                    "<b>Fitur referral kamu dinonaktifkan!</b>\n\n"
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
                f"<b>Bonus Referral!</b>\n\n"
                f"Temanmu <b>{new_user.full_name or 'seseorang'}</b> baru saja bergabung.\n"
                f"Kamu mendapat bonus <b>Rp {REFERRAL_BONUS:,}</b>!\n\n"
                f"Terus sebarkan linkmu untuk dapat lebih banyak bonus"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.debug("[referral] Gagal notif referrer %d: %s", referrer_id, e)

    # Kirim ke live transaction feed (tanpa emoji, nama & ID disensor)
    try:
        from handlers.live_tx import send_live_tx, censor_name, censor_id
        
        ref_user = db.get_user(referrer_id)
        referrer_name = ref_user["full_name"] if ref_user else "Admin"
        
        c_ref_name = censor_name(new_user.full_name or str(new_user.id))
        c_ref_id = censor_id(new_user.id)
        c_referrer_name = censor_name(referrer_name)
        c_referrer_id = censor_id(referrer_id)
        
        live_teks = (
            "LIVE REFERRAL\n\n"
            f"Teman bergabung: {c_ref_name} [{c_ref_id}]\n"
            f"Pengundang: {c_referrer_name} [{c_referrer_id}]\n"
            f"Bonus: Rp {REFERRAL_BONUS:,}\n"
            "Status: Sukses"
        )
        await send_live_tx(ctx.bot, live_teks)
    except Exception as e:
        logger.warning("[referral] Gagal kirim live tx: %s", e)


def register(app):
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cmd_start,   pattern="^menu_utama$"))
    app.add_handler(CallbackQueryHandler(info_akun,   pattern="^info_akun$"))

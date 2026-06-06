"""
handlers/start.py - Menu utama & /start
"""
import logging
import os
import time
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from database import db
from database.db_async import adb
from config import ADMIN_IDS, ADMIN_CONTACT, CHANNEL_LIVE_TX

logger = logging.getLogger(__name__)

MENU_UTAMA = "menu_utama"
BANNER_PATH = "BANNERBOTGMAIL.png"


def fmt_rupiah(n: int) -> str:
    return f"Rp{n:,.0f}".replace(",", ".")


_banner_cache = {
    "file_id": None,
    "mtime": None,
    "last_checked": 0
}


async def _load_banner_cache_on_startup(bot=None, chat_id=None):
    """Panggil sekali di post_init"""
    cached_file_id = await adb.get_setting("banner_file_id")
    if cached_file_id:
        _banner_cache["file_id"] = cached_file_id
        _banner_cache["mtime"] = await adb.get_setting("banner_mtime")
        _banner_cache["last_checked"] = time.time()
        logger.info("✅ Banner cache loaded on startup: file_id=%s, mtime=%s", cached_file_id, _banner_cache["mtime"])


async def kirim_atau_edit_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE, teks: str, markup: InlineKeyboardMarkup):
    """
    Helper untuk mengirim atau mengedit menu dengan menyertakan banner.
    Jika banner ada, akan dikirim/di-edit sebagai foto (banner tetap nempel!).
    Jika banner tidak ada, fallback ke teks biasa.
    """
    user = update.effective_user
    q = update.callback_query
    
    import time
    now = time.time()
    
    # ── LANGKAH 2: Cek banner (dengan throttling disk check 60 detik) ──────────
    # Throttle disk checks to at most once every 60 seconds
    if _banner_cache["mtime"] is not None and now - _banner_cache["last_checked"] < 60:
        current_mtime = _banner_cache["mtime"]
    else:
        try:
            current_mtime = str(int(os.path.getmtime(BANNER_PATH))) if os.path.exists(BANNER_PATH) else ""
        except Exception:
            current_mtime = ""
        _banner_cache["last_checked"] = now

    if _banner_cache["file_id"] is not None and _banner_cache["mtime"] == current_mtime:
        banner_file_id = _banner_cache["file_id"]
    else:
        banner_file_id = None
        if current_mtime:
            cached_file_id = await adb.get_setting("banner_file_id")
            cached_mtime = await adb.get_setting("banner_mtime")
            
            if cached_file_id and cached_mtime == current_mtime:
                banner_file_id = cached_file_id
                _banner_cache["file_id"] = banner_file_id
                _banner_cache["mtime"] = current_mtime
                _banner_cache["last_checked"] = now
            else:
                # Upload banner pertama kali
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
                    await adb.set_setting("banner_file_id", banner_file_id)
                    await adb.set_setting("banner_mtime", current_mtime)
                    _banner_cache["file_id"] = banner_file_id
                    _banner_cache["mtime"] = current_mtime
                    _banner_cache["last_checked"] = now
                    return
                except Exception as e:
                    logger.error("Failed to upload banner: %s", e)
                    banner_file_id = None
        else:
            _banner_cache["file_id"] = None
            _banner_cache["mtime"] = None
            _banner_cache["last_checked"] = now

    # ── LANGKAH 3: Kirim/Edit pesan menu ──────────────────────────────────────
    if banner_file_id and len(teks) <= 1000:
        if q:
            if q.message.photo:
                # FAST PATH: Pesan lama sudah foto → edit caption saja (1 API call, sangat cepat!)
                try:
                    await q.edit_message_caption(caption=teks, parse_mode="HTML", reply_markup=markup)
                    return
                except Exception:
                    pass
            # Pesan lama adalah teks → hapus dan kirim foto banner
            # (Tidak bisa mengubah teks menjadi foto via edit, jadi perlu delete+send)
            try:
                await q.message.delete()
            except Exception:
                pass
            await ctx.bot.send_photo(
                chat_id=user.id,
                photo=banner_file_id,
                caption=teks,
                parse_mode="HTML",
                reply_markup=markup
            )
        else:
            # Command /start atau input non-callback
            await ctx.bot.send_photo(chat_id=user.id, photo=banner_file_id, caption=teks, parse_mode="HTML", reply_markup=markup)
    else:
        # Fallback: tidak ada banner atau teks > 1000 karakter
        if q:
            if q.message.photo:
                # Pesan lama adalah foto, hapus dan kirim teks
                try:
                    await q.message.delete()
                except Exception:
                    pass
                await ctx.bot.send_message(chat_id=user.id, text=teks, parse_mode="HTML", reply_markup=markup)
            else:
                # Pesan lama sudah teks → edit langsung (1 API call!)
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

    if query:
        await query.answer()

    # Only register/update user profile on fresh command inputs (e.g. /start command)
    # Skipping this on callback queries avoids redundant database writes on every main menu navigation.
    if not query:
        await adb.upsert_user(user.id, user.username or "", user.full_name or "")
        
        # Referral dari deep link: /start ref_12345
        if ctx.args and ctx.args[0].startswith("ref_"):
            ref_id_str = ctx.args[0][4:]
            try:
                referrer_id = int(ref_id_str)
                if await adb.set_referral(user.id, referrer_id):
                    # Referral valid → bonus ke referrer (anti-spam dicek di sini)
                    await _proses_referral_bonus(ctx, referrer_id, user)
            except (ValueError, Exception) as e:
                logger.warning("[start] referral error: %s", e)
    
    user_data, stats = await asyncio.gather(
        adb.get_user(user.id),
        adb.get_store_stats()
    )
    saldo     = user_data["saldo"] if user_data else 0

    is_admin = user.id in ADMIN_IDS
    teks = (
        f"<tg-emoji emoji-id=\"6003735582495216112\">✈️</tg-emoji> <b><a href=\"https://t.me/warunggmail\">WARUNG GMAIL BOT</a></b>\n\n"
        f"<blockquote><tg-emoji emoji-id=\"6003735582495216112\">✈️</tg-emoji> User: <code>{user.id}</code>\n"
        f"<tg-emoji emoji-id=\"6156906412761946453\">💵</tg-emoji> Saldo: <b>{fmt_rupiah(saldo)}</b></blockquote>\n"
        f"<blockquote><tg-emoji emoji-id=\"5260587686304956325\">🌐</tg-emoji> Stok: <b>{stats['stok_tersedia']:,} Pcs</b> | <tg-emoji emoji-id=\"5244837092042750681\">📈</tg-emoji> Trx: <b>{stats['total_trx']:,} Tx</b></blockquote>"
    )

    channel_url = f"https://t.me/{CHANNEL_LIVE_TX.lstrip('@')}" if CHANNEL_LIVE_TX else "https://t.me/warunggmail"

    keyboard = [
        [
            InlineKeyboardButton(
                "TOP UP",
                callback_data="topup",
                style="primary",
                icon_custom_emoji_id="5364075889669718872"
            ),
            InlineKeyboardButton(
                "BELI AKUN",
                callback_data="beli_paket",
                style="primary",
                icon_custom_emoji_id="5260587686304956325"
            ),
        ],
        [
            InlineKeyboardButton(
                "RIWAYAT MUTASI",
                callback_data="riwayat_mutasi",
                style="primary",
                icon_custom_emoji_id="5253742260054409879"
            ),
        ],
    ]

    # Row 3 (Ref & Klaim Garansi)
    row3 = [
        InlineKeyboardButton(
            "REF",
            callback_data="referral",
            style="primary",
            icon_custom_emoji_id="6156448083916887235"
        ),
        InlineKeyboardButton(
            "KLAIM GARANSI",
            callback_data="garansi",
            style="primary",
            icon_custom_emoji_id="6158892349805040268"
        ),
    ]
    keyboard.append(row3)

    # Row 4 (Live Transaksi & Info Akun)
    keyboard.append([
        InlineKeyboardButton(
            "LIVE TRANSAKSI",
            url=channel_url,
            style="primary",
            icon_custom_emoji_id="5244837092042750681"
        ),
        InlineKeyboardButton(
            "INFO AKUN",
            callback_data="info_akun",
            style="primary",
            icon_custom_emoji_id="5452069934089641166"
        ),
    ])

    # Row 5 (CHAT CS - full-width with custom emoji and red style)
    keyboard.append([
        InlineKeyboardButton(
            "CHAT CS",
            callback_data="chat_cs",
            style="danger",
            icon_custom_emoji_id="6003735582495216112"
        )
    ])

    # Row 6 (PANEL ADMIN - if admin, full-width)
    if is_admin:
        keyboard.append([
            InlineKeyboardButton("PANEL ADMIN", callback_data="admin_panel", style="danger")
        ])

    markup = InlineKeyboardMarkup(keyboard)
    await kirim_atau_edit_menu(update, ctx, teks, markup)


async def info_akun(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = update.effective_user

    # Answer SEGERA agar spinner di tombol langsung berhenti
    await q.answer()

    u = await adb.get_user(user.id)
    if not u:
        return

    ref_stats = await adb.get_referral_stats(user.id)
    saldo     = fmt_rupiah(u["saldo"])
    teks = (
        f"<tg-emoji emoji-id=\"5452069934089641166\">👤</tg-emoji> <b>INFO AKUN</b>\n\n"
        f"<blockquote>• ID User   : <code>{user.id}</code>\n"
        f"• Nama      : <b>{user.full_name or '-'}</b>\n"
        f"• Username  : @{user.username or '-'}\n"
        f"• Saldo     : <b>{saldo}</b>\n"
        f"• Total Ref : <b>{ref_stats['referral_count']} Orang</b>\n"
        f"• Joined    : <b>{u['joined_at'][:10]}</b></blockquote>"
    )

    kb = [[InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")]]
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


async def chat_cs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    admin_contacts = [c.strip() for c in ADMIN_CONTACT.split(",")]
    
    teks = (
        "<tg-emoji emoji-id=\"6003735582495216112\">⚡️</tg-emoji> <b>Customer Service - Warung Gmail</b>\n\n"
        "Silakan hubungi salah satu admin di bawah untuk bantuan:"
    )
    
    kb = []
    admin_row = []
    for idx, contact in enumerate(admin_contacts, 1):
        username = contact.lstrip('@')
        admin_row.append(InlineKeyboardButton(f"Admin {idx}", url=f"https://t.me/{username}", style="primary"))
    if admin_row:
        kb.append(admin_row)
        
    kb.append([InlineKeyboardButton("Kembali", callback_data="menu_utama", style="danger")])
    
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


async def _proses_referral_bonus(ctx: ContextTypes.DEFAULT_TYPE, referrer_id: int, new_user):
    """Beri bonus saldo ke referrer, dengan deteksi bot/spam."""
    import time
    import json
    from config import REFERRAL_BONUS, REFERRAL_SPAM_WINDOW, REFERRAL_SPAM_THRESHOLD

    # Cek sudah banned
    if await adb.is_referral_banned(referrer_id):
        return

    # Track waktu referral masuk (anti-bot)
    key = f"ref_spam_{referrer_id}"
    now = time.time()
    raw = await adb.get_setting(key)
    try:
        times = json.loads(raw) if raw else []
    except Exception:
        times = []
    times = [t for t in times if now - t < REFERRAL_SPAM_WINDOW]
    times.append(now)
    await adb.set_setting(key, json.dumps(times))

    if len(times) >= REFERRAL_SPAM_THRESHOLD:
        # Deteksi bot — ban referral referrer
        await adb.ban_referral(referrer_id)
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
    await adb.increment_referral_count(referrer_id)
    await adb.tambah_saldo(
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
        
        ref_user = await adb.get_user(referrer_id)
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
    app.add_handler(CallbackQueryHandler(chat_cs,     pattern="^chat_cs$"))

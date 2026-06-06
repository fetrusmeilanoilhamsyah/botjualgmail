"""
handlers/garansi.py - Klaim Garansi 24 Jam
Flow:
  1. User klik "Klaim Garansi"
  2. Tampilkan daftar pembelian yang masih aktif garansinya
  3. User pilih pembelian → ketik alasan
  4. Klaim dicatat, admin dinotif
  5. Admin proses via /garansi_proses
"""
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database import db
from database.db_async import adb
from config import ADMIN_CONTACT, ADMIN_NOTIF_CHATS

logger = logging.getLogger(__name__)


from handlers.start import kirim_atau_edit_menu, edit_menu_caption_or_text

def fmt_rupiah(n: int) -> str:
    return f"Rp{n:,.0f}".replace(",", ".")


async def show_garansi_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan pembelian yang masih dalam masa garansi."""
    q    = update.callback_query
    user = update.effective_user
    await q.answer()

    riwayat = await adb.get_riwayat_beli(user.id, limit=50)
    now_iso = datetime.now().isoformat()

    # Filter: hanya yang status 'aktif' dan garansi belum habis
    valid = [
        r for r in riwayat
        if r["status"] == "aktif" and r["garansi_until"] > now_iso
    ]

    if not valid:
        await kirim_atau_edit_menu(
            update, ctx,
            "<b>Tidak Ada Garansi Aktif</b>\n\n"
            "Tidak ada pembelian yang masih dalam masa garansi (24 jam).\n\n"
            f"Jika ada masalah, hubungi admin: {ADMIN_CONTACT}",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
            ]])
        )
        return

    teks = "<b>Klaim Garansi</b>\n\nPilih invoice pembelian yang ingin diklaim:"
    kb   = []
    for r in valid:
        sisa_jam = (datetime.fromisoformat(r["garansi_until"]) - datetime.now()).seconds // 3600
        label    = f"#{r['id']} – {r['paket_nama']} (sisa ~{sisa_jam}j)"
        kb.append([InlineKeyboardButton(label, callback_data=f"pilih_garansi:{r['id']}", style="primary")])

    kb.append([InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")])
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


async def pilih_garansi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User memilih pembelian untuk diklaim."""
    q            = update.callback_query
    user         = update.effective_user
    pembelian_id = int(q.data.split(":", 1)[1])
    await q.answer()

    detail = await adb.get_detail_pembelian(pembelian_id, user.id)
    if not detail:
        await edit_menu_caption_or_text(ctx, user.id, q.message.message_id, "Pembelian tidak ditemukan.", None)
        return

    if detail["status"] != "aktif":
        await kirim_atau_edit_menu(
            update, ctx,
            "Pembelian ini sudah diklaim garansinya atau sudah selesai.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("Kembali", callback_data="garansi", style="danger")
            ]])
        )
        return

    now_iso = datetime.now().isoformat()
    if detail["garansi_until"] <= now_iso:
        await kirim_atau_edit_menu(
            update, ctx,
            "Masa garansi pembelian ini sudah habis.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
            ]])
        )
        return

    db.set_session(user.id, "waiting_garansi_alasan", {"pembelian_id": pembelian_id, "menu_msg_id": q.message.message_id})

    teks = (
        f"<b>Klaim Garansi - Invoice #{pembelian_id}</b>\n\n"
        f"Item: <b>{detail['paket_nama']}</b>\n\n"
        "Silakan jelaskan kendala atau detail kerusakan akun yang Anda alami:\n"
        "(Contoh: Akun salah sandi, butuh verifikasi nomor, dll)\n\n"
        "Ketik alasan klaim:"
    )
    kb = [[InlineKeyboardButton("Batal", callback_data="garansi", style="danger")]]
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


async def handle_garansi_alasan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Terima alasan klaim dari user."""
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "waiting_garansi_alasan":
        return

    # Hapus pesan input user
    try:
        await update.message.delete()
    except Exception:
        pass

    alasan       = update.message.text.strip()
    pembelian_id = session["data"]["pembelian_id"]
    menu_msg_id  = session["data"].get("menu_msg_id")
    db.clear_session(user.id)

    if len(alasan) < 5:
        teks_err = "<b>Alasan terlalu singkat!</b>\n\nJelaskan kendala Anda secara lebih detail (min 5 karakter):"
        kb = [[InlineKeyboardButton("Batal", callback_data="garansi", style="danger")]]
        
        # Simpan kembali session dengan menu_msg_id
        db.set_session(user.id, "waiting_garansi_alasan", {"pembelian_id": pembelian_id, "menu_msg_id": menu_msg_id})
        
        if menu_msg_id:
            try:
                await edit_menu_caption_or_text(
                    ctx, user.id, menu_msg_id, teks_err, InlineKeyboardMarkup(kb)
                )
                return
            except Exception:
                pass
        await ctx.bot.send_message(chat_id=user.id, text=teks_err, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return

    garansi_id = await adb.create_garansi(pembelian_id, user.id, alasan)

    if garansi_id is None:
        teks_fail = (
            "<b>Klaim Garansi Gagal</b>\n\n"
            "Gagal membuat klaim garansi karena kemungkinan:\n"
            "• Garansi pembelian ini sudah kadaluarsa (melebihi 24 jam)\n"
            "• Sudah ada pengajuan klaim aktif untuk pesanan ini\n\n"
            f"Silakan hubungi admin jika ada kendala: {ADMIN_CONTACT}"
        )
        kb = [[InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")]]
        if menu_msg_id:
            try:
                await edit_menu_caption_or_text(
                    ctx, user.id, menu_msg_id, teks_fail, InlineKeyboardMarkup(kb)
                )
                return
            except Exception:
                pass
        await ctx.bot.send_message(chat_id=user.id, text=teks_fail, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return

    teks_success = (
        f"<b>Klaim Garansi Terkirim</b>\n\n"
        f"No. Klaim: <code>#{garansi_id}</code>\n"
        f"No. Invoice: <code>#{pembelian_id}</code>\n"
        f"Alasan: {alasan}\n\n"
        "Pengajuan klaim Anda telah dicatat. Admin kami akan segera memproses penggantian akun.\n"
        f"Hubungi admin jika mendesak: {ADMIN_CONTACT}"
    )
    kb = [[InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")]]
    if menu_msg_id:
        try:
            await edit_menu_caption_or_text(
                ctx, user.id, menu_msg_id, teks_success, InlineKeyboardMarkup(kb)
            )
            return
        except Exception:
            pass
    await ctx.bot.send_message(chat_id=user.id, text=teks_success, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

    # Notif admin
    try:
        user_info = await adb.get_user(user.id)
        notif = (
            f"<b>KLAIM GARANSI BARU</b>\n\n"
            f"User: {user.full_name} (@{user.username or '-'}) [<code>{user.id}</code>]\n"
            f"ID Klaim: #{garansi_id}\n"
            f"ID Pesanan: #{pembelian_id}\n"
            f"Alasan: {alasan}\n\n"
            f"Gunakan /garansi_list untuk melihat semua klaim."
        )
        for chat_id in ADMIN_NOTIF_CHATS:
            try:
                await ctx.bot.send_message(chat_id=chat_id, text=notif, parse_mode="HTML")
            except Exception as e:
                logger.warning("[garansi] Gagal notif admin %d: %s", chat_id, e)
    except Exception as e:
        logger.debug("[garansi] Gagal notif admin: %s", e)


def register(app):
    app.add_handler(CallbackQueryHandler(show_garansi_menu, pattern="^garansi$"))
    app.add_handler(CallbackQueryHandler(pilih_garansi,     pattern="^pilih_garansi:"))

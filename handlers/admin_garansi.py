"""
handlers/admin_garansi.py - Proses Klaim Garansi (Admin)
Commands: /garansi_list
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from database import db
from middleware.auth import admin_only
from config import ADMIN_NOTIF_CHAT

logger = logging.getLogger(__name__)


def fmt_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")


@admin_only
async def cmd_garansi_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _show_garansi_list(update, ctx)


@admin_only
async def cb_garansi_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await _show_garansi_list(update, ctx)


async def _show_garansi_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pending = db.get_garansi_pending()

    if not pending:
        teks = "<b>Klaim Garansi</b>\n\nTidak ada klaim garansi yang pending."
        kb   = [[InlineKeyboardButton("Panel Admin", callback_data="admin_panel", style="danger")]]
    else:
        teks  = f"<b>Klaim Garansi Pending</b> ({len(pending)} klaim)\n\n"
        kb    = []
        for g in pending:
            teks += (
                f"Klaim #{g['id']}\n"
                f"   User: {g['full_name']} (@{g['username'] or '-'})\n"
                f"   Paket: {g['paket_nama']} | Pesanan #{g['pembelian_id']}\n"
                f"   Alasan: {g['alasan'][:60]}...\n"
                f"   Tanggal: {g['created_at'][:16]}\n\n"
            )
            kb.append([InlineKeyboardButton(
                f"Proses Klaim #{g['id']}",
                callback_data=f"admin_proses_garansi:{g['id']}",
                style="primary"
            )])
        kb.append([InlineKeyboardButton("Panel Admin", callback_data="admin_panel", style="danger")])

    target = update.message if update.message else update.callback_query.message
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
            )
        except Exception:
            await target.reply_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await target.reply_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def cb_proses_garansi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan opsi proses garansi."""
    q          = update.callback_query
    garansi_id = int(q.data.split(":", 1)[1])
    await q.answer()

    kb = [
        [InlineKeyboardButton("Setujui (Kirim Akun Pengganti)", callback_data=f"admin_setuju_garansi:{garansi_id}", style="primary")],
        [InlineKeyboardButton("Tolak", callback_data=f"admin_tolak_garansi:{garansi_id}", style="danger")],
        [InlineKeyboardButton("Kembali", callback_data="admin_garansi_list", style="danger")],
    ]
    await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def cb_setuju_garansi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin setujui garansi → minta pilih paket pengganti."""
    q          = update.callback_query
    garansi_id = int(q.data.split(":", 1)[1])
    await q.answer()

    # Ambil info garansi
    pending  = db.get_garansi_pending()
    garansi  = next((g for g in pending if g["id"] == garansi_id), None)

    if not garansi:
        await q.edit_message_text("Klaim tidak ditemukan.")
        return

    paket_list = db.get_all_paket()
    teks = (
        f"<b>Setujui Garansi #{garansi_id}</b>\n\n"
        f"User: {garansi['full_name']}\n"
        f"Paket: {garansi['paket_nama']}\n\n"
        "Pilih paket akun pengganti yang akan dikirim:"
    )
    kb = [[InlineKeyboardButton(
        f"{p['nama']} (stok: {p['stok_tersedia']})",
        callback_data=f"admin_kirim_pengganti:{garansi_id}:{p['id']}",
        style="primary"
    )] for p in paket_list if p["stok_tersedia"] > 0]
    kb.append([InlineKeyboardButton("Batal", callback_data="admin_garansi_list", style="danger")])

    if not any(True for p in paket_list if p["stok_tersedia"] > 0):
        await q.edit_message_text(
            "Tidak ada stok tersedia untuk pengganti. Tambahkan stok dulu.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Kelola Stok", callback_data="admin_stok_refresh", style="primary")
            ]])
        )
        return

    await q.edit_message_text(teks, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


@admin_only
async def cb_kirim_pengganti(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ambil stok pengganti & kirim ke user."""
    q    = update.callback_query
    parts = q.data.split(":")
    garansi_id  = int(parts[1])
    paket_id    = int(parts[2])
    await q.answer("Memproses...")

    paket = db.get_paket_by_id(paket_id)
    if not paket:
        await q.edit_message_text("Paket tidak ditemukan.")
        return

    akun_list = db.ambil_stok(paket_id, paket["kuantitas"])
    if akun_list is None:
        await q.edit_message_text("Stok habis saat proses. Coba paket lain.")
        return

    stok_ids = [a["id"] for a in akun_list]
    db.resolve_garansi(garansi_id, stok_ids, admin_catatan="Pengganti dikirim admin")

    # Ambil user_id dari garansi
    with db.get_connection() as conn:
        row = conn.execute("SELECT user_id FROM garansi WHERE id=?", (garansi_id,)).fetchone()
        user_id = row["user_id"] if row else None

    if user_id:
        akun_teks = "\n\n".join(
            f"<b>Akun #{i+1}</b>\n"
            f"   Email: <code>{a['email']}</code>\n"
            f"   Password: <code>{a['password']}</code>"
            + (f"\n   Recovery: <code>{a['recovery']}</code>" if a.get("recovery") else "")
            for i, a in enumerate(akun_list)
        )
        try:
            await ctx.bot.send_message(
                chat_id=user_id,
                text=(
                    f"<b>Garansi Diproses!</b>\n\n"
                    f"Klaim garansi kamu telah disetujui.\n"
                    f"Berikut akun pengganti:\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{akun_teks}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "Simpan data ini dengan aman!"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning("[garansi] Gagal kirim ke user %d: %s", user_id, e)

    await q.edit_message_text(
        f"<b>Garansi #{garansi_id} Selesai</b>\n\n"
        f"Akun pengganti berhasil dikirim ke user.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("List Garansi", callback_data="admin_garansi_list", style="primary"),
            InlineKeyboardButton("Panel Admin", callback_data="admin_panel", style="danger"),
        ]])
    )


@admin_only
async def cb_tolak_garansi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q          = update.callback_query
    garansi_id = int(q.data.split(":", 1)[1])
    await q.answer()

    db.set_session(update.effective_user.id, "admin_tolak_garansi_alasan", {"garansi_id": garansi_id})
    await q.edit_message_text(
        f"<b>Tolak Klaim Garansi #{garansi_id}</b>\n\n"
        "Ketik alasan penolakan (akan dikirim ke user):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Batal", callback_data="admin_garansi_list", style="danger")
        ]])
    )


async def admin_terima_alasan_tolak(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    session = db.get_session(user.id)
    if session["state"] != "admin_tolak_garansi_alasan":
        return

    garansi_id = session["data"]["garansi_id"]
    alasan     = update.message.text.strip()
    db.clear_session(user.id)

    db.tolak_garansi(garansi_id, admin_catatan=alasan)

    # Notif ke user
    with db.get_connection() as conn:
        row = conn.execute("SELECT user_id FROM garansi WHERE id=?", (garansi_id,)).fetchone()
        user_id = row["user_id"] if row else None

    if user_id:
        try:
            await ctx.bot.send_message(
                chat_id=user_id,
                text=(
                    f"<b>Klaim Garansi #{garansi_id} Ditolak</b>\n\n"
                    f"Alasan: {alasan}\n\n"
                    "Jika kamu merasa ini kesalahan, hubungi admin."
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass

    await update.message.reply_text(
        f"Klaim #{garansi_id} ditolak dan user sudah dinotifikasi.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("List Garansi", callback_data="admin_garansi_list", style="danger")
        ]])
    )


def register(app):
    app.add_handler(CommandHandler("garansi_list", cmd_garansi_list))
    app.add_handler(CallbackQueryHandler(cb_garansi_list,    pattern="^admin_garansi_list$"))
    app.add_handler(CallbackQueryHandler(cb_proses_garansi,  pattern="^admin_proses_garansi:"))
    app.add_handler(CallbackQueryHandler(cb_setuju_garansi,  pattern="^admin_setuju_garansi:"))
    app.add_handler(CallbackQueryHandler(cb_kirim_pengganti, pattern="^admin_kirim_pengganti:"))
    app.add_handler(CallbackQueryHandler(cb_tolak_garansi,   pattern="^admin_tolak_garansi:"))

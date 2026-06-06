"""
handlers/riwayat.py - Riwayat Beli Gmail & Riwayat Mutasi Saldo
"""
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from database.db_async import adb

logger = logging.getLogger(__name__)

PAGE_SIZE = 3


def fmt_rupiah(n: int) -> str:
    return f"Rp{n:,.0f}".replace(",", ".")


def fmt_short_rupiah(n: int) -> str:
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


def fmt_dt(iso_str: str) -> str:
    """Format ISO datetime ke string cantik."""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d %b %Y %H:%M")
    except Exception:
        return iso_str[:16] if iso_str else "-"


# ── RIWAYAT BELI ─────────────────────────────────────────────────────────────

async def show_riwayat_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = update.effective_user
    await q.answer()

    parts = q.data.split(":")
    page  = int(parts[1]) if len(parts) > 1 else 0

    riwayat = await adb.get_riwayat_beli(user.id, limit=100)

    from handlers.start import kirim_atau_edit_menu
    if not riwayat:
        await kirim_atau_edit_menu(
            update, ctx,
            f"<tg-emoji emoji-id=\"5253742260054409879\">📋</tg-emoji> <b>RIWAYAT PEMBELIAN</b>\n\n"
            f"<blockquote>Anda belum pernah melakukan pembelian akun.</blockquote>",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("Beli Sekarang", callback_data="beli_paket", style="primary"),
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger"),
            ]])
        )
        return

    total   = len(riwayat)
    start   = page * PAGE_SIZE
    end     = min(start + PAGE_SIZE, total)
    items   = riwayat[start:end]

    teks = f"<tg-emoji emoji-id=\"5253742260054409879\">📋</tg-emoji> <b>RIWAYAT PEMBELIAN</b> (Hal {page+1}/{(total-1)//PAGE_SIZE+1})\n\n"

    kb = []
    for r in items:
        status_text = {"aktif": "Aktif", "klaim_garansi": "Garansi", "selesai": "Selesai"}.get(r["status"], "Selesai")
        teks += (
            f"<b>Invoice #{r['id']}</b> ({status_text})\n"
            f"<blockquote>• Item     : <b>{r['paket_nama']}</b>\n"
            f"• Total    : <b>{fmt_rupiah(r['harga_bayar'])}</b>\n"
            f"• Tanggal  : <b>{fmt_dt(r['created_at'])}</b></blockquote>\n\n"
        )
        kb.append([InlineKeyboardButton(
            f"Lihat Data Akun #{r['id']}",
            callback_data=f"lihat_akun:{r['id']}",
            style="primary"
        )])

    # Navigasi
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("Sebelumnya", callback_data=f"riwayat_beli:{page-1}", style="primary"))
    if end < total:
        nav.append(InlineKeyboardButton("Berikutnya", callback_data=f"riwayat_beli:{page+1}", style="primary"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")])

    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


async def lihat_akun(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan detail akun dari pembelian."""
    q            = update.callback_query
    user         = update.effective_user
    pembelian_id = int(q.data.split(":", 1)[1])
    await q.answer()

    detail = await adb.get_detail_pembelian(pembelian_id, user.id)
    if not detail:
        await q.answer("Data tidak ditemukan.", show_alert=True)
        return

    akun_list = detail.get("akun_list", [])
    use_file_delivery = (len(akun_list) > 5)

    teks = (
        f"<tg-emoji emoji-id=\"5253742260054409879\">📋</tg-emoji> <b>DETAIL PESANAN #{pembelian_id}</b>\n\n"
        f"<blockquote>• Item     : <b>{detail['paket_nama']}</b>\n"
        f"• Total    : <b>{fmt_rupiah(detail['harga_bayar'])}</b>\n"
        f"• Tanggal  : <b>{fmt_dt(detail['created_at'])}</b>\n"
        f"• Garansi  : <b>{fmt_dt(detail['garansi_until'])}</b></blockquote>\n\n"
    )

    if use_file_delivery:
        teks += "Karena jumlah pembelian besar, data akun lengkap dikirim via file txt."
    else:
        teks += "<b>DATA AKUN GMAIL:</b>\n"
        for i, a in enumerate(akun_list, 1):
            teks += f"\n• <b>Akun #{i}</b>\n"
            teks += f"   Email   : <code>{a['email']}</code>\n"
            teks += f"   Password: <code>{a['password']}</code>\n"
            if a.get("recovery"):
                teks += f"   Recovery: <code>{a['recovery']}</code>\n"
            if a.get("tgl_buat"):
                teks += f"   Dibuat  : {a['tgl_buat']}\n"
            if a.get("catatan"):
                teks += f"   Catatan : {a['catatan']}\n"

    kb = [[
        InlineKeyboardButton("Riwayat Beli", callback_data="riwayat_beli", style="primary"),
        InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger"),
    ]]
    if detail.get("status") == "aktif":
        now_iso = datetime.now().isoformat()
        if detail.get("garansi_until", "") > now_iso:
            kb.insert(0, [InlineKeyboardButton(
                "Klaim Garansi", callback_data=f"pilih_garansi:{pembelian_id}", style="primary"
            )])

    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))

    if use_file_delivery:
        import io
        # Build clean TXT file
        txt_lines = [
            f"==================================================",
            f"DATA AKUN GMAIL - INVOICE #{pembelian_id}",
            f"==================================================\n",
            "FORMAT IMPOR (Email|Password|Recovery):",
            "--------------------------------------------------"
        ]
        for a in akun_list:
            rec = a.get("recovery") or ""
            txt_lines.append(f"{a['email']}|{a['password']}|{rec}")
        txt_lines.append("--------------------------------------------------\n")
        txt_lines.append("DETAIL AKUN:")
        txt_lines.append("--------------------------------------------------")
        for i, a in enumerate(akun_list, 1):
            txt_lines.append(f"#{i}")
            txt_lines.append(f"Email   : {a['email']}")
            txt_lines.append(f"Password: {a['password']}")
            if a.get("recovery"):
                txt_lines.append(f"Recovery: {a['recovery']}")
            if a.get("tgl_buat"):
                txt_lines.append(f"Dibuat  : {a['tgl_buat']}")
            if a.get("catatan"):
                txt_lines.append(f"Catatan : {a['catatan']}")
            txt_lines.append("")
        txt_lines.append("--------------------------------------------------")
        txt_lines.append("Terima kasih telah berbelanja di Warung Gmail.")
        txt_lines.append("==================================================")
        
        txt_content = "\n".join(txt_lines)
        bio = io.BytesIO(txt_content.encode("utf-8"))
        bio.name = f"Gmail_Order_{pembelian_id}.txt"
        
        try:
            await ctx.bot.send_document(
                chat_id=user.id,
                document=bio,
                filename=f"Gmail_Order_{pembelian_id}.txt",
                caption=f"Detail Akun Gmail Invoice #{pembelian_id}"
            )
        except Exception as e:
            logger.error("[riwayat] Gagal mengirim dokumen akun: %s", e)
            await ctx.bot.send_message(
                chat_id=user.id,
                text="Gagal mengirim file data akun. Silakan hubungi admin untuk bantuan."
            )


# ── RIWAYAT MUTASI ───────────────────────────────────────────────────────────

TIPE_TEXT = {
    "topup":          "[TopUp]",
    "beli":           "[Beli]",
    "referral":       "[Referral]",
    "refund_garansi": "[Refund]",
    "manual":         "[Manual]",
}


async def show_riwayat_mutasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = update.effective_user
    await q.answer()

    parts = q.data.split(":")
    page  = int(parts[1]) if len(parts) > 1 else 0

    mutasi = await adb.get_riwayat_mutasi(user.id, limit=100)

    from handlers.start import kirim_atau_edit_menu
    if not mutasi:
        await kirim_atau_edit_menu(
            update, ctx,
            f"<tg-emoji emoji-id=\"5253742260054409879\">📋</tg-emoji> <b>RIWAYAT MUTASI</b>\n\n"
            f"<blockquote>Belum ada riwayat transaksi.</blockquote>",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")
            ]])
        )
        return

    total = len(mutasi)
    start = page * PAGE_SIZE
    end   = min(start + PAGE_SIZE, total)
    items = mutasi[start:end]

    teks = f"<tg-emoji emoji-id=\"5253742260054409879\">📋</tg-emoji> <b>RIWAYAT MUTASI SALDO</b> (Hal {page+1}/{(total-1)//PAGE_SIZE+1})\n\n"
    for m in items:
        label_tipe = TIPE_TEXT.get(m["tipe"], "[Mutasi]")
        masuk   = m["jumlah"] > 0
        sign    = "+" if masuk else "-"
        teks += (
            f"<b>{label_tipe} {sign}{fmt_rupiah(abs(m['jumlah']))}</b>\n"
            f"<blockquote>• Keterangan  : <b>{m['keterangan']}</b>\n"
            f"• Saldo Akhir : <b>{fmt_rupiah(m['saldo_sesudah'])}</b>\n"
            f"• Tanggal     : <b>{fmt_dt(m['created_at'])}</b></blockquote>\n\n"
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("Sebelumnya", callback_data=f"riwayat_mutasi:{page-1}", style="primary"))
    if end < total:
        nav.append(InlineKeyboardButton("Berikutnya", callback_data=f"riwayat_mutasi:{page+1}", style="primary"))
    kb = []
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("Menu Utama", callback_data="menu_utama", style="danger")])

    from handlers.start import kirim_atau_edit_menu
    await kirim_atau_edit_menu(update, ctx, teks, InlineKeyboardMarkup(kb))


def register(app):
    app.add_handler(CallbackQueryHandler(show_riwayat_beli,   pattern="^riwayat_beli"))
    app.add_handler(CallbackQueryHandler(lihat_akun,          pattern="^lihat_akun:"))
    app.add_handler(CallbackQueryHandler(show_riwayat_mutasi, pattern="^riwayat_mutasi"))

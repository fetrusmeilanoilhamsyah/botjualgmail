"""
config.py - Konfigurasi Bot Jual Gmail
Semua setting baca dari .env
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── BOT ──────────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.getenv("BOT_TOKEN")
ADMIN_IDS     = list(map(int, os.getenv("ADMIN_IDS", "0").split(",")))
ADMIN_CONTACT = os.getenv("ADMIN_CONTACT", "@admin")
BOT_USERNAME  = os.getenv("BOT_USERNAME", "botjualgmail_bot")

# ─── PAKASIR ──────────────────────────────────────────────────────────────────
PAKASIR_ENABLED        = os.getenv("PAKASIR_ENABLED", "true").lower() == "true"
PAKASIR_SLUG           = os.getenv("PAKASIR_SLUG", "")
PAKASIR_API_KEY        = os.getenv("PAKASIR_API_KEY", "")
PAKASIR_SANDBOX        = os.getenv("PAKASIR_SANDBOX", "false").lower() == "true"
PAKASIR_WEBHOOK_SECRET = os.getenv("PAKASIR_WEBHOOK_SECRET", "")

# ─── SERVER PORTS ─────────────────────────────────────────────────────────────
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", 8083))
HEALTH_PORT  = int(os.getenv("HEALTH_PORT", 8084))

# ─── REFERRAL ─────────────────────────────────────────────────────────────────
REFERRAL_BONUS          = int(os.getenv("REFERRAL_BONUS", 50))       # Rp per referral
REFERRAL_SPAM_WINDOW    = int(os.getenv("REFERRAL_SPAM_WINDOW", 10))  # detik
REFERRAL_SPAM_THRESHOLD = int(os.getenv("REFERRAL_SPAM_THRESHOLD", 5)) # max user dalam window

# ─── GARANSI ──────────────────────────────────────────────────────────────────
GARANSI_JAM = int(os.getenv("GARANSI_JAM", 24))

# ─── TOPUP LIMITS ─────────────────────────────────────────────────────────────
TOPUP_MIN = int(os.getenv("TOPUP_MIN", 1000))
TOPUP_MAX = int(os.getenv("TOPUP_MAX", 1000000))

# ─── NOTIFIKASI ───────────────────────────────────────────────────────────────
ADMIN_NOTIF_CHAT = int(os.getenv("ADMIN_NOTIF_CHAT", ADMIN_IDS[0] if ADMIN_IDS else 0))

# ─── LOCAL TELEGRAM BOT API (TELELOKAL) ──────────────────────────────────────
# Share telelokal yang sama dengan botcv (port 8082)
# Set USE_LOCAL_BOT_API=true jika di VPS dan telelokal aktif
USE_LOCAL_BOT_API  = os.getenv("USE_LOCAL_BOT_API", "false").lower() == "true"
LOCAL_BOT_API_PORT = int(os.getenv("LOCAL_BOT_API_PORT", 8082))

# ─── CONCURRENCY ──────────────────────────────────────────────────────────────
DB_POOL_SIZE          = 16
USER_CLICK_COOLDOWN   = 0.3
SESSION_CACHE_MAX_SIZE = 1000

# ─── BROADCAST ────────────────────────────────────────────────────────────────
BROADCAST_DELAY = 0.05   # jeda antar user saat broadcast (detik)
BROADCAST_CHUNK = 30     # kirim dalam batch

# ─── PAKET GMAIL (nama & kuantitas, harga diset di DB via admin) ───────────────
PAKET_KUANTITAS = {
    "1akun":  1,
    "5akun":  5,
    "10akun": 10,
    "20akun": 20,
    "50akun": 50,
}

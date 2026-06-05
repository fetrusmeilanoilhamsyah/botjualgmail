import logging
from telegram import Bot
from config import CHANNEL_LIVE_TX

logger = logging.getLogger(__name__)


def censor_name(name: str) -> str:
    if not name:
        return "Pengguna"
    parts = name.split()
    censored_parts = []
    for part in parts:
        if len(part) <= 2:
            censored_parts.append(part[0] + "*")
        else:
            censored_parts.append(part[:2] + "*" * (len(part) - 2))
    return " ".join(censored_parts)


def censor_id(user_id: int) -> str:
    s = str(user_id)
    if len(s) <= 4:
        return "****"
    return s[:3] + "*" * (len(s) - 3)


async def send_live_tx(bot: Bot, text: str):
    if not CHANNEL_LIVE_TX:
        return
    try:
        # Coba send message, pastikan bot adalah administrator di channel
        await bot.send_message(chat_id=CHANNEL_LIVE_TX, text=text, parse_mode="HTML")
    except Exception as e:
        logger.warning("[live_tx] Gagal mengirim live transaksi ke ch: %s", e)

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
# Empty until you message the bot once and it tells you your chat ID.
OWNER_CHAT_ID = int(_env("OWNER_CHAT_ID") or 0)

AZURE_OPENAI_BASE_URL = _env("AZURE_OPENAI_BASE_URL")
AZURE_OPENAI_MODEL = _env("AZURE_OPENAI_MODEL", "gpt-5.6-terra-1")
AZURE_OPENAI_API_KEY = _env("AZURE_OPENAI_API_KEY")

RESEND_API_KEY = _env("RESEND_API_KEY")
EMAIL_FROM = _env("EMAIL_FROM", "Opportunity Reminder <onboarding@resend.dev>")
EMAIL_TO = [a.strip() for a in _env("EMAIL_TO").split(",") if a.strip()]  # comma-separated

TZ = ZoneInfo(_env("TIMEZONE", "Africa/Kigali"))
REMINDER_HOUR = int(_env("REMINDER_HOUR", "8"))
DIGEST_HOUR = int(_env("DIGEST_HOUR", "18"))  # Sundays
DB_PATH = _env("DB_PATH", "data/reminders.db")

# Days before the deadline. EMAIL_DAYS go to Telegram + email, NUDGE_DAYS to Telegram only.
EMAIL_DAYS = (7, 3, 1, 0)
NUDGE_DAYS = (14, 10, 5, 2)


def model_base_url() -> str:
    return AZURE_OPENAI_BASE_URL.rstrip("/").removesuffix("/responses") + "/"

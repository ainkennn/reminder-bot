import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# Admin Telegram user IDs (integers)
_raw_admins = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = [int(x.strip()) for x in _raw_admins.split(",") if x.strip()]

# Timezone for APScheduler and datetime display
TIMEZONE: str = os.getenv("TIMEZONE", "UTC")

# SQLite database file path
DB_PATH: str = os.getenv("DB_PATH", "bot.db")

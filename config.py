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

# Group chat and thread the bot operates in
# Telegram supergroup IDs are negative: prepend -100 to the URL number
_raw_group = os.getenv("GROUP_ID", "")
GROUP_ID: int | None = int(_raw_group) if _raw_group else None
THREAD_ID: int | None = int(os.getenv("THREAD_ID", "0")) or None

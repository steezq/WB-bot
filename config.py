import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WB_API_KEY = os.getenv("WB_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Wildberries API base URLs
WB_STATS_URL = "https://statistics-api.wildberries.ru/api/v1"
WB_FINANCE_URL = "https://statistics-api.wildberries.ru/api/v5"

# Allowed users (fill with your Telegram user IDs for security)
# Leave empty to allow everyone
ALLOWED_USERS: list[int] = [1621990225]

"""
Central configuration for the Blessings Data Hub bot.

Everything is read from environment variables (see .env.example) so real
secrets never live in source control. Placeholder strings below are only
fallbacks for local development and MUST be overridden via a real .env file
or your host's environment variable settings before going live.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed - fine if env vars are set another way

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
TELEGRAM_API_TOKEN = os.getenv("TELEGRAM_API_TOKEN", "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE")

# Your bot's @username (no @), used to build a "return to Telegram" link
# on the post-payment page. e.g. if your bot is @BlessingsDataHubBot, set
# TELEGRAM_BOT_USERNAME=BlessingsDataHubBot
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "your_bot_username")

# Comma separated Telegram chat IDs that should get admin alerts
# (failed deliveries, insufficient Hubnet balance, etc.)
ADMIN_CHAT_IDS = [
    int(x) for x in os.getenv("ADMIN_CHAT_IDS", "").split(",") if x.strip().lstrip("-").isdigit()
]

# ---------------------------------------------------------------------------
# Hubnet (data bundle delivery)
# ---------------------------------------------------------------------------
HUBNET_API_KEY = os.getenv("HUBNET_API_KEY", "PUT_YOUR_HUBNET_API_KEY_HERE")
HUBNET_BASE_URL = "https://console.hubnet.app/live/api/context/business/transaction"

# Hubnet allows 5 requests/minute per endpoint - keep a safety margin above
# the documented 12s minimum interval.
HUBNET_MIN_INTERVAL_SECONDS = 13
HUBNET_MAX_CALLS_PER_WINDOW = 5
HUBNET_WINDOW_SECONDS = 60

# Networks supported by Hubnet -> {display label, URL path token}
NETWORKS = {
    "mtn": {"label": "MTN", "token": "mtn"},
    "at": {"label": "AIRTELTIGO ISHARE", "token": "at"},
    "big-time": {"label": "AIRTELTIGO BIGTIME", "token": "big-time"},
    "telecel": {"label": "TELECEL", "token": "telecel"},
}

# ---------------------------------------------------------------------------
# Paystack (customer payment collection)
# ---------------------------------------------------------------------------
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "PUT_YOUR_PAYSTACK_SECRET_KEY_HERE")
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY", "PUT_YOUR_PAYSTACK_PUBLIC_KEY_HERE")
PAYSTACK_INIT_URL = "https://api.paystack.co/transaction/initialize"
PAYSTACK_VERIFY_URL = "https://api.paystack.co/transaction/verify/{reference}"

# ---------------------------------------------------------------------------
# Public-facing app settings
# ---------------------------------------------------------------------------
# PUBLIC_BASE_URL must be a real, internet-reachable HTTPS URL in production
# (Paystack and Hubnet both need to be able to POST webhooks to you). Use a
# tool like ngrok while developing locally.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://blessings-data-hub-bot.onrender.com")
PAYSTACK_WEBHOOK_PATH = "/webhook/paystack"
HUBNET_WEBHOOK_PATH = "/webhook/hubnet"
PAYSTACK_CALLBACK_URL = f"{https://blessings-data-hub-bot.onrender.com}/payment/callback"

WEBHOOK_SERVER_HOST = os.getenv("HOST", "0.0.0.0")
WEBHOOK_SERVER_PORT = int(os.getenv("PORT", "5000"))

DB_PATH = os.getenv("DB_PATH", "blessings_data_hub.db")

# Currency used by Paystack for this integration (Ghanaian cedi)
CURRENCY = "GHS"

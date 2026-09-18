# config.py
import os

# Read tokens from Render Environment Variables with default fallback
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8211776512:AAHgwSLoFrXMiBUTid7KsdPb6lqBX8ZCPRo")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY_HERE")
MY_WALLET = os.getenv("MY_WALLET", "YOUR_USDT_WALLET_ADDRESS_HERE")

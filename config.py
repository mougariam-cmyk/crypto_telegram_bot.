import os

# التوكن يقرأ حصراً من متغيرات البيئة بـ Render لحمايته من التجميد
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# TON Main Wallet Address
TON_WALLET_ADDRESS = os.getenv("TON_WALLET", "UQCemeZQdby8XpnMKqE5qn1yn58Nb7-fTmZo7tHRrY2BtpYM")

# Subscription Plans Prices (USD)
PRICES = {
    "1_month": 10,
    "6_months": 50,
    "12_months": 80
}

# Deposit Wallets
WALLETS = {
    "TON": TON_WALLET_ADDRESS,
    "TRON": os.getenv("TRON_WALLET", "TMCQvy6Ny3eJNTZWXNYcs7uDgBom3PyXHb"),
    "EVM": os.getenv("ETH_WALLET", "0x99AF129BB09320706A8E26dDDe75041Cc9aC9619"),
    "SOLANA": os.getenv("SOL_WALLET", "GDNSn1CbV43xkPTCpLvi6dYxEjApfM4DBSS8N1tp7Uhd")
}

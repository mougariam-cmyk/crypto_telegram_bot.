import os
import random
import psycopg2
import asyncio
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from google import genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

from fallback_db import get_fallback_message

# ==========================================
# MULTI-GEMINI API KEYS ROTATION SYSTEM
# ==========================================
GEMINI_API_KEYS = [
    os.getenv("GEMINI_API_KEY_1", ""),
    os.getenv("GEMINI_API_KEY_2", ""),
    os.getenv("GEMINI_API_KEY_3", ""),
    os.getenv("GEMINI_API_KEY_4", "")
]
GEMINI_API_KEYS = [k for k in GEMINI_API_KEYS if k]

if not GEMINI_API_KEYS and os.getenv("GEMINI_API_KEY"):
    GEMINI_API_KEYS = [os.getenv("GEMINI_API_KEY")]

api_key_index = 0

def get_next_gemini_client():
    global api_key_index
    if not GEMINI_API_KEYS:
        return None
    key = GEMINI_API_KEYS[api_key_index % len(GEMINI_API_KEYS)]
    api_key_index += 1
    return genai.Client(api_key=key)

# Enable Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==========================================
# DATABASE SYSTEM (Supabase / PostgreSQL)
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    db_url = DATABASE_URL
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(db_url)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT,
            username TEXT,
            selected_plan TEXT,
            coin_name TEXT,
            coin_desc TEXT,
            contract TEXT,
            buy_link TEXT,
            channel TEXT,
            msg_per_hour INTEGER,
            enable_new_buy INTEGER,
            link_ratio INTEGER DEFAULT 100,
            subscription_status TEXT DEFAULT 'active',
            PRIMARY KEY (user_id, channel)
        );
    ''')
    conn.commit()
    cursor.close()
    conn.close()

def save_user_data(user_id: int, username: str, data: dict):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT selected_plan FROM users WHERE user_id = %s AND channel = %s AND subscription_status = 'active';
    ''', (user_id, data.get('channel', '')))
    existing = cursor.fetchone()
    
    target_plan = data.get('selected_plan', 'free')
    if existing and existing[0] != 'free' and target_plan == 'free' and not data.get('is_editing'):
        target_plan = existing[0]

    cursor.execute('''
        INSERT INTO users (
            user_id, username, selected_plan, coin_name, coin_desc, 
            contract, buy_link, channel, msg_per_hour, enable_new_buy, link_ratio, subscription_status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
        ON CONFLICT(user_id, channel) DO UPDATE SET
            username=EXCLUDED.username,
            selected_plan=EXCLUDED.selected_plan,
            coin_name=EXCLUDED.coin_name,
            coin_desc=EXCLUDED.coin_desc,
            contract=EXCLUDED.contract,
            buy_link=EXCLUDED.buy_link,
            msg_per_hour=EXCLUDED.msg_per_hour,
            enable_new_buy=EXCLUDED.enable_new_buy,
            link_ratio=EXCLUDED.link_ratio,
            subscription_status='active';
    ''', (
        user_id, username, target_plan,
        data.get('coin_name', ''), data.get('coin_desc', ''),
        data.get('contract', ''), data.get('buy_link', ''),
        data.get('channel', ''), data.get('msg_per_hour', 2),
        1 if data.get('enable_new_buy', False) else 0,
        data.get('link_ratio', 100)
    ))
    conn.commit()
    cursor.close()
    conn.close()

def cancel_user_subscription(user_id: int, channel: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users SET subscription_status = 'cancelled'
        WHERE user_id = %s AND (channel = %s OR channel = %s);
    ''', (user_id, channel, f"@{channel}" if not channel.startswith('@') else channel.replace('@', '')))
    conn.commit()
    cursor.close()
    conn.close()

def get_user_channels(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT channel, coin_name FROM users WHERE user_id = %s AND subscription_status = \'active\';', (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def get_user_channel_data(user_id: int, channel: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT selected_plan, coin_name, coin_desc, contract, buy_link, channel, msg_per_hour, enable_new_buy, link_ratio
        FROM users WHERE user_id = %s AND (channel = %s OR channel = %s) AND subscription_status = 'active';
    ''', (user_id, channel, f"@{channel}" if not channel.startswith('@') else channel.replace('@', '')))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return {
            'user_id': user_id, 'selected_plan': row[0], 'coin_name': row[1],
            'coin_desc': row[2], 'contract': row[3], 'buy_link': row[4],
            'channel': row[5], 'msg_per_hour': row[6], 'enable_new_buy': bool(row[7]),
            'link_ratio': row[8] if row[8] is not None else 100
        }
    return None

init_db()

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"DJANGO Crypto Bot Server is Running 24/7!")

def run_health_check_server():
    port = int(os.getenv("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        logging.error(f"Health check server error: {e}")

threading.Thread(target=run_health_check_server, daemon=True).start()

(
    MAIN_MENU, PLAN_SELECT, COIN_NAME, COIN_DESC, CONTRACT, 
    BUY_LINK, CHANNEL, VERIFY_ADMIN, MSG_PER_HOUR, LINK_RATIO, ENABLE_NEW_BUY, 
    EDIT_SELECT_CHANNEL, EDIT_OPTIONS_MENU, CONFIRM_CANCEL_SUB
) = range(14)

ACTIVE_PUBLISH_TASKS = {}

BUY_TEMPLATES = [
    "🚀 NEW BUY DETECTED!\n\n💎 Token: {coin_name}\n💰 Amount: ${amount}\n🛒 Buy Here: {buy_link}\n📜 Contract: {contract}\n\n🔥 Whales are accumulating!",
    "📈 GREEN CANDLE ALERT!\n\nNew buy order executed: ${amount} on {coin_name}! 🔥\n🛒 DEXScreener: {buy_link}\n📜 Contract: {contract}",
    "🐳 WHALE BUY DETECTED!\n\nA massive buy order of ${amount} just came in for {coin_name}!\n📢 Official Channel: {channel}\n🛒 Buy Now: {buy_link}"
]

COMMUNITY_TEMPLATES = [
    "☀️ Good Morning {coin_name} Army!\nWhat are your price targets for today? Drop them below! 👇🔥",
    "🔥 GM legends! Is {coin_name} ready for the next big move? Stay tuned! 🚀",
    "💪 Community Check! How strong are the {coin_name} holders today? Let's go! 💎🙌",
    "🌙 GN to all {coin_name} believers! Big things are coming tomorrow! ✨"
]

FREE_PLAN_AD_TEXT = "\n\n🤖 Powered by Django AI — Affordable AI tools for your crypto project!"

def parse_channel_input(user_input: str) -> str:
    clean_input = user_input.strip()
    if clean_input.startswith("https://t.me/"):
        channel_name = clean_input.replace("https://t.me/", "")
        if clean_input.startswith("+") or "joinchat" in channel_name:
            return clean_input
        return f"@{channel_name.split('/')[0]}"
    elif clean_input.startswith("http://t.me/"):
        channel_name = clean_input.replace("http://t.me/", "")
        return f"@{channel_name.split('/')[0]}"
    elif not clean_input.startswith("@") and not clean_input.startswith("-100"):
        return f"@{clean_input}"
    return clean_input

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    channels = get_user_channels(user_id)

    if channels:
        keyboard = [
            [InlineKeyboardButton("➕ Buy / Setup for Another Channel", callback_data='menu_buy_new')],
            [InlineKeyboardButton("⚙️ Edit Settings for Existing Channel", callback_data='menu_edit_existing')]
        ]
        msg = "🤖 Welcome back to DJANGO Crypto Auto-Promoter!\n\nYou have active channel promotional campaigns running.\nWhat would you like to do today?"
        if update.message:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return MAIN_MENU
    else:
        return await show_subscription_plans(update, context)

async def show_subscription_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    welcome_msg = "🤖 Welcome to DJANGO Crypto Auto-Promoter Bot!\n\nBoost your channel engagement with AI posts & buy alerts.\n\n💰 Select a Plan to Activate:"
    keyboard = [
        [InlineKeyboardButton("🎁 Free Plan (4 Posts/Day)", callback_data='plan_free')],
        [InlineKeyboardButton("💎 1 Month ($10 TON)", callback_data='plan_1_month')],
        [InlineKeyboardButton("💎 6 Months ($50 TON)", callback_data='plan_6_months')],
        [InlineKeyboardButton("💎 12 Months ($80 TON)", callback_data='plan_12_months')]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(welcome_msg, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(welcome_msg, reply_markup=InlineKeyboardMarkup(keyboard))
    return PLAN_SELECT

if __name__ == '__main__':
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    if not TOKEN:
        logging.error("No TELEGRAM_TOKEN found in environment variables!")
    else:
        application = ApplicationBuilder().token(TOKEN).build()
        
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', start)],
            states={
                MAIN_MENU: [
                    CallbackQueryHandler(start, pattern='^menu_buy_new$'),
                    CallbackQueryHandler(start, pattern='^menu_edit_existing$')
                ],
                PLAN_SELECT: [
                    CallbackQueryHandler(show_subscription_plans, pattern='^plan_')
                ]
            },
            fallbacks=[CommandHandler('start', start)],
        )
        
        application.add_handler(conv_handler)
        
        logging.info("Starting Telegram Bot Polling...")
        application.run_polling()

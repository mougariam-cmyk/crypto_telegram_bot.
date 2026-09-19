import os
import random
import psycopg2
import asyncio
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Google GenAI library
from google import genai
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

from config import TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, TON_WALLET_ADDRESS, PRICES
from fallback_db import get_fallback_message

# Enable Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==========================================
# DATABASE SYSTEM (Supabase / PostgreSQL)
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    """Establish a connection to the Supabase database."""
    db_url = DATABASE_URL
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(db_url)

def init_db():
    """Create subscribers table if it doesn't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            selected_plan TEXT,
            coin_name TEXT,
            coin_desc TEXT,
            contract TEXT,
            buy_link TEXT,
            channel TEXT,
            msg_per_hour INTEGER,
            enable_new_buy INTEGER,
            subscription_status TEXT DEFAULT 'active'
        );
    ''')
    conn.commit()
    cursor.close()
    conn.close()

def save_user_data(user_id: int, username: str, data: dict):
    """Save or update full user subscription & setup data in Supabase."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO users (
            user_id, username, selected_plan, coin_name, coin_desc, 
            contract, buy_link, channel, msg_per_hour, enable_new_buy, subscription_status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
        ON CONFLICT(user_id) DO UPDATE SET
            username=EXCLUDED.username,
            selected_plan=EXCLUDED.selected_plan,
            coin_name=EXCLUDED.coin_name,
            coin_desc=EXCLUDED.coin_desc,
            contract=EXCLUDED.contract,
            buy_link=EXCLUDED.buy_link,
            channel=EXCLUDED.channel,
            msg_per_hour=EXCLUDED.msg_per_hour,
            enable_new_buy=EXCLUDED.enable_new_buy,
            subscription_status='active';
    ''', (
        user_id,
        username,
        data.get('selected_plan', ''),
        data.get('coin_name', ''),
        data.get('coin_desc', ''),
        data.get('contract', ''),
        data.get('buy_link', ''),
        data.get('channel', ''),
        data.get('msg_per_hour', 2),
        1 if data.get('enable_new_buy', False) else 0
    ))
    conn.commit()
    cursor.close()
    conn.close()

def get_active_users():
    """Retrieve all active users from Supabase to restore background publishing on restart."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, selected_plan, coin_name, coin_desc, contract, buy_link, channel, msg_per_hour, enable_new_buy
        FROM users WHERE subscription_status = 'active';
    ''')
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    
    users_data = []
    for row in rows:
        users_data.append({
            'user_id': row[0],
            'selected_plan': row[1],
            'coin_name': row[2],
            'coin_desc': row[3],
            'contract': row[4],
            'buy_link': row[5],
            'channel': row[6],
            'msg_per_hour': row[7],
            'enable_new_buy': bool(row[8])
        })
    return users_data

# Initialize Database Table on startup
init_db()

# Lightweight Web Server for Render Health Check (24/7 Live)
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"DJANGO Crypto Bot Server is Running 24/7!")

def run_health_check_server():
    port = int(os.getenv("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logging.info(f"Health check server running on port {port}")
        server.serve_forever()
    except Exception as e:
        logging.error(f"Health check server error: {e}")

threading.Thread(target=run_health_check_server, daemon=True).start()

# Conversation States
PLAN_SELECT, COIN_NAME, COIN_DESC, CONTRACT, BUY_LINK, CHANNEL, MSG_PER_HOUR, ENABLE_NEW_BUY = range(8)

# Hype & Buy Templates (Include text links)
BUY_TEMPLATES = [
    "🚀 **NEW BUY DETECTED!** 🚀\n\n💎 **Token:** {coin_name}\n💰 **Amount:** ${amount}\n🛒 **Buy Here:** {buy_link}\n📜 **Contract:** `{contract}`\n\n🔥 Whales are accumulating!",
    "📈 **GREEN CANDLE ALERT!** 📈\n\nNew buy order executed: **${amount}** on {coin_name}! 🔥\n🛒 **DEXScreener:** {buy_link}\n📜 **Contract:** `{contract}`",
    "🐳 **WHALE BUY DETECTED!** 🐳\n\nA massive buy order of **${amount}** just came in for {coin_name}!\n📢 **Official Channel:** {channel}\n🛒 **Buy Now:** {buy_link}"
]

# Community & Engagement Templates (No links)
COMMUNITY_TEMPLATES = [
    "☀️ **Good Morning {coin_name} Army!**\nWhat are your price targets for today? Drop them below! 👇🔥",
    "🔥 **GM legends!** Is {coin_name} ready for the next big move? Stay tuned! 🚀",
    "💪 **Community Check!** How strong are the {coin_name} holders today? Let's go! 💎🙌",
    "🌙 **GN to all {coin_name} believers!** Big things are coming tomorrow! ✨"
]

def generate_ai_post(data):
    """Generate high-energy promo post with text links via Gemini AI."""
    try:
        if not GEMINI_API_KEY:
            logging.warning("GEMINI_API_KEY is missing! Using fallback message.")
            return get_fallback_message(data)
            
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        prompt = f"""
Write a short, high-energy, hyped promotional post for a crypto token named {data['coin_name']}.
Project Details / Description: {data.get('coin_desc', 'Top crypto gem on the market')}
Use exciting crypto emojis and bullet points.
Include these exact details:
Buy Link (DEXScreener): {data['buy_link']}
Contract Address: `{data['contract']}`
Telegram Channel: {data['channel']}
Keep it concise, hype-driven, and under 4 lines. Output in English only.
"""
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        
        if response and response.text:
            return response.text
        else:
            logging.warning("Gemini returned an empty response. Falling back to database.")
            return get_fallback_message(data)

    except Exception as e:
        logging.error(f"Gemini API Error details: {type(e).__name__} - {e}")
        return get_fallback_message(data)

def generate_post(data):
    """
    Determine post type:
    - Community engagement post (30%) - no links
    - Simulated buy alert post (30%) - with links
    - AI Hype post (40%) - with links
    """
    post_type_chance = random.random()
    
    # 30% Community engagement post
    if post_type_chance < 0.30:
        template = random.choice(COMMUNITY_TEMPLATES)
        return template.format(coin_name=data['coin_name'])
    
    # 30% Simulated Buy alert post (if enabled)
    enable_buy = data.get('enable_new_buy', False)
    if enable_buy and post_type_chance < 0.60:
        amount = random.randint(50, 1500)
        template = random.choice(BUY_TEMPLATES)
        return template.format(
            coin_name=data['coin_name'],
            buy_link=data['buy_link'],
            contract=data['contract'],
            channel=data['channel'],
            amount=amount
        )
    
    # 40% AI Hype post
    return generate_ai_post(data)

# Step 1: Start Command & Subscription Plans
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_msg = (
        "🤖 **Welcome to DJANGO Crypto Auto-Promoter Bot!**\n\n"
        "Boost your crypto channel & group engagement with AI-generated hype posts, "
        "simulated whale buys, and custom promotional schedules.\n\n"
        "💰 **Select a Subscription Plan to Activate:**"
    )
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = [
        [InlineKeyboardButton("💎 1 Month ($10 TON)", callback_data='plan_1_month')],
        [InlineKeyboardButton("💎 6 Months ($50 TON)", callback_data='plan_6_months')],
        [InlineKeyboardButton("💎 12 Months ($80 TON)", callback_data='plan_12_months')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_msg, reply_markup=reply_markup, parse_mode='Markdown')
    return PLAN_SELECT

# Step 2: Select Plan & Start Setup
async def plan_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    plan_key = query.data.replace('plan_', '')
    context.user_data['selected_plan'] = plan_key

    msg = (
        "✅ **Subscription Activated (Test Mode Enabled)!**\n\n"
        "⚙️ **Let's configure your bot settings.**\n\n"
        "1️⃣ Send your **Token / Coin Name** (e.g., HIPPO):"
    )
    
    await query.edit_message_text(msg, parse_mode='Markdown')
    return COIN_NAME

async def get_coin_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['coin_name'] = update.message.text
    await update.message.reply_text("2️⃣ Send a brief **Description / Hype Points** for your token (used by AI to write posts):")
    return COIN_DESC

async def get_coin_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['coin_desc'] = update.message.text
    await update.message.reply_text("3️⃣ Send your **Token Contract Address (CA)**:")
    return CONTRACT

async def get_contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['contract'] = update.message.text
    await update.message.reply_text("4️⃣ Send your **DEXScreener or Buy Link**:")
    return BUY_LINK

async def get_buy_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['buy_link'] = update.message.text
    await update.message.reply_text("5️⃣ Send your **Channel Username or Link** (e.g., @mychannel or https://t.me/mychannel):")
    return CHANNEL

async def get_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['channel'] = update.message.text
    await update.message.reply_text("6️⃣ How many posts per hour do you want? (Enter a number from 1 to 20):")
    return MSG_PER_HOUR

async def get_msg_per_hour(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text)
        if count < 1: 
            count = 1
        if count > 20: 
            count = 20
    except ValueError:
        count = 2
    context.user_data['msg_per_hour'] = count

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = [
        [
            InlineKeyboardButton("Yes 🚀 (Include Buy Alerts)", callback_data='newbuy_yes'),
            InlineKeyboardButton("No 🤖 (AI Hype Only)", callback_data='newbuy_no')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("7️⃣ Would you like to enable **Simulated New Buy Alerts** (30% chance per post)?", reply_markup=reply_markup)
    return ENABLE_NEW_BUY

async def finish_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['enable_new_buy'] = (query.data == 'newbuy_yes')
    coin = context.user_data.get('coin_name')
    user_id = query.from_user.id
    username = query.from_user.username or ""

    # Save to Supabase Database
    save_user_data(user_id, username, context.user_data)
    logging.info(f"User {user_id} saved to Supabase database.")
    
    msg = (
        f"🎉 **Setup Complete for {coin}!**\n\n"
        "⚡ Your auto-publisher is now active!\n"
        "Make sure to add this bot as an **Administrator** in your channel so it can publish posts."
    )
    await query.edit_message_text(msg, parse_mode='Markdown')
    
    # Start auto-publishing loop securely as background task
    asyncio.create_task(start_publishing(context.application, context.user_data.copy()))
    return ConversationHandler.END

async def start_publishing(app, data):
    msg_per_hour = data.get('msg_per_hour', 2)
    delay_seconds = int((60 / msg_per_hour) * 60)
    channel_id = str(data.get('channel', '')).strip()
    
    if channel_id and not channel_id.startswith('@') and not channel_id.startswith('-100') and not channel_id.startswith('http'):
        channel_id = f"@{channel_id}"
    
    logging.info(f"Starting auto-publisher for {channel_id} with interval {delay_seconds}s")

    while True:
        try:
            post_text = generate_post(data)

            try:
                await app.bot.send_message(
                    chat_id=channel_id, 
                    text=post_text, 
                    parse_mode='Markdown'
                )
            except Exception as parse_err:
                logging.warning(f"Markdown parse error, sending plain text: {parse_err}")
                await app.bot.send_message(
                    chat_id=channel_id, 
                    text=post_text
                )

            logging.info(f"Post successfully sent for {data['coin_name']} to {channel_id}")
        except asyncio.CancelledError:
            logging.info(f"Publishing task for {channel_id} was gracefully cancelled.")
            break
        except Exception as e:
            logging.error(f"Failed to send post to channel {channel_id}: {e}")
            
        try:
            await asyncio.sleep(delay_seconds)
        except asyncio.CancelledError:
            logging.info(f"Sleep interrupted. Stopping loop for {channel_id}.")
            break

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Setup canceled. Send /start to begin again.")
    return ConversationHandler.END

# Restore background publisher tasks for all users from DB upon bot startup
async def restore_active_tasks(app):
    users = get_active_users()
    logging.info(f"Restoring {len(users)} active auto-publisher tasks from Supabase database...")
    for user_data in users:
        asyncio.create_task(start_publishing(app, user_data))

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PLAN_SELECT: [CallbackQueryHandler(plan_selected, pattern='^plan_')],
            COIN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_coin_name)],
            COIN_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_coin_desc)],
            CONTRACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_contract)],
            BUY_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_buy_link)],
            CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_channel)],
            MSG_PER_HOUR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_msg_per_hour)],
            ENABLE_NEW_BUY: [CallbackQueryHandler(finish_setup, pattern='^newbuy_')],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(conv_handler)
    
    # Restore saved campaigns on startup
    loop = asyncio.get_event_loop()
    loop.create_task(restore_active_tasks(app))

    print("DJANGO Bot is running with Supabase Database Integration...")
    app.run_polling(drop_pending_updates=True)

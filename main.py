import os
import random
import psycopg2
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

def get_all_active_campaigns():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, selected_plan, coin_name, coin_desc, contract, buy_link, channel, msg_per_hour, enable_new_buy, link_ratio
        FROM users WHERE subscription_status = 'active';
    ''')
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    campaigns = []
    for row in rows:
        campaigns.append({
            'user_id': row[0], 'selected_plan': row[1], 'coin_name': row[2],
            'coin_desc': row[3], 'contract': row[4], 'buy_link': row[5],
            'channel': row[6], 'msg_per_hour': row[7], 'enable_new_buy': bool(row[8]),
            'link_ratio': row[9] if row[9] is not None else 100
        })
    return campaigns

def get_user_channels(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT channel, coin_name FROM users WHERE user_id = %s AND subscription_status = %s;', (user_id, 'active'))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

init_db()

# ==========================================
# HEALTH CHECK SERVER (For Render)
# ==========================================
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

# States (تمت إضافة الحالات الناقصة للأزرار)
(
    MAIN_MENU, PLAN_SELECT, COIN_NAME, COIN_DESC, CONTRACT, 
    BUY_LINK, CHANNEL, VERIFY_ADMIN, MSG_PER_HOUR, LINK_RATIO, ENABLE_NEW_BUY, 
    EDIT_SELECT_CHANNEL, EDIT_OPTIONS_MENU, CONFIRM_CANCEL_SUB
) = range(14)

BUY_TEMPLATES = [
    "🚀 NEW BUY DETECTED!\n\n💎 Token: {coin_name}\n💰 Amount: ${amount}\n🛒 Buy Here: {buy_link}\n📜 Contract: {contract}\n\n🔥 Whales are accumulating!",
    "📈 GREEN CANDLE ALERT!\n\nNew buy order executed: ${amount} on {coin_name}! 🔥\n🛒 DEXScreener: {buy_link}\n📜 Contract: {contract}"
]

FREE_PLAN_AD_TEXT = "\n\n🤖 Powered by Django AI — Affordable AI tools for your crypto project!"

def parse_channel_input(user_input: str) -> str:
    clean_input = user_input.strip()
    if clean_input.startswith("https://t.me/"):
        channel_name = clean_input.replace("https://t.me/", "")
        return f"@{channel_name.split('/')[0]}"
    elif not clean_input.startswith("@") and not clean_input.startswith("-100"):
        return f"@{clean_input}"
    return clean_input

def generate_ai_post(data, include_links=True):
    try:
        client = get_next_gemini_client()
        if not client:
            return get_fallback_message(data)
            
        if include_links:
            prompt = f"Write a short, high-energy promotional post for a crypto token named {data['coin_name']}. Description: {data.get('coin_desc', '')}. Include Buy Link: {data['buy_link']} and Contract: {data['contract']}. Keep it under 4 lines, English only, no asterisks."
        else:
            prompt = f"Write a short, engaging community question for a crypto token named {data['coin_name']}. Keep it interactive, under 4 lines, English only, no asterisks."

        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        return response.text if response and response.text else get_fallback_message(data)
    except Exception as e:
        logging.error(f"Gemini API Error: {e}")
        return get_fallback_message(data)

# ==========================================
# نظام النشر التلقائي (Background Poster Loop)
# ==========================================
async def automated_poster_job(context: ContextTypes.DEFAULT_TYPE):
    campaigns = get_all_active_campaigns()
    for data in campaigns:
        try:
            channel = data['channel']
            link_ratio = data.get('link_ratio', 100)
            include_links = random.randint(1, 100) <= link_ratio

            if data.get('enable_new_buy', False) and random.random() < 0.3:
                template = random.choice(BUY_TEMPLATES)
                amount = random.randint(100, 2500)
                text = template.format(
                    coin_name=data['coin_name'],
                    amount=amount,
                    buy_link=data['buy_link'],
                    contract=data['contract']
                )
            else:
                text = generate_ai_post(data, include_links=include_links)

            if data.get('selected_plan') == 'free':
                text += FREE_PLAN_AD_TEXT

            await context.bot.send_message(chat_id=channel, text=text, disable_web_page_preview=True)
            logging.info(f"Successfully posted to channel {channel}")
        except Exception as e:
            logging.error(f"Failed to send post to channel {data.get('channel')}: {e}")

# ==========================================
# التفاعل مع رسائل المستخدمين في المجموعات
# ==========================================
async def handle_group_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    chat = message.chat
    if chat.type in ["group", "supergroup"]:
        text = message.text.strip()
        bot_username = context.bot.username

        # الرد فقط عند عمل منشن للبوت أو الرد على رسالته (لتفادي استنزاف الطلبات)
        is_mentioned = (bot_username and f"@{bot_username}" in text) or (message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == context.bot.id)

        if is_mentioned:
            try:
                client = get_next_gemini_client()
                if not client:
                    return

                prompt = f"""
You are an intelligent, friendly crypto community assistant bot answering members in a Telegram group chat.
User message: "{text}"
Respond helpfully, engagingly, and concisely in English (or match the user's language if they spoke Arabic). Keep it natural and crypto-friendly.
"""
                response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
                if response and response.text:
                    await message.reply_text(response.text)
            except Exception as e:
                logging.error(f"Group chat AI error: {e}")

# ==========================================
# واجهة البوت والخطوات
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    channels = get_user_channels(user_id)

    if channels:
        keyboard = [
            [InlineKeyboardButton("➕ Setup for Another Channel", callback_data='menu_buy_new')],
            [InlineKeyboardButton("⚙️ Edit Existing Settings", callback_data='menu_edit_existing')]
        ]
        msg = "🤖 Welcome back to DJANGO Crypto Auto-Promoter & Group Assistant!\nWhat would you like to do?"
        if update.message:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        elif update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return MAIN_MENU
    else:
        return await show_subscription_plans(update, context)

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'menu_buy_new':
        return await show_subscription_plans(update, context)
    elif query.data == 'menu_edit_existing':
        await query.edit_message_text("⚙️ Edit feature coming soon. Send /start to restart.")
        return ConversationHandler.END
    return MAIN_MENU

async def show_subscription_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    welcome_msg = "🤖 Welcome to DJANGO Crypto Bot!\n\nSelect a Plan to Activate:"
    keyboard = [
        [InlineKeyboardButton("🎁 Free Plan (4 Posts/Day)", callback_data='plan_free')],
        [InlineKeyboardButton("💎 1 Month ($10 TON)", callback_data='plan_1_month')],
        [InlineKeyboardButton("💎 6 Months ($50 TON)", callback_data='plan_6_months')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(welcome_msg, reply_markup=reply_markup)
    else:
        await update.message.reply_text(welcome_msg, reply_markup=reply_markup)
    return PLAN_SELECT

async def plan_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan_key = query.data.replace('plan_', '')
    context.user_data['selected_plan'] = plan_key
    context.user_data['is_editing'] = False

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_plans')]])
    await query.edit_message_text(f"✅ Plan Selected: {plan_key.upper()}\n\n1️⃣ Send your Token / Coin Name (e.g., HIPPO):", reply_markup=keyboard)
    return COIN_NAME

async def get_coin_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['coin_name'] = update.message.text.strip()
    await update.message.reply_text("2️⃣ Send a brief Description / Hype Points for your token:")
    return COIN_DESC

async def get_coin_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['coin_desc'] = update.message.text.strip()
    await update.message.reply_text("3️⃣ Send your Token Contract Address (CA):")
    return CONTRACT

async def get_contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['contract'] = update.message.text.strip()
    await update.message.reply_text("4️⃣ Send your DEXScreener or Buy Link:")
    return BUY_LINK

async def get_buy_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['buy_link'] = update.message.text.strip()
    await update.message.reply_text("5️⃣ Send your Channel Username (e.g., @mychannel):")
    return CHANNEL

async def get_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    formatted_channel = parse_channel_input(update.message.text.strip())
    context.user_data['channel'] = formatted_channel
    
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    save_user_data(user_id, username, context.user_data)

    await update.message.reply_text(f"🎉 Success! Campaign configured for {formatted_channel}. The bot will now publish posts automatically and reply to user queries in groups.")
    return ConversationHandler.END

# ==========================================
# MAIN FUNCTION
# ==========================================
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logging.error("No TELEGRAM_BOT_TOKEN found in environment variables!")
        return

    app = ApplicationBuilder().token(token).build()

    if app.job_queue:
        app.job_queue.run_repeating(automated_poster_job, interval=3600, first=10)

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            # تم إضافة حالة القائمة الرئيسية لمعالجة استجابة الأزرار بنجاح
            MAIN_MENU: [CallbackQueryHandler(menu_handler, pattern='^menu_')],
            PLAN_SELECT: [
                CallbackQueryHandler(plan_selected, pattern='^plan_'),
                CallbackQueryHandler(show_subscription_plans, pattern='^back_to_plans')
            ],
            COIN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_coin_name)],
            COIN_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_coin_desc)],
            CONTRACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_contract)],
            BUY_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_buy_link)],
            CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_channel)],
        },
        fallbacks=[CommandHandler('start', start)],
    )

    app.add_handler(conv_handler)
    
    # معالجة رسائل المجموعات للتفاعل مع الأعضاء (فقط عند عمل منشن للبوت)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), handle_group_messages))

    logging.info("Bot is starting...")
    app.run_polling()

if __name__ == '__main__':
    main()

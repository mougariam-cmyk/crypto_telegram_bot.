import os
import re
import random
import psycopg2
import asyncio
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Google GenAI library
from google import genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

from config import TELEGRAM_BOT_TOKEN, TON_WALLET_ADDRESS, PRICES
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
    
    cursor.execute('''
        DO $$ 
        BEGIN 
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='users' AND column_name='link_ratio'
            ) THEN 
                ALTER TABLE users ADD COLUMN link_ratio INTEGER DEFAULT 100; 
            END IF; 
        END $$;
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
        user_id,
        username,
        target_plan,
        data.get('coin_name', ''),
        data.get('coin_desc', ''),
        data.get('contract', ''),
        data.get('buy_link', ''),
        data.get('channel', ''),
        data.get('msg_per_hour', 2),
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
    cursor.execute('''
        SELECT channel, coin_name FROM users WHERE user_id = %s AND subscription_status = 'active';
    ''', (user_id,))
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
            'user_id': user_id,
            'selected_plan': row[0],
            'coin_name': row[1],
            'coin_desc': row[2],
            'contract': row[3],
            'buy_link': row[4],
            'channel': row[5],
            'msg_per_hour': row[6],
            'enable_new_buy': bool(row[7]),
            'link_ratio': row[8] if row[8] is not None else 100
        }
    return None

def get_active_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, selected_plan, coin_name, coin_desc, contract, buy_link, channel, msg_per_hour, enable_new_buy, link_ratio
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
            'enable_new_buy': bool(row[8]),
            'link_ratio': row[9] if row[9] is not None else 100
        })
    return users_data

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
        logging.info(f"Health check server running on port {port}")
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

FREE_PLAN_FOOTER_BUTTONS = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📢 Telegram", url="https://t.me/django_ai"),
        InlineKeyboardButton("🐦 Twitter", url="https://x.com/django_ai"),
        InlineKeyboardButton("🌐 Website", url="https://django.ai"),
        InlineKeyboardButton("🤖 Bot", url="https://t.me/django_promoter_bot")
    ]
])

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

def generate_ai_post(data, include_links=True):
    try:
        client = get_next_gemini_client()
        if not client:
            return get_fallback_message(data)
            
        if include_links:
            prompt = f"""
Write a short, high-energy, hyped promotional post for a crypto token named {data['coin_name']}.
Project Details / Description: {data.get('coin_desc', 'Top crypto gem on the market')}
Use exciting crypto emojis.
Include these exact details:
Buy Link (DEXScreener): {data['buy_link']}
Contract Address: {data['contract']}
Telegram Channel: {data['channel']}
Keep it concise, hype-driven, and under 4 lines. Output in English only. Do not use markdown asterisk stars.
"""
        else:
            prompt = f"""
Write a short, high-energy, community-engaging promotional post for a crypto token named {data['coin_name']}.
Project Details / Description: {data.get('coin_desc', 'Top crypto gem on the market')}
Use exciting crypto emojis.
DO NOT include any buy links, contract addresses, or URLs.
Keep it concise, hype-driven, interactive, and under 4 lines. Output in English only. Do not use markdown asterisk stars.
"""

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        
        if response and response.text:
            return response.text
        else:
            return get_fallback_message(data)

    except Exception as e:
        logging.error(f"Gemini API Error with rotation: {e}")
        return get_fallback_message(data)

def generate_post(data):
    link_ratio = data.get('link_ratio', 100) / 100.0
    should_include_links = (random.random() < link_ratio)

    if not should_include_links:
        template = random.choice(COMMUNITY_TEMPLATES)
        post_text = template.format(coin_name=data['coin_name'])
    else:
        enable_buy = data.get('enable_new_buy', False)
        if enable_buy and random.random() < 0.50:
            amount = random.randint(50, 1500)
            template = random.choice(BUY_TEMPLATES)
            post_text = template.format(
                coin_name=data['coin_name'],
                buy_link=data['buy_link'],
                contract=data['contract'],
                channel=data['channel'],
                amount=amount
            )
        else:
            post_text = generate_ai_post(data, include_links=True)

    if data.get('selected_plan') == 'free':
        post_text += FREE_PLAN_AD_TEXT

    return post_text

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    channels = get_user_channels(user_id)

    if channels:
        keyboard = [
            [InlineKeyboardButton("➕ Buy / Setup for Another Channel", callback_data='menu_buy_new')],
            [InlineKeyboardButton("⚙️ Edit Settings for Existing Channel", callback_data='menu_edit_existing')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = (
            "🤖 Welcome back to DJANGO Crypto Auto-Promoter!\n\n"
            "You have active channel promotional campaigns running.\n"
            "What would you like to do today?"
        )
        if update.message:
            await update.message.reply_text(msg, reply_markup=reply_markup)
        else:
            await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
        return MAIN_MENU
    else:
        return await show_subscription_plans(update, context)

async def show_subscription_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    welcome_msg = (
        "🤖 Welcome to DJANGO Crypto Auto-Promoter Bot!\n\n"
        "Boost your crypto channel & group engagement with AI-generated hype posts, "
        "simulated whale buys, and custom promotional schedules.\n\n"
        "💰 Select a Plan to Activate:"
    )
    keyboard = [
        [InlineKeyboardButton("🎁 Free Plan (4 Posts/Day)", callback_data='plan_free')],
        [InlineKeyboardButton("💎 1 Month ($10 TON)", callback_data='plan_1_month')],
        [InlineKeyboardButton("💎 6 Months ($50 TON)", callback_data='plan_6_months')],
        [InlineKeyboardButton("💎 12 Months ($80 TON)", callback_data='plan_12_months')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(welcome_msg, reply_markup=reply_markup)
    else:
        await update.message.reply_text(welcome_msg, reply_markup=reply_markup)
    return PLAN_SELECT

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'menu_buy_new':
        return await show_subscription_plans(update, context)
    elif query.data == 'menu_edit_existing':
        user_id = query.from_user.id
        channels = get_user_channels(user_id)
        
        keyboard = []
        for ch, coin in channels:
            clean_ch = ch.replace('@', '')
            keyboard.append([InlineKeyboardButton(f"📢 {ch} ({coin})", callback_data=f"edit_ch_{clean_ch}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='back_to_main')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("⚙️ Select the channel you want to edit:", reply_markup=reply_markup)
        return EDIT_SELECT_CHANNEL

async def show_edit_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    channel = data.get('channel', 'Channel')
    plan = data.get('selected_plan', 'free')
    
    posts_freq = "4 Posts/Day (Fixed)" if plan == 'free' else f"{data.get('msg_per_hour', 2)}/hour"

    text = (
        f"⚙️ Current Settings for {channel}:\n\n"
        f"💳 Plan: {plan.upper()}\n"
        f"1️⃣ Coin Name: {data.get('coin_name', 'Not set')}\n"
        f"2️⃣ Description: {data.get('coin_desc', 'Not set')}\n"
        f"3️⃣ Contract (CA): {data.get('contract', 'Not set')}\n"
        f"4️⃣ Buy Link: {data.get('buy_link', 'Not set')}\n"
        f"6️⃣ Posts Frequency: {posts_freq}\n"
        f"7️⃣ Posts with Links Ratio: {data.get('link_ratio', 100)}%\n"
        f"8️⃣ Simulated Buy Alerts: {'Enabled' if data.get('enable_new_buy') else 'Disabled'}\n\n"
        "👇 Select an option below to update or cancel:"
    )

    keyboard = [
        [InlineKeyboardButton("🪙 Edit Coin Name", callback_data='opt_coin_name'), InlineKeyboardButton("📝 Edit Description", callback_data='opt_coin_desc')],
        [InlineKeyboardButton("📜 Edit Contract (CA)", callback_data='opt_contract'), InlineKeyboardButton("🛒 Edit Buy Link", callback_data='opt_buy_link')],
        [InlineKeyboardButton("⏱️ Posts Frequency", callback_data='opt_msg_hour'), InlineKeyboardButton("📊 Posts with Links Ratio", callback_data='opt_ratio')],
        [InlineKeyboardButton("🚀 Buy Alerts Toggle", callback_data='opt_new_buy')],
        [InlineKeyboardButton("🔄 Re-configure All Settings", callback_data='opt_edit_all')],
        [InlineKeyboardButton("❌ Cancel / Stop Subscription", callback_data='opt_cancel_sub')],
        [InlineKeyboardButton("✅ Save & Exit Settings", callback_data='opt_save_finish')],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data='back_to_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return EDIT_OPTIONS_MENU

async def select_channel_to_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'back_to_main':
        return await start(update, context)

    raw_channel = query.data.replace('edit_ch_', '')
    user_id = query.from_user.id
    channel_data = get_user_channel_data(user_id, raw_channel)

    if channel_data:
        context.user_data.update(channel_data)
        context.user_data['is_editing'] = True
        return await show_edit_options(update, context)
    else:
        await query.edit_message_text("❌ Channel config not found.")
        return ConversationHandler.END

async def edit_options_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'back_to_main':
        return await start(update, context)

    if data == 'opt_coin_name':
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await query.edit_message_text("1️⃣ Send your new Token / Coin Name (e.g., HIPPO):", reply_markup=keyboard)
        return COIN_NAME
    elif data == 'opt_coin_desc':
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await query.edit_message_text("2️⃣ Send a new Description / Hype Points for your token:", reply_markup=keyboard)
        return COIN_DESC
    elif data == 'opt_contract':
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await query.edit_message_text("3️⃣ Send your new Token Contract Address (CA):", reply_markup=keyboard)
        return CONTRACT
    elif data == 'opt_buy_link':
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await query.edit_message_text("4️⃣ Send your new DEXScreener or Buy Link:", reply_markup=keyboard)
        return BUY_LINK
    elif data == 'opt_msg_hour':
        if context.user_data.get('selected_plan') == 'free':
            await query.answer("⚠️ Free Plan is restricted to 4 posts/day maximum. Upgrade to Premium!", show_alert=True)
            return EDIT_OPTIONS_MENU
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await query.edit_message_text("6️⃣ How many posts per hour do you want? (1 to 20):", reply_markup=keyboard)
        return MSG_PER_HOUR
    elif data == 'opt_ratio':
        keyboard = [
            [InlineKeyboardButton("0%", callback_data='ratio_0'), InlineKeyboardButton("25%", callback_data='ratio_25'), InlineKeyboardButton("50%", callback_data='ratio_50')],
            [InlineKeyboardButton("75%", callback_data='ratio_75'), InlineKeyboardButton("100%", callback_data='ratio_100')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]
        ]
        await query.edit_message_text("7️⃣ Select the percentage of posts that should include Buy Links & Contract:", reply_markup=InlineKeyboardMarkup(keyboard))
        return LINK_RATIO
    elif data == 'opt_new_buy':
        keyboard = [
            [InlineKeyboardButton("Yes 🚀 (Enable Buy Alerts)", callback_data='newbuy_yes')],
            [InlineKeyboardButton("No 🤖 (AI Posts Only)", callback_data='newbuy_no')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]
        ]
        await query.edit_message_text("8️⃣ Would you like to enable Simulated New Buy Alerts?", reply_markup=InlineKeyboardMarkup(keyboard))
        return ENABLE_NEW_BUY
    elif data == 'opt_edit_all':
        context.user_data['is_editing'] = False
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await query.edit_message_text("1️⃣ Send your new Token / Coin Name:", reply_markup=keyboard)
        return COIN_NAME
    elif data == 'opt_cancel_sub':
        channel = context.user_data.get('channel', '')
        keyboard = [
            [InlineKeyboardButton("YES, Stop Auto-Promoter 🛑", callback_data='confirm_cancel_yes')],
            [InlineKeyboardButton("NO, Keep Publishing 🚀", callback_data='confirm_cancel_no')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]
        ]
        await query.edit_message_text(f"⚠️ Are you sure you want to cancel your campaign for {channel}?", reply_markup=InlineKeyboardMarkup(keyboard))
        return CONFIRM_CANCEL_SUB
    elif data == 'opt_save_finish':
        return await finish_setup(update, context)

async def confirm_cancel_sub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'back_to_edit_menu' or query.data == 'confirm_cancel_no':
        return await show_edit_options(update, context)

    if query.data == 'confirm_cancel_yes':
        user_id = query.from_user.id
        channel = context.user_data.get('channel')

        cancel_user_subscription(user_id, channel)

        if channel in ACTIVE_PUBLISH_TASKS:
            ACTIVE_PUBLISH_TASKS[channel].cancel()
            del ACTIVE_PUBLISH_TASKS[channel]

        await query.edit_message_text(f"🛑 Subscription Cancelled! Auto-publishing for channel {channel} has been stopped.")
        return ConversationHandler.END

async def plan_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'back_to_main':
        return await start(update, context)

    plan_key = query.data.replace('plan_', '')
    context.user_data['selected_plan'] = plan_key
    context.user_data['is_editing'] = False

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_plans')]])
    msg = (
        f"✅ Plan Selected: {plan_key.upper()}\n\n"
        "⚙️ Let's configure your bot settings.\n\n"
        "1️⃣ Send your Token / Coin Name (e.g., HIPPO):"
    )
    
    await query.edit_message_text(msg, reply_markup=keyboard)
    return COIN_NAME

async def back_to_edit_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await show_edit_options(update, context)

async def back_to_plans_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await show_subscription_plans(update, context)

async def get_coin_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['coin_name'] = update.message.text.strip()
    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_coin_name')]])
    await update.message.reply_text("2️⃣ Send a brief Description / Hype Points for your token:", reply_markup=keyboard)
    return COIN_DESC

async def get_coin_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['coin_desc'] = update.message.text.strip()
    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)
        
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_coin_desc')]])
    await update.message.reply_text("3️⃣ Send your Token Contract Address (CA):", reply_markup=keyboard)
    return CONTRACT

async def get_contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['contract'] = update.message.text.strip()
    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)
        
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_contract')]])
    await update.message.reply_text("4️⃣ Send your DEXScreener or Buy Link:", reply_markup=keyboard)
    return BUY_LINK

async def get_buy_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['buy_link'] = update.message.text.strip()
    
    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_buy_link')]])
    await update.message.reply_text(
        "5️⃣ Send your Channel Username (e.g., @mychannel) or Channel Link (e.g., https://t.me/mychannel):",
        reply_markup=keyboard
    )
    return CHANNEL

async def get_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_channel = update.message.text.strip()
    formatted_channel = parse_channel_input(raw_channel)
    user_id = update.effective_user.id

    existing_data = get_user_channel_data(user_id, formatted_channel)
    if existing_data and existing_data['selected_plan'] != 'free' and context.user_data.get('selected_plan') == 'free':
        context.user_data['selected_plan'] = existing_data['selected_plan']

    context.user_data['channel'] = formatted_channel

    msg = (
        f"⚠️ IMPORTANT STEP: Admin Rights Required!\n\n"
        f"Please add our bot as an Administrator in your channel {formatted_channel} with Post Messages permission.\n\n"
        "📋 **Steps to add the bot:**\n"
        "1. Copy the Bot ID below.\n"
        "2. Go to your channel/group, open administrators list, and search for the bot using this ID.\n"
        "3. Grant it administrator permissions (Post Messages).\n"
        "4. Click the button below once done!"
    )
    keyboard = [
        [InlineKeyboardButton("📋 Copy Bot ID (@DJANGO_CRYPTO_BOT)", copy_text={"text": "@DJANGO_CRYPTO_BOT"})],
        [InlineKeyboardButton("🔗 I have promoted the bot / Continue", callback_data='verify_admin')],
        [InlineKeyboardButton("🔙 Back", callback_data='back_to_channel_input')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(msg, reply_markup=reply_markup)
    return VERIFY_ADMIN

async def verify_admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if query.data == 'back_to_channel_input':
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_buy_link')]])
        await query.edit_message_text("5️⃣ Send your Channel Username or Link (e.g., @mychannel):", reply_markup=keyboard)
        return CHANNEL

    await query.answer()
    channel_id = context.user_data.get('channel')
    bot_id = context.bot.id

    try:
        member = await context.bot.get_chat_member(chat_id=channel_id, user_id=bot_id)
        if member.status in ['administrator', 'creator']:
            await query.answer("✅ Admin status verified successfully!", show_alert=True)
            
            if context.user_data.get('selected_plan') == 'free':
                context.user_data['msg_per_hour'] = 4
                
                keyboard = [
                    [InlineKeyboardButton("0%", callback_data='ratio_0'), InlineKeyboardButton("25%", callback_data='ratio_25'), InlineKeyboardButton("50%", callback_data='ratio_50')],
                    [InlineKeyboardButton("75%", callback_data='ratio_75'), InlineKeyboardButton("100%", callback_data='ratio_100')],
                    [InlineKeyboardButton("🔙 Back", callback_data='back_to_verify_admin')]
                ]
                await query.edit_message_text(
                    "✅ Admin Status Verified!\n\n7️⃣ What percentage of posts should contain Buy Links & Contract?",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return LINK_RATIO

            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_verify_admin')]])
            await query.edit_message_text(
                "✅ Admin Status Verified!\n\n6️⃣ How many posts per hour do you want? (1 to 20):",
                reply_markup=keyboard
            )
            return MSG_PER_HOUR
        else:
            await query.answer("❌ Bot is not promoted to Admin yet!", show_alert=True)
            return VERIFY_ADMIN

    except Exception as e:
        logging.error(f"Admin verification failed: {e}")
        await query.answer("❌ Unable to verify! Ensure the bot is added as Admin.", show_alert=True)
        return VERIFY_ADMIN

async def get_msg_per_hour(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text.strip())
        count = max(1, min(20, count))
    except ValueError:
        count = 2
    context.user_data['msg_per_hour'] = count

    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)

    keyboard = [
        [InlineKeyboardButton("0%", callback_data='ratio_0'), InlineKeyboardButton("25%", callback_data='ratio_25'), InlineKeyboardButton("50%", callback_data='ratio_50')],
        [InlineKeyboardButton("75%", callback_data='ratio_75'), InlineKeyboardButton("100%", callback_data='ratio_100')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("7️⃣ What percentage of posts should contain Buy Links & Contract?", reply_markup=reply_markup)
    return LINK_RATIO

async def get_link_ratio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'back_to_verify_admin':
        msg = (
            f"⚠️ IMPORTANT STEP: Admin Rights Required!\n\n"
            f"Please add our bot as an Administrator in your channel.\n\n"
            "📋 **Steps to add the bot:**\n"
            "1. Copy the Bot ID below.\n"
            "2. Go to your channel/group, open administrators list, and search for the bot using this ID.\n"
            "3. Grant it administrator permissions (Post Messages).\n"
            "4. Click the button below once done!"
        )
        keyboard = [
            [InlineKeyboardButton("📋 Copy Bot ID (@DJANGO_CRYPTO_BOT)", copy_text={"text": "@DJANGO_CRYPTO_BOT"})],
            [InlineKeyboardButton("🔗 I have promoted the bot / Continue", callback_data='verify_admin')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_to_channel_input')]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return VERIFY_ADMIN

    ratio = int(query.data.replace('ratio_', ''))
    context.user_data['link_ratio'] = ratio

    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)

    keyboard = [
        [InlineKeyboardButton("Yes 🚀 (Include Buy Alerts)", callback_data='newbuy_yes')],
        [InlineKeyboardButton("No 🤖 (AI Hype Only)", callback_data='newbuy_no')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("8️⃣ Would you like to enable Simulated New Buy Alerts?", reply_markup=reply_markup)
    return ENABLE_NEW_BUY

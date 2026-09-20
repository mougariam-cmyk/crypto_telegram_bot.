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
    """Create subscribers table and migrate missing columns if needed."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Create table if not exists
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
    
    # 2. Add link_ratio column if missing in existing table
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
    """Save or update user subscription & setup data based on (user_id, channel)."""
    conn = get_db_connection()
    cursor = conn.cursor()
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
        data.get('selected_plan', 'free'),
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
    """Update subscription status to 'cancelled' for a specific user & channel."""
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
    """Retrieve all channels configured by a specific user."""
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
    """Retrieve full configuration data for a specific channel of a user."""
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
    """Retrieve all active users from Supabase to restore background publishing on restart."""
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
(
    MAIN_MENU, PLAN_SELECT, COIN_NAME, COIN_DESC, CONTRACT, 
    BUY_LINK, CHANNEL, VERIFY_ADMIN, MSG_PER_HOUR, LINK_RATIO, ENABLE_NEW_BUY, 
    EDIT_SELECT_CHANNEL, EDIT_OPTIONS_MENU, CONFIRM_CANCEL_SUB
) = range(14)

# Global dictionary to track active publishing asyncio tasks by channel_id
ACTIVE_PUBLISH_TASKS = {}

# Hype & Buy Templates
BUY_TEMPLATES = [
    "🚀 **NEW BUY DETECTED!** 🚀\n\n💎 **Token:** {coin_name}\n💰 **Amount:** ${amount}\n🛒 **Buy Here:** {buy_link}\n📜 **Contract:** `{contract}`\n\n🔥 Whales are accumulating!",
    "📈 **GREEN CANDLE ALERT!** 📈\n\nNew buy order executed: **${amount}** on {coin_name}! 🔥\n🛒 **DEXScreener:** {buy_link}\n📜 **Contract:** `{contract}`",
    "🐳 **WHALE BUY DETECTED!** 🐳\n\nA massive buy order of **${amount}** just came in for {coin_name}!\n📢 **Official Channel:** {channel}\n🛒 **Buy Now:** {buy_link}"
]

COMMUNITY_TEMPLATES = [
    "☀️ **Good Morning {coin_name} Army!**\nWhat are your price targets for today? Drop them below! 👇🔥",
    "🔥 **GM legends!** Is {coin_name} ready for the next big move? Stay tuned! 🚀",
    "💪 **Community Check!** How strong are the {coin_name} holders today? Let's go! 💎🙌",
    "🌙 **GN to all {coin_name} believers!** Big things are coming tomorrow! ✨"
]

# Free Plan Ad Footer & Buttons
FREE_PLAN_AD_TEXT = "\n\n🤖 *Powered by Django AI — Affordable AI tools for your crypto project!*"

FREE_PLAN_FOOTER_BUTTONS = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📢 Telegram", url="https://t.me/django_ai"),
        InlineKeyboardButton("🐦 Twitter", url="https://x.com/django_ai"),
        InlineKeyboardButton("🌐 Website", url="https://django.ai"),
        InlineKeyboardButton("🤖 Bot", url="https://t.me/django_promoter_bot")
    ]
])

def parse_channel_input(user_input: str) -> str:
    """Helper to sanitize and format channel username, link or ID."""
    clean_input = user_input.strip()
    if clean_input.startswith("https://t.me/"):
        channel_name = clean_input.replace("https://t.me/", "")
        if channel_name.startswith("+") or "joinchat" in channel_name:
            return clean_input
        return f"@{channel_name.split('/')[0]}"
    elif clean_input.startswith("http://t.me/"):
        channel_name = clean_input.replace("http://t.me/", "")
        return f"@{channel_name.split('/')[0]}"
    elif not clean_input.startswith("@") and not clean_input.startswith("-100"):
        return f"@{clean_input}"
    return clean_input

def generate_ai_post(data, include_links=True):
    """Generate high-energy promo post via Gemini AI, optionally with links/contract."""
    try:
        if not GEMINI_API_KEY:
            logging.warning("GEMINI_API_KEY is missing! Using fallback message.")
            return get_fallback_message(data)
            
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        if include_links:
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
        else:
            prompt = f"""
Write a short, high-energy, community-engaging promotional post for a crypto token named {data['coin_name']}.
Project Details / Description: {data.get('coin_desc', 'Top crypto gem on the market')}
Use exciting crypto emojis and bullet points.
DO NOT include any buy links, contract addresses, or URLs.
Keep it concise, hype-driven, interactive, and under 4 lines. Output in English only.
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
    """Generate a post adhering strictly to the user's configured link_ratio percentage."""
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

    # Attach Ad Footer if user is on Free Plan
    if data.get('selected_plan') == 'free':
        post_text += FREE_PLAN_AD_TEXT

    return post_text

# ==========================================
# BOT HANDLERS & WORKFLOW
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: Displays Main Menu for existing users or Plan Selection for new ones."""
    user_id = update.effective_user.id
    channels = get_user_channels(user_id)

    if channels:
        keyboard = [
            [InlineKeyboardButton("➕ Buy / Setup for Another Channel", callback_data='menu_buy_new')],
            [InlineKeyboardButton("⚙️ Edit Settings for Existing Channel", callback_data='menu_edit_existing')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = (
            "🤖 **Welcome back to DJANGO Crypto Auto-Promoter!**\n\n"
            "You have active channel promotional campaigns running.\n"
            "What would you like to do today?"
        )
        if update.message:
            await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
        return MAIN_MENU
    else:
        return await show_subscription_plans(update, context)

async def show_subscription_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_msg = (
        "🤖 **Welcome to DJANGO Crypto Auto-Promoter Bot!**\n\n"
        "Boost your crypto channel & group engagement with AI-generated hype posts, "
        "simulated whale buys, and custom promotional schedules.\n\n"
        "💰 **Select a Plan to Activate:**"
    )
    keyboard = [
        [InlineKeyboardButton("🎁 Free Plan (4 Posts/Day)", callback_data='plan_free')],
        [InlineKeyboardButton("💎 1 Month ($10 TON)", callback_data='plan_1_month')],
        [InlineKeyboardButton("💎 6 Months ($50 TON)", callback_data='plan_6_months')],
        [InlineKeyboardButton("💎 12 Months ($80 TON)", callback_data='plan_12_months')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(welcome_msg, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(welcome_msg, reply_markup=reply_markup, parse_mode='Markdown')
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
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("⚙️ **Select the channel you want to edit:**", reply_markup=reply_markup, parse_mode='Markdown')
        return EDIT_SELECT_CHANNEL

# --- Interactive Edit Menu Options (English) ---
async def show_edit_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    channel = data.get('channel', 'Channel')
    plan = data.get('selected_plan', 'free')
    
    posts_freq = "4 Posts/Day (Fixed)" if plan == 'free' else f"{data.get('msg_per_hour', 2)}/hour"

    text = (
        f"⚙️ **Current Settings for {channel}:**\n\n"
        f"💳 **Plan:** `{plan.upper()}`\n"
        f"1️⃣ **Coin Name:** `{data.get('coin_name', 'Not set')}`\n"
        f"2️⃣ **Description:** `{data.get('coin_desc', 'Not set')}`\n"
        f"3️⃣ **Contract (CA):** `{data.get('contract', 'Not set')}`\n"
        f"4️⃣ **Buy Link:** {data.get('buy_link', 'Not set')}\n"
        f"6️⃣ **Posts Frequency:** `{posts_freq}`\n"
        f"7️⃣ **Posts with Links Ratio:** `{data.get('link_ratio', 100)}%`\n"
        f"8️⃣ **Simulated Buy Alerts:** `{'Enabled' if data.get('enable_new_buy') else 'Disabled'}`\n\n"
        "👇 **Select an option below to update or cancel:**"
    )

    keyboard = [
        [InlineKeyboardButton("🪙 Edit Coin Name", callback_data='opt_coin_name'), InlineKeyboardButton("📝 Edit Description", callback_data='opt_coin_desc')],
        [InlineKeyboardButton("📜 Edit Contract (CA)", callback_data='opt_contract'), InlineKeyboardButton("🛒 Edit Buy Link", callback_data='opt_buy_link')],
        [InlineKeyboardButton("⏱️ Posts Frequency", callback_data='opt_msg_hour'), InlineKeyboardButton("📊 Posts with Links Ratio", callback_data='opt_ratio')],
        [InlineKeyboardButton("🚀 Buy Alerts Toggle", callback_data='opt_new_buy')],
        [InlineKeyboardButton("🔄 Re-configure All Settings Step-by-Step", callback_data='opt_edit_all')],
        [InlineKeyboardButton("❌ Cancel / Stop Subscription", callback_data='opt_cancel_sub')],
        [InlineKeyboardButton("✅ Save & Exit Settings", callback_data='opt_save_finish')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    return EDIT_OPTIONS_MENU

async def select_channel_to_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    raw_channel = query.data.replace('edit_ch_', '')
    user_id = query.from_user.id
    channel_data = get_user_channel_data(user_id, raw_channel)

    if channel_data:
        context.user_data.update(channel_data)
        context.user_data['is_editing'] = True
        return await show_edit_options(update, context)
    else:
        await query.edit_message_text("❌ Channel config not found.", parse_mode='Markdown')
        return ConversationHandler.END

async def edit_options_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'opt_coin_name':
        await query.edit_message_text("1️⃣ Send your new **Token / Coin Name** (e.g., HIPPO):")
        return COIN_NAME
    elif data == 'opt_coin_desc':
        await query.edit_message_text("2️⃣ Send a new **Description / Hype Points** for your token:")
        return COIN_DESC
    elif data == 'opt_contract':
        await query.edit_message_text("3️⃣ Send your new **Token Contract Address (CA)**:")
        return CONTRACT
    elif data == 'opt_buy_link':
        await query.edit_message_text("4️⃣ Send your new **DEXScreener or Buy Link**:")
        return BUY_LINK
    elif data == 'opt_msg_hour':
        if context.user_data.get('selected_plan') == 'free':
            await query.answer("⚠️ Free Plan is restricted to 4 posts/day maximum. Upgrade to Premium to customize frequency!", show_alert=True)
            return EDIT_OPTIONS_MENU
        await query.edit_message_text("6️⃣ How many posts per hour do you want? (Enter a number from 1 to 20):")
        return MSG_PER_HOUR
    elif data == 'opt_ratio':
        keyboard = [
            [InlineKeyboardButton("0%", callback_data='ratio_0'), InlineKeyboardButton("25%", callback_data='ratio_25'), InlineKeyboardButton("50%", callback_data='ratio_50')],
            [InlineKeyboardButton("75%", callback_data='ratio_75'), InlineKeyboardButton("100%", callback_data='ratio_100')]
        ]
        await query.edit_message_text(
            "7️⃣ Select the percentage of posts that should include **Buy Links & Contract Address**:\n"
            "(e.g., 50% = half of the posts will have links, and half will be engagement/community posts without links)", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return LINK_RATIO
    elif data == 'opt_new_buy':
        keyboard = [
            [InlineKeyboardButton("Yes 🚀 (Enable Buy Alerts)", callback_data='newbuy_yes')],
            [InlineKeyboardButton("No 🤖 (AI Posts Only)", callback_data='newbuy_no')]
        ]
        await query.edit_message_text("8️⃣ Would you like to enable **Simulated New Buy Alerts**?", reply_markup=InlineKeyboardMarkup(keyboard))
        return ENABLE_NEW_BUY
    elif data == 'opt_edit_all':
        await query.edit_message_text("1️⃣ Send your new **Token / Coin Name**:")
        return COIN_NAME
    elif data == 'opt_cancel_sub':
        channel = context.user_data.get('channel', '')
        keyboard = [
            [InlineKeyboardButton("YES, Stop Auto-Promoter 🛑", callback_data='confirm_cancel_yes')],
            [InlineKeyboardButton("NO, Keep Publishing 🚀", callback_data='confirm_cancel_no')]
        ]
        await query.edit_message_text(
            f"⚠️ **Are you sure you want to cancel your campaign for {channel}?**\n\n"
            "This will immediately stop all automatic posting in this channel.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return CONFIRM_CANCEL_SUB
    elif data == 'opt_save_finish':
        return await finish_setup(update, context)

async def confirm_cancel_sub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'confirm_cancel_yes':
        user_id = query.from_user.id
        channel = context.user_data.get('channel')

        # 1. Update status in database
        cancel_user_subscription(user_id, channel)

        # 2. Stop active publisher task immediately
        if channel in ACTIVE_PUBLISH_TASKS:
            ACTIVE_PUBLISH_TASKS[channel].cancel()
            del ACTIVE_PUBLISH_TASKS[channel]

        await query.edit_message_text(
            f"🛑 **Subscription Cancelled!**\n\n"
            f"Auto-publishing for channel `{channel}` has been permanently stopped.\n"
            "You can start a new campaign anytime by typing `/start`.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    else:
        return await show_edit_options(update, context)

async def plan_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    plan_key = query.data.replace('plan_', '')
    context.user_data['selected_plan'] = plan_key
    context.user_data['is_editing'] = False

    if plan_key == 'free':
        msg = (
            "🎁 **Free Plan Activated!**\n"
            "• Includes 4 posts per day.\n"
            "• Adds Django AI promo footer & buttons to posts.\n\n"
            "⚙️ **Let's configure your bot settings.**\n\n"
            "1️⃣ Send your **Token / Coin Name** (e.g., HIPPO):"
        )
    else:
        msg = (
            "✅ **Subscription Activated (Test Mode Enabled)!**\n\n"
            "⚙️ **Let's configure your bot settings.**\n\n"
            "1️⃣ Send your **Token / Coin Name** (e.g., HIPPO):"
        )
    
    await query.edit_message_text(msg, parse_mode='Markdown')
    return COIN_NAME

async def get_coin_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['coin_name'] = update.message.text.strip()
    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)
    await update.message.reply_text("2️⃣ Send a brief **Description / Hype Points** for your token (used by AI to write posts):")
    return COIN_DESC

async def get_coin_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['coin_desc'] = update.message.text.strip()
    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)
    await update.message.reply_text("3️⃣ Send your **Token Contract Address (CA)**:")
    return CONTRACT

async def get_contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['contract'] = update.message.text.strip()
    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)
    await update.message.reply_text("4️⃣ Send your **DEXScreener or Buy Link**:")
    return BUY_LINK

async def get_buy_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['buy_link'] = update.message.text.strip()
    
    if context.user_data.get('is_editing') or context.user_data.get('channel'):
        return await show_edit_options(update, context)

    await update.message.reply_text(
        "5️⃣ Send your **Channel Username or Link** (e.g., `@mychannel` or `https://t.me/mychannel`):"
    )
    return CHANNEL

async def get_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_channel = update.message.text.strip()
    formatted_channel = parse_channel_input(raw_channel)
    context.user_data['channel'] = formatted_channel

    msg = (
        f"⚠️ **IMPORTANT STEP: Admin Rights Required!**\n\n"
        f"Please add this bot as an **Administrator** in your channel `{formatted_channel}` with **Post Messages** permission.\n\n"
        "Click the button below once you have promoted the bot!"
    )
    keyboard = [[InlineKeyboardButton("🔗 I have promoted the bot / Continue", callback_data='verify_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    return VERIFY_ADMIN

async def verify_admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    channel_id = context.user_data.get('channel')
    bot_id = context.bot.id

    try:
        member = await context.bot.get_chat_member(chat_id=channel_id, user_id=bot_id)
        if member.status in ['administrator', 'creator']:
            await query.answer("✅ Admin status verified successfully!", show_alert=True)
            
            # If Free plan, automatically enforce 4 posts/day limit and skip manual input
            if context.user_data.get('selected_plan') == 'free':
                context.user_data['msg_per_hour'] = 4  # Represents 4 posts/day in free mode
                
                keyboard = [
                    [
                        InlineKeyboardButton("0%", callback_data='ratio_0'),
                        InlineKeyboardButton("25%", callback_data='ratio_25'),
                        InlineKeyboardButton("50%", callback_data='ratio_50'),
                    ],
                    [
                        InlineKeyboardButton("75%", callback_data='ratio_75'),
                        InlineKeyboardButton("100%", callback_data='ratio_100')
                    ]
                ]
                await query.edit_message_text(
                    "✅ **Admin Status Verified!**\n\n"
                    "ℹ️ *Free Plan automatically set to 4 posts/day (1 post every 6 hours).*\n\n"
                    "7️⃣ What percentage of posts should contain **Buy Links & Contract Address**?",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                return LINK_RATIO

            await query.edit_message_text(
                "✅ **Admin Status Verified!**\n\n"
                "6️⃣ How many posts per hour do you want? (Enter a number from 1 to 20):",
                parse_mode='Markdown'
            )
            return MSG_PER_HOUR
        else:
            await query.answer(
                "❌ Bot is in the chat but not promoted to Admin yet! Please give it Administrator permissions.", 
                show_alert=True
            )
            return VERIFY_ADMIN

    except Exception as e:
        logging.error(f"Admin verification failed for {channel_id}: {e}")
        await query.answer(
            f"❌ Unable to verify! Ensure the bot is added to {channel_id} and promoted to Admin.", 
            show_alert=True
        )
        return VERIFY_ADMIN

async def get_msg_per_hour(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text.strip())
        if count < 1: 
            count = 1
        if count > 20: 
            count = 20
    except ValueError:
        count = 2
    context.user_data['msg_per_hour'] = count

    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)

    keyboard = [
        [
            InlineKeyboardButton("0%", callback_data='ratio_0'),
            InlineKeyboardButton("25%", callback_data='ratio_25'),
            InlineKeyboardButton("50%", callback_data='ratio_50'),
        ],
        [
            InlineKeyboardButton("75%", callback_data='ratio_75'),
            InlineKeyboardButton("100%", callback_data='ratio_100')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "7️⃣ What percentage of posts should contain **Buy Links & Contract Address**?", 
        reply_markup=reply_markup
    )
    return LINK_RATIO

async def get_link_ratio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    ratio = int(query.data.replace('ratio_', ''))
    context.user_data['link_ratio'] = ratio

    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)

    keyboard = [
        [
            InlineKeyboardButton("Yes 🚀 (Include Buy Alerts)", callback_data='newbuy_yes'),
            InlineKeyboardButton("No 🤖 (AI Hype Only)", callback_data='newbuy_no')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "8️⃣ Would you like to enable **Simulated New Buy Alerts** (30% chance per post)?", 
        reply_markup=reply_markup
    )
    return ENABLE_NEW_BUY

async def finish_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if query.data.startswith('newbuy_'):
            context.user_data['enable_new_buy'] = (query.data == 'newbuy_yes')
        user_id = query.from_user.id
        username = query.from_user.username or ""
    else:
        user_id = update.effective_user.id
        username = update.effective_user.username or ""
    
    coin = context.user_data.get('coin_name')
    channel = context.user_data.get('channel')

    # Save configuration to Supabase Database
    save_user_data(user_id, username, context.user_data)
    logging.info(f"User {user_id} channel {channel} saved to Supabase database.")
    
    msg = (
        f"🎉 **Setup / Update Complete for {coin} ({channel})!**\n\n"
        "⚡ Your auto-publisher is now active and live!\n"
        "You can modify these settings or add another channel anytime using `/start`."
    )
    
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg, parse_mode='Markdown')
    
    # Cancel any existing task for this channel before restarting
    if channel in ACTIVE_PUBLISH_TASKS:
        ACTIVE_PUBLISH_TASKS[channel].cancel()

    # Start new auto-publishing loop as background task
    task = asyncio.create_task(start_publishing(context.application, context.user_data.copy()))
    ACTIVE_PUBLISH_TASKS[channel] = task

    context.user_data['is_editing'] = False
    return ConversationHandler.END

async def start_publishing(app, data):
    plan = data.get('selected_plan', 'free')
    
    if plan == 'free':
        # 4 posts per day = 1 post every 6 hours (21,600 seconds)
        delay_seconds = 21600
    else:
        msg_per_hour = data.get('msg_per_hour', 2)
        delay_seconds = int((60 / msg_per_hour) * 60)
        
    channel_id = str(data.get('channel', '')).strip()
    
    logging.info(f"Starting auto-publisher for {channel_id} [{plan.upper()}] with interval {delay_seconds}s")

    while True:
        try:
            post_text = generate_post(data)

            # Free Plan includes ad footer + 4 inline buttons
            reply_markup = FREE_PLAN_FOOTER_BUTTONS if plan == 'free' else None

            try:
                await app.bot.send_message(
                    chat_id=channel_id, 
                    text=post_text, 
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            except Exception as parse_err:
                logging.warning(f"Markdown parse error, sending plain text: {parse_err}")
                await app.bot.send_message(
                    chat_id=channel_id, 
                    text=post_text,
                    reply_markup=reply_markup
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
    context.user_data['is_editing'] = False
    await update.message.reply_text("Setup canceled. Send /start to open the main menu.")
    return ConversationHandler.END

# Restore background publisher tasks for all users from DB upon bot startup
async def restore_active_tasks(app):
    users = get_active_users()
    logging.info(f"Restoring {len(users)} active auto-publisher tasks from Supabase database...")
    for user_data in users:
        channel = user_data.get('channel')
        if channel in ACTIVE_PUBLISH_TASKS:
            ACTIVE_PUBLISH_TASKS[channel].cancel()
        task = asyncio.create_task(start_publishing(app, user_data))
        ACTIVE_PUBLISH_TASKS[channel] = task

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [CallbackQueryHandler(main_menu_handler, pattern='^menu_')],
            EDIT_SELECT_CHANNEL: [CallbackQueryHandler(select_channel_to_edit, pattern='^edit_ch_')],
            EDIT_OPTIONS_MENU: [CallbackQueryHandler(edit_options_handler, pattern='^opt_')],
            CONFIRM_CANCEL_SUB: [CallbackQueryHandler(confirm_cancel_sub_handler, pattern='^confirm_cancel_')],
            PLAN_SELECT: [CallbackQueryHandler(plan_selected, pattern='^plan_')],
            COIN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_coin_name)],
            COIN_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_coin_desc)],
            CONTRACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_contract)],
            BUY_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_buy_link)],
            CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_channel)],
            VERIFY_ADMIN: [CallbackQueryHandler(verify_admin_status, pattern='^verify_admin$')],
            MSG_PER_HOUR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_msg_per_hour)],
            LINK_RATIO: [CallbackQueryHandler(get_link_ratio, pattern='^ratio_')],
            ENABLE_NEW_BUY: [
                CallbackQueryHandler(finish_setup, pattern='^newbuy_'),
                CallbackQueryHandler(edit_options_handler, pattern='^opt_')
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(conv_handler)
    
    # Restore saved campaigns on startup
    loop = asyncio.get_event_loop()
    loop.create_task(restore_active_tasks(app))

    print("DJANGO Bot running with Free Plan and Subscription Cancel feature...")
    app.run_polling(drop_pending_updates=True, stop_signals=None)

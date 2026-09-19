import os
import random
import asyncio
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

from config import TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, TON_WALLET_ADDRESS, PRICES
from fallback_db import get_fallback_message

# Enable Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Lightweight Web Server for Render Health Check (24/7 Live)
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"DJANGO Crypto Bot Server is Running 24/7!")

def run_health_check_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_check_server, daemon=True).start()

# Configure Gemini AI
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Conversation States
PLAN_SELECT, COIN_NAME, COIN_DESC, CONTRACT, BUY_LINK, CHANNEL, MSG_PER_HOUR, ENABLE_NEW_BUY = range(8)

BUY_TEMPLATES = [
    "🚀 **NEW BUY DETECTED!** 🚀\n\n💎 **Token:** {coin_name}\n💰 **Amount:** ${amount}\n🛒 **Buy Here:** {buy_link}\n📜 **Contract:** `{contract}`\n\n🔥 Whales are accumulating!",
    "📈 **GREEN CANDLE ALERT!** 📈\n\nNew buy order executed: **${amount}** on {coin_name}! 🔥\n🛒 **DEXScreener:** {buy_link}",
    "🐳 **WHALE BUY DETECTED!** 🐳\n\nA massive buy order of **${amount}** just came in for {coin_name}!\n📢 **Official Channel:** {channel}"
]

def generate_ai_post(data):
    try:
        if not GEMINI_API_KEY:
            return get_fallback_message(data)
            
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
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logging.warning(f"Gemini API error, falling back to database: {e}")
        return get_fallback_message(data)

def generate_post(data):
    enable_buy = data.get('enable_new_buy', False)
    if enable_buy and random.random() < 0.30:
        amount = random.randint(50, 1500)
        template = random.choice(BUY_TEMPLATES)
        return template.format(
            coin_name=data['coin_name'],
            buy_link=data['buy_link'],
            contract=data['contract'],
            channel=data['channel'],
            amount=amount
        )
    else:
        return generate_ai_post(data)

# Step 1: Start Command & Subscription Plans
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_msg = (
        "🤖 **Welcome to DJANGO Crypto Auto-Promoter Bot!**\n\n"
        "Boost your crypto channel & group engagement with AI-generated hype posts, "
        "simulated whale buys, and custom promotional schedules.\n\n"
        "💰 **Select a Subscription Plan to Activate:**"
    )
    keyboard = [
        [InlineKeyboardButton("💎 1 Month ($10 TON)", callback_data='plan_1_month')],
        [InlineKeyboardButton("💎 6 Months ($50 TON)", callback_data='plan_6_months')],
        [InlineKeyboardButton("💎 12 Months ($80 TON)", callback_data='plan_12_months')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_msg, reply_markup=reply_markup, parse_mode='Markdown')
    return PLAN_SELECT

# Step 2: Bypass Payment & Start Setup Instantly (English Version)
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
    await update.message.reply_text("5️⃣ Send your **Channel Username or Link** (e.g., @mychannel or -100xxxxxxxx):")
    return CHANNEL

async def get_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['channel'] = update.message.text
    await update.message.reply_text("6️⃣ How many posts per hour do you want? (Enter a number from 1 to 4):")
    return MSG_PER_HOUR

async def get_msg_per_hour(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text)
        if count < 1: count = 1
        if count > 4: count = 4
    except ValueError:
        count = 2
    context.user_data['msg_per_hour'] = count

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
    
    msg = (
        f"🎉 **Setup Complete for {coin}!**\n\n"
        "⚡ Your auto-publisher is now active!\n"
        "Make sure to add this bot as an **Administrator** in your channel so it can publish posts."
    )
    await query.edit_message_text(msg, parse_mode='Markdown')
    
    # Start auto-publishing loop
    asyncio.create_task(start_publishing(context.application, context.user_data.copy()))
    return ConversationHandler.END

async def start_publishing(app, data):
    msg_per_hour = data.get('msg_per_hour', 2)
    delay_seconds = (60 / msg_per_hour) * 60 
    channel_id = data.get('channel')
    
    while True:
        post_text = generate_post(data)
        try:
            await app.bot.send_message(chat_id=channel_id, text=post_text, parse_mode='Markdown')
            logging.info(f"Post successfully sent for {data['coin_name']} to {channel_id}")
        except Exception as e:
            logging.error(f"Failed to send post to channel {channel_id}: {e}")
            
        await asyncio.sleep(delay_seconds)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Setup canceled. Send /start to begin again.")
    return ConversationHandler.END

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
    print("DJANGO Bot is running...")
    app.run_polling()

# main.py
import random
import asyncio
import logging
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

from config import TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, MY_WALLET
from fallback_db import get_fallback_message

# Enable Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Configure Gemini AI
genai.configure(api_key=GEMINI_API_KEY)

COIN_NAME, BUY_LINK, CONTRACT, CHANNEL, MSG_PER_HOUR, ENABLE_NEW_BUY, PAYMENT = range(7)

BUY_TEMPLATES = [
    "?? **NEW BUY DETECTED!** ??\n\n?? **Token:** {coin_name}\n?? **Amount:** ${amount}\n?? **Buy Here:** {buy_link}\n?? **Contract:** `{contract}`\n\n?? Whales are accumulating!",
    "?? **GREEN CANDLE ALERT!** ??\n\nNew buy order executed: **${amount}** on {coin_name}! ??\n?? **Buy Link:** {buy_link}",
    "?? **WHALE BUY DETECTED!** ??\n\nA massive buy order of **${amount}** just came in for {coin_name}!\n?? **Join Community:** {channel}"
]

def generate_ai_post(data):
    try:
        prompt = f"""
        Write a short, high-energy, hyped promotional post for a crypto token named {data['coin_name']}.
        Use exciting crypto emojis and bullet points.
        Include these exact details:
        Buy Link: {data['buy_link']}
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
        amount = random.randint(50, 1000)
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_msg = (
        "Welcome to **DJANGO Crypto Auto-Publisher Bot** ??\n\n"
        "Let's set up your channel promotion in a few steps.\n\n"
        "Please enter your **Token / Coin Name** (e.g., HIPPO):"
    )
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')
    return COIN_NAME

async def get_coin_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['coin_name'] = update.message.text
    await update.message.reply_text("Great! Now send your **Direct Buy Link** (e.g., DEX/CEX URL):")
    return BUY_LINK

async def get_buy_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['buy_link'] = update.message.text
    await update.message.reply_text("Send your **Token Contract Address (CA)**:")
    return CONTRACT

async def get_contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['contract'] = update.message.text
    await update.message.reply_text("Send your **Channel Username or Link** (e.g., @mychannel):")
    return CHANNEL

async def get_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['channel'] = update.message.text
    await update.message.reply_text("How many posts per hour do you want? (Enter a number from 1 to 4):")
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
            InlineKeyboardButton("Yes ? (30% Buy Alerts)", callback_data='newbuy_yes'),
            InlineKeyboardButton("No ? (AI Hype Only)", callback_data='newbuy_no')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Would you like to enable **Simulated New Buy Alerts**?", reply_markup=reply_markup)
    return ENABLE_NEW_BUY

async def get_new_buy_option(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['enable_new_buy'] = (query.data == 'newbuy_yes')

    keyboard = [
        [InlineKeyboardButton("??? 1 Month ($10 USDT)", callback_data='10')],
        [InlineKeyboardButton("??? 6 Months ($50 USDT)", callback_data='50')],
        [InlineKeyboardButton("??? 1 Year ($80 USDT)", callback_data='80')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("? Setup completed!\n\nSelect your subscription plan to activate the service:", reply_markup=reply_markup)
    return PAYMENT

async def process_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    amount = query.data
    coin = context.user_data.get('coin_name')
    
    msg = (
        f"?? **Order Confirmation for {coin}:**\n\n"
        f"?? Please transfer **{amount} USDT** to the following deposit address:\n\n"
        f"`{MY_WALLET}`\n\n"
        "? Once paid, your bot will automatically start publishing AI posts to your channel!"
    )
    await query.edit_message_text(text=msg, parse_mode='Markdown')
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
            logging.info(f"Post sent for {data['coin_name']} to {channel_id}")
        except Exception as e:
            logging.error(f"Failed to send post: {e}")
            
        await asyncio.sleep(delay_seconds)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Setup canceled.")
    return ConversationHandler.END

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            COIN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_coin_name)],
            BUY_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_buy_link)],
            CONTRACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_contract)],
            CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_channel)],
            MSG_PER_HOUR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_msg_per_hour)],
            ENABLE_NEW_BUY: [CallbackQueryHandler(get_new_buy_option)],
            PAYMENT: [CallbackQueryHandler(process_payment)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(conv_handler)
    print("DJANGO Bot is running...")
    app.run_polling()
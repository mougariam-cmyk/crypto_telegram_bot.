import os
import random
import asyncio
import logging
import threading
import re
from http.server import HTTPServer, BaseHTTPRequestHandler

from google import genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

from fallback_db import get_fallback_message

from database import (
    init_db,
    save_user_data,
    cancel_user_subscription,
    get_user_channels,
    get_user_channel_data,
    get_active_users
)

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
# DATABASE SYSTEM
# ==========================================
# Database functions are centralized in database.py.
# Initialize the schema once when the bot starts.
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

# Recent AI posts kept in memory to help Gemini avoid repeating the same
# wording, question, hook, or structure during the current bot session.
RECENT_AI_POSTS = {}
MAX_RECENT_AI_POSTS = 12

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

        channel_key = data.get('channel', 'default')
        recent_posts = RECENT_AI_POSTS.get(channel_key, [])
        recent_text = "\n---\n".join(recent_posts[-MAX_RECENT_AI_POSTS:])

        content_angles = [
            "ask the community a direct question",
            "create a prediction challenge",
            "ask holders to choose between two options",
            "start a short debate or controversial-but-fair discussion",
            "address OG holders specifically",
            "welcome or challenge new holders",
            "create a mini game or quick challenge",
            "ask members about their personal target or goal",
            "use a curiosity-driven hook",
            "tell a very short project-related story",
            "ask members to react with an emoji and explain why",
            "create a myth-vs-reality style discussion",
            "ask a hypothetical what-if question",
            "make a short holder roll-call post",
            "create a concise community rally post",
            "ask members what they want to see next from the project",
            "make a one-line punchy engagement post"
        ]

        angle = random.choice(content_angles)

        if include_links:
            prompt = f"""
You are the creative social-media content engine for a crypto community.

Create ONE completely fresh promotional/community post for:
Token: {data['coin_name']}
Project description: {data.get('coin_desc', 'Crypto community project')}

This post MUST be substantially different from typical generic crypto promotion.
Chosen content angle: {angle}

The goal is genuine community interaction: make members want to reply, discuss,
vote with their opinion, or react. You may ask a strong question or create a
challenge, but do not make false factual claims, fake partnerships, fake
transactions, fake whale activity, guaranteed profits, or guaranteed price
predictions.

You may naturally include these exact project details when relevant:
Buy Link: {data['buy_link']}
Contract Address: {data['contract']}
Telegram Channel: {data['channel']}

Rules:
- English only.
- Use natural crypto-community language and emojis.
- Vary the structure, opening, sentence length, and CTA.
- Do NOT always start with an emoji, the token name, "GM", "BREAKING", or "🚀".
- Do NOT always ask about price.
- Do NOT copy or closely imitate the previous posts below.
- Keep it concise, normally 2-6 short lines.
- Do not use markdown asterisk stars.
- Output ONLY the final post.

Previous posts to avoid repeating:
{recent_text if recent_text else "No previous posts available."}
"""
        else:
            prompt = f"""
You are the creative social-media content engine for a crypto community.

Create ONE completely fresh community-engagement post for:
Token: {data['coin_name']}
Project description: {data.get('coin_desc', 'Crypto community project')}

Chosen content angle: {angle}

The goal is genuine interaction. Encourage members to reply, debate, predict,
choose, react, or share an opinion. Do not make false factual claims, fake
partnerships, fake transactions, fake whale activity, guaranteed profits, or
guaranteed price predictions.

Rules:
- English only.
- Use natural crypto-community language and emojis.
- Vary the structure, opening, sentence length, and CTA.
- Do NOT always start with an emoji, the token name, "GM", "BREAKING", or "🚀".
- Do NOT always ask about price.
- Do NOT include any buy links, contract addresses, or URLs.
- Do NOT copy or closely imitate the previous posts below.
- Keep it concise, normally 2-6 short lines.
- Do not use markdown asterisk stars.
- Output ONLY the final post.

Previous posts to avoid repeating:
{recent_text if recent_text else "No previous posts available."}
"""

        response = client.models.generate_content(
            model='gemini-3.6-flash',
            contents=prompt,
        )

        if response and response.text:
            post = response.text.strip()

            RECENT_AI_POSTS.setdefault(channel_key, []).append(post)
            RECENT_AI_POSTS[channel_key] = RECENT_AI_POSTS[channel_key][-MAX_RECENT_AI_POSTS:]

            return post
        else:
            return get_fallback_message(data)

    except Exception as e:
        logging.error(f"Gemini API Error with rotation: {e}")
        return get_fallback_message(data)


def generate_post(data):
    link_ratio = data.get('link_ratio', 100) / 100.0
    should_include_links = (random.random() < link_ratio)

    if not should_include_links:
        # Keep the existing link-ratio logic, but replace the old fixed
        # community templates with genuinely varied AI-generated posts.
        post_text = generate_ai_post(data, include_links=False)
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

# ==========================================
# GROUP COMMUNITY AI INTERACTION
# ==========================================
# These handlers work only for configured/active groups or channels. They do not
# change the existing auto-publishing system.
BOT_USERNAME = None


def _normalize_link(value):
    if not value:
        return ""
    value = value.strip().lower()
    value = value.rstrip(".,);]}>\"")
    if value.startswith("www."):
        value = "https://" + value
    if value.startswith("t.me/"):
        value = "https://" + value
    return value.rstrip("/")


def get_project_data_for_chat(chat):
    """Find the active project configuration belonging to this Telegram chat."""
    try:
        chat_id = str(chat.id)
        chat_username = (chat.username or "").lower()
        chat_username = chat_username.lstrip("@")

        for data in get_active_users():
            configured = str(data.get("channel", "")).strip()
            configured_lower = configured.lower()
            configured_username = configured_lower.lstrip("@")

            if configured == chat_id:
                return data
            if chat_username and configured_username == chat_username:
                return data

        return None
    except Exception as e:
        logging.error(f"Could not resolve project data for chat {chat.id}: {e}")
        return None


def extract_message_links(message):
    """Collect visible and Telegram text-link URLs from a message."""
    text_parts = []
    if message.text:
        text_parts.append(message.text)
    if message.caption:
        text_parts.append(message.caption)

    links = re.findall(
        r"(?:https?://|www\.|t\.me/)[^\s<>()]+",
        "\n".join(text_parts),
        flags=re.IGNORECASE,
    )

    for entity in (message.entities or []) + (message.caption_entities or []):
        if getattr(entity, "url", None):
            links.append(entity.url)

    return [_normalize_link(link) for link in links if link]


def is_approved_project_link(link, data):
    normalized = _normalize_link(link)
    approved = {
        _normalize_link(data.get("buy_link", "")),
        _normalize_link(data.get("channel", "")),
    }

    # Accept the Telegram channel in both @username and https://t.me/username forms.
    channel = str(data.get("channel", "")).strip()
    if channel.startswith("@"):
        approved.add(_normalize_link("https://t.me/" + channel[1:]))

    if normalized in approved:
        return True

    # A link containing the exact approved URL as its prefix is also accepted.
    return any(a and normalized.startswith(a) for a in approved)


async def generate_group_ai_reply(data, member_name, member_message):
    """Generate a real Gemini reply when a member directly mentions the bot.

    Retry across the configured Gemini keys so one exhausted/invalid key does
    not silently turn every community reply into the generic fallback message.
    """
    prompt = f"""
You are the AI community assistant inside a Telegram crypto community.

Project token: {data.get('coin_name', 'the project')}
Project description: {data.get('coin_desc', 'Crypto community project')}
Official buy link: {data.get('buy_link', '')}
Official contract address: {data.get('contract', '')}
Official Telegram: {data.get('channel', '')}

A real member named {member_name} directly tagged you and wrote this message:
---
{member_message}
---

Your job is to reply to THAT EXACT MESSAGE.

Important:
- First understand what the member is actually asking or saying.
- Answer the question directly if there is a question.
- If they make a statement, respond naturally to the statement instead of giving a generic greeting.
- If they ask about the project, use only the project information supplied above.
- If they ask something unrelated to the project, you may answer briefly and naturally.
- Never invent project facts, partnerships, listings, transactions, announcements, or community statistics.
- Never promise profits or guaranteed price increases.
- If asked for a guaranteed future price, explain briefly that it cannot be guaranteed.
- Sound like an active, friendly Telegram community assistant, not a customer-support script.
- Do not begin with "Hey {member_name}! I'm here" unless the member actually greeted you.
- Do not say "Ask me anything" unless the member actually asks what you can do.
- English only.
- Normally 1-5 short lines.
- Use emojis naturally, but do not force them into every reply.
- Do not use markdown asterisk stars.
- Output ONLY the reply text.
"""

    attempts = max(1, len(GEMINI_API_KEYS))
    last_error = None

    for attempt in range(attempts):
        try:
            client = get_next_gemini_client()
            if not client:
                break

            logging.info(
                f"Generating AI group reply for @{member_name}: {member_message[:120]}"
            )

            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt,
            )

            if response and response.text and response.text.strip():
                reply = response.text.strip()
                logging.info(f"AI group reply generated successfully: {reply[:150]}")
                return reply

            last_error = "Gemini returned an empty response"
            logging.warning(last_error)

        except Exception as e:
            last_error = e
            logging.error(
                f"Gemini group reply attempt {attempt + 1}/{attempts} failed: {e}"
            )

    if last_error:
        logging.error(f"All Gemini group reply attempts failed: {last_error}")

    # Only use this when Gemini is genuinely unavailable.
    return f"Hey {member_name}! 👋 I'm here. Ask me anything about {data.get('coin_name', 'the project')}."


async def is_other_coin_mentioned(data, message_text):
    """Use a lightweight heuristic first, then Gemini only for suspicious crypto text."""
    if not message_text:
        return False

    own_coin = str(data.get("coin_name", "")).strip().lower()
    text_lower = message_text.lower()

    # Remove the project's own token name before checking for another token.
    if own_coin:
        text_lower = re.sub(re.escape(own_coin), " ", text_lower, flags=re.IGNORECASE)

    crypto_signal = re.search(
        r"(?:\$[a-z][a-z0-9_]{1,15}\b|\b(?:coin|token|crypto|contract|ca|presale|airdrop|dex|buy|sell|swap|launch)\b|\b(?:bitcoin|btc|ethereum|eth|solana|sol|dogecoin|doge|shiba|shib|pepe|bonk|floki|xrp|cardano|ada|bnb|avalanche|avax)\b)",
        text_lower,
        flags=re.IGNORECASE,
    )
    if not crypto_signal:
        return False

    # Obvious ticker-style mentions that are not the configured token.
    ticker_matches = re.findall(r"\$([a-z][a-z0-9_]{1,15})\b", text_lower, flags=re.IGNORECASE)
    if ticker_matches and own_coin:
        own_clean = re.sub(r"[^a-z0-9]", "", own_coin)
        for ticker in ticker_matches:
            if ticker.lower() != own_clean:
                return True

    try:
        client = get_next_gemini_client()
        if not client:
            return False

        prompt = f"""
Classify this Telegram group message for moderation.

Official project token: {data.get('coin_name', '')}
Message: {message_text}

Return exactly one word:
OTHER_COIN if the member is promoting, recommending, asking people to buy,
sharing the contract/CA of, or materially discussing a cryptocurrency/token
other than the official project.
SAFE if it is not about another cryptocurrency/token.
Do not classify a general crypto word like 'coin' by itself as OTHER_COIN.
"""
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
        result = (response.text or "").strip().upper() if response else ""
        return result.startswith("OTHER_COIN")
    except Exception as e:
        logging.error(f"Gemini moderation error: {e}")
        return False


async def group_message_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle welcomes, bot mentions, external links and other-token promotion."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user:
        return
    if chat.type not in ("group", "supergroup"):
        return
    if user.id == context.bot.id or user.is_bot:
        return

    data = get_project_data_for_chat(chat)
    if not data:
        return

    # 1) Welcome newly joined members.
    if message.new_chat_members:
        for member in message.new_chat_members:
            if member.id == context.bot.id or member.is_bot:
                continue
            display_name = member.first_name or member.username or "there"
            await message.reply_text(
                f"👋 Welcome, {display_name}!\n\n"
                f"You're now part of the {data.get('coin_name', 'project')} community. 💎\n"
                "Keep the group focused on the official project and have fun! 🚀"
            )
        return

    message_text = message.text or message.caption or ""

    # 2) If the member tags the bot, Gemini answers the actual message.
    global BOT_USERNAME
    if BOT_USERNAME is None:
        try:
            me = await context.bot.get_me()
            BOT_USERNAME = (me.username or "").lower()
        except Exception:
            BOT_USERNAME = ""

    mentioned = bool(BOT_USERNAME and re.search(r"@" + re.escape(BOT_USERNAME) + r"\b", message_text, re.IGNORECASE))
    replied_to_bot = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == context.bot.id
    )

    if mentioned or replied_to_bot:
        member_name = user.first_name or user.username or "there"
        clean_message = re.sub(
            r"@" + re.escape(BOT_USERNAME) + r"\b",
            "",
            message_text,
            flags=re.IGNORECASE,
        ).strip()
        if not clean_message:
            clean_message = "Hello!"

        reply = await generate_group_ai_reply(data, member_name, clean_message)
        await message.reply_text(reply)
        return

    # 3) Reject links that are not the configured project's approved links.
    links = extract_message_links(message)
    bad_links = [link for link in links if not is_approved_project_link(link, data)]
    if bad_links:
        await message.reply_text(
            f"⚠️ {user.first_name or 'Please'}, this group is dedicated to {data.get('coin_name', 'the official project')}.\n"
            "Please do not post external links or promotions. Only the project's official links are allowed here."
        )
        return

    # 4) Detect promotion/discussion of another cryptocurrency when no bad URL exists.
    if await is_other_coin_mentioned(data, message_text):
        await message.reply_text(
            f"⚠️ {user.first_name or 'Please'}, please keep this group focused on {data.get('coin_name', 'the official project')}.\n"
            "Promotion or discussion of other tokens is not allowed here."
        )


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
        f"Please add this bot as an Administrator in your channel `{formatted_channel}` with Post Messages permission.\n\n"
        "📋 Instructions:\n"
        "1. Copy the bot username below using the button.\n"
        "2. Go to your channel settings -> Administrators -> Add Admin.\n"
        "3. Paste the username in the search bar, select the bot, and grant posting permission.\n\n"
        "Click the button below once you have promoted the bot!"
    )
    keyboard = [
        [InlineKeyboardButton("📋 Copy Bot Username (@DJANGO_CRYPTO_BOT)", switch_inline_query_current_chat="@DJANGO_CRYPTO_BOT")],
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
            f"Please add this bot as an Administrator in your channel `@DJANGO_CRYPTO_BOT`.\n\n"
            "📋 Instructions:\n"
            "1. Copy the bot username below using the button.\n"
            "2. Go to your channel settings -> Administrators -> Add Admin.\n"
            "3. Paste the username in the search bar, select the bot, and grant posting permission.\n\n"
            "Click the button below once you have promoted the bot!"
        )
        keyboard = [
            [InlineKeyboardButton("📋 Copy Bot Username (@DJANGO_CRYPTO_BOT)", switch_inline_query_current_chat="@DJANGO_CRYPTO_BOT")],
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
        [InlineKeyboardButton("Yes 🚀 (Enable Buy Alerts)", callback_data='newbuy_yes')],
        [InlineKeyboardButton("No 🤖 (AI Posts Only)", callback_data='newbuy_no')],
        [InlineKeyboardButton("🔙 Back", callback_data='back_to_link_ratio')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("8️⃣ Would you like to enable Simulated New Buy Alerts?", reply_markup=reply_markup)
    return ENABLE_NEW_BUY

async def get_enable_new_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'back_to_link_ratio':
        keyboard = [
            [InlineKeyboardButton("0%", callback_data='ratio_0'), InlineKeyboardButton("25%", callback_data='ratio_25'), InlineKeyboardButton("50%", callback_data='ratio_50')],
            [InlineKeyboardButton("75%", callback_data='ratio_75'), InlineKeyboardButton("100%", callback_data='ratio_100')]
        ]
        await query.edit_message_text("7️⃣ What percentage of posts should contain Buy Links & Contract?", reply_markup=InlineKeyboardMarkup(keyboard))
        return LINK_RATIO

    enable_buy = (query.data == 'newbuy_yes')
    context.user_data['enable_new_buy'] = enable_buy

    user_id = query.from_user.id
    username = query.from_user.username or "Unknown"

    save_user_data(user_id, username, context.user_data)
    restart_all_active_tasks(context.application)

    channel = context.user_data.get('channel')
    plan = context.user_data.get('selected_plan', 'free')

    success_text = (
        f"🎉 SUCCESS! Auto-Promoter is now active for {channel}!\n\n"
        f"🪙 Token: {context.user_data.get('coin_name')}\n"
        f"💳 Plan: {plan.upper()}\n"
        f"⏱️ Frequency: {context.user_data.get('msg_per_hour', 2)} posts/hour\n"
        f"📊 Links Ratio: {context.user_data.get('link_ratio', 100)}%\n"
        f"🚀 Buy Alerts: {'Enabled' if enable_buy else 'Disabled'}\n\n"
        f"Your automated crypto growth campaign has started! 🚀"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Edit Settings / Manage", callback_data='menu_edit_existing')],
        [InlineKeyboardButton("🏠 Main Menu", callback_data='back_to_main')]
    ])

    await query.edit_message_text(success_text, reply_markup=keyboard)
    context.user_data.clear()
    return ConversationHandler.END

async def finish_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    username = query.from_user.username or "Unknown"
    
    save_user_data(user_id, username, context.user_data)
    restart_all_active_tasks(context.application)
    
    await query.edit_message_text("✅ Settings updated and saved successfully! 🚀", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data='back_to_main')]
    ]))
    context.user_data.clear()
    return ConversationHandler.END

async def background_publisher(application, user_data):
    channel = user_data['channel']
    
    while True:
        try:
            current_data = get_user_channel_data(user_data['user_id'], channel)
            if not current_data or current_data.get('subscription_status') == 'cancelled':
                break
                
            post_text = generate_post(current_data)
            
            if current_data.get('selected_plan') == 'free':
                await application.bot.send_message(
                    chat_id=channel,
                    text=post_text,
                    reply_markup=FREE_PLAN_FOOTER_BUTTONS,
                    parse_mode='Markdown'
                )
                await asyncio.sleep(6 * 3600)
            else:
                await application.bot.send_message(
                    chat_id=channel,
                    text=post_text,
                    parse_mode='Markdown'
                )
                msg_per_hour = current_data.get('msg_per_hour', 2)
                sleep_seconds = 3600 / max(1, msg_per_hour)
                await asyncio.sleep(sleep_seconds)
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"Publishing error for {channel}: {e}")
            await asyncio.sleep(60)

def restart_all_active_tasks(application):
    global ACTIVE_PUBLISH_TASKS
    for task in ACTIVE_PUBLISH_TASKS.values():
        task.cancel()
    ACTIVE_PUBLISH_TASKS.clear()
    
    active_users = get_active_users()
    for u_data in active_users:
        channel = u_data['channel']
        if channel not in ACTIVE_PUBLISH_TASKS:
            task = asyncio.create_task(background_publisher(application, u_data))
            ACTIVE_PUBLISH_TASKS[channel] = task

async def post_init(application):
    restart_all_active_tasks(application)
    logging.info("Bot initialized and background publishers started.")

# ==========================================
# MAIN FUNCTION & BOT STARTUP
# ==========================================
if __name__ == '__main__':
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logging.error("No TELEGRAM_BOT_TOKEN found in environment variables!")
        exit(1)
        
    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CallbackQueryHandler(start, pattern='^back_to_main$')
        ],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_handler, pattern='^(menu_buy_new|menu_edit_existing)$'),
                CallbackQueryHandler(start, pattern='^back_to_main$')
            ],
            PLAN_SELECT: [
                CallbackQueryHandler(plan_selected, pattern='^plan_'),
                CallbackQueryHandler(start, pattern='^back_to_main$')
            ],
            COIN_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_coin_name),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$'),
                CallbackQueryHandler(plan_selected, pattern='^back_to_coin_name$')
            ],
            COIN_DESC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_coin_desc),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$'),
                CallbackQueryHandler(get_coin_name, pattern='^back_to_coin_desc$')
            ],
            CONTRACT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_contract),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$'),
                CallbackQueryHandler(get_coin_desc, pattern='^back_to_contract$')
            ],
            BUY_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_buy_link),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$'),
                CallbackQueryHandler(get_contract, pattern='^back_to_buy_link$')
            ],
            CHANNEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_channel),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$'),
                CallbackQueryHandler(get_buy_link, pattern='^back_to_channel_input$')
            ],
            VERIFY_ADMIN: [
                CallbackQueryHandler(verify_admin_status, pattern='^(verify_admin|back_to_channel_input)$')
            ],
            EDIT_SELECT_CHANNEL: [
                CallbackQueryHandler(select_channel_to_edit, pattern='^edit_ch_'),
                CallbackQueryHandler(start, pattern='^back_to_main$')
            ],
            EDIT_OPTIONS_MENU: [
                CallbackQueryHandler(edit_options_handler, pattern='^(opt_|back_to_main)'),
                CallbackQueryHandler(start, pattern='^back_to_main$')
            ],
            MSG_PER_HOUR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_msg_per_hour),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$'),
                CallbackQueryHandler(verify_admin_status, pattern='^back_to_verify_admin$')
            ],
            LINK_RATIO: [
                CallbackQueryHandler(get_link_ratio, pattern='^(ratio_|back_to_verify_admin)'),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$')
            ],
            ENABLE_NEW_BUY: [
                CallbackQueryHandler(get_enable_new_buy, pattern='^(newbuy_|back_to_link_ratio)'),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$')
            ],
            CONFIRM_CANCEL_SUB: [
                CallbackQueryHandler(confirm_cancel_sub_handler, pattern='^(confirm_cancel_|back_to_edit_menu)'),
                CallbackQueryHandler(show_edit_options, pattern='^confirm_cancel_no$')
            ]
        },
        fallbacks=[CommandHandler('cancel', lambda u, c: c.application.create_task(start(u, c)))],
        per_user=True
    )

    application.add_handler(conv_handler)

    # Community interaction handlers: these do not alter the existing setup flow.
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, group_message_guard),
        group=1,
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, group_message_guard),
        group=1,
    )
    
    logging.info("Starting bot polling...")
    application.run_polling()

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
    get_free_daily_post_count,
    record_free_daily_post,
    get_active_users,
    replace_content_pool,
    get_content_pool_stats,
    get_content_pool_posts,
    get_user_start_state,
    save_onboarding_profile,
    get_user_active_plan_keys,
    init_official_content_tables,
    get_official_daily_content,
    save_official_daily_content,
    mark_official_publication,
    get_official_published_content_ids,
    save_official_reply,
    get_expired_subscriptions,
    transition_expired_subscription,
    mark_free_transition_announced,
    get_subscription_snapshot
)

# ==========================================
# SEPARATED GEMINI API KEYS
# ==========================================
# POST KEYS 1-2: central content generation only.
# GROUP KEYS 3-10: member answers/moderation only.
# Keep these keys in separate API projects when quota separation is needed.
GEMINI_POST_API_KEYS = [
    os.getenv("GEMINI_API_KEY_1", "").strip(),
    os.getenv("GEMINI_API_KEY_2", "").strip(),
]
GEMINI_POST_API_KEYS = [k for k in GEMINI_POST_API_KEYS if k]

GEMINI_GROUP_API_KEYS = [
    os.getenv("GEMINI_API_KEY_3", "").strip(),
    os.getenv("GEMINI_API_KEY_4", "").strip(),
    os.getenv("GEMINI_API_KEY_5", "").strip(),
    os.getenv("GEMINI_API_KEY_6", "").strip(),
    os.getenv("GEMINI_API_KEY_7", "").strip(),
    os.getenv("GEMINI_API_KEY_8", "").strip(),
    os.getenv("GEMINI_API_KEY_9", "").strip(),
    os.getenv("GEMINI_API_KEY_10", "").strip(),
]
GEMINI_GROUP_API_KEYS = [k for k in GEMINI_GROUP_API_KEYS if k]

# KEY 21: MARSOF AI official marketing/content engine only.
GEMINI_OFFICIAL_KEY = os.getenv("GEMINI_API_KEY_21", "").strip()

def get_official_gemini_client():
    if not GEMINI_OFFICIAL_KEY:
        return None
    return genai.Client(api_key=GEMINI_OFFICIAL_KEY)

# Legacy single-key fallback only when no separated keys are configured.
if not GEMINI_POST_API_KEYS and not GEMINI_GROUP_API_KEYS:
    legacy_key = os.getenv("GEMINI_API_KEY", "").strip()
    if legacy_key:
        GEMINI_POST_API_KEYS = [legacy_key]
        GEMINI_GROUP_API_KEYS = [legacy_key]

_post_api_key_index = 0
_group_api_key_index = 0

def get_next_post_gemini_client():
    global _post_api_key_index
    if not GEMINI_POST_API_KEYS:
        return None
    key = GEMINI_POST_API_KEYS[_post_api_key_index % len(GEMINI_POST_API_KEYS)]
    _post_api_key_index += 1
    return genai.Client(api_key=key)

def get_group_gemini_clients_in_rotation():
    global _group_api_key_index
    if not GEMINI_GROUP_API_KEYS:
        return []
    start = _group_api_key_index % len(GEMINI_GROUP_API_KEYS)
    clients = []
    for offset in range(len(GEMINI_GROUP_API_KEYS)):
        idx = (start + offset) % len(GEMINI_GROUP_API_KEYS)
        clients.append(genai.Client(api_key=GEMINI_GROUP_API_KEYS[idx]))
    _group_api_key_index = (start + 1) % len(GEMINI_GROUP_API_KEYS)
    return clients

# Enable Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==========================================
# DATABASE SYSTEM
# ==========================================
# Database functions are centralized in database.py.
# Initialize the schema once when the bot starts.
init_db()
init_official_content_tables()

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"MARSOF AI Bot Server is Running 24/7!")

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
    EDIT_SELECT_CHANNEL, EDIT_OPTIONS_MENU, CONFIRM_CANCEL_SUB, NETWORK, X_LINK, CONTENT_PREFS,
    LANGUAGE_SELECT, ONBOARDING_INTRO, ANALYZER_INPUT, ANALYZER_NETWORK
) = range(21)

ACTIVE_PUBLISH_TASKS = {}

# ==========================================
# MARSOF AI BRANDING
# ==========================================
MARSOF_BOT_USERNAME = "MARSOF_AI_BOT"
MARSOF_CHANNEL_USERNAME = "marsofai"
MARSOF_SUPPORT_USERNAME = "marsofaisupport"
MARSOF_X_URL = "https://x.com/MARSOF_AI"
MARSOF_WEBSITE_URL = "https://marsof.ct.ws"
MARSOF_BOT_URL = f"https://t.me/{MARSOF_BOT_USERNAME}"
MARSOF_CHANNEL_URL = f"https://t.me/{MARSOF_CHANNEL_USERNAME}"
MARSOF_SUPPORT_URL = f"https://t.me/{MARSOF_SUPPORT_USERNAME}"
MARSOF_LOGO_PATH = os.path.join(os.path.dirname(__file__), "marsof_ai_logo.jpg")
MARSOF_OFFICIAL_COMMUNITY_ID = os.getenv("MARSOF_OFFICIAL_COMMUNITY_ID", "").strip()
MARSOF_OFFICIAL_CHANNEL = os.getenv("MARSOF_OFFICIAL_CHANNEL", MARSOF_CHANNEL_USERNAME).strip()
MARSOF_OFFICIAL_POSTS_PER_DAY = 5
MARSOF_OFFICIAL_COMMUNITY_REPLIES_PER_DAY = 5
MARSOF_OFFICIAL_X_REPLIES_PER_DAY = 10
OFFICIAL_ENGINE_STARTED = False
OFFICIAL_TELEGRAM_CANDIDATES = []
OFFICIAL_TELEGRAM_REPLY_COUNT = 0
OFFICIAL_X_REPLY_COUNT = 0
OFFICIAL_REPLY_COUNT_DATE = None

# Fast /start cache: Telegram entry must never wait for remote PostgreSQL.
START_STATE_CACHE = {}
START_STATE_CACHE_TTL = 300

# ==========================================
# PRIVATE SETUP FLOW CLEANUP
# ==========================================
FLOW_MESSAGE_IDS_KEY = "_flow_message_ids"
FLOW_CURRENT_MESSAGE_KEY = "_flow_current_message_id"

def _flow_ids(context):
    return context.user_data.setdefault(FLOW_MESSAGE_IDS_KEY, set())

def track_flow_message(context, message_id):
    if message_id:
        _flow_ids(context).add(int(message_id))

def track_flow_update(update, context):
    # Keep only a lightweight record; the active flow message is managed
    # separately so each step can replace the previous screen.
    if update.effective_chat and update.effective_chat.type == "private":
        if update.effective_message and update.effective_message.from_user:
            # Incoming user messages are intentionally not retained.
            pass

async def _delete_flow_message(context, chat_id, message_id):
    if not message_id:
        return
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

async def send_flow_photo_reply(update, context, photo_path, caption, reply_markup=None, **kwargs):
    """Send a branded photo screen, then remove the previous flow screen."""
    chat = update.effective_chat
    if not chat:
        return None
    old_id = context.user_data.get(FLOW_CURRENT_MESSAGE_KEY)
    with open(photo_path, "rb") as photo:
        message = await context.bot.send_photo(
            chat_id=chat.id,
            photo=photo,
            caption=caption,
            reply_markup=reply_markup,
            **kwargs
        )
    context.user_data[FLOW_CURRENT_MESSAGE_KEY] = message.message_id
    track_flow_message(context, message.message_id)
    if old_id and old_id != message.message_id:
        await _delete_flow_message(context, chat.id, old_id)
    if update.message and update.message.from_user and update.message.chat.type == "private":
        try:
            await update.message.delete()
        except Exception:
            pass
    return message

async def send_flow_reply(update, context, text, reply_markup=None, **kwargs):
    """Open the new setup screen first, then remove the previous one."""
    chat = update.effective_chat
    if not chat:
        return None

    old_id = context.user_data.get(FLOW_CURRENT_MESSAGE_KEY)

    # IMPORTANT: send the new screen FIRST. This avoids a visible blank/frozen
    # moment while the old screen is being removed.
    message = await context.bot.send_message(chat_id=chat.id, text=text, reply_markup=reply_markup, **kwargs)
    context.user_data[FLOW_CURRENT_MESSAGE_KEY] = message.message_id
    track_flow_message(context, message.message_id)

    # Now clean the previous bot screen.
    if old_id and old_id != message.message_id:
        await _delete_flow_message(context, chat.id, old_id)

    # Remove the user's previous text input when Telegram allows it.
    if update.message and update.message.from_user and update.message.chat.type == "private":
        try:
            await update.message.delete()
        except Exception:
            pass

    return message

async def edit_flow_message(query, context, text, reply_markup=None, **kwargs):
    """Open the next setup screen first, then remove the previous screen."""
    if not query or not query.message:
        return None

    chat_id = query.message.chat_id
    old_id = query.message.message_id

    # IMPORTANT: create the next screen FIRST, then delete the old callback
    # message. This gives the navigation the requested smooth transition.
    message = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        **kwargs
    )
    context.user_data[FLOW_CURRENT_MESSAGE_KEY] = message.message_id
    track_flow_message(context, message.message_id)

    if old_id != message.message_id:
        await _delete_flow_message(context, chat_id, old_id)

    return message

async def cleanup_private_flow(update, context):
    chat = update.effective_chat
    if not chat or chat.type != "private":
        return

    ids = set(_flow_ids(context))
    current = context.user_data.get(FLOW_CURRENT_MESSAGE_KEY)
    if current:
        ids.add(int(current))

    for message_id in sorted(ids):
        await _delete_flow_message(context, chat.id, message_id)

    context.user_data.pop(FLOW_MESSAGE_IDS_KEY, None)
    context.user_data.pop(FLOW_CURRENT_MESSAGE_KEY, None)

async def menu_home_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await cleanup_private_flow(update, context)
    return await start(update, context)

async def flow_message_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Non-consuming tracker retained for compatibility with the handler setup.
    track_flow_update(update, context)

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

FREE_PLAN_AD_TEXT = "\n\n🤖 Powered by MARSOF AI"

FREE_PLAN_AD_BUTTONS = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🤖 MARSOF AI", url=MARSOF_BOT_URL),
        InlineKeyboardButton("🌐 Website", url=MARSOF_WEBSITE_URL),
    ],
    [
        InlineKeyboardButton("💰 Partner Program", url=MARSOF_BOT_URL),
        InlineKeyboardButton("📢 Telegram", url=MARSOF_CHANNEL_URL),
    ]
])

def parse_channel_input(user_input: str) -> str:
    clean_input = user_input.strip()
    if not clean_input:
        return ""

    lower = clean_input.lower()
    if lower.startswith(("https://t.me/", "http://t.me/")):
        remainder = clean_input.split("t.me/", 1)[1].strip("/")
        # Private invite links cannot be resolved as a chat until Telegram has
        # access to the invite. Keep them intact so the validator can explain
        # that limitation instead of turning them into an invalid @username.
        if remainder.startswith("+") or remainder.startswith("joinchat/"):
            return clean_input
        username = remainder.split("/", 1)[0]
        return f"@{username}" if username and not username.startswith("@") else username

    if clean_input.startswith("@") or clean_input.startswith("-100"):
        return clean_input
    return f"@{clean_input}"

def generate_ai_post(data, include_links=True):
    try:
        client = get_next_post_gemini_client()
        if not client:
            logging.warning("No GEMINI_API_KEY_1 configured for AI posts; using fallback_db.py")
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
        # Legacy per-post AI path. Normal publishing no longer calls this;
        # central pool generation is used instead.
        logging.error(f"Legacy Gemini AI post generation failed: {e}")
        logging.info("Falling back to fallback_db.py for this post")
        return get_fallback_message(data)


# ==========================================
# CENTRAL AI CONTENT POOL
# ==========================================
NETWORKS_FOR_POOL = ["BNB", "Solana", "Sui", "Arc", "Robinhood"]
CONTENT_POOL_LOCK = asyncio.Lock()
CONTENT_POOL_CACHE = {"count": 0, "batch_date": None}
CONTENT_DELIVERY_STATE = {}
COMMUNITY_HYPE_PROBABILITY = 0.20
# Prevent repeated Gemini calls after a quota/API failure. Each post key is
# attempted at most once per UTC/local calendar day for the central pool.
CONTENT_POOL_ATTEMPT_DATE = None
CONTENT_POOL_GENERATION_FAILED_TODAY = False


def _extract_json_posts(text):
    import json
    if not text:
        return []
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, list) else []
    except Exception:
        match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
        if not match:
            return []
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, list) else []
        except Exception:
            return []


def _generate_pool_with_client(client, prompt):
    try:
        response = client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
        return _extract_json_posts(response.text if response else "")
    except Exception as e:
        logging.error(f"Central content generation failed: {e}")
        return []


async def ensure_daily_content_pool():
    """Ensure today's shared 30-post content pool exists.

    Gemini is used only to build the central pool, never once per group/post.
    If the post-generation quota is exhausted, do not hammer Gemini again on
    every publisher loop; use the local fallback layer until the next day.
    """
    global CONTENT_POOL_ATTEMPT_DATE, CONTENT_POOL_GENERATION_FAILED_TODAY

    async with CONTENT_POOL_LOCK:
        from datetime import date
        today = date.today()

        count, batch_date = get_content_pool_stats()
        if count >= 30 and str(batch_date) == str(today):
            CONTENT_POOL_CACHE["count"] = count
            CONTENT_POOL_CACHE["batch_date"] = str(today)
            return True

        if CONTENT_POOL_ATTEMPT_DATE == str(today) and CONTENT_POOL_GENERATION_FAILED_TODAY:
            return False

        if not GEMINI_POST_API_KEYS:
            logging.warning("No central-post Gemini keys configured; using local content.")
            CONTENT_POOL_ATTEMPT_DATE = str(today)
            CONTENT_POOL_GENERATION_FAILED_TODAY = True
            return False

        CONTENT_POOL_ATTEMPT_DATE = str(today)
        CONTENT_POOL_GENERATION_FAILED_TODAY = False

        global_prompt = """
You are a Crypto-native Telegram content writer for a memecoin-focused audience.
Create exactly 15 short English posts as a JSON array.
Return ONLY valid JSON:
[{"category":"geopolitical|markets|crypto|question","slot":"morning|midday|evening|any","content":"..."}]

EXACTLY:
- 3 geopolitical / macro-risk posts: one morning, one midday, one evening.
- 3 markets/economy posts about interest rates, inflation, jobs, GDP, central banks or major macro data.
- 4 broad crypto posts.
- 5 evergreen community questions. Every question MUST contain the exact placeholder {coin_name}.

STYLE — CRITICAL:
- Do NOT write boring wire-service summaries.
- Turn each fact/theme into a short, energetic crypto-native post.
- Start with a strong hook when justified: 🚨 🔥 👀 ⚡ etc., but never fake urgency.
- Explain WHY the information matters to crypto traders, memecoin communities or market narratives.
- Use natural crypto language such as momentum, flow, liquidity, narrative, catalyst, rotation, whales, risk-on/risk-off and market reaction only when supported.
- Make the post feel alive, human and punchy: normally 3-6 short lines.
- Use 2-5 relevant emojis, not emoji spam.
- End with a natural watch-point or question only when it genuinely fits.

FACTUALITY:
- Do not invent breaking events, current prices, statistics, partnerships, listings, whale movements or announcements.
- Do not present speculation as fact.
- If a fact is not current/verified in the supplied knowledge, present it as general context rather than pretending it is breaking news.
- Never guarantee pumps, dumps, profits or outcomes.
- Never give direct financial advice such as BUY NOW or SELL NOW.
- Do not use political persuasion; geopolitical/economic items must remain descriptive and market-focused.

Goal: FACT → WHY IT MATTERS → MOMENTUM.
Make ordinary information interesting without manufacturing hype.
"""
        network_prompt = f"""
You are a Crypto-native Telegram writer covering blockchain ecosystems.
Create exactly 15 short English posts as a JSON array.
Return ONLY valid JSON:
[{{"network":"NETWORK","category":"network","content":"..."}}]

Create exactly 3 posts for EACH of these networks: {', '.join(NETWORKS_FOR_POOL)}.
Discuss technology, builders, DeFi, infrastructure, adoption, ecosystem narratives or durable themes.
Make each post energetic and interesting for crypto/memecoin audiences, with a strong hook where justified.
Focus on WHY the ecosystem theme matters rather than merely defining it.
Do not invent current breaking events, prices, partnerships, listings or statistics.
Do not imply guaranteed price movement or give direct financial advice.
Keep each post concise, normally 3-6 short lines, with moderate emoji use.
"""
        # Exactly two central-generation calls: one with key 1 and one with key 2.
        # If only one key exists, reuse it only once for the network batch.
        post_keys = [k for k in GEMINI_POST_API_KEYS[:2] if k]
        if not post_keys:
            CONTENT_POOL_GENERATION_FAILED_TODAY = True
            return False

        global_posts = _generate_pool_with_client(genai.Client(api_key=post_keys[0]), global_prompt)

        # If the first central-generation call is exhausted/unavailable, do not
        # immediately fire another request. This prevents a second quota hit
        # during the same startup and lets the local fallback layer take over.
        if not global_posts:
            CONTENT_POOL_GENERATION_FAILED_TODAY = True
            logging.warning(
                "Central global content batch could not be generated; "
                "skipping the network batch and using local fallback today."
            )
            return False

        network_key = post_keys[1] if len(post_keys) > 1 else post_keys[0]
        network_posts = _generate_pool_with_client(genai.Client(api_key=network_key), network_prompt)
        if not network_posts:
            CONTENT_POOL_GENERATION_FAILED_TODAY = True
            logging.warning(
                "Central network content batch could not be generated; "
                "using local fallback today."
            )
            return False

        normalized = []
        allowed = {"geopolitical", "markets", "crypto", "question"}
        aliases = {
            "bnb": "BNB", "bsc": "BNB", "bnb chain": "BNB", "binance smart chain": "BNB",
            "sol": "Solana", "solana": "Solana", "sui": "Sui", "arc": "Arc",
            "robinhood": "Robinhood", "robinhood chain": "Robinhood"
        }

        for item in global_posts:
            if not isinstance(item, dict) or not item.get("content"):
                continue
            category = str(item.get("category", "crypto")).strip().lower()
            slot = str(item.get("slot", "any")).strip().lower()
            if category not in allowed:
                continue
            if category == "geopolitical" and slot not in {"morning", "midday", "evening"}:
                continue
            if category != "geopolitical":
                slot = "any"
            normalized.append({
                "category": category,
                "network": "",
                "slot": slot,
                "content": str(item["content"]).strip()
            })

        for item in network_posts:
            if not isinstance(item, dict) or not item.get("content") or not item.get("network"):
                continue
            raw = str(item["network"]).strip()
            network = aliases.get(raw.lower(), raw)
            normalized.append({
                "category": "network",
                "network": network,
                "slot": "any",
                "content": str(item["content"]).strip()
            })

        counts = {c: sum(1 for x in normalized if x["category"] == c)
                  for c in ["geopolitical", "markets", "crypto", "question", "network"]}
        net_counts = {n: sum(1 for x in normalized if x["category"] == "network" and x["network"] == n)
                      for n in NETWORKS_FOR_POOL}
        slots = {x["slot"] for x in normalized if x["category"] == "geopolitical"}

        complete = (
            counts["geopolitical"] >= 3 and
            counts["markets"] >= 3 and
            counts["crypto"] >= 4 and
            counts["question"] >= 5 and
            counts["network"] >= 15 and
            {"morning", "midday", "evening"}.issubset(slots) and
            all(net_counts[n] >= 3 for n in NETWORKS_FOR_POOL)
        )

        if not complete:
            CONTENT_POOL_GENERATION_FAILED_TODAY = True
            logging.warning(
                "Central content pool incomplete; local fallback will be used today. "
                f"counts={counts}, networks={net_counts}"
            )
            return False

        selected = []
        for slot in ("morning", "midday", "evening"):
            selected.append(next(x for x in normalized if x["category"] == "geopolitical" and x["slot"] == slot))
        for category, number in (("markets", 3), ("crypto", 4), ("question", 5)):
            selected.extend([x for x in normalized if x["category"] == category][:number])
        for network in NETWORKS_FOR_POOL:
            selected.extend([x for x in normalized if x["category"] == "network" and x["network"] == network][:3])

        replace_content_pool(selected)
        CONTENT_POOL_CACHE["count"] = len(selected)
        CONTENT_POOL_CACHE["batch_date"] = str(today)
        logging.info("Central AI content pool refreshed: 30 posts (3 geo + 3 markets + 4 crypto + 5 questions + 15 network).")
        return True


def choose_central_post(data):
    """Select shared content once per item/day for each group, without Gemini."""
    import hashlib
    from datetime import date, datetime

    channel = str(data.get("channel", ""))
    today = str(date.today())
    state_key = f"{channel}:{today}"
    seen = CONTENT_DELIVERY_STATE.setdefault(state_key, set())

    # Keep memory bounded when the bot runs for many days.
    for key in list(CONTENT_DELIVERY_STATE):
        if not key.endswith(f":{today}"):
            CONTENT_DELIVERY_STATE.pop(key, None)

    eligible=[]
    if data.get("receive_geopolitical_news", True):
        hour=datetime.now().hour
        slot="morning" if 6<=hour<11 else "midday" if 11<=hour<16 else "evening" if 17<=hour<23 else ""
        if slot:
            eligible.extend([x for x in get_content_pool_posts(category="geopolitical") if x.get("slot")==slot])
    if data.get("receive_market_news", True):
        eligible.extend(get_content_pool_posts(category="markets"))
    eligible.extend(get_content_pool_posts(category="crypto"))
    eligible.extend(get_content_pool_posts(category="question"))

    network=str(data.get("network","")).strip()
    if network:
        eligible.extend(get_content_pool_posts(category="network",network=network))
    else:
        for n in NETWORKS_FOR_POOL:
            eligible.extend(get_content_pool_posts(category="network",network=n))

    if not eligible:
        return None

    unseen=[x for x in eligible if x.get("id") not in seen]
    if not unseen:
        # Once this group's eligible daily pool is exhausted, start a new cycle.
        seen.clear()
        unseen=eligible

    digest=int(hashlib.md5(channel.encode()).hexdigest()[:8],16)
    item=unseen[digest % len(unseen)]
    seen.add(item.get("id"))
    return item["content"]


def generate_free_post(data, slot_index: int):
    """Generate one of the five daily Free-plan content slots.

    Slots 1-2 are pure community hype with no MARSOF promotion.
    Slots 3-4 are network/economy updates with the small MARSOF footer.
    Slot 5 is a neutral crypto/community insight, also with the small footer.
    """
    if slot_index in (0, 1):
        post_text = get_fallback_message(data)
        return post_text, None

    if slot_index == 2:
        items = get_content_pool_posts(category="network", network=str(data.get('network', '')).strip()) if str(data.get('network', '')).strip() else []
        if not items:
            items = choose_central_post(data)
            return items or get_fallback_message(data), "ad"
        post_text = random.choice(items).get("content") if items else None
        return post_text or get_fallback_message(data), "ad"

    if slot_index == 3:
        items = get_content_pool_posts(category="markets")
        post_text = random.choice(items).get("content") if items else None
        return post_text or choose_central_post(data) or get_fallback_message(data), "ad"

    # Fifth slot is a single full MARSOF AI promotional post per day.
    full_ad = (
        "🤖 MARSOF AI\n\n"
        "The AI command center for crypto communities.\n\n"
        "🧠 AI Community Management\n"
        "📊 Community Analytics\n"
        "🔍 AI Coin Analyzer\n"
        "📢 AI Promotion\n\n"
        "💰 Join the MARSOF AI Partner Program and earn commissions on qualifying sales.\n\n"
        "🚀 Start Free with MARSOF AI"
    )
    return full_ad, "full_ad"


def generate_post(data, free_slot_index=None):
    if data.get('selected_plan') == 'free':
        post_text, ad_mode = generate_free_post(data, free_slot_index or 0)
        post_text = post_text.replace("{coin_name}", str(data.get("coin_name", "Token")))
        if ad_mode == "ad":
            post_text += FREE_PLAN_AD_TEXT
        return post_text, ad_mode

    # Buy Simulator is a separate community tool and is never mixed into the
    # normal AI Promoter publishing loop.
    if random.random() < COMMUNITY_HYPE_PROBABILITY:
        post_text=get_fallback_message(data)
    else:
        post_text=choose_central_post(data) or get_fallback_message(data)
    post_text=post_text.replace("{coin_name}",str(data.get("coin_name","Token")))
    return post_text, None


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


# ==========================================
# FAST GROUP KEYWORD REPLIES (NO GEMINI)
# ==========================================
# These replies are intentionally handled locally so common questions do not
# consume Gemini quota or wait for an API response. Matching is case-insensitive
# and uses whole words, so a letter such as the "x" in a normal sentence does
# not trigger the X/Twitter reply.

def get_fast_keyword_reply(data, message_text):
    """Return an instant local reply for common group keywords, or None."""
    if not message_text:
        return None

    text = message_text.strip()
    text_lower = text.lower()

    # CA / Contract Address
    if re.search(r"\b(?:ca|contract(?:\s+address)?)\b", text_lower, re.IGNORECASE):
        contract = str(data.get("contract", "")).strip()
        if contract and contract.lower() != "not set":
            return f"📜 Contract (CA):\n{contract}"
        return "📜 The contract address is not configured yet."

    # Buy / Buy link
    if re.search(r"\b(?:buy|buy\s+link|where\s+to\s+buy)\b", text_lower, re.IGNORECASE):
        buy_link = str(data.get("buy_link", "")).strip()
        if buy_link and buy_link.lower() != "not set":
            return f"🛒 Buy here:\n{buy_link}"
        return "🛒 The official buy link is not configured yet."

    # X / Twitter. We only auto-answer if an X link is configured in the
    # project data/environment; otherwise we avoid inventing a social link.
    if re.search(r"\b(?:twitter|x)\b", text_lower, re.IGNORECASE):
        x_link = str(data.get("x_link") or os.getenv("PROJECT_X_LINK", "")).strip()
        if x_link:
            return f"🐦 Official X / Twitter:\n{x_link}"
        return "🐦 The official X / Twitter link is not configured yet."

    return None


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
Network / ecosystem: {data.get('network', '')}

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

    last_error = None
    clients = get_group_gemini_clients_in_rotation()[:3]

    logging.info(
        f"Generating AI group reply for @{member_name}: {member_message[:120]}"
    )

    for attempt, client in enumerate(clients, start=1):
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-3.6-flash",
                contents=prompt,
            )

            if response and response.text and response.text.strip():
                reply = response.text.strip()
                logging.info(f"AI group reply generated successfully: {reply[:150]}")
                return reply

            last_error = "Gemini returned an empty response"
            logging.warning(f"Gemini group reply attempt {attempt}/{len(clients)} returned an empty response")

        except Exception as e:
            last_error = e
            logging.error(
                f"Gemini group reply attempt {attempt}/{len(clients)} failed: {e}"
            )

    if last_error:
        logging.error(f"All group Gemini keys failed: {last_error}")

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

    # Moderation also belongs to the group-AI pool (keys 2-4), never key 1.
    for attempt, client in enumerate(get_group_gemini_clients_in_rotation()[:2], start=1):
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-3.6-flash",
                contents=prompt,
            )
            result = (response.text or "").strip().upper() if response else ""
            return result.startswith("OTHER_COIN")
        except Exception as e:
            logging.error(
                f"Gemini moderation attempt {attempt} failed: {e}"
            )

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

    # Collect genuine questions from the official MARSOF AI community for the
    # separate Key-21 reply engine. This is limited and does not auto-reply to every message.
    if MARSOF_OFFICIAL_COMMUNITY_ID and str(chat.id) == str(MARSOF_OFFICIAL_COMMUNITY_ID):
        candidate_text = (message.text or message.caption or "").strip()
        if candidate_text and ("?" in candidate_text or len(candidate_text.split()) >= 6):
            OFFICIAL_TELEGRAM_CANDIDATES.append({
                "message_id": message.message_id,
                "name": user.first_name or "Member",
                "text": candidate_text[:1200],
            })
            del OFFICIAL_TELEGRAM_CANDIDATES[:-100]

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

    # 2A) Common questions are answered instantly without Gemini. This works
    # even when the member does NOT tag/reply to the bot.
    fast_reply = get_fast_keyword_reply(data, message_text)
    if fast_reply:
        await message.reply_text(fast_reply)
        return

    # 2B) Only use Gemini when the member explicitly tags/replies to the bot.
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


ONBOARDING_LANGUAGES = {
    "en": "🇬🇧 English",
    "zh": "🇨🇳 中文",
    "ru": "🇷🇺 Русский",
    "es": "🇪🇸 Español",
    "ar": "🇸🇦 العربية",
    "fr": "🇫🇷 Français",
    "pt": "🇵🇹 Português",
    "tr": "🇹🇷 Türkçe",
    "de": "🇩🇪 Deutsch",
    "hi": "🇮🇳 हिन्दी",
    "ko": "🇰🇷 한국어",
    "ja": "🇯🇵 日本語",
}

ONBOARDING_INTRO_TEXT = {
    "en": (
        "👑 MARSOF AI — THE INTELLIGENCE BEHIND YOUR COMMUNITY\n\n"
        "MARSOF AI is more than an auto-poster. It is an AI-powered command layer built to help crypto projects keep their communities active, responsive and alive — even when the owner is away.\n\n"
        "🧠 AI CONTENT ENGINE\n"
        "Creates intelligent community content, hype, questions, market-oriented posts and ecosystem content designed around your project.\n\n"
        "📊 COMMUNITY INTELLIGENCE\n"
        "Analyzes community behavior and interaction patterns to help keep the group active and engaging.\n\n"
        "⚡ ACTIVE 24/7\n"
        "Responds to members, handles common questions, encourages discussion and helps maintain the energy of the group.\n\n"
        "🛠️ A GROWING TOOLBOX\n"
        "A powerful and continuously expanding suite of integrated AI tools gives project owners more control, automation and intelligence — even while they are offline.\n\n"
        "🚀 THIS IS ONLY THE BEGINNING\n"
        "Major upgrades are already on the roadmap, including an affordable full AI-agent experience for just a few dollars. More intelligence. More automation. More control.\n\n"
        "Welcome to the next generation of crypto community management.\n"
        "🔥 Welcome to MARSOF AI."
    ),
    "zh": (
        "👑 MARSOF AI — 你的社区智能中枢\n\n"
        "MARSOF AI 不只是自动发帖机器人，而是一套由 AI 驱动的社区管理与增长系统，帮助加密项目保持社区活跃、响应及时，即使项目负责人暂时不在线。\n\n"
        "🧠 AI 内容引擎\n自动生成社区内容、互动问题、市场相关内容和生态内容，并围绕你的项目进行定制。\n\n"
        "📊 社区智能\n分析社区互动和行为模式，帮助持续提升群组活跃度与参与感。\n\n"
        "⚡ 24/7 持续运行\n智能回复成员、处理常见问题、推动讨论，让社区保持活力。\n\n"
        "🛠️ 持续扩展的工具体系\n集成大量先进 AI 工具，让项目方即使离线，也能获得更强的自动化和控制能力。\n\n"
        "🚀 这只是开始\n更多升级正在开发中，包括只需几美元即可使用的完整 AI Agent 体验。更多智能、更多自动化、更多控制。\n\n"
        "🔥 欢迎来到 MARSOF AI。"
    ),
    "ru": (
        "👑 MARSOF AI — ИНТЕЛЛЕКТ ДЛЯ ВАШЕГО СООБЩЕСТВА\n\n"
        "MARSOF AI — это больше, чем автопостер. Это AI-система для управления и развития криптосообщества, которая помогает поддерживать активность и отвечать участникам даже тогда, когда владелец проекта отсутствует.\n\n"
        "🧠 AI-КОНТЕНТ\nСоздаёт интеллектуальные посты, вопросы, рыночный и экосистемный контент под ваш проект.\n\n"
        "📊 ИНТЕЛЛЕКТ СООБЩЕСТВА\nАнализирует взаимодействия и поведение участников, помогая поддерживать активность группы.\n\n"
        "⚡ 24/7\nОтвечает участникам, помогает с типичными вопросами и поддерживает живое общение.\n\n"
        "🛠️ РАСТУЩАЯ ЭКОСИСТЕМА ИНСТРУМЕНТОВ\nБольшой набор интегрированных AI-инструментов даёт владельцу проекта больше автоматизации, контроля и интеллекта даже в его отсутствие.\n\n"
        "🚀 ЭТО ТОЛЬКО НАЧАЛО\nВ разработке новые возможности, включая полноценный AI Agent всего за несколько долларов.\n\n"
        "🔥 Добро пожаловать в MARSOF AI."
    ),
    "es": (
        "👑 MARSOF AI — INTELIGENCIA PARA TU COMUNIDAD\n\n"
        "MARSOF AI es mucho más que un autoposter. Es una capa de inteligencia impulsada por IA diseñada para mantener las comunidades cripto activas, conectadas y atendidas, incluso cuando el propietario está ausente.\n\n"
        "🧠 MOTOR DE CONTENIDO IA\nCrea contenido inteligente, preguntas, publicaciones de mercado y contenido de ecosistema adaptado a tu proyecto.\n\n"
        "📊 INTELIGENCIA DE COMUNIDAD\nAnaliza patrones de interacción y comportamiento para ayudar a mantener el grupo activo.\n\n"
        "⚡ ACTIVO 24/7\nResponde a miembros, ayuda con preguntas frecuentes y estimula la conversación.\n\n"
        "🛠️ UN ECOSISTEMA DE HERRAMIENTAS EN EXPANSIÓN\nUna amplia suite de herramientas de IA integradas ofrece más automatización, control e inteligencia.\n\n"
        "🚀 ESTO ES SOLO EL PRINCIPIO\nLlegan nuevas funciones, incluido un futuro AI Agent completo por solo unos pocos dólares.\n\n"
        "🔥 Bienvenido a MARSOF AI."
    ),
    "ar": (
        "👑 MARSOF AI — العقل الذكي خلف مجتمعك\n\n"
        "MARSOF AI ليس مجرد بوت للنشر الآلي. إنه منظومة ذكاء اصطناعي صُممت لمساعدة مشاريع الكريبتو على إبقاء مجموعاتها نشطة، متفاعلة وسريعة الاستجابة — حتى عندما يكون صاحب المشروع غائبًا.\n\n"
        "🧠 محرك محتوى بالذكاء الاصطناعي\n"
        "يصنع محتوى ذكيًا للمجتمع، أسئلة وتفاعلات، منشورات مرتبطة بالسوق والأنظمة البيئية، ومحتوى مصمم حول مشروعك.\n\n"
        "📊 ذكاء المجتمع\n"
        "يحلل سلوك الأعضاء وأنماط التفاعل للمساعدة في الحفاظ على نشاط المجموعة وزيادة المشاركة.\n\n"
        "⚡ يعمل على مدار الساعة\n"
        "يرد على الأعضاء، يساعد في الأسئلة الشائعة، يشجع النقاش ويحافظ على حيوية المجموعة.\n\n"
        "🛠️ منظومة متطورة من الأدوات\n"
        "مجموعة واسعة ومتنامية من أدوات الذكاء الاصطناعي المدمجة تمنح صاحب المشروع تحكمًا وأتمتة وذكاءً أكبر — حتى وهو بعيد عن المجموعة.\n\n"
        "🚀 هذه مجرد البداية\n"
        "هناك الكثير من التطويرات القادمة، بما فيها تجربة AI Agent متكاملة بسعر لا يتجاوز بضعة دولارات. المزيد من الذكاء، المزيد من الأتمتة، المزيد من التحكم.\n\n"
        "🔥 مرحبًا بك في MARSOF AI."
    ),
    "fr": (
        "👑 MARSOF AI — L'INTELLIGENCE DERRIÈRE VOTRE COMMUNAUTÉ\n\n"
        "MARSOF AI est bien plus qu'un autoposter. C'est une couche d'intelligence alimentée par l'IA conçue pour garder les communautés crypto actives et réactives, même lorsque le propriétaire est absent.\n\n"
        "🧠 MOTEUR DE CONTENU IA\nCrée du contenu intelligent, des questions, des publications orientées marché et des contenus écosystème adaptés à votre projet.\n\n"
        "📊 INTELLIGENCE COMMUNAUTAIRE\nAnalyse les interactions et les comportements afin de contribuer à maintenir le groupe actif.\n\n"
        "⚡ ACTIF 24/7\nRépond aux membres, aide sur les questions courantes et stimule les discussions.\n\n"
        "🛠️ UNE BOÎTE À OUTILS EN CONSTANTE ÉVOLUTION\nUne large suite d'outils IA intégrés apporte davantage d'automatisation, de contrôle et d'intelligence.\n\n"
        "🚀 CE N'EST QUE LE DÉBUT\nDe nombreuses évolutions arrivent, notamment une expérience AI Agent complète pour quelques dollars seulement.\n\n"
        "🔥 Bienvenue dans MARSOF AI."
    ),
}

# Keep the same premium introduction concept for languages whose full localized
# copy is not yet provided; English is used rather than inventing poor translations.
for _code in ONBOARDING_LANGUAGES:
    ONBOARDING_INTRO_TEXT.setdefault(_code, ONBOARDING_INTRO_TEXT["en"])

async def show_language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(ONBOARDING_LANGUAGES["en"], callback_data="lang_en"), InlineKeyboardButton(ONBOARDING_LANGUAGES["zh"], callback_data="lang_zh")],
        [InlineKeyboardButton(ONBOARDING_LANGUAGES["ru"], callback_data="lang_ru"), InlineKeyboardButton(ONBOARDING_LANGUAGES["es"], callback_data="lang_es")],
        [InlineKeyboardButton(ONBOARDING_LANGUAGES["ar"], callback_data="lang_ar"), InlineKeyboardButton(ONBOARDING_LANGUAGES["fr"], callback_data="lang_fr")],
        [InlineKeyboardButton(ONBOARDING_LANGUAGES["pt"], callback_data="lang_pt"), InlineKeyboardButton(ONBOARDING_LANGUAGES["tr"], callback_data="lang_tr")],
        [InlineKeyboardButton(ONBOARDING_LANGUAGES["de"], callback_data="lang_de"), InlineKeyboardButton(ONBOARDING_LANGUAGES["hi"], callback_data="lang_hi")],
        [InlineKeyboardButton(ONBOARDING_LANGUAGES["ko"], callback_data="lang_ko"), InlineKeyboardButton(ONBOARDING_LANGUAGES["ja"], callback_data="lang_ja")],
    ]
    text = (
        "🌐 WELCOME TO MARSOF AI\n\n"
        "Choose your language to enter the experience.\n"
        "اختر لغتك للمتابعة."
    )
    if update.callback_query:
        await edit_flow_message(update.callback_query, context, text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await send_flow_reply(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))
    return LANGUAGE_SELECT

async def onboarding_language_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    language = query.data.replace("lang_", "")
    if language not in ONBOARDING_LANGUAGES:
        return LANGUAGE_SELECT
    context.user_data["language"] = language
    context.user_data["onboarding_seen"] = True
    await asyncio.to_thread(save_onboarding_profile, query.from_user.id, language)

    intro = ONBOARDING_INTRO_TEXT.get(language, ONBOARDING_INTRO_TEXT["en"])
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Enter MARSOF AI", callback_data="onboarding_continue")],
        [
            InlineKeyboardButton("📢 Telegram", url="https://t.me/marsofai"),
            InlineKeyboardButton("𝕏 X / Twitter", url="https://x.com/MARSOF_AI"),
        ],
        [InlineKeyboardButton("🌐 Official Website", url="https://marsof.ct.ws")],
    ])
    await edit_flow_message(query, context, intro, reply_markup=keyboard)
    return ONBOARDING_INTRO

PUBLIC_MENU_TEXT = {
    "en": "👑 MARSOF AI — MAIN MENU\n\nYour AI-powered command center for crypto community growth.\n\nChoose what you want to do:",
    "zh": "👑 MARSOF AI — 主菜单\n\n你的加密社区 AI 智能中枢。\n\n请选择：",
    "ru": "👑 MARSOF AI — ГЛАВНОЕ МЕНЮ\n\nВаш AI-центр управления криптосообществом.\n\nВыберите действие:",
    "es": "👑 MARSOF AI — MENÚ PRINCIPAL\n\nTu centro de inteligencia para el crecimiento de comunidades cripto.\n\nElige una opción:",
    "ar": "👑 MARSOF AI — القائمة الرئيسية\n\nمركز التحكم الذكي لتنشيط مجتمع مشروعك في الكريبتو.\n\nاختر ما تريد القيام به:",
    "fr": "👑 MARSOF AI — MENU PRINCIPAL\n\nVotre centre de contrôle IA pour le développement de votre communauté crypto.\n\nChoisissez une action:",
}
for _code in ONBOARDING_LANGUAGES:
    PUBLIC_MENU_TEXT.setdefault(_code, PUBLIC_MENU_TEXT["en"])

async def show_subscription_plans(update: Update, context: ContextTypes.DEFAULT_TYPE, launch_mode: bool = False):
    """Display the subscription offers without destroying flow/session state."""
    title = "🚀 LAUNCH AI PROMOTER" if launch_mode else "💎 MARSOF AI PLANS"
    intro = (
        "Choose a plan to activate MARSOF AI for your community."
        if launch_mode else
        "Compare the available MARSOF AI plans and choose the access level you need."
    )
    text = (
        f"{title}\n\n{intro}\n\n"
        "🆓 Free — 5 AI posts/day\n"
        "💎 $9.99 — Starter\n"
        "💎 $49.99 — 6 Months\n"
        "💎 $79.99 — Annual\n"
        "♾️ $169.99 — Lifetime\n\n"
        "Select a plan below to continue."
    )
    keyboard = [
        [InlineKeyboardButton("🆓 Free", callback_data="plan_free")],
        [InlineKeyboardButton("💎 $9.99 — Starter", callback_data="plan_1_month")],
        [InlineKeyboardButton("💎 $49.99 — 6 Months", callback_data="plan_6_months")],
        [InlineKeyboardButton("💎 $79.99 — Annual", callback_data="plan_12_months")],
        [InlineKeyboardButton("♾️ $169.99 — Lifetime", callback_data="plan_lifetime")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
    ]
    await (edit_flow_message(update.callback_query, context, text, reply_markup=InlineKeyboardMarkup(keyboard))
           if update.callback_query else
           send_flow_reply(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard)))
    return PLAN_SELECT

async def show_public_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👑 MARSOF AI — MAIN MENU\n\n"
        "Your AI command center for crypto communities and projects.\n\n"
        "Choose a section:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Launch AI Promoter", callback_data="menu_launch"),
         InlineKeyboardButton("💎 View Plans", callback_data="menu_plans")],
        [InlineKeyboardButton("📊 AI & Market Intelligence", callback_data="menu_intelligence")],
        [InlineKeyboardButton("📢 Growth & Monetization", callback_data="menu_growth")],
        [InlineKeyboardButton("🛡️ My Communities", callback_data="menu_community_center")],
        [InlineKeyboardButton("🎨 Project Tools", callback_data="menu_project_tools")],
        [InlineKeyboardButton("🪙 MARSOF AI Token", callback_data="menu_token")],
        [InlineKeyboardButton("🌐 MARSOF AI Community", url=MARSOF_CHANNEL_URL),
         InlineKeyboardButton("🆘 Support", url=MARSOF_SUPPORT_URL)],
    ])
    if update.callback_query:
        await edit_flow_message(update.callback_query, context, text, reply_markup=keyboard)
    else:
        await send_flow_reply(update, context, text, reply_markup=keyboard)
    return MAIN_MENU



# ==========================================
# AI COIN ANALYZER — DATA-FIRST ENGINE
# ==========================================
ANALYZER_CACHE = {}
ANALYZER_CACHE_TTL = 60
ANALYZER_SUPPORTED_CHAINS = {"robinhood", "arc", "solana", "sui", "bsc"}
ANALYZER_CHAIN_LABELS = {
    "robinhood": "Robinhood Chain",
    "arc": "Arc",
    "solana": "Solana",
    "sui": "Sui",
    "bsc": "BNB Chain",
}
ANALYZER_FULL_PLANS = {"6_months", "12_months", "annual", "lifetime"}


def analyzer_normalize_address(value):
    value = (value or "").strip().strip("`<>[]()")
    if value.startswith("https://") or value.startswith("http://"):
        value = value.rstrip("/").split("/")[-1]
    return value


def analyzer_is_evm(value):
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", value or ""))


def analyzer_is_solana(value):
    return bool(re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", value or ""))


def analyzer_is_sui(value):
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{64}", value or ""))


def analyzer_detect_input(address):
    if analyzer_is_sui(address):
        return "sui"
    if analyzer_is_evm(address):
        return "evm"
    if analyzer_is_solana(address):
        return "solana"
    return None


def analyzer_find_chain_pairs(payload):
    pairs = payload.get("pairs") or []
    normalized = []
    for pair in pairs:
        chain = str(pair.get("chainId") or "").lower()
        if chain in ANALYZER_SUPPORTED_CHAINS:
            normalized.append(pair)
    return normalized


def analyzer_build_raw_data(address, pairs, selected_chain=None):
    if selected_chain:
        pairs = [p for p in pairs if str(p.get("chainId", "")).lower() == selected_chain]
    if not pairs:
        return None

    def liq(p):
        try: return float((p.get("liquidity") or {}).get("usd") or 0)
        except Exception: return 0.0

    pairs = sorted(pairs, key=liq, reverse=True)
    main = pairs[0]
    base = main.get("baseToken") or {}
    chain = str(main.get("chainId") or "").lower()
    volume = (main.get("volume") or {})
    txns = (main.get("txns") or {}).get("h24") or {}
    liquidity = (main.get("liquidity") or {})
    info = main.get("info") or {}
    socials = info.get("socials") or []
    websites = info.get("websites") or []

    def num(v):
        try: return float(v) if v is not None else None
        except Exception: return None

    created = main.get("pairCreatedAt")
    age_days = None
    if created:
        try:
            import time
            age_days = max(0, (time.time() * 1000 - float(created)) / 86400000)
        except Exception:
            pass

    return {
        "token": {
            "name": base.get("name"), "symbol": base.get("symbol"),
            "contract": address, "network": ANALYZER_CHAIN_LABELS.get(chain, chain),
            "decimals": None, "total_supply": None, "circulating_supply": None,
            "price_usd": num(main.get("priceUsd")), "market_cap": num(main.get("marketCap")),
            "fdv": num(main.get("fdv")), "pair_created_at": created,
            "age_days": age_days,
        },
        "liquidity": {
            "usd": num(liquidity.get("usd")), "main_pair": main.get("pairAddress"),
            "dex": main.get("dexId"), "pair_count": len(pairs),
        },
        "market": {
            "volume_24h_usd": num(volume.get("h24")),
            "buys_24h": int(txns.get("buys", 0) or 0),
            "sells_24h": int(txns.get("sells", 0) or 0),
            "price_change_24h_pct": num((main.get("priceChange") or {}).get("h24")),
        },
        "social": {
            "websites": [x.get("url") for x in websites if isinstance(x, dict) and x.get("url")],
            "socials": [{"type": x.get("type"), "url": x.get("url")} for x in socials if isinstance(x, dict) and x.get("url")],
        },
        "data_quality": {
            "source": "DexScreener public market data",
            "holders": "unavailable",
            "contract_security": "unavailable",
            "authority_model": "unavailable",
            "honeypot_simulation": "unavailable",
            "taxes": "unavailable",
        },
    }


async def analyzer_fetch_market_data(address):
    import requests
    url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
    response = await asyncio.to_thread(requests.get, url, timeout=12)
    response.raise_for_status()
    payload = response.json()
    return analyzer_find_chain_pairs(payload)


def analyzer_risk_engine(raw):
    """Deterministic risk signals only from verified collected fields."""
    risks = []
    positive = []
    liq = (raw.get("liquidity") or {}).get("usd")
    mc = (raw.get("token") or {}).get("market_cap")
    vol = (raw.get("market") or {}).get("volume_24h_usd")
    buys = (raw.get("market") or {}).get("buys_24h", 0)
    sells = (raw.get("market") or {}).get("sells_24h", 0)

    if liq is not None and liq < 25000:
        risks.append({"category":"Liquidity Risk","level":"High","reason":f"Main-pair liquidity is approximately ${liq:,.0f}."})
    elif liq is not None and liq < 100000:
        risks.append({"category":"Liquidity Risk","level":"Moderate","reason":f"Main-pair liquidity is approximately ${liq:,.0f}."})
    elif liq is not None:
        positive.append({"category":"Liquidity","signal":f"Main-pair liquidity is approximately ${liq:,.0f}."})

    if mc and liq:
        ratio = liq / mc
        if ratio < 0.01:
            risks.append({"category":"Market Structure Risk","level":"High","reason":f"Liquidity is about {ratio*100:.2f}% of market cap."})
        elif ratio < 0.03:
            risks.append({"category":"Market Structure Risk","level":"Moderate","reason":f"Liquidity is about {ratio*100:.2f}% of market cap."})

    if vol is not None and liq and liq > 0 and vol / liq > 20:
        risks.append({"category":"Trading Risk","level":"Moderate","reason":f"24h volume is about {vol/liq:.1f}x reported liquidity."})

    if buys + sells > 0:
        positive.append({"category":"Trading Activity","signal":f"24h activity reports {buys:,} buys and {sells:,} sells."})

    # Missing security data is explicitly reported rather than guessed.
    for category, label in [
        ("Contract Risk", "contract security"),
        ("Ownership / Authority Risk", "owner/authority controls"),
        ("Honeypot / Trading Risk", "honeypot/sell simulation"),
        ("Holder Concentration Risk", "holder distribution"),
        ("Tokenomics Risk", "supply/tokenomics data"),
    ]:
        risks.append({"category":category,"level":"Unavailable","reason":f"Reliable {label} data was not collected by the current analyzer source."})

    return {"risk_signals": risks, "positive_signals": positive}


def analyzer_format_number(value):
    if value is None: return "Unavailable"
    if isinstance(value, float): return f"{value:,.6g}"
    return f"{value:,}"


def analyzer_raw_report(raw):
    t, l, m = raw["token"], raw["liquidity"], raw["market"]
    return (
        "🔍 MARSOF AI — COIN ANALYZER\n\n"
        "📌 TOKEN OVERVIEW\n"
        f"Name: {t.get('name') or 'Unavailable'}\n"
        f"Symbol: {t.get('symbol') or 'Unavailable'}\n"
        f"Network: {t.get('network') or 'Unavailable'}\n"
        f"Contract: {t.get('contract')}\n"
        f"Price: ${analyzer_format_number(t.get('price_usd'))}\n"
        f"Market Cap: ${analyzer_format_number(t.get('market_cap'))}\n"
        f"FDV: ${analyzer_format_number(t.get('fdv'))}\n"
        f"Token age: {analyzer_format_number(t.get('age_days'))} days\n\n"
        "💧 LIQUIDITY\n"
        f"Main liquidity: ${analyzer_format_number(l.get('usd'))}\n"
        f"DEX / Pair: {l.get('dex') or 'Unavailable'} / {l.get('main_pair') or 'Unavailable'}\n"
        f"Detected pairs: {l.get('pair_count', 0)}\n\n"
        "📊 MARKET ACTIVITY (24H)\n"
        f"Volume: ${analyzer_format_number(m.get('volume_24h_usd'))}\n"
        f"Buys: {m.get('buys_24h', 0):,}\n"
        f"Sells: {m.get('sells_24h', 0):,}\n"
        f"Price change: {analyzer_format_number(m.get('price_change_24h_pct'))}%\n\n"
        "🔐 SECURITY / HOLDERS\n"
        "Holder distribution: ⚪ Unavailable\n"
        "Contract security: ⚪ Unavailable\n"
        "Authority controls: ⚪ Unavailable\n"
        "Honeypot simulation: ⚪ Unavailable\n"
        "Taxes: ⚪ Unavailable\n\n"
        "🟢 Data source: DexScreener public market data\n"
        "🟡 Some fields are partial/unavailable. No missing security data has been inferred."
    )


async def analyzer_gemini_report(raw, risks):
    clients = get_group_gemini_clients_in_rotation()
    if not clients:
        return None
    import json
    prompt = f"""
You are the interpretation layer of MARSOF AI Coin Analyzer.
DATA FIRST -> ANALYSIS SECOND -> AI INTERPRETATION THIRD.
Use ONLY the supplied JSON. Never invent missing holders, ownership, taxes, security checks,
partnerships, social activity or other facts. Do not give Buy/Sell advice or price predictions.
Explain the evidence concisely in crypto-native language.

RAW DATA:
{json.dumps(raw, ensure_ascii=False)}

DETERMINISTIC RISK ENGINE:
{json.dumps(risks, ensure_ascii=False)}

Return a concise report with these headings:
🧠 AI INTERPRETATION
⚠️ KEY RISKS
🟢 POSITIVE SIGNALS
🔎 WHAT IS NOT VERIFIED
📋 CONCLUSION
The conclusion must be descriptive, not an investment recommendation.
"""
    try:
        client = clients[0]
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-3.6-flash",
            contents=prompt,
        )
        return response.text.strip() if response and response.text else None
    except Exception as exc:
        logging.error("[COIN ANALYZER] Gemini failed: %s", exc)
        return None


def analyzer_user_plan(user_id, context):
    cached = str(context.user_data.get("selected_plan", "")).lower()
    try:
        for row in get_active_users():
            if str(row.get("user_id", "")) == str(user_id):
                return str(row.get("selected_plan", cached or "free")).lower()
    except Exception:
        pass
    return cached or "free"


async def start_coin_analyzer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan = analyzer_user_plan(query.from_user.id, context)
    access = "Full Risk + Gemini analysis" if plan in ANALYZER_FULL_PLANS else "Raw verified data only"
    text = (
        "🔍 MARSOF AI — COIN ANALYZER\n\n"
        "Enter the token contract address.\n\n"
        "Supported networks: Robinhood Chain • Arc • Solana • Sui • BNB Chain\n\n"
        f"💳 Your plan: {plan.upper()}\n"
        f"📊 Analyzer access: {access}\n\n"
        "Example: 0x... (EVM/Sui) or a Solana token address."
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]])
    await edit_flow_message(query, context, text, reply_markup=keyboard)
    return ANALYZER_INPUT


async def coin_analyzer_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    address = analyzer_normalize_address(update.message.text)
    detected = analyzer_detect_input(address)
    if not detected:
        await update.message.reply_text("❌ I couldn't recognize this as a supported token contract address.\n\nSend a valid Robinhood Chain / Arc / BNB EVM address, Sui object ID, or Solana token address.")
        return ANALYZER_INPUT

    await update.message.reply_text("🔎 Detecting network and collecting live market data…")
    try:
        pairs = await analyzer_fetch_market_data(address)
    except Exception as exc:
        logging.warning("[COIN ANALYZER] market API failed: %s", exc)
        await update.message.reply_text("⚠️ Market data could not be retrieved right now. Please try again shortly.")
        return ANALYZER_INPUT

    if not pairs:
        await update.message.reply_text("⚪ No supported market data was found for this contract. The analyzer will not guess the network.")
        return ANALYZER_INPUT

    chains = sorted({str(p.get("chainId", "")).lower() for p in pairs})
    if len(chains) > 1:
        context.user_data["analyzer_address"] = address
        context.user_data["analyzer_pairs"] = pairs
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(ANALYZER_CHAIN_LABELS.get(c, c), callback_data=f"analyzer_chain_{c}")]
            for c in chains
        ] + [[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]])
        await update.message.reply_text("🌐 Multiple supported networks were detected.\n\nSelect the network to analyze:", reply_markup=keyboard)
        return ANALYZER_NETWORK

    return await run_coin_analyzer(update, context, address, chains[0], pairs)


async def analyzer_network_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chain = query.data.replace("analyzer_chain_", "").lower()
    address = context.user_data.get("analyzer_address")
    pairs = context.user_data.get("analyzer_pairs") or []
    if not address or chain not in ANALYZER_SUPPORTED_CHAINS:
        return await start(update, context)
    return await run_coin_analyzer(update, context, address, chain, pairs)


async def run_coin_analyzer(update, context, address, chain, pairs):
    try:
        raw = analyzer_build_raw_data(address, pairs, chain)
        if not raw:
            raise ValueError("No pair data for selected network")
        cache_key = f"{chain}:{address.lower()}"
        import time
        cached = ANALYZER_CACHE.get(cache_key)
        if cached and time.time() - cached[0] < ANALYZER_CACHE_TTL:
            raw = cached[1]
        else:
            ANALYZER_CACHE[cache_key] = (time.time(), raw)

        plan = analyzer_user_plan(update.effective_user.id, context)
        if plan not in ANALYZER_FULL_PLANS:
            text = analyzer_raw_report(raw) + "\n\n💎 Full Risk Engine + Gemini interpretation is available on eligible paid plans."
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 View Plans", callback_data="menu_plans")],
                [InlineKeyboardButton("🔍 Analyze Another Token", callback_data="menu_coin_analyzer")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
            ])
            if update.callback_query:
                await edit_flow_message(update.callback_query, context, text, reply_markup=keyboard)
            else:
                await update.effective_message.reply_text(text, reply_markup=keyboard)
            return MAIN_MENU

        risks = analyzer_risk_engine(raw)
        await (update.callback_query.message.reply_text("🧠 Running deterministic risk checks and one AI interpretation…") if update.callback_query else update.effective_message.reply_text("🧠 Running deterministic risk checks and one AI interpretation…"))
        ai = await analyzer_gemini_report(raw, risks)
        report = analyzer_raw_report(raw) + "\n\n" + "⚠️ RISK ENGINE\n" + "\n".join(f"• {x['category']}: {x['level']} — {x['reason']}" for x in risks["risk_signals"][:8])
        if ai:
            report += "\n\n" + ai
        else:
            report += "\n\n⚪ Gemini interpretation is currently unavailable; the deterministic data/risk layer is still shown."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Analyze Another Token", callback_data="menu_coin_analyzer")],
            [InlineKeyboardButton("📊 AI & Market Intelligence", callback_data="menu_intelligence")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
        ])
        if update.callback_query:
            await edit_flow_message(update.callback_query, context, report, reply_markup=keyboard)
        else:
            await update.effective_message.reply_text(report, reply_markup=keyboard)
        return MAIN_MENU
    except Exception as exc:
        logging.exception("[COIN ANALYZER] unexpected error: %s", exc)
        msg = update.effective_message
        if msg:
            await msg.reply_text("⚠️ The analyzer encountered an unexpected error. No unsupported conclusion was generated.")
        return ANALYZER_INPUT


async def public_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_plans":
        return await show_subscription_plans(update, context, launch_mode=False)
    if data == "menu_launch":
        return await show_subscription_plans(update, context, launch_mode=True)
    if data == "menu_intelligence":
        text = "📊 AI & MARKET INTELLIGENCE\n\nChoose an intelligence tool:"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 AI Coin Analyzer", callback_data="menu_coin_analyzer")],
            [InlineKeyboardButton("🔥 Trending Memecoins", callback_data="menu_trending")],
            [InlineKeyboardButton("🌐 Network Analytics", callback_data="menu_network_analytics")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
        ])
        await edit_flow_message(query, context, text, reply_markup=keyboard)
        return MAIN_MENU
    if data == "menu_growth":
        text = "📢 GROWTH & MONETIZATION\n\nTools for promoting your project and building revenue through MARSOF AI."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Advertisement", callback_data="menu_advertisement")],
            [InlineKeyboardButton("💰 Earn Up to 35%", callback_data="menu_partner_promotion")],
            [InlineKeyboardButton("🔥 Volume Boost", callback_data="menu_volume_boost")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
        ])
        await edit_flow_message(query, context, text, reply_markup=keyboard)
        return MAIN_MENU
    if data == "menu_community_center":
        return await main_menu_handler(update, context)
    if data == "menu_project_tools":
        text = "🎨 PROJECT TOOLS\n\nBuild and brand your crypto project with MARSOF AI."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎨 AI Coin Logo Maker", callback_data="menu_logo_maker")],
            [InlineKeyboardButton("🌐 AI Website Builder", callback_data="menu_website_builder")],
            [InlineKeyboardButton("🪙 MARSOF AI Token", callback_data="menu_token")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
        ])
        await edit_flow_message(query, context, text, reply_markup=keyboard)
        return MAIN_MENU
    if data == "menu_language":
        return await show_language_selection(update, context)
    if data == "menu_token":
        text = (
            "🪙 MARSOF AI TOKEN\n\n"
            "MARSOF AI is developing a multi-network utility token designed to become part of the MARSOF AI ecosystem.\n\n"
            "🤖 Planned utility across MARSOF AI services\n"
            "🌐 Multi-network ecosystem\n"
            "💳 Planned future payment utility\n"
            "🔥 Planned buyback-and-burn mechanism using designated ecosystem fees/commissions\n\n"
            "The token has not launched yet. These are development plans, not current market claims.\n\n"
            "Follow MARSOF AI for official development updates."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Official Website", url=MARSOF_WEBSITE_URL)],
            [InlineKeyboardButton("𝕏 X / Twitter", url=MARSOF_X_URL)],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
        ])
        await edit_flow_message(query, context, text, reply_markup=keyboard)
        return MAIN_MENU
    if data == "menu_network_analytics":
        text = (
            "🌐 NETWORK ANALYTICS\n\n"
            "Compare supported networks using real market and on-chain data.\n\n"
            "📊 TVL\n💱 DEX Volume\n💰 Market Cap\n⚡ Network Activity\n🔥 Top Memecoins\n\n"
            "Free users can access raw network statistics and a simple recent liquidity-direction note.\n\n"
            "🧠 Deep AI Network Intelligence is available on eligible paid plans and is generated centrally rather than per user."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 View Plans", callback_data="menu_plans")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
        ])
        await edit_flow_message(query, context, text, reply_markup=keyboard)
        return MAIN_MENU
    if data == "menu_volume_boost":
        text = (
            "🔥 VOLUME BOOST\n\n"
            "🚧 Coming Soon\n\n"
            "MARSOF AI is preparing an automated volume campaign system "
            "for supported crypto networks.\n\n"
            "Stay tuned."
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")]])
        await edit_flow_message(query, context, text, reply_markup=keyboard)
        return MAIN_MENU

    if data == "menu_logo_maker":
        try:
            plans = await asyncio.to_thread(get_user_active_plan_keys, query.from_user.id)
        except Exception as exc:
            logging.warning("Could not read plan access for logo maker: %s", exc)
            plans = []
        normalized = {str(p).strip().lower() for p in plans}
        has_access = bool(normalized.intersection({"12_months", "annual", "yearly", "lifetime"}))
        if has_access:
            text = (
                "🎨 AI COIN LOGO MAKER\n\n"
                "🔓 Access granted.\n\n"
                "Create a professional visual identity for your token with MARSOF AI.\n"
                "✨ Describe your concept and generate a unique crypto logo.\n"
                "🪙 The generated logo can later be attached to your token campaign.\n\n"
                "⚙️ The full generation workflow will use the MARSOF AI logo-generation service."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎨 Start Logo Creation", callback_data="logo_maker_start")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
            ])
        else:
            text = (
                "🎨 AI COIN LOGO MAKER\n\n"
                "Create a professional visual identity for your token with MARSOF AI.\n\n"
                "✨ Generate a unique crypto logo from your idea\n"
                "🪙 Designed for your token/project\n"
                "⚡ Fast AI-powered generation\n\n"
                "🔐 This feature is available only on the Annual and Lifetime plans.\n"
                "Your current plan does not include access yet. Upgrade to unlock it."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 View Plans", callback_data="menu_plans")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
            ])
        await edit_flow_message(query, context, text, reply_markup=keyboard)
        return MAIN_MENU

    if data == "logo_maker_start":
        text = (
            "🎨 AI COIN LOGO MAKER\n\n"
            "Send a short description of the logo you want.\n\n"
            "Example: a futuristic wolf, dark background, blue and green neon, "
            "clean crypto identity."
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")]])
        await edit_flow_message(query, context, text, reply_markup=keyboard)
        return MAIN_MENU

    if data == "menu_website_builder":
        text = (
            "🌐 AI WEBSITE BUILDER\n\n"
            "Create a website for your crypto project in seconds.\n\n"
            "⚡ Build your token website in a few clicks\n"
            "🧠 AI-assisted creation\n"
            "🚀 Fast setup without complicated development\n\n"
            "Visit the official MARSOF AI website to start building."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Open MARSOF AI Website", url=MARSOF_WEBSITE_URL)],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
        ])
        await edit_flow_message(query, context, text, reply_markup=keyboard)
        return MAIN_MENU

    # Features being wired into the final build currently return a clear
    # screen instead of leaving the user at a dead end.
    if data == "menu_partner_promotion":
        text = (
            "💰 EARN UP TO 35%\n\n"
            "Turn your network into income with the MARSOF AI Partner Program.\n\n"
            "🔗 Get your personal referral link\n"
            "📊 Track referred users and qualifying sales\n"
            "💵 Earn up to 35% commission according to the program levels\n"
            "🧾 Qualifying sales are verified before commissions become payable.\n\n"
            "[Your personal referral dashboard will be connected in the next build step.]"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 My Referral Link", callback_data="referral_link")],
            [InlineKeyboardButton("📊 My Referral Stats", callback_data="referral_stats")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
        ])
        await edit_flow_message(query, context, text, reply_markup=keyboard)
        return MAIN_MENU

    if data == "menu_coin_analyzer":
        return await start_coin_analyzer(update, context)

    feature_titles = {
        "menu_advertisement": "📢 ADVERTISEMENT",
        "menu_community_management": "🛡️ COMMUNITY MANAGEMENT",
        "menu_analytics": "📊 COMMUNITY ANALYTICS",
        "menu_trending": "🔥 TRENDING MEMECOINS",
    }
    if data == "menu_raffle":
        return await show_buy_simulator(update, context)

    if data in ("buy_sim_settings", "buy_sim_create", "buy_sim_usage", "buy_sim_burst", "buy_sim_spread"):
        return await buy_simulator_subscreen(update, context)

    if data in feature_titles:
        text = (
            f"{feature_titles[data]}\n\n"
            "🚧 This module is being integrated into the MARSOF AI command center.\n\n"
            "The feature will be connected to its full settings and plan limits in the final build."
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")]])
        await edit_flow_message(query, context, text, reply_markup=keyboard)
        return MAIN_MENU

    return MAIN_MENU


BUY_SIMULATOR_LIMITS = {
    "free": {"daily": 3, "max_burst": 3, "bursts": 1},
    "1_month": {"daily": 10, "max_burst": 10, "bursts": 1},
    "6_months": {"daily": 50, "max_burst": 20, "bursts": 3},
    "12_months": {"daily": 80, "max_burst": 80, "bursts": 2},
    "annual": {"daily": 80, "max_burst": 80, "bursts": 2},
    "lifetime": {"daily": 400, "max_burst": 400, "bursts": 10},
}

async def show_buy_simulator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan = str(context.user_data.get('selected_plan', 'free')).lower()
    limits = BUY_SIMULATOR_LIMITS.get(plan, BUY_SIMULATOR_LIMITS['free'])
    text = (
        "🎁 ALERT BUY SIMULATOR\n\n"
        "This is a separate community tool. It is not part of AI Promoter settings.\n\n"
        "⚠️ All simulator alerts must remain clearly labeled as simulated and must not be presented as real blockchain transactions.\n\n"
        f"💳 Plan: {plan.upper()}\n"
        f"📦 Daily allowance: {limits['daily']} alerts\n"
        f"⚡ Burst: up to {limits['max_burst']} alerts per run\n"
        f"🔁 Maximum burst runs/day: {limits['bursts']}\n\n"
        "Choose how to manage the simulator:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Create Simulated Alert", callback_data="buy_sim_create")],
        [InlineKeyboardButton("⚙️ Simulator Settings", callback_data="buy_sim_settings")],
        [InlineKeyboardButton("📊 Today's Usage", callback_data="buy_sim_usage")],
        [InlineKeyboardButton("🔙 Community Control Center", callback_data="menu_community_center")],
    ])
    await edit_flow_message(query, context, text, reply_markup=keyboard)
    return EDIT_OPTIONS_MENU

async def buy_simulator_subscreen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan = str(context.user_data.get('selected_plan', 'free')).lower()
    limits = BUY_SIMULATOR_LIMITS.get(plan, BUY_SIMULATOR_LIMITS['free'])
    if query.data == 'buy_sim_settings':
        text = (
            "⚙️ SIMULATOR SETTINGS\n\n"
            "Choose the publication mode for clearly labeled simulated alerts.\n\n"
            "⚡ Burst — alerts are published with a 3-second interval.\n"
            "🕐 Spread — alerts are distributed across the day.\n\n"
            f"Your plan: {plan.upper()}\n"
            f"Daily limit: {limits['daily']}\n"
            f"Max per burst: {limits['max_burst']}\n"
            f"Max bursts/day: {limits['bursts']}"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Burst — 3 sec", callback_data="buy_sim_burst")],
            [InlineKeyboardButton("🕐 Spread Through Day", callback_data="buy_sim_spread")],
            [InlineKeyboardButton("🔙 Back", callback_data="menu_raffle")],
        ])
    elif query.data == 'buy_sim_create':
        text = (
            "⚡ CREATE SIMULATED ALERT\n\n"
            "The publishing engine is being integrated separately from the Promoter.\n\n"
            "When enabled, every alert will include a visible 'SIMULATED' label and the small MARSOF AI footer/buttons."
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_raffle")]])
    elif query.data == 'buy_sim_usage':
        text = (
            "📊 TODAY'S SIMULATOR USAGE\n\n"
            f"Plan allowance: {limits['daily']} alerts/day\n"
            "Used today: 0\n"
            f"Remaining: {limits['daily']}"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_raffle")]])
    else:
        text = "⚙️ Selected mode will be saved when the simulator publishing engine is connected."
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_raffle")]])
    await edit_flow_message(query, context, text, reply_markup=keyboard)
    return EDIT_OPTIONS_MENU

async def referral_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    link = f"{MARSOF_BOT_URL}?start=ref_{user_id}"
    text = (
        "💰 YOUR MARSOF AI REFERRAL LINK\n\n"
        f"{link}\n\n"
        "Share this link with people who may need MARSOF AI. Qualifying paid sales are verified before commissions are credited."
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_partner_promotion")]])
    await edit_flow_message(query, context, text, reply_markup=keyboard)
    return MAIN_MENU

async def referral_stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "📊 MARSOF AI PARTNER STATS\n\n"
        "Your referral dashboard will show: referrals, verified sales, pending sales, available commissions and your current commission level.\n\n"
        "The accounting layer will be connected to verified payments before launch."
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_partner_promotion")]])
    await edit_flow_message(query, context, text, reply_markup=keyboard)
    return MAIN_MENU


async def onboarding_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await show_public_main_menu(update, context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ultra-fast premium entrance. No DB, Gemini or remote work is awaited."""
    msg = (
        "👑 MARSOF AI\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 THE AI COMMAND CENTER FOR CRYPTO COMMUNITIES\n\n"
        "Turn your Telegram community into an intelligent, active ecosystem — "
        "even when you are away.\n\n"
        "⚡ AI-powered hype & content\n"
        "🧠 Intelligent replies to your community\n"
        "📊 Community engagement & behavior insights\n"
        "🚀 Automated promotion and activation\n"
        "🛠️ Advanced tools built for crypto projects\n\n"
        "MARSOF AI is evolving into a complete AI community operator. "
        "More powerful tools and affordable AI agents are on the way.\n\n"
        "Your project. Your community. Your AI operator."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ ENTER MARSOF AI", callback_data="enter_dashboard")],
        [
            InlineKeyboardButton("📢 Telegram", url=MARSOF_CHANNEL_URL),
            InlineKeyboardButton("𝕏 X / Twitter", url="https://x.com/MARSOF_AI"),
        ],
        [InlineKeyboardButton("🌐 Official Website", url="https://marsof.ct.ws")],
    ])

    if os.path.exists(MARSOF_LOGO_PATH):
        if update.callback_query:
            await send_flow_photo_reply(update, context, MARSOF_LOGO_PATH, msg, reply_markup=keyboard)
        else:
            await send_flow_photo_reply(update, context, MARSOF_LOGO_PATH, msg, reply_markup=keyboard)
    elif update.callback_query:
        await edit_flow_message(update.callback_query, context, msg, reply_markup=keyboard)
    else:
        await send_flow_reply(update, context, msg, reply_markup=keyboard)
    return MAIN_MENU


async def enter_dashboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enter the dashboard without touching PostgreSQL or Gemini."""
    query = update.callback_query
    await query.answer()
    return await show_public_main_menu(update, context)


async def show_faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """FAQ is deliberately local: no Gemini call, no API delay, no quota use."""
    text = (
        "❓ MARSOF AI — FAQ\n\n"
        "Choose a question. Every answer is generated locally for an instant response."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 What does MARSOF AI do?", callback_data="faq_what")],
        [InlineKeyboardButton("⚡ How does publishing work?", callback_data="faq_publish")],
        [InlineKeyboardButton("🧠 Does it use AI for members?", callback_data="faq_ai")],
        [InlineKeyboardButton("💳 What plans are available?", callback_data="faq_plans")],
        [InlineKeyboardButton("🔐 Is my project data protected?", callback_data="faq_data")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")],
    ])
    if update.callback_query:
        await edit_flow_message(update.callback_query, context, text, reply_markup=keyboard)
    else:
        await send_flow_reply(update, context, text, reply_markup=keyboard)
    return MAIN_MENU

FAQ_ANSWERS = {
    "faq_what": "🤖 MARSOF AI automates crypto community promotion, publishing, engagement, moderation and member assistance around your project.",
    "faq_publish": "⚡ Content is selected from the central content engine and published according to your campaign settings. Common project information is handled locally without AI calls.",
    "faq_ai": "🧠 Yes. When a member directly mentions or replies to the bot, MARSOF AI can use Gemini to generate a contextual answer. Common questions such as CA, Buy Link and X/Twitter are answered instantly without AI.",
    "faq_plans": "💳 MARSOF AI offers Free, $10, $50, $80, Annual and $170 Lifetime plans. Open View Plans to see the current offers.",
    "faq_data": "🔐 MARSOF AI uses the project settings you provide to operate your campaign. Avoid sending passwords, private keys, seed phrases or other sensitive credentials.",
}

async def faq_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "back_to_main":
        return await start(update, context)
    answer = FAQ_ANSWERS.get(query.data)
    if not answer:
        return await show_faq(update, context)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to FAQ", callback_data="menu_faq")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
    ])
    await edit_flow_message(query, context, answer, reply_markup=keyboard)
    return MAIN_MENU


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'menu_buy_new':
        return await show_subscription_plans(update, context)
    elif query.data == 'menu_faq':
        return await show_faq(update, context)
    elif query.data in ('menu_edit_existing', 'menu_community_center'):
        user_id = query.from_user.id
        channels = await asyncio.to_thread(get_user_channels, user_id)
        
        keyboard = []
        for ch, coin in channels:
            clean_ch = ch.replace('@', '')
            keyboard.append([InlineKeyboardButton(f"📢 {ch} ({coin})", callback_data=f"edit_ch_{clean_ch}")])
        
        if not keyboard:
            await edit_flow_message(
                query, context,
                "ℹ️ No active campaign was found for your account.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚀 Launch AI Promoter", callback_data="menu_launch")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
                ])
            )
            return MAIN_MENU

        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='back_to_main')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await edit_flow_message(query, context, "⚙️ Select the channel you want to edit:", reply_markup=reply_markup)
        return EDIT_SELECT_CHANNEL

async def show_edit_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    channel = data.get('channel', 'Channel')
    plan = data.get('selected_plan', 'free')
    
    posts_freq = "5 Posts/Day (Fixed)" if plan == 'free' else f"{data.get('msg_per_hour', 2)}/hour"

    text = (
        f"⚙️ Current Settings for {channel}:\n\n"
        f"💳 Plan: {plan.upper()}\n"
        f"1️⃣ Coin Name: {data.get('coin_name', 'Not set')}\n"
        f"2️⃣ Description: {data.get('coin_desc', 'Not set')}\n"
        f"3️⃣ Contract (CA): {data.get('contract', 'Not set')}\n"
        f"4️⃣ Buy Link: {data.get('buy_link', 'Not set')}\n"
        f"5️⃣ X / Twitter: {data.get('x_link', 'Not set')}\n"
        f"6️⃣ Network: {data.get('network', 'Not set')}\n"
        f"7️⃣ Geopolitical News: {'Yes' if data.get('receive_geopolitical_news', True) else 'No'}\n"
        f"8️⃣ Markets/Economy News: {'Yes' if data.get('receive_market_news', True) else 'No'}\n"
        f"9️⃣ Posts Frequency: {posts_freq}\n"
        f"🔟 Posts with Links Ratio: {data.get('link_ratio', 100)}%\n"
        "👇 Select an option below to update or cancel:"
    )

    keyboard = [
        [InlineKeyboardButton("🪙 Edit Coin Name", callback_data='opt_coin_name'), InlineKeyboardButton("📝 Edit Description", callback_data='opt_coin_desc')],
        [InlineKeyboardButton("📜 Edit Contract (CA)", callback_data='opt_contract'), InlineKeyboardButton("🛒 Edit Buy Link", callback_data='opt_buy_link')],
        [InlineKeyboardButton("🐦 Edit X / Twitter", callback_data='opt_x_link')],
        [InlineKeyboardButton("🌐 Edit Network", callback_data='opt_network')],
        [InlineKeyboardButton("🌍 Geopolitical News", callback_data='opt_geo_news')],
        [InlineKeyboardButton("📊 Markets/Economy News", callback_data='opt_market_news')],
        [InlineKeyboardButton("⏱️ Posts Frequency", callback_data='opt_msg_hour'), InlineKeyboardButton("📊 Posts with Links Ratio", callback_data='opt_ratio')],
        [InlineKeyboardButton("🔄 Re-configure All Settings", callback_data='opt_edit_all')],
        [InlineKeyboardButton("❌ Cancel / Stop Subscription", callback_data='opt_cancel_sub')],
        [InlineKeyboardButton("✅ Save & Exit Settings", callback_data='opt_save_finish')],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data='back_to_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await send_flow_reply(update, context, text, reply_markup=reply_markup)
    return EDIT_OPTIONS_MENU

async def show_community_control_center(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Per-community command center. Group-specific tools live here, not in the main menu."""
    channel = context.user_data.get('channel', 'Community')
    text = (
        f"🛡️ COMMUNITY CONTROL CENTER\n\n"
        f"Community: {channel}\n\n"
        "Manage the tools connected to this community."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Advertisement", callback_data="menu_advertisement")],
        [InlineKeyboardButton("🎁 Alert Buy Simulator", callback_data="menu_raffle")],
        [InlineKeyboardButton("🛡️ Community Management", callback_data="menu_community_management")],
        [InlineKeyboardButton("⚠️ Warnings & Moderation", callback_data="menu_community_management")],
        [InlineKeyboardButton("📊 Community Analytics", callback_data="menu_analytics")],
        [InlineKeyboardButton("💰 Earn From Your Community", callback_data="menu_partner_promotion")],
        [InlineKeyboardButton("⚙️ Promoter Settings", callback_data="community_promoter_settings")],
        [InlineKeyboardButton("🔙 My Communities", callback_data="menu_community_center")],
    ])
    await edit_flow_message(update.callback_query, context, text, reply_markup=keyboard)
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
        return await show_community_control_center(update, context)
    else:
        await edit_flow_message(query, context, "❌ Channel config not found.")
        return ConversationHandler.END

async def community_promoter_settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await show_edit_options(update, context)


async def edit_options_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'back_to_main':
        return await start(update, context)

    if data == 'opt_coin_name':
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await edit_flow_message(query, context, "1️⃣ Send your new Token / Coin Name (e.g., HIPPO):", reply_markup=keyboard)
        return COIN_NAME
    elif data == 'opt_coin_desc':
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await edit_flow_message(query, context, "2️⃣ Send a new Description / Hype Points for your token:", reply_markup=keyboard)
        return COIN_DESC
    elif data == 'opt_contract':
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await edit_flow_message(query, context, "3️⃣ Send your new Token Contract Address (CA):", reply_markup=keyboard)
        return CONTRACT
    elif data == 'opt_buy_link':
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await edit_flow_message(query, context, "4️⃣ Send your new DEXScreener or Buy Link:", reply_markup=keyboard)
        return BUY_LINK
    elif data == 'opt_x_link':
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Skip / Clear X Link", callback_data='xlink_skip_edit')], [InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await edit_flow_message(query, context, "5️⃣ Send your official X / Twitter link (e.g., https://x.com/yourproject):", reply_markup=keyboard)
        return X_LINK
    elif data == 'opt_network':
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await edit_flow_message(query, context, "6️⃣ Select the blockchain network / ecosystem of the token:", reply_markup=keyboard)
        return NETWORK
    elif data == 'opt_geo_news':
        keyboard=[[InlineKeyboardButton("Yes 🌍",callback_data='geo_edit_yes'),InlineKeyboardButton("No 🚫",callback_data='geo_edit_no')],[InlineKeyboardButton("🔙 Back",callback_data='back_to_edit_menu')]]
        await edit_flow_message(query, context, "Receive geopolitical news?",reply_markup=InlineKeyboardMarkup(keyboard))
        return CONTENT_PREFS
    elif data == 'opt_market_news':
        keyboard=[[InlineKeyboardButton("Yes 📊",callback_data='market_edit_yes'),InlineKeyboardButton("No 🚫",callback_data='market_edit_no')],[InlineKeyboardButton("🔙 Back",callback_data='back_to_edit_menu')]]
        await edit_flow_message(query, context, "Receive markets & economy news?",reply_markup=InlineKeyboardMarkup(keyboard))
        return CONTENT_PREFS
    elif data == 'opt_msg_hour':
        if context.user_data.get('selected_plan') == 'free':
            await query.answer("⚠️ Free Plan is restricted to 4 posts/day maximum. Upgrade to Premium!", show_alert=True)
            return EDIT_OPTIONS_MENU
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await edit_flow_message(query, context, "8️⃣ How many posts per hour do you want? (1 to 20):", reply_markup=keyboard)
        return MSG_PER_HOUR
    elif data == 'opt_ratio':
        keyboard = [
            [InlineKeyboardButton("0%", callback_data='ratio_0'), InlineKeyboardButton("25%", callback_data='ratio_25'), InlineKeyboardButton("50%", callback_data='ratio_50')],
            [InlineKeyboardButton("75%", callback_data='ratio_75'), InlineKeyboardButton("100%", callback_data='ratio_100')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]
        ]
        await edit_flow_message(query, context, "9️⃣ Select the percentage of posts that should include Buy Links & Contract:", reply_markup=InlineKeyboardMarkup(keyboard))
        return LINK_RATIO
    elif data == 'opt_new_buy':
        keyboard = [
            [InlineKeyboardButton("Yes 🚀 (Enable Buy Alerts)", callback_data='newbuy_yes')],
            [InlineKeyboardButton("No 🤖 (AI Posts Only)", callback_data='newbuy_no')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]
        ]
        await edit_flow_message(query, context, "🔟 Would you like to enable Simulated New Buy Alerts?", reply_markup=InlineKeyboardMarkup(keyboard))
        return ENABLE_NEW_BUY
    elif data == 'opt_edit_all':
        context.user_data['is_editing'] = False
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await edit_flow_message(query, context, "1️⃣ Send your new Token / Coin Name:", reply_markup=keyboard)
        return COIN_NAME
    elif data == 'opt_cancel_sub':
        channel = context.user_data.get('channel', '')
        keyboard = [
            [InlineKeyboardButton("YES, Stop Auto-Promoter 🛑", callback_data='confirm_cancel_yes')],
            [InlineKeyboardButton("NO, Keep Publishing 🚀", callback_data='confirm_cancel_no')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]
        ]
        await edit_flow_message(query, context, f"⚠️ Are you sure you want to cancel your campaign for {channel}?", reply_markup=InlineKeyboardMarkup(keyboard))
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

        await edit_flow_message(
            query, context,
            f"🛑 Subscription Cancelled! Auto-publishing for channel {channel} has been stopped.\n\n"
            "Your campaign is no longer publishing.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Launch AI Promoter", callback_data="menu_launch")],
                [InlineKeyboardButton("❓ FAQ", callback_data="menu_faq")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_main")],
            ])
        )
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
    
    await edit_flow_message(query, context, msg, reply_markup=keyboard)
    return COIN_NAME

async def back_to_edit_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await show_edit_options(update, context)

async def back_to_plans_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await show_subscription_plans(update, context)

def normalize_network_name(value):
    value = (value or "").strip()
    aliases = {
        "bnb": "BNB", "bnb chain": "BNB", "binance smart chain": "BNB", "bsc": "BNB",
        "sol": "Solana", "solana": "Solana",
        "sui": "Sui",
        "arc": "Arc",
        "robinhood": "Robinhood",
        "robinhood chain": "Robinhood",
    }
    return aliases.get(value.lower(), value)


async def get_coin_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['coin_name'] = update.message.text.strip()
    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_coin_name')]])
    await send_flow_reply(update, context, "2️⃣ Send a brief Description / Hype Points for your token:", reply_markup=keyboard)
    return COIN_DESC

async def get_coin_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['coin_desc'] = update.message.text.strip()
    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)
        
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_coin_desc')]])
    await send_flow_reply(update, context, "3️⃣ Send your Token Contract Address (CA):", reply_markup=keyboard)
    return CONTRACT

async def get_contract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['contract'] = update.message.text.strip()
    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)
        
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_contract')]])
    await send_flow_reply(update, context, "4️⃣ Send your DEXScreener or Buy Link:", reply_markup=keyboard)
    return BUY_LINK

async def get_buy_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['buy_link'] = update.message.text.strip()

    if context.user_data.get('is_editing'):
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Skip / Clear X Link", callback_data='xlink_skip_edit')], [InlineKeyboardButton("🔙 Back", callback_data='back_to_edit_menu')]])
        await send_flow_reply(update, context, "5️⃣ Official X / Twitter\n\nPaste your project X / Twitter link here (optional).", reply_markup=keyboard)
        return X_LINK

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Skip", callback_data='xlink_skip')], [InlineKeyboardButton("🔙 Back", callback_data='back_to_buy_link')]])
    await send_flow_reply(update, context, "5️⃣ Official X / Twitter\n\nPaste your project X / Twitter link here (optional).", reply_markup=keyboard)
    return X_LINK


async def get_x_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['x_link'] = update.message.text.strip()
    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)
    keyboard=[[InlineKeyboardButton("Yes 🌍",callback_data='geo_yes'),InlineKeyboardButton("No 🚫",callback_data='geo_no')],[InlineKeyboardButton("🔙 Back",callback_data='back_to_buy_link')]]
    await send_flow_reply(update, context, "🌍 Would you like to receive geopolitical news that may affect markets in this group?", reply_markup=InlineKeyboardMarkup(keyboard))
    return CONTENT_PREFS


async def x_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query
    await query.answer()
    if query.data in ('xlink_skip_edit',):
        context.user_data['x_link'] = ''
        return await show_edit_options(update, context)
    if query.data == 'xlink_skip':
        context.user_data['x_link'] = ''
        keyboard=[[InlineKeyboardButton("Yes 🌍",callback_data='geo_yes'),InlineKeyboardButton("No 🚫",callback_data='geo_no')],[InlineKeyboardButton("🔙 Back",callback_data='back_to_buy_link')]]
        await edit_flow_message(query, context, "🌍 Would you like to receive geopolitical news that may affect markets in this group?", reply_markup=InlineKeyboardMarkup(keyboard))
        return CONTENT_PREFS
    if query.data == 'back_to_buy_link':
        keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data='back_to_contract')]])
        await edit_flow_message(query, context, "4️⃣ Send your DEXScreener or Buy Link:", reply_markup=keyboard)
        return BUY_LINK
    return X_LINK

async def get_content_preferences(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query
    await query.answer()
    d=query.data
    if d in ('geo_yes','geo_edit_yes'):
        context.user_data['receive_geopolitical_news']=True
    elif d in ('geo_no','geo_edit_no'):
        context.user_data['receive_geopolitical_news']=False
    elif d in ('market_yes','market_edit_yes'):
        context.user_data['receive_market_news']=True
    elif d in ('market_no','market_edit_no'):
        context.user_data['receive_market_news']=False
    elif d=='back_to_edit_menu':
        return await show_edit_options(update, context)
    elif d=='back_to_buy_link':
        keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back",callback_data='back_to_content_prefs')]])
        await edit_flow_message(query, context, "4️⃣ Send your DEXScreener or Buy Link:",reply_markup=keyboard)
        return BUY_LINK
    elif d=='back_to_content_prefs':
        keyboard=[[InlineKeyboardButton("Yes 🌍",callback_data='geo_yes'),InlineKeyboardButton("No 🚫",callback_data='geo_no')],[InlineKeyboardButton("🔙 Back",callback_data='back_to_buy_link')]]
        await edit_flow_message(query, context, "🌍 Would you like to receive geopolitical news that may affect markets in this group?",reply_markup=InlineKeyboardMarkup(keyboard))
        return CONTENT_PREFS
    elif d=='back_to_market_pref':
        keyboard=[[InlineKeyboardButton("Yes 📊",callback_data='market_yes'),InlineKeyboardButton("No 🚫",callback_data='market_no')],[InlineKeyboardButton("🔙 Back",callback_data='back_to_content_prefs')]]
        await edit_flow_message(query, context, "📊 Would you like to receive markets & economy news?",reply_markup=InlineKeyboardMarkup(keyboard))
        return CONTENT_PREFS
    else:
        return CONTENT_PREFS
    if d in ('geo_edit_yes','geo_edit_no','market_edit_yes','market_edit_no'):
        return await finish_setup(update, context)
    if d in ('geo_yes','geo_no'):
        keyboard=[[InlineKeyboardButton("Yes 📊",callback_data='market_yes'),InlineKeyboardButton("No 🚫",callback_data='market_no')],[InlineKeyboardButton("🔙 Back",callback_data='back_to_content_prefs')]]
        await edit_flow_message(query, context, "📊 Would you like to receive markets & economy news in this group?\n\nExamples: interest rates, inflation, jobs and major economic data.",reply_markup=InlineKeyboardMarkup(keyboard))
        return CONTENT_PREFS
    keyboard = [
        [
            InlineKeyboardButton("🟡 BNB", callback_data='network_BNB'),
            InlineKeyboardButton("🟣 Solana", callback_data='network_Solana'),
        ],
        [
            InlineKeyboardButton("🔵 Sui", callback_data='network_Sui'),
            InlineKeyboardButton("⚫ Arc", callback_data='network_Arc'),
        ],
        [
            InlineKeyboardButton("🔴 Robinhood", callback_data='network_Robinhood'),
        ],
        [
            InlineKeyboardButton("⏭️ SKIP — All Networks", callback_data='network_SKIP'),
        ],
        [InlineKeyboardButton("🔙 Back", callback_data='back_to_market_pref')],
    ]
    await edit_flow_message(query, context, 
        "🌐 Blockchain network / ecosystem (optional).\n\n"
        "Select a network below. You can also choose SKIP; this will NOT block registration "
        "and the group will receive posts from all supported networks.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return NETWORK


async def get_network_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the six interactive network choices during setup/editing."""
    query = update.callback_query
    await query.answer()

    if query.data == 'back_to_market_pref':
        keyboard = [
            [InlineKeyboardButton("Yes 📊", callback_data='market_yes'),
             InlineKeyboardButton("No 🚫", callback_data='market_no')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_to_content_prefs')]
        ]
        await edit_flow_message(query, context, 
            "📊 Would you like to receive markets & economy news?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CONTENT_PREFS

    network_map = {
        'network_BNB': 'BNB',
        'network_Solana': 'Solana',
        'network_Sui': 'Sui',
        'network_Arc': 'Arc',
        'network_Robinhood': 'Robinhood',
        'network_SKIP': '',
    }

    selected = network_map.get(query.data)
    if selected is None:
        return NETWORK

    context.user_data['network'] = selected

    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_network_input')]])
    selected_label = selected if selected else 'All supported networks'
    await edit_flow_message(query, context, 
        f"✅ Network selected: {selected_label}\n\n"
        "7️⃣ Send your Channel Username (e.g., @mychannel) or Channel Link (e.g., https://t.me/mychannel):",
        reply_markup=keyboard
    )
    return CHANNEL


async def get_network(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backward-compatible text input for network selection."""
    raw_network = update.message.text.strip()
    context.user_data['network'] = '' if raw_network.lower() in {'skip','none','no','-','n/a','na'} else normalize_network_name(raw_network)

    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_network_input')]])
    await send_flow_reply(update, context, 
        "7️⃣ Send your Channel Username (e.g., @mychannel) or Channel Link (e.g., https://t.me/mychannel):",
        reply_markup=keyboard
    )
    return CHANNEL

async def get_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_channel = update.message.text.strip()
    formatted_channel = parse_channel_input(raw_channel)
    user_id = update.effective_user.id

    if not formatted_channel:
        await send_flow_reply(
            update, context,
            "❌ Invalid channel input.\n\nPlease send a public channel username such as @mychannel, a public t.me link, or a Telegram chat ID.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_network_input')]])
        )
        return CHANNEL

    # Validate the chat BEFORE moving to the administrator-verification step.
    # This prevents a wrong link/username from silently advancing the flow.
    try:
        chat = await asyncio.wait_for(
            context.bot.get_chat(chat_id=formatted_channel),
            timeout=8.0
        )
        chat_type = getattr(chat, 'type', '')
        if chat_type not in ('channel', 'group', 'supergroup'):
            raise ValueError('The supplied chat is not a Telegram channel/group.')
    except asyncio.TimeoutError:
        await send_flow_reply(
            update, context,
            "⏱️ Telegram did not respond in time while checking this chat.\n\nPlease verify the username/link and try again.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_network_input')]])
        )
        return CHANNEL
    except Exception as exc:
        logging.info("Invalid/unresolvable channel input %r: %s", raw_channel, exc)
        await send_flow_reply(
            update, context,
            "❌ I couldn't find or access this Telegram channel/group.\n\n"
            "Please check the username or link and send it again.\n"
            "Example: @mychannel or https://t.me/mychannel",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_network_input')]])
        )
        return CHANNEL

    existing_data = await asyncio.to_thread(get_user_channel_data, user_id, formatted_channel)
    if existing_data and existing_data['selected_plan'] != 'free' and context.user_data.get('selected_plan') == 'free':
        context.user_data['selected_plan'] = existing_data['selected_plan']

    context.user_data['channel'] = formatted_channel

    bot_username = context.bot.username or ""
    bot_username_display = f"@{bot_username}" if bot_username else "the bot"

    msg = (
        f"⚠️ IMPORTANT STEP: Admin Rights Required!\n\n"
        f"Telegram chat verified: `{formatted_channel}`\n\n"
        f"Please add this bot as an Administrator in your channel `{formatted_channel}` with Post Messages permission.\n\n"
        f"🤖 Bot to add: `{bot_username_display}`\n\n"
        "📋 Instructions:\n"
        "1. Open the bot profile using the button below.\n"
        "2. Go to your channel settings -> Administrators -> Add Admin.\n"
        "3. Select this bot and grant Post Messages permission.\n\n"
        "Then click Continue. The bot will verify its administrator status automatically."
    )
    keyboard = [
        [InlineKeyboardButton("🤖 Open Bot Profile", url=f"https://t.me/{bot_username}" if bot_username else "https://t.me")],
        [InlineKeyboardButton("🔗 I have promoted the bot / Continue", callback_data='verify_admin')],
        [InlineKeyboardButton("🔙 Back", callback_data='back_to_channel_input')]
    ]
    await send_flow_reply(update, context, msg, reply_markup=InlineKeyboardMarkup(keyboard))
    return VERIFY_ADMIN


async def verify_admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == 'back_to_channel_input':
        await query.answer()
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_buy_link')]])
        await edit_flow_message(query, context, 
            "7️⃣ Send your Channel Username or Link (e.g., @mychannel):",
            reply_markup=keyboard
        )
        return CHANNEL

    # Acknowledge the callback BEFORE the Telegram API verification call so the
    # button never appears to be frozen while get_chat_member is running.
    await query.answer("⏳ Checking administrator status...", show_alert=False)

    channel_id = context.user_data.get('channel')
    bot_id = context.bot.id

    if not channel_id:
        await edit_flow_message(query, context, 
            "❌ Channel information is missing. Please enter the channel again.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_channel_input')]])
        )
        return CHANNEL

    try:
        member = await context.bot.get_chat_member(chat_id=channel_id, user_id=bot_id)
        status = getattr(member, 'status', '')
        can_post = getattr(member, 'can_post_messages', None)

        if status == 'administrator' and (can_post is True or can_post is None):
            if context.user_data.get('selected_plan') == 'free':
                # Free uses a fixed 5-post/day model. Do not ask for paid-plan
                # frequency, link ratio, or Alert Buy Simulator settings.
                context.user_data['msg_per_hour'] = 0
                context.user_data['link_ratio'] = 0
                context.user_data['enable_new_buy'] = False
                return await finish_setup(update, context)

            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_verify_admin')]])
            await edit_flow_message(query, context, 
                "✅ Admin Status Verified!\n\n8️⃣ How many posts per hour do you want? (1 to 20):",
                reply_markup=keyboard
            )
            return MSG_PER_HOUR

        if status == 'administrator' and can_post is False:
            await query.answer(
                "❌ The bot is Admin, but Post Messages permission is disabled. Enable it and try again.",
                show_alert=True
            )
            return VERIFY_ADMIN

        await query.answer(
            "❌ The bot is not an Administrator of this channel yet.",
            show_alert=True
        )
        return VERIFY_ADMIN

    except Exception as e:
        logging.error(f"Admin verification failed for {channel_id}: {e}")
        await query.answer("❌ Could not access or verify this Telegram channel/group.", show_alert=False)
        await edit_flow_message(
            query,
            context,
            "❌ I couldn't find or access this Telegram channel/group.\n\n"
            "Please check the username or link and send it again.\n"
            "Example: @mychannel or https://t.me/mychannel",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data='back_to_channel_input')]
            ])
        )
        return CHANNEL

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
    await send_flow_reply(update, context, "9️⃣ What percentage of posts should contain Buy Links & Contract?", reply_markup=reply_markup)
    return LINK_RATIO

async def get_link_ratio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'back_to_verify_admin':
        bot_username = context.bot.username or ""
        bot_username_display = f"@{bot_username}" if bot_username else "the bot"
        channel = context.user_data.get('channel', 'your channel')
        msg = (
            f"⚠️ IMPORTANT STEP: Admin Rights Required!\n\n"
            f"Please add `{bot_username_display}` as an Administrator in your channel `{channel}` with Post Messages permission.\n\n"
            f"🤖 Bot: `{bot_username_display}`\n\n"
            "Then click Continue. The bot will verify its administrator status automatically."
        )
        keyboard = [
            [InlineKeyboardButton("🤖 Open Bot Profile", url=f"https://t.me/{bot_username}" if bot_username else "https://t.me")],
            [InlineKeyboardButton("🔗 I have promoted the bot / Continue", callback_data='verify_admin')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_to_channel_input')]
        ]
        await edit_flow_message(query, context, msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return VERIFY_ADMIN

    ratio = int(query.data.replace('ratio_', ''))
    context.user_data['link_ratio'] = ratio

    if context.user_data.get('is_editing'):
        return await show_edit_options(update, context)

    # Buy Simulator is deliberately isolated from Promoter onboarding for every plan.
    # The user configures it later from Community Control Center > Alert Buy Simulator.
    context.user_data['enable_new_buy'] = False
    return await finish_setup(update, context)

async def get_enable_new_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'back_to_link_ratio':
        keyboard = [
            [InlineKeyboardButton("0%", callback_data='ratio_0'), InlineKeyboardButton("25%", callback_data='ratio_25'), InlineKeyboardButton("50%", callback_data='ratio_50')],
            [InlineKeyboardButton("75%", callback_data='ratio_75'), InlineKeyboardButton("100%", callback_data='ratio_100')]
        ]
        await edit_flow_message(query, context, "9️⃣ What percentage of posts should contain Buy Links & Contract?", reply_markup=InlineKeyboardMarkup(keyboard))
        return LINK_RATIO

    if query.data not in ('newbuy_yes', 'newbuy_no'):
        return ENABLE_NEW_BUY

    enable_buy = (query.data == 'newbuy_yes')
    context.user_data['enable_new_buy'] = enable_buy
    await query.answer("✅ Simulated Buy Alerts enabled" if enable_buy else "ℹ️ Simulated Buy Alerts disabled", show_alert=False)

    user_id = query.from_user.id
    username = query.from_user.username or "Unknown"

    save_user_data(user_id, username, context.user_data)
    await restart_all_active_tasks(context.application)

    channel = context.user_data.get('channel')
    plan = context.user_data.get('selected_plan', 'free')

    success_text = (
        f"🎉 SUCCESS! Auto-Promoter is now active for {channel}!\n\n"
        f"🪙 Token: {context.user_data.get('coin_name')}\n"
        f"💳 Plan: {plan.upper()}\n"
        f"⏱️ Frequency: {'5 posts/day' if plan == 'free' else str(context.user_data.get('msg_per_hour', 2)) + ' posts/hour'}\n"
        f"📊 Links Ratio: {context.user_data.get('link_ratio', 100)}%\n"
        f"🚀 Buy Alerts: {'Enabled' if enable_buy else 'Disabled'}\n\n"
        f"Your automated crypto growth campaign has started! 🚀"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Edit Settings / Manage", callback_data='menu_edit_existing')],
        [InlineKeyboardButton("🏠 Main Menu", callback_data='back_to_main')]
    ])

    await edit_flow_message(query, context, success_text, reply_markup=keyboard)
    context.user_data.clear()
    return ConversationHandler.END

async def finish_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    username = query.from_user.username or "Unknown"
    
    save_user_data(user_id, username, context.user_data)
    await restart_all_active_tasks(context.application)
    
    await edit_flow_message(query, context, "✅ Settings updated and saved successfully! 🚀", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data='back_to_main')]
    ]))
    context.user_data.clear()
    return ConversationHandler.END

# ==========================================
# SUBSCRIPTION & EXPIRATION ENGINE
# ==========================================

async def subscription_expiration_engine(application):
    """Check subscriptions in the background and automatically move expired paid plans to Free."""
    while True:
        try:
            expired = await asyncio.to_thread(get_expired_subscriptions)
            for item in expired:
                result = await asyncio.to_thread(
                    transition_expired_subscription,
                    item["user_id"], item["channel"]
                )
                if not result:
                    continue

                user_id = item["user_id"]
                channel = item["channel"]
                old_plan = result["old_plan"]
                upgrade_text = (
                    "⏳ MARSOF AI SUBSCRIPTION EXPIRED\n\n"
                    f"Your {old_plan.replace('_', ' ').title()} plan for {channel} has expired.\n\n"
                    "Your community has automatically been moved to the Free plan. "
                    "Your project configuration was preserved.\n\n"
                    "🆓 Free includes 5 AI posts/day.\n"
                    "💎 Upgrade again anytime to restore paid features and higher limits."
                )
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💎 Upgrade Plan", callback_data="subscription_upgrade")],
                    [InlineKeyboardButton("🏠 Open MARSOF AI", callback_data="back_to_main")],
                ])
                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=upgrade_text,
                        reply_markup=keyboard,
                    )
                except Exception as exc:
                    logging.warning("Could not notify expired subscription owner %s: %s", user_id, exc)

                # One transition message in the managed community; never DM members.
                try:
                    await application.bot.send_message(
                        chat_id=channel,
                        text=(
                            "🛡️ This community is now supported by MARSOF AI — Free.\n\n"
                            "MARSOF AI continues to provide AI-powered community support and automation.\n"
                            "🚀 Learn more: https://marsof.ct.ws/"
                        ),
                    )
                    await asyncio.to_thread(mark_free_transition_announced, user_id, channel)
                except Exception as exc:
                    logging.info("Free transition community announcement skipped for %s: %s", channel, exc)

                # Refresh publishers so the channel immediately uses Free-plan limits.
                try:
                    await restart_all_active_tasks(application)
                except Exception as exc:
                    logging.warning("Publisher refresh after expiration failed: %s", exc)

        except Exception as exc:
            logging.error("Subscription expiration engine error: %s", exc)
        await asyncio.sleep(60)

async def subscription_upgrade_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await show_subscription_plans(update, context, launch_mode=False)


# ==========================================
# OFFICIAL MARSOF AI CONTENT ENGINE — KEY 21
# ==========================================
MARSOF_OFFICIAL_TOPICS = [
    "a MARSOF AI feature or development update",
    "the broader MARSOF AI platform and its crypto intelligence tools",
    "Network Analytics and multi-network intelligence",
    "AI Coin Analyzer and objective token research",
    "AI Coin Logo Maker / AI tools for crypto projects",
    "AI Website Builder for token projects",
    "community automation and moderation",
    "the MARSOF AI Partner Program and ecosystem growth",
    "the planned MARSOF AI multi-network utility token",
    "the long-term MARSOF AI ecosystem roadmap",
]

async def generate_official_daily_batch():
    """Generate five fresh official posts in one Gemini-21 call per day."""
    existing = await asyncio.to_thread(get_official_daily_content)
    if len(existing) >= MARSOF_OFFICIAL_POSTS_PER_DAY:
        return existing
    client = get_official_gemini_client()
    if not client:
        logging.warning("GEMINI_API_KEY_21 is not configured; official engine remains dormant.")
        return existing

    prompt = f"""
You are the official content strategist for MARSOF AI.
Generate exactly 5 distinct daily social posts as valid JSON only.
Schema: [{{\"topic\":\"...\",\"content\":\"...\",\"telegram_content\":\"...\",\"x_content\":\"...\"}}]

MARSOF AI is a developing full crypto-AI platform, not merely a Telegram bot.
It includes community management, community analytics, network analytics, AI Coin Analyzer,
Trending Memecoins, AI Coin Logo Maker, AI Website Builder, promotion automation and a partner program.
The platform is developing a future multi-network MARSOF AI utility token. The token has NOT launched yet.
Planned future utility includes payment use across MARSOF AI services. A planned buyback-and-burn mechanism
may use designated ecosystem fees/commissions to purchase and burn the token to reduce supply.
Do NOT present planned features as live. Do NOT invent listings, partnerships, launches, prices, users,
transactions, revenue, token supply, exchange availability or performance. Do NOT promise profit or price appreciation.
Do not call the token a meme coin.

Create varied, natural crypto-native content. Mention real existing platform capabilities accurately.
Future token content must use language such as planned, being developed, roadmap, or future utility.
Each post should be useful or interesting, not just an advertisement.
Telegram version: 3-7 short lines, readable with emojis.
X version: concise, natural, within normal X post length.
English only. No markdown tables.
"""
    try:
        response = await asyncio.to_thread(client.models.generate_content, model="gemini-3.6-flash", contents=prompt)
        items = _extract_json_posts(response.text if response else "")
        valid = []
        for item in items[:MARSOF_OFFICIAL_POSTS_PER_DAY]:
            if isinstance(item, dict) and item.get("content"):
                valid.append({
                    "topic": str(item.get("topic", "MARSOF AI")),
                    "content": str(item["content"]).strip(),
                    "telegram_content": str(item.get("telegram_content", item["content"])).strip(),
                    "x_content": str(item.get("x_content", item["content"])).strip(),
                })
        if len(valid) != MARSOF_OFFICIAL_POSTS_PER_DAY:
            logging.warning("Official daily batch was incomplete: %s/5", len(valid))
            return existing
        await asyncio.to_thread(save_official_daily_content, valid)
        return await asyncio.to_thread(get_official_daily_content)
    except Exception as e:
        logging.error("Official Gemini-21 daily generation failed: %s", e)
        return existing

async def publish_official_telegram_posts(application, items):
    for item in items:
        text = item.get("telegram_content") or item.get("content", "")
        if not text:
            continue
        try:
            msg = await application.bot.send_message(chat_id=MARSOF_OFFICIAL_CHANNEL, text=text)
            await asyncio.to_thread(mark_official_publication, item["id"], "telegram_channel", MARSOF_OFFICIAL_CHANNEL, msg.message_id, "sent", "")
            if MARSOF_OFFICIAL_COMMUNITY_ID:
                cmsg = await application.bot.send_message(chat_id=MARSOF_OFFICIAL_COMMUNITY_ID, text=text)
                await asyncio.to_thread(mark_official_publication, item["id"], "telegram_community", MARSOF_OFFICIAL_COMMUNITY_ID, cmsg.message_id, "sent", "")
        except Exception as e:
            logging.error("Official Telegram publication failed: %s", e)
            await asyncio.to_thread(mark_official_publication, item["id"], "telegram", MARSOF_OFFICIAL_CHANNEL, "", "failed", str(e)[:500])

async def official_x_post(text, content_id=None):
    """Publish to X when user-context OAuth credentials are configured."""
    token = os.getenv("X_ACCESS_TOKEN", "").strip()
    if not token:
        logging.info("X_ACCESS_TOKEN not configured; skipping official X publication.")
        return False
    try:
        import requests
        response = await asyncio.to_thread(requests.post, "https://api.x.com/2/tweets", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json={"text": text}, timeout=20)
        if response.status_code >= 300:
            raise RuntimeError(f"X API {response.status_code}: {response.text[:400]}")
        payload = response.json()
        tweet_id = payload.get("data", {}).get("id", "")
        if content_id:
            await asyncio.to_thread(mark_official_publication, content_id, "x", "MARSOF_AI", tweet_id, "sent", "")
        return True
    except Exception as e:
        logging.error("Official X publication failed: %s", e)
        if content_id:
            await asyncio.to_thread(mark_official_publication, content_id, "x", "MARSOF_AI", "", "failed", str(e)[:500])
        return False

async def generate_official_reply(member_name, member_message, platform="Telegram"):
    client = get_official_gemini_client()
    if not client:
        return None
    prompt = f"""
You are the official MARSOF AI community representative on {platform}.
Reply naturally to this real community member.
Member: {member_name}
Message: {member_message}

MARSOF AI is a full crypto-AI platform with community management, analytics,
network intelligence, AI Coin Analyzer, Trending Memecoins, AI Coin Logo Maker,
AI Website Builder and promotion tools. Its future multi-network utility token
is still under development and has not launched. Do not invent facts or promise
profit. Future token utility must be described as planned/development only.
Answer the actual question. Be concise, useful, friendly and non-spammy.
English only. Output only the reply.
"""
    try:
        response = await asyncio.to_thread(client.models.generate_content, model="gemini-3.6-flash", contents=prompt)
        return response.text.strip() if response and response.text else None
    except Exception as e:
        logging.error("Official reply generation failed: %s", e)
        return None

async def official_telegram_reply_engine(application):
    global OFFICIAL_TELEGRAM_REPLY_COUNT, OFFICIAL_X_REPLY_COUNT, OFFICIAL_REPLY_COUNT_DATE
    while True:
        try:
            from datetime import date
            today = date.today().isoformat()
            if OFFICIAL_REPLY_COUNT_DATE != today:
                OFFICIAL_REPLY_COUNT_DATE = today
                OFFICIAL_TELEGRAM_REPLY_COUNT = 0
                OFFICIAL_X_REPLY_COUNT = 0
            if MARSOF_OFFICIAL_COMMUNITY_ID:
                # Reset daily quota using local process date.
                from datetime import date
                # Keep count in process; restart naturally resets it.
                candidates = list(OFFICIAL_TELEGRAM_CANDIDATES)
                sent_today = OFFICIAL_TELEGRAM_REPLY_COUNT
                if sent_today < MARSOF_OFFICIAL_COMMUNITY_REPLIES_PER_DAY and candidates:
                    candidate = candidates[0]
                    reply = await generate_official_reply(candidate["name"], candidate["text"], "Telegram")
                    if reply:
                        try:
                            await application.bot.send_message(chat_id=MARSOF_OFFICIAL_COMMUNITY_ID, text=reply, reply_to_message_id=candidate["message_id"])
                            await asyncio.to_thread(save_official_reply, "telegram_community", candidate["message_id"], reply, "sent")
                            OFFICIAL_TELEGRAM_REPLY_COUNT += 1
                            try:
                                OFFICIAL_TELEGRAM_CANDIDATES.remove(candidate)
                            except ValueError:
                                pass
                        except Exception as e:
                            logging.error("Official Telegram reply send failed: %s", e)
        except Exception as e:
            logging.error("Official Telegram reply engine error: %s", e)
        await asyncio.sleep(30 * 60)

async def official_x_reply_engine(application):
    global OFFICIAL_X_REPLY_COUNT, OFFICIAL_REPLY_COUNT_DATE
    while True:
        try:
            from datetime import date
            today = date.today().isoformat()
            if OFFICIAL_REPLY_COUNT_DATE != today:
                OFFICIAL_REPLY_COUNT_DATE = today
                OFFICIAL_X_REPLY_COUNT = 0
                OFFICIAL_TELEGRAM_REPLY_COUNT = 0

            bearer = os.getenv("X_BEARER_TOKEN", "").strip()
            access = os.getenv("X_ACCESS_TOKEN", "").strip()
            if bearer and access and OFFICIAL_X_REPLY_COUNT < MARSOF_OFFICIAL_X_REPLIES_PER_DAY:
                import requests
                query = "@MARSOF_AI -is:retweet"
                response = await asyncio.to_thread(requests.get, "https://api.x.com/2/tweets/search/recent", headers={"Authorization": f"Bearer {bearer}"}, params={"query": query, "max_results": 10, "tweet.fields": "author_id,conversation_id,text"}, timeout=20)
                if response.status_code < 300:
                    tweets = response.json().get("data", [])
                    for tweet in tweets:
                        if OFFICIAL_X_REPLY_COUNT >= MARSOF_OFFICIAL_X_REPLIES_PER_DAY:
                            break
                        reply = await generate_official_reply("X user", tweet.get("text", ""), "X")
                        if not reply:
                            continue
                        post = await asyncio.to_thread(requests.post, "https://api.x.com/2/tweets", headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"}, json={"text": reply, "reply": {"in_reply_to_tweet_id": tweet["id"]}}, timeout=20)
                        if post.status_code < 300:
                            await asyncio.to_thread(save_official_reply, "x", tweet["id"], reply, "sent")
                            OFFICIAL_X_REPLY_COUNT += 1
        except Exception as e:
            logging.error("Official X reply engine error: %s", e)
        await asyncio.sleep(60 * 30)

async def official_content_engine(application):
    """Generate five posts once per day and distribute them across the day."""
    await asyncio.sleep(10)
    while True:
        day_started = asyncio.get_running_loop().time()
        try:
            items = await generate_official_daily_batch()
            published_tg = await asyncio.to_thread(get_official_published_content_ids, "telegram_channel")
            for index, item in enumerate(items[:MARSOF_OFFICIAL_POSTS_PER_DAY]):
                # Spread the five daily posts instead of dumping them at once.
                # If Render restarts, already published items are skipped.
                if item.get("id") in published_tg:
                    continue
                if index > 0:
                    await asyncio.sleep((24 * 3600) / MARSOF_OFFICIAL_POSTS_PER_DAY)
                text = item.get("telegram_content") or item.get("content", "")
                if not text:
                    continue
                try:
                    msg = await application.bot.send_message(chat_id=MARSOF_OFFICIAL_CHANNEL, text=text)
                    await asyncio.to_thread(mark_official_publication, item["id"], "telegram_channel", MARSOF_OFFICIAL_CHANNEL, msg.message_id, "sent", "")
                    if MARSOF_OFFICIAL_COMMUNITY_ID:
                        cmsg = await application.bot.send_message(chat_id=MARSOF_OFFICIAL_COMMUNITY_ID, text=text)
                        await asyncio.to_thread(mark_official_publication, item["id"], "telegram_community", MARSOF_OFFICIAL_COMMUNITY_ID, cmsg.message_id, "sent", "")
                    await official_x_post(item.get("x_content") or item.get("content", ""), item.get("id"))
                except Exception as e:
                    logging.error("Official distributed publication failed: %s", e)
        except Exception as e:
            logging.error("Official content engine loop error: %s", e)
        elapsed = asyncio.get_running_loop().time() - day_started
        await asyncio.sleep(max(60, 24 * 3600 - elapsed))


async def background_publisher(application, user_data):
    channel = user_data['channel']
    user_id = user_data['user_id']

    while True:
        try:
            current_data = await asyncio.to_thread(get_user_channel_data, user_id, channel)
            if not current_data or current_data.get('subscription_status') == 'cancelled':
                break

            await ensure_daily_content_pool()

            if current_data.get('selected_plan') == 'free':
                daily_count = await asyncio.to_thread(get_free_daily_post_count, user_id, channel)
                if daily_count >= 5:
                    # Five successful posts have already been published today.
                    # Recheck after roughly one hour; the date rollover will make
                    # the next post eligible without hammering PostgreSQL.
                    await asyncio.sleep(3600)
                    continue

                post_text, ad_mode = generate_post(current_data, free_slot_index=daily_count)
                try:
                    markup = FREE_PLAN_AD_BUTTONS if ad_mode in ('ad', 'full_ad') else None
                    await application.bot.send_message(
                        chat_id=channel,
                        text=post_text,
                        reply_markup=markup,
                        parse_mode='Markdown'
                    )
                    await asyncio.to_thread(record_free_daily_post, user_id, channel)
                except Exception:
                    raise

                # Five posts spread across 24h ~= 4h48m.
                await asyncio.sleep((24 * 3600) / 5)
            else:
                post_text, _ = generate_post(current_data)
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


async def restart_all_active_tasks(application):
    """Restart publishers without blocking Telegram's event loop on PostgreSQL."""
    global ACTIVE_PUBLISH_TASKS

    for task in ACTIVE_PUBLISH_TASKS.values():
        task.cancel()
    ACTIVE_PUBLISH_TASKS.clear()

    # Supabase/PostgreSQL can take seconds or minutes when the connection is
    # slow. Never execute this synchronous DB call directly in the event loop.
    active_users = await asyncio.to_thread(get_active_users)

    for u_data in active_users:
        channel = u_data['channel']
        if channel not in ACTIVE_PUBLISH_TASKS:
            task = asyncio.create_task(background_publisher(application, u_data))
            ACTIVE_PUBLISH_TASKS[channel] = task

async def post_init(application):
    # Startup must NEVER wait for PostgreSQL or Gemini. Both jobs run in the
    # background so /start and callback buttons remain responsive immediately.
    application.create_task(restart_all_active_tasks(application))
    application.create_task(ensure_daily_content_pool())
    application.create_task(official_content_engine(application))
    application.create_task(official_telegram_reply_engine(application))
    application.create_task(official_x_reply_engine(application))
    application.create_task(subscription_expiration_engine(application))
    logging.info("Bot initialized; publishers/content pool/official MARSOF AI engine are running in the background.")

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
            CallbackQueryHandler(enter_dashboard_handler, pattern='^back_to_main$'),
            CallbackQueryHandler(enter_dashboard_handler, pattern='^enter_dashboard$'),
            CallbackQueryHandler(main_menu_handler, pattern='^menu_edit_existing$'),
            CallbackQueryHandler(faq_handler, pattern='^faq_'),
            CallbackQueryHandler(show_faq, pattern='^menu_faq$'),
            CallbackQueryHandler(public_menu_handler, pattern='^(menu_launch|menu_plans|menu_language|menu_intelligence|menu_growth|menu_community_center|menu_project_tools|menu_volume_boost|menu_advertisement|menu_community_management|menu_partner_promotion|menu_raffle|menu_analytics|menu_coin_analyzer|menu_trending|menu_network_analytics|menu_token|menu_logo_maker|logo_maker_start|menu_website_builder)$'),
            CallbackQueryHandler(plan_selected, pattern='^plan_'),
            CallbackQueryHandler(referral_link_handler, pattern='^referral_link$'),
            CallbackQueryHandler(referral_stats_handler, pattern='^referral_stats$'),
            CallbackQueryHandler(subscription_upgrade_handler, pattern='^subscription_upgrade$'),
        ],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(faq_handler, pattern='^faq_'),
                CallbackQueryHandler(show_faq, pattern='^menu_faq$'),
                CallbackQueryHandler(referral_link_handler, pattern='^referral_link$'),
                CallbackQueryHandler(referral_stats_handler, pattern='^referral_stats$'),
                CallbackQueryHandler(subscription_upgrade_handler, pattern='^subscription_upgrade$'),
                CallbackQueryHandler(public_menu_handler, pattern='^(menu_launch|menu_plans|menu_language|menu_intelligence|menu_growth|menu_community_center|menu_project_tools|menu_volume_boost|menu_advertisement|menu_community_management|menu_partner_promotion|menu_raffle|menu_analytics|menu_coin_analyzer|menu_trending|menu_network_analytics|menu_token|menu_logo_maker|logo_maker_start|menu_website_builder)$'),
                CallbackQueryHandler(start_coin_analyzer, pattern='^menu_coin_analyzer$'),
                CallbackQueryHandler(analyzer_network_selected, pattern='^analyzer_chain_'),
                CallbackQueryHandler(main_menu_handler, pattern='^(menu_buy_new|menu_edit_existing)$'),
                CallbackQueryHandler(enter_dashboard_handler, pattern='^back_to_main$'),
                CallbackQueryHandler(enter_dashboard_handler, pattern='^enter_dashboard$')
            ],
            ANALYZER_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, coin_analyzer_input),
                CallbackQueryHandler(start, pattern='^back_to_main$'),
            ],
            ANALYZER_NETWORK: [
                CallbackQueryHandler(analyzer_network_selected, pattern='^analyzer_chain_'),
                CallbackQueryHandler(start, pattern='^back_to_main$'),
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
            X_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_x_link),
                CallbackQueryHandler(x_link_callback, pattern='^(xlink_.*|back_to_buy_link)$'),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$')
            ],
            CONTENT_PREFS: [
                CallbackQueryHandler(get_content_preferences, pattern='^(geo_|market_|back_to_content_prefs|back_to_market_pref|back_to_edit_menu|back_to_buy_link)')
            ],
            NETWORK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_network),
                CallbackQueryHandler(get_network_choice, pattern='^network_(BNB|Solana|Sui|Arc|Robinhood|SKIP)$'),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$'),
                CallbackQueryHandler(get_content_preferences, pattern='^back_to_market_pref$')
            ],
            CHANNEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_channel),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$'),
                CallbackQueryHandler(get_network, pattern='^back_to_channel_input$')
            ],
            VERIFY_ADMIN: [
                CallbackQueryHandler(verify_admin_status, pattern='^(verify_admin|back_to_channel_input)$')
            ],
            EDIT_SELECT_CHANNEL: [
                CallbackQueryHandler(select_channel_to_edit, pattern='^edit_ch_'),
                CallbackQueryHandler(community_promoter_settings_handler, pattern='^community_promoter_settings$'),
                CallbackQueryHandler(start, pattern='^back_to_main$')
            ],
            EDIT_OPTIONS_MENU: [
                CallbackQueryHandler(public_menu_handler, pattern='^(menu_advertisement|menu_raffle|menu_community_management|menu_analytics|menu_partner_promotion|menu_community_center|menu_launch|menu_plans|menu_intelligence|menu_growth|menu_project_tools|menu_coin_analyzer|menu_trending|menu_network_analytics|menu_token|menu_logo_maker|logo_maker_start|menu_website_builder|menu_volume_boost|buy_sim_settings|buy_sim_create|buy_sim_usage|buy_sim_burst|buy_sim_spread)$'),
                CallbackQueryHandler(community_promoter_settings_handler, pattern='^community_promoter_settings$'),
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
            CONFIRM_CANCEL_SUB: [
                CallbackQueryHandler(confirm_cancel_sub_handler, pattern='^(confirm_cancel_|back_to_edit_menu)'),
                CallbackQueryHandler(show_edit_options, pattern='^confirm_cancel_no$')
            ]
        },
        fallbacks=[
            CallbackQueryHandler(menu_home_handler, pattern='^menu_home$'),
            CommandHandler('cancel', lambda u, c: c.application.create_task(start(u, c)))
        ],
        per_user=True
    )

    application.add_handler(conv_handler)

    # Track private setup messages without consuming them. This lets the Menu
    # button perform best-effort cleanup of the current setup flow.
    application.add_handler(CallbackQueryHandler(flow_message_tracker, pattern='.*', block=False), group=-1)
    application.add_handler(MessageHandler(filters.ALL, flow_message_tracker, block=False), group=-1)
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

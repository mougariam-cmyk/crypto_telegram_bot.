import random

FALLBACK_TEMPLATES = [
    # --- GM / Morning Vibe Templates (1-10) ---
    "☀️ **GM {coin_name} FAM!** ☀️\n\nAnother day, another step closer to the moon! 🚀\nGrab your coffee and secure your bags! ☕️💰\n\n🛒 **Buy:** {buy_link}\n📜 **CA:** `{contract}`\n📢 {channel}",
    "🌅 **GM SHIBAS & BULLS!** 🌅\n\nReady for a huge green day with {coin_name}? 🟢🔥\nLet's keep the momentum pushing! 💪\n\n🛒 **Trade:** {buy_link}\n📜 **CA:** `{contract}`",
    "☀️ **GOOD MORNING LEGENDS!** 💎\n\n{coin_name} is awake and ready to pump! 🔥\nWho's holding with diamond hands today? 💎🙌\n\n🛒 **Buy:** {buy_link}\n📜 **CA:** `{contract}`",
    "☕️ **GM! FRESH COFFEE & GREEN CANDLES!** 🟢\n\nNothing beats waking up to {coin_name} pumping! 🚀\n\n🛒 **Get in:** {buy_link}\n📜 **CA:** `{contract}`\n📢 {channel}",
    "🌅 **GM CHADS!** 🗿\n\nDo not sleep on {coin_name}! The chart is looking extremely primed. 📈🔥\n\n🛒 **Buy Link:** {buy_link}\n📜 **CA:** `{contract}`",
    "☀️ **GM FUTURE MILLIONAIRES!** 💸\n\nKeep spreading the word about {coin_name}! We are taking over! 🌐🚀\n\n🛒 **DEXScreener:** {buy_link}\n📜 **CA:** `{contract}`",
    "🌅 **GM! RISE AND GRIND!** ⚡️\n\n{coin_name} is making big moves today! Are you loaded up? 🎒💰\n\n🛒 **Buy Now:** {buy_link}\n📜 **CA:** `{contract}`",
    "☕️ **GM ARMY!** 🛡\n\nStand strong, hold tight, and watch {coin_name} fly! 🚀💥\n\n🛒 **Trade Here:** {buy_link}\n📜 **CA:** `{contract}`",
    "☀️ **GM! THE BULLS ARE HUNGRY!** 🐂\n\nEats dips for breakfast! {coin_name} is loading up! 🍽🔥\n\n🛒 **Buy:** {buy_link}\n📜 **CA:** `{contract}`",
    "🌅 **GM WORLD!** 🌍\n\n{coin_name} is officially trending in our hearts and on the charts! 📈💖\n\n🛒 **Buy Link:** {buy_link}\n📜 **CA:** `{contract}`",

    # --- TO THE MOON / PUMP HYPE (11-25) ---
    "🚀 **TO THE MOOOOOON!** 🚀\n\n{coin_name} is preparing for absolute liftoff! 🌕🔥\nPack your bags before it's too late! 🎒\n\n🛒 **Buy:** {buy_link}\n📜 **CA:** `{contract}`",
    "🔥 **GO GO GO!** 🔥\n\nThe volume on {coin_name} is surging! Don't get left behind! 📈⚡️\n\n🛒 **DEXScreener:** {buy_link}\n📜 **CA:** `{contract}`\n📢 {channel}",
    "🌕 **DESTINATION: MOON!** 🛸\n\n{coin_name} rocket engines are officially ignited! 🚀💥\n\n🛒 **Buy Now:** {buy_link}\n📜 **CA:** `{contract}`",
    "🚀 **NEXT STOP: 100X!** 💎\n\n{coin_name} chart is screaming BULLISH! 🟢📈\n\n🛒 **Trade:** {buy_link}\n📜 **CA:** `{contract}`",
    "💥 **GO GO GO! PUMP IT UP!** 💥\n\n{coin_name} is breaking resistance levels! 🔥🚀\n\n🛒 **Buy Link:** {buy_link}\n📜 **CA:** `{contract}`",
    "🛸 **WE ARE GOING PARABOLIC!** 📈\n\n{coin_name} is showing unmatched strength right now! 🔥\n\n🛒 **Get in:** {buy_link}\n📜 **CA:** `{contract}`",
    "🚀 **MOONBOUND EXPRESS!** 🚂💨\n\nAll aboard {coin_name}! Next stop: Valhalla! 🛡💎\n\n🛒 **Buy:** {buy_link}\n📜 **CA:** `{contract}`",
    "⚡️ **FULL SEND MODE!** ⚡️\n\n{coin_name} is not stopping for anyone! GO GO GO! 🚀🔥\n\n🛒 **DEXScreener:** {buy_link}\n📜 **CA:** `{contract}`",
    "🔥 **BULL RUN CONFIRMED!** 🐂\n\n{coin_name} is leading the market explosion! 📈💥\n\n🛒 **Buy Now:** {buy_link}\n📜 **CA:** `{contract}`",
    "🌟 **THE NEXT BIG MEME GEM!** 💎\n\n{coin_name} is flying to the moon step by step! 🚀\n\n🛒 **Trade Here:** {buy_link}\n📜 **CA:** `{contract}`",
    "🚀 **NO BRAKES ON THIS ROCKET!** 🚀\n\n{coin_name} is melting candles right now! 🟢🟢🟢\n\n🛒 **Buy:** {buy_link}\n📜 **CA:** `{contract}`",
    "🔥 **GO GO GO ARMY!** 🔥\n\nPush the chart! Retweet! Spread the word for {coin_name}! 📣💥\n\n🛒 **Buy Link:** {buy_link}\n📜 **CA:** `{contract}`",
    "🌕 **MOONING IN PROGRESS...** ⌛️\n\nLoading 99%... {coin_name} is ready for massive gains! 💰🚀\n\n🛒 **DEXScreener:** {buy_link}\n📜 **CA:** `{contract}`",
    "⚡️ **HIGH ENERGY PUMP!** ⚡️\n\n{coin_name} is dominating! Are you riding the wave? 🌊🟢\n\n🛒 **Buy Now:** {buy_link}\n📜 **CA:** `{contract}`",
    "🚀 **TO THE STARS AND BEYOND!** ✨\n\n{coin_name} is rewriting crypto history! 🔥💎\n\n🛒 **Trade:** {buy_link}\n📜 **CA:** `{contract}`",

    # --- COMMUNITY ENGAGEMENT / POLLS / QUESTIONS (26-38) ---
    "💬 **COMMUNITY CHECK!** 🗣\n\nHow many {coin_name} tokens are you holding right now? 🤔💎\nDrop your flex in the chat! 👇\n\n🛒 **Buy More:** {buy_link}\n📜 **CA:** `{contract}`",
    "👇 **POLL TIME!** 👇\n\nWhere will {coin_name} be by next week?\n1️⃣ 10x 🚀\n2️⃣ 50x 🛸\n3️⃣ 100x 🌕\n\n🛒 **Secure Bags:** {buy_link}\n📜 **CA:** `{contract}`",
    "💎 **DIAMOND HANDS ONLY!** 💎\n\nWho is holding {coin_name} until $1M market cap? 🙌🔥\nComment below! 👇\n\n🛒 **Buy:** {buy_link}\n📜 **CA:** `{contract}`",
    "🔥 **QUICK QUESTION!** 🔥\n\nDid you buy the last {coin_name} dip or are you watching from the sidelines? 📈👀\n\n🛒 **Buy Dip:** {buy_link}\n📜 **CA:** `{contract}`",
    "🚀 **ARE YOU READY FOR THE NEXT LEG UP?** 🚀\n\n{coin_name} community is stronger than ever! 💪💥\n\n🛒 **DEXScreener:** {buy_link}\n📜 **CA:** `{contract}`",
    "👀 **SPOTTED A WHALE ACCUMULATING!** 🐳\n\nAre you accumulating {coin_name} too? 🎒💎\n\n🛒 **Trade:** {buy_link}\n📜 **CA:** `{contract}`",
    "🔥 **WHAT'S YOUR PRICE TARGET?** 🎯\n\nWhat is your end price target for {coin_name}? 🚀💰\n\n🛒 **Buy Now:** {buy_link}\n📜 **CA:** `{contract}`",
    "🌟 **RATE THIS CHART 1 TO 10!** 📈\n\n{coin_name} is looking absolutely primed! 🔥👇\n\n🛒 **Buy Link:** {buy_link}\n📜 **CA:** `{contract}`",
    "🛡 **SHIBAS / BULLS / CHADS!** 🛡\n\nWhich country is repping {coin_name} today? 🌍👇\n\n🛒 **Buy:** {buy_link}\n📜 **CA:** `{contract}`",
    "💪 **STAY STRONG, HOLD HIGH!** 💪\n\nGreat things take time, but {coin_name} takes off fast! 🚀🔥\n\n🛒 **Trade:** {buy_link}\n📜 **CA:** `{contract}`",
    "🚨 **ATTENTION HOLDERS!** 🚨\n\nDon't forget to like, retweet, and share {coin_name}! 📢🔥\n\n🛒 **DEXScreener:** {buy_link}\n📜 **CA:** `{contract}`",
    "🎉 **CELEBRATION TIME!** 🎉\n\nAnother milestone hit for {coin_name}! We keep growing! 📈✨\n\n🛒 **Buy:** {buy_link}\n📜 **CA:** `{contract}`",
    "👑 **KINGS HOLD DIAMONDS!** 💎\n\nPaper hands sell, Chads buy more {coin_name}! 🗿🔥\n\n🛒 **Buy Link:** {buy_link}\n📜 **CA:** `{contract}`",

    # --- FOMO / URGENCY & BULLISH ALERTS (39-50) ---
    "🚨 **LAST CHANCE TO BUY LOW!** 🚨\n\n{coin_name} dip is getting eaten fast! 📈💥\n\n🛒 **Buy Now:** {buy_link}\n📜 **CA:** `{contract}`",
    "⚡️ **MASSIVE MOMENTUM BUILDING!** ⚡️\n\n{coin_name} chart is about to blow up! 🌋🚀\n\n🛒 **Trade:** {buy_link}\n📜 **CA:** `{contract}`",
    "🔥 **DONT FEAR THE DIP!** 📉➡️📈\n\nSmart money buys {coin_name} dips! 🧠💰\n\n🛒 **Buy Dip:** {buy_link}\n📜 **CA:** `{contract}`",
    "🟢 **GREEN CANDLES LOADING...** 🟢\n\n{coin_name} is preparing for a massive push! 🚀🔥\n\n🛒 **DEXScreener:** {buy_link}\n📜 **CA:** `{contract}`",
    "💣 **BOOM! VOLUME IS UP!** 💣\n\nInflow of new buyers into {coin_name}! 🔥📈\n\n🛒 **Buy:** {buy_link}\n📜 **CA:** `{contract}`",
    "🏆 **CHAMPIONS CHOOSE {coin_name}!** 🏆\n\nThe momentum is unstoppable! GO GO GO! 🚀💥\n\n🛒 **Trade:** {buy_link}\n📜 **CA:** `{contract}`",
    "🔥 **THE ENGINE IS ROARING!** 🏎💨\n\n{coin_name} is racing to new highs! 📈✨\n\n🛒 **Buy Link:** {buy_link}\n📜 **CA:** `{contract}`",
    "💎 **HIDDEN GEM NO MORE!** 💎\n\nEveryone is talking about {coin_name}! Get in early! 🚀🌐\n\n🛒 **Buy:** {buy_link}\n📜 **CA:** `{contract}`",
    "📈 **CHART IS LOOKING SEXY!** 🔥\n\nPure bullish structure on {coin_name}! 🚀🟢\n\n🛒 **DEXScreener:** {buy_link}\n📜 **CA:** `{contract}`",
    "🚀 **PREPARE FOR TAKE-OFF!** 🚀\n\nClear the runway for {coin_name}! 🌕💥\n\n🛒 **Buy Now:** {buy_link}\n📜 **CA:** `{contract}`",
    "⚠️ **FOMO ALERT!** ⚠️\n\nDon't watch {coin_name} 10x from the sidelines! 🎒💰\n\n🛒 **Trade:** {buy_link}\n📜 **CA:** `{contract}`",
    "🎯 **MISSION: MOON!** 🌕\n\n{coin_name} army is growing every single minute! 🔥🚀\n\n🛒 **Buy Link:** {buy_link}\n📜 **CA:** `{contract}`\n📢 {channel}"
]

def get_fallback_message(data):
    template = random.choice(FALLBACK_TEMPLATES)
    return template.format(
        coin_name=data.get('coin_name', 'Token'),
        buy_link=data.get('buy_link', ''),
        contract=data.get('contract', ''),
        channel=data.get('channel', '')
    )

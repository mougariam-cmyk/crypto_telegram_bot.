# fallback_db.py
import random

MESSAGES_BANK = [
    "?? **{coin_name} is heating up!** Don't miss out on the momentum.\n\n?? **Buy Now:** {buy_link}\n?? Diamond hands win the race!",
    "?? Have you checked the contract for **{coin_name}** today?\n\n?? **Contract:** `{contract}`\n?? **Official Channel:** {channel}",
    "?? **BULLISH ALERT!** Big moves expected for **{coin_name}**.\n\n?? **Direct Buy:** {buy_link}\n?? **CA:** `{contract}`",
    "?? Patience in crypto creates legends. **{coin_name}** is today's top opportunity!\n\n?? **Join Channel:** {channel}",
    "? Don't wait for the FOMO to kick in! Get early positioning in **{coin_name}**.\n\n?? **Contract:** `{contract}`"
]

def get_fallback_message(data):
    template = random.choice(MESSAGES_BANK)
    return template.format(
        coin_name=data['coin_name'],
        buy_link=data['buy_link'],
        contract=data['contract'],
        channel=data['channel']
    )
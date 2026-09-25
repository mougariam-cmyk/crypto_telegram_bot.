import os
import json
import psycopg2


DATABASE_URL = os.getenv("DATABASE_URL")


def get_db_connection():
    """
    Create a PostgreSQL connection using Render/Supabase DATABASE_URL.
    """
    db_url = DATABASE_URL

    if not db_url:
        raise ValueError("DATABASE_URL is not configured.")

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    return psycopg2.connect(db_url)


def init_db():
    """
    Create the users table if it does not exist.
    Also add newer columns when an older version of the table already exists.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT NOT NULL,
                username TEXT,
                selected_plan TEXT,
                coin_name TEXT,
                coin_desc TEXT,
                contract TEXT,
                buy_link TEXT,
                x_link TEXT DEFAULT '',
                website_link TEXT DEFAULT '',
                token_symbol TEXT DEFAULT '',
                logo_url TEXT DEFAULT '',
                social_links TEXT DEFAULT '{}',
                channel TEXT NOT NULL,
                network TEXT DEFAULT '',
                msg_per_hour INTEGER DEFAULT 2,
                posts_per_day INTEGER DEFAULT 5,
                enable_new_buy INTEGER DEFAULT 0,
                link_ratio INTEGER DEFAULT 100,
                subscription_status TEXT DEFAULT 'active',
                start_date TEXT,
                end_date TEXT,
                PRIMARY KEY (user_id, channel)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_profiles (
                user_id BIGINT PRIMARY KEY,
                language TEXT DEFAULT 'en',
                onboarding_seen INTEGER DEFAULT 0,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        # Migration for databases created with the older schema.
        cursor.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS start_date TEXT;
        """)

        cursor.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS end_date TEXT;
        """)

        cursor.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS subscription_status TEXT DEFAULT 'active';
        """)

        cursor.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS link_ratio INTEGER DEFAULT 100;
        """)

        cursor.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS msg_per_hour INTEGER DEFAULT 2;
        """)

        cursor.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS enable_new_buy INTEGER DEFAULT 0;
        """)

        cursor.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS network TEXT DEFAULT '';
        """)

        cursor.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS x_link TEXT DEFAULT '';
        """)

        cursor.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS website_link TEXT DEFAULT '';
        """)

        cursor.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS posts_per_day INTEGER DEFAULT 5;
        """)
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS token_symbol TEXT DEFAULT '';")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS logo_url TEXT DEFAULT '';")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS social_links TEXT DEFAULT '{}';")

        cursor.execute("ALTER TABLE users DROP COLUMN IF EXISTS receive_geopolitical_news;")
        cursor.execute("ALTER TABLE users DROP COLUMN IF EXISTS receive_market_news;")

        cursor.execute("DELETE FROM content_pool WHERE category IN ('geopolitical', 'markets', 'economy', 'political');")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS content_pool (
                id BIGSERIAL PRIMARY KEY,
                category TEXT NOT NULL,
                network TEXT DEFAULT '',
                slot TEXT DEFAULT 'any',
                content TEXT NOT NULL,
                batch_date DATE NOT NULL DEFAULT CURRENT_DATE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        cursor.execute("""
            ALTER TABLE content_pool
            ADD COLUMN IF NOT EXISTS slot TEXT DEFAULT 'any';
        """)

        conn.commit()

    finally:
        cursor.close()
        conn.close()


# ==========================================
# MARSOF AI OFFICIAL CONTENT ENGINE
# ==========================================

def init_official_content_tables():
    """Create storage for MARSOF AI's own official publishing engine."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS marsof_official_daily_content (
                id BIGSERIAL PRIMARY KEY,
                content_date DATE NOT NULL,
                slot INTEGER NOT NULL,
                topic TEXT DEFAULT '',
                content TEXT NOT NULL,
                telegram_content TEXT DEFAULT '',
                x_content TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(content_date, slot)
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS marsof_official_publication_log (
                id BIGSERIAL PRIMARY KEY,
                content_id BIGINT,
                platform TEXT NOT NULL,
                destination TEXT DEFAULT '',
                external_message_id TEXT DEFAULT '',
                status TEXT NOT NULL,
                error_text TEXT DEFAULT '',
                published_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS marsof_official_reply_log (
                id BIGSERIAL PRIMARY KEY,
                platform TEXT NOT NULL,
                source_id TEXT DEFAULT '',
                reply_text TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        conn.commit()
    finally:
        cursor.close()
        conn.close()

def get_official_daily_content(content_date=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if content_date is None:
            cursor.execute("""SELECT id, slot, topic, content, telegram_content, x_content, status
                            FROM marsof_official_daily_content
                            WHERE content_date = CURRENT_DATE ORDER BY slot""")
        else:
            cursor.execute("""SELECT id, slot, topic, content, telegram_content, x_content, status
                            FROM marsof_official_daily_content
                            WHERE content_date = %s ORDER BY slot""", (content_date,))
        rows = cursor.fetchall()
        return [{"id": r[0], "slot": r[1], "topic": r[2], "content": r[3],
                 "telegram_content": r[4], "x_content": r[5], "status": r[6]} for r in rows]
    finally:
        cursor.close(); conn.close()

def save_official_daily_content(items):
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM marsof_official_daily_content WHERE content_date = CURRENT_DATE")
        for i, item in enumerate(items, start=1):
            cursor.execute("""INSERT INTO marsof_official_daily_content
                (content_date, slot, topic, content, telegram_content, x_content, status)
                VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, 'pending')
                ON CONFLICT (content_date, slot) DO UPDATE SET
                topic=EXCLUDED.topic, content=EXCLUDED.content,
                telegram_content=EXCLUDED.telegram_content, x_content=EXCLUDED.x_content, status='pending'
            """, (i, item.get('topic',''), item.get('content',''), item.get('telegram_content',''), item.get('x_content','')))
        conn.commit()
    finally:
        cursor.close(); conn.close()

def mark_official_publication(content_id, platform, destination, external_message_id='', status='sent', error_text=''):
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("""INSERT INTO marsof_official_publication_log
            (content_id, platform, destination, external_message_id, status, error_text)
            VALUES (%s,%s,%s,%s,%s,%s)""", (content_id, platform, destination, str(external_message_id or ''), status, error_text))
        conn.commit()
    finally:
        cursor.close(); conn.close()

def get_official_published_content_ids(platform, content_date=None):
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        if content_date is None:
            cursor.execute("""SELECT content_id FROM marsof_official_publication_log
                             WHERE platform=%s AND status='sent' AND published_at::date=CURRENT_DATE""", (platform,))
        else:
            cursor.execute("""SELECT content_id FROM marsof_official_publication_log
                             WHERE platform=%s AND status='sent' AND published_at::date=%s""", (platform, content_date))
        return {int(r[0]) for r in cursor.fetchall() if r[0] is not None}
    finally:
        cursor.close(); conn.close()

def save_official_reply(platform, source_id, reply_text, status='sent'):
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("""INSERT INTO marsof_official_reply_log
            (platform, source_id, reply_text, status) VALUES (%s,%s,%s,%s)""", (platform, str(source_id or ''), reply_text, status))
        conn.commit()
    finally:
        cursor.close(); conn.close()


def save_user_data(user_id: int, username: str, data: dict):
    """Insert or update a user's channel configuration."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        channel = data.get("channel", "")
        cursor.execute("SELECT selected_plan FROM users WHERE user_id=%s AND channel=%s AND subscription_status='active';", (user_id, channel))
        existing = cursor.fetchone()
        target_plan = data.get("selected_plan", "free")
        if existing and existing[0] != "free" and target_plan == "free" and not data.get("is_editing"):
            target_plan = existing[0]
        cursor.execute("""
            INSERT INTO users (user_id, username, selected_plan, coin_name, coin_desc, contract, buy_link, x_link, website_link, token_symbol, logo_url, social_links, channel, network, msg_per_hour, posts_per_day, enable_new_buy, link_ratio, subscription_status, start_date, end_date)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s)
            ON CONFLICT (user_id, channel) DO UPDATE SET
                username=EXCLUDED.username, selected_plan=EXCLUDED.selected_plan, coin_name=EXCLUDED.coin_name,
                coin_desc=EXCLUDED.coin_desc, contract=EXCLUDED.contract, buy_link=EXCLUDED.buy_link,
                x_link=EXCLUDED.x_link, website_link=EXCLUDED.website_link, token_symbol=EXCLUDED.token_symbol,
                logo_url=EXCLUDED.logo_url, social_links=EXCLUDED.social_links, network=EXCLUDED.network,
                msg_per_hour=EXCLUDED.msg_per_hour, posts_per_day=EXCLUDED.posts_per_day,
                enable_new_buy=EXCLUDED.enable_new_buy, link_ratio=EXCLUDED.link_ratio,
                subscription_status='active', start_date=EXCLUDED.start_date, end_date=EXCLUDED.end_date;
        """, (
            user_id, username, target_plan, data.get("coin_name", ""), data.get("coin_desc", ""),
            data.get("contract", ""), data.get("buy_link", ""), data.get("x_link", ""), data.get("website_link", ""),
            data.get("coin_symbol", data.get("token_symbol", "")), data.get("logo_url", ""), json.dumps(data.get("social_links", {}), ensure_ascii=False),
            channel, data.get("network", ""), data.get("msg_per_hour", 0), data.get("posts_per_day", 5),
            1 if data.get("enable_new_buy", False) else 0, data.get("link_ratio", 100),
            data.get("start_date"), data.get("end_date")
        ))
        conn.commit()
    finally:
        cursor.close(); conn.close()

def get_user_start_state(user_id: int):
    """Return active channels plus lightweight first-run onboarding state in one DB call."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT channel, coin_name
            FROM users
            WHERE user_id = %s
              AND subscription_status = 'active'
            ORDER BY channel;
        """, (user_id,))
        channels = cursor.fetchall()

        cursor.execute("""
            SELECT language, onboarding_seen
            FROM bot_profiles
            WHERE user_id = %s;
        """, (user_id,))
        profile = cursor.fetchone()

        return {
            "channels": channels,
            "language": profile[0] if profile else "en",
            "onboarding_seen": bool(profile[1]) if profile else False,
        }
    finally:
        cursor.close()
        conn.close()


def save_onboarding_profile(user_id: int, language: str):
    """Persist the user's first-run language and onboarding completion."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO bot_profiles (user_id, language, onboarding_seen, updated_at)
            VALUES (%s, %s, 1, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET
                language = EXCLUDED.language,
                onboarding_seen = 1,
                updated_at = NOW();
        """, (user_id, language))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def cancel_user_subscription(user_id: int, channel: str):
    """
    Mark a channel subscription as cancelled.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        normalized_channel = channel.replace("@", "")

        cursor.execute("""
            UPDATE users
            SET subscription_status = 'cancelled'
            WHERE user_id = %s
              AND (
                  channel = %s
                  OR channel = %s
              );
        """, (
            user_id,
            channel,
            f"@{normalized_channel}"
        ))

        conn.commit()

    finally:
        cursor.close()
        conn.close()


def get_user_channels(user_id: int):
    """
    Return all active channels belonging to a user.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT channel, coin_name
            FROM users
            WHERE user_id = %s
              AND subscription_status = 'active'
            ORDER BY channel;
        """, (user_id,))

        return cursor.fetchall()

    finally:
        cursor.close()
        conn.close()


def _row_to_user(row):
    return {
        "user_id": row[0], "selected_plan": row[1], "coin_name": row[2], "coin_desc": row[3],
        "contract": row[4], "buy_link": row[5], "x_link": row[6] or "", "website_link": row[7] or "",
        "coin_symbol": row[8] or "", "token_symbol": row[8] or "", "logo_url": row[9] or "",
        "social_links": json.loads(row[10] or "{}") if isinstance(row[10], str) else (row[10] or {}),
        "channel": row[11], "network": row[12] or "", "msg_per_hour": row[13] or 0,
        "posts_per_day": row[14] or 5, "enable_new_buy": bool(row[15]), "link_ratio": row[16] if row[16] is not None else 100,
        "subscription_status": row[17], "start_date": row[18], "end_date": row[19]
    }


def get_user_channel_data(user_id: int, channel: str):
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        normalized_channel = channel.replace("@", "")
        cursor.execute("""SELECT user_id,selected_plan,coin_name,coin_desc,contract,buy_link,x_link,website_link,token_symbol,logo_url,social_links,channel,network,msg_per_hour,posts_per_day,enable_new_buy,link_ratio,subscription_status,start_date,end_date FROM users WHERE user_id=%s AND (channel=%s OR channel=%s) LIMIT 1;""", (user_id, channel, f"@{normalized_channel}"))
        row = cursor.fetchone()
        return _row_to_user(row) if row else None
    finally:
        cursor.close(); conn.close()


def get_active_users():
    conn = get_db_connection(); cursor = conn.cursor()
    try:
        cursor.execute("""SELECT user_id,selected_plan,coin_name,coin_desc,contract,buy_link,x_link,website_link,token_symbol,logo_url,social_links,channel,network,msg_per_hour,posts_per_day,enable_new_buy,link_ratio,subscription_status,start_date,end_date FROM users WHERE subscription_status='active' ORDER BY user_id,channel;""")
        return [_row_to_user(row) for row in cursor.fetchall()]
    finally:
        cursor.close(); conn.close()

def replace_content_pool(posts):
    """Replace the central AI content pool with a fresh batch."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM content_pool;")
        for post in posts:
            cursor.execute(
                """INSERT INTO content_pool (category, network, slot, content) VALUES (%s, %s, %s, %s);""",
                (post.get("category", "global"), post.get("network", ""), post.get("slot", "any"), post.get("content", ""))
            )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def get_content_pool_stats():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*), COALESCE(MAX(batch_date), CURRENT_DATE) FROM content_pool;")
        count, batch_date = cursor.fetchone()
        return count or 0, batch_date
    finally:
        cursor.close()
        conn.close()


def get_content_pool_posts(category=None, network=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if category == "network":
            cursor.execute(
                "SELECT id, category, network, slot, content FROM content_pool WHERE category = 'network' AND lower(network) = lower(%s) ORDER BY id;",
                (network or "",)
            )
        else:
            cursor.execute(
                "SELECT id, category, network, slot, content FROM content_pool WHERE category <> 'network' ORDER BY id;"
            )
        return [
            {"id": r[0], "category": r[1], "network": r[2], "slot": r[3], "content": r[4]}
            for r in cursor.fetchall()
        ]
    finally:
        cursor.close()
        conn.close()


def get_user_active_plan_keys(user_id: int):
    """Return the distinct active subscription plan keys for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT DISTINCT selected_plan
            FROM users
            WHERE user_id = %s
              AND subscription_status = 'active';
        """, (user_id,))
        return [row[0] for row in cursor.fetchall() if row and row[0]]
    finally:
        cursor.close()
        conn.close()

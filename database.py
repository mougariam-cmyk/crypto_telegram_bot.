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
                channel TEXT NOT NULL,
                network TEXT DEFAULT '',
                msg_per_hour INTEGER DEFAULT 2,
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

        for ddl in (
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_symbol TEXT DEFAULT '';",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS website_link TEXT DEFAULT '';",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS logo_url TEXT DEFAULT '';",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS social_links TEXT DEFAULT '{}';",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS posts_per_day INTEGER DEFAULT 5;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_market_snapshot TEXT DEFAULT '';",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_market_check TIMESTAMPTZ;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_market_alert TIMESTAMPTZ;",
        ):
            cursor.execute(ddl)

        # Political/economic content preferences are obsolete in MARSOF AI.
        cursor.execute("ALTER TABLE users DROP COLUMN IF EXISTS receive_geopolitical_news;")
        cursor.execute("ALTER TABLE users DROP COLUMN IF EXISTS receive_market_news;")

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
        cursor.execute("DELETE FROM content_pool WHERE lower(category) IN ('geopolitical','markets','economy','political');")

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
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        channel=data.get("channel","")
        cursor.execute("SELECT selected_plan FROM users WHERE user_id=%s AND channel=%s AND subscription_status='active';",(user_id,channel))
        existing=cursor.fetchone(); target_plan=data.get("selected_plan","free")
        if existing and existing[0] != "free" and target_plan == "free" and not data.get("is_editing"):
            target_plan=existing[0]
        snapshot=data.get("last_market_snapshot") or data.get("token_snapshot") or {}
        if isinstance(snapshot,dict): snapshot=json.dumps(snapshot,ensure_ascii=False)
        cursor.execute("""
            INSERT INTO users (user_id,username,selected_plan,coin_name,coin_desc,contract,buy_link,x_link,channel,network,
                token_symbol,website_link,logo_url,social_links,posts_per_day,msg_per_hour,enable_new_buy,link_ratio,
                subscription_status,start_date,end_date,last_market_snapshot,last_market_check,last_market_alert)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,NOW(),NULL)
            ON CONFLICT (user_id,channel) DO UPDATE SET
                username=EXCLUDED.username,selected_plan=EXCLUDED.selected_plan,coin_name=EXCLUDED.coin_name,
                coin_desc=EXCLUDED.coin_desc,contract=EXCLUDED.contract,buy_link=EXCLUDED.buy_link,x_link=EXCLUDED.x_link,
                network=EXCLUDED.network,token_symbol=EXCLUDED.token_symbol,website_link=EXCLUDED.website_link,
                logo_url=EXCLUDED.logo_url,social_links=EXCLUDED.social_links,posts_per_day=EXCLUDED.posts_per_day,
                msg_per_hour=EXCLUDED.msg_per_hour,enable_new_buy=EXCLUDED.enable_new_buy,link_ratio=EXCLUDED.link_ratio,
                subscription_status='active',start_date=EXCLUDED.start_date,end_date=EXCLUDED.end_date,
                last_market_snapshot=EXCLUDED.last_market_snapshot,last_market_check=NOW();
        """,(user_id,username,target_plan,data.get("coin_name",""),data.get("coin_desc",""),data.get("contract",""),
              data.get("buy_link",""),data.get("x_link",""),channel,data.get("network",""),data.get("token_symbol",""),
              data.get("website_link",""),data.get("logo_url",""),data.get("social_links","{}"),data.get("posts_per_day",5),
              data.get("msg_per_hour",1),1 if data.get("enable_new_buy",False) else 0,data.get("link_ratio",0),
              data.get("start_date"),data.get("end_date"),snapshot))
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


def _row_to_project(row):
    snap={}
    try: snap=json.loads(row[22] or '{}') if row[22] else {}
    except Exception: snap={}
    return {
        "user_id":row[0],"selected_plan":row[1],"coin_name":row[2],"coin_desc":row[3],"contract":row[4],
        "buy_link":row[5],"x_link":row[6] or "","channel":row[7],"network":row[8] or "",
        "token_symbol":row[9] or "","website_link":row[10] or "","logo_url":row[11] or "",
        "social_links":row[12] or "{}","posts_per_day":row[13] or 5,"msg_per_hour":row[14] or 1,
        "enable_new_buy":bool(row[15]),"link_ratio":row[16] if row[16] is not None else 0,
        "subscription_status":row[17],"start_date":row[18],"end_date":row[19],
        "last_market_snapshot":snap,"last_market_check":row[20],"last_market_alert":row[21],
    }

_PROJECT_SELECT="""SELECT user_id,selected_plan,coin_name,coin_desc,contract,buy_link,x_link,channel,network,
 token_symbol,website_link,logo_url,social_links,posts_per_day,msg_per_hour,enable_new_buy,link_ratio,
 subscription_status,start_date,end_date,last_market_check,last_market_alert,last_market_snapshot FROM users"""

def get_user_channel_data(user_id:int,channel:str):
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        cursor.execute(_PROJECT_SELECT+" WHERE user_id=%s AND (channel=%s OR channel=%s) LIMIT 1;",(user_id,channel,f"@{channel.replace('@','')}"))
        row=cursor.fetchone(); return _row_to_project(row) if row else None
    finally: cursor.close(); conn.close()

def get_active_users():
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        cursor.execute(_PROJECT_SELECT+" WHERE subscription_status='active' ORDER BY user_id,channel;")
        return [_row_to_project(r) for r in cursor.fetchall()]
    finally: cursor.close(); conn.close()

def update_market_snapshot(user_id,channel,snapshot):
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        cursor.execute("UPDATE users SET last_market_snapshot=%s,last_market_check=NOW() WHERE user_id=%s AND (channel=%s OR channel=%s);",
                       (json.dumps(snapshot,ensure_ascii=False),user_id,channel,f"@{channel.replace('@','')}")); conn.commit()
    finally: cursor.close(); conn.close()

def should_send_market_alert(user_id,channel):
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        cursor.execute("SELECT last_market_alert IS NULL OR last_market_alert < NOW()-INTERVAL '60 minutes' FROM users WHERE user_id=%s AND (channel=%s OR channel=%s);",(user_id,channel,f"@{channel.replace('@','')}"))
        row=cursor.fetchone(); return bool(row and row[0])
    finally: cursor.close(); conn.close()

def mark_market_alert_sent(user_id,channel):
    conn=get_db_connection(); cursor=conn.cursor()
    try:
        cursor.execute("UPDATE users SET last_market_alert=NOW() WHERE user_id=%s AND (channel=%s OR channel=%s);",(user_id,channel,f"@{channel.replace('@','')}")); conn.commit()
    finally: cursor.close(); conn.close()


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


# ==========================================
# FREE PLAN DAILY POST COUNTER
# ==========================================
def _ensure_free_daily_posts_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS free_daily_posts (
            user_id BIGINT NOT NULL,
            channel TEXT NOT NULL,
            post_date DATE NOT NULL DEFAULT CURRENT_DATE,
            post_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, channel, post_date)
        );
    """)

def get_free_daily_post_count(user_id: int, channel: str):
    """Return the number of successful free-plan posts published today."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        _ensure_free_daily_posts_table(cursor)
        cursor.execute("""
            SELECT post_count
            FROM free_daily_posts
            WHERE user_id = %s AND channel = %s AND post_date = CURRENT_DATE;
        """, (user_id, channel))
        row = cursor.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    finally:
        cursor.close()
        conn.close()


def record_free_daily_post(user_id: int, channel: str):
    """Increment today's free-plan post counter after a successful Telegram send."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        _ensure_free_daily_posts_table(cursor)
        cursor.execute("""
            INSERT INTO free_daily_posts (user_id, channel, post_date, post_count)
            VALUES (%s, %s, CURRENT_DATE, 1)
            ON CONFLICT (user_id, channel, post_date)
            DO UPDATE SET post_count = free_daily_posts.post_count + 1;
        """, (user_id, channel))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


# ==========================================
# SUBSCRIPTION EXPIRATION SUPPORT
# ==========================================
def _ensure_subscription_transition_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscription_transition_log (
            user_id BIGINT NOT NULL,
            channel TEXT NOT NULL,
            old_plan TEXT NOT NULL,
            transitioned_at TIMESTAMPTZ DEFAULT NOW(),
            free_announcement_sent INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, channel, transitioned_at)
        );
    """)

def get_expired_subscriptions():
    """Return active paid subscriptions whose end_date has passed."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT user_id, channel, selected_plan, end_date
            FROM users
            WHERE subscription_status = 'active'
              AND selected_plan IS NOT NULL
              AND selected_plan <> 'free'
              AND end_date IS NOT NULL
              AND end_date::date <= CURRENT_DATE;
        """)
        return [
            {"user_id": row[0], "channel": row[1], "selected_plan": row[2], "end_date": row[3]}
            for row in cursor.fetchall()
        ]
    finally:
        cursor.close()
        conn.close()


def transition_expired_subscription(user_id: int, channel: str):
    """Move one expired paid subscription to Free while preserving project configuration."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        _ensure_subscription_transition_table(cursor)
        cursor.execute("""
            SELECT selected_plan
            FROM users
            WHERE user_id = %s AND channel = %s
              AND subscription_status = 'active'
              AND selected_plan IS NOT NULL
              AND selected_plan <> 'free'
              AND end_date IS NOT NULL
              AND end_date::date <= CURRENT_DATE
            FOR UPDATE;
        """, (user_id, channel))
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return None

        old_plan = row[0]
        cursor.execute("""
            UPDATE users
            SET selected_plan = 'free', end_date = NULL, subscription_status = 'active'
            WHERE user_id = %s AND channel = %s;
        """, (user_id, channel))
        cursor.execute("""
            INSERT INTO subscription_transition_log (user_id, channel, old_plan)
            VALUES (%s, %s, %s);
        """, (user_id, channel, old_plan))
        conn.commit()
        return {"old_plan": old_plan}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def mark_free_transition_announced(user_id: int, channel: str):
    """Mark the most recent paid-to-free transition announcement as sent."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        _ensure_subscription_transition_table(cursor)
        cursor.execute("""
            UPDATE subscription_transition_log
            SET free_announcement_sent = 1
            WHERE user_id = %s AND channel = %s
              AND transitioned_at = (
                  SELECT MAX(transitioned_at)
                  FROM subscription_transition_log
                  WHERE user_id = %s AND channel = %s
              );
        """, (user_id, channel, user_id, channel))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def get_subscription_snapshot(user_id: int, channel: str):
    """Return the current subscription state for one channel."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT selected_plan, subscription_status, start_date, end_date
            FROM users
            WHERE user_id = %s AND channel = %s
            LIMIT 1;
        """, (user_id, channel))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "selected_plan": row[0],
            "subscription_status": row[1],
            "start_date": row[2],
            "end_date": row[3],
        }
    finally:
        cursor.close()
        conn.close()

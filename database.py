import os
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
                channel TEXT NOT NULL,
                network TEXT DEFAULT '',
                receive_geopolitical_news INTEGER DEFAULT 1,
                receive_market_news INTEGER DEFAULT 1,
                msg_per_hour INTEGER DEFAULT 2,
                enable_new_buy INTEGER DEFAULT 0,
                link_ratio INTEGER DEFAULT 100,
                subscription_status TEXT DEFAULT 'active',
                start_date TEXT,
                end_date TEXT,
                PRIMARY KEY (user_id, channel)
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
            ADD COLUMN IF NOT EXISTS receive_geopolitical_news INTEGER DEFAULT 1;
        """)

        cursor.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS receive_market_news INTEGER DEFAULT 1;
        """)

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


def save_user_data(user_id: int, username: str, data: dict):
    """
    Insert or update a user's channel configuration.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        channel = data.get("channel", "")

        cursor.execute("""
            SELECT selected_plan
            FROM users
            WHERE user_id = %s
              AND channel = %s
              AND subscription_status = 'active';
        """, (user_id, channel))

        existing = cursor.fetchone()

        target_plan = data.get("selected_plan", "free")

        # Do not accidentally downgrade an existing paid subscription
        # when saving normal configuration changes.
        if (
            existing
            and existing[0] != "free"
            and target_plan == "free"
            and not data.get("is_editing")
        ):
            target_plan = existing[0]

        cursor.execute("""
            INSERT INTO users (
                user_id,
                username,
                selected_plan,
                coin_name,
                coin_desc,
                contract,
                buy_link,
                channel,
                network,
                receive_geopolitical_news,
                receive_market_news,
                msg_per_hour,
                enable_new_buy,
                link_ratio,
                subscription_status,
                start_date,
                end_date
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, 'active', %s, %s
            )
            ON CONFLICT (user_id, channel)
            DO UPDATE SET
                username = EXCLUDED.username,
                selected_plan = EXCLUDED.selected_plan,
                coin_name = EXCLUDED.coin_name,
                coin_desc = EXCLUDED.coin_desc,
                contract = EXCLUDED.contract,
                buy_link = EXCLUDED.buy_link,
                network = EXCLUDED.network,
                receive_geopolitical_news = EXCLUDED.receive_geopolitical_news,
                receive_market_news = EXCLUDED.receive_market_news,
                msg_per_hour = EXCLUDED.msg_per_hour,
                enable_new_buy = EXCLUDED.enable_new_buy,
                link_ratio = EXCLUDED.link_ratio,
                subscription_status = 'active',
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date;
        """, (
            user_id,
            username,
            target_plan,
            data.get("coin_name", ""),
            data.get("coin_desc", ""),
            data.get("contract", ""),
            data.get("buy_link", ""),
            channel,
            data.get("network", ""),
            1 if data.get("receive_geopolitical_news", True) else 0,
            1 if data.get("receive_market_news", True) else 0,
            data.get("msg_per_hour", 2),
            1 if data.get("enable_new_buy", False) else 0,
            data.get("link_ratio", 100),
            data.get("start_date"),
            data.get("end_date")
        ))

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
        """)

        return cursor.fetchall()

    finally:
        cursor.close()
        conn.close()


def get_user_channel_data(user_id: int, channel: str):
    """
    Return channel data.

    Important:
    We intentionally do not filter subscription_status here.
    The publisher needs to be able to see a cancelled record and stop
    its background task.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        normalized_channel = channel.replace("@", "")

        cursor.execute("""
            SELECT
                user_id,
                selected_plan,
                coin_name,
                coin_desc,
                contract,
                buy_link,
                channel,
                network,
                receive_geopolitical_news,
                receive_market_news,
                msg_per_hour,
                enable_new_buy,
                link_ratio,
                subscription_status,
                start_date,
                end_date
            FROM users
            WHERE user_id = %s
              AND (
                  channel = %s
                  OR channel = %s
              )
            LIMIT 1;
        """, (
            user_id,
            channel,
            f"@{normalized_channel}"
        ))

        row = cursor.fetchone()

        if not row:
            return None

        return {
            "user_id": row[0],
            "selected_plan": row[1],
            "coin_name": row[2],
            "coin_desc": row[3],
            "contract": row[4],
            "buy_link": row[5],
            "channel": row[6],
            "network": row[7] or "",
            "receive_geopolitical_news": bool(row[8]) if row[8] is not None else True,
            "receive_market_news": bool(row[9]) if row[9] is not None else True,
            "msg_per_hour": row[10] if row[10] is not None else 2,
            "enable_new_buy": bool(row[11]),
            "link_ratio": row[12] if row[12] is not None else 100,
            "subscription_status": row[13],
            "start_date": row[14],
            "end_date": row[15]
        }

    finally:
        cursor.close()
        conn.close()


def get_active_users():
    """
    Return all active channel configurations.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT
                user_id,
                selected_plan,
                coin_name,
                coin_desc,
                contract,
                buy_link,
                channel,
                network,
                receive_geopolitical_news,
                receive_market_news,
                msg_per_hour,
                enable_new_buy,
                link_ratio,
                subscription_status,
                start_date,
                end_date
            FROM users
            WHERE subscription_status = 'active'
            ORDER BY user_id, channel;
        """)

        rows = cursor.fetchall()

        users = []

        for row in rows:
            users.append({
                "user_id": row[0],
                "selected_plan": row[1],
                "coin_name": row[2],
                "coin_desc": row[3],
                "contract": row[4],
                "buy_link": row[5],
                "channel": row[6],
                "network": row[7] or "",
                "receive_geopolitical_news": bool(row[8]) if row[8] is not None else True,
                "receive_market_news": bool(row[9]) if row[9] is not None else True,
                "msg_per_hour": row[10] if row[10] is not None else 2,
                "enable_new_buy": bool(row[11]),
                "link_ratio": row[12] if row[12] is not None else 100,
                "subscription_status": row[13],
                "start_date": row[14],
                "end_date": row[15]
            })

        return users

    finally:
        cursor.close()
        conn.close()


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

import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")


def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured.")
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """
    Create the users table if it does not exist and add the columns
    required by the current bot version.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT NOT NULL,
                channel TEXT NOT NULL,
                coin_name TEXT,
                contract_address TEXT,
                website TEXT,
                telegram TEXT,
                twitter TEXT,
                subscription_status TEXT DEFAULT 'active',
                selected_plan TEXT,
                start_date TIMESTAMP,
                end_date TIMESTAMP,
                link_ratio INTEGER DEFAULT 50,
                msg_per_hour INTEGER DEFAULT 2,
                enable_new_buy BOOLEAN DEFAULT TRUE,
                PRIMARY KEY (user_id, channel)
            );
        """)

        # Safe migration for databases created with an older version.
        migrations = [
            ("coin_name", "TEXT"),
            ("contract_address", "TEXT"),
            ("website", "TEXT"),
            ("telegram", "TEXT"),
            ("twitter", "TEXT"),
            ("subscription_status", "TEXT DEFAULT 'active'"),
            ("selected_plan", "TEXT"),
            ("start_date", "TIMESTAMP"),
            ("end_date", "TIMESTAMP"),
            ("link_ratio", "INTEGER DEFAULT 50"),
            ("msg_per_hour", "INTEGER DEFAULT 2"),
            ("enable_new_buy", "BOOLEAN DEFAULT TRUE"),
        ]

        for column, definition in migrations:
            cursor.execute(
                f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {column} {definition};"
            )

        conn.commit()
        cursor.close()
    finally:
        conn.close()


def save_user_data(user_id, channel, data):
    """
    Insert or update one channel configuration.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO users (
                user_id,
                channel,
                coin_name,
                contract_address,
                website,
                telegram,
                twitter,
                subscription_status,
                selected_plan,
                start_date,
                end_date,
                link_ratio,
                msg_per_hour,
                enable_new_buy
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (user_id, channel)
            DO UPDATE SET
                coin_name = EXCLUDED.coin_name,
                contract_address = EXCLUDED.contract_address,
                website = EXCLUDED.website,
                telegram = EXCLUDED.telegram,
                twitter = EXCLUDED.twitter,
                subscription_status = EXCLUDED.subscription_status,
                selected_plan = EXCLUDED.selected_plan,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                link_ratio = EXCLUDED.link_ratio,
                msg_per_hour = EXCLUDED.msg_per_hour,
                enable_new_buy = EXCLUDED.enable_new_buy;
        """, (
            user_id,
            channel,
            data.get("coin_name"),
            data.get("contract_address"),
            data.get("website"),
            data.get("telegram"),
            data.get("twitter"),
            data.get("subscription_status", "active"),
            data.get("selected_plan"),
            data.get("start_date"),
            data.get("end_date"),
            data.get("link_ratio", 50),
            data.get("msg_per_hour", 2),
            data.get("enable_new_buy", True),
        ))

        conn.commit()
        cursor.close()
    finally:
        conn.close()


def cancel_user_subscription(user_id, channel):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users
            SET subscription_status = 'cancelled'
            WHERE user_id = %s
              AND channel = %s;
        """, (user_id, channel))
        conn.commit()
        cursor.close()
    finally:
        conn.close()


def get_user_channels(user_id):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT channel, coin_name
            FROM users
            WHERE user_id = %s
              AND subscription_status = 'active'
            ORDER BY channel;
        """, (user_id,))

        rows = cursor.fetchall()
        cursor.close()

        return [
            {
                "channel": row[0],
                "coin_name": row[1]
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_user_channel_data(user_id, channel):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Accept either "channel" or "@channel" consistently.
        normalized_channel = channel.lstrip("@")
        cursor.execute("""
            SELECT
                user_id,
                channel,
                coin_name,
                contract_address,
                website,
                telegram,
                twitter,
                subscription_status,
                selected_plan,
                start_date,
                end_date,
                link_ratio,
                msg_per_hour,
                enable_new_buy
            FROM users
            WHERE user_id = %s
              AND (channel = %s OR channel = %s)
            LIMIT 1;
        """, (
            user_id,
            normalized_channel,
            f"@{normalized_channel}",
        ))

        row = cursor.fetchone()
        cursor.close()

        if not row:
            return None

        return {
            "user_id": row[0],
            "channel": row[1],
            "coin_name": row[2],
            "contract_address": row[3],
            "website": row[4],
            "telegram": row[5],
            "twitter": row[6],
            "subscription_status": row[7],
            "selected_plan": row[8],
            "start_date": row[9],
            "end_date": row[10],
            "link_ratio": row[11],
            "msg_per_hour": row[12],
            "enable_new_buy": row[13],
        }
    finally:
        conn.close()


def get_active_users():
    """
    Return all currently active channel configurations.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                user_id,
                channel,
                coin_name,
                contract_address,
                website,
                telegram,
                twitter,
                subscription_status,
                selected_plan,
                start_date,
                end_date,
                link_ratio,
                msg_per_hour,
                enable_new_buy
            FROM users
            WHERE subscription_status = 'active'
            ORDER BY user_id, channel;
        """)

        rows = cursor.fetchall()
        cursor.close()

        columns = [
            "user_id",
            "channel",
            "coin_name",
            "contract_address",
            "website",
            "telegram",
            "twitter",
            "subscription_status",
            "selected_plan",
            "start_date",
            "end_date",
            "link_ratio",
            "msg_per_hour",
            "enable_new_buy",
        ]

        return [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()

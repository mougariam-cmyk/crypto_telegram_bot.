from pathlib import Path

code = '''import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")


def get_db_connection():
    db_url = DATABASE_URL
    if not db_url:
        raise ValueError("DATABASE_URL is not configured.")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(db_url)


def init_db():
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
                msg_per_hour INTEGER DEFAULT 2,
                enable_new_buy INTEGER DEFAULT 0,
                link_ratio INTEGER DEFAULT 100,
                subscription_status TEXT DEFAULT 'active',
                start_date TEXT,
                end_date TEXT,
                PRIMARY KEY (user_id, channel)
            );
        """)
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS start_date TEXT;")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS end_date TEXT;")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_status TEXT DEFAULT 'active';")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS link_ratio INTEGER DEFAULT 100;")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS msg_per_hour INTEGER DEFAULT 2;")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS enable_new_buy INTEGER DEFAULT 0;")
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def save_user_data(user_id: int, username: str, data: dict):
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
        if existing and existing[0] != "free" and target_plan == "free" and not data.get("is_editing"):
            target_plan = existing[0]

        cursor.execute("""
            INSERT INTO users (
                user_id, username, selected_plan, coin_name, coin_desc,
                contract, buy_link, channel, msg_per_hour, enable_new_buy,
                link_ratio, subscription_status, start_date, end_date
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, 'active', %s, %s
            )
            ON CONFLICT (user_id, channel)
            DO UPDATE SET
                username = EXCLUDED.username,
                selected_plan = EXCLUDED.selected_plan,
                coin_name = EXCLUDED.coin_name,
                coin_desc = EXCLUDED.coin_desc,
                contract = EXCLUDED.contract,
                buy_link = EXCLUDED.buy_link,
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
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        normalized_channel = channel.replace("@", "")
        cursor.execute("""
            UPDATE users
            SET subscription_status = 'cancelled'
            WHERE user_id = %s
              AND (channel = %s OR channel = %s);
        """, (user_id, channel, f"@{normalized_channel}"))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def get_user_channels(user_id: int):
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


def get_user_channel_data(user_id: int, channel: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        normalized_channel = channel.replace("@", "")
        cursor.execute("""
            SELECT
                user_id, selected_plan, coin_name, coin_desc, contract,
                buy_link, channel, msg_per_hour, enable_new_buy, link_ratio,
                subscription_status, start_date, end_date
            FROM users
            WHERE user_id = %s
              AND (channel = %s OR channel = %s)
            LIMIT 1;
        """, (user_id, channel, f"@{normalized_channel}"))
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
            "msg_per_hour": row[7] if row[7] is not None else 2,
            "enable_new_buy": bool(row[8]),
            "link_ratio": row[9] if row[9] is not None else 100,
            "subscription_status": row[10],
            "start_date": row[11],
            "end_date": row[12],
        }
    finally:
        cursor.close()
        conn.close()


def get_active_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT
                user_id, selected_plan, coin_name, coin_desc, contract,
                buy_link, channel, msg_per_hour, enable_new_buy, link_ratio,
                subscription_status, start_date, end_date
            FROM users
            WHERE subscription_status = 'active'
            ORDER BY user_id, channel;
        """)
        rows = cursor.fetchall()
        return [
            {
                "user_id": row[0],
                "selected_plan": row[1],
                "coin_name": row[2],
                "coin_desc": row[3],
                "contract": row[4],
                "buy_link": row[5],
                "channel": row[6],
                "msg_per_hour": row[7] if row[7] is not None else 2,
                "enable_new_buy": bool(row[8]),
                "link_ratio": row[9] if row[9] is not None else 100,
                "subscription_status": row[10],
                "start_date": row[11],
                "end_date": row[12],
            }
            for row in rows
        ]
    finally:
        cursor.close()
        conn.close()
'''

path = Path("/mnt/data/database.py")
path.write_text(code, encoding="utf-8")
compile(code, str(path), "exec")
print("تم إنشاء database.py المصحح والتحقق من Syntax بنجاح.")

import os
import psycopg2
from psycopg2.extras import RealDictCursor


DATABASE_URL = os.getenv("DATABASE_URL")


def get_db_connection():
    """
    Establish a connection to the PostgreSQL / Supabase database.
    """

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set.")

    db_url = DATABASE_URL

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    return psycopg2.connect(db_url)


def init_db():
    """
    Create the main users/campaigns table if it does not exist.

    For the current version of the bot, each (user_id, channel)
    represents one campaign.
    """

    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:

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

        conn.commit()

    finally:
        conn.close()


def add_or_update_user(
    user_id: int,
    username: str,
    end_date: str,
    selected_plan: str = None
):
    """
    Add or update the subscription information of a user.

    This function is kept for future subscription/payment integration.
    """

    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:

            cursor.execute("""
                SELECT user_id
                FROM users
                WHERE user_id = %s
                LIMIT 1;
            """, (user_id,))

            result = cursor.fetchone()

            if result:
                if selected_plan is not None:
                    cursor.execute("""
                        UPDATE users
                        SET username = %s,
                            subscription_status = 'active',
                            end_date = %s,
                            selected_plan = %s
                        WHERE user_id = %s;
                    """, (
                        username,
                        end_date,
                        selected_plan,
                        user_id
                    ))
                else:
                    cursor.execute("""
                        UPDATE users
                        SET username = %s,
                            subscription_status = 'active',
                            end_date = %s
                        WHERE user_id = %s;
                    """, (
                        username,
                        end_date,
                        user_id
                    ))

            else:
                cursor.execute("""
                    INSERT INTO users (
                        user_id,
                        username,
                        selected_plan,
                        subscription_status,
                        end_date
                    )
                    VALUES (%s, %s, %s, 'active', %s);
                """, (
                    user_id,
                    username,
                    selected_plan,
                    end_date
                ))

        conn.commit()

    finally:
        conn.close()


def check_subscription(user_id: int):
    """
    Return the subscription status and end date of a user.
    """

    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:

            cursor.execute("""
                SELECT subscription_status, end_date
                FROM users
                WHERE user_id = %s
                ORDER BY end_date DESC NULLS LAST
                LIMIT 1;
            """, (user_id,))

            return cursor.fetchone()

    finally:
        conn.close()


def get_user_campaigns(user_id: int):
    """
    Return all campaigns/channels belonging to a user.
    """

    conn = get_db_connection()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:

            cursor.execute("""
                SELECT *
                FROM users
                WHERE user_id = %s
                ORDER BY channel;
            """, (user_id,))

            return cursor.fetchall()

    finally:
        conn.close()


def get_campaign(user_id: int, channel: str):
    """
    Return one campaign for a specific user and channel.
    """

    conn = get_db_connection()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:

            cursor.execute("""
                SELECT *
                FROM users
                WHERE user_id = %s
                  AND channel = %s
                LIMIT 1;
            """, (user_id, channel))

            return cursor.fetchone()

    finally:
        conn.close()


def cancel_campaign(user_id: int, channel: str):
    """
    Cancel one campaign without deleting its data.
    """

    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:

            cursor.execute("""
                UPDATE users
                SET subscription_status = 'cancelled'
                WHERE user_id = %s
                  AND channel = %s;
            """, (user_id, channel))

        conn.commit()

    finally:
        conn.close()

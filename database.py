import os
import psycopg2

# Retrieve the connection URL from Render environment variables
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    """Establish a connection to the Supabase database."""
    db_url = DATABASE_URL
    # Adjust postgres:// to postgresql:// if needed for psycopg2 compatibility
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    
    return psycopg2.connect(db_url)

def init_db():
    """Create the users table in Supabase if it doesn't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            subscription_status TEXT DEFAULT 'active',
            start_date TEXT,
            end_date TEXT
        );
    ''')
    
    conn.commit()
    cursor.close()
    conn.close()

def add_or_update_user(user_id: int, username: str, end_date: str):
    """Add a new subscriber or update an existing subscription."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO users (user_id, username, end_date)
        VALUES (%s, %s, %s)
        ON CONFLICT(user_id) DO UPDATE SET
            username=EXCLUDED.username,
            subscription_status='active',
            end_date=EXCLUDED.end_date;
    ''', (user_id, username, end_date))
    
    conn.commit()
    cursor.close()
    conn.close()

def check_subscription(user_id: int):
    """Check if a user is subscribed and return their status."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT subscription_status, end_date FROM users WHERE user_id = %s;', (user_id,))
    result = cursor.fetchone()
    
    cursor.close()
    conn.close()
    return result

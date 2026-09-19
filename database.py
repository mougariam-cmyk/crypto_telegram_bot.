import sqlite3

def init_db():
    """Create the database and users table if they don't exist."""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            subscription_status TEXT DEFAULT 'active',
            start_date TEXT,
            end_date TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

def add_or_update_user(user_id: int, username: str, end_date: str):
    """Add a new subscriber or update an existing subscription."""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO users (user_id, username, end_date)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            subscription_status='active',
            end_date=excluded.end_date
    ''', (user_id, username, end_date))
    
    conn.commit()
    conn.close()

def check_subscription(user_id: int):
    """Check if a user is subscribed and return their status."""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT subscription_status, end_date FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    conn.close()
    return result

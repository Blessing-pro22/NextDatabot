import sqlite3
from typing import Optional, List, Dict

DB_PATH = "blessings_data_hub.db"


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            name TEXT,
            username TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        )
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            telegram_chat_id INTEGER NOT NULL,
            network TEXT NOT NULL,
            volume_mb INTEGER NOT NULL,
            recipient_phone TEXT NOT NULL,
            price_ghs REAL NOT NULL,
            status TEXT NOT NULL,
            paystack_link TEXT,
            failure_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
        )
        conn.commit()


def get_or_create_user(telegram_id: int, name: Optional[str] = None, username: Optional[str] = None) -> Dict:
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        if row:
            return dict(row)
        cur.execute(
            "INSERT INTO users (telegram_id, name, username) VALUES (?, ?, ?)",
            (telegram_id, name, username),
        )
        conn.commit()
        uid = cur.lastrowid
        cur.execute("SELECT * FROM users WHERE id = ?", (uid,))
        return dict(cur.fetchone())


def save_user_phone(telegram_id: int, phone: str) -> None:
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE users SET phone = ? WHERE telegram_id = ?", (phone, telegram_id))
        conn.commit()


def create_order(reference: str, user_id: int, telegram_chat_id: int, network: str, volume_mb: int, recipient_phone: str, price_ghs: float) -> None:
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO orders (reference, user_id, telegram_chat_id, network, volume_mb, recipient_phone, price_ghs, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (reference, user_id, telegram_chat_id, network, volume_mb, recipient_phone, price_ghs, "pending_payment"),
        )
        conn.commit()


def get_order(reference: str) -> Optional[Dict]:
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders WHERE reference = ?", (reference,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_user_orders(telegram_id: int, limit: int = 10) -> List[Dict]:
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        if not row:
            return []
        user_id = row[0]
        cur.execute(
            "SELECT reference, status, volume_mb, recipient_phone FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def update_order_status(reference: str, status: str, failure_reason: Optional[str] = None) -> None:
    with _connect() as conn:
        cur = conn.cursor()
        if failure_reason is not None:
            cur.execute(
                "UPDATE orders SET status = ?, failure_reason = ? WHERE reference = ?",
                (status, failure_reason, reference),
            )
        else:
            cur.execute("UPDATE orders SET status = ? WHERE reference = ?", (status, reference))
        conn.commit()


def set_paystack_link(reference: str, url: str) -> None:
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE orders SET paystack_link = ? WHERE reference = ?", (url, reference))
        conn.commit()


def init_sample_data():
    # helper for quick manual testing if needed
    init_db()

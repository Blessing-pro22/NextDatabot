"""
SQLite data layer.

Three tables:
  users   - one row per Telegram user (with a `banned` flag for moderation)
  orders  - one row per data-bundle purchase, tracked through its full
            lifecycle: pending_payment -> paid -> queued -> processing ->
            delivered / failed / cancelled
  bundles - customer-facing data packages: size, label, price, and whether
            it's currently offered. Seeded from pricing.DEFAULT_BUNDLES on
            first run and fully editable (add/price/remove) from the admin
            panel from then on - no redeploy needed to change your lineup.
"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import config

UTC = timezone.utc


def _now():
    return datetime.now(UTC).isoformat()


def _today():
    return datetime.now(UTC).date().isoformat()

_local = threading.local()


def get_conn():
    """One SQLite connection per thread (Flask + telebot + worker all run
    in different threads)."""
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


@contextmanager
def cursor():
    conn = get_conn()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def init_db():
    with cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                name TEXT,
                username TEXT,
                phone_number TEXT,
                banned INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reference TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                telegram_chat_id INTEGER NOT NULL,
                network TEXT NOT NULL,
                volume_mb INTEGER NOT NULL,
                recipient_phone TEXT NOT NULL,
                price_ghs REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending_payment',
                paystack_authorization_url TEXT,
                hubnet_transaction_id TEXT,
                failure_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bundles (
                volume_mb INTEGER PRIMARY KEY,
                label TEXT NOT NULL,
                price_ghs REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # Legacy table from before bundles were addable/removable - kept
        # only long enough to migrate its rows into `bundles` below.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pricing (
                volume_mb INTEGER PRIMARY KEY,
                price_ghs REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Migration for DBs created before `banned` existed.
        cur.execute("PRAGMA table_info(users)")
        existing_cols = {row["name"] for row in cur.fetchall()}
        if "banned" not in existing_cols:
            cur.execute("ALTER TABLE users ADD COLUMN banned INTEGER NOT NULL DEFAULT 0")

        # Migration: pull any rows from the old fixed-list `pricing` table
        # into `bundles` (only runs once, the first time an existing
        # deployment upgrades to this version).
        cur.execute("SELECT COUNT(*) AS n FROM bundles")
        if cur.fetchone()["n"] == 0:
            cur.execute("SELECT volume_mb, price_ghs FROM pricing")
            legacy_rows = cur.fetchall()
            if legacy_rows:
                from pricing import default_label
                now = _now()
                cur.executemany(
                    "INSERT INTO bundles (volume_mb, label, price_ghs, active, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, ?, ?)",
                    [(r["volume_mb"], default_label(r["volume_mb"]), r["price_ghs"], now, now)
                     for r in legacy_rows],
                )    
            _seed_bundles_if_empty()
def _seed_bundles_if_empty():
    from pricing import DEFAULT_BUNDLES

    with cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM bundles")
        if cur.fetchone()["n"] == 0:
            now = _now()
            # Flatten nested network dictionary, keeping distinct volume_mb
            unique_bundles = {}
            for network, bundles in DEFAULT_BUNDLES.items():
                for mb, b in bundles.items():
                    if mb not in unique_bundles:
                        unique_bundles[mb] = (mb, b["label"], b["price_ghs"], now, now)

            cur.executemany(
                "INSERT INTO bundles (volume_mb, label, price_ghs, active, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                list(unique_bundles.values()),
            )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def get_or_create_user(telegram_id: int, name: str, username: str | None):
    with cursor() as cur:
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        if row:
            return dict(row)
        cur.execute(
            "INSERT INTO users (telegram_id, name, username, created_at) VALUES (?, ?, ?, ?)",
            (telegram_id, name, username, _now()),
        )
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        return dict(cur.fetchone())


def save_user_phone(telegram_id: int, phone_number: str):
    with cursor() as cur:
        cur.execute(
            "UPDATE users SET phone_number = ? WHERE telegram_id = ?",
            (phone_number, telegram_id),
        )


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def create_order(reference, user_id, telegram_chat_id, network, volume_mb,
                  recipient_phone, price_ghs):
    now = _now()
    with cursor() as cur:
        cur.execute("""
            INSERT INTO orders
                (reference, user_id, telegram_chat_id, network, volume_mb,
                 recipient_phone, price_ghs, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_payment', ?, ?)
        """, (reference, user_id, telegram_chat_id, network, volume_mb,
              recipient_phone, price_ghs, now, now))
    return get_order(reference)


def set_paystack_link(reference, authorization_url):
    with cursor() as cur:
        cur.execute(
            "UPDATE orders SET paystack_authorization_url = ?, updated_at = ? WHERE reference = ?",
            (authorization_url, _now(), reference),
        )


def update_order_status(reference, status, hubnet_transaction_id=None, failure_reason=None):
    with cursor() as cur:
        cur.execute("""
            UPDATE orders
            SET status = ?,
                hubnet_transaction_id = COALESCE(?, hubnet_transaction_id),
                failure_reason = COALESCE(?, failure_reason),
                updated_at = ?
            WHERE reference = ?
        """, (status, hubnet_transaction_id, failure_reason,
              _now(), reference))


def get_order(reference):
    with cursor() as cur:
        cur.execute("SELECT * FROM orders WHERE reference = ?", (reference,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_user_orders(telegram_id, limit=10):
    with cursor() as cur:
        cur.execute("""
            SELECT o.* FROM orders o
            JOIN users u ON u.id = o.user_id
            WHERE u.telegram_id = ?
            ORDER BY o.created_at DESC
            LIMIT ?
        """, (telegram_id, limit))
        return [dict(r) for r in cur.fetchall()]


def get_all_orders(limit=15, status=None):
    """Every order across all customers, newest first, with the
    customer's name/username/telegram_id attached for admin display."""
    with cursor() as cur:
        if status:
            cur.execute("""
                SELECT o.*, u.name AS name, u.username AS username, u.telegram_id AS telegram_id
                FROM orders o JOIN users u ON u.id = o.user_id
                WHERE o.status = ?
                ORDER BY o.created_at DESC LIMIT ?
            """, (status, limit))
        else:
            cur.execute("""
                SELECT o.*, u.name AS name, u.username AS username, u.telegram_id AS telegram_id
                FROM orders o JOIN users u ON u.id = o.user_id
                ORDER BY o.created_at DESC LIMIT ?
            """, (limit,))
        return [dict(r) for r in cur.fetchall()]


def get_stats():
    with cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users")
        total_users = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*) AS n FROM orders")
        total_orders = cur.fetchone()["n"]

        cur.execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")
        by_status = {r["status"]: r["n"] for r in cur.fetchall()}

        cur.execute("""
            SELECT COALESCE(SUM(price_ghs), 0) AS total FROM orders
            WHERE status IN ('paid', 'queued', 'processing', 'delivered')
        """)
        revenue_ghs = cur.fetchone()["total"]

        today_prefix = _today()
        cur.execute("SELECT COUNT(*) AS n FROM orders WHERE created_at LIKE ?", (f"{today_prefix}%",))
        orders_today = cur.fetchone()["n"]

        cur.execute("""
            SELECT COALESCE(SUM(price_ghs), 0) AS total FROM orders
            WHERE status IN ('paid', 'queued', 'processing', 'delivered')
              AND created_at LIKE ?
        """, (f"{today_prefix}%",))
        revenue_today_ghs = cur.fetchone()["total"]

    return {
        "total_users": total_users,
        "total_orders": total_orders,
        "delivered": by_status.get("delivered", 0),
        "failed": by_status.get("failed", 0),
        "pending_payment": by_status.get("pending_payment", 0),
        "revenue_ghs": revenue_ghs,
        "orders_today": orders_today,
        "revenue_today_ghs": revenue_today_ghs,
    }


# ---------------------------------------------------------------------------
# Admin: users
# ---------------------------------------------------------------------------

def get_all_users(limit=15):
    with cursor() as cur:
        cur.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]


def count_users():
    with cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users")
        return cur.fetchone()["n"]


def get_all_user_telegram_ids():
    with cursor() as cur:
        cur.execute("SELECT telegram_id FROM users WHERE banned = 0")
        return [r["telegram_id"] for r in cur.fetchall()]


def get_user_by_telegram_id(telegram_id):
    with cursor() as cur:
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def set_banned(telegram_id, banned: bool):
    with cursor() as cur:
        cur.execute(
            "UPDATE users SET banned = ? WHERE telegram_id = ?",
            (1 if banned else 0, telegram_id),
        )


def is_banned(telegram_id) -> bool:
    user = get_user_by_telegram_id(telegram_id)
    return bool(user and user["banned"])


# ---------------------------------------------------------------------------
# Admin: bundles (data packages)
# ---------------------------------------------------------------------------

def get_all_bundles(active_only=True):
    """Bundles for the customer-facing menu, smallest first."""
    with cursor() as cur:
        if active_only:
            cur.execute("SELECT * FROM bundles WHERE active = 1 ORDER BY volume_mb")
        else:
            cur.execute("SELECT * FROM bundles ORDER BY volume_mb")
        return [dict(r) for r in cur.fetchall()]


def get_bundle(volume_mb):
    """A single bundle by size, active or not - used for label/price
    lookups on historical orders even after a bundle is removed."""
    with cursor() as cur:
        cur.execute("SELECT * FROM bundles WHERE volume_mb = ?", (volume_mb,))
        row = cur.fetchone()
        return dict(row) if row else None


def bundle_label(volume_mb) -> str:
    """Best-effort display label: the stored one if the bundle still
    exists, otherwise a sensible default (e.g. for an order referencing a
    bundle that was later removed)."""
    from pricing import default_label
    b = get_bundle(volume_mb)
    return b["label"] if b else default_label(volume_mb)


def add_bundle(volume_mb: int, label: str, price_ghs: float):
    if get_bundle(volume_mb) is not None:
        raise ValueError(f"A bundle for {volume_mb}MB already exists")
    now = _now()
    with cursor() as cur:
        cur.execute(
            "INSERT INTO bundles (volume_mb, label, price_ghs, active, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (volume_mb, label, price_ghs, now, now),
        )


def set_bundle_price(volume_mb, price_ghs):
    with cursor() as cur:
        cur.execute(
            "UPDATE bundles SET price_ghs = ?, updated_at = ? WHERE volume_mb = ?",
            (price_ghs, _now(), volume_mb),
        )


def set_bundle_label(volume_mb, label):
    with cursor() as cur:
        cur.execute(
            "UPDATE bundles SET label = ?, updated_at = ? WHERE volume_mb = ?",
            (label, _now(), volume_mb),
        )


def set_bundle_active(volume_mb, active: bool):
    """Soft delete / restore. We never hard-delete a bundle row, since
    past orders reference it by volume_mb for their price/label history."""
    with cursor() as cur:
        cur.execute(
            "UPDATE bundles SET active = ?, updated_at = ? WHERE volume_mb = ?",
            (1 if active else 0, _now(), volume_mb),
        )
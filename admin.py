"""
Admin-only commands and panel for the Blessings Data Hub bot.

Everything here is gated by config.ADMIN_CHAT_IDS - anyone else's messages
and button taps are silently ignored (no "access denied" reply, so the
existence of /admin isn't advertised to regular customers).

Covers:
  /admin        - inline panel: stats, transactions, users, bundles, broadcast
  /ban <id>     - block a user from ordering
  /unban <id>   - unblock a user
  /retry <ref>  - re-queue a failed order for Hubnet delivery

bot.py wires this in via `admin.register(bot)` and routes free-text
messages through `admin.handle_admin_text(...)` first, since adding a
bundle, editing a price, and composing a broadcast are all multi-step
conversations.
"""

import logging
import threading
import time

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
import db
import pricing
import worker

logger = logging.getLogger("admin")

# chat_id -> {"step": "awaiting_new_price", "volume_mb": 1000}
#          | {"step": "awaiting_broadcast_text"}
#          | {"step": "awaiting_broadcast_confirm", "text": "..."}
#          | {"step": "awaiting_new_bundle_volume"}
#          | {"step": "awaiting_new_bundle_label", "volume_mb": ..., "default_label": ...}
#          | {"step": "awaiting_new_bundle_price", "volume_mb": ..., "label": ...}
_admin_sessions: dict[int, dict] = {}


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_CHAT_IDS


def _status_emoji(status):
    return {
        "pending_payment": "🕓", "paid": "💳", "queued": "⏳",
        "processing": "⚙️", "delivered": "✅", "failed": "❌", "cancelled": "🚫",
    }.get(status, "•")


def _menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📊 Stats", callback_data="admin:stats"),
        InlineKeyboardButton("📋 Transactions", callback_data="admin:transactions"),
    )
    kb.add(
        InlineKeyboardButton("👥 Users", callback_data="admin:users"),
        InlineKeyboardButton("📦 Bundles", callback_data="admin:bundles"),
    )
    kb.add(InlineKeyboardButton("📢 Broadcast", callback_data="admin:broadcast"))
    return kb


def _back_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("« Back", callback_data="admin:menu"))
    return kb


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(bot):

    @bot.message_handler(commands=["admin"])
    def admin_panel(message):
        if not is_admin(message.from_user.id):
            return
        bot.send_message(message.chat.id, "🛠 <b>Admin Panel</b>", reply_markup=_menu_keyboard())

    @bot.callback_query_handler(func=lambda c: is_admin(c.from_user.id) and c.data == "admin:menu")
    def back_to_menu(call):
        bot.edit_message_text("🛠 <b>Admin Panel</b>", call.message.chat.id,
                               call.message.message_id, reply_markup=_menu_keyboard())

    @bot.callback_query_handler(func=lambda c: is_admin(c.from_user.id) and c.data == "admin:stats")
    def show_stats(call):
        s = db.get_stats()
        text = (
            f"<b>📊 Stats</b>\n\n"
            f"Users: {s['total_users']}\n"
            f"Orders: {s['total_orders']}  "
            f"(✅ {s['delivered']}  ❌ {s['failed']}  🕓 {s['pending_payment']})\n\n"
            f"Total revenue: GHS {s['revenue_ghs']:.2f}\n"
            f"Today: {s['orders_today']} orders, GHS {s['revenue_today_ghs']:.2f}"
        )
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                               reply_markup=_back_keyboard())

    @bot.callback_query_handler(func=lambda c: is_admin(c.from_user.id) and c.data == "admin:transactions")
    def show_transactions(call):
        orders = db.get_all_orders(limit=15)
        if not orders:
            text = "No transactions yet."
        else:
            lines = ["<b>📋 Last 15 transactions</b>\n"]
            for o in orders:
                who = f"@{o['username']}" if o["username"] else (o["name"] or str(o["telegram_id"]))
                bundle = db.bundle_label(o["volume_mb"])
                lines.append(
                    f"{_status_emoji(o['status'])} {bundle} GHS {o['price_ghs']:.2f} "
                    f"→ {o['recipient_phone']} ({who})\n"
                    f"   {o['status']} · <code>{o['reference']}</code>"
                )
            text = "\n".join(lines)
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                               reply_markup=_back_keyboard())

    @bot.callback_query_handler(func=lambda c: is_admin(c.from_user.id) and c.data == "admin:users")
    def show_users(call):
        users = db.get_all_users(limit=15)
        total = db.count_users()
        lines = [f"<b>👥 Users</b> (total: {total}, latest 15 shown)\n"]
        for u in users:
            tag = f"@{u['username']}" if u["username"] else (u["name"] or "unknown")
            ban = " 🚫" if u["banned"] else ""
            lines.append(f"{tag}{ban} — <code>{u['telegram_id']}</code>")
        lines.append("\nUse /ban &lt;telegram_id&gt; or /unban &lt;telegram_id&gt; to moderate.")
        bot.edit_message_text("\n".join(lines), call.message.chat.id, call.message.message_id,
                               reply_markup=_back_keyboard())

    @bot.callback_query_handler(func=lambda c: is_admin(c.from_user.id) and c.data == "admin:bundles")
    def show_bundles(call):
        bundles = db.get_all_bundles(active_only=True)
        kb = InlineKeyboardMarkup(row_width=1)
        for b in bundles:
            kb.add(InlineKeyboardButton(
                f"{b['label']} — GHS {b['price_ghs']:.2f}  (edit price)",
                callback_data=f"admin:setprice:{b['volume_mb']}",
            ))
        kb.add(InlineKeyboardButton("➕ Add Bundle", callback_data="admin:addbundle"))
        if bundles:
            kb.add(InlineKeyboardButton("🗑 Remove a Bundle", callback_data="admin:removebundle_menu"))
        kb.add(InlineKeyboardButton("« Back", callback_data="admin:menu"))
        bot.edit_message_text(
            "<b>📦 Bundles</b>\nTap a bundle to change its price, or manage the list below.",
            call.message.chat.id, call.message.message_id, reply_markup=kb,
        )

    @bot.callback_query_handler(func=lambda c: is_admin(c.from_user.id) and c.data.startswith("admin:setprice:"))
    def ask_new_price(call):
        volume_mb = int(call.data.split(":")[2])
        label = db.bundle_label(volume_mb)
        _admin_sessions[call.message.chat.id] = {"step": "awaiting_new_price", "volume_mb": volume_mb}
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"Send the new price for <b>{label}</b> in GHS (e.g. 8.50):")

    @bot.callback_query_handler(func=lambda c: is_admin(c.from_user.id) and c.data == "admin:addbundle")
    def start_add_bundle(call):
        _admin_sessions[call.message.chat.id] = {"step": "awaiting_new_bundle_volume"}
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"Send the new bundle size in MB (e.g. 3000 for 3GB, 750 for 750MB). "
            f"Hubnet accepts {pricing.MIN_VOLUME_MB}–{pricing.MAX_VOLUME_MB:,} MB per transaction.\n"
            f"Send /cancel anytime to stop.",
        )

    @bot.callback_query_handler(func=lambda c: is_admin(c.from_user.id) and c.data == "admin:removebundle_menu")
    def removebundle_menu(call):
        bundles = db.get_all_bundles(active_only=True)
        kb = InlineKeyboardMarkup(row_width=1)
        for b in bundles:
            kb.add(InlineKeyboardButton(f"🗑 {b['label']}", callback_data=f"admin:removebundle_confirm:{b['volume_mb']}"))
        kb.add(InlineKeyboardButton("« Back", callback_data="admin:bundles"))
        bot.edit_message_text(
            "Tap a bundle to remove it from the customer menu. Past orders for it are unaffected.",
            call.message.chat.id, call.message.message_id, reply_markup=kb,
        )

    @bot.callback_query_handler(func=lambda c: is_admin(c.from_user.id) and c.data.startswith("admin:removebundle_confirm:"))
    def removebundle_confirm(call):
        volume_mb = int(call.data.split(":")[2])
        label = db.bundle_label(volume_mb)
        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("✅ Yes, remove", callback_data=f"admin:removebundle_do:{volume_mb}"),
            InlineKeyboardButton("❌ Cancel", callback_data="admin:bundles"),
        )
        bot.edit_message_text(
            f"Remove <b>{label}</b> from the menu? Existing orders keep their history.",
            call.message.chat.id, call.message.message_id, reply_markup=kb,
        )

    @bot.callback_query_handler(func=lambda c: is_admin(c.from_user.id) and c.data.startswith("admin:removebundle_do:"))
    def removebundle_do(call):
        volume_mb = int(call.data.split(":")[2])
        db.set_bundle_active(volume_mb, False)
        bot.answer_callback_query(call.id, "Removed.")
        show_bundles(call)

    @bot.callback_query_handler(func=lambda c: is_admin(c.from_user.id) and c.data == "admin:broadcast")
    def start_broadcast(call):
        _admin_sessions[call.message.chat.id] = {"step": "awaiting_broadcast_text"}
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "Send the message to broadcast to all users:")

    @bot.callback_query_handler(func=lambda c: is_admin(c.from_user.id) and c.data == "admin:broadcast_confirm")
    def confirm_broadcast(call):
        session = _admin_sessions.get(call.message.chat.id)
        if not session or session.get("step") != "awaiting_broadcast_confirm":
            bot.answer_callback_query(call.id, "Nothing pending to send.")
            return
        text = session["text"]
        _admin_sessions.pop(call.message.chat.id, None)
        bot.edit_message_text("📢 Broadcasting…", call.message.chat.id, call.message.message_id)
        threading.Thread(
            target=_run_broadcast, args=(bot, call.message.chat.id, text),
            daemon=True, name="broadcast",
        ).start()

    @bot.callback_query_handler(func=lambda c: is_admin(c.from_user.id) and c.data == "admin:broadcast_cancel")
    def cancel_broadcast(call):
        _admin_sessions.pop(call.message.chat.id, None)
        bot.edit_message_text("Broadcast cancelled.", call.message.chat.id, call.message.message_id)

    @bot.message_handler(commands=["ban"])
    def ban_cmd(message):
        if not is_admin(message.from_user.id):
            return
        _toggle_ban(bot, message, banned=True)

    @bot.message_handler(commands=["unban"])
    def unban_cmd(message):
        if not is_admin(message.from_user.id):
            return
        _toggle_ban(bot, message, banned=False)

    @bot.message_handler(commands=["retry"])
    def retry_cmd(message):
        if not is_admin(message.from_user.id):
            return
        parts = message.text.split()
        if len(parts) != 2:
            bot.reply_to(message, "Usage: /retry <reference>")
            return
        reference = parts[1]
        order = db.get_order(reference)
        if not order:
            bot.reply_to(message, "No order found with that reference.")
            return
        db.update_order_status(reference, "paid")
        worker.enqueue_order(reference)
        bot.reply_to(message, f"Re-queued {reference} for Hubnet delivery.")


def _toggle_ban(bot, message, banned):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        bot.reply_to(message, f"Usage: /{'ban' if banned else 'unban'} <telegram_id>")
        return
    telegram_id = int(parts[1])
    db.set_banned(telegram_id, banned)
    bot.reply_to(message, f"User {telegram_id} {'banned' if banned else 'unbanned'}.")


# ---------------------------------------------------------------------------
# Multi-step text flows (new price, broadcast composing)
# ---------------------------------------------------------------------------

def handle_admin_text(bot, message) -> bool:
    """Called from bot.py's catch-all text handler before its own logic.
    Returns True if this message was an admin flow reply and should not be
    processed any further."""
    if not is_admin(message.from_user.id):
        return False
    session = _admin_sessions.get(message.chat.id)
    if not session:
        return False

    if message.text and message.text.strip().lower() in ("/cancel", "cancel"):
        _admin_sessions.pop(message.chat.id, None)
        bot.reply_to(message, "Cancelled.")
        return True

    if session["step"] == "awaiting_new_price":
        try:
            new_price = float(message.text.strip())
            if new_price <= 0:
                raise ValueError
        except ValueError:
            bot.reply_to(message, "Please send a valid positive number, e.g. 8.50")
            return True
        volume_mb = session["volume_mb"]
        label = db.bundle_label(volume_mb)
        db.set_bundle_price(volume_mb, new_price)
        _admin_sessions.pop(message.chat.id, None)
        bot.reply_to(message, f"✅ {label} price updated to GHS {new_price:.2f}")
        return True

    if session["step"] == "awaiting_new_bundle_volume":
        text = message.text.strip()
        if not text.isdigit():
            bot.reply_to(message, "Please send the bundle size in MB as a whole number, e.g. 3000")
            return True
        volume_mb = int(text)
        if not (pricing.MIN_VOLUME_MB <= volume_mb <= pricing.MAX_VOLUME_MB):
            bot.reply_to(
                message,
                f"Hubnet only accepts {pricing.MIN_VOLUME_MB}\u2013{pricing.MAX_VOLUME_MB:,} MB per "
                f"transaction. Please send a value in that range.",
            )
            return True
        existing = db.get_bundle(volume_mb)
        if existing:
            status = "active" if existing["active"] else "inactive (hidden from customers)"
            _admin_sessions.pop(message.chat.id, None)
            bot.reply_to(
                message,
                f"A bundle for {volume_mb}MB already exists: <b>{existing['label']}</b> at "
                f"GHS {existing['price_ghs']:.2f} ({status}). Go to Bundles to edit or reactivate "
                f"it instead of adding a duplicate.",
            )
            return True
        default_label = pricing.default_label(volume_mb)
        _admin_sessions[message.chat.id] = {
            "step": "awaiting_new_bundle_label",
            "volume_mb": volume_mb,
            "default_label": default_label,
        }
        bot.reply_to(
            message,
            f"What label should customers see for this bundle? Send a name, or send "
            f"\"-\" to use the default: <b>{default_label}</b>",
        )
        return True

    if session["step"] == "awaiting_new_bundle_label":
        text = message.text.strip()
        label = session["default_label"] if text == "-" else text
        if not label:
            bot.reply_to(message, "Please send a label, or \"-\" for the default.")
            return True
        session["step"] = "awaiting_new_bundle_price"
        session["label"] = label
        bot.reply_to(message, f"Send the price for <b>{label}</b> in GHS (e.g. 16.00):")
        return True

    if session["step"] == "awaiting_new_bundle_price":
        try:
            price_ghs = float(message.text.strip())
            if price_ghs <= 0:
                raise ValueError
        except ValueError:
            bot.reply_to(message, "Please send a valid positive number, e.g. 16.00")
            return True
        volume_mb = session["volume_mb"]
        label = session["label"]
        _admin_sessions.pop(message.chat.id, None)
        try:
            db.add_bundle(volume_mb, label, price_ghs)
        except ValueError as exc:
            bot.reply_to(message, f"⚠️ Couldn't add bundle: {exc}")
            return True
        bot.reply_to(message, f"✅ Added new bundle: <b>{label}</b> ({volume_mb}MB) — GHS {price_ghs:.2f}")
        return True

    if session["step"] == "awaiting_broadcast_text":
        session["step"] = "awaiting_broadcast_confirm"
        session["text"] = message.text
        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("✅ Send", callback_data="admin:broadcast_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="admin:broadcast_cancel"),
        )
        preview = message.text if len(message.text) < 500 else message.text[:500] + "…"
        bot.send_message(message.chat.id, f"Preview:\n\n{preview}\n\nSend to all users?", reply_markup=kb)
        return True

    return False


def _run_broadcast(bot, admin_chat_id, text):
    telegram_ids = db.get_all_user_telegram_ids()
    sent, failed = 0, 0
    for telegram_id in telegram_ids:
        try:
            bot.send_message(telegram_id, text)
            sent += 1
        except Exception:
            failed += 1
        time.sleep(0.05)  # ~20 msgs/sec, safely under Telegram's rate limits
    try:
        bot.send_message(admin_chat_id, f"📢 Broadcast finished. Sent: {sent}, failed: {failed}.")
    except Exception:
        logger.exception("Failed to send broadcast summary")
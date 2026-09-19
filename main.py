"""
Blessings Data Hub - Telegram bot entry point.

Conversation flow:
  /start
    -> register user, show main menu
  "📶 Buy Data"
    -> inline keyboard: pick network
  pick network
    -> inline keyboard: pick bundle size (with price)
  pick bundle
    -> ask customer to type the recipient's phone number
  type phone number
    -> show order summary with Confirm / Cancel buttons
  Confirm
    -> create order, initialize Paystack payment, send "Pay Now" link
  (payment + delivery happen asynchronously - see webhook_server.py / worker.py)
  "📦 My Orders"
    -> list recent orders and their status
"""

import logging
import re

import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
)

import admin
import config
import db
import hubnet_api
import paystack_api
import worker
from pricing import DEFAULT_BUNDLES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

bot = telebot.TeleBot(token=config.TELEGRAM_API_TOKEN, parse_mode="HTML")
admin.register(bot)

PHONE_RE = re.compile(r"^0\d{9}$")

# In-memory per-chat conversation state.
# { chat_id: {"step": "awaiting_phone", "network": "mtn", "volume_mb": 1000} }
_sessions: dict[int, dict] = {}

MAIN_MENU = ReplyKeyboardMarkup(resize_keyboard=True)
MAIN_MENU = ReplyKeyboardMarkup(resize_keyboard=True)
MAIN_MENU.add("📶 Buy Data", "📦 My Orders")
MAIN_MENU.add("❓ Help", "📞 Contact Us")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _network_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton(info["label"], callback_data=f"net:{key}")
        for key, info in config.NETWORKS.items()
    ]
    kb.add(*buttons)
    return kb


def _get_network_bundles(network_key):
    # Try exact match, then uppercase match
    if network_key in DEFAULT_BUNDLES:
        return DEFAULT_BUNDLES[network_key]
    
    # Try match by network label or uppercase
    net_info = config.NETWORKS.get(network_key, {})
    label = net_info.get("label", "").upper()
    for k in DEFAULT_BUNDLES:
        if k.upper() == label or k.upper() == str(network_key).upper():
            return DEFAULT_BUNDLES[k]
    
    # Fallback to first available network if not matched
    return next(iter(DEFAULT_BUNDLES.values()))


def _bundle_keyboard(network_key):
    net_bundles = _get_network_bundles(network_key)
    kb = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton(
            f"{info['label']} - GHS {info['price_ghs']:.2f}",
            callback_data=f"bundle:{network_key}:{volume_mb}"
        )
        for volume_mb, info in net_bundles.items()
    ]
    kb.add(*buttons)
    kb.add(InlineKeyboardButton("« Back", callback_data="back:networks"))
    return kb


def _confirm_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✅ Confirm & Pay", callback_data="confirm_order"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_order"),
    )
    return kb


def _order_summary_text(network_key, volume_mb, phone):
    label = config.NETWORKS[network_key]["label"]
    net_bundles = _get_network_bundles(network_key)
    bundle = net_bundles.get(volume_mb, {"label": f"{volume_mb} MB", "price_ghs": 0.0})
    price = bundle["price_ghs"]
    return (
        f"<b>Order Summary</b>\n"
        f"Network: {label}\n"
        f"Bundle: {bundle['label']}\n"
        f"Recipient: {phone}\n"
        f"Price: GHS {price:.2f}\n\n"
        f"Confirm to proceed to payment."
    )


def _status_emoji(status):
    return {
        "pending_payment": "🕓",
        "paid": "💳",
        "queued": "⏳",
        "processing": "⚙️",
        "delivered": "✅",
        "failed": "❌",
        "cancelled": "🚫",
    }.get(status, "•")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

@bot.message_handler(commands=["start"])
def send_welcome(message):
    db.get_or_create_user(
        telegram_id=message.from_user.id,
        name=message.from_user.first_name,
        username=message.from_user.username,
    )
    if db.is_banned(message.from_user.id):
        bot.send_message(message.chat.id, "🚫 You're currently restricted from using this bot. Contact support.")
        return
    _sessions.pop(message.chat.id, None)
    text = (
        f"Hello {message.from_user.first_name}! Welcome to <b>Blessings Data Hub</b>.\n"
        f"Buy MTN, AirtelTigo, big-time or Telecel data bundles right here, "
        f"pay securely by card/mobile money, and we deliver instantly.\n\n"
        f"Tap <b>📶 Buy Data</b> to get started."
    )
    bot.send_message(message.chat.id, text, reply_markup=MAIN_MENU)


@bot.message_handler(func=lambda m: m.text == "📶 Buy Data")
def buy_data(message):
    if db.is_banned(message.from_user.id):
        bot.send_message(message.chat.id, "🚫 You're currently restricted from using this bot. Contact support.")
        return
    _sessions[message.chat.id] = {"step": "awaiting_network"}
    bot.send_message(message.chat.id, "Choose a network:", reply_markup=_network_keyboard())


@bot.message_handler(func=lambda m: m.text == "📦 My Orders")
def my_orders(message):
    orders = db.get_user_orders(message.from_user.id, limit=10)
    if not orders:
        bot.send_message(message.chat.id, "You haven't placed any orders yet.")
        return
    lines = ["<b>Your recent orders</b>\n"]
    for o in orders:
        lines.append(
            f"{_status_emoji(o['status'])} {db.bundle_label(o['volume_mb'])} "
            f"→ {o['recipient_phone']} — {o['status'].replace('_', ' ')}\n"
            f"   ref: {o['reference']}"
        )
    bot.send_message(message.chat.id, "\n".join(lines))


@bot.message_handler(func=lambda m: m.text == "❓ Help")
def help_cmd(message):
    bot.send_message(
        message.chat.id,
        "Tap <b>📶 Buy Data</b>, pick a network and bundle size, enter the "
        "recipient's phone number, then pay via the secure link we send you. "
        "Data is delivered automatically once payment clears.\n\n"
        "Having an issue with an order? Send /orders and quote the reference "
        "number to our support team.",
    )
@bot.message_handler(commands=["contact"])
@bot.message_handler(func=lambda m: m.text == "📞 Contact Us")
def contact_cmd(message):
    text = (
        "<b>Customer Support</b>\n\n"
        "Need help with your order or have inquiries?\n"
        "Reach out to us directly:\n\n"
        "📞 <b>Phone / WhatsApp:</b> <code>YOUR_PHONE_NUMBER_HERE</code>\n\n"
        "<i>Available 24/7 for order support.</i>"
    )
    bot.send_message(message.chat.id, text)

# ---------------------------------------------------------------------------
# Callback (inline button) handlers
# ---------------------------------------------------------------------------

@bot.callback_query_handler(func=lambda call: call.data.startswith("net:"))
def on_network_chosen(call):
    network_key = call.data.split(":", 1)[1]
    _sessions[call.message.chat.id] = {"step": "awaiting_bundle", "network": network_key}
    bot.edit_message_text(
        f"Network: {config.NETWORKS[network_key]['label']}\nNow choose a bundle size:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=_bundle_keyboard(network_key),
    )


@bot.callback_query_handler(func=lambda call: call.data == "back:networks")
def on_back_to_networks(call):
    _sessions[call.message.chat.id] = {"step": "awaiting_network"}
    bot.edit_message_text(
        "Choose a network:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=_network_keyboard(),
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("bundle:"))
def on_bundle_chosen(call):
    _, network_key, volume_mb = call.data.split(":")
    volume_mb = int(volume_mb)
    net_bundles = _get_network_bundles(network_key)
    bundle = net_bundles.get(volume_mb)
    if not bundle:
        bot.answer_callback_query(call.id, "That bundle is no longer available.")
        bot.edit_message_text(
            f"Network: {config.NETWORKS[network_key]['label']}\nNow choose a bundle size:",
            call.message.chat.id, call.message.message_id,
            reply_markup=_bundle_keyboard(network_key),
        )
        return
    _sessions[call.message.chat.id] = {
        "step": "awaiting_phone",
        "network": network_key,
        "volume_mb": volume_mb,
    }
    bot.send_message(
        call.message.chat.id,
        f"Got it — {bundle['label']} on {config.NETWORKS[network_key]['label']}.\n"
        f"Please type the <b>recipient's phone number</b> (e.g. 0241234567):",
    )


@bot.callback_query_handler(func=lambda call: call.data == "confirm_order")
def on_confirm_order(call):
    session = _sessions.get(call.message.chat.id)
    if not session or session.get("step") != "awaiting_confirmation":
        bot.answer_callback_query(call.id, "This order has expired, please start again.")
        return

    network_key = session["network"]
    volume_mb = session["volume_mb"]
    phone = session["phone"]
    net_bundles = _get_network_bundles(network_key)
    bundle = net_bundles.get(volume_mb)
    if not bundle:
        bot.answer_callback_query(call.id, "That bundle is no longer available.")
        bot.send_message(call.message.chat.id, "That bundle was just removed. Please choose another one.")
        _sessions.pop(call.message.chat.id, None)
    return
    price = bundle["price_ghs"]

    user = db.get_or_create_user(
        telegram_id=call.from_user.id,
        name=call.from_user.first_name,
        username=call.from_user.username,
    )
    reference = hubnet_api.generate_reference(prefix="BDH")
    db.create_order(
        reference=reference,
        user_id=user["id"],
        telegram_chat_id=call.message.chat.id,
        network=config.NETWORKS[network_key]["token"],
        volume_mb=volume_mb,
        recipient_phone=phone,
        price_ghs=price,
    )

    # Paystack requires an email and validates that it looks like a real
    # domain (fake TLDs like ".customers" get rejected with "Invalid Email
    # Address Passed"), so we use a placeholder on a normal .com domain,
    # deterministic per Telegram user.
    email = f"tg{call.from_user.id}@blessingsdatahub.com"

    try:
        init_resp = paystack_api.initialize_transaction(
            email=email,
            amount_ghs=price,
            reference=reference,
            metadata={
                "telegram_chat_id": call.message.chat.id,
                "network": network_key,
                "volume_mb": volume_mb,
                "recipient_phone": phone,
            },
        )
    except paystack_api.PaystackError as exc:
        db.update_order_status(reference, "failed", failure_reason=str(exc))
        bot.send_message(call.message.chat.id, f"⚠️ Couldn't start payment: {exc}. Please try again.")
        _sessions.pop(call.message.chat.id, None)
        return

    pay_url = init_resp["data"]["authorization_url"]
    db.set_paystack_link(reference, pay_url)

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💳 Pay Now", url=pay_url))
    bot.send_message(
        call.message.chat.id,
        f"Order created (ref <code>{reference}</code>). Tap below to pay "
        f"GHS {price:.2f} securely via Paystack. Your data will be delivered "
        f"automatically the moment payment is confirmed.",
        reply_markup=kb,
    )
    _sessions.pop(call.message.chat.id, None)


@bot.callback_query_handler(func=lambda call: call.data == "cancel_order")
def on_cancel_order(call):
    _sessions.pop(call.message.chat.id, None)
    bot.edit_message_text("Order cancelled.", call.message.chat.id, call.message.message_id)


# ---------------------------------------------------------------------------
# Free-text handler (phone number entry) - must be registered last so it
# only catches messages not matched by the menu handlers above.
# ---------------------------------------------------------------------------

@bot.message_handler(func=lambda message: True)
def free_text(message):
    if admin.handle_admin_text(bot, message):
        return

    session = _sessions.get(message.chat.id)

    if session and session.get("step") == "awaiting_phone":
        phone = message.text.strip()
        if not PHONE_RE.match(phone):
            bot.reply_to(
                message,
                "That doesn't look like a valid number. Please enter a 10-digit "
                "number starting with 0, e.g. 0241234567.",
            )
            return
        session["phone"] = phone
        session["step"] = "awaiting_confirmation"
        db.save_user_phone(message.from_user.id, phone)
        bot.send_message(
            message.chat.id,
            _order_summary_text(session["network"], session["volume_mb"], phone),
            reply_markup=_confirm_keyboard(),
        )
        return

    # Fallback for anything else
    bot.reply_to(
        message,
        "Not sure what you mean 🙂 Use the menu below, or tap 📶 Buy Data to "
        "purchase a bundle.",
        reply_markup=MAIN_MENU,
    )


def run_polling():
    db.init_db()
    worker.init(bot)
    worker.start()
    logger.info("Bot polling started")
    bot.infinity_polling()

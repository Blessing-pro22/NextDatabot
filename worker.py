"""
A single background worker thread that delivers paid orders through Hubnet.

Why a queue instead of calling Hubnet straight from the webhook handler?
  - Hubnet is rate limited to 5 req/min. With many customers paying at once,
    calling Hubnet inline from each webhook would pile up and start hitting
    1007 (rate limited) errors.
  - A single-consumer queue naturally serializes delivery and lets
    hubnet_api's RateLimiter pace requests correctly.

enqueue_order() is called from the Paystack webhook once payment is
confirmed. The worker thread then calls Hubnet, updates the order status in
the DB, and notifies the customer via the bot.
"""

import logging
import queue
import threading

import config
import db
import hubnet_api

logger = logging.getLogger("worker")

_order_queue: "queue.Queue[str]" = queue.Queue()
_bot = None  # set via init()


def init(bot_instance):
    """Give the worker a reference to the running TeleBot instance so it can
    message customers once a delivery finishes."""
    global _bot
    _bot = bot_instance


def enqueue_order(reference: str):
    db.update_order_status(reference, "queued")
    _order_queue.put(reference)


def _notify(chat_id, text):
    if _bot is None:
        logger.warning("Bot not initialized, cannot notify chat %s: %s", chat_id, text)
        return
    try:
        _bot.send_message(chat_id, text)
    except Exception:
        logger.exception("Failed to notify chat %s", chat_id)


def _notify_admins(text):
    for admin_id in config.ADMIN_CHAT_IDS:
        _notify(admin_id, text)


def _process_order(reference: str):
    order = db.get_order(reference)
    if not order:
        logger.error("Queued reference %s not found in DB", reference)
        return
    if order["status"] not in ("queued", "paid"):
        # Already processed (e.g. duplicate webhook delivery) - skip.
        return

    db.update_order_status(reference, "processing")
    network_token = order["network"]

    try:
        hubnet_api.make_transaction(
            network_token=network_token,
            phone=order["recipient_phone"],
            volume_mb=order["volume_mb"],
            reference=reference,
            webhook_url=f"{config.PUBLIC_BASE_URL}{config.HUBNET_WEBHOOK_PATH}",
        )
    except hubnet_api.HubnetError as exc:
        db.update_order_status(reference, "failed", failure_reason=str(exc))
        _notify(
            order["telegram_chat_id"],
            f"⚠️ We couldn't deliver your {order['volume_mb']}MB bundle "
            f"(ref {reference}): {exc}\nYour payment is safe — contact support "
            f"and quote this reference for a refund or retry.",
        )
        _notify_admins(f"🚨 Hubnet delivery failed for {reference}: {exc}")
        return
    except Exception as exc:  # network errors, timeouts, etc.
        db.update_order_status(reference, "failed", failure_reason=str(exc))
        _notify(
            order["telegram_chat_id"],
            f"⚠️ Something went wrong delivering your bundle (ref {reference}). "
            f"Support has been notified.",
        )
        _notify_admins(f"🚨 Unexpected error delivering {reference}: {exc}")
        return

    # Hubnet accepted the request. Final delivered/failed status will arrive
    # via the Hubnet webhook - see webhook_server.py. Let the customer know
    # it's on the way.
    _notify(
        order["telegram_chat_id"],
        f"✅ Payment confirmed! Your {order['volume_mb']}MB bundle to "
        f"{order['recipient_phone']} is being processed (ref {reference}). "
        f"We'll message you once it's delivered.",
    )


def _run():
    while True:
        reference = _order_queue.get()
        try:
            _process_order(reference)
        except Exception:
            logger.exception("Unhandled error processing order %s", reference)
        finally:
            _order_queue.task_done()


def start():
    thread = threading.Thread(target=_run, daemon=True, name="hubnet-delivery-worker")
    thread.start()
    logger.info("Hubnet delivery worker started")
    return thread

"""
Public HTTP endpoints that Paystack and Hubnet POST to.

/webhook/paystack  - Paystack calls this when a payment's status changes.
                      On charge.success we mark the order paid and hand it
                      to the worker queue for Hubnet delivery.
/webhook/hubnet     - Hubnet calls this (we pass this URL as `webhook` in
                       every transaction request) with delivery status
                       updates: processing -> delivered / failed.

Both of these need to be reachable over the public internet via HTTPS -
run this behind a real domain + TLS in production, or via a tunnel like
ngrok while developing locally.
"""

import logging

from flask import Flask, request, jsonify

import config
import db
import worker

logger = logging.getLogger("webhook_server")
app = Flask(__name__)


@app.post(config.PAYSTACK_WEBHOOK_PATH)
def paystack_webhook():
    import paystack_api

    signature = request.headers.get("x-paystack-signature", "")
    if not paystack_api.verify_webhook_signature(request.get_data(), signature):
        logger.warning("Rejected Paystack webhook with bad signature")
        return jsonify({"error": "invalid signature"}), 401

    event = request.get_json(silent=True) or {}
    event_type = event.get("event")
    reference = event.get("data", {}).get("reference")

    if event_type == "charge.success" and reference:
        order = db.get_order(reference)
        if not order:
            logger.warning("Paystack webhook for unknown order %s", reference)
            return jsonify({"received": True}), 200

        if order["status"] == "pending_payment":
            # Double-check with Paystack directly rather than trusting the
            # webhook body alone - defends against spoofed/replayed events.
            verified = paystack_api.verify_transaction(reference)
            paid = verified.get("data", {}).get("status") == "success"
            if paid:
                db.update_order_status(reference, "paid")
                worker.enqueue_order(reference)
            else:
                logger.warning("Webhook said success but verify disagreed for %s", reference)

    return jsonify({"received": True}), 200


@app.post(config.HUBNET_WEBHOOK_PATH)
def hubnet_webhook():
    event = request.get_json(silent=True) or {}
    data = event.get("data", {})
    reference = data.get("reference")
    status = str(data.get("status", "")).lower()

    if not reference:
        return jsonify({"received": True}), 200

    order = db.get_order(reference)
    if not order:
        logger.warning("Hubnet webhook for unknown order %s", reference)
        return jsonify({"received": True}), 200

    if status in ("delivered", "success", "completed"):
        db.update_order_status(reference, "delivered")
        worker._notify(
            order["telegram_chat_id"],
            f"📶 Delivered! {order['volume_mb']}MB has landed on "
            f"{order['recipient_phone']} (ref {reference}). Enjoy!",
        )
    elif status in ("failed", "cancelled"):
        db.update_order_status(reference, "failed", failure_reason=data.get("message"))
        worker._notify(
            order["telegram_chat_id"],
            f"⚠️ Delivery failed for ref {reference}: {data.get('message', 'unknown error')}. "
            f"Contact support for a refund or retry.",
        )
        worker._notify_admins(f"🚨 Hubnet reported failed delivery for {reference}")
    # "processing"/"pending" -> no state change needed, we already told the
    # customer their order is being processed.

    return jsonify({"received": True}), 200


@app.get("/payment/callback")
def payment_callback():
    """Paystack redirects the customer's browser here after checkout.
    This page doesn't decide anything (the webhook above is what actually
    confirms payment and triggers delivery) - it just gets the customer
    back into Telegram with a friendly message while that happens."""
    reference = request.args.get("reference", "")
    order = db.get_order(reference) if reference else None
    telegram_link = f"https://t.me/{config.TELEGRAM_BOT_USERNAME}"

    if order and order["status"] in ("paid", "queued", "processing", "delivered"):
        message = "Payment received! Head back to Telegram — your bundle is on its way."
    else:
        message = "Thanks! We're confirming your payment now. Head back to Telegram for updates."

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Blessings Data Hub</title>
      <style>
        body {{ font-family: -apple-system, sans-serif; text-align: center;
                padding: 60px 20px; background: #111; color: #eee; }}
        a.button {{ display: inline-block; margin-top: 24px; padding: 14px 28px;
                    background: #229ED9; color: white; text-decoration: none;
                    border-radius: 8px; font-weight: bold; }}
      </style>
    </head>
    <body>
      <h2>✅ {message}</h2>
      <a class="button" href="{telegram_link}">Return to Telegram</a>
    </body>
    </html>
    """
    return html


@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


def run():
    app.run(host=config.WEBHOOK_SERVER_HOST, port=config.WEBHOOK_SERVER_PORT)

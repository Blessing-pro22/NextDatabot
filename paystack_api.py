"""
Thin client around Paystack's transaction API, used to collect payment from
customers before we deliver a data bundle via Hubnet.

Docs: https://paystack.com/docs/api/transaction/
"""

import hashlib
import hmac

import requests

import config


class PaystackError(Exception):
    pass


def _headers():
    return {
        "Authorization": f"Bearer {config.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def initialize_transaction(email: str, amount_ghs: float, reference: str, metadata: dict):
    """Create a Paystack checkout session. Returns the JSON body, which
    contains data.authorization_url to send the customer to."""
    from pricing import to_pesewas

    payload = {
        "email": email,
        "amount": to_pesewas(amount_ghs),
        "currency": config.CURRENCY,
        "reference": reference,
        "callback_url": config.PAYSTACK_CALLBACK_URL,
        "metadata": metadata,
    }
    resp = requests.post(config.PAYSTACK_INIT_URL, json=payload, headers=_headers(), timeout=30)
    data = resp.json()
    if not data.get("status"):
        raise PaystackError(data.get("message", "Failed to initialize payment"))
    return data


def verify_transaction(reference: str):
    resp = requests.get(
        config.PAYSTACK_VERIFY_URL.format(reference=reference),
        headers=_headers(),
        timeout=30,
    )
    data = resp.json()
    if not data.get("status"):
        raise PaystackError(data.get("message", "Failed to verify payment"))
    return data


def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """Paystack signs webhook payloads with HMAC-SHA512 of your secret key.
    Always verify this before trusting a webhook call."""
    if not signature_header:
        return False
    computed = hmac.new(
        config.PAYSTACK_SECRET_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(computed, signature_header)
"""
Thin client around the Hubnet Mobile Data API.

Docs summary (see uploaded hubnet_api_doc.pdf for full reference):
  - Auth: header  token: Bearer <HUBNET_API_KEY>
  - GET  .../check_balance
  - POST .../{network}-new-transaction        body: phone, volume, reference, referrer?, webhook?
  - POST .../check-transaction-status         body: reference
  - GET  .../check-transaction-status?reference=...
  - Rate limit: 5 requests/minute per endpoint -> we enforce a 13s minimum
    gap between calls (self.enforced in RateLimiter below) so a busy
    reseller bot never trips Hubnet's 1007 error.
"""

import secrets
import string
import threading
import time
from collections import deque

import requests

import config


class RateLimiter:
    """Blocks calling threads so no more than N calls happen per window,
    with an additional minimum gap between any two calls. Shared across the
    whole process since Hubnet's limit is per API key, not per thread."""

    def __init__(self, max_calls, window_seconds, min_interval_seconds):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.min_interval_seconds = min_interval_seconds
        self._calls = deque()
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()

            # Enforce minimum spacing between consecutive calls
            since_last = now - self._last_call
            if since_last < self.min_interval_seconds:
                time.sleep(self.min_interval_seconds - since_last)
                now = time.monotonic()

            # Enforce max calls per rolling window
            while self._calls and now - self._calls[0] > self.window_seconds:
                self._calls.popleft()
            if len(self._calls) >= self.max_calls:
                sleep_for = self.window_seconds - (now - self._calls[0])
                if sleep_for > 0:
                    time.sleep(sleep_for)
                now = time.monotonic()
                while self._calls and now - self._calls[0] > self.window_seconds:
                    self._calls.popleft()

            self._calls.append(now)
            self._last_call = now


_rate_limiter = RateLimiter(
    max_calls=config.HUBNET_MAX_CALLS_PER_WINDOW,
    window_seconds=config.HUBNET_WINDOW_SECONDS,
    min_interval_seconds=config.HUBNET_MIN_INTERVAL_SECONDS,
)


class HubnetError(Exception):
    def __init__(self, message, code=None, raw=None):
        super().__init__(message)
        self.code = code
        self.raw = raw


def _headers():
    return {
        "token": f"Bearer {config.HUBNET_API_KEY}",
        "Content-Type": "application/json",
    }


def generate_reference(prefix="TCX"):
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{prefix}-{suffix}"


def check_balance():
    _rate_limiter.wait()
    resp = requests.get(
        f"{config.HUBNET_BASE_URL}/check_balance",
        headers=_headers(),
        timeout=30,
    )
    data = _safe_json(resp)
    if not data.get("status"):
        raise HubnetError("Failed to fetch balance", raw=data)
    return data


def make_transaction(network_token, phone, volume_mb, reference,
                      referrer=None, webhook_url=None):
    """Submit a single data bundle transaction. Raises HubnetError on
    rejection (bad response, non-2xx, or status=false)."""
    if network_token not in {n["token"] for n in config.NETWORKS.values()}:
        raise ValueError(f"Unknown network token: {network_token}")

    payload = {
        "phone": phone,
        "volume": str(int(volume_mb)),
        "reference": reference,
    }
    if referrer:
        payload["referrer"] = referrer
    if webhook_url:
        payload["webhook"] = webhook_url

    _rate_limiter.wait()
    resp = requests.post(
        f"{config.HUBNET_BASE_URL}/{network_token}-new-transaction",
        json=payload,
        headers=_headers(),
        timeout=30,
    )
    data = _safe_json(resp)

    # Hubnet's own docs say the `status` boolean is the authoritative
    # accept/reject signal ("true = transaction accepted; false =
    # rejected"). We used to also require message == "0000", but Hubnet
    # doesn't always send that - sometimes `message` is a human-readable
    # success sentence instead (e.g. "Your transaction has been processed
    # and is now complete."), which made a real success look like a
    # rejection and reported a delivered bundle as failed. Trust `status`.
    accepted = bool(data.get("status"))
    if not accepted:
        raise HubnetError(
            data.get("data", {}).get("message") or data.get("message") or "Transaction rejected",
            code=data.get("message"),
            raw=data,
        )
    return data


def check_transaction_status(reference):
    """Universal GET status lookup (network-agnostic, no request body)."""
    _rate_limiter.wait()
    resp = requests.get(
        f"{config.HUBNET_BASE_URL}/check-transaction-status",
        params={"reference": reference},
        headers=_headers(),
        timeout=30,
    )
    data = _safe_json(resp)
    if not data.get("status"):
        raise HubnetError("Status check failed", raw=data)
    return data


def _safe_json(resp):
    try:
        return resp.json()
    except ValueError as exc:
        raise HubnetError(
            f"Non-JSON response from Hubnet (HTTP {resp.status_code})",
            raw=resp.text,
        ) from exc

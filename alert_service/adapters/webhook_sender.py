import asyncio
import hashlib
import hmac
import json
from typing import Any

import httpx

from alert_service.domain.models import Delivered, Permanent, Retryable, SendOutcome
from alert_service.domain.ports import Clock

CONNECT_TIMEOUT_S = 3
TOTAL_TIMEOUT_S = 10
_RETRYABLE_4XX = {408, 429}


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """X-Webhook-Signature: HMAC-SHA256 over "<timestamp>.<raw body>" (GitHub/Stripe style)."""
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def make_client() -> httpx.AsyncClient:
    # httpx timeouts are per operation; the total deadline is enforced in send().
    return httpx.AsyncClient(
        timeout=httpx.Timeout(TOTAL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S), follow_redirects=False
    )


def _classify(status: int) -> SendOutcome:
    if 200 <= status < 300:
        return Delivered(status)
    if status >= 500 or status in _RETRYABLE_4XX:
        return Retryable(f"HTTP {status}", status)
    return Permanent(f"HTTP {status}", status)  # other 4xx, and 3xx (redirects not followed)


class HttpWebhookSender:
    def __init__(self, client: httpx.AsyncClient, url: str, secret: str, clock: Clock) -> None:
        self._client = client
        self._url = url
        self._secret = secret
        self._clock = clock

    async def send(self, notification: dict[str, Any]) -> SendOutcome:
        # Serialised once: exactly these bytes are signed and sent.
        body = json.dumps(notification, separators=(",", ":"), sort_keys=True).encode()
        timestamp = str(int(self._clock.now().timestamp()))
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Id": str(notification["id"]),
            "X-Webhook-Timestamp": timestamp,
            "X-Webhook-Signature": sign(self._secret, timestamp, body),
        }
        try:
            async with asyncio.timeout(TOTAL_TIMEOUT_S):
                response = await self._client.post(self._url, content=body, headers=headers)
        except (httpx.HTTPError, TimeoutError) as exc:
            return Retryable(f"{type(exc).__name__}: {exc}")
        return _classify(response.status_code)

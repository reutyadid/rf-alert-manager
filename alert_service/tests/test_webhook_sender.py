import hashlib
import hmac
import json

import httpx
import pytest

from alert_service.adapters.webhook_sender import HttpWebhookSender, sign
from alert_service.domain.models import Delivered, Permanent, Retryable
from alert_service.tests.fakes import T0, FakeClock

pytestmark = pytest.mark.anyio

SECRET = "test-secret"  # test value, not a real secret
URL = "http://receiver.test/hook"
NOTIFICATION = {"id": "ntf-1", "type": "alert.opened", "alert": {"alert_id": "alrt-1"}}


def verify(secret: str, request: httpx.Request) -> bool:
    """What a receiver does: recompute the signature over timestamp + raw body."""
    ts = request.headers["X-Webhook-Timestamp"]
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + request.content, hashlib.sha256)
    return hmac.compare_digest(
        request.headers["X-Webhook-Signature"], f"sha256={expected.hexdigest()}"
    )


def sender_with(handler) -> HttpWebhookSender:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return HttpWebhookSender(client, URL, SECRET, FakeClock(T0))


def test_signature_matches_a_known_vector():
    # HMAC-SHA256("test-secret", "1791280800." + body), computed independently.
    assert sign(SECRET, "1791280800", b'{"id":"ntf-1"}') == (
        "sha256=6db3d150c2c66dd2f26694d556e9b5291e40457004d8c5b18ddf252a8a9f7806"
    )


async def test_sends_signed_json_that_a_receiver_accepts():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    assert await sender_with(handler).send(NOTIFICATION) == Delivered(204)
    [request] = seen
    assert request.method == "POST"
    assert str(request.url) == URL
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["X-Webhook-Id"] == "ntf-1"
    assert request.headers["X-Webhook-Timestamp"] == "1791280800"
    assert json.loads(request.content) == NOTIFICATION
    assert verify(SECRET, request)
    assert not verify("wrong-secret", request)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, Delivered),
        (500, Retryable),
        (503, Retryable),
        (408, Retryable),
        (429, Retryable),
        (404, Permanent),
        (400, Permanent),
        (302, Permanent),  # redirects are not followed
    ],
)
async def test_status_mapping(status, expected):
    outcome = await sender_with(lambda _: httpx.Response(status)).send(NOTIFICATION)
    assert isinstance(outcome, expected)
    assert outcome.status == status


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.ConnectTimeout("x")]
)
async def test_network_errors_are_retryable(error):
    def handler(request):
        raise error

    outcome = await sender_with(handler).send(NOTIFICATION)
    assert isinstance(outcome, Retryable)
    assert outcome.status is None
    assert type(error).__name__ in outcome.reason

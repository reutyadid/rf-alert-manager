"""Demo webhook receiver (compose profile "demo"). Standard library only.

POST /hook      verify X-Webhook-Signature and the timestamp window; 401 if invalid,
                503 for the first FAIL_FIRST_N requests (to show retries), else 204.
GET  /received  the notifications accepted so far, in arrival order.
GET  /health    200.
"""

import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.environ.get("WEBHOOK_SECRET", "").encode()
FAIL_FIRST_N = int(os.environ.get("FAIL_FIRST_N", "0"))
MAX_AGE_S = 300
received: list[dict] = []
requests_seen = 0


def signature_valid(timestamp: str, body: bytes, signature: str) -> bool:
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > MAX_AGE_S:
        return False
    expected = hmac.new(SECRET, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, f"sha256={expected}")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        global requests_seen
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        valid = signature_valid(
            self.headers.get("X-Webhook-Timestamp", ""),
            body,
            self.headers.get("X-Webhook-Signature", ""),
        )
        requests_seen += 1
        failing = valid and requests_seen <= FAIL_FIRST_N
        note = json.loads(body) if valid else {}
        log = {
            "id": note.get("id"),
            "type": note.get("type"),
            "alert_id": note.get("alert", {}).get("alert_id"),
            "sequence": note.get("sequence"),
            "signature_valid": valid,
            "status": 401 if not valid else 503 if failing else 204,
        }
        print(json.dumps(log), flush=True)
        if valid and not failing:
            received.append(note)
        self._reply(log["status"])

    def do_GET(self) -> None:
        if self.path == "/received":
            self._reply(200, json.dumps(received).encode())
        elif self.path == "/health":
            self._reply(200)
        else:
            self._reply(404)

    def _reply(self, status: int, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        if body:
            self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # one JSON line per notification instead
        pass


if __name__ == "__main__":
    if not SECRET:
        raise SystemExit("WEBHOOK_SECRET is not set")
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()

#!/usr/bin/env bash
# Webhook demo on the local stack (run from the repo root). Starts the "demo" profile with a
# receiver that fails the first 2 requests, opens an alert, resolves it through the API, and
# checks that the receiver got alert.opened (after retries) and then alert.resolved/manual.
# Everything stays on the compose network: no public webhook services.
# Usage: scripts/demo-webhooks.sh [base url, default http://localhost]
set -euo pipefail

BASE="${1:-http://localhost}"
FREQ=2471.7  # inside rule-ism-high-power's band; unlikely to collide with the simulator

export FAIL_FIRST_N=2
export WEBHOOK_URL=http://webhook-receiver:8080/hook
secret="$(grep -E '^WEBHOOK_SECRET=' .env 2>/dev/null | cut -d= -f2- || true)"
if [ -z "$secret" ] || [[ "$secret" == change-me* ]]; then
    secret="demo-$(date +%s)-$RANDOM$RANDOM"  # throwaway, only for this demo run
fi
export WEBHOOK_SECRET="$secret"

received() {
    docker compose exec -T webhook-receiver python -c \
        "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/received').read().decode())"
}

echo "== starting the stack with the demo receiver (FAIL_FIRST_N=$FAIL_FIRST_N)"
docker compose --profile demo up -d --build --wait

echo "== sending 15 s of strong observations at $FREQ MHz (opens an alert)"
now=$(date -u +%s)
items=""
for i in $(seq 14 -1 0); do
    ts=$(date -u -d "@$((now - i))" +%Y-%m-%dT%H:%M:%SZ)
    items+="${items:+,}{\"sensor_id\":\"sensor-demo\",\"timestamp\":\"$ts\",\"frequency_mhz\":$FREQ,"
    items+="\"bandwidth_khz\":200,\"power_dbm\":-30.0,\"location\":{\"lat\":32.08,\"lon\":34.78}}"
done
curl -fsS -X POST "$BASE/api/v1/observations" -H "Content-Type: application/json" -d "[$items]"
echo

alert_id=""
for _ in $(seq 1 20); do
    alert_id=$(curl -fsS "$BASE/api/v1/alerts?state=OPEN&min_frequency_mhz=2471.6&max_frequency_mhz=2471.8" |
        grep -o '"alert_id":"[^"]*"' | head -1 | cut -d'"' -f4 || true)
    [ -n "$alert_id" ] && break
    sleep 1
done
[ -n "$alert_id" ] || { echo "DEMO FAILED: no alert opened" >&2; exit 1; }
echo "== alert $alert_id opened; resolving it through the API"
curl -fsS -X POST "$BASE/api/v1/alerts/$alert_id/resolve" >/dev/null

echo "== waiting for the receiver (opened is retried: 503, 503, then 204 after 2 s + 4 s)"
for _ in $(seq 1 60); do
    if received | docker compose exec -T webhook-receiver python -c "
import json, sys
notes = [n for n in json.load(sys.stdin) if n['alert']['alert_id'] == '$alert_id']
got = [(n['type'], n['resolution']) for n in notes]
sys.exit(0 if got == [('alert.opened', None), ('alert.resolved', 'manual')] else 1)
"; then
        echo "== OK: receiver got alert.opened then alert.resolved (manual), signatures valid"
        docker compose logs --no-log-prefix webhook-receiver | grep "$alert_id" || true
        echo
        echo "Webhooks stay on until you recreate alert-service with your .env values:"
        echo "  docker compose up -d alert-service && docker compose --profile demo stop webhook-receiver"
        exit 0
    fi
    sleep 1
done
echo "DEMO FAILED: receiver did not get opened + resolved for $alert_id" >&2
docker compose logs --tail 20 webhook-receiver alert-service >&2
exit 1

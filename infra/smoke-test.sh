#!/usr/bin/env bash
# Runs ON the VM after a deploy. Fails (exit 1) unless the stack is healthy AND runs the
# expected image tag.
# Usage: smoke-test.sh <commit sha> [base url, default http://127.0.0.1]
set -euo pipefail

TAG="${1:?usage: smoke-test.sh <commit sha> [base url]}"
BASE="${2:-http://127.0.0.1}"
cd /opt/rf-alert-manager

fail() {
    echo "SMOKE TEST FAILED: $*" >&2
    docker compose ps >&2 || true
    docker compose logs --tail 30 ingest-service alert-service >&2 || true
    exit 1
}

# 1. The containers run exactly the images we deployed.
for svc in ingest-service alert-service; do
    image="$(docker inspect --format '{{.Config.Image}}' "$(docker compose ps -q "$svc")")"
    [[ "$image" == *":$TAG" ]] || fail "$svc runs $image, expected tag $TAG"
done
echo "ok: both services run :$TAG"

# 2. Combined health (DB, Redis, alert-service background tasks), up to 2 minutes.
for _ in $(seq 1 40); do
    if curl -fsS -o /dev/null "$BASE/health"; then
        healthy=1
        break
    fi
    sleep 3
done
[ "${healthy:-0}" = 1 ] || fail "$BASE/health not 200 after 120 s"
echo "ok: /health"

# 3. One real read through nginx on each service.
rules="$(curl -fsS "$BASE/api/v1/rules")" || fail "GET /api/v1/rules"
[[ "$rules" == \[\{* ]] || fail "GET /api/v1/rules returned no rules: $rules"
curl -fsS -o /dev/null "$BASE/api/v1/alerts" || fail "GET /api/v1/alerts"
echo "ok: /api/v1/rules and /api/v1/alerts"

# 4. Observability: Grafana is up behind nginx, and Prometheus scrapes both services.
# Grafana takes 20-30 s to start after a (re)create.
for _ in $(seq 1 30); do
    curl -fsS -o /dev/null "$BASE/grafana/api/health" && grafana=1 && break
    sleep 3
done
[ "${grafana:-0}" = 1 ] || fail "GET /grafana/api/health not 200 after 90 s"
for _ in $(seq 1 20); do
    targets="$(docker compose exec -T prometheus \
        wget -qO- 'http://127.0.0.1:9090/api/v1/targets?state=active' || true)"
    up="$(grep -o '"health":"up"' <<<"$targets" | wc -l)"
    [ "$up" -ge 2 ] && break
    sleep 3
done
[ "$up" -ge 2 ] || fail "Prometheus: $up of 2 scrape targets up"
echo "ok: grafana, prometheus scraping 2/2 targets"

echo "SMOKE TEST PASSED for $TAG"

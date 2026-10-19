#!/usr/bin/env bash
# Runs ON the VM. Unpacks a release archive into /opt/rf-alert-manager and (re)starts the stack.
# Usage: vm-deploy.sh <release.tar>
# .env is kept across deploys; on the first deploy it is created from .env.example with a
# random Postgres password generated here, so the secret never leaves the VM.
set -euo pipefail

ARCHIVE="${1:?usage: vm-deploy.sh <release.tar>}"
APP_DIR=/opt/rf-alert-manager

# Replace the code, keep .env and the rollback marker.
find "$APP_DIR" -mindepth 1 -maxdepth 1 ! -name .env ! -name .previous-tag -exec rm -rf {} +
tar -xf "$ARCHIVE" -C "$APP_DIR"
cd "$APP_DIR"

if [ ! -f .env ]; then
    password="$(openssl rand -hex 24)"
    sed "s/change-me/${password}/g" .env.example > .env
    chmod 600 .env
    echo "created $APP_DIR/.env with a generated POSTGRES_PASSWORD"
fi

docker compose up -d --build --remove-orphans

# Wait until nginx reports both services healthy.
for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null http://127.0.0.1/health; then
        echo "deploy OK: http://$(hostname -I | awk '{print $1}')/health"
        docker compose ps
        exit 0
    fi
    sleep 2
done
echo "deploy FAILED: /health not healthy after 120 s" >&2
docker compose ps >&2
docker compose logs --tail 50 >&2
exit 1

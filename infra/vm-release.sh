#!/usr/bin/env bash
# Runs ON the VM (from the self-hosted runner, or by hand for a rollback).
# Deploys the images tagged <sha> from GHCR: no build on the VM.
# Usage: vm-release.sh <40-char commit sha> [source dir with docker-compose.yml, default: APP_DIR]
#   Rollback by hand:  /opt/rf-alert-manager/infra/vm-release.sh "$(cat /opt/rf-alert-manager/.previous-tag)"
# The deployed tag is written to .env (IMAGE_TAG) so later `docker compose` commands use it;
# the tag it replaced is kept in .previous-tag.
set -euo pipefail

TAG="${1:?usage: vm-release.sh <commit sha> [source dir]}"
APP_DIR=/opt/rf-alert-manager
SRC="${2:-$APP_DIR}"

if ! [[ "$TAG" =~ ^[0-9a-f]{40}$ ]]; then
    echo "invalid image tag '$TAG': expected a full commit SHA" >&2
    exit 2
fi
if [ ! -f "$APP_DIR/.env" ]; then
    echo "$APP_DIR/.env is missing: run infra/deploy-vm.ps1 once to create it" >&2
    exit 1
fi

# Deployment files for this version (compose, nginx config, scripts). .env is never copied.
if [ "$SRC" != "$APP_DIR" ]; then
    cp "$SRC/docker-compose.yml" "$SRC/.env.example" "$APP_DIR/"
    rm -rf "$APP_DIR/nginx" "$APP_DIR/infra"
    cp -r "$SRC/nginx" "$SRC/infra" "$APP_DIR/"
fi
cd "$APP_DIR"

current="$(sed -n 's/^IMAGE_TAG=//p' .env)"
export IMAGE_TAG="$TAG"

# Pull first: if the image does not exist, the running stack is left untouched.
docker compose pull ingest-service alert-service
docker compose up -d --no-build --remove-orphans

if [ "$current" != "$TAG" ]; then
    echo "$current" > .previous-tag
    sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=${TAG}/" .env
fi
# Bound disk use: drop unused images older than 3 days (a rollback re-pulls from GHCR).
docker image prune -af --filter "until=72h" >/dev/null
echo "deployed $TAG (previous: $(cat .previous-tag 2>/dev/null || echo none))"
docker compose ps

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

# Config folder -> the container that bind-mounts it.
declare -A CONFIG_OWNER=([nginx]=nginx [observability/prometheus]=prometheus [observability/grafana]=grafana)

config_hash() {
    if [ -d "$APP_DIR/$1" ]; then
        (cd "$APP_DIR" && find "$1" -type f -print0 | sort -z | xargs -0 -r sha256sum | sha256sum)
    else
        echo none
    fi
}

declare -A before=()
for dir in "${!CONFIG_OWNER[@]}"; do
    before[$dir]="$(config_hash "$dir")"
done

# Deployment files for this version: everything docker-compose.yml mounts, plus the scripts.
# .env is never copied. A bind-mount source missing here would be created by Docker as an
# empty root-owned directory and the container would fail to start.
if [ "$SRC" != "$APP_DIR" ]; then
    cp "$SRC/docker-compose.yml" "$SRC/.env.example" "$APP_DIR/"
    for dir in nginx infra observability; do
        rm -rf "${APP_DIR:?}/$dir"
        cp -r "$SRC/$dir" "$APP_DIR/"
    done
fi
cd "$APP_DIR"
bash infra/env-sync.sh "$APP_DIR"

current="$(sed -n 's/^IMAGE_TAG=//p' .env)"
export IMAGE_TAG="$TAG"

# Pull first: if the image does not exist, the running stack is left untouched.
docker compose pull ingest-service alert-service
docker compose up -d --no-build --remove-orphans

# Compose does not recreate a container when only a mounted file changed, and the copy above
# gives every file a new inode, so a running container would keep reading the old file.
# Recreate the owner of each changed config folder (nginx: about 1 s of 502s, only then).
for dir in "${!CONFIG_OWNER[@]}"; do
    if [ "${before[$dir]}" != none ] && [ "${before[$dir]}" != "$(config_hash "$dir")" ]; then
        echo "config changed in $dir: recreating ${CONFIG_OWNER[$dir]}"
        docker compose up -d --no-build --no-deps --force-recreate "${CONFIG_OWNER[$dir]}"
    fi
done

if [ "$current" != "$TAG" ]; then
    echo "$current" > .previous-tag
    sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=${TAG}/" .env
fi
# Bound disk use: drop unused images older than 3 days (a rollback re-pulls from GHCR).
docker image prune -af --filter "until=72h" >/dev/null
echo "deployed $TAG (previous: $(cat .previous-tag 2>/dev/null || echo none))"
docker compose ps

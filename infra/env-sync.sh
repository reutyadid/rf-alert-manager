#!/usr/bin/env bash
# Runs ON the VM. Creates <dir>/.env from <dir>/.env.example, or adds keys that are missing
# from an existing .env (a new setting in a later version). Existing values are never changed.
# A placeholder `change-me[-name]` becomes a random secret generated here; the same placeholder
# gets the same secret within one run (POSTGRES_PASSWORD and DATABASE_URL must match).
# Usage: env-sync.sh <dir>
set -euo pipefail

cd "${1:?usage: env-sync.sh <dir>}"
touch .env
chmod 600 .env

declare -A secrets=()
added=()
while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)= ]] || continue
    key="${BASH_REMATCH[1]}"
    grep -q "^${key}=" .env && continue
    value="${line#*=}"
    while [[ "$value" =~ (change-me(-[a-z0-9]+)?) ]]; do
        token="${BASH_REMATCH[1]}"
        [ -n "${secrets[$token]:-}" ] || secrets[$token]="$(openssl rand -hex 24)"
        value="${value//"$token"/${secrets[$token]}}"
    done
    echo "${key}=${value}" >> .env
    added+=("$key")
done < .env.example

if [ "${#added[@]}" -gt 0 ]; then
    echo "env-sync: added ${added[*]} to $(pwd)/.env"
fi

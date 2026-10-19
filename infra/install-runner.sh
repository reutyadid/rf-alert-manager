#!/usr/bin/env bash
# Runs ON the VM as user ubuntu. Installs the GitHub Actions self-hosted runner as a systemd
# service (starts on boot). The runner connects out to GitHub, so no inbound port is needed.
# Usage: RUNNER_TOKEN=<one-time token> install-runner.sh <owner/repo>
# The registration token comes from GitHub (Settings -> Actions -> Runners) and expires in 1 h;
# it is only used here and never stored in the repository.
set -euo pipefail

REPO="${1:?usage: RUNNER_TOKEN=... install-runner.sh <owner/repo>}"
: "${RUNNER_TOKEN:?RUNNER_TOKEN is not set}"
RUNNER_DIR=/opt/actions-runner

if [ -f "$RUNNER_DIR/.runner" ]; then
    echo "runner already configured in $RUNNER_DIR"
    exit 0
fi

sudo install -d -o "$USER" -g "$USER" "$RUNNER_DIR"
cd "$RUNNER_DIR"

release="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest)"
version="$(grep -m1 '"tag_name"' <<<"$release" | sed -E 's/.*"v([^"]+)".*/\1/')"
echo "installing actions/runner $version"
curl -fsSL -o runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${version}/actions-runner-linux-x64-${version}.tar.gz"
tar -xzf runner.tar.gz
rm runner.tar.gz

./config.sh --unattended --replace \
    --url "https://github.com/${REPO}" \
    --token "$RUNNER_TOKEN" \
    --name "$(hostname)" \
    --labels rf-vm \
    --work _work

sudo ./svc.sh install "$USER"
sudo ./svc.sh start
sudo ./svc.sh status --no-pager | head -5

# RF Alert Manager

Backend for an RF spectrum monitoring network: sensors post observations, rules turn
persistent strong emitters into alerts. See `ARCHITECTURE.md` and `DECISIONS.md`.

## Run locally

Requires Docker with compose.

```bash
bash infra/env-sync.sh .      # creates .env from .env.example with generated passwords
docker compose up -d --build
curl http://localhost/health   # 200 only if both services are healthy
```

Endpoints (through nginx on port 80):

| Path | Service |
|---|---|
| `/api/v1/observations`, `/api/v1/sensors`, `/api/v1/rules` | ingest-service |
| `/api/v1/alerts` | alert-service |
| `/health` | combined health of both services |
| `/ingest/health`, `/ingest/metrics`, `/alert/health`, `/alert/metrics` | per service |
| `/grafana/` | Grafana dashboard (login `admin` / `GRAFANA_ADMIN_PASSWORD` from `.env`) |

### Observability (Grafana + Prometheus)

Open `http://localhost/grafana/` (or `http://<vm-ip>/grafana/`) and log in as `admin`. The
**RF Alert Manager** dashboard is the home page: services, ingest rates, open alerts by
severity, alerts opened/resolved and dropped events. It is provisioned from
`observability/`; edit the JSON in git, not in the UI. Prometheus is internal only.

Grafana password on the VM (generated at deploy):

```bash
multipass exec rf-vm -- grep GRAFANA_ADMIN_PASSWORD /opt/rf-alert-manager/.env
```

## Simulator

```bash
python simulator/simulate.py --url http://localhost --rate 5 --watch
```

Sends background traffic, a persistent emitter (opens an alert), a short burst (does not),
an emitter that disappears (its alert auto-resolves) and late events.

## Tests

```bash
pip install -r ingest_service/requirements-dev.txt -r alert_service/requirements-dev.txt
ruff check . && ruff format --check .
pytest -q
```

Integration tests are skipped unless `INGEST_IT_DATABASE_URL` / `INGEST_IT_REDIS_URL` point to
a throwaway Postgres and Redis.

## VM (Ubuntu 24.04, Multipass)

Requires [Multipass](https://multipass.run) (Hyper-V on Windows Pro).

```powershell
powershell -ExecutionPolicy Bypass -File infra\launch-vm.ps1    # create + provision, prints IP
powershell -ExecutionPolicy Bypass -File infra\deploy-vm.ps1    # deploy committed HEAD
powershell -ExecutionPolicy Bypass -File infra\destroy-vm.ps1   # delete the VM
```

- Provisioning is `infra/cloud-init.yaml`: Docker, start on boot, ufw (22 and 80 only),
  bounded Docker and journal logs.
- The first deploy creates `/opt/rf-alert-manager/.env` on the VM with a random Postgres
  password; later deploys keep it.
- The VM's IP can change after a host reboot; `multipass list` or `launch-vm.ps1` shows it.

## CI/CD (GitHub Actions)

| Workflow | Trigger | What it does |
|---|---|---|
| `CI` (`.github/workflows/ci.yml`) | every push and PR | lint, unit tests and integration tests (Postgres + Redis), per service |
| `Deploy` (`.github/workflows/deploy.yml`) | CI green on `main` | build images tagged with the commit SHA → GHCR → deploy on the VM → smoke test (auto-rollback on failure) |
| `Deploy` (manual) | Actions → Deploy → Run workflow | **rollback / redeploy**: deploys `image_tag` (an earlier commit SHA) from GHCR |

One-time setup:

1. Create and deploy the VM once (see above): `launch-vm.ps1`, then `deploy-vm.ps1` (creates `.env`).
2. Register the VM as a runner: GitHub → Settings → Actions → Runners → New self-hosted runner
   (Linux), copy the token (valid 1 h), then
   `powershell -ExecutionPolicy Bypass -File infra\register-runner.ps1 -Token <token>`.
3. Optional (needs a paid GitHub plan for a private repo): branch protection on `main`
   requiring the `lint`, `unit-tests` and `integration-tests` checks. Without it a red CI still
   blocks the deploy, but not the merge (DECISIONS §10).

Rollback without GitHub (on the VM):

```bash
cd /opt/rf-alert-manager && infra/vm-release.sh "$(cat .previous-tag)"
```

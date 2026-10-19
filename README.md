# RF Alert Manager

Backend for an RF spectrum monitoring network: sensors post observations, rules turn
persistent strong emitters into alerts. See `ARCHITECTURE.md` and `DECISIONS.md`.

## Run locally

Requires Docker with compose.

```bash
cp .env.example .env          # then set POSTGRES_PASSWORD (and the same value in DATABASE_URL)
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

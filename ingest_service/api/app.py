"""FastAPI application factory. Services are injected; main.py builds the real ones."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ingest_service.api import metrics
from ingest_service.api.errors import install_error_handlers
from ingest_service.api.observations import DEFAULT_MAX_BATCH_SIZE
from ingest_service.api.observations import router as observations_router
from ingest_service.api.rules import router as rules_router
from ingest_service.api.schemas import SensorOut
from ingest_service.domain.ingest import IngestService
from ingest_service.domain.ports import ObservationReader, SensorReader
from ingest_service.domain.rules import RuleService

HealthCheck = Callable[[], Awaitable[None]]
HEALTH_TIMEOUT_S = 2.0


@dataclass
class Services:
    ingest: IngestService
    rules: RuleService
    observations: ObservationReader
    sensors: SensorReader
    health_checks: dict[str, HealthCheck]


def create_app(
    services: Services | None = None,
    lifespan=None,
    max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
) -> FastAPI:
    app = FastAPI(title="RF ingest-service", version="1.0.0", lifespan=lifespan)
    app.state.max_batch_size = max_batch_size
    if services is not None:
        app.state.services = services

    install_error_handlers(app)
    app.middleware("http")(metrics.track_requests)
    app.include_router(observations_router)
    app.include_router(rules_router)

    @app.get("/api/v1/sensors", response_model=list[SensorOut], tags=["sensors"])
    async def list_sensors(request: Request) -> list[SensorOut]:
        return [SensorOut.from_domain(s) for s in await request.app.state.services.sensors.list()]

    @app.get("/health", tags=["ops"])
    async def health(request: Request) -> JSONResponse:
        checks = {}
        for name, check in request.app.state.services.health_checks.items():
            try:
                await asyncio.wait_for(check(), HEALTH_TIMEOUT_S)
                checks[name] = "ok"
            except Exception:
                checks[name] = "unavailable"
        healthy = all(v == "ok" for v in checks.values())
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ok" if healthy else "unavailable", "checks": checks},
        )

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    async def get_metrics():
        return metrics.metrics_response()

    return app

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.exceptions import HTTPException

from alert_service.adapters import metrics
from alert_service.api.schemas import AlertDetailOut, AlertOut, ErrorOut, ObservationOut
from alert_service.domain.errors import AlertNotFound, InvalidTransition
from alert_service.domain.models import AlertFilter, AlertState
from alert_service.domain.service import AlertService

log = logging.getLogger(__name__)

HealthCheck = Callable[[], Awaitable[object]]

router = APIRouter()


def _service(request: Request) -> AlertService:
    return request.app.state.service


def _error(status: int, error: str, message: str, details: list | None = None) -> JSONResponse:
    body = ErrorOut(error=error, message=message, details=details or [])
    return JSONResponse(status_code=status, content=body.model_dump())


@router.get("/api/v1/alerts", response_model=list[AlertOut])
async def list_alerts(
    request: Request,
    state: AlertState | None = None,
    severity: str | None = None,
    sensor_id: str | None = None,
    min_frequency_mhz: float | None = Query(default=None, ge=0),
    max_frequency_mhz: float | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
):
    if (
        min_frequency_mhz is not None
        and max_frequency_mhz is not None
        and min_frequency_mhz > max_frequency_mhz
    ):
        return _error(
            422,
            "validation_error",
            "invalid request",
            [{"loc": ["query", "min_frequency_mhz"], "reason": "must be <= max_frequency_mhz"}],
        )
    flt = AlertFilter(state, severity, sensor_id, min_frequency_mhz, max_frequency_mhz, limit)
    return [AlertOut.from_domain(a) for a in await _service(request).list_alerts(flt)]


@router.get("/api/v1/alerts/{alert_id}", response_model=AlertDetailOut)
async def get_alert(request: Request, alert_id: str):
    alert, contributions = await _service(request).get_alert(alert_id)
    return AlertDetailOut(
        **AlertOut.from_domain(alert).model_dump(),
        observations=[ObservationOut.from_domain(c) for c in contributions],
    )


@router.post("/api/v1/alerts/{alert_id}/ack", response_model=AlertOut)
async def acknowledge(request: Request, alert_id: str):
    alert = await _service(request).acknowledge(alert_id)
    log.info("alert acknowledged", extra={"alert_id": alert_id})
    return AlertOut.from_domain(alert)


@router.post("/api/v1/alerts/{alert_id}/resolve", response_model=AlertOut)
async def resolve(request: Request, alert_id: str):
    alert = await _service(request).resolve(alert_id)
    metrics.ALERTS_RESOLVED.labels(reason="manual").inc()
    log.info("alert resolved", extra={"alert_id": alert_id, "reason": "manual"})
    return AlertOut.from_domain(alert)


@router.get("/health")
async def health(request: Request):
    checks: dict[str, HealthCheck] = request.app.state.health_checks
    results = {}
    for name, check in checks.items():
        try:
            await asyncio.wait_for(check(), timeout=2)
            results[name] = "ok"
        except Exception as exc:
            results[name] = f"error: {type(exc).__name__}"
    healthy = all(v == "ok" for v in results.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "unhealthy", "checks": results},
    )


@router.get("/metrics")
async def prometheus_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AlertNotFound)
    async def not_found(_: Request, exc: AlertNotFound):
        return _error(404, "not_found", str(exc))

    @app.exception_handler(InvalidTransition)
    async def invalid_transition(_: Request, exc: InvalidTransition):
        return _error(409, "invalid_transition", str(exc), [{"state": str(exc.state)}])

    @app.exception_handler(RequestValidationError)
    async def validation(_: Request, exc: RequestValidationError):
        details = [
            {"loc": list(e["loc"]), "reason": e["msg"]}
            for e in exc.errors()  # input values are not echoed back
        ]
        return _error(422, "validation_error", "invalid request", details)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return _error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception):
        log.exception("unhandled error")
        return _error(500, "internal_error", "internal server error")

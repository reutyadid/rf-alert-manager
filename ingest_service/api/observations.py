"""Observation endpoints. A batch is all or nothing (DECISIONS §6)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, Request
from pydantic import ValidationError

from ingest_service.api import metrics
from ingest_service.api.errors import ApiError, field_path
from ingest_service.api.schemas import IngestResponse, IsoTimestamp, ObservationIn, ObservationOut
from ingest_service.domain.models import Observation, ObservationQuery

router = APIRouter(prefix="/api/v1/observations", tags=["observations"])


def parse_observations(payload: Any) -> list[Observation]:
    """Validate a single observation or a batch. Raises ApiError listing every invalid item."""
    if isinstance(payload, dict):
        items = [payload]
    elif isinstance(payload, list):
        items = payload
    else:
        raise ApiError(422, "validation_error", "body must be an observation or an array of them")

    observations: list[Observation] = []
    details: list[dict] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            details.append({"index": index, "field": "", "reason": "must be an object"})
            continue
        try:
            observations.append(ObservationIn.model_validate(item).to_domain())
        except ValidationError as exc:
            details.extend(
                {"index": index, "field": field_path(e["loc"]), "reason": e["msg"]}
                for e in exc.errors()
            )

    if details:
        bad = len({d["index"] for d in details})
        raise ApiError(
            422,
            "validation_error",
            f"{bad} of {len(items)} observations are invalid; nothing was stored",
            details,
        )
    return observations


@router.post("", response_model=IngestResponse)
async def post_observations(request: Request, payload: Annotated[Any, Body()]) -> IngestResponse:
    try:
        observations = parse_observations(payload)
    except ApiError:
        metrics.BATCHES_REJECTED.inc()
        raise
    result = await request.app.state.services.ingest.ingest(observations)
    metrics.OBSERVATIONS_RECEIVED.inc(len(observations))
    metrics.OBSERVATIONS_DUPLICATE.inc(result.duplicates)
    metrics.RULE_MATCHES_PUBLISHED.inc(result.published)
    return IngestResponse(accepted=result.accepted, duplicates=result.duplicates)


@router.get("", response_model=list[ObservationOut])
async def get_observations(
    request: Request,
    sensor_id: str | None = None,
    min_frequency_mhz: Annotated[float | None, Query(ge=0)] = None,
    max_frequency_mhz: Annotated[float | None, Query(ge=0)] = None,
    start: Annotated[IsoTimestamp | None, Query(alias="from")] = None,
    end: Annotated[IsoTimestamp | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[ObservationOut]:
    query = ObservationQuery(
        sensor_id=sensor_id,
        min_frequency_mhz=min_frequency_mhz,
        max_frequency_mhz=max_frequency_mhz,
        start=start,
        end=end,
        limit=limit,
    )
    rows = await request.app.state.services.observations.query(query)
    return [ObservationOut.from_domain(o) for o in rows]

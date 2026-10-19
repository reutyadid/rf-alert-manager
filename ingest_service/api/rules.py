"""Rule CRUD. ingest-service owns the rules table."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ingest_service.api.errors import ApiError
from ingest_service.api.schemas import RuleIn, RuleOut, RuleUpdate

router = APIRouter(prefix="/api/v1/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
async def list_rules(request: Request) -> list[RuleOut]:
    return [RuleOut.from_domain(r) for r in await request.app.state.services.rules.list()]


@router.post("", response_model=RuleOut, status_code=201)
async def create_rule(request: Request, body: RuleIn) -> RuleOut:
    rule = await request.app.state.services.rules.create(body.to_domain(body.rule_id))
    return RuleOut.from_domain(rule)


@router.put("/{rule_id}", response_model=RuleOut)
async def update_rule(request: Request, rule_id: str, body: RuleUpdate) -> RuleOut:
    if body.rule_id is not None and body.rule_id != rule_id:
        raise ApiError(
            422,
            "validation_error",
            "rule_id in the body does not match the path",
            [{"field": "rule_id"}],
        )
    rule = await request.app.state.services.rules.update(body.to_domain(rule_id))
    return RuleOut.from_domain(rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(request: Request, rule_id: str) -> Response:
    await request.app.state.services.rules.delete(rule_id)
    return Response(status_code=204)

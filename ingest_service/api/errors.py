"""Uniform error body: {"error": "<code>", "message": "...", "details": [...]}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ingest_service.domain.errors import (
    DependencyUnavailable,
    RuleAlreadyExists,
    RuleNotFound,
    UnknownRuleType,
)

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(
        self, status_code: int, error: str, message: str, details: list | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message
        self.details = details or []


def error_response(status_code: int, error: str, message: str, details: list | None = None):
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "message": message, "details": details or []},
    )


def field_path(loc: tuple) -> str:
    return ".".join(str(part) for part in loc)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.status_code, exc.error, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [
            # Drop the leading "body"/"query"/"path" segment.
            {"field": field_path(tuple(e["loc"][1:])) or e["loc"][0], "reason": e["msg"]}
            for e in exc.errors()
        ]
        return error_response(422, "validation_error", "request is invalid", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = "not_found" if exc.status_code == 404 else "http_error"
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RuleNotFound)
    async def _not_found(_: Request, exc: RuleNotFound):
        return error_response(404, "not_found", str(exc))

    @app.exception_handler(RuleAlreadyExists)
    async def _conflict(_: Request, exc: RuleAlreadyExists):
        return error_response(409, "conflict", str(exc))

    @app.exception_handler(UnknownRuleType)
    async def _unknown_type(_: Request, exc: UnknownRuleType):
        return error_response(422, "validation_error", str(exc), [{"field": "type"}])

    @app.exception_handler(DependencyUnavailable)
    async def _unavailable(_: Request, exc: DependencyUnavailable):
        log.warning("dependency unavailable", extra={"reason": str(exc)})
        return error_response(503, "unavailable", "a dependency is unavailable, retry later")

    @app.exception_handler(Exception)
    async def _internal(_: Request, exc: Exception):
        log.exception("unhandled error")
        return error_response(500, "internal_error", "internal server error")

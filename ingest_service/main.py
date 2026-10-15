"""Composition root: settings, adapters, domain services, FastAPI app.

Run: python -m ingest_service.main
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import uvicorn
from fastapi import FastAPI
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis

from ingest_service.adapters import postgres, redis_publisher
from ingest_service.adapters.clock import SystemClock
from ingest_service.adapters.log_config import configure_logging
from ingest_service.adapters.prometheus_metrics import PrometheusMetrics
from ingest_service.adapters.rules_yaml import read_rules_file
from ingest_service.api.app import Services, create_app
from ingest_service.api.observations import DEFAULT_MAX_BATCH_SIZE
from ingest_service.api.schemas import RuleIn
from ingest_service.domain.errors import DependencyUnavailable
from ingest_service.domain.ingest import IngestService
from ingest_service.domain.rules import RuleService
from ingest_service.domain.validation import DEFAULT_MAX_CLOCK_SKEW_S

log = logging.getLogger("ingest_service")

DEFAULT_RULES_FILE = Path(__file__).parent / "config" / "rules.yaml"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str
    redis_url: str
    rules_file: Path = DEFAULT_RULES_FILE
    default_max_gap_s: float = Field(default=3.0, gt=0)
    max_batch_size: int = Field(default=DEFAULT_MAX_BATCH_SIZE, ge=1)
    max_clock_skew_s: float = Field(default=DEFAULT_MAX_CLOCK_SKEW_S, ge=0)
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    startup_retries: int = Field(default=30, ge=1)
    startup_retry_delay_s: float = Field(default=2.0, gt=0)


async def _with_retries(name: str, attempt: Callable[[], Awaitable], settings: Settings):
    for n in range(1, settings.startup_retries + 1):
        try:
            return await attempt()
        except (OSError, TimeoutError, DependencyUnavailable, asyncpg.PostgresError) as exc:
            if n == settings.startup_retries:
                raise
            log.warning(
                "dependency not ready, retrying",
                extra={"dependency": name, "attempt": n, "error": str(exc)},
            )
            await asyncio.sleep(settings.startup_retry_delay_s)


async def seed_rules(rules: RuleService, path: Path) -> None:
    # Missing or invalid seed file = fail fast at startup. A wrong RULES_FILE path would
    # otherwise start the service with zero rules, and no alert would ever open.
    if not path.is_file():
        raise FileNotFoundError(f"RULES_FILE not found: {path}")
    seed = [RuleIn.model_validate(item) for item in read_rules_file(path)]
    count = await rules.seed_if_empty([r.to_domain(r.rule_id) for r in seed])
    log.info("rules seed", extra={"rules_file": str(path), "seeded": count})


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings

    async def connect_db() -> asyncpg.Pool:
        return await asyncpg.create_pool(
            settings.database_url, min_size=1, max_size=10, command_timeout=10
        )

    pool = await _with_retries("postgres", connect_db, settings)
    redis = Redis.from_url(settings.redis_url, socket_timeout=5, socket_connect_timeout=5)
    try:
        await _with_retries("redis", lambda: redis_publisher.ping(redis), settings)
        await postgres.init_schema(pool)

        rule_repo = postgres.PostgresRuleRepository(pool)
        rules = RuleService(rule_repo)
        await seed_rules(rules, settings.rules_file)

        publisher = redis_publisher.RedisStreamPublisher(redis)
        app.state.services = Services(
            ingest=IngestService(
                rule_repo,
                lambda: postgres.PostgresUnitOfWork(pool, publisher),
                default_max_gap_s=settings.default_max_gap_s,
                metrics=PrometheusMetrics(),
            ),
            rules=rules,
            observations=postgres.PostgresObservationReader(pool),
            sensors=postgres.PostgresSensorReader(pool),
            health_checks={
                "database": lambda: postgres.ping(pool),
                "redis": lambda: redis_publisher.ping(redis),
            },
        )
        log.info("ingest-service started")
        yield
    finally:
        await redis.aclose()
        await pool.close()


def build_app(settings: Settings) -> FastAPI:
    app = create_app(
        lifespan=lifespan,
        max_batch_size=settings.max_batch_size,
        clock=SystemClock(),
        max_clock_skew_s=settings.max_clock_skew_s,
    )
    app.state.settings = settings
    return app


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()

"""Composition root: settings, adapters, domain service, background tasks, HTTP app."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as redis
import uvicorn
from fastapi import FastAPI
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from alert_service.adapters import metrics
from alert_service.adapters.clock import SystemClock
from alert_service.adapters.json_logging import configure_logging
from alert_service.adapters.postgres_repo import PostgresAlertRepository, apply_schema
from alert_service.adapters.redis_consumer import RedisMatchConsumer
from alert_service.api.app import create_app
from alert_service.domain.service import AlertService
from alert_service.domain.watermark import Watermark

log = logging.getLogger("alert_service")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str
    redis_url: str
    # Validated so a bad value fails at startup instead of stalling the consumer.
    freq_tolerance_mhz: float = Field(default=0.1, gt=0)
    allowed_lateness_s: float = Field(default=30, ge=0)
    idle_timeout_s: float = Field(default=30, gt=0)
    sweep_interval_s: float = Field(default=5, gt=0)
    max_clock_skew_s: float = Field(default=60, gt=0)
    log_level: str = "INFO"
    rule_matches_stream: str = "rule_matches"
    consumer_group: str = "alert-service"
    port: int = 8000


async def with_retries[T](
    name: str, connect: Callable[[], Awaitable[T]], attempts: int = 30, delay_s: float = 2
) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return await connect()
        except Exception as exc:
            if attempt == attempts:
                raise
            log.warning(
                f"{name} not ready, retrying",
                extra={"attempt": attempt, "error": f"{type(exc).__name__}: {exc}"},
            )
            await asyncio.sleep(delay_s)
    raise AssertionError("unreachable")


def tasks_alive(tasks: list[asyncio.Task]) -> Callable[[], Awaitable[None]]:
    """Health check: 503 if the consumer or sweep task has stopped, even if DB and Redis
    are reachable (otherwise /health would stay green while no events are processed)."""

    async def check() -> None:
        stopped = [t.get_name() for t in tasks if t.done()]
        if stopped:
            raise RuntimeError(f"background task stopped: {', '.join(stopped)}")

    return check


async def sweep_loop(service: AlertService, interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            for alert in await service.sweep():
                metrics.ALERTS_RESOLVED.labels(reason="auto").inc()
                log.info(
                    "alert auto-resolved",
                    extra={"alert_id": alert.alert_id, "last_seen": alert.last_seen.isoformat()},
                )
            metrics.PENDING_RUNS.set(service.pending_run_count)
        except Exception:
            log.exception("sweep failed")


def build_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = await with_retries("postgres", lambda: asyncpg.create_pool(settings.database_url))
        await apply_schema(pool)
        # The socket timeout must exceed the consumer's XREADGROUP block time (5 s).
        client = redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=15)
        await with_retries("redis", client.ping)

        clock = SystemClock()
        watermark = Watermark(clock, settings.allowed_lateness_s, settings.idle_timeout_s)
        service = AlertService(
            PostgresAlertRepository(pool),
            clock,
            watermark,
            settings.freq_tolerance_mhz,
            settings.max_clock_skew_s,
        )
        consumer = RedisMatchConsumer(
            client,
            service,
            stream=settings.rule_matches_stream,
            group=settings.consumer_group,
            consumer=settings.consumer_group,
        )
        tasks = [
            asyncio.create_task(consumer.run(), name="consumer"),
            asyncio.create_task(sweep_loop(service, settings.sweep_interval_s), name="sweep"),
        ]
        app.state.service = service
        app.state.health_checks = {
            "database": lambda: pool.fetchval("SELECT 1"),
            "redis": client.ping,
            "background_tasks": tasks_alive(tasks),
        }
        log.info("alert-service started")
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await client.aclose()
            await pool.close()

    return create_app(lifespan)


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    uvicorn.run(build_app(settings), host="0.0.0.0", port=settings.port, log_config=None)


if __name__ == "__main__":
    main()

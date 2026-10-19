"""Redis Streams publisher for rule_match events (contract in CLAUDE.md)."""

from __future__ import annotations

import json

from redis.asyncio import Redis
from redis.exceptions import RedisError

from ingest_service.domain.errors import DependencyUnavailable

STREAM = "rule_matches"
# Approximate cap so the stream cannot fill Redis memory if alert-service is down for long.
# ~100k events is hours of matches at simulator rates.
STREAM_MAXLEN = 100_000


class RedisStreamPublisher:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, events: list[dict]) -> None:
        """XADD rule_matches * data <JSON>, all events in one round-trip."""
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                for event in events:
                    pipe.xadd(
                        STREAM,
                        {"data": json.dumps(event, separators=(",", ":"))},
                        maxlen=STREAM_MAXLEN,
                        approximate=True,
                    )
                await pipe.execute()
        except (RedisError, OSError, TimeoutError) as exc:
            raise DependencyUnavailable(f"redis: {exc}") from exc


async def ping(redis: Redis) -> None:
    try:
        await redis.ping()
    except (RedisError, OSError, TimeoutError) as exc:
        raise DependencyUnavailable(f"redis: {exc}") from exc

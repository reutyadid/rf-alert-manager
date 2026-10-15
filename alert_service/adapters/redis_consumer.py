import asyncio
import logging

import redis.asyncio as redis
from redis.exceptions import ResponseError

from alert_service.adapters import metrics
from alert_service.adapters.events import InvalidEvent, parse_rule_match
from alert_service.domain.models import MatchOutcome
from alert_service.domain.service import AlertService

log = logging.getLogger(__name__)


class RedisMatchConsumer:
    """Reads rule_match events from a Redis Stream consumer group.

    An entry is acknowledged only after the domain has processed it (alert state is in
    PostgreSQL), so a crash or DB error means redelivery, never loss. On startup and after
    an error, this consumer's own unacknowledged entries are read again first ("0").
    Redelivery is safe because the domain skips observation_ids it has already seen.
    """

    def __init__(
        self,
        client: redis.Redis,
        service: AlertService,
        stream: str = "rule_matches",
        group: str = "alert-service",
        consumer: str = "alert-service",
        batch_size: int = 100,
        block_ms: int = 5000,
        retry_delay_s: float = 2.0,
    ) -> None:
        self._redis = client
        self._service = service
        self._stream = stream
        self._group = group
        self._consumer = consumer
        self._batch_size = batch_size
        self._block_ms = block_ms
        self._retry_delay_s = retry_delay_s

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def run(self) -> None:
        await self.ensure_group()
        read_from = "0"  # our own pending entries first
        while True:
            try:
                handled = await self.poll_once(read_from)
                if read_from == "0" and handled == 0:
                    read_from = ">"  # pending drained: switch to new entries
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("rule_match processing failed; retrying unacked entries")
                read_from = "0"
                await asyncio.sleep(self._retry_delay_s)

    async def poll_once(self, read_from: str = ">") -> int:
        """Read and process one batch. Returns the number of entries read."""
        response = await self._redis.xreadgroup(
            self._group,
            self._consumer,
            {self._stream: read_from},
            count=self._batch_size,
            block=self._block_ms if read_from == ">" else None,
        )
        entries = response[0][1] if response else []
        for entry_id, fields in entries:
            await self._handle(entry_id, fields)
        return len(entries)

    async def _handle(self, entry_id: str, fields: dict[str, str] | None) -> None:
        try:
            match = parse_rule_match((fields or {}).get("data", ""))
        except InvalidEvent as exc:
            # A malformed event will never succeed: ack it so it does not block the stream.
            metrics.INVALID_EVENTS.inc()
            log.warning("invalid rule_match event", extra={"entry_id": entry_id, "error": str(exc)})
            await self._redis.xack(self._stream, self._group, entry_id)
            return

        outcome = await self._service.handle_match(match)
        await self._redis.xack(self._stream, self._group, entry_id)

        metrics.RULE_MATCHES.labels(outcome=outcome.value).inc()
        metrics.PENDING_RUNS.set(self._service.pending_run_count)
        if outcome is MatchOutcome.LATE:
            metrics.LATE_DROPPED.inc()
        if outcome is MatchOutcome.OPENED:
            log.info(
                "alert opened",
                extra={"rule_id": match.rule_id, "frequency_mhz": match.frequency_mhz},
            )

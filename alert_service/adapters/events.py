"""Parse the rule_match wire format (shared contract with ingest-service) into a RuleMatch."""

import json
from datetime import UTC, datetime

from alert_service.domain.models import RuleMatch, RuleParams

SUPPORTED_SCHEMA_VERSION = 1


class InvalidEvent(ValueError):
    pass


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise InvalidEvent("timestamp must be an ISO-8601 string")
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidEvent(f"invalid timestamp {value!r}") from exc
    if ts.tzinfo is None:
        raise InvalidEvent("timestamp must include a UTC offset")
    return ts.astimezone(UTC)


def _number(obj: dict, key: str) -> float:
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidEvent(f"{key} must be a number")
    return float(value)


def _string(obj: dict, key: str) -> str:
    value = obj[key]
    if not isinstance(value, str) or not value:
        raise InvalidEvent(f"{key} must be a non-empty string")
    return value


def parse_rule_match(data: str) -> RuleMatch:
    """All fields are required; no defaults are applied. schema_version is optional (the
    agreed contract omits it), but if present it must be a version this service supports."""
    try:
        event = json.loads(data)
    except json.JSONDecodeError as exc:
        raise InvalidEvent("data is not valid JSON") from exc
    if not isinstance(event, dict):
        raise InvalidEvent("event must be a JSON object")
    version = event.get("schema_version", SUPPORTED_SCHEMA_VERSION)
    if version != SUPPORTED_SCHEMA_VERSION:
        raise InvalidEvent(f"unsupported schema_version {version!r}")
    try:
        rule = event["rule"]
        if not isinstance(rule, dict):
            raise InvalidEvent("rule must be an object")
        return RuleMatch(
            observation_id=_string(event, "observation_id"),
            rule_id=_string(event, "rule_id"),
            sensor_id=_string(event, "sensor_id"),
            timestamp=_timestamp(event["timestamp"]),
            frequency_mhz=_number(event, "frequency_mhz"),
            power_dbm=_number(event, "power_dbm"),
            rule=RuleParams(
                min_duration_s=_number(rule, "min_duration_s"),
                max_gap_s=_number(rule, "max_gap_s"),
                resolve_after_s=_number(rule, "resolve_after_s"),
                severity=_string(rule, "severity"),
            ),
        )
    except KeyError as exc:
        raise InvalidEvent(f"missing field {exc.args[0]!r}") from exc

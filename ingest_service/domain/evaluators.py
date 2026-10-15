"""Rule evaluators. A new rule type is a new evaluator class plus one registry entry."""

from __future__ import annotations

from typing import Protocol

from ingest_service.domain.models import Observation, Rule


class RuleEvaluator(Protocol):
    def matches(self, rule: Rule, observation: Observation) -> bool: ...


class PowerThresholdEvaluator:
    """Inside the band (edges inclusive) and strictly above the threshold."""

    def matches(self, rule: Rule, observation: Observation) -> bool:
        return (
            rule.band.contains(observation.frequency_mhz)
            and observation.power_dbm > rule.threshold_dbm
        )


EVALUATORS: dict[str, RuleEvaluator] = {
    "power_threshold": PowerThresholdEvaluator(),
}


def is_known_rule_type(rule_type: str) -> bool:
    return rule_type in EVALUATORS


def matching_rules(observation: Observation, rules: list[Rule]) -> list[Rule]:
    """Each enabled rule is evaluated independently; rules of unknown type never match."""
    matched = []
    for rule in rules:
        evaluator = EVALUATORS.get(rule.type)
        if rule.enabled and evaluator is not None and evaluator.matches(rule, observation):
            matched.append(rule)
    return matched

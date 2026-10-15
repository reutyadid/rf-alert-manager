"""Rule management. The database is the source of truth; YAML only seeds an empty table."""

from __future__ import annotations

from ingest_service.domain.errors import RuleAlreadyExists, RuleNotFound, UnknownRuleType
from ingest_service.domain.evaluators import is_known_rule_type
from ingest_service.domain.models import Rule
from ingest_service.domain.ports import RuleRepository


class RuleService:
    def __init__(self, repository: RuleRepository) -> None:
        self._repo = repository

    async def list(self) -> list[Rule]:
        return await self._repo.list()

    async def create(self, rule: Rule) -> Rule:
        _check_type(rule)
        if not await self._repo.create(rule):
            raise RuleAlreadyExists(f"rule '{rule.rule_id}' already exists")
        return rule

    async def update(self, rule: Rule) -> Rule:
        _check_type(rule)
        if not await self._repo.update(rule):
            raise RuleNotFound(f"rule '{rule.rule_id}' not found")
        return rule

    async def delete(self, rule_id: str) -> None:
        if not await self._repo.delete(rule_id):
            raise RuleNotFound(f"rule '{rule_id}' not found")

    async def seed_if_empty(self, rules: list[Rule]) -> int:
        for rule in rules:
            _check_type(rule)
        return await self._repo.seed_if_empty(rules)


def _check_type(rule: Rule) -> None:
    if not is_known_rule_type(rule.type):
        raise UnknownRuleType(f"unknown rule type '{rule.type}'")

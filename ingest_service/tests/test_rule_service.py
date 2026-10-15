import asyncio

import pytest

from ingest_service.domain.errors import RuleAlreadyExists, RuleNotFound, UnknownRuleType
from ingest_service.domain.rules import RuleService
from ingest_service.tests.builders import a_rule
from ingest_service.tests.fakes import FakeRuleRepository


def run(coro):
    return asyncio.run(coro)


def test_create_and_list():
    service = RuleService(FakeRuleRepository())
    run(service.create(a_rule()))
    assert [r.rule_id for r in run(service.list())] == ["rule-ism-high-power"]


def test_create_duplicate_id_fails():
    service = RuleService(FakeRuleRepository([a_rule()]))
    with pytest.raises(RuleAlreadyExists):
        run(service.create(a_rule()))


def test_unknown_type_rejected():
    service = RuleService(FakeRuleRepository())
    with pytest.raises(UnknownRuleType):
        run(service.create(a_rule(type="magic")))


def test_update_and_disable():
    repo = FakeRuleRepository([a_rule()])
    run(RuleService(repo).update(a_rule(enabled=False)))
    assert repo.rules["rule-ism-high-power"].enabled is False


def test_update_missing_rule_fails():
    with pytest.raises(RuleNotFound):
        run(RuleService(FakeRuleRepository()).update(a_rule()))


def test_delete():
    repo = FakeRuleRepository([a_rule()])
    run(RuleService(repo).delete("rule-ism-high-power"))
    assert repo.rules == {}
    with pytest.raises(RuleNotFound):
        run(RuleService(repo).delete("rule-ism-high-power"))


def test_seed_loads_when_table_empty():
    repo = FakeRuleRepository()
    assert run(RuleService(repo).seed_if_empty([a_rule(), a_rule(rule_id="b")])) == 2
    assert set(repo.rules) == {"rule-ism-high-power", "b"}


def test_seed_skipped_when_table_has_rules():
    repo = FakeRuleRepository([a_rule(rule_id="operator-made")])
    assert run(RuleService(repo).seed_if_empty([a_rule()])) == 0
    assert set(repo.rules) == {"operator-made"}


def test_seed_rejects_unknown_type():
    with pytest.raises(UnknownRuleType):
        run(RuleService(FakeRuleRepository()).seed_if_empty([a_rule(type="magic")]))

import asyncio

import pytest

from ingest_service.adapters.rules_yaml import read_rules_file
from ingest_service.api.schemas import RuleIn
from ingest_service.domain.evaluators import is_known_rule_type
from ingest_service.domain.rules import RuleService
from ingest_service.main import DEFAULT_RULES_FILE, seed_rules
from ingest_service.tests.fakes import FakeRuleRepository


def test_shipped_seed_file_is_valid():
    rules = [RuleIn.model_validate(item) for item in read_rules_file(DEFAULT_RULES_FILE)]
    assert "rule-ism-high-power" in {r.rule_id for r in rules}
    assert all(is_known_rule_type(r.type) for r in rules)


def test_top_level_list_accepted(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("- rule_id: a\n  name: n\n")
    assert read_rules_file(path) == [{"rule_id": "a", "name": "n"}]


def test_bad_shape_rejected(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("rules: just-a-string\n")
    with pytest.raises(ValueError):
        read_rules_file(path)


def test_missing_rules_file_fails_startup(tmp_path):
    repo = FakeRuleRepository()
    with pytest.raises(FileNotFoundError):
        asyncio.run(seed_rules(RuleService(repo), tmp_path / "missing.yaml"))
    assert repo.rules == {}


def test_seed_rules_from_shipped_file():
    repo = FakeRuleRepository()
    asyncio.run(seed_rules(RuleService(repo), DEFAULT_RULES_FILE))
    assert "rule-ism-high-power" in repo.rules

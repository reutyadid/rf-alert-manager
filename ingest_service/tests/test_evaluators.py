import pytest

from ingest_service.domain.evaluators import EVALUATORS, is_known_rule_type, matching_rules
from ingest_service.tests.builders import a_rule, an_observation


@pytest.mark.parametrize(
    ("frequency", "power", "expected"),
    [
        (2437.0, -42.5, True),  # inside band, strong
        (2400.0, -42.5, True),  # lower band edge is inclusive
        (2483.5, -42.5, True),  # upper band edge is inclusive
        (2399.9, -42.5, False),  # below band
        (2483.6, -42.5, False),  # above band
        (2437.0, -50.0, False),  # equal to threshold: strict >
        (2437.0, -49.9, True),
        (2437.0, -80.0, False),  # weak
    ],
)
def test_power_threshold(frequency, power, expected):
    evaluator = EVALUATORS["power_threshold"]
    obs = an_observation(frequency_mhz=frequency, power_dbm=power)
    assert evaluator.matches(a_rule(), obs) is expected


def test_matching_rules_skips_disabled():
    rules = [a_rule(), a_rule(rule_id="off", enabled=False)]
    assert [r.rule_id for r in matching_rules(an_observation(), rules)] == ["rule-ism-high-power"]


def test_each_rule_evaluated_independently():
    rules = [
        a_rule(),
        a_rule(rule_id="second", threshold_dbm=-45.0),
        a_rule(rule_id="strict", threshold_dbm=-30.0),
    ]
    matched = [r.rule_id for r in matching_rules(an_observation(power_dbm=-42.5), rules)]
    assert matched == ["rule-ism-high-power", "second"]


def test_rule_type_registry():
    assert is_known_rule_type("power_threshold")
    assert not is_known_rule_type("magic")

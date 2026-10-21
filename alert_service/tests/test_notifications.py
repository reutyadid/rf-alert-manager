from datetime import timedelta

import pytest

from alert_service.domain.notifications import BACKOFF_S, MAX_ATTEMPTS, next_attempt_at
from alert_service.tests.fakes import T0


@pytest.mark.parametrize(
    ("attempts", "delay_s"),
    [(1, 2), (2, 4), (3, 8), (4, 16), (5, 32), (6, 60), (7, 120), (8, 300), (9, 300)],
)
def test_backoff_after_each_failed_attempt(attempts, delay_s):
    assert next_attempt_at(attempts, T0) == T0 + timedelta(seconds=delay_s)


def test_gives_up_after_max_attempts():
    assert MAX_ATTEMPTS == 10
    assert next_attempt_at(MAX_ATTEMPTS, T0) is None
    assert next_attempt_at(MAX_ATTEMPTS + 1, T0) is None


def test_backoff_is_capped_at_300_s():
    assert max(BACKOFF_S) == 300
    assert len(BACKOFF_S) == MAX_ATTEMPTS - 1  # one wait between each pair of attempts

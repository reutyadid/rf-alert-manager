from datetime import datetime, timedelta

# Wait after the n-th failed attempt (index n - 1). Deterministic, no jitter: one dispatcher
# and one receiver, so there is no thundering herd to spread out.
BACKOFF_S = (2, 4, 8, 16, 32, 60, 120, 300, 300)
MAX_ATTEMPTS = len(BACKOFF_S) + 1


def next_attempt_at(attempts: int, now: datetime) -> datetime | None:
    """When to retry after `attempts` failed attempts, or None to give up."""
    if attempts >= MAX_ATTEMPTS:
        return None
    return now + timedelta(seconds=BACKOFF_S[attempts - 1])

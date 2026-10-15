import pytest
from pydantic import ValidationError

from alert_service.main import Settings

REQUIRED = {"database_url": "postgresql://x", "redis_url": "redis://x"}


def test_defaults_are_valid():
    settings = Settings(**REQUIRED)
    assert settings.freq_tolerance_mhz == 0.1


@pytest.mark.parametrize(
    "override",
    [
        {"freq_tolerance_mhz": 0},
        {"allowed_lateness_s": -1},
        {"idle_timeout_s": 0},
        {"sweep_interval_s": 0},
        {"max_clock_skew_s": 0},
    ],
)
def test_invalid_values_fail_at_startup(override):
    with pytest.raises(ValidationError):
        Settings(**REQUIRED, **override)


@pytest.mark.anyio
async def test_health_check_fails_when_a_background_task_stopped():
    import asyncio

    from alert_service.main import tasks_alive

    async def crash():
        raise RuntimeError("boom")

    task = asyncio.create_task(crash(), name="consumer")
    await asyncio.gather(task, return_exceptions=True)
    with pytest.raises(RuntimeError, match="consumer"):
        await tasks_alive([task])()

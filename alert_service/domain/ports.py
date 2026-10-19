from datetime import datetime
from typing import Protocol

from alert_service.domain.models import Alert, AlertFilter, AlertState, Contribution


class Clock(Protocol):
    def now(self) -> datetime: ...


class AlertRepository(Protocol):
    async def create(self, alert: Alert, contributions: list[Contribution]) -> None: ...

    async def get(self, alert_id: str) -> Alert | None: ...

    async def get_active_by_signal_key(self, signal_key: str) -> Alert | None: ...

    async def list_active(self) -> list[Alert]: ...

    async def has_contribution(self, alert_id: str, observation_id: str) -> bool: ...

    async def add_contribution(self, alert: Alert, contribution: Contribution) -> None:
        """Store the contribution and write the alert's aggregate fields. Never the state."""
        ...

    async def transition(
        self, alert_id: str, from_states: set[AlertState], to_state: AlertState, at: datetime
    ) -> Alert | None:
        """Change state only if the current state is in from_states. None if it was not."""
        ...

    async def query(self, flt: AlertFilter) -> list[Alert]: ...

    async def list_contributions(self, alert_id: str) -> list[Contribution]: ...

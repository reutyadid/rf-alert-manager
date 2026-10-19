class AlertNotFound(Exception):
    def __init__(self, alert_id: str) -> None:
        super().__init__(f"alert {alert_id} not found")
        self.alert_id = alert_id


class InvalidTransition(Exception):
    def __init__(self, alert_id: str, state: str, action: str) -> None:
        super().__init__(f"cannot {action} alert {alert_id} in state {state}")
        self.alert_id = alert_id
        self.state = state
        self.action = action

class DomainError(Exception):
    """Base class for errors the API maps to a client-facing response."""


class RuleNotFound(DomainError):
    pass


class RuleAlreadyExists(DomainError):
    pass


class UnknownRuleType(DomainError):
    pass


class DependencyUnavailable(Exception):
    """The database or the broker could not be reached. Adapters raise this."""

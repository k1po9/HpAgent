class DomainError(Exception):
    """A stable domain error suitable for a future HTTP adapter."""


class IdempotencyConflict(DomainError):
    pass


class ConversationBusy(DomainError):
    pass


class ResourceNotFound(DomainError):
    pass


class OutboxLeaseLost(DomainError):
    pass


class RunNotCancellable(DomainError):
    pass

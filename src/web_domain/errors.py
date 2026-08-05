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


class RunNotRetryable(DomainError):
    pass


class VersionConflict(DomainError):
    def __init__(self, current_version: int):
        self.current_version = current_version


class PreconditionRequired(DomainError):
    pass

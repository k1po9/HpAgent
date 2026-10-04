class DomainError(Exception):
    """A stable domain error suitable for a future HTTP adapter."""


class IdempotencyConflict(DomainError):
    pass


class ConversationBusy(DomainError):
    pass


class ResourceNotFound(DomainError):
    pass


class FileNotReady(DomainError):
    pass


class FileAlreadyBound(DomainError):
    pass


class FileTooLarge(DomainError):
    pass


class UnsupportedFileType(DomainError):
    pass


class FileEncodingUnsupported(DomainError):
    pass


class FileHashMismatch(DomainError):
    pass


class FileUploadInvalid(DomainError):
    pass


class OutboxLeaseLost(DomainError):
    pass


class RunNotCancellable(DomainError):
    pass


class RunNotRetryable(DomainError):
    pass


class RunRetryNotSafe(DomainError):
    def __init__(self, failure_code: str):
        self.failure_code = failure_code
        self.reason = ("unsafe_side_effect_state" if failure_code in {"tool_side_effect_uncertain", "side_effect_reconciliation_failed"} else "non_retryable_failure")


class VersionConflict(DomainError):
    def __init__(self, current_version: int):
        self.current_version = current_version


class PreconditionRequired(DomainError):
    pass

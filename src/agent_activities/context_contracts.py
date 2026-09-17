"""Source-owned execution bindings injected into durable capabilities."""
from typing import Any, Protocol


class ExecutionContextBindings(Protocol):
    def session_key(self, request: Any) -> str:
        """Return the source adapter's resource key for the Action capability."""
        ...

    def transcript_context(self, request: Any) -> dict[str, str | None]: ...

    def validate_loaded(self, request: Any, loaded: Any) -> None: ...

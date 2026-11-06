"""Work command conflicts with the existing current-state projection."""

from web_domain.errors import DomainError
from work_domain.serialization import to_json_value


class WorkConflict(DomainError):
    def __init__(self, current: dict, reason: str = "version_conflict"):
        super().__init__(reason)
        self.current = to_json_value(current)
        self.reason = reason

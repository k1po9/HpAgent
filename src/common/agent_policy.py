"""Small immutable Run policy; no runtime dependencies or environment reads."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from common.config_validation import typed_fields


@dataclass(frozen=True)
class AgentPolicy:
    max_tool_turns: int = 20
    default_timezone: str = "Asia/Shanghai"
    tool_result_summary_enabled: bool = True
    tool_result_summary_threshold: int = 4000
    tool_result_summary_max_chars: int = 1000

    def __post_init__(self):
        typed_fields(type(self), asdict(self), "agent_policy")
        for name in ("max_tool_turns", "tool_result_summary_threshold", "tool_result_summary_max_chars"):
            if getattr(self, name) <= 0:
                raise ValueError(f"agent_policy.{name}: must be positive")
        try:
            ZoneInfo(self.default_timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("agent_policy.default_timezone: invalid IANA timezone") from None

    def to_snapshot(self) -> dict:
        return {"schema_version": 1, **asdict(self)}

    @classmethod
    def from_snapshot(cls, value: object) -> AgentPolicy:
        names = {f.name for f in fields(cls)}
        if (not isinstance(value, dict) or set(value) != names | {"schema_version"}
                or type(value.get("schema_version")) is not int or value["schema_version"] != 1):
            raise ValueError("agent_policy: invalid snapshot fields or version")
        return cls(**{name: value[name] for name in names})

    @classmethod
    def from_config(cls, config: object) -> AgentPolicy:
        return cls(**{f.name: getattr(config, f.name) for f in fields(cls)})


def policy_from_run(snapshot: dict) -> AgentPolicy | None:
    # Absence alone is legacy. Explicit null/malformed snapshots must fail closed.
    return AgentPolicy.from_snapshot(snapshot["agent_policy"]) if "agent_policy" in snapshot else None


def load_agent_policy(path: str | Path | None = None) -> AgentPolicy:
    """Read only the existing Agent section, without loading models or services."""
    from memory.quality_config import read_yaml

    if path is None:
        source_root = Path(__file__).resolve().parents[1]
        root = source_root.parent if source_root.name == "src" else source_root
        path = root / "config" / "config.yaml"
    raw = read_yaml(Path(path), section="config")
    if not isinstance(raw, dict) or (raw.get("agent") is not None and not isinstance(raw["agent"], dict)):
        raise ValueError("config.agent: expected a mapping")
    values = raw.get("agent") or {}
    # These remain compatibility-only fields; recall_top_n belongs to memory config.
    legacy = {"mode", "idle_timeout_minutes", "event_fetch_limit", "activity_timeout",
              "archive_timeout", "multi_agent", "max_history_turns", "reflect_interval_hours", "recall_top_n"}
    names = {f.name for f in fields(AgentPolicy)}
    if unknown := values.keys() - names - legacy:
        raise ValueError(f"config.agent.{sorted(unknown)[0]}: unknown configuration field")
    for name in sorted((values.keys() & legacy) - {"recall_top_n"}):
        logging.getLogger(__name__).warning("config.agent.%s is ignored by execution policy", name)
    return AgentPolicy(**{name: values[name] for name in names & values.keys()})

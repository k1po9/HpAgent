"""Action requests and results shared with Brain and durable Activities."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol

from common.agent_policy import AgentPolicy

RAW_RESULT_MAX_BYTES = 1_048_576
OBSERVATION_MAX_CHARS = 50_000
OBSERVATION_MAX_BYTES = 200_000


def iter_result_json(value: Any):
    """Stream the default json.dumps representation with local traversal bounds.

    Strings are escaped in slices, so one giant scalar cannot allocate a giant
    encoder chunk. Keys and metadata count just like every other JSON byte.
    """
    active: set[int] = set()
    nodes = 0

    def encode(item, depth):
        nonlocal nodes
        nodes += 1
        if depth > 64:
            raise ValueError('result depth exceeds 64')
        if nodes > 100_000:
            raise ValueError('result nodes exceed 100000')
        if isinstance(item, str):
            yield '"'
            for offset in range(0, len(item), 8192):
                part = item[offset:offset + 8192]
                # PostgreSQL JSONB cannot represent NUL or lone surrogates.
                if '\x00' in part:
                    raise ValueError('result contains NUL')
                part.encode('utf-8')
                yield json.dumps(part)[1:-1]
            yield '"'
        elif isinstance(item, (dict, list, tuple)):
            identity = id(item)
            if identity in active:
                raise ValueError('Circular reference detected')
            active.add(identity)
            try:
                mapping = isinstance(item, dict)
                yield '{' if mapping else '['
                for index, entry in enumerate(item.items() if mapping else item):
                    if index:
                        yield ', '
                    if mapping:
                        key, child = entry
                        # Let the standard encoder validate/coerce JSON keys.
                        key_value = (key if isinstance(key, str) else
                                     json.loads(json.dumps({key: 0}, allow_nan=False)[1:-4]))
                        yield from encode(key_value, depth + 1)
                        yield ': '
                    else:
                        child = entry
                    yield from encode(child, depth + 1)
                yield '}' if mapping else ']'
            finally:
                active.remove(identity)
        else:
            yield json.dumps(item, allow_nan=False)

    yield from encode(value, 0)


def result_preview(value: Any, limit: int = 44_000) -> str:
    if isinstance(value, str):
        return value[:limit].encode('utf-8', errors='replace').decode('utf-8').replace('\x00', '\\u0000')
    parts, length = [], 0
    try:
        for chunk in iter_result_json(value):
            parts.append(chunk[:max(0, limit - length)])
            length += len(chunk)
            if length >= limit:
                break
    except (TypeError, ValueError, UnicodeError):
        parts.append(' [result preview unavailable]')
    return ''.join(parts)[:limit]


def result_references(output: Any) -> dict:
    """Keep existing top-level command and output receipts outside the preview."""
    if isinstance(output, str) and output.lstrip().startswith('{'):
        try:
            output = json.loads(output)
        except (ValueError, RecursionError):
            return {}
    return bounded_result_fields(output, ('command_receipt', 'file_id', 'file_ids', 'outputs',
                                          'result_ref', 'result_refs', 'artifact_id', 'version_id'))


def bounded_result_fields(output: Any, fields: tuple[str, ...]) -> dict:
    if not isinstance(output, dict):
        return {}
    references = {}
    remaining = 12_000 - 2
    for key in fields:
        if key in output:
            value = output[key]
            try:
                parts, size = [], len(json.dumps(key)) + 4
                for chunk in iter_result_json(value):
                    size += len(chunk)
                    if size > remaining:
                        break
                    parts.append(chunk)
                else:
                    references[key] = json.loads(''.join(parts))
                    remaining -= size
            except (ValueError, TypeError, UnicodeError):
                pass
    return references

if TYPE_CHECKING:
    from sandbox.tools.routing.models import ToolSelectionResult


@dataclass(frozen=True)
class ActionRequest:
    """A tool action requested by the brain."""

    id: str
    name: str
    arguments: Dict[str, Any]

    @classmethod
    def from_tool_call(cls, tool_call: Any) -> "ActionRequest":
        return cls(
            id=str(getattr(tool_call, "id", "")),
            name=str(getattr(tool_call, "name", "")),
            arguments=dict(getattr(tool_call, "arguments", {}) or {}),
        )

    def to_assistant_tool_call(self) -> Dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments}

    def to_model_event_tool_call(self, raw_tool_call: Any = None) -> Dict[str, Any]:
        if raw_tool_call is not None and hasattr(raw_tool_call, "to_dict"):
            return raw_tool_call.to_dict()
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass(frozen=True)
class ActionResult:
    """Result returned by the action runtime for one action request."""

    request: ActionRequest
    success: bool | None = None
    output: Any = None
    summary: Any = None
    error: Any = None
    metadata: Any = None
    raw: Optional[Dict[str, Any]] = None

    @classmethod
    def from_runtime_result(
        cls,
        request: ActionRequest,
        result: Dict[str, Any],
    ) -> "ActionResult":
        return cls(
            request=request,
            success=result.get("success"),
            output=result.get("output"),
            summary=result.get("summary"),
            error=result.get("error"),
            metadata=result.get("metadata"),
            raw=result,
        )

    @property
    def display_result(self) -> Any:
        return self.summary if self.summary is not None else self.output

    @property
    def failed(self) -> bool:
        """Whether the tool reported a semantic execution failure."""
        if self.success is not None:
            return not self.success
        return self.error is not None

    def to_observation(self, *, receipt_ref: str = '') -> str:
        """Build a valid, bounded model projection without mutating raw output."""
        processing = dict((self.metadata or {}).get('result_processing') or {})
        preview = result_preview(self.display_result, 44_001)
        processing['truncated'] = processing.get('truncated', False) or len(preview) > 44_000
        processing['error_truncated'] = isinstance(self.error, str) and len(self.error) > 2000
        processing['truncated'] |= processing['error_truncated']
        preview = preview[:44_000]
        envelope = {
            'success': not self.failed,
            'output': preview,
            'error': result_preview(self.error, 2000) if self.error is not None else None,
            'references': result_references(self.output),
            'result_processing': processing,
        }
        if receipt_ref:
            envelope['execution_receipt_ref'] = receipt_ref
        processing.update(returned_chars=0, returned_bytes=0)
        while True:
            text = json.dumps(envelope, ensure_ascii=False)
            size = len(text.encode('utf-8'))
            if len(text) > OBSERVATION_MAX_CHARS or size > OBSERVATION_MAX_BYTES:
                processing['truncated'] = True
                # Account for JSON escaping as well as UTF-8 expansion.
                preview = preview[:max(0, len(preview) - max(1, len(text) - OBSERVATION_MAX_CHARS,
                                                           (size - OBSERVATION_MAX_BYTES + 3) // 4))]
                envelope['output'] = preview
                continue
            if processing['returned_chars'] == len(text) and processing['returned_bytes'] == size:
                return text
            processing.update(returned_chars=len(text), returned_bytes=size)


class ActionCapability(Protocol):
    """Stable tool capability consumed by durable Agent Activities."""

    def reset_execution(self, resource_key: str, execution_id: str) -> None: ...

    async def select_tools(
        self,
        *,
        resource_key: str,
        execution_id: str,
        user_content: str,
        group_context_text: str = "",
    ) -> ToolSelectionResult: ...

    def side_effect_class(self, resource_key: str, tool_name: str) -> str: ...

    def tool_category(self, resource_key: str, tool_name: str) -> str | None: ...

    def budget_reservation(self, resource_key: str, tool_name: str) -> dict[str, int]: ...

    async def execute_request(
        self,
        request: ActionRequest,
        *,
        resource_key: str,
        execution_id: str,
        user_query: str = "",
        idempotency_key: str = "",
        agent_policy: AgentPolicy | None = None,
    ) -> ActionResult: ...

    def clear_execution(self, resource_key: str, execution_id: str) -> None: ...

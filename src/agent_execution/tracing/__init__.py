"""Agent execution trace domain and best-effort EventSink projection."""

from .context import TraceContext, current_trace_context, trace_context, trace_node_id
from .instrumentation import model_observation_metadata, trace_end, trace_start
from .lifecycle import TraceLifecycleObserver
from .models import TraceEvent, TraceEventNode, TraceRun, TraceTree
from .repository import PostgresTraceRepository
from .sink import TraceEventSink, TracingWebEventSinkFactory

__all__ = [
    "PostgresTraceRepository",
    "TraceContext",
    "TraceEvent",
    "TraceEventNode",
    "TraceEventSink",
    "TraceLifecycleObserver",
    "TraceRun",
    "TraceTree",
    "TracingWebEventSinkFactory",
    "current_trace_context",
    "model_observation_metadata",
    "trace_context",
    "trace_end",
    "trace_node_id",
    "trace_start",
]

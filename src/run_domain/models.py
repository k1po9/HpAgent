"""Run success is an execution fact, separate from mandate completion."""

ACTIVE = frozenset({'queued', 'running', 'cancelling'})
TERMINAL = frozenset({'succeeded', 'failed', 'cancelled'})

"""Canonical durable lifecycle and independent business Workflow composition."""
__all__ = ["start_worker"]


def __getattr__(name: str):
    """Keep public imports lazy so Workflow sandbox imports stay isolated."""
    if name == "start_worker":
        from .worker import start_worker

        return start_worker
    raise AttributeError(name)

"""Independent HpAgent Web HTTP API boundary."""

from .app import create_app
from .config import WebApiSettings

__all__ = ["WebApiSettings", "create_app"]

"""Backward-compatible channel imports.

The transport implementations live in :mod:`channels`. This package remains as
an import shim for older code that still imports ``sandbox.channels``.
"""

from channels import BaseChannel, ChannelRouter, ConsoleChannel, NapCatChannel, OfficialQQChannel

__all__ = ["BaseChannel", "ConsoleChannel", "NapCatChannel", "OfficialQQChannel", "ChannelRouter"]

"""Chat memory adapters and deterministic scheduled workflows.

Import adapters explicitly from memory.hindsight_client. Do not eagerly load
SDK/HTTP dependencies here: importing memory.workflows enters Temporal sandbox.
Native Consolidation owns persistent observations; whole-bank Reflect is retired.
"""

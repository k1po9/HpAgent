from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, cast


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def token_digest(token: str, pepper: bytes) -> bytes:
    return hmac.new(pepper, token.encode(), hashlib.sha256).digest()


def csrf_token(session_token: str, key: bytes) -> str:
    return _b64encode(hmac.new(key, session_token.encode(), hashlib.sha256).digest())


def csrf_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@dataclass(frozen=True)
class CursorError(Exception):
    code: str


class CursorCodec:
    def __init__(self, keys: dict[str, bytes], active_key_id: str, ttl_seconds: int):
        self._keys = keys
        self._active_key_id = active_key_id
        self._ttl_seconds = ttl_seconds

    def encode(self, payload: dict[str, Any]) -> str:
        body = {"kid": self._active_key_id, "issued_at": int(time.time()), **payload}
        encoded = _b64encode(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        )
        signature = hmac.new(
            self._keys[self._active_key_id], encoded.encode(), hashlib.sha256
        ).digest()
        return f"{encoded}.{_b64encode(signature)}"

    def decode(self, value: str) -> dict[str, Any]:
        try:
            encoded, encoded_signature = value.split(".", 1)
            body = json.loads(_b64decode(encoded))
            key = self._keys[body["kid"]]
            signature = _b64decode(encoded_signature)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            raise CursorError("invalid_cursor") from None
        expected = hmac.new(key, encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise CursorError("invalid_cursor")
        issued_at = body.get("issued_at")
        if not isinstance(issued_at, int):
            raise CursorError("invalid_cursor")
        if issued_at > int(time.time()) + 60:
            raise CursorError("invalid_cursor")
        if int(time.time()) - issued_at > self._ttl_seconds:
            raise CursorError("cursor_expired")
        return cast(dict[str, Any], body)

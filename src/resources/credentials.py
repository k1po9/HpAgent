from typing import Dict, Any, Optional, List
from threading import RLock
from dataclasses import dataclass, field
import time
import uuid


@dataclass
class Credential:
    resource_id: str
    credential_type: str
    encrypted_value: str
    scope: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def has_scope(self, required_scope: str) -> bool:
        if "all" in self.scope:
            return True
        return required_scope in self.scope


class CredentialManager:
    def __init__(self):
        self._credentials: Dict[str, Credential] = {}
        self._temp_tokens: Dict[str, Dict[str, Any]] = {}
        self._lock = RLock()

    def register_credential(self, resource_id: str, credential_type: str, value: str, scope: Optional[list] = None, expires_at: Optional[float] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            credential = Credential(resource_id=resource_id, credential_type=credential_type, encrypted_value=self._encrypt(value), scope=scope or [], expires_at=expires_at, metadata=metadata or {})
            self._credentials[resource_id] = credential

    def get_credential(self, resource_id: str) -> Optional[Credential]:
        with self._lock:
            return self._credentials.get(resource_id)

    def issue_temp_token(self, resource_id: str, scope: list, ttl_seconds: int = 3600) -> str:
        with self._lock:
            credential = self._credentials.get(resource_id)
            if not credential:
                raise ValueError(f"Credential not found: {resource_id}")
            if credential.is_expired():
                raise ValueError(f"Credential expired: {resource_id}")
            for required_scope in scope:
                if not credential.has_scope(required_scope):
                    raise ValueError(f"Scope '{required_scope}' not allowed for resource '{resource_id}'")
            token_id = str(uuid.uuid4())
            self._temp_tokens[token_id] = {"resource_id": resource_id, "scope": scope, "issued_at": time.time(), "expires_at": time.time() + ttl_seconds}
            return token_id

    def validate_token(self, token_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            token_data = self._temp_tokens.get(token_id)
            if not token_data:
                return None
            if time.time() > token_data["expires_at"]:
                del self._temp_tokens[token_id]
                return None
            return token_data

    def revoke_token(self, token_id: str) -> bool:
        with self._lock:
            if token_id in self._temp_tokens:
                del self._temp_tokens[token_id]
                return True
            return False

    def _encrypt(self, value: str) -> str:
        return value

    def _decrypt(self, encrypted_value: str) -> str:
        return encrypted_value

    def get_decrypted_credential(self, resource_id: str) -> Optional[str]:
        credential = self.get_credential(resource_id)
        if not credential:
            return None
        return self._decrypt(credential.encrypted_value)

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def _secret(name: str, default: str) -> bytes:
    return os.getenv(name, default).encode("utf-8")


@dataclass(frozen=True)
class WebApiSettings:
    database_url: str
    public_origin: str
    cursor_signing_keys: dict[str, bytes]
    active_cursor_key_id: str
    session_token_pepper: bytes
    csrf_signing_key: bytes
    qq_binding_code_pepper: bytes = b"development-qq-binding-code-pepper"
    qq_binding_challenge_seconds: int = 300
    environment: str = "development"
    worker_database_url: str | None = None
    credential_records: dict[str, str] = field(default_factory=dict)
    session_absolute_seconds: int = 7 * 24 * 60 * 60
    session_idle_seconds: int = 24 * 60 * 60
    cursor_ttl_seconds: int = 24 * 60 * 60
    cookie_secure: bool = True
    fake_executor_enabled: bool = False
    fake_executor_delay_seconds: float = 0.05
    fake_executor_mode: str = "success"
    fake_executor_content: str = "这是由测试执行器生成的回复。"
    fake_executor_failure_code: str = "fake_executor_failure"
    real_agent_enabled: bool = False
    durable_agent_enabled: bool = False
    # --- Phase E: SSE / terminal publisher ---
    redis_url: str | None = None
    sse_handshake_buffer_events: int = 256
    sse_handshake_buffer_bytes: int = 1 * 1024 * 1024
    sse_keepalive_seconds: float = 15.0
    sse_max_connections: int = 256
    terminal_publisher_poll_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.active_cursor_key_id not in self.cursor_signing_keys:
            raise ValueError("active cursor key id is not configured")
        if self.fake_executor_enabled and self.environment == "production":
            raise ValueError("fake executor cannot be enabled in production")
        if self.fake_executor_enabled and not self.worker_database_url:
            raise ValueError("fake executor requires WORKER_DATABASE_URL")
        if self.fake_executor_mode not in {"success", "failure", "hold"}:
            raise ValueError("invalid fake executor mode")
        if self.qq_binding_challenge_seconds <= 0:
            raise ValueError("QQ binding challenge TTL must be positive")
        if self.environment == "production":
            for value, name in (
                (self.session_token_pepper, "session token pepper"),
                (self.csrf_signing_key, "csrf signing key"),
                (self.qq_binding_code_pepper, "QQ binding code pepper"),
                (self.cursor_signing_keys[self.active_cursor_key_id], "cursor key"),
            ):
                if len(value) < 32:
                    raise ValueError(f"{name} must contain at least 32 bytes")

    @classmethod
    def from_env(cls) -> WebApiSettings:
        key_id = os.getenv("WEB_CURSOR_ACTIVE_KEY_ID", "v1")
        cursor_keys = json.loads(os.getenv("WEB_CURSOR_KEYS_JSON", "{}"))
        if not cursor_keys:
            cursor_keys = {key_id: os.getenv("WEB_CURSOR_SECRET", "development-cursor-secret")}
        credentials = json.loads(os.getenv("WEB_CREDENTIALS_JSON", "{}"))
        environment = os.getenv("HPAGENT_ENV", "development")
        # G-02 §10.2：生产必须显式配置最终用户真正访问的 HTTPS origin，
        # 不得沿用开发默认值或内部服务地址（http://hpagent-api:8080）。
        raw_public_origin = os.getenv("WEB_PUBLIC_ORIGIN")
        if environment == "production":
            if not raw_public_origin:
                raise ValueError("WEB_PUBLIC_ORIGIN must be explicitly set in production")
            if raw_public_origin == "https://localhost":
                raise ValueError(
                    "WEB_PUBLIC_ORIGIN must be the real public HTTPS origin, not the dev "
                    "default https://localhost"
                )
            if not raw_public_origin.startswith("https://"):
                raise ValueError(
                    "WEB_PUBLIC_ORIGIN must use the https:// scheme in production"
                )
            if not os.getenv("QQ_BINDING_CODE_PEPPER"):
                raise ValueError(
                    "QQ_BINDING_CODE_PEPPER must be explicitly set in production"
                )
        return cls(
            database_url=os.environ["APP_DATABASE_URL"],
            worker_database_url=os.getenv("WORKER_DATABASE_URL"),
            public_origin=raw_public_origin or "https://localhost",
            cursor_signing_keys={name: value.encode() for name, value in cursor_keys.items()},
            active_cursor_key_id=key_id,
            session_token_pepper=_secret(
                "WEB_SESSION_TOKEN_PEPPER", "development-session-token-pepper"
            ),
            csrf_signing_key=_secret("WEB_CSRF_SIGNING_KEY", "development-csrf-key"),
            qq_binding_code_pepper=_secret(
                "QQ_BINDING_CODE_PEPPER", "development-qq-binding-code-pepper"
            ),
            qq_binding_challenge_seconds=int(
                os.getenv("QQ_BINDING_CHALLENGE_SECONDS", "300")
            ),
            environment=environment,
            credential_records=credentials,
            cookie_secure=os.getenv("WEB_COOKIE_SECURE", "true").lower() == "true",
            fake_executor_enabled=os.getenv("WEB_FAKE_EXECUTOR_ENABLED", "false").lower()
            == "true",
            fake_executor_delay_seconds=float(
                os.getenv("WEB_FAKE_EXECUTOR_DELAY_SECONDS", "0.05")
            ),
            fake_executor_mode=os.getenv("WEB_FAKE_EXECUTOR_MODE", "success"),
            fake_executor_content=os.getenv(
                "WEB_FAKE_EXECUTOR_CONTENT", "这是由测试执行器生成的回复。"
            ),
            fake_executor_failure_code=os.getenv(
                "WEB_FAKE_EXECUTOR_FAILURE_CODE", "fake_executor_failure"
            ),
            real_agent_enabled=os.getenv("WEB_REAL_AGENT_ENABLED", "false").lower() == "true",
            durable_agent_enabled=os.getenv("DURABLE_AGENT_ENABLED", "false").lower()
            == "true",
            redis_url=os.getenv("REDIS_URL") or None,
            sse_handshake_buffer_events=int(
                os.getenv("WEB_SSE_HANDSHAKE_BUFFER_EVENTS", "256")
            ),
            sse_handshake_buffer_bytes=int(
                os.getenv("WEB_SSE_HANDSHAKE_BUFFER_BYTES", str(1 * 1024 * 1024))
            ),
            sse_keepalive_seconds=float(os.getenv("WEB_SSE_KEEPALIVE_SECONDS", "15.0")),
            sse_max_connections=int(os.getenv("WEB_SSE_MAX_CONNECTIONS", "256")),
            terminal_publisher_poll_seconds=float(
                os.getenv("WEB_TERMINAL_PUBLISHER_POLL_SECONDS", "0.5")
            ),
        )

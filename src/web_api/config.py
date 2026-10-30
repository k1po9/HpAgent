from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields

from common.config_validation import boolean, environment_name, integer, number, typed_fields
from common.runtime_config import (
    DEFAULT_QQ_BINDING_CHALLENGE_SECONDS,
    DEVELOPMENT_QQ_BINDING_CODE_PEPPER,
    CapacityConfig,
    FileRuntimeConfig,
    binding_challenge_seconds,
    run_budget_mode,
)


def _secret(name: str, default: str | bytes) -> bytes:
    value = os.getenv(name)
    if value is not None:
        return value.encode("utf-8")
    return default if isinstance(default, bytes) else default.encode("utf-8")


@dataclass(frozen=True)
class WebApiSettings:
    database_url: str
    public_origin: str
    cursor_signing_keys: dict[str, bytes]
    active_cursor_key_id: str
    session_token_pepper: bytes
    csrf_signing_key: bytes
    qq_binding_code_pepper: bytes = DEVELOPMENT_QQ_BINDING_CODE_PEPPER
    qq_binding_challenge_seconds: int = DEFAULT_QQ_BINDING_CHALLENGE_SECONDS
    environment: str = "development"
    worker_database_url: str | None = None
    hindsight_url: str | None = None
    hindsight_api_key: str = ""
    session_absolute_seconds: int = 7 * 24 * 60 * 60
    session_idle_seconds: int = 24 * 60 * 60
    cursor_ttl_seconds: int = 24 * 60 * 60
    cookie_secure: bool = True
    fake_executor_enabled: bool = False
    fake_executor_delay_seconds: float = 0.05
    fake_executor_mode: str = "success"
    fake_executor_content: str = "这是由测试执行器生成的回复。"
    fake_executor_failure_code: str = "fake_executor_failure"
    web_file_upload_enabled: bool = False
    web_file_transform_enabled: bool = False
    web_file_shell_enabled: bool = False
    file_store_root: str = ".data/file-store"
    file_max_bytes: int = FileRuntimeConfig.file_max_bytes
    file_max_count_per_message: int = FileRuntimeConfig.file_max_count_per_message
    run_budget_mode: str = "observe"
    run_budget_policy_version: str = "file-p0-v1"
    # --- Phase E: SSE / terminal publisher ---
    redis_url: str | None = None
    sse_handshake_buffer_events: int = 256
    sse_handshake_buffer_bytes: int = 1 * 1024 * 1024
    sse_keepalive_seconds: float = 15.0
    sse_max_connections: int = 256
    terminal_publisher_poll_seconds: float = 0.5

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", environment_name(self.environment))
        typed_fields(type(self), {f.name: getattr(self, f.name) for f in fields(self)})
        for attribute, path in (
            ("session_absolute_seconds", "session_absolute_seconds"),
            ("session_idle_seconds", "session_idle_seconds"),
            ("cursor_ttl_seconds", "cursor_ttl_seconds"),
            ("sse_handshake_buffer_events", "WEB_SSE_HANDSHAKE_BUFFER_EVENTS"),
            ("sse_handshake_buffer_bytes", "WEB_SSE_HANDSHAKE_BUFFER_BYTES"),
            ("sse_keepalive_seconds", "WEB_SSE_KEEPALIVE_SECONDS"),
            ("sse_max_connections", "WEB_SSE_MAX_CONNECTIONS"),
            ("terminal_publisher_poll_seconds", "WEB_TERMINAL_PUBLISHER_POLL_SECONDS"),
        ):
            if getattr(self, attribute) <= 0:
                raise ValueError(f"{path}: must be positive")
        if self.fake_executor_delay_seconds < 0:
            raise ValueError("WEB_FAKE_EXECUTOR_DELAY_SECONDS: must be nonnegative")
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
        FileRuntimeConfig(
            file_max_bytes=self.file_max_bytes,
            file_max_count_per_message=self.file_max_count_per_message,
        )
        if self.run_budget_mode not in {"off", "observe", "enforce"}:
            raise ValueError("RUN_BUDGET_MODE must be off, observe, or enforce")
        if self.web_file_transform_enabled and not self.web_file_upload_enabled:
            raise ValueError("file transforms require file uploads")
        if self.web_file_shell_enabled and not self.web_file_upload_enabled:
            raise ValueError("Web file shell requires file uploads")
        if (
            self.environment == "production"
            and self.web_file_upload_enabled
            and self.run_budget_mode != "enforce"
        ):
            raise ValueError("production file capability requires RUN_BUDGET_MODE=enforce")
        if self.environment == "production" and self.web_file_shell_enabled:
            raise ValueError("host Bash cannot be enabled for production Web file runs")
        if self.environment == "production":
            if self.public_origin == "https://localhost":
                raise ValueError("WEB_PUBLIC_ORIGIN must be the real public HTTPS origin")
            if not self.public_origin.startswith("https://"):
                raise ValueError("WEB_PUBLIC_ORIGIN must use the https:// scheme in production")
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
        CapacityConfig.from_environment()
        key_id = os.getenv("WEB_CURSOR_ACTIVE_KEY_ID", "v1")
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        try:
            cursor_keys = json.loads(
                os.getenv("WEB_CURSOR_KEYS_JSON", "{}"), object_pairs_hook=unique_keys
            )
            if not isinstance(cursor_keys, dict) or any(
                not isinstance(value, str) for value in cursor_keys.values()
            ):
                raise ValueError("invalid key ring")
        except (TypeError, ValueError):
            raise ValueError("WEB_CURSOR_KEYS_JSON: expected an object of unique text keys") from None
        if not cursor_keys:
            cursor_keys = {key_id: os.getenv("WEB_CURSOR_SECRET", "development-cursor-secret")}
        environment = environment_name(os.getenv("HPAGENT_ENV", cls.environment))
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
            hindsight_url=os.getenv("HINDSIGHT_URL", "http://hindsight:8888"),
            hindsight_api_key=os.getenv("HINDSIGHT_API_KEY", cls.hindsight_api_key),
            public_origin=raw_public_origin or "https://localhost",
            cursor_signing_keys={name: value.encode() for name, value in cursor_keys.items()},
            active_cursor_key_id=key_id,
            session_token_pepper=_secret(
                "WEB_SESSION_TOKEN_PEPPER", "development-session-token-pepper"
            ),
            csrf_signing_key=_secret("WEB_CSRF_SIGNING_KEY", "development-csrf-key"),
            qq_binding_code_pepper=_secret(
                "QQ_BINDING_CODE_PEPPER", cls.qq_binding_code_pepper
            ),
            qq_binding_challenge_seconds=binding_challenge_seconds(default=cls.qq_binding_challenge_seconds),
            environment=environment,
            cookie_secure=boolean(os.getenv("WEB_COOKIE_SECURE", str(cls.cookie_secure).lower()), "WEB_COOKIE_SECURE"),
            fake_executor_enabled=boolean(
                os.getenv("WEB_FAKE_EXECUTOR_ENABLED", str(cls.fake_executor_enabled).lower()), "WEB_FAKE_EXECUTOR_ENABLED"
            ),
            fake_executor_delay_seconds=number(
                os.getenv("WEB_FAKE_EXECUTOR_DELAY_SECONDS", str(cls.fake_executor_delay_seconds)), "WEB_FAKE_EXECUTOR_DELAY_SECONDS"
            ),
            fake_executor_mode=os.getenv("WEB_FAKE_EXECUTOR_MODE", cls.fake_executor_mode),
            fake_executor_content=os.getenv(
                "WEB_FAKE_EXECUTOR_CONTENT", cls.fake_executor_content
            ),
            fake_executor_failure_code=os.getenv(
                "WEB_FAKE_EXECUTOR_FAILURE_CODE", cls.fake_executor_failure_code
            ),
            web_file_upload_enabled=boolean(
                os.getenv("WEB_FILE_UPLOAD_ENABLED", str(cls.web_file_upload_enabled).lower()), "WEB_FILE_UPLOAD_ENABLED"
            ),
            web_file_transform_enabled=boolean(
                os.getenv("WEB_FILE_TRANSFORM_ENABLED", str(cls.web_file_transform_enabled).lower()), "WEB_FILE_TRANSFORM_ENABLED"
            ),
            web_file_shell_enabled=boolean(
                os.getenv("WEB_FILE_SHELL_ENABLED", str(cls.web_file_shell_enabled).lower()), "WEB_FILE_SHELL_ENABLED"
            ),
            file_store_root=os.getenv("FILE_STORE_ROOT", cls.file_store_root),
            file_max_bytes=integer(
                os.getenv("FILE_MAX_BYTES", str(cls.file_max_bytes)), "FILE_MAX_BYTES"
            ),
            file_max_count_per_message=integer(
                os.getenv("FILE_MAX_COUNT_PER_MESSAGE", str(cls.file_max_count_per_message)), "FILE_MAX_COUNT_PER_MESSAGE"
            ),
            run_budget_mode=run_budget_mode(default=cls.run_budget_mode),
            run_budget_policy_version=os.getenv(
                "RUN_BUDGET_POLICY_VERSION", cls.run_budget_policy_version
            ),
            redis_url=os.getenv("REDIS_URL") or None,
            sse_handshake_buffer_events=integer(
                os.getenv("WEB_SSE_HANDSHAKE_BUFFER_EVENTS", str(cls.sse_handshake_buffer_events)), "WEB_SSE_HANDSHAKE_BUFFER_EVENTS"
            ),
            sse_handshake_buffer_bytes=integer(
                os.getenv("WEB_SSE_HANDSHAKE_BUFFER_BYTES", str(cls.sse_handshake_buffer_bytes)),
                "WEB_SSE_HANDSHAKE_BUFFER_BYTES"
            ),
            sse_keepalive_seconds=number(
                os.getenv("WEB_SSE_KEEPALIVE_SECONDS", str(cls.sse_keepalive_seconds)), "WEB_SSE_KEEPALIVE_SECONDS"
            ),
            sse_max_connections=integer(
                os.getenv("WEB_SSE_MAX_CONNECTIONS", str(cls.sse_max_connections)), "WEB_SSE_MAX_CONNECTIONS"
            ),
            terminal_publisher_poll_seconds=number(
                os.getenv("WEB_TERMINAL_PUBLISHER_POLL_SECONDS", str(cls.terminal_publisher_poll_seconds)),
                "WEB_TERMINAL_PUBLISHER_POLL_SECONDS"
            ),
        )

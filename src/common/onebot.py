"""Configuration shared by the composition root and OneBot surface adapter."""
from dataclasses import dataclass


@dataclass
class OneBot11Config:
    host: str = '0.0.0.0'
    port: int = 8082
    path: str = '/onebot/v11'
    access_token_file: str = '/run/secrets/onebot_access_token'
    expected_self_id: str = ''
    ack_timeout_seconds: float = 30
    status_interval_seconds: float = 30
    status_stale_seconds: float = 90
    max_frame_bytes: int = 1048576
    ingress_queue_size: int = 256
    send_interval_seconds: float = 2

    def __post_init__(self):
        if not self.path.startswith('/') or '?' in self.path or not 0 <= self.port <= 65535:
            raise ValueError('invalid OneBot listener configuration')
        if min(self.ack_timeout_seconds, self.status_interval_seconds,
               self.max_frame_bytes, self.ingress_queue_size) <= 0:
            raise ValueError('OneBot bounds must be positive')
        if self.status_stale_seconds <= self.status_interval_seconds or self.send_interval_seconds < 0:
            raise ValueError('invalid OneBot timing bounds')

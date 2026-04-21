from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
import time


class RetryStrategy(str, Enum):
    EXPONENTIAL_BACKOFF = "exponential_backoff"
    LINEAR_BACKOFF = "linear_backoff"
    FIXED_DELAY = "fixed_delay"


@dataclass
class RetryPolicy:
    max_retries: int = 3
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    backoff_multiplier: float = 2.0
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL_BACKOFF
    retryable_errors: list = field(default_factory=list)

    def should_retry(self, error_type: str, attempt: int) -> bool:
        if attempt >= self.max_retries:
            return False
        if not self.retryable_errors:
            return True
        return error_type in self.retryable_errors

    def get_delay(self, attempt: int) -> float:
        if self.strategy == RetryStrategy.FIXED_DELAY:
            return self.initial_delay_seconds
        elif self.strategy == RetryStrategy.LINEAR_BACKOFF:
            delay = self.initial_delay_seconds * (attempt + 1)
            return min(delay, self.max_delay_seconds)
        elif self.strategy == RetryStrategy.EXPONENTIAL_BACKOFF:
            delay = self.initial_delay_seconds * (self.backoff_multiplier ** attempt)
            return min(delay, self.max_delay_seconds)
        return self.initial_delay_seconds

    def record_failure(self, error_type: str, error_message: str) -> None:
        pass

    def record_success(self) -> None:
        pass


class RetryExecutor:
    def __init__(self, policy: RetryPolicy):
        self._policy = policy
        self._attempt_times: Dict[str, list] = {}

    async def execute_with_retry(self, func, error_type: str, *args, **kwargs) -> Any:
        last_error = None
        for attempt in range(self._policy.max_retries + 1):
            if attempt > 0:
                if not self._policy.should_retry(error_type, attempt - 1):
                    raise last_error or Exception(f"Max retries exceeded for {error_type}")
                delay = self._policy.get_delay(attempt - 1)
                time.sleep(delay)
            try:
                result = await func(*args, **kwargs)
                if attempt > 0:
                    self._policy.record_success()
                return result
            except Exception as e:
                last_error = e
                self._policy.record_failure(error_type, str(e))
                if not self._policy.should_retry(error_type, attempt):
                    raise
        raise last_error or Exception(f"Max retries exceeded for {error_type}")

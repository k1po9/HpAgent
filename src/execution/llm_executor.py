from typing import Optional
import httpx
from src.core.types import TemplateContext
from src.core.config import ModelConfig


class ModelError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class ModelExecutor:
    def __init__(self, config: ModelConfig):
        self.config = config
        self._client: Optional[httpx.Client] = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.config.timeout_seconds)
        return self._client

    def generate(self, context: TemplateContext) -> str:
        """
        发送 conversation_history 到模型 API，返回回复文本。
        若失败则按 config.max_retries 重试，最终失败抛出 ModelError。
        """
        base_url = self.config.base_url or "https://api.openai.com/v1"
        url = f"{base_url.rstrip('/')}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.config.model,
            "messages": context.conversation_history,
            "temperature": 0.7,
        }

        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries + 1):
            try:
                client = self._get_client()
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                result = response.json()
                return result["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code < 500:
                    raise ModelError(
                        f"HTTP error: {e.response.status_code}",
                        status_code=e.response.status_code
                    )
            except (httpx.RequestError, KeyError, IndexError) as e:
                last_error = e

        raise ModelError(f"Failed after {self.config.max_retries + 1} attempts: {last_error}")

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

import pytest
from unittest.mock import Mock, patch, MagicMock
import httpx
from src.core.config import ModelConfig
from src.core.types import TemplateContext
from src.execution.llm_executor import ModelExecutor, ModelError


class TestLlmExecutor:
    def test_successful_generation(self):
        config = ModelConfig(
            api_key="test-key",
            model="gpt-4o-mini",
            base_url="https://api.test.com/v1",
        )
        executor = ModelExecutor(config)

        context = TemplateContext(
            body="Hello",
            session_key="test",
            conversation_history=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
            ]
        )

        mock_response = Mock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello! How can I help?"}}]
        }

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = executor.generate(context)

            assert result == "Hello! How can I help?"
            mock_client.post.assert_called_once()

    def test_retry_on_server_error(self):
        config = ModelConfig(
            api_key="test-key",
            max_retries=2,
        )
        executor = ModelExecutor(config)

        context = TemplateContext(
            body="Hello",
            session_key="test",
            conversation_history=[{"role": "user", "content": "Hello"}]
        )

        mock_success_response = Mock()
        mock_success_response.json.return_value = {
            "choices": [{"message": {"content": "Success"}}]
        }

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.post.side_effect = [
                httpx.RequestError("Server error"),
                httpx.RequestError("Server error"),
                mock_success_response,
            ]
            mock_client_class.return_value = mock_client

            result = executor.generate(context)
            assert result == "Success"
            assert mock_client.post.call_count == 3

    def test_raise_on_client_error(self):
        config = ModelConfig(api_key="test-key", max_retries=2)
        executor = ModelExecutor(config)

        context = TemplateContext(
            body="Hello",
            session_key="test",
            conversation_history=[]
        )

        mock_error_response = Mock()
        mock_error_response.status_code = 401

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_http_error = httpx.HTTPStatusError(
                "Unauthorized",
                request=Mock(),
                response=mock_error_response
            )
            mock_client.post.side_effect = mock_http_error
            mock_client_class.return_value = mock_client

            with pytest.raises(ModelError) as exc_info:
                executor.generate(context)

            assert exc_info.value.status_code == 401

    def test_max_retries_exceeded(self):
        config = ModelConfig(api_key="test-key", max_retries=2)
        executor = ModelExecutor(config)

        context = TemplateContext(
            body="Hello",
            session_key="test",
            conversation_history=[]
        )

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.post.side_effect = httpx.RequestError("Persistent error")
            mock_client_class.return_value = mock_client

            with pytest.raises(ModelError) as exc_info:
                executor.generate(context)

            assert "Failed after 3 attempts" in str(exc_info.value)

    def test_close(self):
        config = ModelConfig(api_key="test-key")
        executor = ModelExecutor(config)

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            executor._get_client()
            assert executor._client is not None

            executor.close()
            assert executor._client is None

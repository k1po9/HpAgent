from __future__ import annotations

import re
from typing import Any, Protocol

import httpx

from common.errors import ModelAPIError
from resources.model_governance_errors import classify_model_governance_failure
from resources.run_budget import RunBudgetExhausted


class ModelResource(Protocol):
    async def generate(self, *, messages: list[dict[str, Any]], model_selector: str,
                       tools: None = None, stream: bool = False,
                       max_tokens: int | None = None) -> Any: ...


class ArtifactGenerationError(Exception):
    def __init__(self, code: str, message: str, *, exception_type: str | None = None,
                 retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.exception_type = exception_type
        self.retryable = retryable


def classify_model_failure(exc: BaseException) -> ArtifactGenerationError:
    """Classify the original failure, including ResourcePool and client wrappers."""
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    for cause in chain:
        governance = classify_model_governance_failure(cause)
        if governance is not None:
            return ArtifactGenerationError(governance.code, governance.safe_message,
                                           exception_type=type(cause).__name__)
        if isinstance(cause, RunBudgetExhausted):
            return ArtifactGenerationError(cause.code, "模型预算不足。",
                                           exception_type=type(cause).__name__)
    for cause in chain:
        if isinstance(cause, (TimeoutError, httpx.TimeoutException)):
            return ArtifactGenerationError("artifact_model_timeout", "模型调用超时。",
                                           exception_type=type(cause).__name__, retryable=True)
        if isinstance(cause, (ConnectionError, httpx.ConnectError, httpx.NetworkError)):
            return ArtifactGenerationError("artifact_model_connection_failed", "模型连接失败。",
                                           exception_type=type(cause).__name__, retryable=True)
        if isinstance(cause, httpx.RequestError):
            return ArtifactGenerationError("artifact_model_http_error", "模型 HTTP 请求失败。",
                                           exception_type=type(cause).__name__, retryable=True)
    for cause in reversed(chain):
        status = None
        if isinstance(cause, httpx.HTTPStatusError):
            status = cause.response.status_code
        elif isinstance(cause, ModelAPIError):
            status = cause.details.get("status_code")
        if status is None:
            continue
        if status in (401, 403):
            code, message, retryable = "artifact_model_access_denied", "模型访问被拒绝。", False
        elif status in (402, 429):
            code, message, retryable = "artifact_model_quota_exhausted", "模型配额不足。", False
        elif 400 <= status < 500:
            code, message, retryable = "artifact_model_request_rejected", "模型提供商拒绝了请求。", False
        else:
            code, message, retryable = "artifact_model_http_error", "模型 HTTP 请求失败。", True
        return ArtifactGenerationError(code, message, exception_type=type(cause).__name__,
                                       retryable=retryable)
    if all(isinstance(cause, ModelAPIError) for cause in chain):
        return ArtifactGenerationError("artifact_model_unavailable", "模型暂时不可用。",
                                       exception_type=type(chain[-1]).__name__, retryable=True)
    return ArtifactGenerationError("artifact_internal_error", "Artifact 生成失败。",
                                   exception_type=type(chain[-1]).__name__)


_CSP = """<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; media-src data: blob:; object-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none';">"""
_ERROR_BRIDGE = """<script>(function(){function report(message){parent.postMessage({type:'hpagent-artifact-runtime-error',message:String(message||'Artifact runtime error')},'*')}window.addEventListener('error',function(e){report(e.message)});window.addEventListener('unhandledrejection',function(e){report(e.reason)});})();</script>"""


class WebArtifactGenerator:
    def __init__(self, resources: ModelResource, *, model_selector: str = "chat",
                 max_bytes: int = 1024 * 1024):
        self.resources = resources
        self.model_selector = model_selector
        self.max_bytes = max_bytes

    async def generate(self, *, source_markdown: str, instruction: str | None,
                       previous_html: str | None = None) -> str:
        prompt = self._prompt(source_markdown, instruction, previous_html)
        try:
            response = await self.resources.generate(
                messages=[{"role": "user", "content": prompt}],
                model_selector=self.model_selector, tools=None, stream=False,
            )
        except Exception as exc:
            raise classify_model_failure(exc) from exc
        return self.harden(str(getattr(response, "content", "") or ""))

    def harden(self, value: str) -> str:
        html = self._strip_fence(value).strip()
        if not html or not re.search(r"<html(?:\s|>)", html, re.I) or not re.search(r"</html\s*>", html, re.I):
            raise ArtifactGenerationError("artifact_invalid_html", "模型未返回完整 HTML。")
        injection = _CSP + _ERROR_BRIDGE
        head = re.search(r"<head(?:\s[^>]*)?>", html, re.I)
        if head:
            html = html[:head.end()] + injection + html[head.end():]
        else:
            html = re.sub(r"(<html(?:\s[^>]*)?>)", r"\1<head>" + injection + "</head>", html,
                          count=1, flags=re.I)
        if len(html.encode("utf-8")) > self.max_bytes:
            raise ArtifactGenerationError("artifact_html_too_large", "生成的 HTML 超过大小限制。")
        return html

    @staticmethod
    def _strip_fence(value: str) -> str:
        match = re.fullmatch(r"\s*```(?:html)?\s*\n([\s\S]*?)\n```\s*", value, re.I)
        return match.group(1) if match else value

    @staticmethod
    def _prompt(source: str, instruction: str | None, previous: str | None) -> str:
        parts = [
            "你是 Web Artifact Generator。把来源 Markdown 转换为完整、可独立运行的 HTML 文档。",
            "CSS 和 JavaScript 必须内联；不得使用 npm、CDN、外部网络或 fetch；不得虚构来源中的事实；只返回 HTML，不要代码围栏。",
            "页面会在 sandboxed iframe 中运行，不得读取 parent/window storage/cookie。可以使用按钮、筛选、Tab、SVG 和 Canvas。",
            "\n来源 Markdown:\n" + source,
        ]
        if previous:
            parts.append("\n上一版本 HTML（在其基础上修改）:\n" + previous)
        if instruction:
            parts.append("\n用户额外要求:\n" + instruction)
        return "\n".join(parts)

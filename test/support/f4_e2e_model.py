"""Deterministic OpenAI-compatible model used only by the F4 browser E2E profile."""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self._reply(200, {"status": "ok"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        messages = payload.get("messages", [])
        tools = payload.get("tools", [])
        names = {
            item.get("function", {}).get("name")
            for item in tools if isinstance(item, dict)
        }
        serialized = json.dumps(messages, ensure_ascii=False)
        last_user = max(
            (index for index, item in enumerate(messages) if item.get("role") == "user"),
            default=-1,
        )
        has_tool_result = any(
            item.get("role") == "tool" for item in messages[last_user + 1:]
        )
        if "save_persistent_file" in names and not has_tool_result:
            path = re.search(r"research/f4-e2e-[\w-]+\.md", serialized)
            file_ids = re.findall(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                serialized,
            )
            arguments = {
                "logical_path": path.group(0) if path else "research/f4-e2e.md",
                "source_file_id": file_ids[-1],
            }
            message = {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "f4-save", "type": "function",
                    "function": {
                        "name": "save_persistent_file",
                        "arguments": json.dumps(arguments),
                    },
                }],
            }
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": "持久文件操作已完成。"}
            finish = "stop"
        self._reply(200, {
            "id": "f4-fixture", "object": "chat.completion", "model": "f4-deterministic",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        })

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _reply(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 18081), Handler).serve_forever()

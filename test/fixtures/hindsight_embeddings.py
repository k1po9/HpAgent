"""Deterministic OpenAI embedding fixture. No external model requests.

Runs only on the dedicated G1 Docker network. Constant vectors deliberately
remove semantic ranking from the isolation assertions.
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        inputs = body["input"]
        if isinstance(inputs, str) or (inputs and isinstance(inputs[0], int)):
            inputs = [inputs]
        payload = {
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": [1.0] + [0.0] * 383}
                for i in range(len(inputs))
            ],
            "model": "g1-fixture",
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8181), Handler).serve_forever()

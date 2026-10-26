"""Local-only embedding/rerank HTTP for the production entrypoint rehearsal.

No chat completion endpoint: P5 never needs extraction/answering model calls.
"""
import json
from http.server import HTTPServer

from hindsight_embeddings import Handler as EmbeddingHandler


class Handler(EmbeddingHandler):
    def do_POST(self):
        if self.path.endswith('/embeddings'):
            return super().do_POST()
        if self.path.endswith('/rerank'):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            result = {'results': [{'index': i, 'relevance_score': 1 / (i + 1)}
                                   for i, _ in enumerate(body['documents'])]}
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(405)


if __name__ == '__main__':
    HTTPServer(('0.0.0.0',8181),Handler).serve_forever()

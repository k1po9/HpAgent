"""Private acceptance gateway serving the frozen production UI and API."""
import http.client
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Gateway(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith(('/api/', '/auth/', '/health/')):
            return self.proxy()
        return super().do_GET()

    def proxy(self):
        connection = http.client.HTTPConnection('api', 8080, timeout=30)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in {'host', 'connection'}}
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        connection.request(self.command, self.path, body=body, headers=headers)
        response = connection.getresponse()
        self.send_response(response.status)
        for k, v in response.getheaders():
            if k.lower() not in {'transfer-encoding', 'connection'}:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(response.read())
        connection.close()

    do_POST = do_PUT = do_PATCH = do_DELETE = proxy

    def log_message(self, *_):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), partial(Gateway, directory='/batch/frontend')).serve_forever()

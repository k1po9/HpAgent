"""Dedicated-container CONNECT proxy. Sole bridge from internal test network."""
import ipaddress
import json
import select
import socket
import time
from pathlib import Path
from socketserver import StreamRequestHandler, ThreadingTCPServer
from urllib.parse import urlsplit


def relay(client, upstream, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ready, _, _ = select.select([client, upstream], [], [], 1)
        for source in ready:
            data = source.recv(65536)
            if not data:
                return
            (upstream if source is client else client).sendall(data)


class Handler(StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(10)
        try:
            line = self.rfile.readline(4096).decode('ascii').strip()
            method, target, _ = line.split(' ')
            size = 0
            while True:
                header = self.rfile.readline(4096)
                size += len(header)
                if size > 16384:
                    raise ValueError('headers')
                if header in (b'\r\n', b'\n', b''):
                    break
            host, port = target.rsplit(':', 1)
            manifest = json.loads(Path('/batch/manifest.json').read_text())
            allowed = manifest['egress_hosts']
            if method != 'CONNECT' or host not in allowed or port != '443':
                self.wfile.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
                return
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError('nonpublic destination')
            proxy = manifest['resources'].get('outbound_proxy')
            if proxy:
                parsed = urlsplit(proxy)
                if parsed.scheme != 'http' or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('invalid frozen upstream proxy')
                upstream = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=10)
            else:
                family, socktype, proto, _, addr = addresses[0]
                upstream = socket.socket(family, socktype, proto)
                upstream.settimeout(10)
                upstream.connect(addr)
            with upstream:
                if proxy:
                    upstream.sendall(f'CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n'.encode())
                    header = b''
                    while not header.endswith(b'\r\n\r\n') and len(header) < 16384:
                        part = upstream.recv(1)
                        if not part:
                            raise ValueError('upstream proxy closed')
                        header += part
                    if b' 200 ' not in header.split(b'\r\n')[0]:
                        raise ValueError('upstream proxy denied')
                self.wfile.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                self.wfile.flush()
                relay(self.connection, upstream, manifest['resources'].get('egress_tunnel_seconds', 120))
        except (OSError, ValueError, UnicodeError):
            return


if __name__ == '__main__':
    with ThreadingTCPServer(('0.0.0.0', 8888), Handler) as server:
        server.daemon_threads = True
        server.serve_forever()

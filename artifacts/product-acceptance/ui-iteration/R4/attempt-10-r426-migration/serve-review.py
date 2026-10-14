from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
ROOT=Path('/home/hp/workspace/HpAgent/artifacts/product-acceptance/ui-iteration/R4/attempt-10-r426-migration')
class Review(SimpleHTTPRequestHandler):
 def __init__(self,*a,**k):super().__init__(*a,directory=str(ROOT),**k)
 def do_GET(self):
  if self.path.startswith('/assets/'):self.path='/dist'+self.path
  return super().do_GET()
 def do_POST(self):self.send_response(204);self.end_headers()
 def log_message(self,*a):pass
ThreadingHTTPServer(('0.0.0.0',5327),Review).serve_forever()

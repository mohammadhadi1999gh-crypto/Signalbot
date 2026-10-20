import os, time, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Separate service: runs ONLY the experimental strategy 2 (signal-only).
# It does not import toobit and never places exchange orders.
# Serves dashboard at / and data at /strategy2.json

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.getenv('DATA_DIR', '/data')
os.makedirs(DATA, exist_ok=True)

# Separation policy (as requested):
# - Signal messages: sent through the SAME Telegram bot and chat as the main strategy,
#   in a short format (entry / SL / TP / result), tagged [S2].
# - Reports and stats: kept separate. Strategy 2 has its own Volume (DATA_DIR) and its own
#   dashboard, and it never calls the main daily/weekly report functions.
# - Exchange: no orders are placed by strategy 2.
import bot
import strategy2

strategy2.install(bot)

HTML_PATH = os.path.join(HERE, 'dashboard_strategy2.html')
JSON_PATH = os.path.join(DATA, 'strategy2.json')


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        p = self.path.split('?')[0]
        if p in ('/', '/index.html'):
            body = open(HTML_PATH, 'rb').read() if os.path.exists(HTML_PATH) else b'<h1>dashboard file missing</h1>'
            ctype = 'text/html; charset=utf-8'
        elif p == '/strategy2.json':
            body = open(JSON_PATH, 'rb').read() if os.path.exists(JSON_PATH) else b'[]'
            ctype = 'application/json; charset=utf-8'
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def serve():
    ThreadingHTTPServer(('0.0.0.0', int(os.getenv('PORT', '8080'))), H).serve_forever()


threading.Thread(target=serve, daemon=True).start()
print('run_s2: strategy 2 service started (signal-only, no exchange orders)')
while True:
    time.sleep(3600)

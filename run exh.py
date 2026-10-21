"""
Runner for the Exhaustion Short backtest dashboard.
- Every EXH_EVERY_MIN minutes (default 60, min 15) runs the backtest over the top EXH_TOP USDT-SWAP
  symbols (default 100) for the last EXH_DAYS days (default 30) and writes
  $DATA_DIR/exhaustion.json (atomic write).
- Starts the LIVE signal scanner (exh_live.py, signal only, no orders).
- Serves dashboard_exhaustion.html at "/" and the JSON at "/exhaustion.json" on $PORT (default 8080) with live data at /live.json.
No orders are placed. This only backtests on historical OKX data.
"""
import os, json, time, threading, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import backtest_exhaustion as bt
import exh_live

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.getenv('DATA_DIR', '/data')
OUT = os.path.join(DATA, 'exhaustion.json')
LIVE_OUT = os.path.join(DATA, 'exh_live.json')
HTML = os.path.join(HERE, 'dashboard_exhaustion.html')
PORT = int(os.getenv('PORT', '8080'))
EVERY = int(float(os.getenv('EXH_EVERY_MIN', '60')) * 60)  # backtest refresh (min 15 min)
EVERY = max(EVERY, 900)
TOP = int(os.getenv('EXH_TOP', '100'))
DAYS = int(os.getenv('EXH_DAYS', '30'))
os.makedirs(DATA, exist_ok=True)


def build():
    syms = bt.top_symbols(TOP)
    all_trades, per = [], {}
    for n, s in enumerate(syms, 1):
        try:
            b15 = bt.fetch_hist(s, '15m', DAYS + 2)
            b5 = bt.fetch_hist(s, '5m', DAYS + 2)
            if len(b15) < 200 or len(b5) < 200:
                continue
            tr = bt.run_symbol(s, b15, b5)
            all_trades += tr
            per[s] = bt.stats(tr)
            print('[%d/%d] %s trades=%d' % (n, len(syms), s, len(tr)), flush=True)
        except Exception as e:
            print('skip', s, e, flush=True)
    # keep the JSON small: trades are kept in full for the dashboard table
    res = dict(updated=int(time.time() * 1000), days=DAYS, symbols=len(syms),
               total=bt.stats(all_trades), per_symbol=per, trades=all_trades)
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False)
    os.replace(tmp, OUT)
    print('exhaustion.json written, total =', res['total'], flush=True)


def loop():
    time.sleep(60)
    while True:
        try:
            build()
        except Exception:
            traceback.print_exc()
        time.sleep(EVERY)


class H(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/', '/index.html'):
            try:
                return self._send(200, 'text/html; charset=utf-8', open(HTML, 'rb').read())
            except Exception:
                return self._send(500, 'text/plain', b'dashboard file missing')
        if path == '/live.json':
            if os.path.exists(LIVE_OUT):
                return self._send(200, 'application/json; charset=utf-8', open(LIVE_OUT, 'rb').read())
            return self._send(404, 'application/json', b'{}')
        if path == '/exhaustion.json':
            if os.path.exists(OUT):
                return self._send(200, 'application/json; charset=utf-8', open(OUT, 'rb').read())
            return self._send(404, 'application/json', b'{}')
        return self._send(404, 'text/plain', b'not found')

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    threading.Thread(target=loop, daemon=True).start()
    exh_live.start()
    print('exhaustion dashboard on port', PORT, flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()

"""
Runner for the Exhaustion Short backtest dashboard.
- Every EXH_EVERY_MIN minutes (default 60, min 15) runs the backtest over the top-gainer universe (EXH_TOP_GAIN)
  top-gainer universe (EXH_TOP_GAIN per 15m bar, default 20; volume >= EXH_MIN_VOL) for the last EXH_DAYS days (default 30) and writes
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
TOP_GAIN = int(os.getenv('EXH_TOP_GAIN', '20'))
MIN_VOL = float(os.getenv('EXH_MIN_VOL', '5000000'))
DAYS = int(os.getenv('EXH_DAYS', '30'))
os.makedirs(DATA, exist_ok=True)


def build():
    trades = bt.run_universe(DAYS, TOP_GAIN, MIN_VOL, log=lambda m: print(m, flush=True))
    per = {}
    for t in trades:
        per.setdefault(t['sym'], []).append(t)
    per = {k: bt.stats(v) for k, v in per.items()}
    res = dict(updated=int(time.time() * 1000), days=DAYS, symbols=len(per),
               total=bt.stats(trades), per_symbol=per, trades=trades)
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

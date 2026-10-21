"""
LIVE signal scanner for the Exhaustion Short strategy. SIGNAL ONLY: never places orders.

Every EXH_LIVE_SEC seconds (default 60):
  - scans the top EXH_TOP USDT-SWAP symbols (universe refreshed every 30 min)
  - detects new 15m exhaustion signals on the last CLOSED 15m bar (same rules as backtest_exhaustion.py)
  - waits for the 5m CHoCH trigger (up to 1h); on trigger sends an entry message
  - tracks open trades with the same simulator used in the backtest, sends TP1 / close messages
  - writes $DATA_DIR/exh_live.json (dashboard data) and $DATA_DIR/exh_live_state.json (state)

Telegram: uses TELEGRAM_TOKEN and TELEGRAM_CHAT_ID (comma-separated) directly.
Messages start with [EX] so they are easy to tell apart from the main bot.
"""
import os, json, time, bisect, threading, traceback
import requests
import backtest_exhaustion as bt

DATA = os.getenv('DATA_DIR', '/data')
STATE = os.path.join(DATA, 'exh_live_state.json')
OUT = os.path.join(DATA, 'exh_live.json')
EVERY = int(os.getenv('EXH_LIVE_SEC', '60'))
TOP = int(os.getenv('EXH_TOP', '100'))
TOKEN = os.getenv('TELEGRAM_TOKEN', '')
CHATS = [c.strip() for c in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if c.strip()]
os.makedirs(DATA, exist_ok=True)
LOCK = threading.Lock()


def now_ms():
    return int(time.time() * 1000)


def tg(text):
    print('[EX]', text, flush=True)
    if not TOKEN:
        return
    for cid in CHATS:
        try:
            requests.post('https://api.telegram.org/bot%s/sendMessage' % TOKEN,
                          data={'chat_id': cid, 'text': text}, timeout=15)
        except Exception as e:
            print('telegram failed', e, flush=True)


def load_state():
    try:
        return json.load(open(STATE, encoding='utf-8'))
    except Exception:
        return {'pending': [], 'open': [], 'closed': [], 'seen': [], 'universe': [], 'uni_ts': 0}


def save_state(st):
    st['seen'] = st['seen'][-800:]
    st['closed'] = st['closed'][-1000:]
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE)
    trades = st['closed'] + st['open']
    res = dict(updated=now_ms(), live=True, trades=trades, total=bt.stats([t for t in st['closed']]),
               open=len(st['open']))
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False)
    os.replace(tmp, OUT)


def fetch_recent(inst, bar, limit=300):
    data = bt._get('/api/v5/market/candles', {'instId': inst, 'bar': bar, 'limit': str(limit)})
    rows = [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5]))
            for x in data if x[8] == '1']
    rows.sort(key=lambda r: r[0])
    return rows


def process_symbol(sym, st):
    b15 = fetch_recent(sym, '15m', 300)
    time.sleep(0.05)
    b5 = fetch_recent(sym, '5m', 300)
    time.sleep(0.05)
    if len(b15) < 120 or len(b5) < 120:
        return
    t15 = [x[0] for x in b15]
    o = [x[1] for x in b15]; h = [x[2] for x in b15]; l = [x[3] for x in b15]
    c = [x[4] for x in b15]; v = [x[5] for x in b15]
    A = bt.atr(h, l, c); E = bt.ema(c, 20); VA = bt.vol_avg(v); HIST = bt.macd_hist(c)
    t5 = [x[0] for x in b5]
    h5 = [x[2] for x in b5]; l5 = [x[3] for x in b5]; c5 = [x[4] for x in b5]
    H5 = bt.macd_hist(c5)
    i = len(b15) - 1
    now = now_ms()

    # 1) new 15m signal on the last closed bar
    if c[i] / c[i - 96] - 1 >= bt.GAIN_MIN:
        sig = bt.detect15(i, o, h, l, c, v, A, E, VA, HIST)
        if sig:
            t_high = t15[sig['hidx']]
            key = '%s|%d' % (sym, t_high)
            if key not in st['seen']:
                st['seen'].append(key)
                sig['t_high'] = t_high
                st['pending'].append({'key': key, 'sym': sym, 'sig': sig,
                                      't_sig': t15[i], 'expires': t15[i] + 3600000})

    # 2) triggers for this symbol's pending signals
    for p in [p for p in st['pending'] if p['sym'] == sym]:
        start5 = bisect.bisect_left(t5, p['t_sig'] + 900000)
        k = bt.find_trigger(p['sig'], t5, h5, l5, c5, H5, start5)
        if k is not None:
            st['pending'].remove(p)
            if t5[k] < now - 1200000:
                continue  # stale (e.g. bot was down); do not announce old entries
            tr = bt.simulate(p['sig'], k, t5, h5, l5, c5)
            if tr is None:
                continue
            e = tr['entry']
            rec = dict(id=p['key'], sym=sym, side='short', entry_time=t5[k], entry=e,
                       sl=tr['sl'], tp1=tr['tp1'], tp2=tr['tp2'], sig=p['sig'],
                       tp1_notified=False, pnl=None, R=None, reason='open', exit=None)
            st['open'].append(rec)
            tg('[EX] SHORT #%s | ورود: %s | SL: %s | TP1: %s | TP2: %s' % (
                sym.replace('-USDT-SWAP', ''), '%.6g' % e, '%.6g' % tr['sl'],
                '%.6g' % tr['tp1'], '%.6g' % tr['tp2']))
        elif t15[i] > p['expires']:
            st['pending'].remove(p)

    # 3) open trades for this symbol
    for rec in [r for r in st['open'] if r['sym'] == sym]:
        kk = bisect.bisect_left(t5, rec['entry_time'])
        if kk >= len(t5) or t5[kk] != rec['entry_time']:
            st['open'].remove(rec)
            rec.update(reason='lost_data', pnl=None)
            st['closed'].append(rec)
            continue
        tr = bt.simulate(rec['sig'], kk, t5, h5, l5, c5)
        if tr is None:
            continue
        if tr['tp1_hit_flag'] and not rec['tp1_notified']:
            rec['tp1_notified'] = True
            tg('[EX] TP1: #%s' % sym.replace('-USDT-SWAP', ''))
        if not tr['open']:
            st['open'].remove(rec)
            rec.update(exit=tr['exit'], reason=tr['reason'], pnl=tr['pnl'], R=tr['R'],
                       tp1_hit=tr['tp1_hit'], tp2_hit=tr['tp2_hit'])
            st['closed'].append(rec)
            tg('[EX] بسته شد: #%s | %s | %+.2f%%' % (sym.replace('-USDT-SWAP', ''), rec['reason'],
                                                    rec['pnl'] * 100))


def refresh_universe(st):
    if now_ms() - st.get('uni_ts', 0) > 30 * 60 * 1000 or not st.get('universe'):
        st['universe'] = bt.top_symbols(TOP)
        st['uni_ts'] = now_ms()


def loop():
    time.sleep(20)
    while True:
        with LOCK:
            st = load_state()
            try:
                refresh_universe(st)
                for sym in st['universe']:
                    try:
                        process_symbol(sym, st)
                    except Exception as e:
                        print('live: skip', sym, e, flush=True)
                save_state(st)
            except Exception:
                traceback.print_exc()
        time.sleep(EVERY)


def start():
    threading.Thread(target=loop, daemon=True).start()
    print('exh_live: scanner started (signal only, no orders)', flush=True)

"""
Backtest: Exhaustion Short on OKX USDT-SWAP top gainers.

Rules (all evaluated on CLOSED candles only, no look-ahead):
  Universe      : USDT-SWAP, top N by 24h volume (tickers endpoint)
  Gainer filter : 24h change (close vs close 96 bars earlier on 15m) >= GAIN_MIN
  Impulse (15m) : bullish bar, body >= 1.8*ATR14, volume >= 2.5*avg(vol,20)  (within last 16 bars = 4h)
  Extension     : close >= EMA20 + 2*ATR14
  Exhaustion    : high = max high from impulse bar to now (must be before the current bar)
  Weakness      : need >= 2 of 3
                  a) current volume < 0.5 * impulse volume
                  b) upper wick >= 50% of range on a bar after the high
                  c) MACD hist at the high < max hist between impulse and high (divergence)
  Trigger (5m)  : within 12 bars (1h) of the signal, a 5m CLOSE below the lowest low
                  since the 5m bar that contains the high, AND 5m MACD hist < 0
                  (entry at that close, a simplification: no retest is simulated)
  Stop          : high + 0.3*ATR14(15m). Skip if stop distance > 4*ATR14(15m) (in price)
  Targets       : TP1 = high - 0.382*leg, TP2 = high - 0.618*leg, leg = high - impulse low
                  Position split 1/3 TP1, 1/3 TP2, 1/3 runner (SL moves to entry after TP1)
  Invalidation  : 5m close above the high -> close remaining at that close
  Max hold      : 48 five-minute bars (4h)
  Costs         : taker fee 0.05% per side, slippage 0.05% on entry

Conservative fill rule: if a 5m bar touches both SL and TP, SL is assumed first.

Usage:
  python backtest_exhaustion.py --days 30 --top 100 --out results_exhaustion.json

Output: per-symbol and total stats in R-multiples and % of notional.
NOTE: this script has not been run against real OKX data inside the authoring session
(OKX access was blocked there). Treat the results you get as the first real evidence.
"""
import os, time, json, argparse, bisect
import requests

OKX = os.getenv('OKX_BASE', 'https://www.okx.com')
S = requests.Session()
GAIN_MIN = 0.15
FEE = 0.0005
SLIP = 0.0005


# ---------- data ----------
def _get(path, params):
    for _ in range(4):
        r = S.get(OKX + path, params=params, timeout=20)
        j = r.json()
        if j.get('code') == '0':
            return j['data']
        if j.get('code') == '50011':  # rate limit
            time.sleep(2)
            continue
        raise RuntimeError('%s %s' % (path, j.get('msg')))
    raise RuntimeError('rate limited: ' + path)


def top_symbols(n):
    data = _get('/api/v5/market/tickers', {'instType': 'SWAP'})
    rows = [d for d in data if d['instId'].endswith('-USDT-SWAP')]
    rows.sort(key=lambda d: float(d.get('volCcy24h') or 0), reverse=True)
    return [d['instId'] for d in rows[:n]]


def fetch_hist(inst, bar, days):
    """Closed candles, oldest first: list of (ts, o, h, l, c, v)."""
    start = int(time.time() * 1000) - days * 86400 * 1000
    after = str(int(time.time() * 1000) + 1)
    out = {}
    while True:
        data = _get('/api/v5/market/history-candles',
                    {'instId': inst, 'bar': bar, 'limit': 100, 'after': after})
        if not data:
            break
        for x in data:
            if x[8] == '1':
                out[int(x[0])] = (int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5]))
        oldest = int(data[-1][0])
        after = str(oldest)
        if oldest <= start:
            break
        time.sleep(0.12)
    return [out[k] for k in sorted(out) if k >= start]


# ---------- indicators ----------
def ema(v, n):
    k = 2 / (n + 1)
    out = [None] * len(v)
    e = None
    for i, x in enumerate(v):
        e = x if e is None else x * k + e * (1 - k)
        out[i] = e
    return out


def atr(h, l, c, n=14):
    tr = [None] * len(c)
    for i in range(1, len(c)):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    out = [None] * len(c)
    for i in range(n, len(c)):
        out[i] = sum(tr[i - n + 1:i + 1]) / n
    return out


def macd_hist(c):
    e12, e26 = ema(c, 12), ema(c, 26)
    m = [a - b for a, b in zip(e12, e26)]
    sig = ema(m, 9)
    return [a - b for a, b in zip(m, sig)]


def vol_avg(v, n=20):
    out = [None] * len(v)
    for i in range(n, len(v)):
        out[i] = sum(v[i - n:i]) / n
    return out


# ---------- signal ----------
def detect15(i, o, h, l, c, v, A, E, VA, HIST):
    if A[i] is None or E[i] is None or VA[i] is None:
        return None
    if c[i] < E[i] + 2 * A[i]:
        return None
    jmin = max(20, i - 16)
    j = None
    for k in range(i - 1, jmin - 1, -1):
        if A[k] and VA[k] and (c[k] - o[k]) >= 1.8 * A[k] and v[k] >= 2.5 * VA[k] and c[k] > o[k]:
            j = k
            break
    if j is None:
        return None
    hidx = max(range(j, i + 1), key=lambda k: h[k])
    if hidx >= i:
        return None
    high = h[hidx]
    low_leg = min(l[j:hidx + 1])
    leg = high - low_leg
    if leg <= 0:
        return None
    score = 0
    if v[i] < 0.5 * v[j]:
        score += 1
    for k in range(hidx + 1, i + 1):
        rng = h[k] - l[k]
        if rng > 0 and (h[k] - max(o[k], c[k])) >= 0.5 * rng:
            score += 1
            break
    if hidx > j and HIST[hidx] is not None and HIST[hidx] < max(HIST[j:hidx]):
        score += 1
    if score < 2:
        return None
    return dict(high=high, hidx=hidx, leg=leg, sl=high + 0.3 * A[i], atr=A[i])


def find_trigger(sig, t5, h5, l5, c5, HIST5, start5):
    """Return index of 5m trigger bar or None."""
    hi5 = bisect.bisect_right(t5, sig['t_high']) - 1
    if hi5 < 0:
        return None
    for k in range(max(start5, hi5 + 3), min(len(c5), start5 + 12)):
        if max(h5[hi5:k]) > sig['high']:
            return None  # high already exceeded
        swing_low = min(l5[hi5 + 1:k])
        if c5[k] < swing_low and HIST5[k] is not None and HIST5[k] < 0:
            return k
    return None


def simulate(sig, entry_idx, t5, h5, l5, c5):
    e = c5[entry_idx] * (1 - SLIP)
    sl = sig['sl']
    risk = (sl - e) / e
    if risk <= 0 or (sl - e) > 4 * sig['atr']:
        return None
    leg = sig['leg']
    tp1 = sig['high'] - 0.382 * leg
    tp2 = sig['high'] - 0.618 * leg
    if tp1 >= e:
        return None  # entry already below TP1: no clean target left
    parts = [1 / 3, 1 / 3, 1 / 3]
    pnl = 0.0
    tp1_hit = tp2_hit = False
    cur_sl = sl
    exit_k = None
    reason = 'timeout'
    end = min(len(c5), entry_idx + 1 + 48)
    still_open = False
    for k in range(entry_idx + 1, end):
        hi, lo, cl = h5[k], l5[k], c5[k]
        if cl > sig['high']:
            exit_k, reason = k, 'invalid'
            rem_exit = cl
            break
        if hi >= cur_sl:
            exit_k, reason = k, 'sl' if not tp1_hit else 'be'
            rem_exit = cur_sl
            break
        if not tp1_hit and lo <= tp1:
            pnl += parts[0] * (e - tp1) / e
            parts[0] = 0
            tp1_hit = True
            cur_sl = e
        if not tp2_hit and lo <= tp2:
            pnl += parts[1] * (e - tp2) / e
            parts[1] = 0
            tp2_hit = True
    else:
        exit_k = end - 1
        rem_exit = c5[exit_k]
        still_open = end < entry_idx + 49  # data ran out before max hold
    if exit_k is None:
        exit_k = end - 1
        rem_exit = c5[exit_k]
    # remaining size closes at rem_exit
    rem = sum(parts)
    pnl += rem * (e - rem_exit) / e
    pnl -= 2 * FEE  # fees on full notional, round trip
    R = pnl / risk
    return dict(entry=e, sl=sl, tp1=tp1, tp2=tp2, exit=rem_exit, reason=reason,
                bars=exit_k - entry_idx, pnl=pnl, R=R, tp1_hit=tp1_hit, tp2_hit=tp2_hit,
                exit_idx=exit_k, open=still_open, tp1_hit_flag=tp1_hit)


def run_symbol(sym, b15, b5):
    t15 = [x[0] for x in b15]
    o = [x[1] for x in b15]; h = [x[2] for x in b15]; l = [x[3] for x in b15]
    c = [x[4] for x in b15]; v = [x[5] for x in b15]
    A = atr(h, l, c); E = ema(c, 20); VA = vol_avg(v); HIST = macd_hist(c)

    t5 = [x[0] for x in b5]
    h5 = [x[2] for x in b5]; l5 = [x[3] for x in b5]; c5 = [x[4] for x in b5]
    HIST5 = macd_hist(c5)

    trades = []
    i = 96
    while i < len(b15) - 2:
        if c[i] / c[i - 96] - 1 < GAIN_MIN:
            i += 1
            continue
        sig = detect15(i, o, h, l, c, v, A, E, VA, HIST)
        if sig is None:
            i += 1
            continue
        sig['t_high'] = t15[sig['hidx']]
        start5 = bisect.bisect_left(t5, t15[i] + 900000)
        k = find_trigger(sig, t5, h5, l5, c5, HIST5, start5)
        if k is None:
            i += 1
            continue
        tr = simulate(sig, k, t5, h5, l5, c5)
        if tr is None:
            i += 1
            continue
        tr.update(sym=sym, signal_time=t15[i], entry_time=t5[k])
        trades.append(tr)
        # skip past the trade's exit on 15m
        exit_ts = t5[tr['exit_idx']]
        i = max(i + 1, bisect.bisect_left(t15, exit_ts))
    return trades


def stats(trades):
    if not trades:
        return dict(n=0)
    wins = [t for t in trades if t['pnl'] > 0]
    gross_w = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    gross_l = -sum(t['pnl'] for t in trades if t['pnl'] <= 0)
    cum, peak, mdd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda x: x['entry_time']):
        cum += t['pnl']
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return dict(n=len(trades), win_rate=round(100 * len(wins) / len(trades), 1),
                avg_R=round(sum(t['R'] for t in trades) / len(trades), 3),
                total_pct=round(100 * sum(t['pnl'] for t in trades), 2),
                profit_factor=round(gross_w / gross_l, 2) if gross_l else None,
                max_dd_pct=round(100 * mdd, 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--top', type=int, default=100)
    ap.add_argument('--out', default='results_exhaustion.json')
    a = ap.parse_args()

    syms = top_symbols(a.top)
    print('symbols:', len(syms))
    all_trades = []
    per = {}
    for n, s in enumerate(syms, 1):
        try:
            b15 = fetch_hist(s, '15m', a.days + 2)
            b5 = fetch_hist(s, '5m', a.days + 2)
            if len(b15) < 200 or len(b5) < 200:
                continue
            tr = run_symbol(s, b15, b5)
            all_trades += tr
            per[s] = stats(tr)
            print('[%d/%d] %s trades=%d' % (n, len(syms), s, len(tr)))
        except Exception as e:
            print('skip', s, e)
    result = dict(total=stats(all_trades), per_symbol=per, trades=all_trades)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print('TOTAL', json.dumps(result['total'], ensure_ascii=False))
    print('saved', a.out)


if __name__ == '__main__':
    main()

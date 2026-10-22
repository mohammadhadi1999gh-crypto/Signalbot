"""
Data collection for self-learning (no trading here).

features.jsonl : one row per FVG signal, written the moment a signal is emitted,
                 with market context (session, HTF trend, BTC trend, volatility...)
outcomes.jsonl : one row per closed position, joined to its feature row by key.

Join key: symbol|tf|side|round(entry, 8). Each position is written once.
"""
import datetime as dt
import json
import os

import numpy as np

import bot

FEAT_PATH = os.path.join(bot.HERE, 'features.jsonl')
OUT_PATH = os.path.join(bot.HERE, 'outcomes.jsonl')
DONE_PATH = os.path.join(bot.HERE, 'outcomes_done.json')


def _key(sym, tf, side, entry):
    return f'{sym}|{tf}|{side}|{round(float(entry), 8)}'


def session(hour_utc):
    if hour_utc < 7:
        return 'asia'
    if hour_utc < 13:
        return 'london'
    if hour_utc < 21:
        return 'newyork'
    return 'late'


def trend(sym, tf):
    """1 = EMA50 above EMA200 (up), -1 = below (down), 0 = not enough data."""
    try:
        L = bot.fetch_klines(sym, tf, 260)
        if len(L) < 210:
            return 0
        c = bot.prep(L)['c']
        s = np.asarray(c, dtype=float)
        def ema(x, n):
            a = 2 / (n + 1)
            e = x[0]
            for v in x[1:]:
                e = a * v + (1 - a) * e
            return e
        e50, e200 = ema(s[-200:], 50), ema(s[-200:], 200)
        return 1 if e50 > e200 else -1
    except Exception:  # noqa
        return 0


def _append(path, row):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')


def record_signal(sym, tf, P, t, sig, lb):
    """Called from fvg.analyze when a signal is produced."""
    try:
        ts = int(P['t'][t])
        hour = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).hour
        c = float(P['c'][t])
        row = {
            'key': _key(sym, tf, 'Short' if sig['side'] == -1 else 'Long', sig['entry']),
            'symbol': sym, 'tf': tf, 'side': 'short' if sig['side'] == -1 else 'long',
            'candle_ts': ts, 'hour_utc': hour,
            'weekday': dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).weekday(),
            'session': session(hour),
            'entry': float(sig['entry']), 'sl': float(sig['sl']),
            'tp1': float(sig['tps'][0]), 'tp2': float(sig['tps'][1]), 'tp3': float(sig['tps'][2]),
            'R_pct': round(float(sig['R']) / float(sig['entry']) * 100, 4),
            'atr_pct': round(float(sig['atr']) / c * 100, 4) if c else None,
            'vol_ratio': round(float(sig['vol_ratio']), 3),
            'rsi': round(float(sig['rsi']), 2),
            'range_pct': round(float(sig['pump_pct']) * 100, 3),
            'trend_tf': trend(sym, tf),
            'trend_4h': trend(sym, '4h'),
            'trend_1d': trend(sym, '1d'),
            'btc_trend_1h': trend('BTCUSDT', '1h'),
            'recorded_at': int(dt.datetime.now(dt.timezone.utc).timestamp()),
        }
        _append(FEAT_PATH, row)
    except Exception as e:  # noqa
        print('record_signal failed', e)


def _load_features():
    idx = {}
    if os.path.exists(FEAT_PATH):
        with open(FEAT_PATH, encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                    idx[r['key']] = r
                except Exception:  # noqa
                    pass
    return idx


def record_outcomes():
    done = bot.jload(DONE_PATH, [])
    done_set = set(done)
    feats = _load_features()
    pos = bot.jload(bot.POSITIONS_PATH, {})
    new = 0
    for pid, p in pos.items():
        if pid in done_set or p.get('result') not in ('win', 'loss', 'be'):
            continue
        side = 'Short' if p.get('side') == -1 else 'Long'
        k = _key(p.get('symbol'), p.get('tf'), side, p.get('entry', 0))
        f = feats.get(k, {})
        row = {
            'pid': pid, 'key': k, 'symbol': p.get('symbol'), 'tf': p.get('tf'),
            'side': 'short' if p.get('side') == -1 else 'long',
            'opened_at': p.get('opened_at'), 'closed_at': p.get('closed_at'),
            'result': p.get('result'), 'final_pct': p.get('final_pct'),
            'hit_tps': p.get('hit_tps'),
            'features': {kk: vv for kk, vv in f.items() if kk not in ('key', 'symbol', 'tf')},
        }
        _append(OUT_PATH, row)
        done.append(pid)
        new += 1
    bot.jsave(DONE_PATH, done)
    return new

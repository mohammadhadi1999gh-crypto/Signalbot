"""
Builds docs/data.json for the dashboard:
  - live monitor: open / pending positions with current price and distances
  - reports for today, yesterday, 7d, 30d, 90d, 365d, and all time
  - breakdowns: symbol, timeframe, side, hour (Tehran), weekday
  - setup suggestions: best symbol/timeframe/hour combinations with enough trades
Prices are fetched at each run (about every 5 minutes).
"""
import datetime as dt
import time

import bot

TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))
PERIODS = [('today', 0), ('yesterday', 1), ('7d', 7), ('30d', 30), ('90d', 90), ('365d', 365), ('all', -1)]
DAYS_FA = ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنج‌شنبه', 'جمعه', 'شنبه', 'یکشنبه']


def _ts(p):
    return p.get('closed_at') or p.get('opened_at') or 0


def _stats(rows):
    closed = [r for r in rows if r.get('result') in ('win', 'loss', 'be')]
    n = len(closed)
    if not n:
        return None
    wins = sum(1 for r in closed if r['result'] == 'win')
    pct = sum((r.get('final_pct') or 0) for r in closed)
    return {'n': n, 'wr': round(wins / n * 100, 1), 'pct': round(pct, 2)}


def _group(rows, fn):
    g = {}
    for r in rows:
        g.setdefault(fn(r), []).append(r)
    out = {}
    for k, v in g.items():
        s = _stats(v)
        if s:
            out[str(k)] = s
    return out


def _period_start(days, now):
    if days == -1:
        return 0
    if days == 0:
        t = dt.datetime.now(TEHRAN).replace(hour=0, minute=0, second=0, microsecond=0)
        return t.timestamp()
    if days == 1:
        t = dt.datetime.now(TEHRAN).replace(hour=0, minute=0, second=0, microsecond=0)
        return t.timestamp() - 86400
    return now - days * 86400


def _report(rows, days, now):
    start = _period_start(days, now)
    end = now - 0 if days != 1 else _period_start(0, now)
    sel = [r for r in rows if start <= _ts(r) < end + 1 and r.get('result') in ('win', 'loss', 'be')]
    tot = _stats(sel) or {'n': 0, 'wr': 0, 'pct': 0}
    return {
        'total': tot,
        'by_symbol': _group(sel, lambda r: str(r.get('symbol', '?')).replace('USDT', '')),
        'by_tf': _group(sel, lambda r: r.get('tf', '?')),
        'by_side': _group(sel, lambda r: 'Short' if r.get('side') == -1 else 'Long'),
        'by_hour': _group(sel, lambda r: dt.datetime.fromtimestamp(_ts(r), TEHRAN).hour),
        'by_weekday': _group(sel, lambda r: DAYS_FA[dt.datetime.fromtimestamp(_ts(r), TEHRAN).weekday()]),
    }


def _suggestions(rows):
    closed = [r for r in rows if r.get('result') in ('win', 'loss', 'be')]
    g = {}
    for r in closed:
        k = (str(r.get('symbol', '?')).replace('USDT', ''), r.get('tf', '?'),
             dt.datetime.fromtimestamp(_ts(r), TEHRAN).hour)
        g.setdefault(k, []).append(r)
    out = []
    for k, v in g.items():
        s = _stats(v)
        if s and s['n'] >= 5 and s['pct'] > 0:
            out.append({'symbol': k[0], 'tf': k[1], 'hour': k[2], **s})
    out.sort(key=lambda x: x['pct'], reverse=True)
    return out[:10]


def _live(positions, last_price_fn):
    live = []
    for pid, p in positions.items():
        if p.get('status') not in ('pending', 'open'):
            continue
        sym = p.get('symbol')
        px = None
        try:
            px = last_price_fn(sym)
        except Exception:  # noqa
            px = None
        entry = float(p.get('entry') or 0)
        sl = float(p.get('sl') or 0)
        tp1 = float((p.get('tps') or [0])[0] or 0)
        dist_entry = round((px - entry) / entry * 100, 2) if px and entry else None
        dist_sl = round((sl - px) / px * 100, 2) if px and sl else None
        live.append({
            'symbol': str(sym).replace('USDT', ''), 'tf': p.get('tf'),
            'status': p.get('status'), 'entry': entry, 'sl': sl, 'tp1': tp1,
            'price': px, 'dist_to_entry_pct': dist_entry, 'dist_to_sl_pct': dist_sl,
            'hit_tps': p.get('hit_tps'),
        })
    return live


def build(settings, enabled, last_price_fn):
    now = time.time()
    positions = bot.jload(bot.POSITIONS_PATH, {})
    rows = list(positions.values())
    reports = {name: _report(rows, days, now) for name, days in PERIODS}
    return {
        'generated_at': int(now),
        'enabled': bool(enabled),
        'trade_enabled': bool(settings.get('trade_enabled')),
        'settings': settings,
        'live': _live(positions, last_price_fn),
        'reports': reports,
        'suggestions': _suggestions(rows),
        'recent_closed': sorted(
            [{'symbol': str(r.get('symbol', '?')).replace('USDT', ''), 'tf': r.get('tf'),
              'side': 'Short' if r.get('side') == -1 else 'Long', 'result': r.get('result'),
              'final_pct': r.get('final_pct'), 'closed_at': r.get('closed_at')}
             for r in rows if r.get('result') in ('win', 'loss', 'be')],
            key=lambda x: x.get('closed_at') or 0, reverse=True)[:100],
    }

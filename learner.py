"""
Self-learning for the FVG strategy. Changes only filters, never the rules.

Runs after closed trades change. Needs at least MIN_TOTAL closed trades before
it changes anything. Every change is sent to Telegram and stored in learned.json.
Blocks expire after EXPIRE_DAYS so a bad streak cannot freeze a filter forever.

Filters it can set:
  - symbols: a symbol with enough trades, net loss and low win rate
  - tfs:     a timeframe with a clear net loss
  - hours:   an hour of day (Tehran) with a clear net loss
"""
import datetime as dt
import os
import time

import bot

LEARN_PATH = os.path.join(bot.HERE, 'learned.json')
TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))

MIN_TOTAL = 30
EXPIRE_DAYS = 14
MAX_BLOCKED_SYMBOLS = 15
RULES = {
    'symbols': {'min_n': 6, 'max_wr': 30.0},
    'tfs': {'min_n': 20},
    'hours': {'min_n': 15},
}


def _load():
    return bot.jload(LEARN_PATH, {'blocked': {'symbols': {}, 'tfs': {}, 'hours': {}}, 'last_n': 0, 'log': []})


def _save(d):
    bot.jsave(LEARN_PATH, d)


def _ts(r):
    return r.get('closed_at') or r.get('opened_at') or 0


def is_blocked(sym, tf, hour_tehran):
    d = _load()
    b = d.get('blocked', {})
    now = time.time()
    lim = EXPIRE_DAYS * 86400
    def live(bucket, key):
        t = b.get(bucket, {}).get(str(key))
        return t is not None and now - t < lim
    return (live('symbols', sym) or live('tfs', tf) or live('hours', hour_tehran))


def _closed():
    pos = bot.jload(bot.POSITIONS_PATH, {})
    return [p for p in pos.values() if p.get('result') in ('win', 'loss', 'be')]


def learn():
    rows = _closed()
    d = _load()
    if len(rows) < MIN_TOTAL or len(rows) == d.get('last_n', 0):
        return []
    now = time.time()
    changes = []
    b = d.setdefault('blocked', {'symbols': {}, 'tfs': {}, 'hours': {}})

    def stats(group):
        out = {}
        for k, L in group.items():
            n = len(L)
            wins = sum(1 for r in L if r['result'] == 'win')
            pct = sum(r.get('final_pct') or 0 for r in L)
            out[k] = {'n': n, 'wr': wins / n * 100, 'pct': pct}
        return out

    def group_by(fn):
        g = {}
        for r in rows:
            g.setdefault(fn(r), []).append(r)
        return stats(g)

    sym = group_by(lambda r: r.get('symbol', '?'))
    for k, v in sym.items():
        if v['n'] >= RULES['symbols']['min_n'] and v['wr'] < RULES['symbols']['max_wr'] and v['pct'] < 0:
            if str(k) not in b['symbols'] and len(b['symbols']) < MAX_BLOCKED_SYMBOLS:
                b['symbols'][str(k)] = now
                changes.append(f'بلاک ارز {k}: {v["n"]} معامله، وین {v["wr"]:.0f}٪، برایند {v["pct"]:+.1f}٪')

    tfs = group_by(lambda r: r.get('tf', '?'))
    for k, v in tfs.items():
        if v['n'] >= RULES['tfs']['min_n'] and v['pct'] < 0 and str(k) not in b['tfs']:
            b['tfs'][str(k)] = now
            changes.append(f'بلاک تایم‌فریم {k}: {v["n"]} معامله، برایند {v["pct"]:+.1f}٪')

    hours = group_by(lambda r: dt.datetime.fromtimestamp(_ts(r), TEHRAN).hour)
    for k, v in hours.items():
        if v['n'] >= RULES['hours']['min_n'] and v['pct'] < 0 and str(k) not in b['hours']:
            b['hours'][str(k)] = now
            changes.append(f'بلاک ساعت {k:02d}:00 تهران: {v["n"]} معامله، برایند {v["pct"]:+.1f}٪')

    d['last_n'] = len(rows)
    if changes:
        d['log'] = (d.get('log', []) + [{'t': int(now), 'c': changes}])[-50:]
    _save(d)
    return changes


def run_and_notify():
    try:
        changes = learn()
    except Exception as e:  # noqa
        print('learn failed', e)
        return
    if changes:
        msg = '🧠 یادگیری خودکار:\n' + '\n'.join('• ' + c for c in changes)
        for cid in bot.CFG.chat_ids:
            try:
                bot.tg_send(msg, cid)
            except Exception as e:  # noqa
                print('notify failed', e)

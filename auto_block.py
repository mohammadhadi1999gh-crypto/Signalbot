"""
Automatic symbol blocking from signals_log.jsonl.
  rule 1: more than 3 signals and no win (with at least one closed trade)
  rule 2: at least 3 closed trades and 60% or more closed by stop loss
Result is saved to blocked.json; fvg.py skips blocked symbols.
"""
import json
import os

import bot

PATH = os.path.join(bot.HERE, 'blocked.json')
MIN_SIGNALS = 4        # "more than 3"
SL_SHARE = 0.6
MIN_CLOSED_SL = 3


def _rows():
    rows = []
    try:
        with open(bot.SIGNALS_LOG_PATH, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except FileNotFoundError:
        pass
    return rows


def compute(rows):
    per = {}
    for r in rows:
        sym = r.get('symbol')
        if not sym:
            continue
        d = per.setdefault(sym, {'signals': 0, 'wins': 0, 'losses': 0, 'closed': 0, 'sl': 0})
        ev = r.get('event')
        if ev == 'new_signal':
            d['signals'] += 1
        elif ev == 'closed':
            d['closed'] += 1
            if r.get('result') == 'win':
                d['wins'] += 1
            elif r.get('result') == 'loss':
                d['losses'] += 1
            if r.get('reason') == 'sl':
                d['sl'] += 1
    return per


def update():
    per = compute(_rows())
    reasons = {}
    for sym, d in per.items():
        if d['signals'] >= MIN_SIGNALS and d['closed'] > 0 and d['wins'] == 0:
            reasons[sym] = f"بیش از ۳ سیگنال ({d['signals']}) و بدون برد"
        elif d['closed'] >= MIN_CLOSED_SL and d['sl'] / d['closed'] >= SL_SHARE:
            reasons[sym] = f"{d['sl']} از {d['closed']} معامله با حدضرر بسته شده"
    bot.jsave(PATH, {'symbols': sorted(reasons), 'reasons': reasons, 'stats': per})
    return reasons


def blocked_symbols():
    return set(bot.jload(PATH, {}).get('symbols', []))

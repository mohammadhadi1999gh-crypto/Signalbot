"""
Backtest of the FVG strategy on the candles the bot can fetch (about 400 per timeframe).

Simulation rules (conservative):
  - a signal appears on the close of candle t (no look-ahead)
  - pending limit sell at entry, waits up to WAIT candles
  - cancelled if price hits TP1 before the fill (same as the live bot)
  - after fill: if one candle touches both SL and TP1, SL is assumed first
  - result in R: win = +1R (TP1), loss = -1R (SL)
  - one open setup per symbol+timeframe at a time
Fees and slippage are NOT included. Results are for filtering, not for promises.
Output: backtest_results.json and a Telegram summary (if TELEGRAM_TOKEN is set).
"""
import datetime as dt
import json
import os

import bot
import fvg

WAIT = int(os.getenv('BT_WAIT', '50'))
MAX_SYMBOLS = int(os.getenv('BT_MAX_SYMBOLS', '60'))
TFS = tuple(x.strip() for x in os.getenv('FVG_TFS', '15m,30m,1h,2h,4h,6h,12h,1d').split(',') if x.strip())
TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))
OUT = os.path.join(bot.HERE, 'backtest_results.json')

fvg.install(bot)
detect = bot._fvg_detect


def simulate(P, lb, msl):
    o, h, l, c, t_ms = P['o'], P['h'], P['l'], P['c'], P['t']
    n = len(c)
    trades = []
    t = lb + 30
    open_until = -1
    while t < n - 2:
        if t <= open_until:
            t += 1
            continue
        sig = detect(P, t, lb, msl)
        if sig is None:
            t += 1
            continue
        entry, sl, tp1 = sig['entry'], sig['sl'], sig['tps'][0]
        filled, fill_i, result, end_i = False, None, None, None
        for j in range(t + 1, min(n, t + 1 + WAIT)):
            if not filled:
                if l[j] <= tp1:
                    result, end_i = 'cancel', j
                    break
                if h[j] >= entry:
                    filled, fill_i = True, j
                    if h[j] >= sl:
                        result, end_i = 'loss', j
                        break
                    if l[j] <= tp1:
                        result, end_i = 'win', j
                        break
            else:
                if h[j] >= sl:
                    result, end_i = 'loss', j
                    break
                if l[j] <= tp1:
                    result, end_i = 'win', j
                    break
        if result is None:
            result, end_i = ('expired' if not filled else 'open'), min(n - 1, t + WAIT)
        if filled and result in ('win', 'loss', 'open', 'expired'):
            trades.append({
                'entry_ts': int(t_ms[t]),
                'result': result,
                'R': 1.0 if result == 'win' else (-1.0 if result == 'loss' else 0.0),
                'risk_pct': float((sig['sl'] - entry) / entry * 100),
            })
        open_until = end_i if end_i is not None else t + 1
        t = open_until + 1
    return trades


def summarize(rows):
    closed = [r for r in rows if r['result'] in ('win', 'loss')]
    if not closed:
        return {'n': 0}
    wins = [r['R'] for r in closed if r['R'] > 0]
    losses = [-r['R'] for r in closed if r['R'] < 0]
    total = sum(r['R'] for r in closed)
    eq, peak, dd = 0.0, 0.0, 0.0
    for r in closed:
        eq += r['R']
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {
        'n': len(closed),
        'win_rate': round(len(wins) / len(closed) * 100, 1),
        'total_R': round(total, 2),
        'avg_R': round(total / len(closed), 3),
        'profit_factor': round(sum(wins) / sum(losses), 2) if losses else None,
        'max_drawdown_R': round(dd, 2),
    }


def group(rows, keyfn):
    g = {}
    for r in rows:
        g.setdefault(keyfn(r), []).append(r)
    out = {str(k): summarize(v) for k, v in g.items()}
    return {k: v for k, v in out.items() if v.get('n')}


def main():
    syms = [s for s in bot.get_scan_symbols() if s != bot.CFG.gold_symbol][:MAX_SYMBOLS]
    all_rows = []
    for tf in TFS:
        lb = bot.pump_lookback_for(tf)
        msl = bot.max_sl_pct_for(tf)
        for sym in syms:
            try:
                L = bot.fetch_klines(sym, tf, bot.LIVE_BARS)
                if len(L) < lb + 60:
                    continue
                P = bot.prep(L)
                for tr in simulate(P, lb, msl):
                    tr.update({'symbol': sym, 'tf': tf})
                    all_rows.append(tr)
            except Exception as e:  # noqa
                print('skip', sym, tf, e)

    for r in all_rows:
        ts = dt.datetime.fromtimestamp(r['entry_ts'] / 1000, TEHRAN)
        r['hour'] = ts.hour
        r['weekday'] = ts.weekday()

    res = {
        'generated_at': int(dt.datetime.now(dt.timezone.utc).timestamp()),
        'symbols': len(syms),
        'tfs': list(TFS),
        'overall': summarize(all_rows),
        'by_tf': group(all_rows, lambda r: r['tf']),
        'by_symbol': group(all_rows, lambda r: r['symbol']),
        'by_hour_tehran': group(all_rows, lambda r: r['hour']),
        'by_weekday': group(all_rows, lambda r: r['weekday']),
    }
    bot.jsave(OUT, res)

    o = res['overall']
    lines = ['🧪 بک‌تست FVG', f"معاملات: {o.get('n', 0)} | وین‌ریت: {o.get('win_rate', 0)}٪ | "
             f"مجموع R: {o.get('total_R', 0)} | PF: {o.get('profit_factor')} | افت: {o.get('max_drawdown_R', 0)}R"]
    if o.get('n'):
        best = sorted(res['by_symbol'].items(), key=lambda kv: kv[1]['total_R'], reverse=True)[:3]
        worst = sorted(res['by_symbol'].items(), key=lambda kv: kv[1]['total_R'])[:3]
        lines.append('بهترین ارزها: ' + ', '.join(f"{k} {v['total_R']}R" for k, v in best))
        lines.append('بدترین ارزها: ' + ', '.join(f"{k} {v['total_R']}R" for k, v in worst))
    text = '\n'.join(lines)
    print(text)
    token = os.getenv('TELEGRAM_TOKEN', '')
    for cid in bot.CFG.chat_ids if token else []:
        try:
            bot.tg_send(text, cid)
        except Exception as e:  # noqa
            print('telegram failed', e)


if __name__ == '__main__':
    main()

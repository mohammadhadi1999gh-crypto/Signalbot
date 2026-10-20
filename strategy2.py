import os, json, time, threading, datetime

TEH = datetime.timezone(datetime.timedelta(hours=3, minutes=30))

# Strategy 2 (signal-only):
# 1) 15m: liquidity sweep (wick beyond previous high/low, candle closes back inside)
# 2) 1m : CHoCH after the sweep (close breaks the last confirmed opposite pivot)
# 3) 1m : FVG formed BEFORE the CHoCH -> entry inside the FVG (default midpoint)
# 4) SL : beyond the impulse (middle) candle of that FVG
# 5) TP : nearest 15m pivot on the other side of entry
# Exchange orders are NOT placed by this module. Results go to strategy2.json.

def install(bot):
    DATA = os.getenv('DATA_DIR', '/data')
    PATH = os.path.join(DATA, 'strategy2.json')
    ENT = float(os.getenv('S2_ENTRY_PCT', '0.5'))
    SWN = int(os.getenv('S2_SWEEP_N', '20'))
    NSYM = int(os.getenv('S2_MAX_SYMBOLS', '40'))
    EXPIRE = float(os.getenv('S2_EXPIRE_H', '12')) * 3600
    PIV = 2
    lock = threading.Lock()
    try:
        recs = bot.jload(PATH, [])
    except Exception:
        recs = []

    def save():
        with lock:
            bot.jsave(PATH, recs[-5000:])

    def note(msg):
        print('strategy2:', msg.replace('\n', ' | '))
        for cid in bot.CFG.chat_ids:
            try:
                bot.tg_send(msg, cid)
            except Exception as e:
                print('strategy2: tg failed', e)

    def okx_1m(sym, n=300):
        inst = sym[:-4] + '-USDT-SWAP'
        r = bot.SESSION.get(bot.CFG.okx_base.rstrip('/') + '/api/v5/market/candles',
                            params={'instId': inst, 'bar': '1m', 'limit': n}, timeout=15)
        j = r.json()
        if j.get('code') != '0':
            raise RuntimeError(str(j.get('msg')))
        rows = [x for x in j['data'] if x[8] == '1']
        rows.sort(key=lambda x: int(x[0]))
        return [dict(t=int(x[0]) / 1000.0, o=float(x[1]), h=float(x[2]), l=float(x[3]), c=float(x[4])) for x in rows]

    def is_piv_low(arr, q):
        if q - PIV < 0 or q + PIV >= len(arr):
            return False
        return all(arr[q] < arr[j] for j in range(q - PIV, q + PIV + 1) if j != q)

    def is_piv_high(arr, q):
        if q - PIV < 0 or q + PIV >= len(arr):
            return False
        return all(arr[q] > arr[j] for j in range(q - PIV, q + PIV + 1) if j != q)

    def find_signal(sym):
        d = bot.fetch_klines(sym, '15m', 120)
        if len(d) < SWN + 5:
            return None
        H = list(d.high.values); L = list(d.low.values); C = list(d.close.values); T = list(d.time.values / 1000.0)
        k = len(d) - 1
        prev_hi = max(H[k - SWN:k]); prev_lo = min(L[k - SWN:k])
        side = None
        if H[k] > prev_hi and C[k] < prev_hi:
            side = -1
        elif L[k] < prev_lo and C[k] > prev_lo:
            side = 1
        if side is None:
            return None
        sweep_t = T[k]
        m = okx_1m(sym, 300)
        if not m:
            return None
        ml = [x['l'] for x in m]; mh = [x['h'] for x in m]; mc = [x['c'] for x in m]
        s_idx = next((i for i, x in enumerate(m) if x['t'] >= sweep_t), None)
        if s_idx is None:
            return None
        # CHoCH: close breaks the last confirmed opposite pivot formed after the sweep
        choch = None
        lvl = None
        for j in range(s_idx + PIV, len(m)):
            p = j - PIV
            if p >= s_idx + PIV:
                if side == -1 and is_piv_low(ml, p):
                    lvl = ml[p]
                if side == 1 and is_piv_high(mh, p):
                    lvl = mh[p]
            if lvl is not None:
                if (side == -1 and mc[j] < lvl) or (side == 1 and mc[j] > lvl):
                    choch = j
                    break
        if choch is None:
            return None
        # FVG formed before the CHoCH (last one before CHoCH)
        fvg = None
        for i in range(s_idx + 1, choch - 1):
            if side == -1 and ml[i + 1] > 0 and mh[i + 1] < ml[i - 1]:
                fvg = (i, ml[i - 1], mh[i + 1])
            if side == 1 and ml[i + 1] > mh[i - 1]:
                fvg = (i, ml[i + 1], mh[i - 1])
        if fvg is None:
            return None
        i, a, b = fvg
        hi, lo = max(a, b), min(a, b)
        entry = lo + ENT * (hi - lo)
        if side == -1:
            sl = mh[i] * 1.0005
        else:
            sl = ml[i] * 0.9995
        # TP: nearest 15m pivot on the other side of entry
        tp = None
        for q in range(len(d) - PIV - 1, PIV - 1, -1):
            if side == -1 and is_piv_low(L, q) and L[q] < entry:
                tp = L[q]
                break
            if side == 1 and is_piv_high(H, q) and H[q] > entry:
                tp = H[q]
                break
        if tp is None:
            return None
        last = mc[-1]
        if side == -1:
            valid = last < entry and sl > entry and tp < entry
        else:
            valid = last > entry and sl < entry and tp > entry
        if not valid:
            return None
        return dict(sym=sym, side=side, entry=entry, sl=sl, tp=tp, sweep_t=sweep_t, t_created=time.time(), status='pending', t_filled=None, t_closed=None, pct=None, key=sym + '|' + str(int(sweep_t)))

    def update(r):
        m = okx_1m(r['sym'], 300)
        bars = [x for x in m if x['t'] >= r['t_created']]
        for x in bars:
            if r['status'] == 'pending':
                if r['side'] == -1:
                    hit_sl = x['h'] >= r['sl']
                    hit_tp = x['l'] <= r['tp']
                    hit_en = x['h'] >= r['entry']
                else:
                    hit_sl = x['l'] <= r['sl']
                    hit_tp = x['h'] >= r['tp']
                    hit_en = x['l'] <= r['entry']
                if hit_sl or hit_tp:
                    if hit_en:
                        pass
                    r['status'] = 'cancel'
                    r['t_closed'] = x['t']
                    note('[S2] کنسل: ' + r['sym'])
                    return
                if hit_en:
                    r['status'] = 'open'
                    r['t_filled'] = x['t']
                    note('[S2] ورود فعال: ' + ('SHORT' if r['side'] == -1 else 'LONG') + ' #' + r['sym'].replace('USDT', '') + ' | ورود: ' + ('%.6g' % r['entry']))
            elif r['status'] == 'open' and x['t'] > r['t_filled']:
                if r['side'] == -1:
                    hit_sl = x['h'] >= r['sl']
                    hit_tp = x['l'] <= r['tp']
                else:
                    hit_sl = x['l'] <= r['sl']
                    hit_tp = x['h'] >= r['tp']
                if hit_sl:
                    r['status'] = 'loss'
                    r['t_closed'] = x['t']
                    r['pct'] = -abs(r['sl'] - r['entry']) / r['entry'] * 100
                    note('[S2] SL: #' + r['sym'].replace('USDT', '') + ' | ' + ('%.2f' % r['pct']) + '%')
                    return
                if hit_tp:
                    r['status'] = 'win'
                    r['t_closed'] = x['t']
                    r['pct'] = abs(r['entry'] - r['tp']) / r['entry'] * 100
                    note('[S2] TP: #' + r['sym'].replace('USDT', '') + ' | +' + ('%.2f' % r['pct']) + '%')
                    return
        if r['status'] == 'pending' and time.time() - r['t_created'] > EXPIRE:
            r['status'] = 'cancel'
            r['t_closed'] = time.time()
            note('[S2] منقضی: ' + r['sym'])

    REPORT_PATH = os.path.join(DATA, 'strategy2_reports.json')

    def report_text(title, label, items):
        scored = [r for r in items if r.get('status') in ('win', 'loss')]
        cancelled = [r for r in items if r.get('status') in ('cancel',)]
        L = ['[S2] ' + title, 'بازه: ' + label, '']
        if not scored:
            L.append('سیگنال فعال‌شده‌ای در این بازه نبود.')
            if cancelled:
                L.append('کنسل/منقضی: ' + str(len(cancelled)))
            return '\n'.join(L)
        wins = [r for r in scored if r['status'] == 'win']
        losses = [r for r in scored if r['status'] == 'loss']
        wr = len(wins) / len(scored) * 100
        tot = sum(r.get('pct') or 0 for r in scored)
        avg = tot / len(scored)
        best = max(scored, key=lambda r: r.get('pct') or 0)
        worst = min(scored, key=lambda r: r.get('pct') or 0)
        L.append('تعداد فعال‌شده: ' + str(len(scored)))
        L.append('وین‌ریت: %.0f%% (%d برد / %d باخت)' % (wr, len(wins), len(losses)))
        L.append('مجموع بازدهی (قیمت، بدون اهرم): %+.2f%%' % tot)
        L.append('میانگین هر سیگنال: %+.2f%%' % avg)
        L.append('بهترین: #%s (%+.2f%%)' % (best['sym'].replace('USDT', ''), best.get('pct') or 0))
        L.append('بدترین: #%s (%+.2f%%)' % (worst['sym'].replace('USDT', ''), worst.get('pct') or 0))
        if cancelled:
            L.append('کنسل/منقضی: %d (بدون تأثیر در وین‌ریت)' % len(cancelled))
        return '\n'.join(L)

    def send_reports():
        now = datetime.datetime.now(TEH)
        hm = now.hour * 60 + now.minute
        today = now.strftime('%Y-%m-%d')
        try:
            state = bot.jload(REPORT_PATH, {})
        except Exception:
            state = {}
        changed = False
        if hm >= 23 * 60 + 59 and state.get('daily') != today:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            with lock:
                items = [r for r in recs if (r.get('t_closed') or 0) >= start]
            note(report_text('گزارش روزانه استراتژی ۲', 'امروز', items))
            state['daily'] = today
            changed = True
        if now.weekday() == 4 and hm >= 10 * 60 and state.get('weekly') != today:
            start = (now - datetime.timedelta(days=7)).timestamp()
            with lock:
                items = [r for r in recs if (r.get('t_closed') or 0) >= start]
            note(report_text('گزارش هفتگی استراتژی ۲', '۷ روز گذشته', items))
            state['weekly'] = today
            changed = True
        if changed:
            bot.jsave(REPORT_PATH, state)

    def loop():
        time.sleep(120)
        seen = set(x.get('key') for x in recs)
        while True:
            try:
                syms = [s for s in bot.CFG.symbols if s != bot.CFG.gold_symbol][:NSYM]
                for sym in syms:
                    try:
                        sig = find_signal(sym)
                        if sig and sig['key'] not in seen:
                            seen.add(sig['key'])
                            with lock:
                                recs.append(sig)
                            save()
                            note('[S2] ' + ('SHORT' if sig['side'] == -1 else 'LONG') + ' #' + sym.replace('USDT', '') + ' | ورود: ' + ('%.6g' % sig['entry']) + ' | SL: ' + ('%.6g' % sig['sl']) + ' | TP: ' + ('%.6g' % sig['tp']))
                    except Exception as e:
                        print('strategy2: scan error', sym, e)
                with lock:
                    open_recs = [r for r in recs if r['status'] in ('pending', 'open')]
                for r in open_recs:
                    try:
                        update(r)
                    except Exception as e:
                        print('strategy2: update error', r['sym'], e)
                save()
                send_reports()
            except Exception as e:
                print('strategy2: loop error', e)
            time.sleep(60)
    threading.Thread(target=loop, daemon=True).start()
    print('strategy2: installed (signal-only, no exchange orders)')

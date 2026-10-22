"""
FVG strategy (short only), installed on top of bot.py.

Rules (as given by the user, with the definitions chosen here):
  1. Fresh FVG: bearish 3-candle gap (low of candle k-2 > high of candle k).
     The gap must not have been touched by any candle after it formed.
  2. Liquidity sweep BEFORE the gap: a confirmed swing high was taken
     (a wick above it) and the candle closed back below it.
  3. Market structure shift: the displacement candles close below the most
     recent confirmed swing low before the gap.
  Entry: limit sell at the middle of the gap (pending until price fills it).
  SL: above max(sweep wick high, top of gap) + SL_BUF_ATR * ATR.
  TP1 = entry - 1R, TP2 = entry - 2R, TP3 = min(MSS low, entry - 3R).
Signal only. This module never places exchange orders.
"""
import os
import time

import numpy as np


def install(bot):
    CFG = bot.CFG
    N = int(os.getenv('FVG_SWING_N', '3'))
    MAXAGE = int(os.getenv('FVG_MAX_AGE', '40'))
    DISP = float(os.getenv('FVG_DISP_ATR', '1.0'))
    SLB = float(os.getenv('FVG_SL_BUF_ATR', '0.2'))
    TFS = tuple(x.strip() for x in os.getenv('FVG_TFS', '15m,30m,1h,2h,4h,6h,12h,1d').split(',') if x.strip())
    DATA = os.getenv('DATA_DIR', bot.HERE)

    def piv(arr, a, b, high):
        out = []
        for i in range(max(a, N), b + 1):
            if i + N >= len(arr):
                break
            seg = arr[i - N:i + N + 1]
            if high:
                if arr[i] == seg.max() and int((seg >= arr[i]).sum()) == 1:
                    out.append(i)
            else:
                if arr[i] == seg.min() and int((seg <= arr[i]).sum()) == 1:
                    out.append(i)
        return out

    def detect_fvg(P, t, lb, msl):
        o, h, l, c, v, vs, atr, rsi = P['o'], P['h'], P['l'], P['c'], P['v'], P['vsma'], P['atr'], P['rsi']
        a = atr[t]
        w0 = t - lb
        if w0 < 0 or not a > 0:
            return None
        kmin = max(w0 + 2, t - MAXAGE)
        for k in range(t, kmin - 1, -1):
            g_lo = float(h[k])
            g_hi = float(l[k - 2])
            if not g_hi > g_lo:
                continue
            if not c[k - 1] < o[k - 1] or (h[k - 1] - l[k - 1]) < DISP * a:
                continue
            # freshness: no candle after the gap may have touched it
            if k < t and float(h[k + 1:t + 1].max()) >= g_lo:
                continue
            # price must still be below the gap (entry is a pending sell above price)
            if c[t] >= g_lo:
                continue
            # MSS: displacement closes below the last confirmed swing low before the gap
            lows = piv(l, w0 + N, k - 2 - N, False)
            if not lows:
                continue
            m = lows[-1]
            Lm = float(l[m])
            if not (c[k - 1] < Lm or c[k] < Lm):
                continue
            # liquidity sweep: a confirmed swing high wicked above, closed back below, before the gap
            highs = piv(h, w0 + N, k - 3 - N, True)
            best = None
            for j in reversed(highs):
                Hs = float(h[j])
                for s in range(j + 1, k - 2):
                    if h[s] > Hs and c[s] < Hs:
                        best = (j, s, Hs)
                        break
                if best:
                    break
            if not best:
                continue
            j, s, Hs = best
            entry = (g_lo + g_hi) / 2
            if entry <= c[t] + CFG.min_entry_gap_atr * a:
                continue
            sl = max(float(h[s]), g_hi) + SLB * a
            R = sl - entry
            if R <= 0 or R / entry > msl or R / entry < 0.001:
                continue
            tps = [entry - R, entry - 2 * R, min(Lm, entry - 3 * R)]
            vr = float(v[k - 1] / vs[k - 1]) if vs[k - 1] > 0 else 0.0
            comp = dict(trendline=2, pump=1, divergence=0,
                        reversal_candle=1 if bot.is_bearish_reversal(o, h, l, c, t) else 0,
                        volume_climax=0, breakout_volume=1 if vr >= 1.3 else 0,
                        red_candle=1 if c[t] < o[t] else 0)
            return dict(side=-1, entry=float(entry), sl=float(sl), tps=[float(x) for x in tps],
                        R=float(R), atr=float(a), cancel_price=float(tps[0]),
                        pump_low=Lm, pump_high=float(h[s]),
                        pump_pct=float((float(h[s]) - Lm) / Lm),
                        line_pts=[(int(j), Hs), (int(t), Hs)], slope=0.0,
                        rsi=float(rsi[t]), vol_ratio=vr, comp=comp,
                        score=sum(comp.values()), L=int(k),
                        max_score=len(comp) + 1, fvg=True)
        return None

    _orig_an = bot.analyze_symbol
    _orig_msg = bot.build_message

    def analyze(sym, tf):
        if sym == CFG.gold_symbol:
            return _orig_an(sym, tf)
        if tf not in TFS or bot.perf_gate_blocked(tf):
            return None
        lb = bot.pump_lookback_for(tf)
        L = bot.fetch_klines(sym, tf, bot.LIVE_BARS)
        if len(L) < lb + 30:
            return None
        P = bot.prep(L)
        t = len(L) - 1
        tc = int(P['t'][t]) + bot.TF_MS[tf]
        if time.time() * 1000 - tc > CFG.max_age_min * 60000:
            return None
        sig = detect_fvg(P, t, lb, bot.max_sl_pct_for(tf))
        if sig is None:
            return None
        if sym != 'BTCUSDT' and bot.btc_blocks(-1):
            return None
        px = bot.last_price(sym)
        if px and px >= sig['entry']:
            return None
        info = {'candle_time': bot.datetime.fromtimestamp(tc / 1000, bot.timezone.utc).strftime('%H:%M')}
        return sig, info, bot.build_message(sym, tf, sig, info), int(P['t'][t]), dict(P=P, t=t)

    def build_message(sym, tf, sig, info):
        m = _orig_msg(sym, tf, sig, info)
        if not sig.get('fvg'):
            return m
        return m.replace('Trendline Break Short (Pump Fade) + Pending Retest + ضدترند',
                         'FVG Short (Sweep + MSS) + Pending Retest')

    bot.analyze_symbol = analyze
    bot.build_message = build_message
    # the FVG rules are the gate; the old score threshold does not apply
    CFG.min_score = 0
    print('strategy installed: fvg')

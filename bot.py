#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strategy_v2.py  -  لایه‌ی اصلاح استراتژی برای bot.py (بدون تغییر در bot.py)

کنار bot.py بگذارید و به‌جای bot.py اجرا کنید:
    python strategy_v2.py warmup      # بک‌تست با کارمزد + مدیریت معامله (حداقل یک بار)
    python strategy_v2.py once --dry  # یک اسکن آزمایشی
    python strategy_v2.py run         # اجرای دائمی

تغییرات نسبت به نسخه‌ی قبل:
  - ورود تک‌پله (حذف میانگین‌گیری تا نزدیکی SL)
  - SL = بیشتر از (پشت FVG - 0.5 ATR) و (SL_ATR × ATR)، حداقل فاصله MIN_R_PCT
  - مدیریت معامله: 50% در TP1، SL روی ورود، 30% در TP2، 20% در TP3
  - بک‌تست با کارمزد و اسلیپیج و خروجی «میانگین R» (نه فقط وین‌ریت)
  - فیلترها: ADX، هم‌جهتی ۲ تایم‌فریم بالاتر، روند BTC، EMA50/200 تایم‌فریم سیگنال،
    حجم کندل شتاب اجباری، کیفیت کندل تایید، RSI، حذف سشن کم‌نقدینگی
  - محدودیت سیگنال هم‌جهت همزمان + یک سیگنال در هر ارز
  - کلید قطع: اگر میانگین R بک‌تست منفی شد، سیگنال ارسال نمی‌شود
"""
import os, sys, time, logging
import numpy as np
import pandas as pd

import bot

log = logging.getLogger("v2")

# ---------------------------------------------------------------- پارامترها (از .env قابل تغییر)
FEE = float(os.getenv("FEE_PCT", "0.05")) / 100        # کارمزد هر طرف
SLIP = float(os.getenv("SLIP_PCT", "0.02")) / 100      # اسلیپیج هر طرف
TP1_R = float(os.getenv("TP1_R", "1.0"))
SL_ATR = float(os.getenv("SL_ATR", "1.2"))
MIN_ADX = float(os.getenv("MIN_ADX", "22"))
MIN_R_PCT = float(os.getenv("MIN_R_PCT", "0.005"))     # حداقل فاصله SL (0.5%)
STRICT_T2 = os.getenv("STRICT_T2", "1") == "1"
BTC_FILTER = os.getenv("BTC_FILTER", "1") == "1"
MAX_SAME = int(os.getenv("MAX_SAME_SIDE", "3"))        # حداکثر سیگنال هم‌جهت در ۴ ساعت
KILL_N = int(os.getenv("KILL_MIN_TRADES", "30"))
W = (0.5, 0.3, 0.2)                                    # سهم بستن در TP1/TP2/TP3

CURRENT = {"sym": None}
_BTC = {}


# ---------------------------------------------------------------- ابزارها
def _tf_of(P):
    if "_tf" not in P:
        d = int(np.median(np.diff(P["t"][-50:])))
        P["_tf"] = next((k for k, v in bot.TF_MS.items() if v == d), None)
    return P["_tf"]


def _ind(P):
    if "adx" in P:
        return
    h, l, c = (pd.Series(P[k]) for k in ("h", "l", "c"))
    up, dn = h.diff(), -l.diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0))
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0))
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a = 1 / 14
    atr = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=a, adjust=False).mean() / atr
    mdi = 100 * minus.ewm(alpha=a, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    P["adx"] = dx.ewm(alpha=a, adjust=False).mean().fillna(0).values
    P["e50"] = c.ewm(span=50, adjust=False).mean().values
    P["e200"] = c.ewm(span=200, adjust=False).mean().values


def _btc_ok(side, tf, tc):
    """روند BTC نباید مخالف سیگنال باشد (برای خود BTC غیرفعال است)."""
    if not BTC_FILTER or (CURRENT["sym"] or "").startswith("BTC"):
        return True
    htf = bot.MTF_MAP[tf][0]
    e = _BTC.get(htf)
    if e is None or time.time() - e[0] > 1800:
        try:
            pack = bot.htf_pack(bot.fetch_long("BTCUSDT", htf, 1500), htf)
        except Exception as ex:  # noqa
            log.warning("btc fetch failed: %s", ex)
            return False  # محتاطانه: بدون داده BTC سیگنال نده
        e = _BTC[htf] = (time.time(), pack)
    return bot.trend_at(e[1], tc) != -side


# ---------------------------------------------------------------- تشخیص ستاپ (نسخه ۲)
def detect(P, t, t1, t2, extra=()):
    C = bot.CFG
    if t < 205 or t1 == 0:
        return None
    _ind(P)
    a = P["atr"][t]
    o, h, l, c, v, vs = P["o"], P["h"], P["l"], P["c"], P["v"], P["vsma"]
    if not a > 0 or not (0.0012 <= a / c[t] <= 0.03):
        return None
    if P["adx"][t] < MIN_ADX:
        return None
    tf = _tf_of(P)
    if tf is None:
        return None
    tc = int(P["t"][t]) + bot.TF_MS[tf]
    if bot.session_of(tc)[0] == "Off":
        return None

    side = t1
    if STRICT_T2 and t2 != side:
        return None
    if not STRICT_T2 and t2 == -side:
        return None
    e50, e200, rsi = P["e50"][t], P["e200"][t], P["rsi"][t]
    if side == 1 and not (c[t] > e50 > e200 and 40 <= rsi <= 65):
        return None
    if side == -1 and not (c[t] < e50 < e200 and 35 <= rsi <= 60):
        return None
    rng = h[t] - l[t]
    if rng <= 0 or abs(c[t] - o[t]) / rng < 0.5:
        return None
    if side == 1 and (c[t] - l[t]) / rng < 0.65:
        return None
    if side == -1 and (h[t] - c[t]) / rng < 0.65:
        return None
    if not _btc_ok(side, tf, tc):
        return None

    min_fvg = max(C.min_fvg_atr, 0.35)
    for i in range(t - 1, max(2, t - C.fvg_lookback) - 1, -1):
        if side == 1 and l[i] > h[i - 2]:
            bot_, top = h[i - 2], l[i]
        elif side == -1 and h[i] < l[i - 2]:
            bot_, top = h[i], l[i - 2]
        else:
            continue
        if top - bot_ < min_fvg * a:
            continue
        imp = v[i - 1] / vs[i - 1] if vs[i - 1] > 0 else 0
        if imp < 1.5:            # کندل شتاب باید حجم واقعی داشته باشد
            continue
        mid = (bot_ + top) / 2
        if side == 1:
            if i + 1 < t and c[i + 1:t].min() < bot_:
                continue
            if (l[i + 1:t] <= top).sum() > 1:
                continue
            if not (l[t] <= top and c[t] > o[t] and c[t] > mid and l[t] >= bot_ - 0.5 * a):
                continue
        else:
            if i + 1 < t and c[i + 1:t].max() > top:
                continue
            if (h[i + 1:t] >= bot_).sum() > 1:
                continue
            if not (h[t] >= bot_ and c[t] < o[t] and c[t] < mid and h[t] <= top + 0.5 * a):
                continue

        # ورود تک‌پله + SL دورتر از نویز
        e1 = float(c[t])
        far = bot_ if side == 1 else top
        struct_sl = far - side * 0.5 * a
        atr_sl = e1 - side * SL_ATR * a
        sl = min(struct_sl, atr_sl) if side == 1 else max(struct_sl, atr_sl)
        R = abs(e1 - sl)
        if R / e1 > C.max_sl_pct or R / e1 < MIN_R_PCT:
            continue
        tps = [e1 + side * k * R for k in (TP1_R, 2.0, 3.0)]

        w0 = max(0, t - 299)
        piv = bot.pivot_levels(h[w0:t + 1], l[w0:t + 1], a) + [float(x) for x in extra]
        opp = [p for p in piv if (p > e1 if side == 1 else p < e1)]
        if opp:
            nearest = min(opp) if side == 1 else max(opp)
            if abs(nearest - e1) < TP1_R * R:   # جا برای TP1 نیست
                continue
        allv = piv + bot.round_levels(e1)
        conf = any(bot_ - 0.5 * a <= p <= top + 0.5 * a for p in allv)
        sup = [p for p in piv if p < e1]
        res = [p for p in piv if p > e1]
        trg = v[t] / vs[t] if vs[t] > 0 else 0
        comp = dict(fvg=2, t1=1, t2=1 if t2 == side else 0, vol_imp=1,
                    vol_trg=1 if trg >= 1.0 else 0, level=1 if conf else 0)
        return dict(
            side=side, zone=(float(bot_), float(top)), entries=[e1, e1, e1],
            sl=float(sl), tps=[float(x) for x in tps], R=float(R), atr=float(a),
            base=sum(comp.values()), comp=comp, t1=t1, t2=t2, rsi=float(rsi),
            imp_vol=float(imp), trg_vol=float(trg), adx=float(P["adx"][t]),
            support=max(sup) if sup else None, resistance=min(res) if res else None,
            round_lv=[float(x) for x in bot.round_levels(e1) if abs(x - e1) / e1 < 0.03][:3],
            fvg_idx=int(i), levels=[float(x) for x in piv],
        )
    return None


# ---------------------------------------------------------------- شبیه‌ساز با مدیریت معامله
def simulate_managed(P, t, sig, max_bars):
    """50% در TP1 -> SL روی ورود -> 30% TP2 -> 20% TP3. اگر SL و TP در یک کندل بود = SL.
    خروجی: (تعداد TP لمس‌شده، R خالص بعد از کارمزد) یا None برای معاملات بی‌نتیجه."""
    h, l, c = P["h"], P["l"], P["c"]
    side, e1, R = sig["side"], sig["entries"][0], sig["R"]
    sl, tps = sig["sl"], sig["tps"]
    cost = 2 * (FEE + SLIP) * e1 / R          # هزینه رفت‌وبرگشت بر حسب R
    total, remaining, reached = 0.0, 1.0, 0
    end = min(len(h), t + 1 + int(max_bars))
    for k in range(t + 1, end):
        if (l[k] <= sl) if side == 1 else (h[k] >= sl):
            total += remaining * (sl - e1) * side / R
            return reached, total - cost
        while reached < 3 and ((h[k] >= tps[reached]) if side == 1 else (l[k] <= tps[reached])):
            total += W[reached] * (tps[reached] - e1) * side / R
            remaining -= W[reached]
            reached += 1
            if reached == 1:
                sl = e1                        # بریک‌ایون بعد از TP1
        if reached == 3:
            return 3, total - cost
    if end - 1 <= t:
        return None
    total += remaining * (c[end - 1] - e1) * side / R   # پایان زمان: خروج در قیمت
    net = total - cost
    if reached == 0 and net >= 0:
        return None
    return reached, net


def backtest(sym, tf):
    CURRENT["sym"] = sym
    C = bot.CFG
    h1, h2 = bot.MTF_MAP[tf]
    L = bot.fetch_long(sym, tf, 3000)
    H1 = bot.htf_pack(bot.fetch_long(sym, h1, 3000), h1)
    H2 = bot.htf_pack(bot.fetch_long(sym, h2, 1000), h2)
    P = bot.prep(L)
    trades, last = [], -999
    for t in range(250, len(L) - 1):
        if t - last < C.cooldown_candles:
            continue
        tc = int(P["t"][t]) + bot.TF_MS[tf]
        t1, t2 = bot.trend_at(H1, tc), bot.trend_at(H2, tc)
        if t1 == 0:
            continue
        sig = detect(P, t, t1, t2)
        if not sig or sig["base"] < C.base_min:
            continue
        last = t
        res = simulate_managed(P, t, sig, C.max_bars)
        if res is None:
            continue
        trades.append([tc, bot.session_of(tc)[0], res[0], sig["side"], round(float(res[1]), 3)])
    return trades


# ---------------------------------------------------------------- پیام تلگرام (ورود تک‌پله)
def _avg_r(tf):
    r = [x[4] for x in bot.pooled(tf) if len(x) > 4]
    return (float(np.mean(r)), len(r)) if r else (None, 0)


def build_message(sym, tf, sig, info):
    C, fp = bot.CFG, bot.fp
    long_ = sig["side"] == 1
    base = sym[:-4] if sym.endswith("USDT") else sym
    e1 = sig["entries"][0]
    lev = str(C.leverage).translate(bot.FA_DIGITS)
    h1, h2 = bot.MTF_MAP[tf]
    L = [f"⭕️#{base}/ USDT {'📈' if long_ else '📉'}💰", ""]
    L.append("🟢Cross (Long) 📈" if long_ else "🔴Cross (Short) 📉")
    L.append(f"⏱ تایم‌فریم: {tf} | {C.label} Futures")
    if info.get("candle_time"):
        L.append(f"🕒 کندل تایید: {info['candle_time']} UTC")
    L += ["", f"✅Entry : {fp(e1)}  (فقط نزدیک این قیمت؛ اگر دور شد وارد نشوید)", ""]
    icons = ["🎯", "🚀", "💸"]
    for i, w in enumerate(("۵۰٪", "۳۰٪", "۲۰٪")):
        L.append(f"Tp {i + 1} : {bot.roi(sig['tps'][i], e1)}% {icons[i]} ({fp(sig['tps'][i])}) - بستن {w}")
    L += ["", f"Stop Loss : {fp(sig['sl'])} ⛔️ (ریسک {sig['R'] / e1 * 100:.2f}% قیمت)", ""]
    L.append("♻️ بعد از TP1: نیمی از حجم را ببند و SL را روی نقطه ورود بگذار")
    L.append(f"⚖️ حجم را طوری بگیر که رسیدن به SL حداکثر نیم تا یک درصد کل سرمایه ضرر باشد (اهرم {lev}×)")
    L += ["", f"با تایید ریتست FVG {'صعودی' if long_ else 'نزولی'} ({fp(sig['zone'][0])} - {fp(sig['zone'][1])})", ""]
    L.append("📊 تحلیل:")
    L.append(f"• روند: {h1} {bot.trend_txt(sig['t1'])} | {h2} {bot.trend_txt(sig['t2'])}")
    L.append(f"• حجم: کندل شتاب {sig['imp_vol']:.1f}× | کندل تایید {sig['trg_vol']:.1f}×")
    lv = []
    if sig["support"]:
        lv.append(f"حمایت {fp(sig['support'])}")
    if sig["resistance"]:
        lv.append(f"مقاومت {fp(sig['resistance'])}")
    if lv:
        L.append("• " + " | ".join(lv))
    L.append(f"• RSI: {sig['rsi']:.0f} | ADX: {sig['adx']:.0f}" + (f" | TradingView: {info['tv']}" if info.get("tv") else ""))
    L.append(f"• سشن: {info['session_fa']}")
    if info.get("wr") is not None:
        L.append(f"🎯 بک‌تست {tf} (با کارمزد): TP1 در {info['wr'] * 100:.0f}% از {info['wr_n']} معامله ({info['wr_src']})")
    avg, n = _avg_r(tf)
    if avg is not None:
        L.append(f"📈 میانگین نتیجه هر معامله: {avg:+.2f}R (از {n} معامله)")
    L += [f"• امتیاز سیگنال: {info['score']}/9", "🧠 FVG Retest + MTF + BTC Filter + ADX + Volume", "",
          "Ai Agent🤖", "", C.handle, "", "فعال شده✅✅✅"]
    return "\n".join(L)


# ---------------------------------------------------------------- کنترل ریسک زنده
def _crowded(sym, tf, side):
    sent = bot.jload(bot.STATE_PATH, {"sent": {}}).get("sent", {})
    now, same = time.time() * 1000, 0
    for k, ts in sent.items():
        try:
            s, f, sd = k.split("|")
            sd = int(sd)
        except Exception:  # noqa
            continue
        if now - ts > 4 * 3600_000 or sd != side:
            continue
        same += 1
        if s == sym and f != tf:
            return True
    return same >= MAX_SAME


_orig_analyze = bot.analyze_symbol


def analyze_symbol(sym, tf):
    CURRENT["sym"] = sym
    res = _orig_analyze(sym, tf)
    if not res:
        return None
    r = [x[4] for x in bot.pooled(tf) if len(x) > 4]
    if len(r) >= KILL_N and float(np.mean(r)) <= 0:
        log.info("kill-switch: %s expectancy<=0 (%d trades) -> skip %s", tf, len(r), sym)
        return None
    if _crowded(sym, tf, res[0]["side"]):
        log.info("skip %s %s: too many same-side signals", sym, tf)
        return None
    return res


# ---------------------------------------------------------------- گزارش و اجرا
def warmup():
    for tf in bot.SIGNAL_TFS:
        allr = []
        for sym in bot.CFG.symbols:
            try:
                tr = bot.get_trades(sym, tf, force=True)
                wr, n = bot.winrate(tr)
                r = [x[4] for x in tr]
                allr += r
                print(f"{sym:10s} {tf:4s} n={n:3d} TP1={'-' if wr is None else f'{wr * 100:.0f}%'} "
                      f"avgR={np.mean(r) if r else 0:+.2f}")
            except Exception as e:  # noqa
                print(f"{sym} {tf} خطا: {e}")
        if allr:
            a = np.array(allr)
            gp, gl = a[a > 0].sum(), -a[a < 0].sum()
            print(f"\n=== {tf} مجموع: n={len(a)} | برد={np.mean(a > 0) * 100:.0f}% | "
                  f"میانگین R={a.mean():+.3f} | profit factor={gp / gl if gl else float('inf'):.2f}\n")


def install():
    bot.STATS_PATH = os.path.join(bot.HERE, "stats_v2.json")   # جدا از آمار قدیمی
    bot.detect = detect
    bot.backtest = backtest
    bot.build_message = build_message
    bot.analyze_symbol = analyze_symbol


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    install()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    dry = "--dry" in sys.argv
    if cmd == "run":
        bot.run(dry)
    elif cmd == "once":
        for tf in bot.SIGNAL_TFS:
            bot.scan_tf(tf, dry)
    elif cmd == "warmup":
        warmup()
    else:
        print("دستورها: run | once | warmup   (اضافه کردن --dry = فقط چاپ)\n"
              "setup / check / test-telegram را مثل قبل با bot.py اجرا کنید.")


if __name__ == "__main__":
    main()

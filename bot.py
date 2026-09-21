#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
4ux AI Signal Bot
-----------------
اسکن FVG + روند مولتی‌تایم‌فریم + حجم + حمایت/مقاومت + سشن‌ها
سیگنال ورود روی تایم‌فریم 15 دقیقه و 1 ساعت -> تلگرام

دستورات:
    python bot.py run            # اجرای دائمی
    python bot.py once           # یک بار اسکن هر دو تایم‌فریم
    python bot.py warmup         # ساخت آمار بک‌تست (وین ریت + سشن‌ها)
    python bot.py sessions       # نمایش سشن‌های سودده
    python bot.py check          # تست اتصال صرافی + تلگرام
    python bot.py setup          # ویزارد تنظیم توکن و chat id (خودکار)
    python bot.py install-service # اجرای دائمی و خودکار روی سرور لینوکس
    python bot.py test-telegram  # ارسال پیام تست
    اضافه کردن --dry یعنی ارسال نکن، فقط چاپ کن
"""
import os, sys, json, math, time, logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------------------
# تنظیمات
# ----------------------------------------------------------------------------
def load_env(path=os.path.join(HERE, ".env")):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


def _f(name, default):
    return type(default)(os.getenv(name, default))


class CFG:
    token = os.getenv("TELEGRAM_TOKEN", "")
    chat_ids = [x.strip() for x in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if x.strip()]
    send_chart = os.getenv("SEND_CHART", "1") == "1"
    exchange = os.getenv("EXCHANGE", "okx").lower()           # okx (سازگار با GitHub) | binance
    okx_base = os.getenv("OKX_BASE", "https://www.okx.com")
    scan_back = _f("SCAN_BACK", 1)                             # علاوه بر آخرین کندل، چند کندل قبلی هم بررسی شود
    max_age_min = _f("MAX_AGE_MIN", 25)                        # حداکثر تاخیر مجاز سیگنال (دقیقه)
    futures_only = os.getenv("FUTURES_ONLY", "1") == "1"      # فقط دیتای Binance USDT-M Futures
    fapi_base = os.getenv("BINANCE_FAPI_BASE", "https://fapi.binance.com")
    handle = os.getenv("CHANNEL_HANDLE", "@ai4uxx")
    symbols = [s.strip().upper() for s in os.getenv(
        "SYMBOLS",
        "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,DOTUSDT,"
        "LTCUSDT,ATOMUSDT,NEARUSDT,APTUSDT,ARBUSDT,OPUSDT,SUIUSDT,INJUSDT,TRXUSDT,TONUSDT"
    ).split(",") if s.strip()]
    leverage = _f("LEVERAGE", 20)
    max_sl_pct = _f("MAX_SL_PCT", 2.0) / 100     # حداکثر فاصله SL از ورود (قیمت)
    min_fvg_atr = _f("MIN_FVG_ATR", 0.25)         # حداقل اندازه FVG نسبت به ATR
    fvg_lookback = _f("FVG_LOOKBACK", 50)         # تعداد کندل برای جستجوی FVG
    base_min = _f("BASE_MIN_SCORE", 5)            # حداقل امتیاز پایه (از 7)
    min_score = _f("MIN_SCORE", 6)                # حداقل امتیاز نهایی (از 9)
    max_bars = _f("BACKTEST_MAX_BARS", 48)        # حداکثر کندل انتظار برای نتیجه معامله
    use_tv = os.getenv("USE_TRADINGVIEW_TA", "1") == "1"
    cooldown_candles = _f("COOLDOWN_CANDLES", 6)
    stats_ttl_h = _f("STATS_TTL_HOURS", 12)
    label = "OKX" if os.getenv("EXCHANGE", "okx").lower() == "okx" else "Binance"


TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
MTF_MAP = {"15m": ("1h", "4h"), "1h": ("4h", "1d")}   # تایم‌فریم‌های بالاتر برای تایید روند
SIGNAL_TFS = ("15m", "1h")

log = logging.getLogger("bot")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 signal-bot"})

# ----------------------------------------------------------------------------
# دریافت کندل‌ها
# ----------------------------------------------------------------------------
ENDPOINTS = [CFG.fapi_base.rstrip("/") + "/fapi/v1/klines"]   # فقط برای EXCHANGE=binance
if not CFG.futures_only:   # فقط اگر فیوچرز در دسترس نبود (قیمت اسپات کمی متفاوت است)
    ENDPOINTS += ["https://api.binance.com/api/v3/klines",
                  "https://data-api.binance.vision/api/v3/klines"]


OKX_BAR = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}


def _okx_klines(symbol, tf, limit, end=None):
    """کندل‌های بسته‌شده‌ی قرارداد Perpetual در OKX (بدون محدودیت منطقه‌ای GitHub)."""
    inst = symbol[:-4] + "-USDT-SWAP" if symbol.endswith("USDT") else symbol
    rows, after = [], (int(end) + 1 if end else None)
    while len(rows) < limit:
        params = {"instId": inst, "bar": OKX_BAR[tf], "limit": min(300, limit - len(rows))}
        if after:
            params["after"] = after
        r = SESSION.get(CFG.okx_base.rstrip("/") + "/api/v5/market/candles", params=params, timeout=15)
        r.raise_for_status()
        j = r.json()
        if j.get("code") != "0":
            raise RuntimeError(f"okx {inst}: {j.get('msg')}")
        data = j["data"]
        if not data:
            break
        rows += data
        after = int(data[-1][0])
        if len(data) < params["limit"]:
            break
        time.sleep(0.12)
    if not rows:
        raise RuntimeError(f"okx: no data for {inst}")
    df = pd.DataFrame(rows)
    df = df[df[8] == "1"].iloc[:, :6]                      # فقط کندل‌های بسته
    df.columns = ["time", "open", "high", "low", "close", "volume"]
    df = df.astype(float)
    df["time"] = df["time"].astype("int64")
    return df.sort_values("time").reset_index(drop=True)


def last_price(symbol):
    try:
        if CFG.exchange == "okx":
            inst = symbol[:-4] + "-USDT-SWAP"
            r = SESSION.get(CFG.okx_base.rstrip("/") + "/api/v5/market/ticker", params={"instId": inst}, timeout=10)
            return float(r.json()["data"][0]["last"])
        r = SESSION.get(CFG.fapi_base.rstrip("/") + "/fapi/v1/ticker/price", params={"symbol": symbol}, timeout=10)
        return float(r.json()["price"])
    except Exception:  # noqa
        return None


def fetch_klines(symbol, tf, limit=500, end=None):
    """فقط کندل‌های بسته‌شده را برمی‌گرداند."""
    if CFG.exchange == "okx":
        return _okx_klines(symbol, tf, limit, end)
    params = {"symbol": symbol, "interval": tf, "limit": min(limit, 1000)}
    if end:
        params["endTime"] = int(end)
    err = None
    for url in ENDPOINTS:
        try:
            r = SESSION.get(url, params=params, timeout=15)
            r.raise_for_status()
            rows = r.json()
            df = pd.DataFrame(rows).iloc[:, :6]
            df.columns = ["time", "open", "high", "low", "close", "volume"]
            df = df.astype(float)
            df["time"] = df["time"].astype("int64")
            if len(df) and df.time.iloc[-1] + TF_MS[tf] > time.time() * 1000:
                df = df.iloc[:-1]
            return df.reset_index(drop=True)
        except Exception as e:  # noqa
            err = e
    raise RuntimeError(f"fetch failed {symbol} {tf}: {err}")


def fetch_long(symbol, tf, total):
    frames, end, got = [], None, 0
    while got < total:
        want = min(1000, total - got)
        df = fetch_klines(symbol, tf, want, end)
        if df.empty:
            break
        frames.append(df)
        got += len(df)
        end = int(df.time.iloc[0]) - 1
        if len(df) < want:
            break
        time.sleep(0.1)
    out = pd.concat(frames).drop_duplicates("time").sort_values("time")
    return out.reset_index(drop=True)


# ----------------------------------------------------------------------------
# اندیکاتورها
# ----------------------------------------------------------------------------
def prep(df):
    c = df.close
    tr = pd.concat([df.high - df.low, (df.high - c.shift()).abs(),
                    (df.low - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return dict(
        t=df.time.values, o=df.open.values, h=df.high.values, l=df.low.values,
        c=c.values, v=df.volume.values, atr=atr.values,
        rsi=rsi.fillna(50).values, vsma=df.volume.rolling(20).mean().values,
    )


def htf_pack(df, tf):
    c = df.close
    e50 = c.ewm(span=50, adjust=False).mean()
    e200 = c.ewm(span=200, adjust=False).mean()
    trend = np.where((c > e50) & (e50 > e200), 1, np.where((c < e50) & (e50 < e200), -1, 0))
    return dict(tc=df.time.values + TF_MS[tf], trend=trend, df=df)


def trend_at(H, t_close):
    i = int(np.searchsorted(H["tc"], t_close, side="right")) - 1
    return int(H["trend"][i]) if i >= 0 else 0


def pivot_levels(h, l, atr_val, left=4, right=4):
    pts = []
    for i in range(left, len(h) - right):
        if h[i] == h[i - left:i + right + 1].max():
            pts.append(h[i])
        if l[i] == l[i - left:i + right + 1].min():
            pts.append(l[i])
    pts.sort()
    tol, cl = 0.5 * atr_val, []
    for p in pts:
        if cl and p - np.mean(cl[-1]) <= tol:
            cl[-1].append(p)
        else:
            cl.append([p])
    return [float(np.mean(x)) for x in cl if len(x) >= 2]


def round_levels(price):
    step = 10 ** math.floor(math.log10(price)) / 2
    lo = math.floor(price / step) * step
    return [lo - step, lo, lo + step, lo + 2 * step]


# ----------------------------------------------------------------------------
# سشن‌ها (UTC)
# ----------------------------------------------------------------------------
SESSIONS = [
    (0, 7, "Asia", "آسیا 🌏"),
    (7, 12, "London", "لندن 🇬🇧"),
    (12, 16, "LDN-NY", "هم‌پوشانی لندن-نیویورک 🔥"),
    (16, 21, "NewYork", "نیویورک 🗽"),
    (21, 24, "Off", "خارج از سشن اصلی 🌙"),
]
SESSION_FA = {s[2]: s[3] for s in SESSIONS}


def session_of(ts_ms):
    h = datetime.fromtimestamp(ts_ms / 1000, timezone.utc).hour
    for a, b, key, fa in SESSIONS:
        if a <= h < b:
            return key, fa
    return "Off", SESSION_FA["Off"]


# ----------------------------------------------------------------------------
# تشخیص ستاپ: FVG + روند + حجم + سطوح
# ----------------------------------------------------------------------------
def detect(P, t, t1, t2, extra=()):
    """
    P: اندیکاتورهای کل دیتا | t: اندیس کندل تاییدی (آخرین کندل بسته)
    t1,t2: روند دو تایم‌فریم بالاتر (+1 صعودی، -1 نزولی، 0 خنثی)
    """
    if t < 60:
        return None
    a = P["atr"][t]
    if not a > 0:
        return None
    o, h, l, c, v, vs = P["o"], P["h"], P["l"], P["c"], P["v"], P["vsma"]
    rsi = P["rsi"][t]

    for i in range(t - 1, max(2, t - CFG.fvg_lookback) - 1, -1):
        if l[i] > h[i - 2]:
            side, bot, top = 1, h[i - 2], l[i]
        elif h[i] < l[i - 2]:
            side, bot, top = -1, h[i], l[i - 2]
        else:
            continue
        if top - bot < CFG.min_fvg_atr * a:
            continue
        if side == 1 and not (t1 == 1 and t2 >= 0):
            continue
        if side == -1 and not (t1 == -1 and t2 <= 0):
            continue

        mid = (bot + top) / 2
        if side == 1:
            if i + 1 < t and c[i + 1:t].min() < bot:          # FVG شکسته شده
                continue
            if (l[i + 1:t] <= top).sum() > 1:                  # بیش از یک بار تست شده
                continue
            trig = l[t] <= top and c[t] > o[t] and c[t] > mid and l[t] >= bot - 0.5 * a
            if rsi > 72:
                continue
        else:
            if i + 1 < t and c[i + 1:t].max() > top:
                continue
            if (h[i + 1:t] >= bot).sum() > 1:
                continue
            trig = h[t] >= bot and c[t] < o[t] and c[t] < mid and h[t] <= top + 0.5 * a
            if rsi < 28:
                continue
        if not trig:
            continue

        # پله‌های ورود، حد ضرر و تارگت‌ها
        e1 = c[t]
        far = bot if side == 1 else top
        e3 = far
        if abs(e1 - e3) < 0.3 * a:
            e3 = e1 - side * 0.3 * a
        e2 = (e1 + e3) / 2
        sl = (min(e3, far) - 0.5 * a) if side == 1 else (max(e3, far) + 0.5 * a)
        R = abs(e1 - sl)
        if R / e1 > CFG.max_sl_pct or R / e1 < 0.001:
            continue
        tps = [e1 + side * k * R for k in (1, 2, 3)]

        # حمایت / مقاومت
        w0 = max(0, t - 299)
        piv = pivot_levels(h[w0:t + 1], l[w0:t + 1], a) + [float(x) for x in extra]
        opp = [p for p in piv if (p > e1 if side == 1 else p < e1)]
        if opp:
            nearest = min(opp) if side == 1 else max(opp)
            if abs(nearest - e1) < R:                          # جا برای TP1 نیست
                continue
        allv = piv + round_levels(e1)
        conf = any(bot - 0.5 * a <= p <= top + 0.5 * a for p in allv)
        sup = [p for p in piv if p < e1]
        res = [p for p in piv if p > e1]

        imp = v[i - 1] / vs[i - 1] if vs[i - 1] > 0 else 0
        trg = v[t] / vs[t] if vs[t] > 0 else 0
        comp = dict(
            fvg=2, t1=1, t2=1 if t2 == side else 0,
            vol_imp=1 if imp >= 1.5 else 0, vol_trg=1 if trg >= 1.0 else 0,
            level=1 if conf else 0,
        )
        return dict(
            side=side, zone=(float(bot), float(top)), entries=[float(e1), float(e2), float(e3)],
            sl=float(sl), tps=[float(x) for x in tps], R=float(R), atr=float(a),
            base=sum(comp.values()), comp=comp, t1=t1, t2=t2, rsi=float(rsi),
            imp_vol=float(imp), trg_vol=float(trg),
            support=max(sup) if sup else None, resistance=min(res) if res else None,
            round_lv=[float(x) for x in round_levels(e1) if abs(x - e1) / e1 < 0.03][:3],
            fvg_idx=int(i), levels=[float(x) for x in piv],
        )
    return None


def simulate(P, t, sig, max_bars):
    """تا کجا تارگت‌ها قبل از SL فعال می‌شوند؟ (محافظه‌کارانه: اگر هر دو در یک کندل بود = SL)"""
    h, l, side, sl, tps = P["h"], P["l"], sig["side"], sig["sl"], sig["tps"]
    reached = 0
    for k in range(t + 1, min(len(h), t + 1 + int(max_bars))):
        if (l[k] <= sl) if side == 1 else (h[k] >= sl):
            return reached
        while reached < 3 and ((h[k] >= tps[reached]) if side == 1 else (l[k] <= tps[reached])):
            reached += 1
        if reached == 3:
            return 3
    return reached if reached > 0 else None


# ----------------------------------------------------------------------------
# بک‌تست و آمار (وین ریت + سشن‌ها)
# ----------------------------------------------------------------------------
STATS_PATH = os.path.join(HERE, "stats.json")
STATE_PATH = os.path.join(HERE, "state.json")


def jload(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def jsave(path, obj):
    json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False)


def backtest(sym, tf):
    h1, h2 = MTF_MAP[tf]
    L = fetch_long(sym, tf, 3000)
    H1 = htf_pack(fetch_long(sym, h1, 3000), h1)
    H2 = htf_pack(fetch_long(sym, h2, 1000), h2)
    P = prep(L)
    trades, last = [], -999
    for t in range(250, len(L) - 1):
        if t - last < CFG.cooldown_candles:
            continue
        tc = int(P["t"][t]) + TF_MS[tf]
        t1, t2 = trend_at(H1, tc), trend_at(H2, tc)
        if t1 == 0:
            continue
        sig = detect(P, t, t1, t2)
        if not sig or sig["base"] < CFG.base_min:
            continue
        last = t
        r = simulate(P, t, sig, CFG.max_bars)
        if r is None:
            continue
        trades.append([tc, session_of(tc)[0], r, sig["side"]])
    return trades


def get_trades(sym, tf, force=False):
    st = jload(STATS_PATH, {})
    key = f"{sym}|{tf}"
    e = st.get(key)
    if e and not force and time.time() - e["ts"] < CFG.stats_ttl_h * 3600:
        return e["trades"]
    tr = backtest(sym, tf)
    st = jload(STATS_PATH, {})
    st[key] = {"ts": time.time(), "trades": tr}
    jsave(STATS_PATH, st)
    return tr


def winrate(trades):
    n = len(trades)
    return (sum(1 for x in trades if x[2] >= 1) / n if n else None), n


def pooled(tf, session=None):
    out = []
    for k, e in jload(STATS_PATH, {}).items():
        if k.endswith("|" + tf):
            out += [x for x in e["trades"] if session is None or x[1] == session]
    return out


# ----------------------------------------------------------------------------
# تریدینگ‌ویو (اختیاری): رتبه‌بندی خود TradingView به‌عنوان تاییدیه
# ----------------------------------------------------------------------------
def tv_rating(sym, tf):
    if not CFG.use_tv:
        return None
    try:
        from tradingview_ta import TA_Handler, Interval
        iv = {"15m": Interval.INTERVAL_15_MINUTES, "1h": Interval.INTERVAL_1_HOUR}[tf]
        for tv_sym in (sym + ".P", sym):          # اول قرارداد Perpetual، بعد اسپات
            try:
                h = TA_Handler(symbol=tv_sym, screener="crypto", exchange="BINANCE", interval=iv)
                return h.get_analysis().summary["RECOMMENDATION"]
            except Exception:  # noqa
                continue
        return None
    except Exception as e:  # noqa
        log.debug("tv fail %s: %s", sym, e)
        return None


# ----------------------------------------------------------------------------
# شکست‌ها (BOS) و چارت
# ----------------------------------------------------------------------------
def find_breaks(P, t, w=150, left=4, recent=60):
    """شکست سقف/کف‌های مهم با بسته شدن کندل (Break of Structure)"""
    h, l, c = P["h"], P["l"], P["c"]
    out = []
    for p in range(max(left, t - w + 1), t - left):
        if h[p] == h[p - left:p + left + 1].max():
            for k in range(p + left + 1, t + 1):
                if c[k] > h[p]:
                    out.append(("up", p, k, float(h[p])))
                    break
        if l[p] == l[p - left:p + left + 1].min():
            for k in range(p + left + 1, t + 1):
                if c[k] < l[p]:
                    out.append(("down", p, k, float(l[p])))
                    break
    out = [x for x in out if x[2] >= t - recent]
    out.sort(key=lambda x: x[2])
    return out[-3:]


def make_chart(sym, tf, P, t, sig, info, breaks, bars=90):
    """چارت کندلی + EMA + FVG + سطوح + ورود/SL/TP + شکست‌ها + حجم + RSI  ->  bytes (PNG)"""
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    n0 = max(0, t - bars + 1)
    x = np.arange(n0, t + 1)
    o, h, l, c, v = (P[k][n0:t + 1] for k in ("o", "h", "l", "c", "v"))
    cs = pd.Series(P["c"])
    e50 = cs.ewm(span=50, adjust=False).mean().values[n0:t + 1]
    e200 = cs.ewm(span=200, adjust=False).mean().values[n0:t + 1]
    vsma, rsi = P["vsma"][n0:t + 1], P["rsi"][n0:t + 1]

    BG, FG, UP, DN = "#0e1117", "#c9d1d9", "#26a69a", "#ef5350"
    long_ = sig["side"] == 1
    fig, (ax, axv, axr) = plt.subplots(
        3, 1, figsize=(11, 9), sharex=True, facecolor=BG,
        gridspec_kw={"height_ratios": [6, 1.3, 1.3], "hspace": 0.05})
    for a_ in (ax, axv, axr):
        a_.set_facecolor(BG)
        a_.tick_params(colors=FG, labelsize=8)
        for sp in a_.spines.values():
            sp.set_color("#30363d")
        a_.grid(color="#21262d", lw=0.5)
    right = t + 16
    lo = min(l[-55:].min(), sig["sl"], sig["tps"][-1])      # تمرکز روی ناحیه معامله
    hi = max(h[-55:].max(), sig["sl"], sig["tps"][-1])
    pad = (hi - lo) * 0.05
    lo, hi = lo - pad, hi + pad
    inview = lambda y: lo <= y <= hi

    # کندل‌ها
    col = np.where(c >= o, UP, DN)
    ax.vlines(x, l, h, colors=col, lw=1)
    ax.bar(x, np.abs(c - o), bottom=np.minimum(o, c), width=0.6, color=col)
    ax.plot(x, e50, color="#f0b90b", lw=1.2, label="EMA 50")
    ax.plot(x, e200, color="#58a6ff", lw=1.2, label="EMA 200")

    # FVG
    bot, top = sig["zone"]
    zc = UP if long_ else DN
    ax.add_patch(Rectangle((sig["fvg_idx"] - 2 - 0.4, bot), right - (sig["fvg_idx"] - 2), top - bot,
                           facecolor=zc, alpha=0.22, edgecolor=zc, lw=0.8))
    ax.text(sig["fvg_idx"] - 2, bot, f" FVG {'Bullish' if long_ else 'Bearish'}", color=zc, fontsize=8, va="top")

    # حمایت/مقاومت و سطوح روز قبل
    price = sig["entries"][0]
    drawn = []
    for lv in sorted(sig.get("levels", [])):
        if inview(lv) and all(abs(lv - d) / price > 0.002 for d in drawn):
            drawn.append(lv)
            ax.axhline(lv, color="#8b949e", lw=0.8, ls="--", alpha=0.7)
            ax.text(n0, lv, f" S/R {fp(lv)}", color="#8b949e", fontsize=7, va="bottom", clip_on=True)
    for key, name in (("pdh", "PDH"), ("pdl", "PDL")):
        if info.get(key) and inview(info[key]):
            ax.axhline(info[key], color="#d29922", lw=0.9, ls=":")
            ax.text(n0 + 12, info[key], f" {name} {fp(info[key])}", color="#d29922", fontsize=7, va="bottom", clip_on=True)

    # شکست‌ها (BOS)
    for kind, p_, k_, lvl in breaks:
        if k_ < n0:
            continue
        cc = UP if kind == "up" else DN
        ax.plot([max(p_, n0), k_], [lvl, lvl], color=cc, lw=1.1, ls="-.")
        ax.annotate(f"BOS {'↑' if kind == 'up' else '↓'}", xy=(k_, lvl),
                    xytext=(k_, lvl + (1 if kind == 'up' else -1) * 0.6 * sig["atr"]),
                    color=cc, fontsize=8, fontweight="bold", ha="center",
                    arrowprops=dict(arrowstyle="->", color=cc, lw=0.8))

    # ورود / حد ضرر / تارگت‌ها
    def hline(y, color, label, ls="-"):
        ax.plot([t - 3, right], [y, y], color=color, lw=1.2, ls=ls)
        ax.text(right + 0.3, y, f"{label} {fp(y)}", color=color, fontsize=8, va="center", clip_on=False)

    for i, e in enumerate(sig["entries"]):
        hline(e, "#e6edf3", f"E{i + 1}", "--")
    hline(sig["sl"], DN, "SL")
    for i, tp in enumerate(sig["tps"]):
        hline(tp, UP, f"TP{i + 1}")
    ax.scatter([t], [l[-1] * 0.9985 if long_ else h[-1] * 1.0015], marker="^" if long_ else "v",
               s=90, color="#ffffff", zorder=5)

    ax.set_ylim(lo, hi)
    ax.set_xlim(n0 - 1, right)
    ax.legend(loc="upper left", fontsize=8, facecolor=BG, edgecolor="#30363d", labelcolor=FG)

    # حجم
    axv.bar(x, v, width=0.6, color=col, alpha=0.8)
    axv.plot(x, vsma, color="#f0b90b", lw=1)
    axv.set_ylabel("Vol", color=FG, fontsize=8)
    # RSI
    axr.plot(x, rsi, color="#a371f7", lw=1.2)
    for lvl_ in (30, 70):
        axr.axhline(lvl_, color="#8b949e", lw=0.7, ls="--")
    axr.set_ylim(0, 100)
    axr.set_ylabel("RSI 14", color=FG, fontsize=8)
    ticks = list(range(n0, t + 1, max(1, bars // 8)))
    axr.set_xticks(ticks)
    axr.set_xticklabels([pd.to_datetime(P["t"][i], unit="ms").strftime("%d %H:%M") for i in ticks], rotation=0)

    base = sym[:-4] if sym.endswith("USDT") else sym
    h1, h2 = MTF_MAP[tf]
    tr = lambda z: {1: "Bull", -1: "Bear", 0: "Flat"}[z]
    fig.suptitle(f"#{base}/USDT  {tf}  {'LONG' if long_ else 'SHORT'}  |  {CFG.label} Futures  |  Score {info['score']}/9",
                 color="#ffffff", fontsize=13, fontweight="bold", x=0.06, ha="left", y=0.965)
    strat = (f"Strategy: FVG retest  +  MTF trend ({h1}: {tr(sig['t1'])}, {h2}: {tr(sig['t2'])})  +  "
             f"Volume (impulse x{sig['imp_vol']:.1f}, trigger x{sig['trg_vol']:.1f})  +  S/R & key levels  +  BOS"
             + (f"  +  TradingView: {info['tv']}" if info.get("tv") else ""))
    fig.text(0.06, 0.935, strat, color=FG, fontsize=8.5, ha="left")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ----------------------------------------------------------------------------
# ساخت پیام (مطابق قالب کانال)
# ----------------------------------------------------------------------------
FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fp(x):
    d = 2 if x >= 1000 else max(2, min(8, 4 - int(math.floor(math.log10(x)))))
    return f"{x:.{d}f}".replace(".", "/")


def roi(price, e1):
    return int(round(abs(price - e1) / e1 * 100 * CFG.leverage))


def trend_txt(x):
    return {1: "صعودی ✅", -1: "نزولی ✅", 0: "خنثی ➖"}[x]


def build_message(sym, tf, sig, info):
    long_ = sig["side"] == 1
    base = sym[:-4] if sym.endswith("USDT") else sym
    e = sig["entries"]
    lev = str(CFG.leverage).translate(FA_DIGITS)
    h1, h2 = MTF_MAP[tf]
    risk_pct = sig["R"] / e[0] * 100

    L = []
    L.append(f"⭕️#{base}/ USDT {'📈' if long_ else '📉'}💰")
    L.append("")
    L.append("🟢Cross (Long) 📈" if long_ else "🔴Cross (Short) 📉")
    L.append(f"⏱ تایم‌فریم: {tf} | {CFG.label} Futures")
    if info.get("candle_time"):
        L.append(f"🕒 کندل تایید: {info['candle_time']} UTC")
    L.append("")
    L.append(" در این محدوده")
    L.append("")
    for i in range(3):
        L.append(f"✅Entry {i + 1} : {fp(e[i])}")
        L.append("")
    icons = ["🎯", "🚀", "💸"]
    for i in range(3):
        L.append(f"Tp {i + 1} : {roi(sig['tps'][i], e[0])}% {icons[i]}  ({fp(sig['tps'][i])})")
    L.append("")
    L.append(f"Stop Loss : {fp(sig['sl'])} ⛔️📉  (ریسک {risk_pct:.1f}% قیمت)")
    L.append("")
    L.append(f"با نیم الی یک درصد سرمایه با اهرم {lev}× وارد شوید 🏦📉")
    L.append("")
    L.append("1️⃣هر پله ۱ درصد سرمایه")
    L.append("")
    L.append(f"تی پی ها با اهرم {lev}× محاسبه شده")
    L.append("")
    L.append(f"با تایید ریتست FVG {'صعودی' if long_ else 'نزولی'} ({fp(sig['zone'][0])} - {fp(sig['zone'][1])})")
    L.append("")
    L.append("📊 تحلیل:")
    L.append(f"• روند: {h1} {trend_txt(sig['t1'])} | {h2} {trend_txt(sig['t2'])}")
    L.append(f"• حجم: کندل شتاب {sig['imp_vol']:.1f}× | کندل تایید {sig['trg_vol']:.1f}× میانگین")
    for kind, p_, k_, lvl in info.get("breaks", [])[-2:]:
        L.append(f"• شکست ساختار {'صعودی ⬆️' if kind == 'up' else 'نزولی ⬇️'} (BOS) در {fp(lvl)}")
    lv = []
    if sig["support"]:
        lv.append(f"حمایت {fp(sig['support'])}")
    if sig["resistance"]:
        lv.append(f"مقاومت {fp(sig['resistance'])}")
    if lv:
        L.append("• " + " | ".join(lv))
    if info.get("pdh"):
        L.append(f"• سقف/کف دیروز: {fp(info['pdh'])} / {fp(info['pdl'])}")
    if sig["round_lv"]:
        L.append("• اعداد رند: " + " ، ".join(fp(x) for x in sig["round_lv"]))
    L.append(f"• RSI: {sig['rsi']:.0f}" + (f" | TradingView: {info['tv']}" if info.get("tv") else ""))
    L.append(f"• سشن: {info['session_fa']}")
    if info.get("sess_wr") is not None:
        L.append(f"• وین ریت این سشن (بک‌تست): {info['sess_wr'] * 100:.0f}% از {info['sess_n']} معامله")
    if info.get("wr") is not None:
        L.append(f"🎯 وین ریت (بک‌تست {tf}، TP1): {info['wr'] * 100:.0f}% از {info['wr_n']} معامله ({info['wr_src']})")
    else:
        L.append("🎯 وین ریت: داده کافی برای بک‌تست وجود ندارد")
    L.append(f"• امتیاز سیگنال: {info['score']}/9")
    L.append("🧠 استراتژی: FVG Retest + MTF Trend + Volume + S/R + BOS")
    L.append("📎 چارت پیوست شده")
    L.append("")
    L.append("Ai Agent🤖")
    L.append("")
    L.append(CFG.handle)
    L.append("")
    L.append("فعال شده✅✅✅")
    return "\n".join(L)


def _tg(method, **kw):
    if not CFG.token or not CFG.chat_ids:
        raise RuntimeError("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID تنظیم نشده؛ دستور `python bot.py setup` را اجرا کنید")
    r = SESSION.post(f"https://api.telegram.org/bot{CFG.token}/{method}", timeout=40, **kw)
    if not r.ok:
        raise RuntimeError(f"telegram {method} {r.status_code}: {r.text[:200]}")
    return r.json()["result"]


def tg_send(text, chat_id):
    res = _tg("sendMessage", json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
    return res["message_id"]


def tg_send_photo(png, chat_id, reply_to=None, caption=""):
    data = {"chat_id": chat_id, "caption": caption}
    if reply_to:
        data.update(reply_to_message_id=reply_to, allow_sending_without_reply="true")
    _tg("sendPhoto", data=data, files={"photo": ("chart.png", png, "image/png")})


def broadcast(text, png=None, caption=""):
    """پیام سیگنال را به همه چت‌ها می‌فرستد و چارت را به‌صورت ریپلای/پیوست روی همان پیام می‌گذارد."""
    ok = False
    for cid in CFG.chat_ids:
        try:
            mid = tg_send(text, cid)
            ok = True
        except Exception as e:  # noqa
            log.error("send to %s failed: %s", cid, e)
            continue
        if png:
            try:
                tg_send_photo(png, cid, reply_to=mid, caption=caption)
            except Exception as e:  # noqa
                log.error("photo to %s failed: %s", cid, e)
    return ok


# ----------------------------------------------------------------------------
# اسکن زنده
# ----------------------------------------------------------------------------
LIVE_BARS = 300


def analyze_symbol(sym, tf):
    """برمی‌گرداند (sig, info, message, candle_time, ctx) یا None"""
    h1, h2 = MTF_MAP[tf]
    L = fetch_klines(sym, tf, LIVE_BARS)
    if len(L) < 120:
        return None
    P = prep(L)
    H1 = htf_pack(fetch_klines(sym, h1, LIVE_BARS), h1)
    H2 = htf_pack(fetch_klines(sym, h2, LIVE_BARS), h2)

    # آخرین کندل بسته + (SCAN_BACK) کندل قبلی؛ اگر اجرای زمان‌بندی‌شده کمی دیر شد، کندل از دست نرود
    now_ms = time.time() * 1000
    cand = None
    for off in range(int(CFG.scan_back) + 1):
        t = len(L) - 1 - off
        tc = int(P["t"][t]) + TF_MS[tf]
        if now_ms - tc > CFG.max_age_min * 60_000:
            break
        t1, t2 = trend_at(H1, tc), trend_at(H2, tc)
        if t1 != 0 and detect(P, t, t1, t2) is not None:   # پیش‌فیلتر سریع
            cand = (t, tc, t1, t2)
            break
    if cand is None:
        return None
    t, tc, t1, t2 = cand

    # سطوح تایم‌فریم‌های بالاتر + سقف/کف دیروز
    d1 = fetch_klines(sym, "1d", 30)
    extra, info = [], {}
    hd = H1["df"].tail(300)
    extra += pivot_levels(hd.high.values, hd.low.values, P["atr"][t] * 2)
    if len(d1) >= 2:
        info["pdh"], info["pdl"] = float(d1.high.iloc[-1]), float(d1.low.iloc[-1])
        extra += [info["pdh"], info["pdl"]]
    sig = detect(P, t, t1, t2, extra)
    if sig is None or sig["base"] < CFG.base_min:
        return None

    # اگر قیمت الان از محدوده‌ی ورود دور شده یا SL خورده، سیگنال کهنه است
    px = last_price(sym)
    if px:
        e1_, R_ = sig["entries"][0], sig["R"]
        if sig["side"] == 1 and not (sig["sl"] < px <= e1_ + 0.5 * R_):
            return None
        if sig["side"] == -1 and not (e1_ - 0.5 * R_ <= px < sig["sl"]):
            return None
    info["candle_time"] = datetime.fromtimestamp(tc / 1000, timezone.utc).strftime("%H:%M")

    score = sig["base"]
    side_txt = "BUY" if sig["side"] == 1 else "SELL"
    tv = tv_rating(sym, tf)
    info["tv"] = tv
    if tv:
        if side_txt in tv:
            score += 1
        elif ("SELL" if sig["side"] == 1 else "BUY") in tv and tv.startswith("STRONG"):
            return None                          # تریدینگ‌ویو کاملاً مخالف است

    key, fa = session_of(tc)
    info["session_fa"] = fa
    sym_trades = get_trades(sym, tf)
    wr, n = winrate(sym_trades)
    if n >= 8:
        info.update(wr=wr, wr_n=n, wr_src="همین ارز")
    else:
        pw, pn = winrate(pooled(tf))
        if pn >= 15:
            info.update(wr=pw, wr_n=pn, wr_src="میانگین همه ارزها")
    sw, sn = winrate(pooled(tf, key))
    if sn >= 10:
        info.update(sess_wr=sw, sess_n=sn)
        if sw >= 0.55:
            score += 1
    info["score"] = score
    if score < CFG.min_score:
        return None
    info["breaks"] = find_breaks(P, t)
    return sig, info, build_message(sym, tf, sig, info), int(P["t"][t]), dict(P=P, t=t)


def scan_tf(tf, dry=False):
    state = jload(STATE_PATH, {"sent": {}})
    sent = state["sent"]
    now = time.time()
    for sym in CFG.symbols:
        try:
            res = analyze_symbol(sym, tf)
        except Exception as e:  # noqa
            log.warning("%s %s: %s", sym, tf, e)
            continue
        time.sleep(0.2)
        if not res:
            continue
        sig, info, msg, tt, ctx = res
        k = f"{sym}|{tf}|{sig['side']}"
        last = sent.get(k, 0)
        if tt == last or (tt - last) < CFG.cooldown_candles * TF_MS[tf]:
            continue
        log.info("SIGNAL %s %s side=%s score=%s", sym, tf, sig["side"], info["score"])
        png = None
        if CFG.send_chart:
            try:
                png = make_chart(sym, tf, ctx["P"], ctx["t"], sig, info, info["breaks"])
            except Exception as e:  # noqa
                log.error("chart failed %s: %s", sym, e)
        base = sym[:-4] if sym.endswith("USDT") else sym
        cap = f"📈 چارت #{base} | {tf} | FVG • Trend • Volume • S/R • BOS"
        if dry:
            print("\n" + "=" * 40 + "\n" + msg + "\n" + "=" * 40)
            if png:
                os.makedirs(os.path.join(HERE, "charts"), exist_ok=True)
                fn = os.path.join(HERE, "charts", f"{sym}_{tf}_{tt}.png")
                open(fn, "wb").write(png)
                print("chart saved:", fn)
        elif not broadcast(msg, png, cap):
            continue
        sent[k] = tt
        jsave(STATE_PATH, state)


def run(dry=False):
    log.info("bot started | symbols=%d | dry=%s", len(CFG.symbols), dry)
    if not dry and CFG.chat_ids:
        try:
            for cid in CFG.chat_ids:
                tg_send(f"✅ ربات سیگنال 4ux Ai فعال شد\n📊 {CFG.label} Futures | تایم‌فریم 15m و 1h\n🔎 ارزها: "
                        + ", ".join(x[:-4] for x in CFG.symbols), cid)
        except Exception as e:  # noqa
            log.error("startup message failed: %s", e)
    last = {tf: None for tf in SIGNAL_TFS}
    while True:
        now_ms = time.time() * 1000
        for tf in SIGNAL_TFS:
            b = int(now_ms // TF_MS[tf])
            if b != last[tf] and now_ms - b * TF_MS[tf] >= 8000:   # 8 ثانیه بعد از بسته شدن کندل
                last[tf] = b
                log.info("scan %s", tf)
                try:
                    scan_tf(tf, dry)
                except Exception as e:  # noqa
                    log.error("scan error: %s", e)
        time.sleep(5)


def warmup():
    for tf in SIGNAL_TFS:
        for sym in CFG.symbols:
            try:
                tr = get_trades(sym, tf, force=True)
                wr, n = winrate(tr)
                print(f"{sym:10s} {tf:4s} trades={n:3d} winrate={'-' if wr is None else f'{wr * 100:.0f}%'}")
            except Exception as e:  # noqa
                print(f"{sym} {tf} خطا: {e}")
    show_sessions()


def show_sessions():
    print("\nسشن‌های سودده (بر اساس بک‌تست همین استراتژی؛ TP1 قبل از SL):")
    for tf in SIGNAL_TFS:
        print(f"\n--- {tf} ---")
        rows = []
        for a, b, key, fa in SESSIONS:
            tr = pooled(tf, key)
            wr, n = winrate(tr)
            if n:
                tp2 = sum(1 for x in tr if x[2] >= 2) / n
                rows.append((wr, key, fa, n, tp2))
        for wr, key, fa, n, tp2 in sorted(rows, reverse=True):
            print(f"{fa:32s} n={n:4d}  TP1={wr * 100:4.0f}%  TP2={tp2 * 100:4.0f}%")
        if not rows:
            print("داده‌ای نیست؛ اول `python bot.py warmup` را اجرا کنید")


def write_env(updates):
    path = os.path.join(HERE, ".env")
    src = path if os.path.exists(path) else os.path.join(HERE, ".env.example")
    lines = open(src, encoding="utf-8").read().splitlines() if os.path.exists(src) else []
    done = set()
    for i, ln in enumerate(lines):
        key = ln.split("=", 1)[0].strip()
        if key in updates and not ln.strip().startswith("#"):
            lines[i] = f"{key}={updates[key]}"
            done.add(key)
    for k, v in updates.items():
        if k not in done:
            lines.append(f"{k}={v}")
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def setup_wizard():
    print("=== راه‌اندازی ربات سیگنال ===")
    token = input("1) توکن ربات (از @BotFather) را وارد کنید: ").strip()
    me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20).json()
    if not me.get("ok"):
        print("❌ توکن نامعتبر است.")
        return
    print(f"✅ ربات @{me['result']['username']} تایید شد.\n")
    print("2) حالا در تلگرام به همین ربات پیام /start بفرستید.")
    print("   (برای ارسال به کانال: ربات را ادمین کانال کنید و یک پیام در کانال بنویسید)")
    print("   منتظر دریافت پیام هستم (تا ۹۰ ثانیه)...")
    found, offset, t0 = {}, None, time.time()
    while time.time() - t0 < 90 and not found:
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                             params={"timeout": 10, "offset": offset}, timeout=25).json()
        except Exception:  # noqa
            continue
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            for key in ("message", "channel_post", "my_chat_member"):
                ch = (u.get(key) or {}).get("chat")
                if ch:
                    found[str(ch["id"])] = ch.get("title") or ch.get("username") or ch.get("first_name") or "?"
    ids = list(found)
    if ids:
        for i, cid in enumerate(ids, 1):
            print(f"   {i}) {found[cid]}  ->  {cid}")
        ans = input("شماره‌ها را با کاما وارد کنید (Enter = همه): ").strip()
        if ans:
            ids = [ids[int(x) - 1] for x in ans.split(",")]
    else:
        print("پیامی دریافت نشد.")
        ids = [x.strip() for x in input("chat id را دستی وارد کنید (مثلاً 123456789 یا @ai4uxx): ").split(",") if x.strip()]
    if not ids:
        print("❌ چت‌آیدی وارد نشد.")
        return
    write_env({"TELEGRAM_TOKEN": token, "TELEGRAM_CHAT_ID": ",".join(ids)})
    CFG.token, CFG.chat_ids = token, ids
    for cid in ids:
        try:
            tg_send("✅ اتصال ربات سیگنال 4ux Ai برقرار شد.", cid)
            print(f"✅ پیام تست به {cid} ارسال شد.")
        except Exception as e:  # noqa
            print(f"❌ ارسال به {cid} ناموفق: {e}")
    print("\nتنظیمات در فایل .env ذخیره شد. برای اجرا: python bot.py run")
    print("برای اجرای دائمی روی سرور لینوکس: python bot.py install-service")


def install_service():
    import getpass
    unit = f"""[Unit]
Description=4ux Ai Signal Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={getpass.getuser()}
WorkingDirectory={HERE}
ExecStart={sys.executable} {os.path.join(HERE, 'bot.py')} run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
    path = os.path.join(HERE, "signalbot.service")
    open(path, "w").write(unit)
    print(f"فایل سرویس ساخته شد: {path}\nحالا این دستورات را اجرا کنید:\n")
    print(f"  sudo cp {path} /etc/systemd/system/signalbot.service")
    print("  sudo systemctl daemon-reload")
    print("  sudo systemctl enable --now signalbot")
    print("  journalctl -u signalbot -f     # مشاهده لاگ")


def check():
    """تست اتصال: صرافی + تلگرام + chat id (برای اجرای اولیه روی GitHub)"""
    ok = True
    print(f"exchange = {CFG.exchange} | symbols = {len(CFG.symbols)}")
    try:
        df = fetch_klines("BTCUSDT", "15m", 5)
        print(f"✅ صرافی OK | BTCUSDT آخرین کلوز 15m = {df.close.iloc[-1]}")
    except Exception as e:  # noqa
        ok = False
        print(f"❌ دریافت دیتا ناموفق: {e}")
        if "451" in str(e) or "403" in str(e):
            print("   این IP از صرافی بلاک است؛ EXCHANGE=okx را امتحان کنید.")
    if not CFG.token:
        print("❌ TELEGRAM_TOKEN تنظیم نشده")
        return False
    me = SESSION.get(f"https://api.telegram.org/bot{CFG.token}/getMe", timeout=20).json()
    if not me.get("ok"):
        print("❌ توکن تلگرام نامعتبر است")
        return False
    print(f"✅ توکن OK | @{me['result']['username']}")
    if not CFG.chat_ids:
        print("❌ TELEGRAM_CHAT_ID تنظیم نشده")
        return False
    for cid in CFG.chat_ids:
        try:
            tg_send(f"✅ تست اتصال ربات سیگنال ({CFG.label} Futures) موفق بود.", cid)
            print(f"✅ پیام تست ارسال شد به chat id = {cid}")
        except Exception as e:  # noqa
            ok = False
            print(f"❌ ارسال به {cid} ناموفق: {e}")
            if "chat not found" in str(e):
                print("   chat id اشتباه است: باید آیدی عددی خود شما باشد، نه آیدی ربات. ابتدا به ربات /start بفرستید.")
    return ok


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv
    cmd = args[0] if args else "run"
    if cmd == "run":
        run(dry)
    elif cmd == "once":
        for tf in SIGNAL_TFS:
            scan_tf(tf, dry)
    elif cmd == "warmup":
        warmup()
    elif cmd == "sessions":
        show_sessions()
    elif cmd == "test-telegram":
        for cid in CFG.chat_ids:
            tg_send("✅ ربات سیگنال 4ux Ai متصل شد.", cid)
        print("پیام تست ارسال شد")
    elif cmd == "setup":
        setup_wizard()
    elif cmd == "check":
        sys.exit(0 if check() else 1)
    elif cmd == "install-service":
        install_service()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

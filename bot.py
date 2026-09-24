#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Signal Bot — Trendline Break Short (Pump Fade) با نقطه ورود معلق (Pending Retest)
-----------------------------------------------------------------------------------
استراتژی:
  1) قیمت یک پامپ (روند صعودی قوی) می‌زند.
  2) روی سقف‌های همین پامپ یک ترندلاین صعودی/تقریباً افقی رسم می‌شود
     (با اتصال حداقل دو سقف پیوتال).
  3) وقتی یک کندل با تایید (بسته شدن کندل) این ترندلاین را به سمت پایین
     می‌شکند، ضعف پامپ تایید می‌شود.
  4) به‌جای ورود آنی، ربات یک سیگنال «معلق» (Pending) با Entry نزدیک سقف
     همان پامپ صادر می‌کند — یعنی منتظر می‌ماند قیمت دوباره به همان سقف
     برگردد (ری‌تست خط شکسته‌شده) و از آنجا ریزش کند. به همین دلیل نقطه
     ورود گاهی خیلی زود (نزدیک قیمت لحظه‌ای) و گاهی تا چند روز بعد لمس
     می‌شود؛ ولی چون سطح، سطح مقاومتِ تایید‌شده‌ای است، وقتی لمس شود
     معمولاً ریزش سریع رخ می‌دهد و احتمال خوردن SL پایین می‌آید.

قابلیت‌های اضافه:
  - وقتی سیگنالی معلق فعال (لمس) می‌شود، ربات روی همان پیام سیگنال Reply
    می‌زند و اعلام می‌کند سیگنال فعال شد.
  - در ادامه هر بار TP زده شود یا SL بخورد، دوباره روی همان پیام Reply
    می‌زند و می‌گوید کدام TP و چند درصد سود/ضرر شده.
  - اگر سیگنال معلق ظرف مهلت مشخص (پیش‌فرض چند روز) اصلاً لمس نشود،
    منقضی اعلام می‌شود و در آمار برد/باخت شمرده نمی‌شود.
  - هر جمعه، گزارش عملکرد هفتگی (وین ریت + بازدهی کل) برای همه چت‌ها
    ارسال می‌شود.

دستورات:
  python bot.py run             # اجرای دائمی (اسکن سیگنال + ردیابی + گزارش هفتگی)
  python bot.py once            # یک بار اسکن (بدون حلقه دائمی)
  python bot.py check           # تست اتصال صرافی + تلگرام
  python bot.py setup           # ویزارد تنظیم توکن و chat id
  python bot.py test-telegram   # ارسال پیام تست
  python bot.py report          # ارسال دستی گزارش هفتگی (برای تست)
  python bot.py install-service # سرویس systemd برای اجرای دائمی روی سرور

افزودن --dry یعنی ارسال نکن، فقط چاپ کن.
"""
import os, sys, json, math, time, logging, getpass
from datetime import datetime, timezone, timedelta

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

    exchange = os.getenv("EXCHANGE", "okx").lower()          # okx | binance
    okx_base = os.getenv("OKX_BASE", "https://www.okx.com")
    fapi_base = os.getenv("BINANCE_FAPI_BASE", "https://fapi.binance.com")
    futures_only = os.getenv("FUTURES_ONLY", "1") == "1"
    label = "OKX" if exchange == "okx" else "Binance"
    handle = os.getenv("CHANNEL_HANDLE", "@signallroom_bot")

    symbols = [s.strip().upper() for s in os.getenv(
        "SYMBOLS",
        "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,DOTUSDT,"
        "LTCUSDT,ATOMUSDT,NEARUSDT,APTUSDT,ARBUSDT,OPUSDT,SUIUSDT,INJUSDT,TRXUSDT,TONUSDT"
    ).split(",") if s.strip()]

    leverage = _f("LEVERAGE", 20)
    max_sl_pct = _f("MAX_SL_PCT", 3.0) / 100      # حداکثر فاصله SL از ورود (نسبت به قیمت)
    scan_back = _f("SCAN_BACK", 1)
    max_age_min = _f("MAX_AGE_MIN", 25)
    cooldown_candles = _f("COOLDOWN_CANDLES", 6)

    # --- پارامترهای استراتژی «شکست ترندلاین بعد از پامپ» ---
    pump_lookback = _f("PUMP_LOOKBACK", 120)      # چند کندل به عقب برای پیدا کردن شروع پامپ
    pump_min_bars = _f("PUMP_MIN_BARS", 6)        # حداقل طول پامپ (تعداد کندل)
    pump_min_pct = _f("PUMP_MIN_PCT", 6.0) / 100  # حداقل درصد رشد پامپ (کف تا سقف)
    pivot_left = _f("PIVOT_LEFT", 2)
    pivot_right = _f("PIVOT_RIGHT", 2)
    break_buffer_atr = _f("BREAK_BUFFER_ATR", 0.15)   # حداقل عمق شکست خط (ضریب ATR)
    sl_buffer_atr = _f("SL_BUFFER_ATR", 0.5)          # فاصله SL بالای سقف پامپ (ضریب ATR)
    retest_buffer_atr = _f("RETEST_BUFFER_ATR", 0.3)  # فاصله نقطه ورود (ری‌تست) پایین‌تر از سقف دقیق (ضریب ATR)
    min_score = _f("MIN_SCORE", 4)                    # حداقل امتیاز نهایی (از 6)
    enable_long = os.getenv("ENABLE_LONG", "0") == "1"  # حالت قرینه (شکست خط نزولی بعد دامپ) - پیش‌فرض خاموش

    # --- ردیابی سیگنال‌های فعال ---
    monitor_interval_sec = _f("MONITOR_INTERVAL_SEC", 45)
    pending_max_days = _f("PENDING_MAX_DAYS", 3.0)   # حداکثر زمان انتظار برای لمس نقطه ورود (روز)

    # --- گزارش هفتگی ---
    weekly_report_dow = _f("WEEKLY_REPORT_DOW", 4)     # 0=دوشنبه ... 4=جمعه (پایتون: Mon=0 ... Fri=4)
    weekly_report_hour = _f("WEEKLY_REPORT_HOUR", 18)  # ساعت UTC ارسال گزارش

    stats_ttl_h = _f("STATS_TTL_HOURS", 12)


TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
MTF_MAP = {"15m": ("1h", "4h"), "1h": ("4h", "1d")}
SIGNAL_TFS = ("15m", "1h")

log = logging.getLogger("bot")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 signal-bot"})


def enable_console_logging(level=logging.INFO):
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# ----------------------------------------------------------------------------
# دریافت کندل‌ها (OKX پیش‌فرض، سازگار با محدودیت منطقه‌ای GitHub Actions / Binance جایگزین)
# ----------------------------------------------------------------------------
ENDPOINTS = [CFG.fapi_base.rstrip("/") + "/fapi/v1/klines"]
if not CFG.futures_only:
    ENDPOINTS += ["https://api.binance.com/api/v3/klines",
                  "https://data-api.binance.vision/api/v3/klines"]

OKX_BAR = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}


def _okx_klines(symbol, tf, limit, end=None):
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
    df = df[df[8] == "1"].iloc[:, :6]
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


def pivot_idx(arr, left, right, kind="high"):
    """اندیس نقاط پیوت (سقف یا کف) در یک آرایه قیمتی."""
    out = []
    n = len(arr)
    for i in range(left, n - right):
        window = arr[i - left:i + right + 1]
        if kind == "high":
            if arr[i] == window.max() and (i == left or arr[i] > arr[i - left:i].max()):
                out.append(i)
        else:
            if arr[i] == window.min() and (i == left or arr[i] < arr[i - left:i].min()):
                out.append(i)
    return out


# ----------------------------------------------------------------------------
# استراتژی: شکست ترندلاین بعد از پامپ (Trendline Break Short / Pump Fade)
# ----------------------------------------------------------------------------
def _fit_trendline(idxs, prices):
    """رگرسیون خطی ساده روی نقاط پیوت برای رسم ترندلاین."""
    x = np.array(idxs, dtype=float)
    y = np.array(prices, dtype=float)
    if len(x) < 2:
        return None
    slope, intercept = np.polyfit(x, y, 1)
    return slope, intercept


def detect_trendline_break(P, t, side, extra_levels=()):
    """
    side = -1 : شکست ترندلاین بالای یک پامپ => سیگنال Short (استراتژی اصلی کانال)
    side = +1 : حالت قرینه، شکست ترندلاین زیر یک دامپ => سیگنال Long (اختیاری، ENABLE_LONG)
    """
    o, h, l, c, v, vs, atr, rsi = P["o"], P["h"], P["l"], P["c"], P["v"], P["vsma"], P["atr"], P["rsi"]
    a = atr[t]
    if not a > 0 or t < CFG.pump_lookback + 5:
        return None

    w0 = max(0, t - CFG.pump_lookback)

    if side == -1:
        # نقطه شروع پامپ = کف قیمت در بازه لوک‌بک
        L = w0 + int(np.argmin(l[w0:t + 1]))
        if t - L < CFG.pump_min_bars:
            return None
        pump_low = l[L]
        pump_high = h[L:t + 1].max()
        pump_pct = (pump_high - pump_low) / pump_low
        if pump_pct < CFG.pump_min_pct:
            return None

        piv = [i + L for i in pivot_idx(h[L:t + 1], CFG.pivot_left, CFG.pivot_right, "high")]
        mid_level = pump_low + 0.5 * (pump_high - pump_low)
        piv = [i for i in piv if i < t and h[i] >= mid_level]   # فقط سقف‌های واقعی خودِ پامپ (نه نویز نزدیک شروع)
        if len(piv) < 2:
            return None
        h1, h2 = piv[0], piv[-1]
        if h2 <= h1:
            return None

        fit = _fit_trendline([h1, h2], [h[h1], h[h2]])
        if fit is None:
            return None
        slope, intercept = fit
        if slope < -1e-9:  # طبق استراتژی، خط باید روی سقف‌ها صعودی/تقریباً افقی باشد
            return None
        line = lambda x: slope * x + intercept

        win_start = max(h2, t - 8)
        recently_respected = any(c[k] >= line(k) - CFG.break_buffer_atr * a for k in range(win_start, t))
        if not recently_respected:          # تازگی شکست: باید اخیراً (حداکثر ۸ کندل قبل) خط رعایت شده باشد
            return None
        if not (c[t] < line(t) - CFG.break_buffer_atr * a):   # شکست معنادار به سمت پایین در کندل تایید
            return None
        if c[t] <= pump_low + 0.15 * (pump_high - pump_low):  # پامپ قبلاً کامل فید شده، سیگنال دیر است
            return None

        # --- نقطه ورود معلق (Pending Retest) ---
        # به‌جای ورود آنی روی کندل شکست، منتظر برگشت قیمت به سقف همان پامپ می‌مانیم:
        # این «سقف» دقیقاً همان جایی است که در توضیح استراتژی به آن اشاره شده («در سقف ریزش می‌کند»).
        ceiling = max(pump_high, h[h2])
        entry = float(ceiling - CFG.retest_buffer_atr * a)   # کمی پایین‌تر از نوک سقف، برای افزایش احتمال پر شدن سفارش
        sl = float(ceiling + CFG.sl_buffer_atr * a)          # ابطال: عبور واقعی و قطعی از سقف
        R = sl - entry
        if R <= 0 or R / entry > CFG.max_sl_pct or R / entry < 0.001:
            return None
        if entry <= c[t]:
            # اگر قیمت لحظه سیگنال از سقف عبور نکرده (یعنی هنوز به سمت پایین فاصله دارد)، ورود پایین‌تر
            # از قیمت فعلی بی‌معنی است؛ سیگنال معتبر نیست.
            return None

        rng = pump_high - pump_low
        raw_tps = [pump_high - 0.382 * rng, pump_high - 0.618 * rng, pump_low,
                   pump_low - 0.272 * rng, pump_low - 0.618 * rng]
        tps = []
        for x in raw_tps:
            if x < entry - 0.05 * a and (not tps or x < tps[-1] - 0.05 * a):
                tps.append(x)
            if len(tps) == 3:
                break
        while len(tps) < 3:
            tps.append(tps[-1] - R if tps else entry - R)
        tps = [float(x) for x in tps[:3]]
        if c[t] <= tps[-1]:
            # قیمت لحظه‌ی سیگنال از قبل به آخرین هدف رسیده؛ دیگر ری‌تستی برای شکار باقی نمانده.
            return None

        # سطوح مقاومت اضافه (تایم‌فریم بالاتر / سقف دیروز) که نباید بین ورود و TP1 مانع شوند نادیده گرفته می‌شود؛
        # فقط برای امتیازدهی استفاده می‌شود.
        near_res = [x for x in extra_levels if entry * 0.995 <= x <= sl]

        vol_ratio = v[t] / vs[t] if vs[t] > 0 else 0
        comp = dict(
            trendline=2,
            pump=1 if pump_pct >= 2 * CFG.pump_min_pct else 0,
            volume=1 if vol_ratio >= 1.3 else 0,
            rsi=1 if rsi[t - 1] >= 55 else 0,
            red_candle=1 if c[t] < o[t] else 0,
            no_extra_res=1 if not near_res else 0,
        )
        score = sum(comp.values())

        return dict(
            side=-1, entry=entry, sl=sl, tps=tps, R=float(R), atr=float(a),
            pump_low=float(pump_low), pump_high=float(pump_high), pump_pct=float(pump_pct),
            line_pts=[(int(h1), float(h[h1])), (int(h2), float(h[h2]))], slope=float(slope),
            rsi=float(rsi[t]), vol_ratio=float(vol_ratio), comp=comp, score=score,
            L=int(L),
        )

    else:  # side == +1  (حالت قرینه، اختیاری)
        L = w0 + int(np.argmax(h[w0:t + 1]))
        if t - L < CFG.pump_min_bars:
            return None
        dump_high = h[L]
        dump_low = l[L:t + 1].min()
        dump_pct = (dump_high - dump_low) / dump_high
        if dump_pct < CFG.pump_min_pct:
            return None

        piv = [i + L for i in pivot_idx(l[L:t + 1], CFG.pivot_left, CFG.pivot_right, "low")]
        mid_level = dump_high - 0.5 * (dump_high - dump_low)
        piv = [i for i in piv if i < t and l[i] <= mid_level]
        if len(piv) < 2:
            return None
        l1, l2 = piv[0], piv[-1]
        if l2 <= l1:
            return None

        fit = _fit_trendline([l1, l2], [l[l1], l[l2]])
        if fit is None:
            return None
        slope, intercept = fit
        if slope > 1e-9:
            return None
        line = lambda x: slope * x + intercept

        win_start = max(l2, t - 8)
        recently_respected = any(c[k] <= line(k) + CFG.break_buffer_atr * a for k in range(win_start, t))
        if not recently_respected:
            return None
        if not (c[t] > line(t) + CFG.break_buffer_atr * a):
            return None
        if c[t] >= dump_high - 0.15 * (dump_high - dump_low):
            return None

        floor_ = min(dump_low, l[l2])
        entry = float(floor_ + CFG.retest_buffer_atr * a)
        sl = float(floor_ - CFG.sl_buffer_atr * a)
        R = entry - sl
        if R <= 0 or R / entry > CFG.max_sl_pct or R / entry < 0.001:
            return None
        if entry >= c[t]:
            return None

        rng = dump_high - dump_low
        raw_tps = [dump_low + 0.382 * rng, dump_low + 0.618 * rng, dump_high,
                   dump_high + 0.272 * rng, dump_high + 0.618 * rng]
        tps = []
        for x in raw_tps:
            if x > entry + 0.05 * a and (not tps or x > tps[-1] + 0.05 * a):
                tps.append(x)
            if len(tps) == 3:
                break
        while len(tps) < 3:
            tps.append(tps[-1] + R if tps else entry + R)
        tps = [float(x) for x in tps[:3]]
        if c[t] >= tps[-1]:
            return None

        near_res = [x for x in extra_levels if sl <= x <= entry * 1.005]
        vol_ratio = v[t] / vs[t] if vs[t] > 0 else 0
        comp = dict(
            trendline=2,
            dump=1 if dump_pct >= 2 * CFG.pump_min_pct else 0,
            volume=1 if vol_ratio >= 1.3 else 0,
            rsi=1 if rsi[t - 1] <= 45 else 0,
            green_candle=1 if c[t] > o[t] else 0,
            no_extra_res=1 if not near_res else 0,
        )
        score = sum(comp.values())

        return dict(
            side=1, entry=entry, sl=sl, tps=tps, R=float(R), atr=float(a),
            pump_low=float(dump_low), pump_high=float(dump_high), pump_pct=float(dump_pct),
            line_pts=[(int(l1), float(l[l1])), (int(l2), float(l[l2]))], slope=float(slope),
            rsi=float(rsi[t]), vol_ratio=float(vol_ratio), comp=comp, score=score,
            L=int(L),
        )


# ----------------------------------------------------------------------------
# سطوح کمکی (S/R تایم‌فریم بالاتر + سقف/کف دیروز)
# ----------------------------------------------------------------------------
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


# ----------------------------------------------------------------------------
# چارت (اختیاری) — کندل + ترندلاین شکسته‌شده + ورود/SL/TP
# ----------------------------------------------------------------------------
def make_chart(sym, tf, P, t, sig, bars=110):
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n0 = max(0, t - bars + 1)
    x = np.arange(n0, t + 1)
    o, h, l, c = (P[k][n0:t + 1] for k in ("o", "h", "l", "c"))
    BG, FG, UP, DN = "#0e1117", "#c9d1d9", "#26a69a", "#ef5350"
    short_ = sig["side"] == -1

    fig, ax = plt.subplots(figsize=(11, 6.5), facecolor=BG)
    ax.set_facecolor(BG)
    ax.tick_params(colors=FG, labelsize=8)
    for sp in ax.spines.values():
        sp.set_color("#30363d")
    ax.grid(color="#21262d", lw=0.5)

    col = np.where(c >= o, UP, DN)
    ax.vlines(x, l, h, colors=col, lw=1)
    ax.bar(x, np.abs(c - o), bottom=np.minimum(o, c), width=0.6, color=col)

    right = t + 14
    (i1, p1), (i2, p2) = sig["line_pts"]
    lx = np.array([i1, right])
    ly = sig["slope"] * lx + (p1 - sig["slope"] * i1)
    ax.plot(lx, ly, color="#f0b90b", lw=1.4, ls="--", label="Trendline")
    ax.scatter([i1, i2], [p1, p2], color="#f0b90b", zorder=5, s=25)

    def hline(y, color, label, ls="-"):
        ax.plot([t - 3, right], [y, y], color=color, lw=1.2, ls=ls)
        ax.text(right + 0.3, y, f"{label} {y:.4g}", color=color, fontsize=8, va="center")

    hline(sig["entry"], "#e6edf3", "Entry", "--")
    hline(sig["sl"], DN, "SL")
    for i, tp in enumerate(sig["tps"]):
        hline(tp, UP, f"TP{i + 1}")

    lo = min(l.min(), sig["sl"], sig["tps"][-1])
    hi = max(h.max(), sig["sl"], sig["pump_high"])
    pad = (hi - lo) * 0.06
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(n0 - 1, right + 8)

    base = sym[:-4] if sym.endswith("USDT") else sym
    ax.set_title(
        f"#{base}/USDT {tf} {'SHORT' if short_ else 'LONG'} | {CFG.label} Futures | "
        f"شکست ترندلاین بعد از {'پامپ' if short_ else 'دامپ'} | Score {sig['score']}/6",
        color="#ffffff", fontsize=11, fontweight="bold")
    ax.legend(loc="upper left", fontsize=8, facecolor=BG, edgecolor="#30363d", labelcolor=FG)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ----------------------------------------------------------------------------
# ساخت پیام سیگنال
# ----------------------------------------------------------------------------
FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fp(x):
    x = abs(x)
    if x == 0:
        return "0"
    d = 2 if x >= 1000 else max(2, min(8, 4 - int(math.floor(math.log10(x)))))
    return f"{x:.{d}f}".replace(".", "/")


def roi(price, entry):
    return int(round(abs(price - entry) / entry * 100 * CFG.leverage))


def build_message(sym, tf, sig, info):
    short_ = sig["side"] == -1
    base = sym[:-4] if sym.endswith("USDT") else sym
    entry = sig["entry"]
    lev = str(CFG.leverage).translate(FA_DIGITS)
    risk_pct = sig["R"] / entry * 100

    L = []
    L.append(f"⭕️#{base}/ USDT {'📉' if short_ else '📈'}💰")
    L.append("")
    L.append("🔴Cross (Short) 📉" if short_ else "🟢Cross (Long) 📈")
    L.append(f"با تایید شکست ترند لاین ({'فید پامپ' if short_ else 'فید دامپ'})")
    L.append(f"⏱ تایم‌فریم: {tf} | {CFG.label} Futures")
    if info.get("candle_time"):
        L.append(f"🕒 کندل تایید: {info['candle_time']} UTC")
    L.append("")
    L.append(f"⏳Entry (نقطه ورود) : {fp(entry)}")
    L.append("سفارش معلق — منتظر لمس این سطح می‌مانیم (از چند دقیقه تا چند روز طول می‌کشد)")
    L.append("")
    icons = ["🎯", "🚀", "💸"]
    for i in range(3):
        L.append(f"Tp {i + 1} : {roi(sig['tps'][i], entry)}% {icons[i]} ({fp(sig['tps'][i])})")
    L.append("")
    L.append(f"Stop Loss : {fp(sig['sl'])} ⛔️ (ریسک {risk_pct:.1f}% قیمت)")
    L.append("")
    L.append(f"با نیم الی یک درصد سرمایه با اهرم {lev}× وارد شوید 🏦")
    L.append(f"تی پی‌ها با اهرم {lev}× محاسبه شده")
    L.append("")
    L.append("📊 تحلیل:")
    L.append(f"• {'پامپ' if short_ else 'دامپ'} شناسایی‌شده: {sig['pump_pct'] * 100:.1f}% "
              f"({fp(sig['pump_low'])} → {fp(sig['pump_high'])})")
    L.append(f"• ترندلاین از دو سقف/کف پیوتال رسم و با تایید بسته‌شدن کندل شکسته شد")
    L.append(f"• نقطه ورود = ری‌تست {'سقف' if short_ else 'کف'} همان حرکت؛ با لمس، Reply فعال‌سازی ارسال می‌شود")
    L.append(f"• حجم کندل شکست: {sig['vol_ratio']:.1f}× میانگین")
    L.append(f"• RSI: {sig['rsi']:.0f}")
    L.append(f"• امتیاز سیگنال: {sig['score']}/6")
    L.append("🧠 استراتژی: Trendline Break " + ("Short (Pump Fade)" if short_ else "Long (Dump Fade)") + " + Pending Retest")
    if CFG.send_chart:
        L.append("📎 چارت پیوست شده")
    L.append("")
    L.append("Ai Agent🤖")
    L.append("")
    L.append(CFG.handle)
    L.append("")
    L.append("⏳ در انتظار لمس نقطه ورود — به‌محض فعال شدن Reply می‌شود")
    return "\n".join(L)


# ----------------------------------------------------------------------------
# تلگرام
# ----------------------------------------------------------------------------
def _tg(method, **kw):
    if not CFG.token or not CFG.chat_ids:
        raise RuntimeError("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID تنظیم نشده؛ دستور `python bot.py setup` را اجرا کنید")
    r = SESSION.post(f"https://api.telegram.org/bot{CFG.token}/{method}", timeout=40, **kw)
    if not r.ok:
        raise RuntimeError(f"telegram {method} {r.status_code}: {r.text[:200]}")
    return r.json()["result"]


def tg_send(text, chat_id, reply_to=None):
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
        payload["allow_sending_without_reply"] = True
    res = _tg("sendMessage", json=payload)
    return res["message_id"]


def tg_send_photo(png, chat_id, reply_to=None, caption=""):
    data = {"chat_id": chat_id, "caption": caption}
    if reply_to:
        data.update(reply_to_message_id=reply_to, allow_sending_without_reply="true")
    _tg("sendPhoto", data=data, files={"photo": ("chart.png", png, "image/png")})


def broadcast(text, png=None, caption=""):
    """پیام سیگنال را برای همه چت‌ها می‌فرستد؛ چارت را ریپلای روی همان پیام می‌گذارد.
    خروجی: لیستی از {chat_id, message_id} برای استفاده در ردیابی بعدی."""
    sent = []
    for cid in CFG.chat_ids:
        try:
            mid = tg_send(text, cid)
            sent.append({"chat_id": cid, "message_id": mid})
        except Exception as e:  # noqa
            log.error("send to %s failed: %s", cid, e)
            continue
        if png:
            try:
                tg_send_photo(png, cid, reply_to=mid, caption=caption)
            except Exception as e:  # noqa
                log.error("photo to %s failed: %s", cid, e)
    return sent


# ----------------------------------------------------------------------------
# ذخیره‌سازی وضعیت
# ----------------------------------------------------------------------------
STATE_PATH = os.path.join(HERE, "state.json")
POSITIONS_PATH = os.path.join(HERE, "positions.json")
TRADES_LOG_PATH = os.path.join(HERE, "trades_log.json")


def jload(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def jsave(path, obj):
    json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ----------------------------------------------------------------------------
# اسکن زنده
# ----------------------------------------------------------------------------
LIVE_BARS = 400


def analyze_symbol(sym, tf):
    """برمی‌گرداند (sig, info, message, candle_time_ms, ctx) یا None"""
    L = fetch_klines(sym, tf, LIVE_BARS)
    if len(L) < CFG.pump_lookback + 30:
        return None
    P = prep(L)

    now_ms = time.time() * 1000
    t = len(L) - 1
    tc = int(P["t"][t]) + TF_MS[tf]
    if now_ms - tc > CFG.max_age_min * 60_000:
        return None

    # سطوح تایم‌فریم بالاتر برای فیلتر امتیازی (اختیاری)
    extra = []
    try:
        h1, _ = MTF_MAP[tf]
        hd = fetch_klines(sym, h1, 300)
        extra += pivot_levels(hd.high.values, hd.low.values, P["atr"][t] * 2)
        d1 = fetch_klines(sym, "1d", 3)
        if len(d1) >= 2:
            extra += [float(d1.high.iloc[-1]), float(d1.low.iloc[-1])]
    except Exception:  # noqa
        pass

    sig = detect_trendline_break(P, t, side=-1, extra_levels=extra)
    if sig is None and CFG.enable_long:
        sig = detect_trendline_break(P, t, side=1, extra_levels=extra)
    if sig is None or sig["score"] < CFG.min_score:
        return None

    # اعتبارسنجی با قیمت لحظه‌ای: چون Entry اینجا «معلق» است (باید قیمت به آن برگردد)،
    # فقط سیگنال‌هایی که از قبل باطل شده‌اند (رد شده از SL) یا تارگت را کامل زده‌اند حذف می‌شوند.
    px = last_price(sym)
    if px:
        if sig["side"] == -1 and (px >= sig["sl"] or px <= sig["tps"][-1]):
            return None
        if sig["side"] == 1 and (px <= sig["sl"] or px >= sig["tps"][-1]):
            return None

    info = {"candle_time": datetime.fromtimestamp(tc / 1000, timezone.utc).strftime("%H:%M")}
    return sig, info, build_message(sym, tf, sig, info), int(P["t"][t]), dict(P=P, t=t)


def scan_tf(tf, dry=False):
    state = jload(STATE_PATH, {"sent": {}})
    sent = state["sent"]
    positions = jload(POSITIONS_PATH, {})

    for sym in CFG.symbols:
        try:
            res = analyze_symbol(sym, tf)
        except Exception as e:  # noqa
            log.warning("%s %s: %s", sym, tf, e)
            continue
        time.sleep(0.15)
        if not res:
            continue
        sig, info, msg, tt, ctx = res
        k = f"{sym}|{tf}|{sig['side']}"
        last = sent.get(k, 0)
        if tt == last or (tt - last) < CFG.cooldown_candles * TF_MS[tf]:
            continue

        log.info("SIGNAL %s %s side=%s score=%s", sym, tf, sig["side"], sig["score"])
        png = None
        if CFG.send_chart:
            try:
                png = make_chart(sym, tf, ctx["P"], ctx["t"], sig)
            except Exception as e:  # noqa
                log.error("chart failed %s: %s", sym, e)
        base = sym[:-4] if sym.endswith("USDT") else sym
        cap = f"📈 چارت #{base} | {tf} | Trendline Break"

        if dry:
            print("\n" + "=" * 40 + "\n" + msg + "\n" + "=" * 40)
            sent[k] = tt
            continue

        chats = broadcast(msg, png, cap)
        if not chats:
            continue
        sent[k] = tt

        pos_id = f"{sym}|{tf}|{sig['side']}|{tt}"
        positions[pos_id] = {
            "symbol": sym, "tf": tf, "side": sig["side"],
            "entry": sig["entry"], "sl": sig["sl"], "tps": sig["tps"],
            "opened_at": int(time.time()), "chats": chats,
            "hit_tps": [False, False, False], "status": "pending",
            "activated_at": None,
        }

    jsave(STATE_PATH, state)
    jsave(POSITIONS_PATH, positions)


# ----------------------------------------------------------------------------
# ردیابی سیگنال‌های فعال + اطلاع‌رسانی با Reply
# ----------------------------------------------------------------------------
def _reply_all(pos, text, dry):
    if dry:
        print("[DRY REPLY]", text)
        return
    for ch in pos["chats"]:
        try:
            tg_send(text, ch["chat_id"], reply_to=ch["message_id"])
        except Exception as e:  # noqa
            log.error("reply failed %s: %s", ch, e)


def monitor_positions(dry=False):
    positions = jload(POSITIONS_PATH, {})
    trades_log = jload(TRADES_LOG_PATH, [])
    changed = False

    for pos_id, pos in list(positions.items()):
        status = pos.get("status")
        if status not in ("pending", "open"):
            continue
        px = last_price(pos["symbol"])
        if px is None:
            continue
        side = pos["side"]
        entry, sl, tps = pos["entry"], pos["sl"], pos["tps"]

        # --- مرحله ۱: سیگنال معلق -> منتظر لمس نقطه ورود ---
        if status == "pending":
            age_days = (time.time() - pos["opened_at"]) / 86400
            touched = (px >= entry) if side == -1 else (px <= entry)
            if touched:
                pos["status"] = "open"
                pos["activated_at"] = int(time.time())
                changed = True
                _reply_all(pos, f"✅ سیگنال فعال شد!\n#{pos['symbol']} | {'SHORT' if side == -1 else 'LONG'}\n"
                                 f"قیمت به نقطه ورود ({fp(entry)}) رسید — از این لحظه تارگت‌ها و SL دنبال می‌شود.", dry)
                status = "open"   # اجازه بده در همین چرخه، وضعیت باز هم بلافاصله چک شود
            elif age_days > CFG.pending_max_days:
                pos["status"] = "expired"
                changed = True
                _reply_all(pos, f"⌛️ سیگنال منقضی شد\n#{pos['symbol']} | {'SHORT' if side == -1 else 'LONG'}\n"
                                 f"قیمت ظرف {CFG.pending_max_days:.0f} روز به نقطه ورود نرسید.", dry)
                trades_log.append({
                    "symbol": pos["symbol"], "tf": pos["tf"], "side": pos["side"],
                    "opened_at": pos["opened_at"], "closed_at": int(time.time()),
                    "result": "expired", "tps_hit": 0, "pct": 0.0,
                })
                continue
            else:
                continue

        events = []

        sl_hit = (px >= sl) if side == -1 else (px <= sl)
        if sl_hit:
            loss_pct = -(abs(sl - entry) / entry * 100 * CFG.leverage)
            events.append(("SL", None, loss_pct))
        else:
            for i, tp in enumerate(tps):
                if pos["hit_tps"][i]:
                    continue
                hit = (px <= tp) if side == -1 else (px >= tp)
                if hit:
                    pct = roi(tp, entry)
                    events.append(("TP", i, pct))

        for kind, tp_idx, pct in events:
            changed = True
            if kind == "SL":
                text = (f"⛔️ استاپ لاس فعال شد\n"
                        f"#{pos['symbol']} | {'SHORT' if side == -1 else 'LONG'}\n"
                        f"نتیجه: {pct:.1f}% (ضرر)")
                pos["status"] = "closed"
                pos["result"] = "loss"
                pos["closed_at"] = int(time.time())
                pos["final_pct"] = pct
            else:
                pos["hit_tps"][tp_idx] = True
                text = (f"🎯 TP{tp_idx + 1} فعال شد ✅\n"
                        f"#{pos['symbol']} | {'SHORT' if side == -1 else 'LONG'}\n"
                        f"سود این پله: {pct:.1f}%")
                if tp_idx == 2:  # TP3 => بستن کامل پوزیشن
                    text += "\n\n✅ سیگنال با موفقیت کامل بسته شد (TP3)"
                    pos["status"] = "closed"
                    pos["result"] = "win"
                    pos["closed_at"] = int(time.time())
                    pos["final_pct"] = pct

            _reply_all(pos, text, dry)

        if pos.get("status") == "closed":
            trades_log.append({
                "symbol": pos["symbol"], "tf": pos["tf"], "side": pos["side"],
                "opened_at": pos["opened_at"], "closed_at": pos.get("closed_at", int(time.time())),
                "result": pos.get("result", "?"),
                "tps_hit": sum(pos["hit_tps"]),
                "pct": pos.get("final_pct", 0.0),
            })

    if changed:
        jsave(POSITIONS_PATH, positions)
        jsave(TRADES_LOG_PATH, trades_log)


# ----------------------------------------------------------------------------
# گزارش هفتگی (جمعه)
# ----------------------------------------------------------------------------
def weekly_report(dry=False, days=7):
    trades_log = jload(TRADES_LOG_PATH, [])
    cutoff = time.time() - days * 86400
    recent_all = [t for t in trades_log if t.get("closed_at", 0) >= cutoff]
    expired = [t for t in recent_all if t["result"] == "expired"]
    recent = [t for t in recent_all if t["result"] in ("win", "loss")]

    n = len(recent)
    if n == 0:
        text = "📊 گزارش عملکرد هفتگی\n\nاین هفته سیگنال فعال‌شده‌ای برای گزارش وجود نداشت."
        if expired:
            text += f"\n({len(expired)} سیگنال معلق منقضی شد و لمس نشد)"
    else:
        wins = [t for t in recent if t["result"] == "win" or t["tps_hit"] >= 1]
        losses = [t for t in recent if t["result"] == "loss" and t["tps_hit"] == 0]
        win_rate = len(wins) / n * 100
        total_pct = sum(t["pct"] for t in recent)
        avg_pct = total_pct / n
        best = max(recent, key=lambda t: t["pct"])
        worst = min(recent, key=lambda t: t["pct"])

        L = []
        L.append("📊 گزارش عملکرد هفتگی ربات سیگنال")
        L.append(f"🗓 بازه: ۷ روز گذشته")
        L.append("")
        L.append(f"تعداد سیگنال‌های فعال‌شده: {n}")
        L.append(f"وین ریت: {win_rate:.0f}% ({len(wins)} برد / {len(losses)} باخت)")
        L.append(f"مجموع بازدهی (با اهرم {int(CFG.leverage)}×): {total_pct:.1f}%")
        L.append(f"میانگین بازدهی هر سیگنال: {avg_pct:.1f}%")
        L.append(f"بهترین معامله: #{best['symbol']} ({best['pct']:.1f}%)")
        L.append(f"بدترین معامله: #{worst['symbol']} ({worst['pct']:.1f}%)")
        if expired:
            L.append(f"⌛️ {len(expired)} سیگنال معلق ظرف مهلت لمس نشد و منقضی شد (بدون تاثیر در وین‌ریت)")
        L.append("")
        L.append(CFG.handle)
        text = "\n".join(L)

    if dry:
        print("\n" + "=" * 40 + "\n" + text + "\n" + "=" * 40)
    else:
        for cid in CFG.chat_ids:
            try:
                tg_send(text, cid)
            except Exception as e:  # noqa
                log.error("weekly report to %s failed: %s", cid, e)
    return text


def maybe_send_weekly_report(dry=False):
    state = jload(STATE_PATH, {"sent": {}})
    now = datetime.now(timezone.utc)
    today_key = now.strftime("%Y-%m-%d")
    if (now.weekday() == CFG.weekly_report_dow and now.hour == CFG.weekly_report_hour
            and state.get("last_weekly_report") != today_key):
        log.info("sending weekly report")
        weekly_report(dry=dry)
        state["last_weekly_report"] = today_key
        jsave(STATE_PATH, state)


# ----------------------------------------------------------------------------
# اجرای دائمی / یک‌باره
# ----------------------------------------------------------------------------
def once(dry=False):
    for tf in SIGNAL_TFS:
        scan_tf(tf, dry)
    monitor_positions(dry)


def run(dry=False):
    log.info("bot started | symbols=%d | dry=%s", len(CFG.symbols), dry)
    if not dry and CFG.chat_ids:
        try:
            for cid in CFG.chat_ids:
                tg_send(
                    "✅ ربات سیگنال (Trendline Break Short + Pending Retest) فعال شد\n"
                    f"📊 {CFG.label} Futures | تایم‌فریم 15m و 1h\n"
                    "🔎 ارزها: " + ", ".join(x[:-4] for x in CFG.symbols), cid)
        except Exception as e:  # noqa
            log.error("startup message failed: %s", e)

    last = {tf: None for tf in SIGNAL_TFS}
    last_monitor = 0.0
    while True:
        now_ms = time.time() * 1000
        for tf in SIGNAL_TFS:
            b = int(now_ms // TF_MS[tf])
            if b != last[tf] and now_ms - b * TF_MS[tf] >= 8000:
                last[tf] = b
                log.info("scan %s", tf)
                try:
                    scan_tf(tf, dry)
                except Exception as e:  # noqa
                    log.error("scan error: %s", e)

        if time.time() - last_monitor >= CFG.monitor_interval_sec:
            last_monitor = time.time()
            try:
                monitor_positions(dry)
            except Exception as e:  # noqa
                log.error("monitor error: %s", e)
            try:
                maybe_send_weekly_report(dry)
            except Exception as e:  # noqa
                log.error("weekly report error: %s", e)

        time.sleep(5)


# ----------------------------------------------------------------------------
# چک اتصال / تلگرام تست / ویزارد تنظیم / سرویس systemd
# ----------------------------------------------------------------------------
def check_connection():
    print("در حال تست اتصال صرافی...")
    try:
        df = fetch_klines(CFG.symbols[0], "15m", 5)
        print(f"✅ اتصال صرافی ({CFG.label}) موفق — آخرین قیمت {CFG.symbols[0]}: {df.close.iloc[-1]}")
    except Exception as e:  # noqa
        print(f"❌ اتصال صرافی ناموفق: {e}")

    print("در حال تست اتصال تلگرام...")
    if not CFG.token:
        print("❌ TELEGRAM_TOKEN تنظیم نشده. دستور `python bot.py setup` را اجرا کنید.")
        return
    try:
        me = SESSION.get(f"https://api.telegram.org/bot{CFG.token}/getMe", timeout=15).json()
        if me.get("ok"):
            print(f"✅ ربات تلگرام @{me['result']['username']} متصل است.")
        else:
            print("❌ توکن تلگرام نامعتبر است.")
    except Exception as e:  # noqa
        print(f"❌ اتصال تلگرام ناموفق: {e}")

    if not CFG.chat_ids:
        print("⚠️ هیچ chat id ای تنظیم نشده.")


def test_telegram():
    if not CFG.chat_ids:
        print("❌ chat id تنظیم نشده.")
        return
    for cid in CFG.chat_ids:
        try:
            tg_send("✅ پیام تست از ربات سیگنال.", cid)
            print(f"✅ ارسال به {cid} موفق.")
        except Exception as e:  # noqa
            print(f"❌ ارسال به {cid} ناموفق: {e}")


def write_env(updates):
    path = os.path.join(HERE, ".env")
    lines = open(path, encoding="utf-8").read().splitlines() if os.path.exists(path) else []
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
    print("2) حالا در تلگرام به همین ربات پیام /start بفرستید (یا ربات را ادمین کانال/گروه کنید).")
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
            print(f"  {i}) {found[cid]} -> {cid}")
        ans = input("شماره‌ها را با کاما وارد کنید (Enter = همه): ").strip()
        if ans:
            ids = [ids[int(x) - 1] for x in ans.split(",")]
    else:
        print("پیامی دریافت نشد.")
        ids = [x.strip() for x in input("chat id را دستی وارد کنید (مثلاً 123456789 یا @channel): ").split(",") if x.strip()]

    if not ids:
        print("❌ چت‌آیدی وارد نشد.")
        return

    write_env({"TELEGRAM_TOKEN": token, "TELEGRAM_CHAT_ID": ",".join(ids)})
    CFG.token, CFG.chat_ids = token, ids
    for cid in ids:
        try:
            tg_send("✅ اتصال ربات سیگنال برقرار شد.", cid)
            print(f"✅ پیام تست به {cid} ارسال شد.")
        except Exception as e:  # noqa
            print(f"❌ ارسال به {cid} ناموفق: {e}")
    print("\nتنظیمات در فایل .env ذخیره شد. برای اجرا: python bot.py run")


def install_service():
    unit = f"""[Unit]
Description=Signal Bot (Trendline Break Short)
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
    print("  journalctl -u signalbot -f   # مشاهده لاگ")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    enable_console_logging(logging.INFO)
    args = sys.argv[1:]
    dry = "--dry" in args
    args = [a for a in args if a != "--dry"]
    cmd = args[0] if args else "run"

    if cmd == "run":
        run(dry)
    elif cmd == "once":
        once(dry)
    elif cmd == "check":
        check_connection()
    elif cmd == "setup":
        setup_wizard()
    elif cmd == "test-telegram":
        test_telegram()
    elif cmd == "report":
        weekly_report(dry)
    elif cmd == "install-service":
        install_service()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

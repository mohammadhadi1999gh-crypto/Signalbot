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
import os, sys, json, math, time, logging, threading
from concurrent.futures import ThreadPoolExecutor
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


# سطح حساسیت: هرچه بالاتر، سیگنال بیشتر (و معمولاً دقت کمتر)
PRESETS = {
    "conservative": dict(touches=1, fvg_atr=0.25, base_min=5, min_score=6, strict=True,  room=1.0, cooldown=6, spike=2.5, hunt=3, rsi=(28, 72), veto=True),
    "balanced":     dict(touches=2, fvg_atr=0.20, base_min=4, min_score=5, strict=False, room=0.8, cooldown=4, spike=2.0, hunt=2, rsi=(25, 75), veto=True),
    "aggressive":   dict(touches=3, fvg_atr=0.15, base_min=4, min_score=4, strict=False, room=0.6, cooldown=3, spike=1.7, hunt=2, rsi=(22, 78), veto=False),
    "ultra":        dict(touches=5, fvg_atr=0.10, base_min=3, min_score=3, strict=False, room=0.4, cooldown=1, spike=1.3, hunt=1, rsi=(15, 85), veto=False),
}
_SENS = os.getenv("SENSITIVITY", "ultra").lower()
_PR = PRESETS.get(_SENS, PRESETS["ultra"])


class CFG:
    sensitivity = _SENS if _SENS in PRESETS else "ultra"
    rsi_lo, rsi_hi = _PR["rsi"]                                  # محدوده RSI برای رد کردن ورود دیرهنگام
    tv_veto = _PR["veto"]                                        # اگر تریدینگ‌ویو STRONG مخالف بود رد شود؟
    spike_on = os.getenv("SPIKE_HUNTER", "1") == "1"             # اسپایک‌یاب (بعد از اسپایک)
    hunter_on = os.getenv("HUNTER_LIMITS", "1") == "1"           # سفارش‌های Limit شکارچی روی استخر نقدینگی
    spike_atr = _f("SPIKE_ATR", _PR["spike"])                    # حداقل اندازه‌ی اسپایک (برحسب ATR)
    hunt_min_spikes = _f("HUNT_MIN_SPIKES", _PR["hunt"])         # حداقل اسپایک اخیر برای فعال شدن شکارچی
    pending_valid = _f("PENDING_VALID", 4)                       # اعتبار سفارش Limit (کندل)
    fvg_touches = _f("FVG_MAX_TOUCHES", _PR["touches"])          # حداکثر دفعات تست‌شدن قبلی FVG
    strict_gate = _PR["strict"]                                  # روند: سخت‌گیرانه یا منعطف
    room_r = _f("ROOM_R", _PR["room"])                           # حداقل فاصله تا مانع بعدی (بر حسب R)
    trendlines = os.getenv("TRENDLINES", "1") == "1"            # ستاپ و تاییدیه ترندلاین
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
        "LTCUSDT,ATOMUSDT,NEARUSDT,APTUSDT,ARBUSDT,OPUSDT,SUIUSDT,INJUSDT,TRXUSDT,TONUSDT,"
        "BCHUSDT,ETCUSDT,FILUSDT,UNIUSDT,AAVEUSDT,RUNEUSDT,SEIUSDT,TIAUSDT,WLDUSDT,PEPEUSDT,"
        "SHIBUSDT,HBARUSDT,ICPUSDT,STXUSDT,ENAUSDT,WIFUSDT,CRVUSDT,LDOUSDT,ORDIUSDT,TAOUSDT"
    ).split(",") if s.strip()]
    leverage = _f("LEVERAGE", 20)
    max_sl_pct = _f("MAX_SL_PCT", 2.0) / 100     # حداکثر فاصله SL از ورود (قیمت)
    min_fvg_atr = _f("MIN_FVG_ATR", _PR["fvg_atr"])         # حداقل اندازه FVG نسبت به ATR
    fvg_lookback = _f("FVG_LOOKBACK", 50)         # تعداد کندل برای جستجوی FVG
    base_min = _f("BASE_MIN_SCORE", _PR["base_min"])   # حداقل امتیاز پایه (از 8)
    min_score = _f("MIN_SCORE", _PR["min_score"])      # حداقل امتیاز نهایی (از 10)
    max_bars = _f("BACKTEST_MAX_BARS", 48)        # حداکثر کندل انتظار برای نتیجه معامله
    use_tv = os.getenv("USE_TRADINGVIEW_TA", "1") == "1"
    cooldown_candles = _f("COOLDOWN_CANDLES", _PR["cooldown"])
    stats_ttl_h = _f("STATS_TTL_HOURS", 12)
    workers = _f("WORKERS", 10)                                   # تعداد ارز که هم‌زمان اسکن می‌شود
    exchange = os.getenv("EXCHANGE", "okx").lower()
    kcex_base = os.getenv("KCEX_BASE", "https://api.kcex.com")
    label = {"okx": "OKX", "binance": "Binance", "kcex": "KCEX"}.get(os.getenv("EXCHANGE", "okx").lower(), "OKX")
    # --- استراتژی Pump-Fade Grid (فرمول کشف‌شده از کانال نمونه) ---
    fvgtl_on = os.getenv("FVG_TL_STRATEGY", "1") == "1"     # ستاپ‌های FVG/ترندلاین (استراتژی‌های قبلی)
    grid_on = os.getenv("GRID_STRATEGY", "1") == "1"
    grid_gain_min = _f("GRID_GAIN_MIN", 0.25)      # حداقل رشد قیمت در پنجره‌ی زیر برای «پامپ‌شده» حساب شدن
    grid_lookback_h = _f("GRID_LOOKBACK_H", 24)    # پنجره‌ی بررسی رشد قیمت (ساعت)
    grid_rsi_min = _f("GRID_RSI_MIN", 68)          # حداقل RSI برای تایید اشباع خرید
    grid_e2 = _f("GRID_E2_MULT", 1.20)             # ضریب Entry2 نسبت به Entry1 (کشف‌شده: ۱٫۲۰)
    grid_e3 = _f("GRID_E3_MULT", 1.44)             # ضریب Entry3 نسبت به Entry1 (کشف‌شده: ۱٫۴۴ = ۱٫۲۰²)
    grid_sl = _f("GRID_SL_MULT", 1.10)             # ضریب SL نسبت به Entry3 (کشف‌شده: ۱٫۱۰)
    grid_tp = tuple(float(x) for x in os.getenv("GRID_TP_PCT", "0.03,0.04,0.05").split(","))  # کشف‌شده: ۳٪/۴٪/۵٪
    real_leverage = _f("REAL_LEVERAGE", 5)         # اهرم واقعی حساب شما، برای هشدار لیکویید (نه اهرم نمایشی ۲۰×)
    auto_universe = os.getenv("AUTO_UNIVERSE", "1") == "1"   # اسکن خودکار بیشترین‌پامپ‌کرده‌های بازار به‌جای SYMBOLS ثابت
    universe_size = _f("UNIVERSE_SIZE", 60)        # تعداد ارزی که از صرافی به‌عنوان کاندید Pump-Fade گرفته می‌شود


TF_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
MTF_MAP = {"15m": ("1h", "4h"), "30m": ("1h", "4h"), "1h": ("4h", "1d")}   # تایم‌فریم‌های بالاتر برای تایید روند
SIGNAL_TFS = ("15m", "30m", "1h")

log = logging.getLogger("bot")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 signal-bot"})
_adapter = requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64, max_retries=0)
SESSION.mount("https://", _adapter)
SESSION.mount("http://", _adapter)

# ----------------------------------------------------------------------------
# دریافت کندل‌ها
# ----------------------------------------------------------------------------
ENDPOINTS = [CFG.fapi_base.rstrip("/") + "/fapi/v1/klines"]   # فقط برای EXCHANGE=binance
if not CFG.futures_only:   # فقط اگر فیوچرز در دسترس نبود (قیمت اسپات کمی متفاوت است)
    ENDPOINTS += ["https://api.binance.com/api/v3/klines",
                  "https://data-api.binance.vision/api/v3/klines"]


OKX_BAR = {"15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}


def _okx_klines(symbol, tf, limit, end=None):
    """کندل‌های بسته‌شده‌ی قرارداد Perpetual در OKX (بدون محدودیت منطقه‌ای GitHub)."""
    inst = symbol[:-4] + "-USDT-SWAP" if symbol.endswith("USDT") else symbol
    rows, after = [], (int(end) + 1 if end else None)
    while len(rows) < limit:
        params = {"instId": inst, "bar": OKX_BAR[tf], "limit": min(300, limit - len(rows))}
        if after:
            params["after"] = after
        for attempt in range(4):
            r = SESSION.get(CFG.okx_base.rstrip("/") + "/api/v5/market/candles", params=params, timeout=15)
            if getattr(r, "status_code", 200) == 429:      # محدودیت نرخ: کمی صبر و تلاش مجدد
                time.sleep(1 + attempt)
                continue
            break
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
    if not rows:
        raise RuntimeError(f"okx: no data for {inst}")
    df = pd.DataFrame(rows)
    df = df[df[8] == "1"].iloc[:, :6]                      # فقط کندل‌های بسته
    df.columns = ["time", "open", "high", "low", "close", "volume"]
    df = df.astype(float)
    df["time"] = df["time"].astype("int64")
    return df.sort_values("time").reset_index(drop=True)


KCEX_BAR = {"15m": "15m", "30m": "30m", "1h": "60m", "4h": "4h", "1d": "1d"}


def _kcex_klines(symbol, tf, limit, end=None):
    """کندل‌های اسپات KCEX (پابلیک، بدون نیاز به API Key)."""
    rows, cur_end = [], (int(end) if end else None)
    while len(rows) < limit:
        params = {"symbol": symbol, "interval": KCEX_BAR[tf], "limit": min(1000, limit - len(rows))}
        if cur_end:
            params["endTime"] = cur_end
        for attempt in range(4):
            r = SESSION.get(CFG.kcex_base.rstrip("/") + "/api/v1/klines", params=params, timeout=15)
            if getattr(r, "status_code", 200) == 429:
                time.sleep(1 + attempt)
                continue
            break
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list) or not data:
            break
        rows = data + rows                 # صعودی برمی‌گردد؛ صفحه‌ی قدیمی‌تر را جلو می‌گذاریم
        cur_end = int(data[0][0]) - 1
        if len(data) < params["limit"]:
            break
    if not rows:
        raise RuntimeError(f"kcex: no data for {symbol}")
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["time", "open", "high", "low", "close", "volume"]
    df = df.astype(float)
    df["time"] = df["time"].astype("int64")
    df = df.drop_duplicates("time").sort_values("time")
    if len(df) and df.time.iloc[-1] + TF_MS[tf] > time.time() * 1000:   # کندل باز را کنار بگذار
        df = df.iloc[:-1]
    return df.tail(limit).reset_index(drop=True)


_LEV_TOKEN = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S")


def fetch_kcex_gainers(n):
    """صعودی‌ترین جفت‌های USDT بازار اسپات KCEX در ۲۴ ساعت اخیر (برای اسکن Pump-Fade).
    n بزرگ‌تر یا مساوی تعداد کل جفت‌های USDT یعنی عملاً همه‌ی بازار بررسی می‌شود."""
    r = SESSION.get(CFG.kcex_base.rstrip("/") + "/api/v1/ticker/24hr", timeout=20)
    r.raise_for_status()
    rows = [x for x in r.json() if isinstance(x, dict) and str(x.get("symbol", "")).endswith("USDT")
            and not any(x["symbol"][:-4].endswith(suf) for suf in _LEV_TOKEN)]
    rows.sort(key=lambda x: float(x.get("priceChangePercent") or 0), reverse=True)
    log.info("kcex universe: %d USDT pairs total, taking top %d by 24h change", len(rows), min(n, len(rows)))
    return [x["symbol"] for x in rows[:n]]


def fetch_grid_candidates():
    """کل بازار USDT کِی‌سکس را با یک درخواست ارزان (تیکر ۲۴ ساعته) پیش‌فیلتر می‌کند:
    فقط ارزهایی که از قبل رشد قابل‌توجه ۲۴ ساعته دارند برای بررسی کامل (کندل/RSI) کاندید می‌شوند.
    این یعنی «همه‌ی ارزهای این استراتژی» بدون نیاز به گرفتن کندل تک‌تک صدها جفت."""
    try:
        r = SESSION.get(CFG.kcex_base.rstrip("/") + "/api/v1/ticker/24hr", timeout=20)
        r.raise_for_status()
        rows = [x for x in r.json() if isinstance(x, dict) and str(x.get("symbol", "")).endswith("USDT")
                and not any(x["symbol"][:-4].endswith(suf) for suf in _LEV_TOKEN)]
    except Exception as e:  # noqa
        log.warning("grid universe fetch failed: %s", e)
        return []
    floor = CFG.grid_gain_min * 0.6   # آستانه‌ی نرم؛ فیلتر دقیق واقعی روی کندل در detect_pump_fade انجام می‌شود
    cands = [x["symbol"] for x in rows if float(x.get("priceChangePercent") or 0) >= floor]
    log.info("grid universe: %d/%d USDT pairs pumped >= %.0f%% in 24h, checking those in full",
             len(cands), len(rows), floor * 100)
    return cands


def last_price(symbol):
    try:
        if CFG.exchange == "okx":
            inst = symbol[:-4] + "-USDT-SWAP"
            r = SESSION.get(CFG.okx_base.rstrip("/") + "/api/v5/market/ticker", params={"instId": inst}, timeout=10)
            return float(r.json()["data"][0]["last"])
        if CFG.exchange == "kcex":
            r = SESSION.get(CFG.kcex_base.rstrip("/") + "/api/v1/ticker/price", params={"symbol": symbol}, timeout=10)
            return float(r.json()["price"])
        r = SESSION.get(CFG.fapi_base.rstrip("/") + "/fapi/v1/ticker/price", params={"symbol": symbol}, timeout=10)
        return float(r.json()["price"])
    except Exception:  # noqa
        return None


def fetch_klines(symbol, tf, limit=500, end=None):
    """فقط کندل‌های بسته‌شده را برمی‌گرداند."""
    if CFG.exchange == "okx":
        return _okx_klines(symbol, tf, limit, end)
    if CFG.exchange == "kcex":
        return _kcex_klines(symbol, tf, limit, end)
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
def gate_ok(side, t1, t2):
    """هم‌جهت بودن روند تایم‌فریم‌های بالاتر با معامله"""
    if CFG.strict_gate:
        return t1 == side and t2 * side >= 0
    if CFG.sensitivity == "ultra":
        return t1 * side >= 0 and t2 * side >= 0          # کافی است هیچ‌کدام مخالف نباشند
    return t1 * side >= 0 and t2 * side >= 0 and (t1 == side or t2 == side)


# ----------------------------- ترندلاین -----------------------------
def swing_points(h, l, k=3):
    n = len(h)
    if n < 2 * k + 1:
        return [], []
    from numpy.lib.stride_tricks import sliding_window_view as swv
    hi = np.where(h[k:n - k] == swv(h, 2 * k + 1).max(axis=1))[0] + k
    lo = np.where(l[k:n - k] == swv(l, 2 * k + 1).min(axis=1))[0] + k
    return list(hi), list(lo)


def find_trendlines(P, t, w=160, k=3):
    """بهترین ترندلاین نزولی (از سقف‌ها) و صعودی (از کف‌ها) را پیدا می‌کند."""
    a = P["atr"][t]
    tol = 0.25 * a
    w0 = max(0, t - w)
    h, l, c = P["h"][w0:t + 1], P["l"][w0:t + 1], P["c"][w0:t + 1]
    hi, lo = swing_points(h, l, k)
    out = {}
    for typ, piv, price in (("down", hi, h), ("up", lo, l)):
        piv = piv[-6:]
        best = None
        for ai in range(len(piv)):
            for bi in range(ai + 1, len(piv)):
                p1, p2 = piv[ai], piv[bi]
                if p2 - p1 < 6:
                    continue
                y1, y2 = price[p1], price[p2]
                if (typ == "down" and not y2 < y1) or (typ == "up" and not y2 > y1):
                    continue
                m = (y2 - y1) / (p2 - p1)
                line = y1 + m * (np.arange(p1, len(c)) - p1)
                viol = (c[p1:] > line + tol) if typ == "down" else (c[p1:] < line - tol)
                idx = np.where(viol)[0]
                brk = None
                if len(idx):
                    brk = p1 + int(idx[0])
                    if brk < len(c) - 6:                     # خیلی وقت پیش شکسته شده
                        continue
                end = brk if brk is not None else len(c)
                touches = sum(1 for q in piv if p1 <= q < end and abs(price[q] - (y1 + m * (q - p1))) <= tol)
                key = (touches, p2)
                if touches >= 2 and (best is None or key > best[0]):
                    best = (key, dict(type=typ, p1=(w0 + p1, float(y1)), p2=(w0 + p2, float(y2)), m=float(m),
                                      brk=None if brk is None else w0 + brk, touches=int(touches),
                                      line_t=float(y1 + m * (len(c) - 1 - p1))))
        if best:
            out[typ] = best[1]
    return out


def tl_setup(P, t, tls):
    """ستاپ‌های ترندلاین: شکست، ریتست پس از شکست، برگشت از خط (Bounce)"""
    a = P["atr"][t]
    tol = 0.25 * a
    o, h, l, c = P["o"][t], P["h"][t], P["l"][t], P["c"][t]
    found = []
    for typ, tl in tls.items():
        lt, brk, name = tl["line_t"], tl["brk"], ("نزولی" if typ == "down" else "صعودی")
        if typ == "down":
            if brk == t and c > o and c - o >= 0.3 * a:
                found.append((3, 1, "break", f"شکست ترندلاین نزولی ({tl['touches']} برخورد)", tl))
            elif brk is not None and brk < t and l <= lt + tol and c > lt and c > o:
                found.append((2, 1, "retest", f"ریتست ترندلاین نزولیِ شکسته‌شده ({tl['touches']} برخورد)", tl))
            elif brk is None and h >= lt - tol and c < lt and c < o:
                found.append((1, -1, "bounce", f"برگشت از ترندلاین نزولی (برخورد {tl['touches'] + 1}ام)", tl))
        else:
            if brk == t and c < o and o - c >= 0.3 * a:
                found.append((3, -1, "break", f"شکست ترندلاین صعودی ({tl['touches']} برخورد)", tl))
            elif brk is not None and brk < t and h >= lt - tol and c < lt and c < o:
                found.append((2, -1, "retest", f"ریتست ترندلاین صعودیِ شکسته‌شده ({tl['touches']} برخورد)", tl))
            elif brk is None and l <= lt + tol and c > lt and c > o:
                found.append((1, 1, "bounce", f"برگشت از ترندلاین صعودی (برخورد {tl['touches'] + 1}ام)", tl))
    if not found:
        return None
    _, side, kind, desc, tl = max(found, key=lambda x: x[0])
    return dict(side=side, kind=kind, desc=desc, line_t=tl["line_t"], type=tl["type"])


def tl_bonus(side, tls, P, t):
    """تاییدیه ترندلاین برای ستاپ‌های FVG"""
    a, c = P["atr"][t], P["c"][t]
    for typ, tl in tls.items():
        lt, brk = tl["line_t"], tl["brk"]
        if side == 1:
            if typ == "up" and brk is None and abs(c - lt) <= 1.5 * a and c >= lt - 0.25 * a:
                return 1, f"قیمت روی ترندلاین صعودی (حمایت) ✅ ({tl['touches']} برخورد)"
            if typ == "down" and brk is not None and t - brk <= 5:
                return 1, "شکست اخیر ترندلاین نزولی ✅"
        else:
            if typ == "down" and brk is None and abs(c - lt) <= 1.5 * a and c <= lt + 0.25 * a:
                return 1, f"قیمت زیر ترندلاین نزولی (مقاومت) ✅ ({tl['touches']} برخورد)"
            if typ == "up" and brk is not None and t - brk <= 5:
                return 1, "شکست اخیر ترندلاین صعودی ✅"
    return 0, None


# ----------------------------- ساخت سیگنال -----------------------------
MAX_BASE, MAX_SCORE = 8, 10


def _build(P, t, side, bot, top, extra, kind, fvg_idx, imp_idx, t1, t2, tls, tl_main=None):
    a = P["atr"][t]
    o, h, l, c, v, vs = P["o"], P["h"], P["l"], P["c"], P["v"], P["vsma"]
    e1 = c[t]
    far = bot if side == 1 else top
    e3 = far
    if abs(e1 - e3) < 0.3 * a:
        e3 = e1 - side * 0.3 * a
    e2 = (e1 + e3) / 2
    sl = (min(e3, far) - 0.5 * a) if side == 1 else (max(e3, far) + 0.5 * a)
    R = abs(e1 - sl)
    if R / e1 > CFG.max_sl_pct or R / e1 < 0.001:
        return None
    tps = [e1 + side * k * R for k in (1, 2, 3)]

    w0 = max(0, t - 299)
    piv = pivot_levels(h[w0:t + 1], l[w0:t + 1], a) + [float(x) for x in extra]
    opp = [p for p in piv if (p > e1 if side == 1 else p < e1)]
    if opp:
        nearest = min(opp) if side == 1 else max(opp)
        if abs(nearest - e1) < CFG.room_r * R:
            return None
    allv = piv + round_levels(e1)
    conf = any(bot - 0.5 * a <= p <= top + 0.5 * a for p in allv)
    sup = [p for p in piv if p < e1]
    res = [p for p in piv if p > e1]

    if imp_idx is not None:
        imp = v[imp_idx] / vs[imp_idx] if vs[imp_idx] > 0 else 0
    else:
        imp = float(np.max(v[t - 2:t + 1] / np.maximum(vs[t - 2:t + 1], 1e-12)))
    trg = v[t] / vs[t] if vs[t] > 0 else 0

    if tl_main:
        tl_pt, tl_desc = 1, tl_main["desc"]
    elif tls:
        tl_pt, tl_desc = tl_bonus(side, tls, P, t)
    else:
        tl_pt, tl_desc = 0, None

    comp = dict(
        setup=2, t1=1 if t1 == side else 0, t2=1 if t2 == side else 0,
        vol_imp=1 if imp >= 1.5 else 0, vol_trg=1 if trg >= 1.0 else 0,
        level=1 if conf else 0, tl=tl_pt,
    )
    return dict(
        side=side, kind=kind, zone=(float(bot), float(top)),
        entries=[float(e1), float(e2), float(e3)],
        sl=float(sl), tps=[float(x) for x in tps], R=float(R), atr=float(a),
        base=sum(comp.values()), comp=comp, t1=t1, t2=t2, rsi=float(P["rsi"][t]),
        imp_vol=float(imp), trg_vol=float(trg),
        support=max(sup) if sup else None, resistance=min(res) if res else None,
        round_lv=[float(x) for x in round_levels(e1) if abs(x - e1) / e1 < 0.03][:3],
        fvg_idx=int(fvg_idx), levels=[float(x) for x in piv],
        tl_desc=tl_desc, tl_kind=(tl_main or {}).get("kind"),
        tls=[dict(type=x["type"], p1=x["p1"], p2=x["p2"], m=x["m"], brk=x["brk"],
                  touches=x["touches"]) for x in (tls or {}).values()],
    )


def detect(P, t, t1, t2, extra=()):
    """
    P: اندیکاتورهای کل دیتا | t: اندیس کندل تاییدی (آخرین کندل بسته)
    t1,t2: روند دو تایم‌فریم بالاتر (+1 صعودی، -1 نزولی، 0 خنثی)
    دو نوع ستاپ: 1) ریتست FVG   2) شکست/ریتست/برگشت از ترندلاین
    """
    if t < 60:
        return None
    a = P["atr"][t]
    if not a > 0:
        return None
    o, h, l, c = P["o"], P["h"], P["l"], P["c"]
    rsi = P["rsi"][t]
    tls = None

    for i in range(t - 1, max(2, t - CFG.fvg_lookback) - 1, -1):
        if l[i] > h[i - 2]:
            side, bot, top = 1, h[i - 2], l[i]
        elif h[i] < l[i - 2]:
            side, bot, top = -1, h[i], l[i - 2]
        else:
            continue
        if top - bot < CFG.min_fvg_atr * a or not gate_ok(side, t1, t2):
            continue

        mid = (bot + top) / 2
        if side == 1:
            if i + 1 < t and c[i + 1:t].min() < bot:          # FVG شکسته شده
                continue
            if (l[i + 1:t] <= top).sum() > CFG.fvg_touches:     # بیش از حد تست شده
                continue
            trig = l[t] <= top and c[t] > o[t] and c[t] > mid and l[t] >= bot - 0.5 * a
            if rsi > CFG.rsi_hi:
                continue
        else:
            if i + 1 < t and c[i + 1:t].max() > top:
                continue
            if (h[i + 1:t] >= bot).sum() > CFG.fvg_touches:
                continue
            trig = h[t] >= bot and c[t] < o[t] and c[t] < mid and h[t] <= top + 0.5 * a
            if rsi < CFG.rsi_lo:
                continue
        if not trig:
            continue
        if tls is None:
            tls = find_trendlines(P, t) if CFG.trendlines else {}
        sig = _build(P, t, side, bot, top, extra, "FVG", i, i - 1, t1, t2, tls)
        if sig:
            return sig

    if CFG.trendlines:
        if tls is None:
            tls = find_trendlines(P, t)
        st = tl_setup(P, t, tls) if tls else None
        if st and gate_ok(st["side"], t1, t2):
            side = st["side"]
            if (side == 1 and rsi > CFG.rsi_hi) or (side == -1 and rsi < CFG.rsi_lo):
                return None
            lt = st["line_t"]
            return _build(P, t, side, lt - 0.3 * a, lt + 0.3 * a, extra, "TL", t - 12, None, t1, t2, tls, st)
    return None


# ----------------------------- اسپایک‌یاب -----------------------------
def _spike_sig(P, t, side, kind, e, sl, zone, extra_fields, comp, t1, t2, levels, sup_res_src):
    a = P["atr"][t]
    e1 = e[0]
    R = abs(e1 - sl)
    tps = [e1 + side * k * R for k in (1, 2, 3)]
    v, vs = P["v"], P["vsma"]
    sup = [p for p in sup_res_src if p < e1]
    res = [p for p in sup_res_src if p > e1]
    return dict(
        side=side, kind=kind, zone=(float(zone[0]), float(zone[1])),
        entries=[float(x) for x in e], sl=float(sl), tps=[float(x) for x in tps], R=float(R), atr=float(a),
        base=sum(comp.values()), comp=comp, t1=t1, t2=t2, rsi=float(P["rsi"][t]),
        imp_vol=float(v[t] / vs[t]) if vs[t] > 0 else 0.0,
        trg_vol=float(np.max(v[t - 2:t + 1] / np.maximum(vs[t - 2:t + 1], 1e-12))),
        support=max(sup) if sup else None, resistance=min(res) if res else None,
        round_lv=[float(x) for x in round_levels(e1) if abs(x - e1) / e1 < 0.03][:3],
        fvg_idx=int(t), levels=[float(x) for x in levels],
        tl_desc=None, tl_kind=None, tls=[], pending=True, valid=int(CFG.pending_valid), **extra_fields)


def detect_spike(P, t, t1, t2, extra=()):
    """اسپایک بزرگ با فتیله‌ی رد (Stop-Hunt / Liquidity Sweep) -> سفارش‌های Limit داخل فتیله"""
    if t < 60:
        return None
    a = P["atr"][t - 1]                       # ATR قبل از اسپایک
    if not a > 0:
        return None
    o, h, l, c, v, vs = P["o"][t], P["h"][t], P["l"][t], P["c"][t], P["v"], P["vsma"]
    rng = h - l
    if rng < CFG.spike_atr * a:
        return None
    body_hi, body_lo = max(o, c), min(o, c)
    lw, uw = body_lo - l, h - body_hi
    if lw >= 0.5 * rng and (c - l) >= 0.5 * rng and lw >= 0.4 * a:
        side = 1
    elif uw >= 0.5 * rng and (h - c) >= 0.5 * rng and uw >= 0.4 * a:
        side = -1
    else:
        return None
    if CFG.sensitivity != "ultra" and t1 == -side and t2 == -side:   # هر دو تایم‌فریم بالاتر قویاً مخالف
        return None
    rsi = P["rsi"][t]
    if (side == 1 and rsi > CFG.rsi_hi + 3) or (side == -1 and rsi < CFG.rsi_lo - 3):
        return None

    wl = lw if side == 1 else uw              # طول فتیله
    if side == 1:
        e = [body_lo - 0.2 * wl, body_lo - 0.5 * wl, body_lo - 0.8 * wl]
        sl, zone = l - 0.4 * a, (l, body_lo)
    else:
        e = [body_hi + 0.2 * wl, body_hi + 0.5 * wl, body_hi + 0.8 * wl]
        sl, zone = h + 0.4 * a, (body_hi, h)
    if abs(e[0] - sl) / e[0] > CFG.max_sl_pct:
        return None

    w0 = max(0, t - 299)
    piv = pivot_levels(P["h"][w0:t], P["l"][w0:t], a) + [float(x) for x in extra]
    swept = any((l < p < body_lo) if side == 1 else (body_hi < p < h) for p in piv)   # فتیله سطح مهمی را شکار کرده
    vr = v[t] / vs[t] if vs[t] > 0 else 0
    comp = dict(setup=2, t1=1 if t1 == side else 0, t2=1 if t2 == side else 0,
                vol_imp=1 if vr >= 1.5 else 0, vol_trg=1 if vr >= 1.0 else 0,
                level=1 if swept else 0, tl=0)
    return _spike_sig(P, t, side, "SPIKE", e, sl, zone,
                      dict(spike_x=float(rng / a), wick_pct=float(wl / rng * 100), swept=bool(swept),
                           spike_desc=f"اسپایک {'نزولی' if side == 1 else 'صعودی'} {rng / a:.1f}×ATR با فتیله {wl / rng * 100:.0f}%"
                                      + (" و شکار سطح مهم (Liquidity Sweep)" if swept else "")),
                      comp, t1, t2, piv, piv)


def detect_hunter(P, t, t1, t2, extra=()):
    """سفارش Limit از پیش روی استخر نقدینگی (کف/سقف برابر، سقف/کف دیروز) در بازار اسپایکی"""
    if t < 80:
        return None
    a = P["atr"][t]
    if not a > 0:
        return None
    h, l, c = P["h"], P["l"], P["c"]
    rng, at = (h - l)[t - 39:t + 1], P["atr"][t - 39:t + 1]
    n_sp = int((rng >= 1.8 * at).sum())
    if n_sp < CFG.hunt_min_spikes:            # بازار اسپایکی نیست
        return None
    w0 = max(0, t - 299)
    piv = pivot_levels(h[w0:t + 1], l[w0:t + 1], a) + [float(x) for x in extra]
    if not piv:
        return None
    rsi = P["rsi"][t]
    px = c[t]
    order = sorted((1, -1), key=lambda sd: -(t1 == sd) - (t2 == sd))   # اول جهت هم‌راستا با روند
    for side in order:
        if not gate_ok(side, t1, t2):
            continue
        if (side == 1 and rsi > CFG.rsi_hi) or (side == -1 and rsi < CFG.rsi_lo):
            continue
        cands = [p for p in piv if (px - 2.2 * a <= p <= px - 0.7 * a)] if side == 1 else \
                [p for p in piv if (px + 0.7 * a <= p <= px + 2.2 * a)]
        if not cands:
            continue
        lv = max(cands) if side == 1 else min(cands)
        e = [lv, lv - side * 0.3 * a, lv - side * 0.6 * a]
        sl = lv - side * 1.1 * a
        if abs(e[0] - sl) / e[0] > CFG.max_sl_pct:
            continue
        zone = (e[2], e[0]) if side == 1 else (e[0], e[2])
        pdlh = any(abs(lv - float(x)) <= 0.2 * a for x in extra)
        vr = P["v"][t] / P["vsma"][t] if P["vsma"][t] > 0 else 0
        comp = dict(setup=2, t1=1 if t1 == side else 0, t2=1 if t2 == side else 0,
                    vol_imp=1 if n_sp >= 3 else 0, vol_trg=1 if vr >= 1.0 else 0,
                    level=1, tl=1 if pdlh else 0)
        sg = _spike_sig(P, t, side, "HUNT", e, sl, zone,
                        dict(spike_x=0.0, wick_pct=0.0, swept=False, hunt_level=float(lv), n_spikes=n_sp,
                             spike_desc=f"سفارش شکار روی استخر نقدینگی {fp(lv)}"
                                        + (" (سقف/کف دیروز)" if pdlh else " (کف/سقف‌های برابر)")
                                        + f" | {n_sp} اسپایک در ۴۰ کندل اخیر"),
                        comp, t1, t2, piv, piv)
        sg["valid"] = int(CFG.pending_valid) + 2
        sg["fvg_idx"] = int(t - 10)
        return sg
    return None


def detect_pump_fade(P, t, t1, t2, extra=()):
    """
    Pump-Fade Grid: استراتژی کشف‌شده از تحلیل ۲۹ سیگنال یک کانال نمونه.
    فقط SHORT. Entry1 = قیمت لحظه‌ی تشخیص «پامپ افراطی» (رشد شدید + RSI بالا).
    Entry2/Entry3/TP1-3/SL همه با ضرایب ثابت کشف‌شده از Entry1 محاسبه می‌شوند
    (این نسبت‌ها فرمول‌اند، نه برگرفته از سطح چارت):
      Entry2 = E1×1.20   Entry3 = E1×1.44   SL = E3×1.10 (≈ E1×1.584)
      TP1/2/3 = E1×(0.97 / 0.96 / 0.95)
    """
    if not CFG.grid_on or t < 60:
        return None
    a = P["atr"][t]
    if not a > 0:
        return None
    c, rsi = P["c"], P["rsi"][t]
    tf_ms = int(round((P["t"][1] - P["t"][0])))
    if tf_ms <= 0:
        return None
    look = max(4, int(CFG.grid_lookback_h * 3600_000 / tf_ms))
    if t - look < 0:
        return None
    gain = c[t] / c[t - look] - 1
    if gain < CFG.grid_gain_min or rsi < CFG.grid_rsi_min:
        return None
    # عمداً هیچ فیلتر «روند تایم‌فریم بالاتر باید نزولی باشد» اینجا نیست: خودِ ماهیت این استراتژی
    # فید کردن یک پامپ در اوج آن است، یعنی درست همان لحظه‌ای که تایم‌فریم بالاتر هنوز صعودی به‌نظر می‌رسد.
    # t1/t2 فقط برای امتیازدهی کیفیت زیر استفاده می‌شوند، نه به‌عنوان شرط ورود.

    e1 = float(c[t])
    e2, e3 = e1 * CFG.grid_e2, e1 * CFG.grid_e3
    sl = e3 * CFG.grid_sl
    p1, p2, p3 = CFG.grid_tp
    tps = [e1 * (1 - p1), e1 * (1 - p2), e1 * (1 - p3)]
    w0 = max(0, t - 299)
    piv = pivot_levels(P["h"][w0:t + 1], P["l"][w0:t + 1], a) + [float(x) for x in extra]
    vr = P["v"][t] / P["vsma"][t] if P["vsma"][t] > 0 else 0
    comp = dict(setup=2, t1=1 if t1 != 1 else 0, t2=1 if t2 != 1 else 0,
                vol_imp=1 if vr >= 1.3 else 0, vol_trg=1 if gain >= 2 * CFG.grid_gain_min else 0,
                level=1, tl=0)
    sup = [p for p in piv if p < e1]
    return dict(
        side=-1, kind="GRID", zone=(e1, e2), entries=[e1, e2, e3], sl=float(sl),
        tps=tps, R=float(abs(sl - e1)), atr=float(a), base=sum(comp.values()), comp=comp,
        t1=t1, t2=t2, rsi=float(rsi), imp_vol=float(vr), trg_vol=float(vr),
        support=max(sup) if sup else None, resistance=None,
        round_lv=[float(x) for x in round_levels(e1) if abs(x - e1) / e1 < 0.03][:3],
        fvg_idx=int(t), levels=[float(x) for x in piv], tl_desc=None, tl_kind=None, tls=[],
        pending=True, valid=10**6,                 # پله‌های ۲ و ۳ فقط اگر قیمت باز هم پامپ کند پر می‌شوند
        gain_pct=float(gain * 100), grid_desc=f"پامپ {gain*100:.0f}٪ در {CFG.grid_lookback_h:.0f} ساعت اخیر + RSI {rsi:.0f}",
    )


def detect_all(P, t, t1, t2, extra=(), kinds=None):
    """همه‌ی سیگنال‌های ممکن روی این کندل: اسپایک، FVG/ترندلاین، شکارچی، Pump-Fade Grid.
    kinds=None یعنی همه؛ در غیر این صورت فقط انواع داخل مجموعه (مثلاً {"GRID"}) بررسی می‌شود."""
    out = []
    if CFG.spike_on and (kinds is None or "SPIKE" in kinds):
        sg = detect_spike(P, t, t1, t2, extra)  # noqa
        if sg:
            out.append(sg)
    if CFG.fvgtl_on and (kinds is None or "FVG" in kinds or "TL" in kinds):
        sg = detect(P, t, t1, t2, extra)
        if sg:
            out.append(sg)
    if CFG.hunter_on and (kinds is None or "HUNT" in kinds):
        sg = detect_hunter(P, t, t1, t2, extra)
        if sg:
            out.append(sg)
    if CFG.grid_on and (kinds is None or "GRID" in kinds):
        sg = detect_pump_fade(P, t, t1, t2, extra)
        if sg:
            out.append(sg)
    return out


def kind_group(kind):
    return {"FVG": "SETUP", "TL": "SETUP", "SPIKE": "SPIKE", "HUNT": "HUNT", "GRID": "GRID"}.get(kind, "SETUP")


def simulate(P, t, sig, max_bars):
    """تا کجا تارگت‌ها قبل از SL فعال می‌شوند؟ (محافظه‌کارانه: اگر هر دو در یک کندل بود = SL)
    سفارش‌های Limit (pending) باید اول فعال شوند؛ اگر فعال نشدند معامله حساب نمی‌شود."""
    h, l, side, sl, tps = P["h"], P["l"], sig["side"], sig["sl"], sig["tps"]
    start = t + 1
    if sig.get("pending"):
        e1, fill = sig["entries"][0], None
        for k in range(t + 1, min(len(h), t + 1 + int(sig["valid"]))):
            if (l[k] <= e1) if side == 1 else (h[k] >= e1):
                fill = k
                break
        if fill is None:
            return None
        if (l[fill] <= sl) if side == 1 else (h[fill] >= sl):
            return 0
        start = fill + 1
    reached = 0
    for k in range(start, min(len(h), start + int(max_bars))):
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
    trades, last = [], {}
    for t in range(250, len(L) - 1):
        tc = int(P["t"][t]) + TF_MS[tf]
        t1, t2 = trend_at(H1, tc), trend_at(H2, tc)
        if t1 == 0 and t2 == 0:
            continue
        for sig in detect_all(P, t, t1, t2):
            grp = kind_group(sig["kind"])
            if sig["base"] < CFG.base_min or t - last.get(grp, -999) < CFG.cooldown_candles:
                continue
            last[grp] = t
            r = simulate(P, t, sig, CFG.max_bars)
            if r is None:
                continue
            trades.append([tc, session_of(tc)[0], r, sig["side"], grp])
    return trades


_STATS_LOCK = threading.Lock()


def get_trades(sym, tf, force=False):
    key = f"{sym}|{tf}"
    with _STATS_LOCK:
        e = jload(STATS_PATH, {}).get(key)
    if e and not force and time.time() - e["ts"] < CFG.stats_ttl_h * 3600:
        return e["trades"]
    tr = backtest(sym, tf)
    with _STATS_LOCK:
        st = jload(STATS_PATH, {})
        st[key] = {"ts": time.time(), "trades": tr}
        jsave(STATS_PATH, st)
    return tr


def by_group(trades, grp):
    return [x for x in trades if (x[4] if len(x) > 4 else "SETUP") == grp]


def winrate(trades):
    n = len(trades)
    return (sum(1 for x in trades if x[2] >= 1) / n if n else None), n


def pooled(tf, session=None, grp=None):
    out = []
    for k, e in jload(STATS_PATH, {}).items():
        if k.endswith("|" + tf):
            out += [x for x in e["trades"] if session is None or x[1] == session]
    return by_group(out, grp) if grp else out


# ----------------------------------------------------------------------------
# تریدینگ‌ویو (اختیاری): رتبه‌بندی خود TradingView به‌عنوان تاییدیه
# ----------------------------------------------------------------------------
def tv_rating(sym, tf):
    if not CFG.use_tv:
        return None
    try:
        from tradingview_ta import TA_Handler, Interval
        iv = {"15m": Interval.INTERVAL_15_MINUTES, "30m": Interval.INTERVAL_30_MINUTES, "1h": Interval.INTERVAL_1_HOUR}[tf]
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
    core_lo, core_hi = l[-55:].min(), h[-55:].max()
    core_span = core_hi - core_lo
    sl_clip = sig["kind"] == "GRID" and abs(sig["sl"] - core_hi) > 3 * max(core_span, sig["atr"])
    lo = min(core_lo, sig["tps"][-1], sig["sl"] if not sl_clip else core_hi)
    hi = max(core_hi, sig["tps"][-1], sig["sl"] if not sl_clip else core_hi)
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
    zone_lbl = {"FVG": f" FVG {'Bullish' if long_ else 'Bearish'}", "TL": " Trendline zone",
                "SPIKE": f" Spike wick x{sig.get('spike_x', 0):.1f} ATR", "HUNT": " Liquidity pool (limit ladder)",
                "GRID": f" Pump +{sig.get('gain_pct', 0):.0f}% (grid zone)"}[sig["kind"]]
    ax.text(sig["fvg_idx"] - 2, bot, zone_lbl, color=zc, fontsize=8, va="top")

    # ترندلاین‌ها
    for tl in sig.get("tls", []):
        x1, y1 = tl["p1"]
        xs = np.array([max(x1, n0), right])
        ys = y1 + tl["m"] * (xs - x1)
        tc_ = UP if tl["type"] == "up" else DN
        ax.plot(xs, ys, color=tc_, lw=1.7, ls="--", alpha=0.95)
        pts = [q for q in (tl["p1"], tl["p2"]) if q[0] >= n0]
        if pts:
            ax.scatter([q[0] for q in pts], [q[1] for q in pts], color=tc_, s=22, zorder=4)
        lx = min(right - 2, max(xs[0] + 4, n0 + 4))
        ax.text(lx, y1 + tl["m"] * (lx - x1), f" TL {'support' if tl['type'] == 'up' else 'resistance'} ({tl['touches']}x)",
                color=tc_, fontsize=7.5, va="bottom", clip_on=True)
        if tl["brk"] is not None and tl["brk"] >= n0:
            by = y1 + tl["m"] * (tl["brk"] - x1)
            ax.annotate("TL break", xy=(tl["brk"], by), xytext=(tl["brk"] - 5, by + (1 if tl["type"] == "down" else -1) * 0.9 * sig["atr"]),
                        color=tc_, fontsize=8, fontweight="bold",
                        arrowprops=dict(arrowstyle="->", color=tc_, lw=0.8), clip_on=True)

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
        hline(e, "#e6edf3", f"{'L' if sig.get('pending') else 'E'}{i + 1}", "--")
    if sl_clip:
        ax.annotate(f"SL {fp(sig['sl'])}  (خیلی دورتر، {abs(sig['sl']-e[0])/e[0]*100:.0f}% از Entry1)",
                    xy=(right - 4, hi - pad * 1.2), xytext=(right - 4, hi - pad * 1.2),
                    color=DN, fontsize=8, fontweight="bold", ha="right", va="top",
                    arrowprops=None, clip_on=True)
        ax.annotate("", xy=(right - 4, hi), xytext=(right - 4, hi - pad),
                    arrowprops=dict(arrowstyle="-|>", color=DN, lw=1.6))
    else:
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
    tname = ("PUMP-FADE" if sig["kind"] == "GRID" else
             "SPIKE HUNTER" if sig["kind"] in ("SPIKE", "HUNT") else
             ("SCALP" if tf in ("15m", "30m") else "SWING"))
    fig.suptitle(f"#{base}/USDT  {tf}  {'LONG' if long_ else 'SHORT'}  |  {tname}  |  {CFG.label} Futures  |  Score {info['score']}/{MAX_SCORE}",
                 color="#ffffff", fontsize=13, fontweight="bold", x=0.06, ha="left", y=0.965)
    setup_name = strat_name(sig)
    strat = (f"Strategy: {setup_name}  +  MTF trend ({h1}: {tr(sig['t1'])}, {h2}: {tr(sig['t2'])})  +  "
             f"Volume (impulse x{sig['imp_vol']:.1f}, trigger x{sig['trg_vol']:.1f})  +  S/R & key levels  +  BOS"
             + ("  +  Trendline" if sig["kind"] == "FVG" and sig.get("tl_desc") else "")
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


def trade_type(tf, sig):
    """نوع ترید و مدت نگهداری تخمینی"""
    if sig["kind"] == "GRID":
        return "پامپ‌فید 🩸 (Pump-Fade Grid)", "چند ساعت تا چند روز (تا شکل‌گیری اصلاح)"
    if sig["kind"] in ("SPIKE", "HUNT"):
        return "اسپایک‌یاب 🎯 (Spike Hunter)", "چند کندل تا چند ساعت"
    if tf in ("15m", "30m"):
        return "اسکالپ ⚡ (Scalp)", "چند دقیقه تا چند ساعت"
    return "نوسان‌گیری 🌊 (Swing)", "چند ساعت تا ۱ الی ۲ روز"


STRAT_NAME = {"FVG": "FVG Retest", "SPIKE": "Spike Reversal (Limit Ladder)",
              "HUNT": "Spike Hunter (Liquidity Limit)", "GRID": "Pump-Fade Grid (fixed-ratio DCA short)"}


def strat_name(sig):
    if sig["kind"] == "TL":
        return f"Trendline {sig['tl_kind'].capitalize()}"
    return STRAT_NAME[sig["kind"]]


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
    ttype, hold = trade_type(tf, sig)
    L.append(f"🏷 نوع ترید: {ttype}")
    L.append(f"⏳ مدت نگهداری تخمینی: {hold}")
    if info.get("candle_time"):
        L.append(f"🕒 کندل تایید: {info['candle_time']} UTC")
    L.append("")
    pend = sig.get("pending")
    L.append(" سفارش‌های Limit در این محدوده" if pend else " در این محدوده")
    L.append("")
    is_grid = sig["kind"] == "GRID"
    for i in range(3):
        if is_grid:
            tag = "Entry" if i == 0 else "Limit"
        else:
            tag = "Limit" if pend else "Entry"
        L.append(f"✅{tag} {i + 1} : {fp(e[i])}")
        L.append("")
    if is_grid:
        L.append("⏳ Entry 1 تقریباً قیمت لحظه‌ای است. Limit 2 و 3 فقط اگر قیمت باز هم پامپ کرد پر می‌شوند (پله‌گذاری در خلاف پامپ)")
        L.append("")
    elif pend:
        L.append(f"⏳ سفارش Limit است؛ اگر تا {sig['valid']} کندل ({tf}) به Limit 1 نرسید یا قیمت به TP1 رسید، لغو کنید")
        L.append("")
    icons = ["🎯", "🚀", "💸"]
    for i in range(3):
        L.append(f"Tp {i + 1} : {roi(sig['tps'][i], e[0])}% {icons[i]}  ({fp(sig['tps'][i])})")
    L.append("")
    L.append(f"Stop Loss : {fp(sig['sl'])} ⛔️📉  (ریسک {risk_pct:.1f}% قیمت)")
    liq_pct = 100 / max(CFG.real_leverage, 1)
    if sig.get("pending") and risk_pct > liq_pct:
        safe_sl = e[0] * (1 + liq_pct / 100 * 0.7) if not long_ else e[0] * (1 - liq_pct / 100 * 0.7)
        L.append(f"⚠️ هشدار: با اهرم واقعی {CFG.real_leverage}×، حرکت {liq_pct:.0f}% خلاف جهت یعنی لیکویید؛ "
                 f"این SL ({risk_pct:.0f}%) عملاً قابل لمس نیست. SL امن‌تر پیشنهادی: {fp(safe_sl)}")
    L.append("")
    L.append(f"با نیم الی یک درصد سرمایه با اهرم {lev}× وارد شوید 🏦📉")
    L.append("")
    L.append("1️⃣هر پله ۱ درصد سرمایه")
    L.append("")
    L.append(f"تی پی ها با اهرم {lev}× محاسبه شده")
    L.append("")
    if sig["kind"] == "FVG":
        L.append(f"با تایید ریتست FVG {'صعودی' if long_ else 'نزولی'} ({fp(sig['zone'][0])} - {fp(sig['zone'][1])})")
    elif sig["kind"] == "TL":
        L.append(f"با تایید {sig['tl_desc']} ({fp(sig['zone'][0])} - {fp(sig['zone'][1])})")
    elif sig["kind"] == "GRID":
        L.append(f"🩸 {sig['grid_desc']}")
    else:
        L.append(f"🎯 {sig['spike_desc']}")
    L.append("")
    L.append("📊 تحلیل:")
    L.append(f"• روند: {h1} {trend_txt(sig['t1'])} | {h2} {trend_txt(sig['t2'])}")
    if sig["kind"] == "FVG" and sig.get("tl_desc"):
        L.append(f"• ترندلاین: {sig['tl_desc']}")
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
    L.append(f"• امتیاز سیگنال: {info['score']}/{MAX_SCORE}")
    if sig["kind"] == "GRID":
        L.append(f"🧠 استراتژی: {strat_name(sig)} + فیلتر رشد قیمت/RSI + MTF Trend")
    else:
        L.append(f"🧠 استراتژی: {strat_name(sig)} + MTF Trend + Volume + S/R + BOS + Trendline")
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
_LIVE, _LIVE_LOCK = {}, threading.Lock()


def fetch_live(sym, tf, limit):
    """کش کوتاه‌مدت داخل یک اسکن: تایم‌فریم‌های بالاتر برای چند تایم‌فریم مشترک‌اند."""
    key, now = (sym, tf, limit), time.time()
    with _LIVE_LOCK:
        e = _LIVE.get(key)
        if e and now - e[0] < 120:
            return e[1]
    df = fetch_klines(sym, tf, limit)
    with _LIVE_LOCK:
        _LIVE[key] = (now, df)
    return df


def analyze_symbol(sym, tf, kinds=None):
    """لیستی از (sig, info, message, candle_time, ctx) برمی‌گرداند. kinds محدود می‌کند کدام نوع سیگنال بررسی شود."""
    light = kinds == frozenset({"GRID"})   # اسکن سبک برای پاس گسترده‌ی کل بازار: بدون بک‌تست/TradingView سنگین
    h1, h2 = MTF_MAP[tf]
    L = fetch_live(sym, tf, LIVE_BARS)
    if len(L) < 120:
        return []
    P = prep(L)
    H1 = htf_pack(fetch_live(sym, h1, LIVE_BARS), h1)
    H2 = htf_pack(fetch_live(sym, h2, LIVE_BARS), h2)

    # آخرین کندل بسته + (SCAN_BACK) کندل قبلی؛ اگر اجرای زمان‌بندی‌شده کمی دیر شد، کندل از دست نرود
    now_ms = time.time() * 1000
    cand = None
    for off in range(int(CFG.scan_back) + 1):
        t = len(L) - 1 - off
        tc = int(P["t"][t]) + TF_MS[tf]
        if now_ms - tc > CFG.max_age_min * 60_000:
            break
        t1, t2 = trend_at(H1, tc), trend_at(H2, tc)
        if (t1 or t2) and detect_all(P, t, t1, t2, kinds=kinds):         # پیش‌فیلتر سریع
            cand = (t, tc, t1, t2)
            break
    if cand is None:
        return []
    t, tc, t1, t2 = cand

    # سطوح تایم‌فریم‌های بالاتر + سقف/کف دیروز
    d1 = fetch_live(sym, "1d", 30)
    extra, base_info = [], {}
    hd = H1["df"].tail(300)
    extra += pivot_levels(hd.high.values, hd.low.values, P["atr"][t] * 2)
    if len(d1) >= 2:
        base_info["pdh"], base_info["pdl"] = float(d1.high.iloc[-1]), float(d1.low.iloc[-1])
        extra += [base_info["pdh"], base_info["pdl"]]
    base_info["candle_time"] = datetime.fromtimestamp(tc / 1000, timezone.utc).strftime("%H:%M")
    key, fa = session_of(tc)
    base_info["session_fa"] = fa
    px = last_price(sym)
    tv_cache = {}
    sym_trades = [] if light else get_trades(sym, tf)
    breaks = find_breaks(P, t)
    out = []

    for sig in detect_all(P, t, t1, t2, extra, kinds=kinds):
        if sig["base"] < CFG.base_min:
            continue
        info = dict(base_info)
        grp = kind_group(sig["kind"])
        e1_, R_, sd = sig["entries"][0], sig["R"], sig["side"]

        # اگر قیمت الان از محدوده‌ی ورود دور شده یا SL خورده، سیگنال کهنه است
        if px:
            if sig["kind"] == "HUNT":
                if not (px > e1_ > sig["sl"] if sd == 1 else px < e1_ < sig["sl"]):
                    continue
            elif sig.get("pending"):
                if not (sig["sl"] < px < sig["tps"][0] if sd == 1 else sig["tps"][0] < px < sig["sl"]):
                    continue
            else:
                if sd == 1 and not (sig["sl"] < px <= e1_ + 0.5 * R_):
                    continue
                if sd == -1 and not (e1_ - 0.5 * R_ <= px < sig["sl"]):
                    continue

        score = sig["base"]
        if not light:
            if tv_cache.get("v") is None:
                tv_cache["v"] = tv_rating(sym, tf) or ""
            tv = tv_cache["v"]
            info["tv"] = tv or None
            if tv:
                if ("BUY" if sd == 1 else "SELL") in tv:
                    score += 1
                elif CFG.tv_veto and ("SELL" if sd == 1 else "BUY") in tv and tv.startswith("STRONG"):
                    continue                      # تریدینگ‌ویو کاملاً مخالف است

        wr, n = winrate(by_group(sym_trades, grp)) if sym_trades else (None, 0)
        if n >= 8:
            info.update(wr=wr, wr_n=n, wr_src="همین ارز")
        else:
            pw, pn = winrate(pooled(tf, None, grp))
            if pn >= 15:
                info.update(wr=pw, wr_n=pn, wr_src="میانگین همه ارزها")
        sw, sn = winrate(pooled(tf, key, grp))
        if sn >= 10:
            info.update(sess_wr=sw, sess_n=sn)
            if sw >= 0.55:
                score += 1
        info["score"] = score
        if score < CFG.min_score:
            continue
        info["breaks"] = breaks
        out.append((sig, info, build_message(sym, tf, sig, info), int(P["t"][t]), dict(P=P, t=t)))
    return out


def scan_tf(tf, dry=False):
    age_min = (time.time() * 1000 % TF_MS[tf]) / 60000        # چند دقیقه از بسته شدن آخرین کندل گذشته
    if age_min > CFG.max_age_min:
        log.info("skip %s (last candle closed %.0f min ago)", tf, age_min)
        return
    with _STATS_LOCK:
        state = jload(STATE_PATH, {"sent": {}})
    sent = state["sent"]
    cutoff = (time.time() - 3 * 86400) * 1000                  # پاک‌سازی سابقه‌ی قدیمی‌تر از ۳ روز
    for k in [k for k, v in sent.items() if v < cutoff]:
        del sent[k]

    def work(sym, kinds=None):
        try:
            return sym, analyze_symbol(sym, tf, kinds=kinds)
        except Exception as e:  # noqa
            log.warning("%s %s: %s", sym, tf, e)
            return sym, []

    with ThreadPoolExecutor(max_workers=int(CFG.workers)) as ex:
        results = list(ex.map(work, CFG.symbols))

    n_full = len(CFG.symbols)
    if CFG.grid_on and CFG.exchange == "kcex":
        try:
            cands = [s for s in fetch_grid_candidates() if s not in CFG.symbols]
        except Exception as e:  # noqa
            log.warning("grid candidates fetch failed: %s", e)
            cands = []
        if cands:
            grid_kinds = frozenset({"GRID"})
            with ThreadPoolExecutor(max_workers=int(CFG.workers)) as ex:
                results += list(ex.map(lambda s: work(s, kinds=grid_kinds), cands))
            n_full += len(cands)

    n_sent = 0
    for sym, res in results:
        for sig, info, msg, tt, ctx in res:
            grp = kind_group(sig["kind"])
            lvl = f"|{sig['hunt_level']:.6g}" if sig["kind"] == "HUNT" else ""
            k = f"{sym}|{tf}|{sig['side']}|{grp}{lvl}"
            cd = 16 if sig["kind"] == "HUNT" else (40 if sig["kind"] == "GRID" else CFG.cooldown_candles)
            last = sent.get(k, 0)
            if tt == last or (tt - last) < cd * TF_MS[tf]:
                continue
            log.info("SIGNAL %s %s %s side=%s score=%s", sym, tf, sig["kind"], sig["side"], info["score"])
            png = None
            if CFG.send_chart:
                try:
                    png = make_chart(sym, tf, ctx["P"], ctx["t"], sig, info, info["breaks"])
                except Exception as e:  # noqa
                    log.error("chart failed %s: %s", sym, e)
            base = sym[:-4] if sym.endswith("USDT") else sym
            label, _ = trade_type(tf, sig)
            cap = f"📈 چارت #{base} | {tf} | {label}"
            if dry:
                print("\n" + "=" * 40 + "\n" + msg + "\n" + "=" * 40)
                if png:
                    os.makedirs(os.path.join(HERE, "charts"), exist_ok=True)
                    fn = os.path.join(HERE, "charts", f"{sym}_{tf}_{sig['kind']}_{tt}.png")
                    open(fn, "wb").write(png)
                    print("chart saved:", fn)
            elif not broadcast(msg, png, cap):
                continue
            sent[k] = tt
            n_sent += 1
            with _STATS_LOCK:
                cur = jload(STATE_PATH, {"sent": {}})
                cur["sent"].update(sent)
                jsave(STATE_PATH, cur)
    log.info("scan %s done: %d symbols, %d signals", tf, n_full, n_sent)


def run(dry=False):
    refresh_universe()
    log.info("bot started | symbols=%d | dry=%s", len(CFG.symbols), dry)
    if not dry and CFG.chat_ids:
        try:
            for cid in CFG.chat_ids:
                tg_send(f"✅ ربات سیگنال 4ux Ai فعال شد\n📊 {CFG.label} Futures | تایم‌فریم 15m و 1h\n🔎 ارزها: "
                        + ", ".join(x[:-4] for x in CFG.symbols), cid)
        except Exception as e:  # noqa
            log.error("startup message failed: %s", e)
    last = {tf: None for tf in SIGNAL_TFS}
    last_universe = time.time()
    while True:
        now_ms = time.time() * 1000
        if time.time() - last_universe > 1800:      # هر ۳۰ دقیقه لیست ارزها را تازه کن
            refresh_universe()
            last_universe = time.time()
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


def refresh_universe():
    """اگر AUTO_UNIVERSE فعال باشد، لیست ارزها را از صعودی‌ترین‌های بازار صرافی به‌روزرسانی می‌کند."""
    if not CFG.auto_universe:
        return
    try:
        if CFG.exchange == "kcex":
            top = fetch_kcex_gainers(int(CFG.universe_size))
        else:
            log.info("AUTO_UNIVERSE only implemented for EXCHANGE=kcex; keeping fixed SYMBOLS list")
            return
        if top:
            CFG.symbols = top
            log.info("universe refreshed: %d symbols from %s", len(top), CFG.label)
    except Exception as e:  # noqa
        log.warning("universe refresh failed, keeping previous symbol list: %s", e)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv
    cmd = args[0] if args else "run"
    if cmd == "run":
        run(dry)
    elif cmd == "once":
        refresh_universe()
        _LIVE.clear()
        with ThreadPoolExecutor(max_workers=len(SIGNAL_TFS)) as ex:
            list(ex.map(lambda tf: scan_tf(tf, dry), SIGNAL_TFS))
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
اتاق شکار 🏹 — Signal Bot v3 (Trendline Break Short + Pending Retest + فیلترهای ضدترند)
------------------------------------------------------------------------------------------
چرا این نسخه ساخته شد:
  نسخه‌ی قبلی گاهی وقتی بازار واقعاً در یک روند صعودی قوی بود هم سیگنال شورت
  می‌داد و SL می‌خورد. این نسخه چند فیلتر سخت‌گیرانه‌ی اضافه دارد که هدفشان
  دقیقاً جلوگیری از همین حالت است (نه حذف کامل ریسک؛ هیچ استراتژی‌ای این
  تضمین را نمی‌دهد):

  1) فیلتر روند تایم‌فریم بالاتر (HTF): اگر تایم‌فریم بالاتر (1h برای سیگنال‌های
     15m، 4h برای سیگنال‌های 1h) خودش در روند صعودی قوی باشد، اصلاً سیگنال
     شورت صادر نمی‌شود — چون فید کردن یک پامپ در دل یک روند صعودی واقعی،
     دقیقاً همان چیزی است که باعث خوردن SL می‌شود.
  2) واگرایی نزولی RSI و/یا کندل بازگشتی (پین‌بار / انگالف نزولی) در نقطه‌ی
     سقف پامپ: حداقل یکی از این دو باید تایید کند که مومنتوم صعودی واقعاً
     ضعیف شده، نه اینکه فقط قیمت مکث کرده.
  3) کنسل‌ زودهنگام: اگر بعد از صدور سیگنال معلق، قیمت با یک کندل قاطع
     (بسته‌شدن قطعی) بالاتر از سقف برود، سیگنال به‌جای رسیدن به SL واقعی
     همان لحظه «کنسل شده❌» اعلام می‌شود.
  4) فعال‌سازی بر اساس کندل، نه فقط برخورد لحظه‌ای قیمت: وقتی قیمت به نقطه‌ی
     ورود می‌رسد، تنها اگر همان کندل با رد شدن (Rejection) بسته شود سیگنال
     «فعال شده» اعلام می‌شود؛ اگر کندل بالای نقطه ورود بسته شود، به‌جای
     فعال‌سازی، بررسی کنسل‌شدن انجام می‌شود.

استراتژی پایه (ری‌تست شکست ترندلاین):
  پامپ شناسایی می‌شود -> ترندلاین از دو سقف پیوتال پامپ رسم می‌شود -> شکست
  تاییدشده‌ی این خط به سمت پایین رصد می‌شود -> Entry به‌صورت سفارش معلق روی
  سقف همان پامپ گذاشته می‌شود (منتظر ری‌تست) -> با لمس + تایید کندلی، سیگنال
  فعال و TP/SL دنبال می‌شود.

وضعیت هر سیگنال به‌صورت پیام جداگانه‌ی Pin‌شونده زیر همان سیگنال منتشر می‌شود:
  🦭 فعال نشده -> ✅💯 فعال شده -> بسته شده با سود / بسته شده با ضرر
  یا در هر مرحله -> ❌ کنسل شده

گزارش‌ها (به وقت تهران):
  - هر روز ساعت 23:59: گزارش عملکرد همان روز
  - هر جمعه ساعت 10:00: گزارش عملکرد کل هفته

دستورات:
  python bot.py run             # اجرای دائمی (برای سرور/VPS)
  python bot.py once            # یک‌بار اسکن + مانیتور + چک گزارش‌ها (برای GitHub Actions/کرون)
  python bot.py check           # تست اتصال صرافی + تلگرام (پیام واقعی نمی‌فرستد)
  python bot.py setup           # ویزارد تنظیم توکن و chat id
  python bot.py test-telegram   # ارسال پیام تست واقعی به تلگرام
  python bot.py daily-report    # ارسال دستی گزارش روزانه (تست)
  python bot.py weekly-report   # ارسال دستی گزارش هفتگی (تست)
  python bot.py demo-check      # تست اتصال به OKX Demo Trading (فقط خواندن موجودی)
  python bot.py install-service # سرویس systemd برای اجرای دائمی روی سرور

افزودن --dry یعنی ارسال نکن، فقط چاپ کن.
"""
import os, sys, json, math, time, logging, getpass
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))   # ایران از ۲۰۲۲ ساعت تابستانی ندارد


def now_tehran():
    return datetime.now(TEHRAN_TZ)


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
    pin_status = os.getenv("PIN_STATUS", "1") == "1"

    exchange = os.getenv("EXCHANGE", "okx").lower()          # okx | binance
    okx_base = os.getenv("OKX_BASE", "https://www.okx.com")
    fapi_base = os.getenv("BINANCE_FAPI_BASE", "https://fapi.binance.com")
    futures_only = os.getenv("FUTURES_ONLY", "1") == "1"
    label = "OKX" if exchange == "okx" else "Binance"

    # --- اتصال اختیاری به حساب Demo Trading خودِ OKX (پیش‌فرض خاموش) ---
    # وقتی روشن باشد، به‌محض «فعال شدن» هر سیگنال، یک معامله‌ی واقعی روی حساب
    # دمو (پول فرضی، قیمت واقعی) باز می‌شود تا بازدهی را مستقیم در اپ OKX ببینید.
    okx_demo_trading = os.getenv("OKX_DEMO_TRADING", "0") == "1"
    okx_api_key = os.getenv("OKX_API_KEY", "")
    okx_api_secret = os.getenv("OKX_API_SECRET", "")
    okx_api_passphrase = os.getenv("OKX_API_PASSPHRASE", "")
    okx_demo_margin_usdt = _f("OKX_DEMO_MARGIN_USDT", 5.0)   # مارجین هر معامله‌ی دمو (دلار فرضی)
    okx_demo_td_mode = os.getenv("OKX_DEMO_TD_MODE", "cross")  # cross | isolated

    brand_name = os.getenv("BRAND_NAME", "🏹 اتاق شکار")
    handle = os.getenv("CHANNEL_HANDLE", "@signallroom")

    symbols = [s.strip().upper() for s in os.getenv(
        "SYMBOLS",
        "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,DOTUSDT,"
        "LTCUSDT,ATOMUSDT,NEARUSDT,APTUSDT,ARBUSDT,OPUSDT,SUIUSDT,INJUSDT,TRXUSDT,TONUSDT,"
        "MATICUSDT,FILUSDT,ETCUSDT,ICPUSDT,AAVEUSDT,UNIUSDT,RUNEUSDT,SANDUSDT,MANAUSDT,GALAUSDT,"
        "FTMUSDT,ALGOUSDT,VETUSDT,EOSUSDT,XLMUSDT,XTZUSDT,THETAUSDT,CHZUSDT,ENJUSDT,ZILUSDT,"
        "1000PEPEUSDT,1000SHIBUSDT,1000FLOKIUSDT,WIFUSDT,ORDIUSDT,SEIUSDT,TIAUSDT,STXUSDT,IMXUSDT,DYDXUSDT,"
        "LDOUSDT,GMXUSDT,SNXUSDT,CRVUSDT,COMPUSDT,MKRUSDT,YFIUSDT,1INCHUSDT,KAVAUSDT,MINAUSDT,"
        "ROSEUSDT,ARUSDT,FLOWUSDT,KSMUSDT,WAVESUSDT,QNTUSDT,GRTUSDT,BATUSDT,ZRXUSDT,RSRUSDT,"
        "CFXUSDT,ONEUSDT,HBARUSDT,EGLDUSDT,ANKRUSDT,IOTAUSDT,NEOUSDT,DASHUSDT,ZECUSDT,XMRUSDT"
    ).split(",") if s.strip()]

    # به‌جای/علاوه‌بر لیست بالا، به‌صورت خودکار پرحجم‌ترین جفت‌های Futures صرافی را
    # هم اضافه می‌کند — یعنی هر روز خودش با ارزهای داغ‌تر تطبیق پیدا می‌کند.
    auto_universe = os.getenv("AUTO_UNIVERSE", "1") == "1"
    universe_size = _f("UNIVERSE_SIZE", 80)

    leverage = _f("LEVERAGE", 20)
    max_sl_pct = _f("MAX_SL_PCT", 3.0) / 100
    scan_back = _f("SCAN_BACK", 1)
    max_age_min = _f("MAX_AGE_MIN", 25)
    cooldown_candles = _f("COOLDOWN_CANDLES", 6)
    workers = _f("WORKERS", 8)   # تعداد رشته‌های موازی برای اسکن هم‌زمان چند ارز (سرعت را چند برابر می‌کند)

    # --- پارامترهای پایه‌ی استراتژی «شکست ترندلاین بعد از پامپ» ---
    pump_lookback = _f("PUMP_LOOKBACK", 120)
    pump_min_bars = _f("PUMP_MIN_BARS", 6)
    pump_min_pct = _f("PUMP_MIN_PCT", 6.0) / 100
    pivot_left = _f("PIVOT_LEFT", 2)
    pivot_right = _f("PIVOT_RIGHT", 2)
    break_buffer_atr = _f("BREAK_BUFFER_ATR", 0.15)
    sl_buffer_atr = _f("SL_BUFFER_ATR", 0.5)
    retest_buffer_atr = _f("RETEST_BUFFER_ATR", 0.3)
    cancel_buffer_atr = _f("CANCEL_BUFFER_ATR", 1.0)   # عبور قاطع از این فاصله بالای سقف = کنسل
    enable_long = os.getenv("ENABLE_LONG", "0") == "1"

    # --- فیلترهای ضدترند (جدید) ---
    require_htf_filter = os.getenv("REQUIRE_HTF_FILTER", "1") == "1"   # ممنوعیت شورت روی روند صعودی قوی تایم بالاتر
    require_confirmation = os.getenv("REQUIRE_CONFIRMATION", "1") == "1"  # الزام واگرایی یا کندل بازگشتی
    volume_climax_mult = _f("VOLUME_CLIMAX_MULT", 1.2)
    min_score = _f("MIN_SCORE", 7)      # از 10 (سخت‌گیرانه‌تر از قبل) — این کم نشد، فقط دامنه‌ی جستجو زیاد شد

    # --- ردیابی سیگنال‌های فعال ---
    monitor_interval_sec = _f("MONITOR_INTERVAL_SEC", 45)
    pending_max_days = _f("PENDING_MAX_DAYS", 3.0)

    # --- گزارش‌ها (به وقت تهران) ---
    daily_report_hour = _f("DAILY_REPORT_HOUR", 23)
    daily_report_minute = _f("DAILY_REPORT_MINUTE", 59)
    weekly_report_dow = _f("WEEKLY_REPORT_DOW", 4)         # 0=دوشنبه ... 4=جمعه
    weekly_report_hour = _f("WEEKLY_REPORT_HOUR", 10)
    weekly_report_minute = _f("WEEKLY_REPORT_MINUTE", 0)

    stats_ttl_h = _f("STATS_TTL_HOURS", 12)


TF_MS = {
    "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000, "1d": 86_400_000,
    "3d": 259_200_000, "1w": 604_800_000, "1M": 2_592_000_000,  # 1M تقریبی (۳۰ روز)
}
MTF_MAP = {
    "5m": ("15m", "1h"),
    "15m": ("1h", "4h"),
    "30m": ("2h", "4h"),
    "1h": ("4h", "1d"),
    "2h": ("6h", "1d"),
    "4h": ("1d", "1w"),
    "6h": ("1d", "1w"),
    "12h": ("1d", "1w"),
    "1d": ("1w", "1M"),
    "3d": ("1w", "1M"),
    "1w": ("1M", None),
    "1M": (None, None),
}
SIGNAL_TFS = ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "3d", "1w", "1M")

# پارامترهایی که منطقاً باید متناسب با طول تایم‌فریم فرق کنند: هرچه تایم‌فریم
# بزرگ‌تر، هم پامپ‌ها به‌طور طبیعی درصد بزرگ‌تری دارند، هم فاصله‌ی منطقی SL
# بزرگ‌تر است، هم صبر برای لمس نقطه‌ی ورود باید بیشتر باشد.
# نکته: این جدول‌ها گسترده شدند تا سیگنال بیشتری رد نشود، ولی همان معیارهای
# کیفی (امتیاز حداقل CFG.min_score، فیلتر روند بالاتر، واگرایی/کندل بازگشتی)
# برای همه‌ی تایم‌فریم‌ها بدون استثنا اجرا می‌شود — یعنی دقت سیگنال کم نشده،
# فقط دامنه‌ی جستجو (تعداد ارز × تعداد تایم‌فریم) زیاد شده است.
PUMP_LOOKBACK_BY_TF = {
    "5m": 130, "15m": 120, "30m": 110, "1h": 120, "2h": 110, "4h": 100,
    "6h": 100, "12h": 95, "1d": 90, "3d": 70, "1w": 52, "1M": 24,
}
MAX_SL_PCT_BY_TF = {          # درصد
    "5m": 2.0, "15m": 3.0, "30m": 3.5, "1h": 4.0, "2h": 4.5, "4h": 5.0,
    "6h": 6.0, "12h": 7.0, "1d": 8.0, "3d": 10.0, "1w": 12.0, "1M": 18.0,
}
PENDING_MAX_DAYS_BY_TF = {
    "5m": 1, "15m": 3, "30m": 4, "1h": 5, "2h": 7, "4h": 10,
    "6h": 13, "12h": 16, "1d": 20, "3d": 35, "1w": 60, "1M": 180,
}

_ENV_PUMP_LOOKBACK = os.getenv("PUMP_LOOKBACK")
_ENV_MAX_SL_PCT = os.getenv("MAX_SL_PCT")
_ENV_PENDING_MAX_DAYS = os.getenv("PENDING_MAX_DAYS")


def pump_lookback_for(tf):
    if _ENV_PUMP_LOOKBACK is not None:
        return int(_ENV_PUMP_LOOKBACK)
    return PUMP_LOOKBACK_BY_TF.get(tf, 120)


def max_sl_pct_for(tf):
    if _ENV_MAX_SL_PCT is not None:
        return float(_ENV_MAX_SL_PCT) / 100
    return MAX_SL_PCT_BY_TF.get(tf, 3.0) / 100


def pending_max_days_for(tf):
    if _ENV_PENDING_MAX_DAYS is not None:
        return float(_ENV_PENDING_MAX_DAYS)
    return PENDING_MAX_DAYS_BY_TF.get(tf, 3)

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

OKX_BAR = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "2h": "2H", "4h": "4H",
           "6h": "6H", "12h": "12H", "1d": "1Dutc", "3d": "3Dutc", "1w": "1Wutc", "1M": "1Mutc"}


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
# جهان قابل‌اسکن (لیست دستی + Auto Universe) و کش‌های سبک درون‌اجرا
# ----------------------------------------------------------------------------
def fetch_okx_top_universe(size):
    """پرحجم‌ترین جفت‌های Futures-USDT روی OKX را بر اساس حجم ۲۴ ساعته برمی‌گرداند."""
    r = SESSION.get(CFG.okx_base.rstrip("/") + "/api/v5/market/tickers", params={"instType": "SWAP"}, timeout=15)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != "0":
        raise RuntimeError(f"okx tickers: {j.get('msg')}")
    rows = []
    for d in j.get("data", []):
        inst = d.get("instId", "")
        if not inst.endswith("-USDT-SWAP"):
            continue
        try:
            vol = float(d.get("volCcy24h") or 0)
        except Exception:  # noqa
            vol = 0.0
        rows.append((inst.replace("-USDT-SWAP", "") + "USDT", vol))
    rows.sort(key=lambda x: -x[1])
    return [s for s, _ in rows[:size]]


_SCAN_SYMBOLS_CACHE = None


def get_scan_symbols():
    """لیست نهایی ارزها برای اسکن: ارزهای دستی/ثابت + (در صورت فعال بودن) پرحجم‌ترین‌های زنده‌ی صرافی.
    فقط یک‌بار در هر اجرا محاسبه می‌شود (کش در حافظه)."""
    global _SCAN_SYMBOLS_CACHE
    if _SCAN_SYMBOLS_CACHE is not None:
        return _SCAN_SYMBOLS_CACHE
    syms = list(dict.fromkeys(CFG.symbols))   # حفظ ترتیب + حذف تکراری
    if CFG.auto_universe and CFG.exchange == "okx":
        try:
            top = fetch_okx_top_universe(CFG.universe_size)
            for s in top:
                if s not in syms:
                    syms.append(s)
            log.info("auto universe: %d ارز اضافه شد (مجموع %d)", len(top), len(syms))
        except Exception as e:  # noqa
            log.warning("auto universe fetch failed, فقط لیست دستی استفاده می‌شود: %s", e)
    _SCAN_SYMBOLS_CACHE = syms
    return syms


# --- کش سبک برای جلوگیری از فراخوانی تکراری API در یک اجرا (وقتی چند تایم‌فریم
#     سیگنال از یک تایم‌فریم بالاتر مشترک استفاده می‌کنند) ---
_HTF_CACHE = {}
_DAILY_LEVELS_CACHE = {}


def get_htf_cached(symbol, htf_tf):
    key = (symbol, htf_tf)
    if key in _HTF_CACHE:
        return _HTF_CACHE[key]
    try:
        hd = fetch_klines(symbol, htf_tf, 300)
        val = (htf_pack(hd, htf_tf), hd)
    except Exception as e:  # noqa
        log.warning("htf fetch failed %s %s: %s", symbol, htf_tf, e)
        val = (None, None)
    _HTF_CACHE[key] = val
    return val


def get_daily_levels_cached(symbol):
    if symbol in _DAILY_LEVELS_CACHE:
        return _DAILY_LEVELS_CACHE[symbol]
    try:
        d1 = fetch_klines(symbol, "1d", 3)
        val = [float(d1.high.iloc[-1]), float(d1.low.iloc[-1])] if len(d1) >= 2 else []
    except Exception:  # noqa
        val = []
    _DAILY_LEVELS_CACHE[symbol] = val
    return val


# ----------------------------------------------------------------------------
# اتصال اختیاری به OKX Demo Trading — باز کردن معامله‌ی واقعی روی حساب دمو
# ----------------------------------------------------------------------------
import base64
import hmac
import hashlib


def okx_ts():
    n = datetime.now(timezone.utc)
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}Z"


def okx_signed(method, path, body=None):
    """درخواست امضاشده به OKX (برای حساب واقعی یا دمو، بسته به x-simulated-trading)."""
    if not (CFG.okx_api_key and CFG.okx_api_secret and CFG.okx_api_passphrase):
        raise RuntimeError("OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE تنظیم نشده")
    body_str = json.dumps(body) if body else ""
    ts = okx_ts()
    prehash = ts + method.upper() + path + body_str
    sign = base64.b64encode(
        hmac.new(CFG.okx_api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
    ).decode()
    headers = {
        "OK-ACCESS-KEY": CFG.okx_api_key,
        "OK-ACCESS-SIGN": sign,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": CFG.okx_api_passphrase,
        "Content-Type": "application/json",
    }
    if CFG.okx_demo_trading:
        headers["x-simulated-trading"] = "1"   # این هدر یعنی سفارش روی محیط دمو اجرا می‌شود، نه حساب واقعی
    url = CFG.okx_base.rstrip("/") + path
    r = SESSION.request(method, url, headers=headers,
                         data=body_str if body is not None else None, timeout=15)
    r.raise_for_status()
    j = r.json()
    if j.get("code") not in ("0", 0):
        raise RuntimeError(f"okx {path}: {j}")
    return j.get("data")


def okx_inst_id(symbol):
    return (symbol[:-4] + "-USDT-SWAP") if symbol.endswith("USDT") else symbol


_INST_SPEC_CACHE = {}


def get_inst_spec(inst_id):
    if inst_id in _INST_SPEC_CACHE:
        return _INST_SPEC_CACHE[inst_id]
    r = SESSION.get(CFG.okx_base.rstrip("/") + "/api/v5/public/instruments",
                     params={"instType": "SWAP", "instId": inst_id}, timeout=15)
    r.raise_for_status()
    d = r.json()["data"][0]
    spec = dict(ctVal=float(d["ctVal"]), lotSz=float(d["lotSz"]), minSz=float(d["minSz"]))
    _INST_SPEC_CACHE[inst_id] = spec
    return spec


def compute_contracts(inst_id, margin_usdt, leverage, price):
    spec = get_inst_spec(inst_id)
    notional = margin_usdt * leverage
    raw = notional / (price * spec["ctVal"])
    lot = spec["lotSz"]
    n_lots = math.floor(raw / lot)
    contracts = max(spec["minSz"], n_lots * lot)
    return round(contracts, 8)


def place_demo_trade(symbol, side, sl, tp):
    """وقتی سیگنال فعال می‌شود، این تابع یک معامله‌ی واقعی روی حساب Demo Trading
    خودِ OKX باز می‌کند: ورود با اردر مارکت + یک اردر OCO (خروج با TP یا SL،
    هرکدام زودتر برسد) با کل حجم. برای سادگی و ایمنی، فقط از TP وسط (شماره ۲)
    به‌عنوان هدف OCO استفاده می‌شود؛ اگر می‌خواهید دقیقاً مثل تلگرام سه‌پله‌ای
    باشد، بعداً می‌توان آن را هم اضافه کرد."""
    if not CFG.okx_demo_trading:
        return None
    inst = okx_inst_id(symbol)
    try:
        okx_signed("POST", "/api/v5/account/set-leverage",
                   {"instId": inst, "lever": str(int(CFG.leverage)), "mgnMode": CFG.okx_demo_td_mode})
        px = last_price(symbol)
        if not px:
            return None
        contracts = compute_contracts(inst, CFG.okx_demo_margin_usdt, CFG.leverage, px)
        if contracts <= 0:
            return None

        open_side = "sell" if side == -1 else "buy"
        order = okx_signed("POST", "/api/v5/trade/order", {
            "instId": inst, "tdMode": CFG.okx_demo_td_mode, "side": open_side,
            "ordType": "market", "sz": str(contracts),
        })

        close_side = "buy" if side == -1 else "sell"
        okx_signed("POST", "/api/v5/trade/order-algo", {
            "instId": inst, "tdMode": CFG.okx_demo_td_mode, "side": close_side,
            "ordType": "oco", "sz": str(contracts), "reduceOnly": "true",
            "tpTriggerPx": str(tp), "tpOrdPx": "-1",
            "slTriggerPx": str(sl), "slOrdPx": "-1",
        })
        log.info("demo trade opened %s contracts=%s entry~%s sl=%s tp=%s", inst, contracts, px, sl, tp)
        return {"inst": inst, "contracts": contracts, "entry_px": px, "order": order}
    except Exception as e:  # noqa
        log.error("demo trade failed %s: %s", symbol, e)
        return None


def okx_demo_check():
    """تست اتصال کلیدهای دمو: فقط موجودی را می‌خواند، هیچ سفارشی ثبت نمی‌کند."""
    data = okx_signed("GET", "/api/v5/account/balance")
    return data


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
    """روند EMA50/EMA200 تایم‌فریم بالاتر؛ برای فیلتر «آیا داریم برخلاف یک روند واقعی معامله می‌کنیم؟»"""
    c = df.close
    e50 = c.ewm(span=50, adjust=False).mean()
    e200 = c.ewm(span=200, adjust=False).mean()
    trend = np.where((c > e50) & (e50 > e200), 1, np.where((c < e50) & (e50 < e200), -1, 0))
    return dict(tc=df.time.values + TF_MS[tf], trend=trend)


def trend_at(H, t_close):
    i = int(np.searchsorted(H["tc"], t_close, side="right")) - 1
    return int(H["trend"][i]) if i >= 0 else 0


def pivot_idx(arr, left, right, kind="high"):
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


def _fit_trendline(idxs, prices):
    x = np.array(idxs, dtype=float)
    y = np.array(prices, dtype=float)
    if len(x) < 2:
        return None
    slope, intercept = np.polyfit(x, y, 1)
    return slope, intercept


def upper_wick_ratio(o, h, l, c, i):
    rng = h[i] - l[i]
    if rng <= 0:
        return 0.0
    return (h[i] - max(o[i], c[i])) / rng


def lower_wick_ratio(o, h, l, c, i):
    rng = h[i] - l[i]
    if rng <= 0:
        return 0.0
    return (min(o[i], c[i]) - l[i]) / rng


def is_bearish_reversal(o, h, l, c, i):
    """پین‌بار/شوتینگ‌استار (سایه‌ی بالا بلند) یا انگالف نزولی نسبت به کندل قبل."""
    if upper_wick_ratio(o, h, l, c, i) >= 0.45:
        return True
    if i > 0 and c[i - 1] > o[i - 1] and c[i] < o[i]:      # کندل قبل سبز، این کندل قرمز
        if c[i] <= o[i - 1] and o[i] >= c[i - 1]:          # بدنه‌ی کندل قبل را کامل می‌بلعد
            return True
    return False


def is_bullish_reversal(o, h, l, c, i):
    if lower_wick_ratio(o, h, l, c, i) >= 0.45:
        return True
    if i > 0 and c[i - 1] < o[i - 1] and c[i] > o[i]:
        if c[i] >= o[i - 1] and o[i] <= c[i - 1]:
            return True
    return False


# ----------------------------------------------------------------------------
# استراتژی: شکست ترندلاین بعد از پامپ + نقطه ورود معلق (ری‌تست) + فیلترهای ضدترند
# ----------------------------------------------------------------------------
def detect_trendline_break(P, t, side, extra_levels=(), htf_trend=0, lookback=None, max_sl_pct=None):
    """
    side = -1 : شکست ترندلاین بالای یک پامپ => سیگنال Short (استراتژی اصلی)
    side = +1 : حالت قرینه، شکست ترندلاین زیر یک دامپ => سیگنال Long (اختیاری)
    htf_trend : روند تایم‌فریم بالاتر در لحظه‌ی سیگنال (+1 صعودی قوی، -1 نزولی قوی، 0 خنثی)
    lookback / max_sl_pct : مقادیر اختصاصیِ تایم‌فریم سیگنال (اگر داده نشود، مقدار سراسری CFG استفاده می‌شود)
    """
    lookback = CFG.pump_lookback if lookback is None else int(lookback)
    max_sl_pct = CFG.max_sl_pct if max_sl_pct is None else float(max_sl_pct)
    o, h, l, c, v, vs, atr, rsi = P["o"], P["h"], P["l"], P["c"], P["v"], P["vsma"], P["atr"], P["rsi"]
    a = atr[t]
    if not a > 0 or t < lookback + 5:
        return None

    w0 = max(0, t - lookback)

    if side == -1:
        # فیلتر روند تایم بالاتر: اگر خودِ تایم‌فریم بالاتر صعودی قوی است، این پامپ
        # احتمالاً بخشی از یک روند واقعی است، نه یک نوسان قابل فید — رد کن.
        if CFG.require_htf_filter and htf_trend == 1:
            return None

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
        piv = [i for i in piv if i < t and h[i] >= mid_level]
        if len(piv) < 2:
            return None
        h1, h2 = piv[0], piv[-1]
        if h2 <= h1 or h[h2] < h[h1]:      # باید واقعاً سقف دوم بالاتر/هم‌سطح باشد
            return None

        fit = _fit_trendline([h1, h2], [h[h1], h[h2]])
        if fit is None:
            return None
        slope, intercept = fit
        if slope < -1e-9:
            return None
        line = lambda x: slope * x + intercept

        win_start = max(h2, t - 8)
        recently_respected = any(c[k] >= line(k) - CFG.break_buffer_atr * a for k in range(win_start, t))
        if not recently_respected:
            return None
        if not (c[t] < line(t) - CFG.break_buffer_atr * a):
            return None
        if c[t] <= pump_low + 0.15 * (pump_high - pump_low):
            return None

        # --- تاییدیه‌ی کندلی / واگرایی (الزامی) ---
        bearish_divergence = rsi[h2] <= rsi[h1] - 2.0   # سقف بالاتر با RSI پایین‌تر
        reversal_candle = is_bearish_reversal(o, h, l, c, h2) or is_bearish_reversal(o, h, l, c, t)
        if CFG.require_confirmation and not (bearish_divergence or reversal_candle):
            return None

        ceiling = max(pump_high, h[h2])
        entry = float(ceiling - CFG.retest_buffer_atr * a)
        sl = float(ceiling + CFG.sl_buffer_atr * a)
        cancel_price = float(ceiling + CFG.cancel_buffer_atr * a)
        R = sl - entry
        if R <= 0 or R / entry > max_sl_pct or R / entry < 0.001:
            return None
        if entry <= c[t]:
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
            return None

        near_res = [x for x in extra_levels if entry * 0.995 <= x <= sl]
        vol_ratio = v[t] / vs[t] if vs[t] > 0 else 0
        vol_climax = (v[h2] / vs[h2]) >= CFG.volume_climax_mult if vs[h2] > 0 else False

        comp = dict(
            trendline=2,
            pump=1 if pump_pct >= 2 * CFG.pump_min_pct else 0,
            htf_ok=1 if htf_trend != 1 else 0,
            divergence=1 if bearish_divergence else 0,
            reversal_candle=1 if reversal_candle else 0,
            volume_climax=1 if vol_climax else 0,
            breakout_volume=1 if vol_ratio >= 1.3 else 0,
            red_candle=1 if c[t] < o[t] else 0,
            no_extra_res=1 if not near_res else 0,
        )
        score = sum(comp.values())

        return dict(
            side=-1, entry=entry, sl=sl, tps=tps, R=float(R), atr=float(a),
            cancel_price=cancel_price,
            pump_low=float(pump_low), pump_high=float(pump_high), pump_pct=float(pump_pct),
            line_pts=[(int(h1), float(h[h1])), (int(h2), float(h[h2]))], slope=float(slope),
            rsi=float(rsi[t]), vol_ratio=float(vol_ratio), comp=comp, score=score,
            L=int(L), max_score=len(comp) + 1,   # trendline ارزش ۲ دارد
        )

    else:  # side == +1 (حالت قرینه، اختیاری)
        if CFG.require_htf_filter and htf_trend == -1:
            return None

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
        if l2 <= l1 or l[l2] > l[l1]:
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

        bullish_divergence = rsi[l2] >= rsi[l1] + 2.0
        reversal_candle = is_bullish_reversal(o, h, l, c, l2) or is_bullish_reversal(o, h, l, c, t)
        if CFG.require_confirmation and not (bullish_divergence or reversal_candle):
            return None

        floor_ = min(dump_low, l[l2])
        entry = float(floor_ + CFG.retest_buffer_atr * a)
        sl = float(floor_ - CFG.sl_buffer_atr * a)
        cancel_price = float(floor_ - CFG.cancel_buffer_atr * a)
        R = entry - sl
        if R <= 0 or R / entry > max_sl_pct or R / entry < 0.001:
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
        vol_climax = (v[l2] / vs[l2]) >= CFG.volume_climax_mult if vs[l2] > 0 else False

        comp = dict(
            trendline=2,
            dump=1 if dump_pct >= 2 * CFG.pump_min_pct else 0,
            htf_ok=1 if htf_trend != -1 else 0,
            divergence=1 if bullish_divergence else 0,
            reversal_candle=1 if reversal_candle else 0,
            volume_climax=1 if vol_climax else 0,
            breakout_volume=1 if vol_ratio >= 1.3 else 0,
            green_candle=1 if c[t] > o[t] else 0,
            no_extra_res=1 if not near_res else 0,
        )
        score = sum(comp.values())

        return dict(
            side=1, entry=entry, sl=sl, tps=tps, R=float(R), atr=float(a),
            cancel_price=cancel_price,
            pump_low=float(dump_low), pump_high=float(dump_high), pump_pct=float(dump_pct),
            line_pts=[(int(l1), float(l[l1])), (int(l2), float(l[l2]))], slope=float(slope),
            rsi=float(rsi[t]), vol_ratio=float(vol_ratio), comp=comp, score=score,
            L=int(L), max_score=len(comp) + 1,
        )


# ----------------------------------------------------------------------------
# چارت (اختیاری)
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
    hline(sig["cancel_price"], "#8b949e", "Cancel", ":")
    for i, tp in enumerate(sig["tps"]):
        hline(tp, UP, f"TP{i + 1}")

    lo = min(l.min(), sig["sl"], sig["tps"][-1])
    hi = max(h.max(), sig["sl"], sig["cancel_price"], sig["pump_high"])
    pad = (hi - lo) * 0.06
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(n0 - 1, right + 8)

    base = sym[:-4] if sym.endswith("USDT") else sym
    ax.set_title(
        f"#{base}/USDT {tf} {'SHORT' if short_ else 'LONG'} | {CFG.label} Futures | "
        f"شکست ترندلاین + ری‌تست | Score {sig['score']}/{sig['max_score']}",
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
    L.append(f"❌ کنسل‌شدن سیگنال در صورت بسته‌شدن قاطع کندل بالای: {fp(sig['cancel_price'])}")
    L.append("")
    L.append(f"با نیم الی یک درصد سرمایه با اهرم {lev}× وارد شوید 🏦")
    L.append(f"تی پی‌ها با اهرم {lev}× محاسبه شده")
    L.append("")
    L.append("📊 تحلیل:")
    L.append(f"• {'پامپ' if short_ else 'دامپ'} شناسایی‌شده: {sig['pump_pct'] * 100:.1f}% "
              f"({fp(sig['pump_low'])} → {fp(sig['pump_high'])})")
    L.append("• ترندلاین از دو سقف/کف پیوتال رسم و با تایید بسته‌شدن کندل شکسته شد")
    if sig["comp"].get("divergence"):
        L.append("• واگرایی RSI نزولی تایید شد" if short_ else "• واگرایی RSI صعودی تایید شد")
    if sig["comp"].get("reversal_candle"):
        L.append("• الگوی کندلی بازگشتی (پین‌بار/انگالف) مشاهده شد")
    if sig["comp"].get("volume_climax"):
        L.append("• حجم در نقطه‌ی سقف/کف، نشانه‌ی اتمام حرکت (Climax) دارد")
    L.append(f"• روند تایم‌فریم بالاتر: {'خنثی/نزولی ✅' if short_ else 'خنثی/صعودی ✅'} (فیلتر ضدترند رد نشد)")
    L.append(f"• حجم کندل شکست: {sig['vol_ratio']:.1f}× میانگین")
    L.append(f"• RSI: {sig['rsi']:.0f}")
    L.append(f"• امتیاز سیگنال: {sig['score']}/{sig['max_score']}")
    L.append("🧠 استراتژی: Trendline Break " + ("Short (Pump Fade)" if short_ else "Long (Dump Fade)") +
              " + Pending Retest + ضدترند")
    if CFG.send_chart:
        L.append("📎 چارت پیوست شده")
    L.append("")
    L.append(CFG.brand_name)
    L.append(CFG.handle)
    return "\n".join(L)


STATUS_TEXT = {
    "pending": "🦭 فعال نشده",
    "open": "✅💯 فعال شده",
    "cancelled": "❌ کنسل شده",
}


def status_win_text(pct):
    return f"✅ بسته شده با سود «{pct:.1f}% سود»"


def status_loss_text(pct):
    return f"🔴 بسته شده «{abs(pct):.1f}% ضرر»"


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


def tg_pin(chat_id, message_id):
    try:
        _tg("pinChatMessage", json={"chat_id": chat_id, "message_id": message_id, "disable_notification": True})
    except Exception as e:  # noqa
        log.warning("pin failed %s/%s: %s (ربات باید ادمین با دسترسی Pin باشد)", chat_id, message_id, e)


def tg_unpin(chat_id, message_id):
    try:
        _tg("unpinChatMessage", json={"chat_id": chat_id, "message_id": message_id})
    except Exception:  # noqa
        pass


def broadcast(text, png=None, caption=""):
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


def post_status(pos, text, dry):
    """زیر پیام سیگنال، وضعیت جدید را Reply می‌کند و (در صورت فعال بودن) Pin می‌کند؛
    وضعیت قبلی همان سیگنال از حالت Pin خارج می‌شود."""
    if dry:
        print("[DRY STATUS]", text)
        return
    status_msgs = pos.setdefault("status_msgs", {})
    for ch in pos["chats"]:
        cid = ch["chat_id"]
        try:
            mid = tg_send(text, cid, reply_to=ch["message_id"])
        except Exception as e:  # noqa
            log.error("status send failed %s: %s", cid, e)
            continue
        if CFG.pin_status:
            prev = status_msgs.get(cid)
            if prev:
                tg_unpin(cid, prev)
            tg_pin(cid, mid)
        status_msgs[cid] = mid


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
    h1_tf, _h2_tf = MTF_MAP[tf]
    lb = pump_lookback_for(tf)
    sl_pct = max_sl_pct_for(tf)

    L = fetch_klines(sym, tf, LIVE_BARS)
    if len(L) < lb + 30:
        return None
    P = prep(L)

    now_ms = time.time() * 1000
    t = len(L) - 1
    tc = int(P["t"][t]) + TF_MS[tf]
    if now_ms - tc > CFG.max_age_min * 60_000:
        return None

    extra = []
    htf_trend = 0
    if h1_tf:
        H1, hd = get_htf_cached(sym, h1_tf)
        if H1 is not None:
            htf_trend = trend_at(H1, tc)
        if hd is not None:
            try:
                extra += pivot_levels(hd.high.values, hd.low.values, P["atr"][t] * 2)
            except Exception:  # noqa
                pass
    extra += get_daily_levels_cached(sym)

    sig = detect_trendline_break(P, t, side=-1, extra_levels=extra, htf_trend=htf_trend, lookback=lb, max_sl_pct=sl_pct)
    if sig is None and CFG.enable_long:
        sig = detect_trendline_break(P, t, side=1, extra_levels=extra, htf_trend=htf_trend, lookback=lb, max_sl_pct=sl_pct)
    if sig is None or sig["score"] < CFG.min_score:
        return None

    px = last_price(sym)
    if px:
        if sig["side"] == -1 and (px >= sig["sl"] or px <= sig["tps"][-1]):
            return None
        if sig["side"] == 1 and (px <= sig["sl"] or px >= sig["tps"][-1]):
            return None

    info = {"candle_time": datetime.fromtimestamp(tc / 1000, timezone.utc).strftime("%H:%M")}
    return sig, info, build_message(sym, tf, sig, info), int(P["t"][t]), dict(P=P, t=t)


def scan_tf(tf, dry=False):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    state = jload(STATE_PATH, {"sent": {}})
    sent = state["sent"]
    positions = jload(POSITIONS_PATH, {})
    symbols = get_scan_symbols()

    results = {}
    with ThreadPoolExecutor(max_workers=max(1, int(CFG.workers))) as ex:
        futures = {ex.submit(analyze_symbol, sym, tf): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                results[sym] = fut.result()
            except Exception as e:  # noqa
                log.warning("%s %s: %s", sym, tf, e)

    # ارسال پیام‌ها و نوشتن فایل‌ها به‌صورت ترتیبی (تک‌رشته) تا رقابت روی state/positions پیش نیاید
    for sym in symbols:
        res = results.get(sym)
        if not res:
            continue
        sig, info, msg, tt, ctx = res
        k = f"{sym}|{tf}|{sig['side']}"
        last = sent.get(k, 0)
        if tt == last or (tt - last) < CFG.cooldown_candles * TF_MS[tf]:
            continue

        log.info("SIGNAL %s %s side=%s score=%s/%s", sym, tf, sig["side"], sig["score"], sig["max_score"])
        png = None
        if CFG.send_chart:
            try:
                png = make_chart(sym, tf, ctx["P"], ctx["t"], sig)
            except Exception as e:  # noqa
                log.error("chart failed %s: %s", sym, e)
        base = sym[:-4] if sym.endswith("USDT") else sym
        cap = f"📈 چارت #{base} | {tf} | Trendline Break + Retest"

        if dry:
            print("\n" + "=" * 40 + "\n" + msg + "\n" + "=" * 40)
            sent[k] = tt
            continue

        chats = broadcast(msg, png, cap)
        if not chats:
            continue
        sent[k] = tt

        pos_id = f"{sym}|{tf}|{sig['side']}|{tt}"
        pos = {
            "symbol": sym, "tf": tf, "side": sig["side"],
            "entry": sig["entry"], "sl": sig["sl"], "tps": sig["tps"],
            "cancel_price": sig["cancel_price"],
            "opened_at": int(time.time()), "chats": chats,
            "hit_tps": [False, False, False], "status": "pending",
            "activated_at": None, "status_msgs": {},
            "pending_max_days": pending_max_days_for(tf),
        }
        positions[pos_id] = pos
        post_status(pos, STATUS_TEXT["pending"], dry)

    jsave(STATE_PATH, state)
    jsave(POSITIONS_PATH, positions)


# ----------------------------------------------------------------------------
# ردیابی سیگنال‌های فعال (بر اساس کندل، نه فقط قیمت لحظه‌ای) + وضعیت پین‌شونده
# ----------------------------------------------------------------------------
def _last_candle(symbol, tf):
    try:
        df = fetch_klines(symbol, tf, 3)
        if df.empty:
            return None
        row = df.iloc[-1]
        return dict(o=float(row.open), h=float(row.high), l=float(row.low), c=float(row.close))
    except Exception as e:  # noqa
        log.warning("candle fetch failed %s %s: %s", symbol, tf, e)
        return None


def _reply_all(pos, text, dry):
    if dry:
        print("[DRY REPLY]", text)
        return
    for ch in pos["chats"]:
        try:
            tg_send(text, ch["chat_id"], reply_to=ch["message_id"])
        except Exception as e:  # noqa
            log.error("reply failed %s: %s", ch, e)


def _close_trade(pos, result, pct, trades_log):
    trades_log.append({
        "symbol": pos["symbol"], "tf": pos["tf"], "side": pos["side"],
        "opened_at": pos["opened_at"], "closed_at": int(time.time()),
        "result": result, "tps_hit": sum(pos.get("hit_tps", [False] * 3)), "pct": pct,
    })


def monitor_positions(dry=False):
    positions = jload(POSITIONS_PATH, {})
    trades_log = jload(TRADES_LOG_PATH, [])
    changed = False

    for pos_id, pos in list(positions.items()):
        status = pos.get("status")
        if status not in ("pending", "open"):
            continue
        candle = _last_candle(pos["symbol"], pos["tf"])
        if candle is None:
            continue
        side = pos["side"]
        entry, sl, tps, cancel_price = pos["entry"], pos["sl"], pos["tps"], pos["cancel_price"]

        # --- مرحله ۱: سیگنال معلق ---
        if status == "pending":
            age_days = (time.time() - pos["opened_at"]) / 86400
            touched = (candle["h"] >= entry) if side == -1 else (candle["l"] <= entry)
            closed_beyond_cancel = (candle["c"] > cancel_price) if side == -1 else (candle["c"] < cancel_price)

            if touched:
                rejected = (candle["c"] <= entry) if side == -1 else (candle["c"] >= entry)
                if rejected:
                    pos["status"] = "open"
                    pos["activated_at"] = int(time.time())
                    changed = True
                    post_status(pos, STATUS_TEXT["open"], dry)
                    if CFG.okx_demo_trading and not dry:
                        demo = place_demo_trade(pos["symbol"], side, sl, tps[1])
                        if demo:
                            pos["demo"] = demo
                            _reply_all(pos, f"🧪 معامله‌ی دمو باز شد روی OKX\n"
                                             f"حجم: {demo['contracts']} کانترکت | ورود≈{demo['entry_px']}\n"
                                             f"برای دیدن جزئیات، اپ OKX (حالت Demo Trading) را چک کن.", dry)
                        else:
                            _reply_all(pos, "⚠️ باز کردن معامله‌ی دمو ناموفق بود (جزئیات در لاگ).", dry)
                    status = "open"
                elif closed_beyond_cancel:
                    pos["status"] = "cancelled"
                    changed = True
                    post_status(pos, STATUS_TEXT["cancelled"], dry)
                    _close_trade(pos, "cancelled", 0.0, trades_log)
                    continue
                else:
                    continue  # لمس شده ولی هنوز تایید رد یا کنسل قطعی نداریم؛ کندل بعد چک می‌شود
            elif closed_beyond_cancel:
                pos["status"] = "cancelled"
                changed = True
                post_status(pos, STATUS_TEXT["cancelled"], dry)
                _close_trade(pos, "cancelled", 0.0, trades_log)
                continue
            elif age_days > pos.get("pending_max_days", CFG.pending_max_days):
                pos["status"] = "cancelled"
                changed = True
                post_status(pos, STATUS_TEXT["cancelled"] + f"\n(ظرف {pos.get('pending_max_days', CFG.pending_max_days):.0f} روز لمس نشد)", dry)
                _close_trade(pos, "cancelled", 0.0, trades_log)
                continue
            else:
                continue

        # --- مرحله ۲: سیگنال فعال -> بررسی TP/SL بر اساس کندل ---
        sl_hit = (candle["h"] >= sl) if side == -1 else (candle["l"] <= sl)
        if sl_hit:
            pct = -(abs(sl - entry) / entry * 100 * CFG.leverage)
            changed = True
            pos["status"] = "closed"
            _reply_all(pos, status_loss_text(pct), dry)
            post_status(pos, status_loss_text(pct), dry)
            _close_trade(pos, "loss", pct, trades_log)
            continue

        for i, tp in enumerate(tps):
            if pos["hit_tps"][i]:
                continue
            hit = (candle["l"] <= tp) if side == -1 else (candle["h"] >= tp)
            if not hit:
                break
            pos["hit_tps"][i] = True
            pct = roi(tp, entry)
            changed = True
            _reply_all(pos, f"🎯 TP{i + 1} فعال شد ✅\n#{pos['symbol']} | {'SHORT' if side == -1 else 'LONG'}\n"
                             f"سود این پله: {pct:.1f}%", dry)
            if i == 2:
                pos["status"] = "closed"
                post_status(pos, status_win_text(pct), dry)
                _close_trade(pos, "win", pct, trades_log)

    if changed:
        jsave(POSITIONS_PATH, positions)
        jsave(TRADES_LOG_PATH, trades_log)


# ----------------------------------------------------------------------------
# گزارش روزانه و هفتگی (به وقت تهران)
# ----------------------------------------------------------------------------
def _report_text(trades, title, period_label):
    n_all = len(trades)
    cancelled = [t for t in trades if t["result"] == "cancelled"]
    scored = [t for t in trades if t["result"] in ("win", "loss")]
    n = len(scored)
    L = [title, f"🗓 بازه: {period_label}", ""]
    if n == 0:
        L.append("سیگنال فعال‌شده‌ای برای گزارش وجود نداشت.")
        if cancelled:
            L.append(f"⌛️ {len(cancelled)} سیگنال کنسل/منقضی شد.")
        L.append("")
        L.append(CFG.brand_name)
        L.append(CFG.handle)
        return "\n".join(L)

    wins = [t for t in scored if t["result"] == "win"]
    losses = [t for t in scored if t["result"] == "loss"]
    win_rate = len(wins) / n * 100
    total_pct = sum(t["pct"] for t in scored)
    avg_pct = total_pct / n
    best = max(scored, key=lambda t: t["pct"])
    worst = min(scored, key=lambda t: t["pct"])

    L.append(f"تعداد سیگنال‌های فعال‌شده: {n}")
    L.append(f"وین ریت: {win_rate:.0f}% ({len(wins)} برد / {len(losses)} باخت)")
    L.append(f"مجموع بازدهی (با اهرم {int(CFG.leverage)}×): {total_pct:.1f}%")
    L.append(f"میانگین بازدهی هر سیگنال: {avg_pct:.1f}%")
    L.append(f"بهترین معامله: #{best['symbol']} ({best['pct']:.1f}%)")
    L.append(f"بدترین معامله: #{worst['symbol']} ({worst['pct']:.1f}%)")
    if cancelled:
        L.append(f"⌛️ {len(cancelled)} سیگنال کنسل/منقضی شد (بدون تاثیر در وین‌ریت)")
    L.append("")
    L.append(CFG.brand_name)
    L.append(CFG.handle)
    return "\n".join(L)


def daily_report(dry=False):
    trades_log = jload(TRADES_LOG_PATH, [])
    start_local = now_tehran().replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = start_local.timestamp()
    trades = [t for t in trades_log if t.get("closed_at", 0) >= cutoff]
    text = _report_text(trades, "📅 گزارش عملکرد امروز", "امروز")
    if dry:
        print("\n" + "=" * 40 + "\n" + text + "\n" + "=" * 40)
    else:
        for cid in CFG.chat_ids:
            try:
                tg_send(text, cid)
            except Exception as e:  # noqa
                log.error("daily report to %s failed: %s", cid, e)
    return text


def weekly_report(dry=False, days=7):
    trades_log = jload(TRADES_LOG_PATH, [])
    cutoff = time.time() - days * 86400
    trades = [t for t in trades_log if t.get("closed_at", 0) >= cutoff]
    text = _report_text(trades, "📊 گزارش عملکرد هفتگی", "۷ روز گذشته")
    if dry:
        print("\n" + "=" * 40 + "\n" + text + "\n" + "=" * 40)
    else:
        for cid in CFG.chat_ids:
            try:
                tg_send(text, cid)
            except Exception as e:  # noqa
                log.error("weekly report to %s failed: %s", cid, e)
    return text


def maybe_send_scheduled_reports(dry=False):
    """با آستانه‌ی >= چک می‌شود (نه تساوی دقیق دقیقه) تا با کرون هر ۵ دقیقه‌ی
    گیت‌هاب اکشنز هم قابل اعتماد کار کند."""
    state = jload(STATE_PATH, {"sent": {}})
    now = now_tehran()
    today_key = now.strftime("%Y-%m-%d")

    daily_target = now.replace(hour=CFG.daily_report_hour, minute=CFG.daily_report_minute, second=0, microsecond=0)
    if now >= daily_target and state.get("last_daily_report") != today_key:
        log.info("sending daily report")
        daily_report(dry=dry)
        state["last_daily_report"] = today_key
        jsave(STATE_PATH, state)

    weekly_target = now.replace(hour=CFG.weekly_report_hour, minute=CFG.weekly_report_minute, second=0, microsecond=0)
    if now.weekday() == CFG.weekly_report_dow and now >= weekly_target and state.get("last_weekly_report") != today_key:
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
    maybe_send_scheduled_reports(dry)


def run(dry=False):
    symbols = get_scan_symbols()
    log.info("bot started | symbols=%d | tfs=%s | dry=%s", len(symbols), ",".join(SIGNAL_TFS), dry)
    if not dry and CFG.chat_ids:
        try:
            for cid in CFG.chat_ids:
                tg_send(
                    f"✅ {CFG.brand_name} فعال شد\n"
                    f"📊 {CFG.label} Futures | تایم‌فریم‌ها: {', '.join(SIGNAL_TFS)}\n"
                    f"🔎 تعداد ارز تحت اسکن: {len(symbols)}"
                    + (" (شامل Auto Universe)" if CFG.auto_universe else ""), cid)
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
                maybe_send_scheduled_reports(dry)
            except Exception as e:  # noqa
                log.error("report error: %s", e)

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

    print("در حال تست اتصال تلگرام (بدون ارسال پیام واقعی)...")
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
    print("ℹ️ برای دریافت پیام واقعی در تلگرام: python bot.py test-telegram")

    if CFG.okx_demo_trading:
        print("\nدر حال تست اتصال به OKX Demo Trading (فقط خواندن موجودی، بدون ثبت سفارش)...")
        try:
            data = okx_demo_check()
            print("✅ اتصال به حساب دمو OKX برقرار است.")
            for acc in data or []:
                for d in acc.get("details", []):
                    if float(d.get("eq") or 0) > 0:
                        print(f"   موجودی {d['ccy']}: {d['eq']}")
        except Exception as e:  # noqa
            print(f"❌ اتصال به OKX Demo Trading ناموفق: {e}")
            print("   کلیدهای OKX_API_KEY/OKX_API_SECRET/OKX_API_PASSPHRASE را چک کن (باید از داخل حالت Demo Trading ساخته شده باشند).")


def test_telegram():
    if not CFG.chat_ids:
        print("❌ chat id تنظیم نشده.")
        return
    for cid in CFG.chat_ids:
        try:
            tg_send(f"✅ پیام تست از {CFG.brand_name}.", cid)
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
    print("2) حالا در تلگرام به همین ربات پیام /start بفرستید (یا ربات را ادمین کانال/گروه با دسترسی Pin کنید).")
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
            tg_send(f"✅ اتصال {CFG.brand_name} برقرار شد.", cid)
            print(f"✅ پیام تست به {cid} ارسال شد.")
        except Exception as e:  # noqa
            print(f"❌ ارسال به {cid} ناموفق: {e}")
    print("\nتنظیمات در فایل .env ذخیره شد. برای اجرا: python bot.py run")
    print("نکته: برای Pin شدن وضعیت‌ها، ربات باید در گروه/کانال ادمین با دسترسی Pin Messages باشد.")


def install_service():
    unit = f"""[Unit]
Description={CFG.brand_name} Signal Bot
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
    elif cmd in ("daily-report", "report"):
        daily_report(dry)
    elif cmd == "weekly-report":
        weekly_report(dry)
    elif cmd == "demo-check":
        try:
            okx_demo_check()
            print("✅ اتصال به OKX Demo Trading برقرار است.")
        except Exception as e:  # noqa
            print(f"❌ ناموفق: {e}")
    elif cmd == "install-service":
        install_service()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

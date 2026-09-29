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
  python bot.py backtest        # بک‌تست روی داده‌ی واقعی OKX (--days 30 --tfs 15m,1h --symbols BTCUSDT,ETHUSDT)
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

    # --- استراتژی مخصوص طلا (Trendline + شکست ساختار + فیبوی ۰.۷۸۶-۱) ---
    gold_enabled = os.getenv("GOLD_STRATEGY", "1") == "1"
    gold_symbol = "XAUUSDT"
    # طلای واقعی OKX یک محصول جدا و پیچیده به اسم X-Perp است (قرارداد ۵ساله با
    # انقضا، مخصوص کاربران اروپا، instId متغیر) که با endpoint معمولی کندل کار
    # نمی‌کند. برای پایداری و در دسترس بودن همیشگی، از توکن Paxos Gold (PAXG)
    # -یک SPOT استاندارد و همیشه در دسترس روی OKX که تقریباً برابر با یک اونس
    # طلای واقعی است- به‌عنوان منبع قیمت استفاده می‌شود.
    gold_inst_id = os.getenv("GOLD_INST_ID", "PAXG-USDT")
    gold_tf = os.getenv("GOLD_TF", "5m")
    gold_htf = os.getenv("GOLD_HTF", "1h")
    gold_lookback = _f("GOLD_LOOKBACK", 150)
    gold_min_trend_pct = _f("GOLD_MIN_TREND_PCT", 0.4) / 100
    gold_fib_entry = _f("GOLD_FIB_ENTRY", 0.786)
    gold_max_sl_pct = _f("GOLD_MAX_SL_PCT", 1.5) / 100
    gold_pending_max_days = _f("GOLD_PENDING_MAX_DAYS", 2.0)
    gold_min_entry_gap_atr = _f("GOLD_MIN_ENTRY_GAP_ATR", 1.0)

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
    # قبلاً SL فقط ۰٫۵ ATR بالای سقف بود؛ با نویز عادی بازار مدام استاپ می‌خورد.
    sl_buffer_atr = _f("SL_BUFFER_ATR", 0.8)
    retest_buffer_atr = _f("RETEST_BUFFER_ATR", 0.3)
    # حداقل فاصله‌ی لازم بین قیمت لحظه‌ی سیگنال و نقطه‌ی ورود (ضریب ATR)
    min_entry_gap_atr = _f("MIN_ENTRY_GAP_ATR", 0.8)
    # حداقل تاخیر قبل از فعال‌شدن؛ حالا فقط کندل‌های «بعد از ارسال سیگنال» بررسی
    # می‌شوند، پس این عدد به‌طور پیش‌فرض صفر است.
    min_activation_delay_sec = _f("MIN_ACTIVATION_DELAY_SEC", 0)
    enable_long = os.getenv("ENABLE_LONG", "0") == "1"

    # --- فیلترهای دقت (جدید) ---
    require_htf_filter = os.getenv("REQUIRE_HTF_FILTER", "1") == "1"   # ممنوعیت شورت روی روند صعودی قوی تایم بالاتر
    require_confirmation = os.getenv("REQUIRE_CONFIRMATION", "1") == "1"  # الزام واگرایی یا کندل بازگشتی
    # اگر تایم بالاتر «خنثی» باشد (نه نزولی)، فقط با هم‌پوشانی قوی همه‌ی شواهد سیگنال بده
    htf_neutral_requires_confluence = os.getenv("HTF_NEUTRAL_CONFLUENCE", "1") == "1"
    min_rsi_peak = _f("MIN_RSI_PEAK", 65.0)          # RSI اشباع خرید روی سقف (برای شورت)
    min_extension_atr = _f("MIN_EXTENSION_ATR", 2.0)  # فاصله‌ی سقف از EMA20 (ضریب ATR)
    volume_climax_mult = _f("VOLUME_CLIMAX_MULT", 1.2)
    min_score = _f("MIN_SCORE", 9)      # از 12 (سخت‌گیرانه‌تر)
    # فقط تایم‌فریم‌هایی که نویز کمتری دارند (۵ دقیقه مخصوص طلاست)
    crypto_tfs = tuple(x.strip() for x in os.getenv(
        "CRYPTO_TFS", "15m,30m,1h,2h,4h,6h,12h,1d,3d,1w").split(",") if x.strip())
    # فیلتر وضعیت بیت‌کوین: شورت آلت‌کوین وقتی BTC در حال پرواز است ممنوع
    btc_filter = os.getenv("BTC_FILTER", "1") == "1"
    btc_block_ret_pct = _f("BTC_BLOCK_RET_PCT", 1.0) / 100

    # --- مدیریت معامله (جدید) ---
    # سهم هر هدف از حجم؛ بعد از TP1 استاپ به Entry (ریسک‌فری) و بعد از TP2 به TP1 منتقل می‌شود
    tp_weights = tuple(float(x) for x in os.getenv("TP_WEIGHTS", "0.4,0.3,0.3").split(","))
    # وضعیت سیگنال‌ها با کندل‌های ریز (نه کندل خودِ تایم‌فریم سیگنال) دنبال می‌شود
    monitor_tf = os.getenv("MONITOR_TF", "5m")

    # --- ردیابی سیگنال‌های فعال ---
    monitor_interval_sec = _f("MONITOR_INTERVAL_SEC", 45)
    pending_max_days = _f("PENDING_MAX_DAYS", 3.0)

    # --- کنترل کیفیت خودکار: اگر یک تایم‌فریم اخیراً ضعیف بوده، موقتاً بسته شود ---
    gate_enabled = os.getenv("PERF_GATE", "1") == "1"
    gate_days = _f("GATE_DAYS", 14)
    gate_min_trades = _f("GATE_MIN_TRADES", 12)
    gate_min_winrate = _f("GATE_MIN_WINRATE", 0.35)

    # --- بک‌تست ---
    backtest_days = _f("BACKTEST_DAYS", 30)
    backtest_max_symbols = _f("BACKTEST_MAX_SYMBOLS", 25)
    backtest_tfs = tuple(x.strip() for x in os.getenv("BACKTEST_TFS", "15m,1h,4h").split(",") if x.strip())

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


def _okx_klines(symbol, tf, limit, end=None, inst_override=None, history=False):
    inst = inst_override or (symbol[:-4] + "-USDT-SWAP" if symbol.endswith("USDT") else symbol)
    rows, after = [], (int(end) + 1 if end else None)
    # endpoint معمولی فقط ~۱۴۴۰ کندل آخر را می‌دهد؛ برای بک‌تست از history-candles استفاده می‌شود
    path = "/api/v5/market/history-candles" if history else "/api/v5/market/candles"
    page = 100 if history else 300
    while len(rows) < limit:
        params = {"instId": inst, "bar": OKX_BAR[tf], "limit": min(page, limit - len(rows))}
        if after:
            params["after"] = after
        r = SESSION.get(CFG.okx_base.rstrip("/") + path, params=params, timeout=15)
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


def last_price(symbol, inst_override=None):
    try:
        if CFG.exchange == "okx":
            inst = inst_override or (symbol[:-4] + "-USDT-SWAP")
            r = SESSION.get(CFG.okx_base.rstrip("/") + "/api/v5/market/ticker", params={"instId": inst}, timeout=10)
            return float(r.json()["data"][0]["last"])
        r = SESSION.get(CFG.fapi_base.rstrip("/") + "/fapi/v1/ticker/price", params={"symbol": symbol}, timeout=10)
        return float(r.json()["price"])
    except Exception:  # noqa
        return None


def fetch_klines(symbol, tf, limit=500, end=None, inst_override=None, history=False):
    if CFG.exchange == "okx":
        return _okx_klines(symbol, tf, limit, end, inst_override=inst_override, history=history)
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
    if CFG.gold_enabled and CFG.gold_symbol not in _SCAN_SYMBOLS_CACHE:
        _SCAN_SYMBOLS_CACHE = [CFG.gold_symbol] + _SCAN_SYMBOLS_CACHE   # طلا اول اسکن شود
    return _SCAN_SYMBOLS_CACHE


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
        ema20=c.ewm(span=20, adjust=False).mean().values,
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
def build_targets(side, entry, R, ref_hi, ref_lo):
    """سه هدف از سطوح فیبوی حرکت قبلی. اولین هدف حداقل ۱R از Entry فاصله دارد
    (تا نسبت ریسک‌به‌ریوارد هرگز کمتر از ۱ به ۱ نباشد) و هر هدف بعدی حداقل ۰٫۳R
    از قبلی دورتر است. این‌طوری TP1 به‌اندازه‌ی کافی نزدیک و قابل‌رسیدن است، نه
    اینکه فقط با بازگشت کاملِ کل پامپ برد حساب شود."""
    rng = abs(ref_hi - ref_lo)
    ratios = (0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618, 2.0)
    tps = []
    for r in ratios:
        x = (ref_hi - r * rng) if side == -1 else (ref_lo + r * rng)
        dist = (entry - x) if side == -1 else (x - entry)
        if not tps:
            if dist >= 1.0 * R:
                tps.append(float(x))
        else:
            prev_dist = (entry - tps[-1]) if side == -1 else (tps[-1] - entry)
            if dist >= prev_dist + 0.3 * R:
                tps.append(float(x))
        if len(tps) == 3:
            break
    while len(tps) < 3:
        last = tps[-1] if tps else entry
        tps.append(float(last - R if side == -1 else last + R))
    return tps


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

        # --- اشباع خرید و کشیدگی از میانگین (سوخت بازگشت به میانگین) ---
        peak_rsi = float(rsi[max(0, h2 - 2):h2 + 3].max())
        overbought = peak_rsi >= CFG.min_rsi_peak
        ema20 = P.get("ema20")
        overextended = bool(ema20 is not None and (h[h2] - ema20[h2]) >= CFG.min_extension_atr * a)
        # وقتی تایم بالاتر نزولی نیست (خنثی)، فید کردن پامپ فقط با هم‌پوشانی کامل شواهد مجاز است
        if CFG.htf_neutral_requires_confluence and htf_trend == 0:
            if not (bearish_divergence and reversal_candle and overbought and overextended):
                return None

        ceiling = max(pump_high, h[h2])
        entry = float(ceiling - CFG.retest_buffer_atr * a)
        sl = float(ceiling + CFG.sl_buffer_atr * a)
        R = sl - entry
        if R <= 0 or R / entry > max_sl_pct or R / entry < 0.001:
            return None
        if entry <= c[t] + CFG.min_entry_gap_atr * a:
            return None   # فاصله‌ی ورود تا قیمت فعلی خیلی کمه؛ زمانی برای واکنش نمی‌مونه

        tps = build_targets(-1, entry, R, pump_high, pump_low)
        if c[t] <= tps[0]:
            return None   # قبل از سیگنال، قیمت به هدف اول رسیده؛ دیر شده
        # کنسل: اگر قبل از لمس Entry قیمت به هدف اول برسد، حرکت از دست رفته
        cancel_price = float(tps[0])

        near_res = [x for x in extra_levels if entry * 0.995 <= x <= sl]
        vol_ratio = v[t] / vs[t] if vs[t] > 0 else 0
        vol_climax = (v[h2] / vs[h2]) >= CFG.volume_climax_mult if vs[h2] > 0 else False

        comp = dict(
            trendline=2,
            pump=1 if pump_pct >= 2 * CFG.pump_min_pct else 0,
            htf_aligned=1 if htf_trend == -1 else 0,
            divergence=1 if bearish_divergence else 0,
            reversal_candle=1 if reversal_candle else 0,
            volume_climax=1 if vol_climax else 0,
            breakout_volume=1 if vol_ratio >= 1.3 else 0,
            red_candle=1 if c[t] < o[t] else 0,
            no_extra_res=1 if not near_res else 0,
            overbought=1 if overbought else 0,
            overextended=1 if overextended else 0,
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

        trough_rsi = float(rsi[max(0, l2 - 2):l2 + 3].min())
        oversold = trough_rsi <= (100 - CFG.min_rsi_peak)
        ema20 = P.get("ema20")
        underextended = bool(ema20 is not None and (ema20[l2] - l[l2]) >= CFG.min_extension_atr * a)
        if CFG.htf_neutral_requires_confluence and htf_trend == 0:
            if not (bullish_divergence and reversal_candle and oversold and underextended):
                return None

        floor_ = min(dump_low, l[l2])
        entry = float(floor_ + CFG.retest_buffer_atr * a)
        sl = float(floor_ - CFG.sl_buffer_atr * a)
        R = entry - sl
        if R <= 0 or R / entry > max_sl_pct or R / entry < 0.001:
            return None
        if entry >= c[t] - CFG.min_entry_gap_atr * a:
            return None

        tps = build_targets(1, entry, R, dump_high, dump_low)
        if c[t] >= tps[0]:
            return None
        cancel_price = float(tps[0])

        near_res = [x for x in extra_levels if sl <= x <= entry * 1.005]
        vol_ratio = v[t] / vs[t] if vs[t] > 0 else 0
        vol_climax = (v[l2] / vs[l2]) >= CFG.volume_climax_mult if vs[l2] > 0 else False

        comp = dict(
            trendline=2,
            dump=1 if dump_pct >= 2 * CFG.pump_min_pct else 0,
            htf_aligned=1 if htf_trend == 1 else 0,
            divergence=1 if bullish_divergence else 0,
            reversal_candle=1 if reversal_candle else 0,
            volume_climax=1 if vol_climax else 0,
            breakout_volume=1 if vol_ratio >= 1.3 else 0,
            green_candle=1 if c[t] > o[t] else 0,
            no_extra_res=1 if not near_res else 0,
            overbought=1 if oversold else 0,
            overextended=1 if underextended else 0,
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
# استراتژی مخصوص طلا (XAUUSD, تایم‌فریم 5m): بازگشتِ روند تایید‌شده با
# شکست ترندلاین + شکست آخرین سقف/کف ساختار + ورود در محدوده فیبوی ۰.۷۸۶ تا ۱
# ----------------------------------------------------------------------------
def detect_gold_reversal(P, t, htf_trend, lookback):
    """
    قدم‌های استراتژی (دقیقاً طبق توضیح کاربر):
      ۱) روند تایم‌فریم بالاتر مشخص می‌شود (htf_trend، از قبل محاسبه شده).
      ۲) اگر HTF نزولی است: خط روند نزولی (روی سقف‌ها) رسم و شکستش رو به بالا
         تایید می‌شود، سپس باید آخرین سقف هم به سمت بالا شکسته شود (بازگشت به Long).
         اگر HTF صعودی است: خط روند صعودی (روی کف‌ها) و شکستش رو به پایین +
         شکست آخرین کف (بازگشت به Short) — کاملاً قرینه.
      ۳) فیبوی دو نقطه از ابتدای روند تا انتهای روند رسم می‌شود.
      ۴) فقط سطوح 0 / 0.786 / 1 نگه داشته می‌شوند: محدوده‌ی بین ۱ و ۰.۷۸۶ به‌عنوان
         نقطه‌ی ورودِ معلق (ری‌تست) و حد ضرر استفاده می‌شود.
    خروجی دقیقاً هم‌شکل خروجی detect_trendline_break است تا از همان خط لوله‌ی
    ردیابی/پیام/گزارش استفاده شود.
    """
    o, h, l, c, v, vs, atr, rsi = P["o"], P["h"], P["l"], P["c"], P["v"], P["vsma"], P["atr"], P["rsi"]
    a = atr[t]
    if htf_trend == 0 or not a > 0 or t < lookback + 5:
        return None
    w0 = max(0, t - lookback)
    fib_r = CFG.gold_fib_entry   # پیش‌فرض 0.786

    if htf_trend == -1:
        # HTF نزولی -> دنبال بازگشت صعودی (Long)، خط روند نزولی از سقف‌ها
        ts_idx = w0 + int(np.argmax(h[w0:t + 1]))            # شروع روند نزولی: بالاترین سقف
        if t - ts_idx < CFG.pump_min_bars:
            return None
        te_idx = ts_idx + int(np.argmin(l[ts_idx:t + 1]))    # انتهای روند (تاکنون): پایین‌ترین کف
        trend_start_px, trend_end_px = float(h[ts_idx]), float(l[te_idx])
        if trend_start_px <= 0 or (trend_start_px - trend_end_px) / trend_start_px < CFG.gold_min_trend_pct:
            return None

        piv = [i + ts_idx for i in pivot_idx(h[ts_idx:t + 1], CFG.pivot_left, CFG.pivot_right, "high")]
        piv = [i for i in piv if i < t]
        if len(piv) < 2:
            return None
        p1, p2 = piv[0], piv[-1]
        if h[p2] > h[p1]:                    # سقف‌ها باید نزولی (پایین‌تر) باشند
            return None
        fit = _fit_trendline([p1, p2], [h[p1], h[p2]])
        if fit is None:
            return None
        slope, intercept = fit
        if slope > 1e-9:
            return None
        line = lambda x: slope * x + intercept

        win_start = max(p2, t - 8)
        if not any(c[k] <= line(k) + CFG.break_buffer_atr * a for k in range(win_start, t)):
            return None
        if not (c[t] > line(t) + CFG.break_buffer_atr * a):
            return None

        last_high = float(h[piv[-1]])
        if not (c[t] > last_high):           # تاییدیه‌ی دوم: شکست آخرین سقف
            return None

        fib = lambda r: trend_start_px + (trend_end_px - trend_start_px) * r
        entry = float(fib(fib_r))
        sl = float(fib(1.0) - CFG.retest_buffer_atr * a)
        R = entry - sl
        if R <= 0 or R / entry > CFG.gold_max_sl_pct or entry <= c[t] + CFG.gold_min_entry_gap_atr * a:
            return None
        tps = [float(fib(0.5)), float(fib(0.236)), float(fib(0.0))]
        if any(tp <= entry for tp in tps) or c[t] >= tps[0]:
            return None
        cancel_price = float(tps[0])   # قبل از لمس Entry به TP1 برسد => کنسل

        reversal_candle = is_bullish_reversal(o, h, l, c, t) or is_bullish_reversal(o, h, l, c, piv[-1])
        vol_ratio = v[t] / vs[t] if vs[t] > 0 else 0
        comp = dict(trendline=2, structure_break=2, fib_zone=2,
                    reversal_candle=1 if reversal_candle else 0,
                    volume=1 if vol_ratio >= 1.2 else 0)
        score = sum(comp.values())
        return dict(
            side=1, entry=entry, sl=sl, tps=tps, R=float(R), atr=float(a), cancel_price=cancel_price,
            pump_low=trend_end_px, pump_high=trend_start_px, pump_pct=(trend_start_px - trend_end_px) / trend_start_px,
            line_pts=[(int(p1), float(h[p1])), (int(p2), float(h[p2]))], slope=float(slope),
            rsi=float(rsi[t]), vol_ratio=float(vol_ratio), comp=comp, score=score,
            L=int(ts_idx), max_score=8, is_gold=True, fib_levels={"0": trend_start_px, "0.786": entry, "1": float(fib(1.0))},
        )

    else:  # htf_trend == 1: HTF صعودی -> دنبال بازگشت نزولی (Short)، خط روند صعودی از کف‌ها
        ts_idx = w0 + int(np.argmin(l[w0:t + 1]))            # شروع روند صعودی: پایین‌ترین کف
        if t - ts_idx < CFG.pump_min_bars:
            return None
        te_idx = ts_idx + int(np.argmax(h[ts_idx:t + 1]))    # انتهای روند (تاکنون): بالاترین سقف
        trend_start_px, trend_end_px = float(l[ts_idx]), float(h[te_idx])
        if trend_start_px <= 0 or (trend_end_px - trend_start_px) / trend_start_px < CFG.gold_min_trend_pct:
            return None

        piv = [i + ts_idx for i in pivot_idx(l[ts_idx:t + 1], CFG.pivot_left, CFG.pivot_right, "low")]
        piv = [i for i in piv if i < t]
        if len(piv) < 2:
            return None
        p1, p2 = piv[0], piv[-1]
        if l[p2] < l[p1]:                    # کف‌ها باید صعودی (بالاتر) باشند
            return None
        fit = _fit_trendline([p1, p2], [l[p1], l[p2]])
        if fit is None:
            return None
        slope, intercept = fit
        if slope < -1e-9:
            return None
        line = lambda x: slope * x + intercept

        win_start = max(p2, t - 8)
        if not any(c[k] >= line(k) - CFG.break_buffer_atr * a for k in range(win_start, t)):
            return None
        if not (c[t] < line(t) - CFG.break_buffer_atr * a):
            return None

        last_low = float(l[piv[-1]])
        if not (c[t] < last_low):            # تاییدیه‌ی دوم: شکست آخرین کف
            return None

        fib = lambda r: trend_start_px + (trend_end_px - trend_start_px) * r
        entry = float(fib(fib_r))
        sl = float(fib(1.0) + CFG.retest_buffer_atr * a)
        R = sl - entry
        if R <= 0 or R / entry > CFG.gold_max_sl_pct or entry >= c[t] - CFG.gold_min_entry_gap_atr * a:
            return None
        tps = [float(fib(0.5)), float(fib(0.236)), float(fib(0.0))]
        if any(tp >= entry for tp in tps) or c[t] <= tps[0]:
            return None
        cancel_price = float(tps[0])

        reversal_candle = is_bearish_reversal(o, h, l, c, t) or is_bearish_reversal(o, h, l, c, piv[-1])
        vol_ratio = v[t] / vs[t] if vs[t] > 0 else 0
        comp = dict(trendline=2, structure_break=2, fib_zone=2,
                    reversal_candle=1 if reversal_candle else 0,
                    volume=1 if vol_ratio >= 1.2 else 0)
        score = sum(comp.values())
        return dict(
            side=-1, entry=entry, sl=sl, tps=tps, R=float(R), atr=float(a), cancel_price=cancel_price,
            pump_low=trend_start_px, pump_high=trend_end_px, pump_pct=(trend_end_px - trend_start_px) / trend_start_px,
            line_pts=[(int(p1), float(l[p1])), (int(p2), float(l[p2]))], slope=float(slope),
            rsi=float(rsi[t]), vol_ratio=float(vol_ratio), comp=comp, score=score,
            L=int(ts_idx), max_score=8, is_gold=True, fib_levels={"0": trend_start_px, "0.786": entry, "1": float(fib(1.0))},
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

    is_gold = sig.get("is_gold", False)
    base = "XAU/USD (PAXG)" if is_gold else (sym[:-4] if sym.endswith("USDT") else sym) + "/USDT"
    ax.set_title(
        f"#{base} {tf} {'SHORT' if short_ else 'LONG'} | "
        f"{'Gold Reversal' if is_gold else CFG.label + ' Futures | شکست ترندلاین + ری‌تست'} | "
        f"Score {sig['score']}/{sig['max_score']}",
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
    is_gold = sig.get("is_gold", False)
    base = "XAU" if is_gold else (sym[:-4] if sym.endswith("USDT") else sym)
    entry = sig["entry"]
    lev = str(CFG.leverage).translate(FA_DIGITS)
    risk_pct = sig["R"] / entry * 100

    L = []
    L.append(f"⭕️#{base}/USD {'📉' if short_ else '📈'}💰" if is_gold else f"⭕️#{base}/ USDT {'📉' if short_ else '📈'}💰")
    L.append("")
    L.append("🔴Cross (Short) 📉" if short_ else "🟢Cross (Long) 📈")
    if is_gold:
        L.append("با تایید شکست ترند لاین + شکست آخرین " + ("سقف" if not short_ else "کف") + " (بازگشت روند)")
        L.append(f"⏱ تایم‌فریم: {tf} | XAU/USD (پروکسی: PAXG)")
    else:
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
    w = CFG.tp_weights
    L.append(f"📦 تقسیم حجم: {int(w[0]*100)}٪ / {int(w[1]*100)}٪ / {int(w[2]*100)}٪ روی TP1 / TP2 / TP3")
    L.append("🔒 بعد از TP1 استاپ را به Entry ببر (ریسک‌فری)، بعد از TP2 به TP1")
    L.append(f"❌ اگر قبل از لمس Entry قیمت به TP1 ({fp(sig['cancel_price'])}) برسد، سیگنال کنسل است")
    L.append("")
    if not is_gold:
        L.append(f"با نیم الی یک درصد سرمایه با اهرم {lev}× وارد شوید 🏦")
        L.append(f"تی پی‌ها با اهرم {lev}× محاسبه شده")
        L.append("")
    L.append("📊 تحلیل:")
    if is_gold:
        L.append(f"• روند شناسایی‌شده ({'نزولی' if not short_ else 'صعودی'} در تایم‌فریم بالاتر): "
                  f"{sig['pump_pct'] * 100:.2f}% ({fp(sig['pump_low'])} ↔ {fp(sig['pump_high'])})")
        L.append("• خط روند رسم و با تایید بسته‌شدن کندل شکسته شد")
        L.append(f"• آخرین {'سقف' if not short_ else 'کف'} ساختار هم شکسته شد (تاییدیه‌ی دوم بازگشت روند)")
        fl = sig.get("fib_levels", {})
        L.append(f"• فیبوی ۰/۰٫۷۸۶/۱ روی حرکت رسم شد: ورود از سطح ۰٫۷۸۶، حد ضرر فراتر از سطح ۱")
        if sig["comp"].get("reversal_candle"):
            L.append("• الگوی کندلی بازگشتی هم دیده شد")
    else:
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
    if is_gold:
        L.append("🧠 استراتژی: Gold Trend-Reversal (Structure Break + Fib 0.786-1)")
    else:
        L.append("🧠 استراتژی: Trendline Break " + ("Short (Pump Fade)" if short_ else "Long (Dump Fade)") +
                  " + Pending Retest + ضدترند")
    if CFG.send_chart:
        L.append("📎 چارت پیوست شده")
    L.append("")
    L.append(CFG.brand_name)
    L.append(CFG.handle)
    if is_gold:
        L.append("")
        L.append("طلااااااا💯💯💯💯💯💯")
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


_BTC_CACHE = {}


def btc_blocks(side):
    """اگر بیت‌کوین قوی در جهت مخالفِ سیگنال حرکت می‌کند (مثلاً BTC در حال پرواز و ما شورت آلت‌کوین)،
    سیگنال بلاک می‌شود. در صورت هر خطا، بلاک نمی‌کند."""
    if not CFG.btc_filter:
        return False
    try:
        if "v" not in _BTC_CACHE:
            df = fetch_klines("BTCUSDT", "1h", 260)
            H = htf_pack(df, "1h")
            tr = int(H["trend"][-1])
            ret6 = float(df.close.iloc[-1] / df.close.iloc[-7] - 1)
            _BTC_CACHE["v"] = (tr, ret6)
        tr, ret6 = _BTC_CACHE["v"]
    except Exception as e:  # noqa
        log.warning("btc filter unavailable: %s", e)
        return False
    if side == -1:
        return tr == 1 and ret6 >= CFG.btc_block_ret_pct
    return tr == -1 and ret6 <= -CFG.btc_block_ret_pct


_GATE_CACHE = {}


def perf_gate_blocked(tf, is_gold=False):
    """اگر آخرین معاملات بسته‌شده‌ی این تایم‌فریم (در بازه‌ی GATE_DAYS) وین‌ریت ضعیفی داشته‌اند،
    موقتاً سیگنال جدید از همین تایم‌فریم صادر نمی‌شود. با گذشت زمان، معاملات قدیمی از پنجره
    خارج می‌شوند و تایم‌فریم دوباره فعال می‌شود."""
    if not CFG.gate_enabled:
        return False
    key = (tf, is_gold)
    if key in _GATE_CACHE:
        return _GATE_CACHE[key]
    trades = jload(TRADES_LOG_PATH, [])
    cutoff = time.time() - CFG.gate_days * 86400
    rel = [t for t in trades
           if t.get("tf") == tf and (t.get("symbol") == CFG.gold_symbol) == is_gold
           and t.get("closed_at", 0) >= cutoff and t.get("result") in ("win", "loss", "be")]
    blocked = False
    if len(rel) >= CFG.gate_min_trades:
        wr = sum(1 for t in rel if t["result"] == "win") / len(rel)
        if wr < CFG.gate_min_winrate:
            blocked = True
            log.warning("PERF GATE: tf=%s gold=%s winrate=%.0f%% (%d trades) -> signals paused",
                        tf, is_gold, wr * 100, len(rel))
    _GATE_CACHE[key] = blocked
    return blocked


def tf_allowed(sym, tf):
    if sym == CFG.gold_symbol:
        return tf == CFG.gold_tf
    return tf in CFG.crypto_tfs


def analyze_gold(tf):
    """مسیر تحلیل مخصوص طلا: قرارداد واقعی XAU-USDT-SWAP روی OKX (Commodity Perp)."""
    if tf != CFG.gold_tf or perf_gate_blocked(tf, is_gold=True):
        return None
    inst = CFG.gold_inst_id
    try:
        L = fetch_klines(CFG.gold_symbol, tf, LIVE_BARS, inst_override=inst)
    except Exception as e:  # noqa
        log.warning("gold fetch failed: %s", e)
        return None
    lb = CFG.gold_lookback
    if len(L) < lb + 30:
        return None
    P = prep(L)

    now_ms = time.time() * 1000
    t = len(L) - 1
    tc = int(P["t"][t]) + TF_MS[tf]
    if now_ms - tc > CFG.max_age_min * 60_000:
        return None

    try:
        hd = fetch_klines(CFG.gold_symbol, CFG.gold_htf, 300, inst_override=inst)
        H1 = htf_pack(hd, CFG.gold_htf)
        htf_trend = trend_at(H1, tc)
    except Exception as e:  # noqa
        log.warning("gold htf fetch failed: %s", e)
        return None
    if htf_trend == 0:
        return None   # بدون روند مشخص در تایم بالاتر، طبق تعریف استراتژی سیگنالی صادر نمی‌شود

    sig = detect_gold_reversal(P, t, htf_trend, lb)
    if sig is None:
        return None

    px = last_price(CFG.gold_symbol, inst_override=inst)
    if px:
        if sig["side"] == -1 and (px >= sig["entry"] or px <= sig["tps"][0]):
            return None
        if sig["side"] == 1 and (px <= sig["entry"] or px >= sig["tps"][0]):
            return None

    info = {"candle_time": datetime.fromtimestamp(tc / 1000, timezone.utc).strftime("%H:%M")}
    return sig, info, build_message(CFG.gold_symbol, tf, sig, info), int(P["t"][t]), dict(P=P, t=t)


def analyze_symbol(sym, tf):
    if sym == CFG.gold_symbol:
        return analyze_gold(tf)

    if not tf_allowed(sym, tf) or perf_gate_blocked(tf):
        return None

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

    if sym != "BTCUSDT" and btc_blocks(sig["side"]):
        log.info("BTC filter blocked %s %s side=%s", sym, tf, sig["side"])
        return None

    # اعتبارسنجی با قیمت لحظه‌ای: اگر Entry همین الان لمس شده (دیر شده) یا هدف اول رد شده، سیگنال نده
    px = last_price(sym)
    if px:
        if sig["side"] == -1 and (px >= sig["entry"] or px <= sig["tps"][0]):
            return None
        if sig["side"] == 1 and (px <= sig["entry"] or px >= sig["tps"][0]):
            return None

    info = {"candle_time": datetime.fromtimestamp(tc / 1000, timezone.utc).strftime("%H:%M")}
    return sig, info, build_message(sym, tf, sig, info), int(P["t"][t]), dict(P=P, t=t)


def scan_tf(tf, dry=False):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    state = jload(STATE_PATH, {"sent": {}})
    sent = state["sent"]
    positions = jload(POSITIONS_PATH, {})
    symbols = [x for x in get_scan_symbols() if tf_allowed(x, tf)]

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
            "weights": list(CFG.tp_weights), "cur_sl": sig["sl"], "realized_pct": 0.0,
            # فقط کندل‌هایی که «بعد از ارسال سیگنال» شروع شده‌اند بررسی می‌شوند
            "eval_from_ms": int(time.time() * 1000),
        }
        positions[pos_id] = pos
        post_status(pos, STATUS_TEXT["pending"], dry)

    jsave(STATE_PATH, state)
    jsave(POSITIONS_PATH, positions)


# ----------------------------------------------------------------------------
# ردیابی سیگنال‌های فعال (بر اساس کندل، نه فقط قیمت لحظه‌ای) + وضعیت پین‌شونده
# ----------------------------------------------------------------------------
def signed_pct(side, entry, exit_px):
    """سود/زیان درصدی (با اهرم) برای کل حجم؛ مثبت = سود."""
    move = (entry - exit_px) if side == -1 else (exit_px - entry)
    return move / entry * 100 * CFG.leverage


def _ensure_pos_fields(pos):
    """رکوردهای قدیمی positions.json را با فیلدهای جدید سازگار می‌کند."""
    pos.setdefault("weights", list(CFG.tp_weights))
    pos.setdefault("cur_sl", pos["sl"])
    pos.setdefault("realized_pct", 0.0)
    pos.setdefault("hit_tps", [False, False, False])
    pos.setdefault("eval_from_ms", int(pos.get("opened_at", time.time()) * 1000))
    pos.setdefault("status_msgs", {})


def step_position(pos, cd, now_ts):
    """
    یک کندل (cd: dict با t/o/h/l/c) را روی یک سیگنال اعمال می‌کند و لیست رویدادها را برمی‌گرداند.
    همین تابع هم در ربات زنده و هم در بک‌تست استفاده می‌شود، پس رفتارشان یکسان است.

    قوانین (واقع‌بینانه و محتاطانه):
      • فعال‌شدن = «لمس» قیمت ورود (همان‌طور که اردر لیمیت واقعی پر می‌شود)، نه بسته‌شدن کندل.
      • اگر قبل از لمس Entry قیمت به TP1 برسد => کنسل (حرکت از دست رفته). اگر مهلت تمام شود => کنسل.
      • داخل کندل فعال‌سازی فقط SL بررسی می‌شود (ترتیب نامعلوم؛ بدترین حالت فرض می‌شود).
      • هر کندل: اول SL فعلی، بعد اهداف به‌ترتیب.
      • بعد از TP1 استاپ به Entry (ریسک‌فری)، بعد از TP2 به TP1 منتقل می‌شود.
      • سود/ضرر نهایی = مجموع وزنی خروجی‌ها؛ پس معاملهٔ «TP1 خورد، بعد برگشت به Entry» یک سود جزئی است نه باخت.
    """
    events = []
    side = pos["side"]
    entry, tps = pos["entry"], pos["tps"]
    w = pos["weights"]
    activated_now = False

    if pos["status"] == "pending":
        touched = (cd["h"] >= entry) if side == -1 else (cd["l"] <= entry)
        if touched:
            pos["status"] = "open"
            pos["activated_at"] = int(now_ts)
            activated_now = True
            events.append({"type": "activated"})
        else:
            target_first = (cd["l"] <= tps[0]) if side == -1 else (cd["h"] >= tps[0])
            if target_first:
                pos["status"] = "cancelled"
                events.append({"type": "cancelled", "reason": "target_before_entry"})
                return events
            max_days = pos.get("pending_max_days", CFG.pending_max_days)
            if (now_ts - pos["opened_at"]) / 86400 > max_days:
                pos["status"] = "cancelled"
                events.append({"type": "cancelled", "reason": "expired", "days": max_days})
            return events

    if pos["status"] != "open":
        return events

    cur_sl = pos["cur_sl"]
    sl_hit = (cd["h"] >= cur_sl) if side == -1 else (cd["l"] <= cur_sl)
    if sl_hit:
        remaining = 1.0 - sum(w[i] for i in range(3) if pos["hit_tps"][i])
        total = pos["realized_pct"] + remaining * signed_pct(side, entry, cur_sl)
        pos["status"] = "closed"
        result = "win" if total > 0.5 else ("loss" if total < -0.5 else "be")
        pos["result"] = result
        pos["final_pct"] = total
        events.append({"type": "closed", "result": result, "pct": total, "reason": "sl",
                       "tps_hit": sum(pos["hit_tps"])})
        return events

    if activated_now:
        return events   # داخل کندلِ فعال‌سازی، TP حساب نمی‌شود (ترتیب نامعلوم)

    for i, tp in enumerate(tps):
        if pos["hit_tps"][i]:
            continue
        hit = (cd["l"] <= tp) if side == -1 else (cd["h"] >= tp)
        if not hit:
            break
        pos["hit_tps"][i] = True
        gain = w[i] * signed_pct(side, entry, tp)
        pos["realized_pct"] += gain
        moved = None
        if i == 0:
            pos["cur_sl"] = entry
            moved = entry
        elif i == 1:
            pos["cur_sl"] = tps[0]
            moved = tps[0]
        events.append({"type": "tp", "i": i, "step_pct": gain, "full_pct": signed_pct(side, entry, tp),
                       "weight": w[i], "sl_moved_to": moved})
        if i == 2:
            pos["status"] = "closed"
            pos["result"] = "win"
            pos["final_pct"] = pos["realized_pct"]
            events.append({"type": "closed", "result": "win", "pct": pos["realized_pct"], "reason": "tp3",
                           "tps_hit": 3})
            break
    return events


def _fetch_candles_since(symbol, since_ms):
    """کندل‌های «بسته‌شده»ی تایم‌فریم مانیتور از since_ms به بعد (به‌ترتیب زمانی)."""
    tf = CFG.monitor_tf
    tfm = TF_MS[tf]
    now_ms = time.time() * 1000
    need = int((now_ms - since_ms) / tfm) + 3
    need = max(3, min(need, 1000))
    inst_override = CFG.gold_inst_id if symbol == CFG.gold_symbol else None
    df = fetch_klines(symbol, tf, need, inst_override=inst_override)
    out = []
    for row in df.itertuples():
        t_open = int(row.time)
        if t_open < since_ms or t_open + tfm > now_ms:
            continue
        out.append(dict(t=t_open, o=float(row.open), h=float(row.high), l=float(row.low), c=float(row.close)))
    return out


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


def _notify_event(pos, ev, dry, trades_log):
    """رویداد شبیه‌ساز را به پیام تلگرام/لاگ معاملات تبدیل می‌کند."""
    side = pos["side"]
    tag = f"#{pos['symbol']} | {'SHORT' if side == -1 else 'LONG'}"
    t = ev["type"]
    if t == "activated":
        post_status(pos, STATUS_TEXT["open"], dry)
        if CFG.okx_demo_trading and not dry and pos["symbol"] != CFG.gold_symbol:
            demo = place_demo_trade(pos["symbol"], side, pos["sl"], pos["tps"][1])
            if demo:
                pos["demo"] = demo
                _reply_all(pos, f"🧪 معامله‌ی دمو باز شد روی OKX\n"
                                 f"حجم: {demo['contracts']} کانترکت | ورود≈{demo['entry_px']}\n"
                                 f"برای دیدن جزئیات، اپ OKX (حالت Demo Trading) را چک کن.", dry)
            else:
                _reply_all(pos, "⚠️ باز کردن معامله‌ی دمو ناموفق بود (جزئیات در لاگ).", dry)
    elif t == "tp":
        txt = (f"🎯 TP{ev['i'] + 1} فعال شد ✅\n{tag}\n"
               f"سود این پله: {ev['step_pct']:.1f}% (سهم {int(ev['weight'] * 100)}٪ حجم)")
        if ev.get("sl_moved_to") is not None:
            txt += f"\n🔒 استاپ منتقل شد به {fp(ev['sl_moved_to'])} (ریسک‌فری)"
        _reply_all(pos, txt, dry)
    elif t == "closed":
        pct = ev["pct"]
        if ev["result"] == "win":
            txt = status_win_text(pct)
        elif ev["result"] == "loss":
            txt = status_loss_text(pct)
        else:
            txt = "⚪️ بسته شده در نقطه‌ی سر به سر (۰٪)"
        _reply_all(pos, f"{txt}\n{tag}", dry)
        post_status(pos, txt, dry)
        _close_trade(pos, ev["result"], pct, trades_log)
    elif t == "cancelled":
        why = ("قیمت قبل از لمس Entry به TP1 رسید (حرکت از دست رفت)"
               if ev["reason"] == "target_before_entry"
               else f"ظرف {ev.get('days', CFG.pending_max_days):.0f} روز لمس نشد")
        post_status(pos, STATUS_TEXT["cancelled"] + f"\n({why})", dry)
        _close_trade(pos, "cancelled", 0.0, trades_log)


def monitor_positions(dry=False):
    positions = jload(POSITIONS_PATH, {})
    trades_log = jload(TRADES_LOG_PATH, [])
    changed = False
    tfm = TF_MS[CFG.monitor_tf]

    for pos_id, pos in list(positions.items()):
        if pos.get("status") not in ("pending", "open"):
            continue
        _ensure_pos_fields(pos)
        try:
            candles = _fetch_candles_since(pos["symbol"], pos["eval_from_ms"])
        except Exception as e:  # noqa
            log.warning("monitor fetch failed %s: %s", pos["symbol"], e)
            continue
        for cd in candles:
            now_ts = (cd["t"] + tfm) / 1000
            evs = step_position(pos, cd, now_ts)
            pos["eval_from_ms"] = cd["t"] + tfm
            changed = True
            for ev in evs:
                _notify_event(pos, ev, dry, trades_log)
            if pos["status"] not in ("pending", "open"):
                break

    if changed:
        jsave(POSITIONS_PATH, positions)
        jsave(TRADES_LOG_PATH, trades_log)


# ----------------------------------------------------------------------------
# گزارش روزانه و هفتگی (به وقت تهران)
# ----------------------------------------------------------------------------
def _report_text(trades, title, period_label):
    n_all = len(trades)
    cancelled = [t for t in trades if t["result"] == "cancelled"]
    scored = [t for t in trades if t["result"] in ("win", "loss", "be")]
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
    bes = [t for t in scored if t["result"] == "be"]
    win_rate = len(wins) / n * 100
    total_pct = sum(t["pct"] for t in scored)
    avg_pct = total_pct / n
    best = max(scored, key=lambda t: t["pct"])
    worst = min(scored, key=lambda t: t["pct"])

    L.append(f"تعداد سیگنال‌های فعال‌شده: {n}")
    L.append(f"وین ریت: {win_rate:.0f}% ({len(wins)} برد / {len(losses)} باخت / {len(bes)} سر‌به‌سر)")
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
    monitor_positions(dry)   # اول پوزیشن‌های قدیمی رو چک کن، بعد دنبال سیگنال جدید بگرد
    for tf in SIGNAL_TFS:
        scan_tf(tf, dry)
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
# بک‌تست (Walk-Forward) با همان منطق تشخیص و همان موتور مدیریت معامله‌ی ربات زنده
# ----------------------------------------------------------------------------
def _bt_fetch(symbol, tf, bars, inst_override=None):
    last_err = None
    for attempt in range(4):
        try:
            return fetch_klines(symbol, tf, bars, inst_override=inst_override, history=True)
        except Exception as e:  # noqa
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"backtest fetch failed {symbol} {tf}: {last_err}")


def backtest_series(sym, tf, df, H1, is_gold=False):
    """روی یک سری کندل، ربات را قدم‌به‌قدم اجرا می‌کند و معاملات شبیه‌سازی‌شده را برمی‌گرداند."""
    P = prep(df)
    n = len(df)
    tfm = TF_MS[tf]
    lb = CFG.gold_lookback if is_gold else pump_lookback_for(tf)
    sl_pct = max_sl_pct_for(tf)
    out, unresolved, last_sig_t = [], 0, -10 ** 9
    for t in range(lb + 6, n - 1):
        if t - last_sig_t < CFG.cooldown_candles:
            continue
        tc = int(P["t"][t]) + tfm
        ht = trend_at(H1, tc) if H1 is not None else 0
        if is_gold:
            sig = detect_gold_reversal(P, t, ht, lb) if ht != 0 else None
        else:
            sig = detect_trendline_break(P, t, -1, (), ht, lb, sl_pct)
            if sig is None and CFG.enable_long:
                sig = detect_trendline_break(P, t, 1, (), ht, lb, sl_pct)
            if sig is not None and sig["score"] < CFG.min_score:
                sig = None
        if sig is None:
            continue
        last_sig_t = t
        pos = {
            "symbol": sym, "tf": tf, "side": sig["side"], "entry": sig["entry"], "sl": sig["sl"],
            "tps": sig["tps"], "cancel_price": sig["cancel_price"], "opened_at": tc / 1000,
            "hit_tps": [False, False, False], "status": "pending", "activated_at": None,
            "pending_max_days": (CFG.gold_pending_max_days if is_gold else pending_max_days_for(tf)),
            "weights": list(CFG.tp_weights), "cur_sl": sig["sl"], "realized_pct": 0.0,
        }
        k = t + 1
        while k < n and pos["status"] in ("pending", "open"):
            cd = dict(t=int(P["t"][k]), o=float(P["o"][k]), h=float(P["h"][k]),
                      l=float(P["l"][k]), c=float(P["c"][k]))
            step_position(pos, cd, (cd["t"] + tfm) / 1000)
            k += 1
        if pos["status"] == "closed":
            out.append({"result": pos["result"], "pct": pos["final_pct"], "tps_hit": sum(pos["hit_tps"])})
        elif pos["status"] == "cancelled":
            out.append({"result": "cancelled", "pct": 0.0, "tps_hit": 0})
        else:
            unresolved += 1
    return out, unresolved


def _fmt_pf(pf):
    return "∞" if pf == float("inf") else f"{pf:.2f}"


def summarize_trades(trades):
    scored = [t for t in trades if t["result"] in ("win", "loss", "be")]
    n = len(scored)
    wins = [t for t in scored if t["result"] == "win"]
    losses = [t for t in scored if t["result"] == "loss"]
    bes = [t for t in scored if t["result"] == "be"]
    gp = sum(t["pct"] for t in scored if t["pct"] > 0)
    gl = -sum(t["pct"] for t in scored if t["pct"] < 0)
    return dict(
        signals=len(trades), activated=n, cancelled=sum(1 for t in trades if t["result"] == "cancelled"),
        wins=len(wins), losses=len(losses), be=len(bes),
        winrate=(len(wins) / n * 100 if n else 0.0),
        not_loss=((len(wins) + len(bes)) / n * 100 if n else 0.0),
        total_pct=sum(t["pct"] for t in scored), avg_pct=(sum(t["pct"] for t in scored) / n if n else 0.0),
        pf=(gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)),
    )


def run_backtest(days=None, tfs=None, symbols=None, send=True):
    days = days or CFG.backtest_days
    tfs = tuple(tfs) if tfs else CFG.backtest_tfs
    symbols = list(symbols) if symbols else [x for x in CFG.symbols][:int(CFG.backtest_max_symbols)]
    per_tf = {tf: [] for tf in tfs}
    gold_trades = []
    t0 = time.time()
    unresolved_total = 0

    for i, sym in enumerate(symbols, 1):
        for tf in tfs:
            if tf not in CFG.crypto_tfs:
                continue
            tfm = TF_MS[tf]
            lb = pump_lookback_for(tf)
            bars = int(days * 86400_000 / tfm) + lb + 60
            h1_tf = MTF_MAP[tf][0]
            try:
                df = _bt_fetch(sym, tf, bars)
                H1 = None
                if h1_tf:
                    hbars = int(days * 86400_000 / TF_MS[h1_tf]) + 260
                    H1 = htf_pack(_bt_fetch(sym, h1_tf, hbars), h1_tf)
                trades, unres = backtest_series(sym, tf, df, H1)
                per_tf[tf] += trades
                unresolved_total += unres
            except Exception as e:  # noqa
                log.warning("backtest skip %s %s: %s", sym, tf, e)
        log.info("backtest %d/%d %s done (%.0fs)", i, len(symbols), sym, time.time() - t0)

    if CFG.gold_enabled and os.getenv("BACKTEST_GOLD", "1") == "1":
        try:
            gdays = min(days, 10)
            tf = CFG.gold_tf
            bars = int(gdays * 86400_000 / TF_MS[tf]) + CFG.gold_lookback + 60
            df = _bt_fetch(CFG.gold_symbol, tf, bars, inst_override=CFG.gold_inst_id)
            hbars = int(gdays * 86400_000 / TF_MS[CFG.gold_htf]) + 260
            H1 = htf_pack(_bt_fetch(CFG.gold_symbol, CFG.gold_htf, hbars, inst_override=CFG.gold_inst_id), CFG.gold_htf)
            gold_trades, _u = backtest_series(CFG.gold_symbol, tf, df, H1, is_gold=True)
        except Exception as e:  # noqa
            log.warning("gold backtest skipped: %s", e)

    L = [f"🧪 نتیجه‌ی بک‌تست ({int(days)} روز، {len(symbols)} ارز، همان قوانین ربات)", ""]
    all_tr = []
    for tf in tfs:
        r = summarize_trades(per_tf[tf])
        all_tr += per_tf[tf]
        L.append(f"⏱ {tf}: {r['activated']} معامله | وین‌ریت {r['winrate']:.0f}% | بدون‌ضرر {r['not_loss']:.0f}% | "
                 f"میانگین {r['avg_pct']:.1f}% | PF {_fmt_pf(r['pf'])} | کنسل {r['cancelled']}")
    tot = summarize_trades(all_tr)
    L.append("")
    L.append(f"📊 مجموع کریپتو: {tot['activated']} معامله | برد {tot['wins']} / باخت {tot['losses']} / سر‌به‌سر {tot['be']}")
    L.append(f"وین‌ریت {tot['winrate']:.0f}% | مجموع بازدهی (اهرم {int(CFG.leverage)}×): {tot['total_pct']:.0f}% | "
             f"PF {_fmt_pf(tot['pf'])}")
    if gold_trades:
        g = summarize_trades(gold_trades)
        L.append("")
        L.append(f"🥇 طلا (5m): {g['activated']} معامله | وین‌ریت {g['winrate']:.0f}% | مجموع {g['total_pct']:.0f}%")
    if unresolved_total:
        L.append(f"(⏳ {unresolved_total} سیگنال تا پایان داده هنوز باز بودند و شمرده نشدند)")
    L.append("")
    L.append("ℹ️ شبیه‌سازی روی کندل‌های خودِ تایم‌فریم انجام می‌شود و در هر کندل بدترین ترتیب فرض می‌شود؛ "
             "در ربات زنده مانیتور با کندل ۵ دقیقه دقیق‌تر است. نتیجه‌ی گذشته تضمین آینده نیست.")
    text = "\n".join(L)
    print("\n" + "=" * 50 + "\n" + text + "\n" + "=" * 50)
    if send and CFG.token and CFG.chat_ids and os.getenv("BACKTEST_TELEGRAM", "1") == "1":
        for cid in CFG.chat_ids:
            try:
                tg_send(text, cid)
            except Exception as e:  # noqa
                log.error("backtest send failed %s: %s", cid, e)
    return text


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
    elif cmd == "backtest":
        def _arg(name, default=None):
            return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default
        run_backtest(
            days=float(_arg("--days")) if _arg("--days") else None,
            tfs=_arg("--tfs").split(",") if _arg("--tfs") else None,
            symbols=[x.strip().upper() for x in _arg("--symbols").split(",")] if _arg("--symbols") else None,
            send=not dry)
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

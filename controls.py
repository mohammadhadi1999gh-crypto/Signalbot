"""
Telegram control panel + reports (phase 1: no exchange orders here).

Only chat ids in TELEGRAM_CHAT_ID are accepted. Old commands (older than
10 minutes) are ignored. Settings are stored in settings.json inside DATA_DIR.
Trading is NOT implemented in this phase: trade_enabled is only a stored flag.
"""
import datetime as dt
import os
import time

import requests

import bot

SETTINGS_PATH = os.path.join(bot.HERE, 'settings.json')
OFFSET_PATH = os.path.join(bot.HERE, 'tg_offset.json')
MAX_CMD_AGE = 3600  # GitHub cron can lag; keep commands up to 1h old
TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))

DEFAULTS = {
    'signals_enabled': False,   # /on /off
    'trade_enabled': False,     # /trade_on /trade_off (no exchange code in phase 1)
    'margin_usdt': 0.20,        # per trade
    'leverage': 20,             # 1..20
    'margin_mode': 'isolated',  # cross | isolated
    'max_open': 3,              # max simultaneous positions
    'max_exposure_pct': 10,     # % of balance tied up at once
    'sides': 'both',            # long | short | both
    'daily_loss_pct': 10,       # stop trading after this % daily loss
    'hours': None,              # e.g. "08-18" (Tehran time) or None = 24h
}

PERIODS = {
    'today': 0, 'yesterday': 1, 'week': 7, 'month': 30, 'quarter': 90, 'year': 365,
}

OWNERS = set(str(x) for x in bot.CFG.chat_ids)


def load_settings():
    s = dict(DEFAULTS)
    s.update(bot.jload(SETTINGS_PATH, {}))
    return s


def save_settings(s):
    bot.jsave(SETTINGS_PATH, s)


def reply(text):
    for cid in OWNERS:
        try:
            bot.tg_send(text, cid)
        except Exception as e:  # noqa
            print('reply failed', e)


def in_hours(s, now=None):
    h = s.get('hours')
    if not h:
        return True
    try:
        a, b = [int(x) for x in h.split('-')]
    except Exception:
        return True
    cur = (now or dt.datetime.now(TEHRAN)).hour
    return a <= cur < b if a < b else (cur >= a or cur < b)


def _validate(key, val, s):
    if key == 'margin_usdt':
        v = float(val)
        if v <= 0:
            raise ValueError('حجم باید بزرگ‌تر از صفر باشد')
        return v
    if key == 'leverage':
        v = int(val)
        if not 1 <= v <= 20:
            raise ValueError('اهرم باید بین ۱ و ۲۰ باشد')
        return v
    if key == 'margin_mode':
        if val not in ('cross', 'isolated'):
            raise ValueError('cross یا isolated')
        return val
    if key == 'max_open':
        v = int(val)
        if v < 1:
            raise ValueError('حداقل ۱')
        return v
    if key == 'max_exposure_pct':
        v = float(val)
        if not 0 < v <= 100:
            raise ValueError('بین ۰ و ۱۰۰')
        return v
    if key == 'sides':
        if val not in ('long', 'short', 'both'):
            raise ValueError('long | short | both')
        return val
    if key == 'daily_loss_pct':
        v = float(val)
        if not 0 < v <= 100:
            raise ValueError('بین ۰ و ۱۰۰')
        return v
    if key == 'hours':
        if val in ('off', 'none', '-'):
            return None
        a, b = val.split('-')
        a, b = int(a), int(b)
        if not (0 <= a < 24 and 0 < b <= 24 and a != b):
            raise ValueError('مثل 08-18')
        return f'{a:02d}-{b:02d}'
    raise ValueError('کلید ناشناخته')


def _stats(rows):
    closed = [r for r in rows if r.get('result') in ('win', 'loss', 'be')]
    n = len(closed)
    if not n:
        return {'n': 0}
    wins = sum(1 for r in closed if r['result'] == 'win')
    pct = sum(r.get('final_pct') or 0 for r in closed)
    return {'n': n, 'wr': wins / n * 100, 'pct': pct}


def _group(rows, keyfn):
    g = {}
    for r in rows:
        k = keyfn(r)
        g.setdefault(k, []).append(r)
    out = {k: _stats(v) for k, v in g.items()}
    return {k: v for k, v in out.items() if v.get('n')}


def _ts(r):
    return r.get('closed_at') or r.get('opened_at') or 0


def report_text(period):
    days = PERIODS[period]
    now = time.time()
    if period == 'yesterday':
        start = now - 2 * 86400
        end = now - 86400
    elif days == 0:
        t = dt.datetime.now(TEHRAN).replace(hour=0, minute=0, second=0, microsecond=0)
        start, end = t.timestamp(), now + 1
    else:
        start, end = now - days * 86400, now + 1
    pos = bot.jload(bot.POSITIONS_PATH, {})
    rows = [p for p in pos.values() if start <= _ts(p) <= end]
    tot = _stats(rows)
    if not tot.get('n'):
        return f'📊 گزارش {period}: معامله‌ی بسته‌شده‌ای نیست.'
    lines = [
        f'📊 گزارش {period}',
        f"معاملات بسته: {tot['n']} | وین‌ریت: {tot['wr']:.0f}٪ | برایند: {tot['pct']:+.1f}٪",
    ]
    def top(g, title):
        if not g:
            return
        items = sorted(g.items(), key=lambda kv: kv[1]['pct'], reverse=True)
        lines.append(title)
        for k, v in items[:3]:
            lines.append(f"  {k}: {v['pct']:+.1f}٪ ({v['n']} معامله، وین {v['wr']:.0f}٪)")
        lines.append('  ...' if len(items) > 3 else '')
        for k, v in items[-3:][::-1] if len(items) > 3 else []:
            lines.append(f"  ضعیف: {k}: {v['pct']:+.1f}٪")
    top(_group(rows, lambda r: r.get('symbol', '?').replace('USDT', '')), 'ارزها (بهترین):')
    top(_group(rows, lambda r: r.get('tf', '?')), 'تایم‌فریم‌ها:')
    top(_group(rows, lambda r: 'Short' if r.get('side') == -1 else 'Long'), 'جهت‌ها:')
    top(_group(rows, lambda r: dt.datetime.fromtimestamp(_ts(r), TEHRAN).strftime('%H:00')), 'ساعت‌ها (تهران):')
    days_fa = ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنج‌شنبه', 'جمعه', 'شنبه', 'یکشنبه']
    top(_group(rows, lambda r: days_fa[dt.datetime.fromtimestamp(_ts(r), TEHRAN).weekday()]), 'روزهای هفته:')
    return '\n'.join(x for x in lines if x)


def menu_text():
    return (
        '📋 دستورها:\n'
        '/on /off — روشن/خاموش سیگنال\n'
        '/trade_on /trade_off — معامله‌ی خودکار (فعلاً فقط پرچم)\n'
        '/status — تنظیمات فعلی\n'
        '/set کلید مقدار — مثال: /set leverage 10\n'
        '  کلیدها: margin_usdt، leverage (۱–۲۰)، margin_mode (cross|isolated)،\n'
        '  max_open، max_exposure_pct، sides (long|short|both)،\n'
        '  daily_loss_pct، hours (مثل 08-18 یا off)\n'
        '/report امروز|دیروز|هفته|ماه|سه_ماه|سال\n'
        '/report_en today|yesterday|week|month|quarter|year'
    )


def handle_commands():
    token = os.getenv('TELEGRAM_TOKEN', '')
    if not token:
        return
    off = bot.jload(OFFSET_PATH, {'offset': 0}).get('offset', 0)
    try:
        r = requests.get(f'https://api.telegram.org/bot{token}/getUpdates',
                         params={'offset': off, 'timeout': 0}, timeout=20)
        updates = r.json().get('result', [])
    except Exception as e:  # noqa
        print('getUpdates failed', e)
        return
    new_off = off
    for u in updates:
        new_off = max(new_off, u['update_id'] + 1)
        msg = u.get('message') or {}
        chat = str((msg.get('chat') or {}).get('id', ''))
        if chat not in OWNERS or time.time() - msg.get('date', 0) > MAX_CMD_AGE:
            continue
        parts = (msg.get('text') or '').strip().split()
        if not parts:
            continue
        cmd = parts[0].lower().split('@')[0]
        args = parts[1:]
        s = load_settings()
        if cmd in ('/menu', '/help', '/start'):
            reply(menu_text())
        elif cmd == '/on':
            s['signals_enabled'] = True
            save_settings(s)
            reply('✅ دریافت سیگنال روشن شد.')
        elif cmd == '/off':
            s['signals_enabled'] = False
            save_settings(s)
            reply('⏸ دریافت سیگنال خاموش شد. پوزیشن‌های باز همچنان چک می‌شوند.')
        elif cmd == '/trade_on':
            s['trade_enabled'] = True
            save_settings(s)
            reply('⚠️ پرچم معامله روشن شد، ولی در این مرحله ماژول معاملاتی نصب نیست و سفارشی ارسال نمی‌شود.')
        elif cmd == '/trade_off':
            s['trade_enabled'] = False
            save_settings(s)
            reply('🛑 معامله‌ی خودکار خاموش شد.')
        elif cmd == '/status':
            reply('⚙️ تنظیمات:\n' + '\n'.join(f'{k}: {v}' for k, v in s.items()))
        elif cmd == '/set' and len(args) == 2:
            try:
                s[args[0]] = _validate(args[0], args[1], s)
                save_settings(s)
                reply(f'✅ {args[0]} = {s[args[0]]}')
            except Exception as e:  # noqa
                reply(f'❌ مقدار نامعتبر: {e}')
        elif cmd == '/report' and args:
            p = args[0].lower()
            names = {'امروز': 'today', 'دیروز': 'yesterday', 'هفته': 'week',
                     'ماه': 'month', 'سه_ماه': 'quarter', 'سال': 'year'}
            p = names.get(p, p)
            reply(report_text(p) if p in PERIODS else 'دوره‌ی نامعتبر: امروز|دیروز|هفته|ماه|سه_ماه|سال')
        elif cmd == '/report_en' and args and args[0] in PERIODS:
            reply(report_text(args[0]))
        else:
            reply(menu_text())
    if new_off != off:
        bot.jsave(OFFSET_PATH, {'offset': new_off})

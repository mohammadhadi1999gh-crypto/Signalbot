"""
Daily delivery of the signals log.
Once per Tehran day (first run after midnight) it sends the cumulative
signals_log.jsonl to the owner chats, with a summary of the previous day.
"""
import datetime as dt
import json
import os

import requests

import bot

TEH = dt.timezone(dt.timedelta(hours=3, minutes=30))
STATE_PATH = os.path.join(bot.HERE, 'daily_sent.json')


def _rows():
    rows = []
    try:
        with open(bot.SIGNALS_LOG_PATH, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except FileNotFoundError:
        pass
    return rows


def _summary(day_rows, day):
    def count(ev, **kw):
        return sum(1 for r in day_rows if r.get('event') == ev
                   and all(r.get(k) == v for k, v in kw.items()))
    lines = [
        f'📅 خلاصه‌ی {day}',
        f'🆕 سیگنال جدید: {count("new_signal")}',
        f'🔥 فعال شده: {count("activated")}',
        f'🎯 TP گرفته شده: {count("tp")}',
        f'🛑 بسته شده با ضرر: {count("closed", result="loss")}',
        f'🏆 بسته شده با سود: {count("closed", result="win")}',
        f'❌ کنسل شده: {count("cancelled")}',
    ]
    return '\n'.join(lines)


def _send_document(path, caption):
    token = os.getenv('TELEGRAM_TOKEN', '')
    if not token:
        return
    url = f'https://api.telegram.org/bot{token}/sendDocument'
    for cid in bot.CFG.chat_ids:
        try:
            with open(path, 'rb') as f:
                requests.post(url, data={'chat_id': cid, 'caption': caption[:1000]},
                              files={'document': (os.path.basename(path), f)}, timeout=60)
        except Exception as e:  # noqa
            print('daily send failed', e)


def run():
    today = dt.datetime.now(TEH).date().isoformat()
    st = bot.jload(STATE_PATH, {})
    last = st.get('last_sent')
    if not last:
        bot.jsave(STATE_PATH, {'last_sent': today})
        return
    if last == today:
        return
    rows = _rows()
    day_rows = [r for r in rows if str(r.get('ts', '')).startswith(last)]
    summary = _summary(day_rows, last)
    if os.path.exists(bot.SIGNALS_LOG_PATH):
        _send_document(bot.SIGNALS_LOG_PATH, summary + f'\n\nفایل تجمعی تا {last}')
    bot.jsave(STATE_PATH, {'last_sent': today})

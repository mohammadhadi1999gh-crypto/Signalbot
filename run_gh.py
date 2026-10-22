"""
One GitHub Actions run:
  1. read Telegram commands (/on, /off, /status) from the owner chat only
  2. if enabled: monitor open positions + scan for new FVG signals
     if disabled: only monitor open positions (SL/TP still tracked), no new signals
  3. write docs/data.json for the dashboard (published by the workflow)
Nothing here places exchange orders.
"""
import json
import os
import time

import requests

import bot

if os.getenv('STRAT_MODE', 'fvg') == 'fvg':
    import fvg
    fvg.install(bot)

HERE = bot.HERE
CONTROL = os.path.join(HERE, 'control.json')
OFFSET = os.path.join(HERE, 'tg_offset.json')
DOCS = os.path.join(HERE, 'docs')
OWNER_IDS = set(str(x) for x in bot.CFG.chat_ids)
MAX_CMD_AGE = 600  # seconds; older commands are ignored so a stale /on never replays


def read_enabled():
    return bool(bot.jload(CONTROL, {'enabled': False}).get('enabled', False))


def set_enabled(val):
    bot.jsave(CONTROL, {'enabled': bool(val), 'changed_at': int(time.time())})


def reply(text):
    for cid in OWNER_IDS:
        try:
            bot.tg_send(text, cid)
        except Exception as e:  # noqa
            print('reply failed', e)


def handle_commands():
    token = os.getenv('TELEGRAM_TOKEN', '')
    if not token:
        return
    off = bot.jload(OFFSET, {'offset': 0}).get('offset', 0)
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
        text = (msg.get('text') or '').strip().lower()
        age = time.time() - msg.get('date', 0)
        if chat not in OWNER_IDS or age > MAX_CMD_AGE:
            continue
        cmd = text.split('@')[0]
        if cmd == '/on':
            set_enabled(True)
            reply('✅ ربات روشن شد. سیگنال‌های جدید ارسال می‌شوند.')
        elif cmd == '/off':
            set_enabled(False)
            reply('⏸ ربات خاموش شد. پوزیشن‌های باز همچنان چک می‌شوند، ولی سیگنال جدیدی ثبت نمی‌شود.')
        elif text == '/status':
            reply('وضعیت: ' + ('روشن' if read_enabled() else 'خاموش'))
    if new_off != off:
        bot.jsave(OFFSET, {'offset': new_off})


def export_dashboard():
    os.makedirs(DOCS, exist_ok=True)
    pos = bot.jload(bot.POSITIONS_PATH, {})
    rows = []
    for pid, p in pos.items():
        rows.append({
            'id': pid, 'symbol': p.get('symbol'), 'tf': p.get('tf'), 'side': p.get('side'),
            'entry': p.get('entry'), 'sl': p.get('sl'), 'tps': p.get('tps'),
            'status': p.get('status'), 'result': p.get('result'),
            'hit_tps': p.get('hit_tps'), 'final_pct': p.get('final_pct'),
            'opened_at': p.get('opened_at'), 'closed_at': p.get('closed_at'),
        })
    bot.jsave(os.path.join(DOCS, 'data.json'), {
        'generated_at': int(time.time()),
        'enabled': read_enabled(),
        'signals': rows,
    })


def main():
    handle_commands()
    if read_enabled():
        bot.once(False)
    else:
        bot.monitor_positions(False)
    export_dashboard()


if __name__ == '__main__':
    main()

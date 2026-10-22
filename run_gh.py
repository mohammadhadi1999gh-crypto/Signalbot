"""
One GitHub Actions run:
  1. read Telegram commands (controls.py) from the owner chat only
  2. if signals are on and inside the allowed hours: monitor open positions + scan
     otherwise: only monitor open positions (SL/TP still tracked)
  3. write docs/data.json for the dashboard
No exchange orders are placed here.
"""
import json
import os
import time

import bot

if os.getenv('STRAT_MODE', 'fvg') == 'fvg':
    import fvg
    fvg.install(bot)

import controls  # noqa: E402

DOCS = os.path.join(bot.HERE, 'docs')


def export_dashboard(s):
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
        'enabled': bool(s.get('signals_enabled')),
        'trade_enabled': bool(s.get('trade_enabled')),
        'settings': s,
        'signals': rows,
    })


def main():
    controls.handle_commands()
    s = controls.load_settings()
    if s.get('signals_enabled') and controls.in_hours(s):
        bot.once(False)
    else:
        bot.monitor_positions(False)
    import features
    features.record_outcomes()
    import learner
    learner.run_and_notify()
    export_dashboard(s)


if __name__ == '__main__':
    main()

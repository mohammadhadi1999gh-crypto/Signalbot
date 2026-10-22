"""
One GitHub Actions run (about every 5 minutes):
  1. read Telegram commands (controls.py), owner chat only
  2. if signals are on and inside allowed hours: monitor open positions + scan
     otherwise: only monitor open positions (SL/TP still tracked)
  3. record outcomes, run self-learning filters
  4. write docs/data.json for the live dashboard
No exchange orders are placed here.
"""
import os
import time

import bot

if os.getenv('STRAT_MODE', 'fvg') == 'fvg':
    import fvg
    fvg.install(bot)

import controls  # noqa: E402
import dashboard_data  # noqa: E402
import features  # noqa: E402
import learner  # noqa: E402

DOCS = os.path.join(bot.HERE, 'docs')


def main():
    controls.handle_commands()
    s = controls.load_settings()
    if s.get('signals_enabled') and controls.in_hours(s):
        bot.once(False)
    else:
        bot.monitor_positions(False)
    features.record_outcomes()
    learner.run_and_notify()

    os.makedirs(DOCS, exist_ok=True)
    data = dashboard_data.build(s, s.get('signals_enabled'), bot.last_price)
    data['run_at'] = int(time.time())
    bot.jsave(os.path.join(DOCS, 'data.json'), data)


if __name__ == '__main__':
    main()

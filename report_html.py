import json, os, sys, html, datetime as dt, collections as C

SRC, OUT = sys.argv[1], sys.argv[2]
FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts', 'IRNazanin-Bold.ttf')
rows = [json.loads(l) for l in open(SRC, encoding='utf-8') if l.strip()]

def num(x):
    return str(x).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))
def fmt(x, d=1):
    s = f"{x:+,.{d}f}".replace(',', '٬')
    return num(s)
def pc(x, d=0):
    return num(f"{x:.{d}f}") + '٪'
MONTHS = {'11': 'نوامبر', '10': 'اکتبر'}
def daylab(md):
    m, d = md.split('-')
    return f"{num(int(d))} {MONTHS.get(m, m)}"

ns = [r for r in rows if r['event'] == 'new_signal']
ac = [r for r in rows if r['event'] == 'activated']
ca = [r for r in rows if r['event'] == 'cancelled']
cl = [r for r in rows if r['event'] == 'closed']
N, A, K, CA = len(ns), len(ac), len(cl), len(ca)
W = sum(r['result'] == 'win' for r in cl); L = K - W
TOT = sum(r['pct'] for r in cl)
d0 = dt.date.fromisoformat(rows[0]['ts'][:10]); d1 = dt.date.fromisoformat(rows[-1]['ts'][:10])
SPAN = (d1 - d0).days + 1
PERIOD = f"{num(d0.day)} نوامبر تا {num(d1.day)} نوامبر ۲۰۲۶"
peak_day, peak_n = max(C.Counter(r['ts'][:10] for r in ns).items(), key=lambda kv: kv[1])
PEAK = f"{num(int(peak_day[8:10]))} نوامبر، {num(peak_n)} سیگنال"

# exits
tp3 = [r for r in cl if r['reason'] == 'tp3']
be = [r for r in cl if r['reason'] == 'sl' and any(r['hit_tps'])]
sl = [r for r in cl if r['reason'] == 'sl' and not any(r['hit_tps'])]
avg = lambda xs: sum(x['pct'] for x in xs) / len(xs) if xs else 0
tps_cnt = C.Counter(sum(1 for x in r['hit_tps'] if x) for r in cl)

# per symbol
S = C.defaultdict(lambda: dict(n=0, w=0, l=0, pct=0.0, tps=C.Counter(), tf=C.defaultdict(lambda: [0, 0, 0.0]), sl_only=0))
for r in cl:
    s = S[r['symbol']]
    s['n'] += 1; s['w'] += r['result'] == 'win'; s['l'] += r['result'] == 'loss'; s['pct'] += r['pct']
    k = sum(1 for x in r['hit_tps'] if x)
    s['tps'][k] += 1
    s['sl_only'] += (r['reason'] == 'sl' and k == 0)
    t = s['tf'][r['tf']]; t[0] += 1; t[1] += r['result'] == 'win'; t[2] += r['pct']
sym_rows = sorted(S.items(), key=lambda kv: -kv[1]['pct'])

TF_ORDER = ['15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d']
TF = {}
for tf in TF_ORDER:
    c = [r for r in cl if r['tf'] == tf]
    TF[tf] = dict(new=sum(r['tf'] == tf for r in ns), act=sum(r['tf'] == tf for r in ac),
                  closed=len(c), win=sum(r['result'] == 'win' for r in c), pct=sum(r['pct'] for r in c))

days = C.defaultdict(lambda: [0, 0, 0.0])
for r in cl:
    x = days[r['ts'][5:10]]; x[0] += 1; x[1] += r['result'] == 'win'; x[2] += r['pct']
days = sorted(days.items())
best_day = max(days, key=lambda kv: kv[1][2]); worst_day = min(days, key=lambda kv: kv[1][2])

ns_cnt = C.Counter(r['symbol'] for r in ns)
ruleA = [s for s in S if ns_cnt[s] > 3 and S[s]['n'] > 0 and S[s]['w'] == 0]
ruleB_buggy = [s for s in S if S[s]['n'] >= 3 and (S[s]['sl_only'] + 0) / S[s]['n'] >= 0.6]
ruleB_buggy = [s for s in S if S[s]['n'] >= 3 and sum(1 for r in cl if r['symbol'] == s and r['reason'] == 'sl') / S[s]['n'] >= 0.6]
ruleB_fixed = [s for s in S if S[s]['n'] >= 3 and S[s]['sl_only'] / S[s]['n'] >= 0.6]
strong = [s for s, v in sym_rows if v['n'] >= 2 and v['w'] == v['n']]
sn = lambda s: s.replace('USDT', '')

# ---------- HTML helpers ----------
def hbar_rows(items):
    items = [(sn(l), v) for l, v in items]
    mx = max(max(abs(v) for _, v in items), 1)
    out = []
    for lab, v in items:
        w = abs(v) / mx * 50
        side = 'left:50%' if v >= 0 else 'right:50%'
        col = '#16a34a' if v >= 0 else '#dc2626'
        out.append(f'<div class="hb"><span class="lab">{html.escape(lab)}</span>'
                   f'<div class="track"><div class="bar" style="{side};width:{w:.1f}%;background:{col}"></div></div>'
                   f'<span class="val {"g" if v>=0 else "r"}">{fmt(v,0)}٪</span></div>')
    return ''.join(out)

def vcols(items, half=70, signed=True):
    mx = max(max(abs(v) for _, v in items), 1)
    cols = []
    for lab, v in items:
        h = abs(v) / mx * half
        pos = (f'bottom:50%;height:{h:.1f}px;background:#16a34a' if v >= 0
               else f'top:50%;height:{h:.1f}px;background:#dc2626')
        cols.append(f'<div class="vc"><div class="vtop"><div class="vb" style="{pos}"></div></div>'
                    f'<div class="vlab">{html.escape(lab)}</div><div class="vval">{fmt(v,0) if signed else num(int(v))}</div></div>')
    return f'<div class="vwrap">{"".join(cols)}</div>'

def funnel_rows(items):
    mx = items[0][1]
    return ''.join(
        f'<div class="fn"><span class="flab">{lab}</span><div class="ftrack"><div class="fbar" style="width:{max(v/mx*100,4):.1f}%;background:{col}">{num(v)}</div></div></div>'
        for lab, v, col in items)

def donut(parts):
    total = sum(v for _, v, _ in parts)
    acc, stops = 0.0, []
    for _, v, col in parts:
        a = v / total * 100
        stops.append(f'{col} {acc:.2f}% {acc+a:.2f}%'); acc += a
    legend = ''.join(f'<div class="lg"><span style="background:{col}"></span>{lab}: <b>{num(v)}</b></div>' for lab, v, col in parts)
    return (f'<div class="donut"><div class="ring" style="background:conic-gradient({",".join(stops)})">'
            f'<div class="hole"><b>{num(total)}</b><small>معامله بسته</small></div></div><div>{legend}</div></div>')

# ---------- content ----------
kpis = [('سیگنال جدید', num(N), '#2563eb'),
        ('فعال‌شده', f"{num(A)} ({pc(100*A/N,1)})", '#7c3aed'),
        ('بسته‌شده', num(K), '#0891b2'),
        ('وین‌ریت', pc(100*W/K), '#16a34a'),
        ('برایند (اهرمی)', fmt(TOT), '#16a34a' if TOT >= 0 else '#dc2626'),
        ('بهترین روز', f"{daylab(best_day[0])} ({fmt(best_day[1][2],0)})", '#16a34a')]
kpi_html = ''.join(f'<div class="kpi" style="border-top:4px solid {c}"><small>{l}</small><b>{v}</b></div>' for l, v, c in kpis)

exit_table = (f'<table><tr><th>دسته</th><th>تعداد</th><th>میانگین درصد</th></tr>'
              f'<tr><td>TP3</td><td>{num(len(tp3))}</td><td class="g">{fmt(avg(tp3))}٪</td></tr>'
              f'<tr><td>SL بعد از TP1</td><td>{num(len(be))}</td><td class="g">{fmt(avg(be))}٪</td></tr>'
              f'<tr><td>SL بدون TP</td><td>{num(len(sl))}</td><td class="r">{fmt(avg(sl))}٪</td></tr></table>')

sym_top = [(s, v['pct']) for s, v in sym_rows if v['n'] >= 2]
sym_chart = hbar_rows(sym_top[:12] + sym_top[-12:])

tf_bars = hbar_rows([(tf, TF[tf]['pct']) for tf in TF_ORDER if TF[tf]['closed']])
tf_table = ('<table><tr><th>تایم‌فریم</th><th>سیگنال</th><th>فعال</th><th>نرخ فعال‌شدن</th><th>بسته</th><th>برد</th><th>وین‌ریت</th><th>برایند</th></tr>')
for tf in TF_ORDER:
    t = TF[tf]
    if not t['new']:
        continue
    wr = 100 * t['win'] / t['closed'] if t['closed'] else 0
    tf_table += (f"<tr><td>{tf}</td><td>{num(t['new'])}</td><td>{num(t['act'])}</td><td>{pc(100*t['act']/t['new'],1)}</td>"
                 f"<td>{num(t['closed'])}</td><td>{num(t['win'])}</td><td>{pc(wr)}</td>"
                 f"<td class='{'g' if t['pct']>=0 else 'r'}'>{fmt(t['pct'])}٪</td></tr>")
tf_table += '</table>'

day_chart = vcols([(daylab(d), v[2]) for d, v in days])
day_table = '<table><tr><th>روز</th><th>معامله</th><th>برد</th><th>وین‌ریت</th><th>برایند</th></tr>'
for d, (n, w, p) in days:
    day_table += f"<tr><td>{daylab(d)}</td><td>{num(n)}</td><td>{num(w)}</td><td>{pc(100*w/n)}</td><td class='{'g' if p>=0 else 'r'}'>{fmt(p)}٪</td></tr>"
day_table += '</table>'

sym_table = ('<table class="big"><tr><th>ارز</th><th>معامله</th><th>برد</th><th>باخت</th><th>وین‌ریت</th><th>برایند</th>'
             '<th>بهترین TF</th><th>بدترین TF</th><th>۰ TP</th><th>۱ TP</th><th>۲ TP</th><th>۳ TP</th></tr>')
for sym, s in sym_rows:
    tfs = sorted(s['tf'].items(), key=lambda kv: -kv[1][2])
    b, wv = tfs[0], tfs[-1]
    sym_table += (f"<tr><td>{sn(sym)}</td><td>{num(s['n'])}</td><td>{num(s['w'])}</td><td>{num(s['l'])}</td>"
                  f"<td>{pc(100*s['w']/s['n'])}</td><td class='{'g' if s['pct']>=0 else 'r'}'>{fmt(s['pct'])}٪</td>"
                  f"<td>{b[0]} ({fmt(b[1][2],0)})</td><td>{wv[0]} ({fmt(wv[1][2],0)})</td>"
                  f"<td>{num(s['tps'][0])}</td><td>{num(s['tps'][1])}</td><td>{num(s['tps'][2])}</td><td>{num(s['tps'][3])}</td></tr>")
sym_table += '</table>'

lst = lambda xs: '، '.join(sn(x) for x in xs) or 'هیچ'
f30 = TF['30m']
CSS = f"""
@font-face {{ font-family: 'IRNazanin'; src: url('file://{FONT}'); }}
@page {{ size: A4; margin: 12mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: 'IRNazanin', 'DejaVu Sans', sans-serif; direction: rtl; color:#1f2937; font-size:12px; margin:0; }}
h1 {{ font-size:22px; margin:0; }} h2 {{ font-size:16px; border-right:4px solid #2563eb; padding-right:8px; margin:16px 0 8px; page-break-after:avoid; }}
.hero {{ background:linear-gradient(135deg,#1e3a8a,#2563eb); color:#fff; border-radius:12px; padding:16px 18px; }}
.kpis {{ display:grid; grid-template-columns:repeat(6,1fr); gap:6px; margin-top:10px; }}
.kpi {{ background:rgba(255,255,255,.14); border-radius:8px; padding:7px; }} .kpi small {{ display:block; opacity:.85; }} .kpi b {{ font-size:14px; direction:ltr; display:inline-block; }}
.card {{ background:#fff; border:1px solid #e5e7eb; border-radius:10px; padding:10px; margin-bottom:8px; page-break-inside:avoid; }}
.two {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }}
.note {{ background:#fffbeb; border-right:4px solid #f59e0b; padding:7px 9px; border-radius:6px; margin-top:6px; }}
.bad {{ background:#fef2f2; border-right:4px solid #dc2626; padding:7px 9px; border-radius:6px; }}
.ok {{ background:#f0fdf4; border-right:4px solid #16a34a; padding:7px 9px; border-radius:6px; margin-top:6px; }}
table {{ width:100%; border-collapse:collapse; font-size:11px; }} th,td {{ padding:4px; border-bottom:1px solid #eef0f3; text-align:right; }} th {{ background:#f3f4f6; font-weight:normal; }}
table.big {{ font-size:9.5px; }} table.big td,table.big th {{ padding:3px; }}
.g {{ color:#15803d; }} .r {{ color:#b91c1c; }}
.hb {{ display:flex; align-items:center; gap:6px; margin:3px 0; }} .lab {{ width:70px; text-align:left; direction:ltr; font-size:11px; }}
.track {{ flex:1; position:relative; height:13px; background:linear-gradient(to right,transparent 49.8%,#9ca3af 49.8%,#9ca3af 50.2%,transparent 50.2%); }}
.bar {{ position:absolute; top:0; height:100%; border-radius:3px; }} .val {{ width:70px; direction:ltr; font-size:11px; }}
.fn {{ display:flex; align-items:center; gap:8px; margin:6px 0; }} .flab {{ width:170px; }} .ftrack {{ flex:1; }}
.fbar {{ color:#fff; padding:6px 8px; border-radius:6px; font-weight:bold; }}
.donut {{ display:flex; align-items:center; gap:16px; }} .ring {{ width:170px; height:170px; border-radius:50%; position:relative; }}
.hole {{ position:absolute; inset:38px; background:#fff; border-radius:50%; display:flex; flex-direction:column; align-items:center; justify-content:center; }}
.hole b {{ font-size:20px; }} .hole small {{ color:#6b7280; }}
.lg {{ margin:4px 0; }} .lg span {{ display:inline-block; width:12px; height:12px; border-radius:3px; margin-left:6px; vertical-align:middle; }}
.vwrap {{ display:flex; align-items:stretch; gap:6px; height:200px; }} .vc {{ flex:1; display:flex; flex-direction:column; align-items:center; }}
.vtop {{ position:relative; flex:1; width:100%; border-bottom:1px solid #9ca3af; }} .vb {{ position:absolute; left:20%; width:60%; border-radius:3px; }}
.vlab {{ font-size:10px; margin-top:3px; text-align:center; }} .vval {{ font-size:10px; direction:ltr; color:#374151; }}
.foot {{ color:#9ca3af; font-size:10px; margin-top:8px; }}
"""

body = f"""
<div class="hero"><h1>گزارش عملکرد ربات سیگنال FVG</h1>
<p style="margin:5px 0 0;opacity:.9">بازه: {PERIOD} · منبع: signals_log.jsonl · ارز OKX</p>
<div class="kpis">{kpi_html}</div></div>

<h2>۱. خلاصه اجرایی</h2>
<div class="card">
<p>در این بازه <b>{num(N)}</b> سیگنال ارسال شد و فقط <b>{pc(100*A/N,1)}</b> آن‌ها وارد معامله شدند. <b>{num(CA)}</b> سیگنال قبل از ورود کنسل شد، چون قیمت زودتر به TP1 رسید. از <b>{num(K)}</b> معامله بسته‌شده، <b>{num(W)}</b> برد و <b>{num(L)}</b> باخت داشتیم (وین‌ریت {pc(100*W/K)}). برایند کل با اهرم ۲۰ برابر <b>{fmt(TOT)}</b> درصد است. این عدد سود واقعی حساب نیست، ولی جهت عملکرد را نشان می‌دهد.</p>
<div class="note">مهم‌ترین مشکل: بیشتر سیگنال‌ها قبل از ورود کنسل می‌شوند، چون قیمت از محدوده گذشته است. اصلاح اصلی باید روی فیلتر ورود باشد.</div>
</div>

<h2>۲. قیف سیگنال‌ها</h2>
<div class="card">{funnel_rows([('سیگنال جدید', N, '#2563eb'), ('فعال‌شده (وارد معامله)', A, '#7c3aed'), ('بسته‌شده', K, '#0891b2'), ('کنسل‌شده (قبل از ورود)', CA, '#94a3b8')])}</div>

<h2>۳. نتیجه خروج معاملات</h2>
<div class="two">
<div class="card">{donut([('رسیدن به TP3', len(tp3), '#16a34a'), ('SL بعد از TP1 (سود جزئی)', len(be), '#84cc16'), ('SL بدون TP', len(sl), '#dc2626')])}</div>
<div class="card">{exit_table}<p class="foot">درصدها با اهرم ۲۰ محاسبه شده‌اند.</p></div>
</div>
<div class="card"><b>تعداد TP زده‌شده در هر معامله</b>{vcols([('۰ TP', tps_cnt[0]), ('۱ TP', tps_cnt[1]), ('۲ TP', tps_cnt[2]), ('۳ TP', tps_cnt[3])], half=60, signed=False)}</div>

<h2>۴. برایند هر ارز (ارزهای با حداقل ۲ معامله)</h2>
<div class="card">{sym_chart}</div>

<h2>۵. عملکرد هر تایم‌فریم</h2>
<div class="card">{tf_bars}</div>
<div class="card">{tf_table}</div>
<div class="ok">۳۰ دقیقه بهترین تایم‌فریم است: وین‌ریت {pc(100*f30['win']/f30['closed'])} و برایند {fmt(f30['pct'],0)}٪. ۲ ساعت، ۴ ساعت و ۱۵ دقیقه ضرر خالص داده‌اند.</div>

<h2>۶. بهترین و بدترین روز معاملاتی</h2>
<div class="card">{day_chart}</div>
<div class="card">{day_table}</div>
<p>بهترین روز <b>{daylab(best_day[0])}</b> با {num(best_day[1][0])} معامله، {num(best_day[1][1])} برد و برایند {fmt(best_day[1][2],0)}٪ بود. بدترین روز <b>{daylab(worst_day[0])}</b> با برایند {fmt(worst_day[1][2],0)}٪ بود.</p>

<h2>۷. جدول کامل هر ارز</h2>
<div class="card">{sym_table}</div>
<p class="foot">TP۰ تا TP۳ یعنی تعداد معاملاتی که صفر تا سه هدف را زده‌اند. ارزهایی که فقط یک معامله دارند نتیجه آماری ندارند.</p>

<h2>۸. پیشنهادهای اصلاحی</h2>
<div class="two">
<div class="bad"><b>بلاک با قانون (الف)</b> (بیش از ۳ سیگنال و بدون برد):<br>{lst(ruleA)}</div>
<div class="note"><b>قانون (ب) در کد باگ دارد.</b> در کد فعلی هر خروج با SL حساب می‌شود، حتی اگر TP1 خورده باشد. با حذف این باگ، با قانون درست (فقط SL بدون TP) بلاک می‌شود: {lst(ruleB_fixed)}</div>
</div>
<div class="card"><ul>
<li><b>فیلتر ورود:</b> سیگنالی که قیمت فعلی‌اش پایین‌تر از TP1 است نفرستیم. این کار {num(CA)} کنسل بی‌فایده را حذف می‌کند.</li>
<li><b>تایم‌فریم:</b> ۱۵ دقیقه، ۲ ساعت و ۴ ساعت برایند منفی دارند و بررسی غیرفعال‌سازی آن‌ها پیشنهاد می‌شود. ۱۲ ساعت به بالا نمونه کافی ندارند.</li>
<li><b>تعداد سیگنال:</b> در روزهای پرحجم ({PEAK}) حجم زیادی نویز است. می‌توان محدودیت روزانه گذاشت.</li>
<li><b>ارزهای قوی در ۳۰ دقیقه:</b> {lst(strong)}.</li>
</ul></div>

<h2>۹. محدودیت‌های گزارش</h2>
<div class="card"><ul>
<li>نمونه کوچک است: {num(K)} معامله بسته در {num(SPAN)} روز. نتیجه‌گیری قطعی نیست.</li>
<li>درصدها با اهرم ۲۰ محاسبه شده‌اند و سود واقعی حساب نیستند. کارمزد، اسلیپیج و فاندینگ لحاظ نشده است.</li>
<li>بک‌تست خارج از نمونه انجام نشده است.</li>
</ul></div>
<div class="foot">تولید شده در {num(d1.day)} نوامبر ۲۰۲۶ · این گزارش مشاوره سرمایه‌گذاری نیست.</div>
"""
open(OUT, 'w', encoding='utf-8').write(f'<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><style>{CSS}</style></head><body>{body}</body></html>')
print('ok', N, A, K, CA, W, round(TOT,1), PERIOD)

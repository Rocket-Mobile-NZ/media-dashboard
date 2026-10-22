#!/usr/bin/env python3
"""Deterministic spend / CPA / orders calculations for the Rocket Mobile dashboard.

The LLM that runs the daily refresh only FETCHES data (appends rows to the dr-data block, mom-data, etc.).
Every blended-spend, CPA, run-rate, delta and chart-position number is computed here, from the data that is
already embedded in index.html plus build_config.json, so there is no LLM arithmetic involved.

    python3 build.py            recompute everything and write index.html
    python3 build.py --check    recompute, change nothing, exit 1 if any displayed number differs
    python3 build.py --print    print the headline figures
    python3 build.py --init     (re)insert the <!--c:...--> region markers (only needed if they were lost)

Owned regions (never hand-edit inside <!--c:NAME-->...<!--/c:NAME-->): big, lead, kpis, bars, rsc, trend, budget,
plus the dr-data radio_cost entries for months listed in radio_equal_per_day_months, plus the numeric labels /
bar heights / line positions of the Trends charts (spend, CPA, gross adds, CPC).

Definitions
  media          = Search + PMax + Meta + TikTok + ChatGPT daily cost (dr-data)
  fee (per day)  = seo_fee_monthly / days-in-month, plus commission_pct x media for days before commission_ends
  blended spend  = media + radio + fee
  blended CPA    = blended spend / SERVCO orders
  run-rate       = media, orders, sessions x days-in-month / days-elapsed; Radio and the SEO fee = full monthly amounts
"""
import calendar, datetime as dt, difflib, json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, 'index.html')
CONFIG = os.path.join(HERE, 'build_config.json')
MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
CH = ['search', 'pmax', 'meta', 'tiktok']


def money(x): return '$' + format(round(x), ',')
def num(x): return format(round(x), ',')
def dim(d): return calendar.monthrange(d.year, d.month)[1]
def mkey(d): return '%d-%02d' % (d.year, d.month)
def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


# ------------------------------------------------------------------ data
class Calc:
    def __init__(self, html):
        self.cfg = json.load(open(CONFIG))
        i = html.index("id='dr-data'")
        s = html.index('{', i)
        self.dr, self.dr_end = json.JSONDecoder().raw_decode(html, s)
        self.dr_start = s
        m = re.search(r"<script id='mom-data' type='application/json'>(.*?)</script>", html, re.S)
        self.mom = json.loads(m.group(1))
        self.D = self.dr['dates']
        self.idx = {d: i for i, d in enumerate(self.D)}
        self.R = dt.date.fromisoformat(self.D[-1])
        self.ensure_radio()

    def ensure_radio(self):
        """Radio is spent equally per day for the configured months (budget / days-in-month)."""
        for i, ds in enumerate(self.D):
            d = dt.date.fromisoformat(ds)
            k = mkey(d)
            if k in self.cfg['radio_equal_per_day_months'] and k in self.cfg['radio_budget']:
                self.dr['radio_cost'][i] = round(self.cfg['radio_budget'][k] / dim(d), 4)

    def media_day(self, i):
        return sum(self.dr['channels'][c]['cost'][i] for c in CH) + self.dr['chatgpt_cost'][i]

    def fee_day(self, i):
        d = dt.date.fromisoformat(self.D[i])
        f = self.cfg['seo_fee_monthly'] / dim(d)
        if d < dt.date.fromisoformat(self.cfg['commission_ends']):
            f += self.cfg['commission_pct'] * self.media_day(i)
        return f

    def W(self, a, b):
        ix = [self.idx[d.isoformat()] for d in daterange(a, b) if d.isoformat() in self.idx]
        media = sum(self.media_day(i) for i in ix)
        radio = sum(self.dr['radio_cost'][i] for i in ix)
        fee = sum(self.fee_day(i) for i in ix)
        orders = sum(self.dr['orders'][i] for i in ix)
        sess = sum(self.dr['sessions'][i] for i in ix)
        spend = media + radio + fee
        return dict(n=len(ix), media=media, radio=radio, fee=fee, spend=spend, orders=orders, sessions=sess,
                    cpa=(spend / orders if orders else None))

    def radio_month_total(self, d):
        k = mkey(d)
        if k in self.cfg['radio_budget']:
            return self.cfg['radio_budget'][k]
        first = d.replace(day=1)
        w = self.W(first, self.R)
        return w['radio'] * dim(d) / max(w['n'], 1)

    def prev_month_cpa(self, d):
        pm = (d.replace(day=1) - dt.timedelta(days=1))
        k = mkey(pm)
        if k in self.mom and self.mom[k].get('cpa'):
            return self.mom[k]['cpa'], pm
        w = self.W(pm.replace(day=1), pm)
        return w['cpa'], pm


def hero(c):
    R = c.R
    first = R.replace(day=1)
    el, dm = R.day, dim(R)
    m = c.W(first, R)
    pm_last = first - dt.timedelta(days=1)
    pm_first = pm_last.replace(day=1)
    lm = c.W(pm_first, pm_first.replace(day=min(el, pm_last.day)))
    lw = c.W(R - dt.timedelta(days=6), R)
    pw = c.W(R - dt.timedelta(days=13), R - dt.timedelta(days=7))
    f = dm / el
    radio_rr = c.radio_month_total(R)
    seo = c.cfg['seo_fee_monthly']
    rr_spend = m['media'] * f + radio_rr + seo
    rr_orders = m['orders'] * f
    rr_sess = m['sessions'] * f
    target_cpa, tmonth = (c.cfg['cpa_target_override'], None) if c.cfg['cpa_target_override'] else c.prev_month_cpa(R)
    return dict(R=R, el=el, dm=dm, m=m, lm=lm, lw=lw, pw=pw, rr_spend=rr_spend, rr_orders=rr_orders, rr_sess=rr_sess,
                rr_cpa=rr_spend / rr_orders, radio_rr=radio_rr, seo=seo, target_cpa=target_cpa, pm_last=pm_last,
                plan=c.cfg['spend_plan'], ga_target=c.cfg['gross_adds_target'], ses_target=c.cfg['sessions_target'],
                label='%s 1-%d' % (MON[R.month - 1], R.day))


# ------------------------------------------------------------------ small renderers
def delta(cur, prev, lower_better):
    if cur is None or prev in (None, 0):
        return "<span class='flat'>&mdash;</span>"
    pc = (cur / prev - 1) * 100
    if abs(pc) < 0.05:
        return "<span class='flat'>0.0%</span>"
    good = pc < 0 if lower_better else pc > 0
    return "<span class='%s'>%s %.1f%%</span>" % ('up' if good else 'down', '&#9650;' if pc > 0 else '&#9660;', abs(pc))


def r_big(c, H):
    return "<div class='big dp' style='color:var(--teal)'>%s<small>SERVCO gross adds, %s</small></div>" % (num(H['m']['orders']), H['label'])


def r_lead(c, H):
    m = H['m']
    pm = H['pm_last']
    under = (1 - H['rr_spend'] / H['plan']) * 100
    side = 'under' if under >= 0 else 'over'
    month = MONTH_NAMES[H['R'].month - 1]
    return ("<b>Run-rate spend is %s, %.1f%% %s the %s plan.</b> Blended CPA is $%.2f against $%.2f for %s %d at the same elapsed days &mdash; "
            "spend across all paid channels (Search, PMax, Meta, TikTok, ChatGPT Ads) plus Radio and the agency's flat $5,000/month SEO fee "
            "(the 15%% paid-media commission no longer applies). Radio's %s %s budget and the SEO fee are each assumed to be spent equally per day "
            "(%s/day and %s/day; %s and %s for %s), so run-rate spend counts the full %s and %s."
            % (money(H['rr_spend']), abs(under), side, money(H['plan']), m['cpa'], H['lm']['cpa'], MONTH_NAMES[pm.month - 1], pm.year,
               money(H['radio_rr']), month, money(H['radio_rr'] / H['dm']), money(H['seo'] / H['dm']),
               money(m['radio']), money(m['fee']), H['label'], money(H['radio_rr']), money(H['seo'])))


def r_kpis(c, H):
    m, lm, lw, pw = H['m'], H['lm'], H['lw'], H['pw']
    def kpi(label, pill, val, dlw, dlm):
        return ("<div class='kpi'><p class='lbl'>%s<span class='pill'>%s</span></p><p class='val n'>%s</p>"
                "<p class='dl'>%s <span class='pv'>vs LW</span></p><p class='dl'>%s <span class='pv'>vs LM</span></p></div>" % (label, pill, val, dlw, dlm))
    return ("<div class='kpis'>"
            + kpi('Blended CPA', 'Target &le; $%.2f' % H['target_cpa'], '$%.2f' % m['cpa'], delta(lw['cpa'], pw['cpa'], True), delta(m['cpa'], lm['cpa'], True))
            + kpi('SERVCO Gross Adds', 'Target %s' % num(H['ga_target']), num(m['orders']), delta(lw['orders'], pw['orders'], False), delta(m['orders'], lm['orders'], False))
            + kpi('Blended Spend (incl. Radio &amp; SEO fee)', 'Plan %s' % money(H['plan']), money(m['spend']), delta(lw['spend'], pw['spend'], True), delta(m['spend'], lm['spend'], True))
            + kpi('GA4 Sessions', 'Target %s' % num(H['ses_target']), num(m['sessions']), delta(lw['sessions'], pw['sessions'], False), delta(m['sessions'], lm['sessions'], False))
            + "</div>")


def r_bars(c, H):
    def row(name, rr, target, lower_better, fig):
        p = rr / target
        fill = round(min(p, 1.0) * 93)
        mark = 93.0 if p <= 1 else 93.0 / p
        good = p <= 1 if lower_better else p >= 1
        col = 'var(--teal)' if good else 'var(--coral)'
        return ("<div class='bar-row'><span class='nm'>%s</span><span class='track'><span class='fill' style='width:%d%%;background:var(--teal)'></span>"
                "<span class='target-mark' style='left:%.1f%%'></span></span><span class='fig n'><b>%s</b> / %s target <span style='color:%s'>(%d%%)</span></span></div>"
                % (name, fill, mark, fig(rr), fig(target), col, round(p * 100)))
    return (row('Blended CPA', H['rr_cpa'], H['target_cpa'], True, lambda v: '$%.2f' % v if v == H['target_cpa'] else '$%d' % round(v))
            + row('SERVCO Gross Adds', H['rr_orders'], H['ga_target'], False, num)
            + row('Blended Spend', H['rr_spend'], H['plan'], True, money)
            + row('GA4 Sessions', H['rr_sess'], H['ses_target'], False, num))


def r_rsc(c, H):
    m = H['m']
    month = MONTH_NAMES[H['R'].month - 1]
    return ("<div class='card'><h3>Radio (NZME) &amp; SEO fee &mdash; %s</h3><p class='sub'>Both assumed spent equally per day &mdash; Radio (%s/day) has no digital tracking, "
            "and the agency's SEO management fee is a flat $5,000 a month (%s/day). Estimates, not live pacing feeds.</p>"
            "<div class='rr'><span class='k'>Radio %s budget</span><span class='v n'>%s</span></div>"
            "<div class='rr'><span class='k'>SEO management fee</span><span class='v n'>%s / month</span></div>"
            "<div class='rr'><span class='k'>Elapsed</span><span class='v n'>%d of %d days</span></div>"
            "<div class='rr hl'><span class='k'>Radio spend to date</span><span class='v n'>%s</span></div>"
            "<div class='rr hl'><span class='k'>SEO fee to date</span><span class='v n'>%s</span></div></div>"
            % (month, money(H['radio_rr'] / H['dm']), money(H['seo'] / H['dm']), month, money(H['radio_rr']), money(H['seo']), H['el'], H['dm'], money(m['radio']), money(m['fee'])))


# ------------------------------------------------------------------ SVG helpers
def axis_groups(cap, step, fmt):
    n = int(round(cap / step))
    ys = [232.0 - 210.0 * k / n for k in range(n + 1)]
    grid = "<g stroke='var(--c-grid)' stroke-width='1'>" + ''.join("<line x1='58' y1='%.1f' x2='622' y2='%.1f'/>" % (y, y) for y in ys) + "</g>"
    labs = ("<g fill='var(--c-axis)' font-size='10.5' font-family=\"Space Grotesk, monospace\" text-anchor='end'>"
            + ''.join("<text x='52' y='%.1f' text-anchor='end'>%s</text>" % (y + 4, fmt(step * k)) for k, y in enumerate(ys)) + "</g>")
    return grid + labs


MONO = "font-family=\"Space Grotesk, monospace\""
RR_STYLE = " fill-opacity='.55' stroke='%s' stroke-width='1.5' stroke-dasharray='4 3'"


def bar_el(x0, w, xc, val, scale, fill, label, rr=False):
    hgt = val * scale
    y = 232 - hgt
    extra = RR_STYLE % fill if rr else ''
    if hgt >= 30:
        t = "<text x='%.1f' y='%.1f' fill='#fff' font-size='10.5' %s text-anchor='middle'>%s</text>" % (xc, y + 16, MONO, label)
    else:
        t = "<text x='%.1f' y='%.1f' fill='var(--c-label)' font-size='10.5' %s text-anchor='middle'>%s</text>" % (xc, y - 8, MONO, label)
    return "<rect x='%.1f' y='%.1f' width='%s' height='%.1f' fill='%s'%s/>%s" % (x0, y, w, hgt, fill, extra, t)


def pt_el(xc, cy, color, label, hollow=False):
    circ = ("<circle cx='%.1f' cy='%.1f' r='4' fill='var(--panel)' stroke='%s' stroke-width='2'/>" % (xc, cy, color)) if hollow \
        else ("<circle cx='%.1f' cy='%.1f' r='4' fill='%s'/>" % (xc, cy, color))
    return circ + "<text x='%.1f' y='%.1f' fill='%s' font-size='10' font-weight='700' %s text-anchor='middle'>%s</text>" % (xc, cy - 9, color, MONO, label)


def line_el(color, pts):
    return "<polyline fill='none' stroke='%s' stroke-width='2.5' stroke-linejoin='round' stroke-linecap='round' points='%s'/>" % (
        color, ' '.join('%.1f,%.1f' % p for p in pts))


def xl_el(xc, label):
    return "<text x='%.1f' y='248' fill='var(--c-axis)' font-size='10.5' font-family=\"Inter, sans-serif\" text-anchor='middle'>%s</text>" % (xc, label)


def week_list(R):
    mon = R - dt.timedelta(days=R.weekday())
    ws = []
    for k in range(6):
        a = mon - dt.timedelta(days=7 * (5 - k))
        ws.append((a, a + dt.timedelta(days=6)))
    return ws, (R - mon).days + 1


def week_label(a, b):
    if a.month == b.month:
        return '%s %d-%d' % (MON[a.month - 1], a.day, b.day)
    return '%s %d-%s %d' % (MON[a.month - 1], a.day, MON[b.month - 1], b.day)


def r_trend(c, H):
    R = c.R
    ws, el = week_list(R)
    partial = el < 7
    labels = [week_label(a, b) for a, b in ws]
    if partial:
        labels[5] += ' (RR)'
    data = []
    for k, (a, b) in enumerate(ws):
        w = c.W(a, b)
        f = 7 / el if (k == 5 and partial) else 1.0
        data.append(dict(spend=w['spend'] * f, orders=w['orders'] * f, sess=w['sessions'] * f, cpa=w['cpa'], rr=(k == 5 and partial)))
    cap = max(30000, math.ceil(max(d['spend'] for d in data) * 1.05 / 10000) * 10000)
    ocap = next(x for x in (900, 1200, 1500, 1800, 2400, 3000) if x >= max(d['orders'] for d in data) * 1.02)
    scap = max(54280.0, math.ceil(max(d['sess'] for d in data) * 1.05 / 5000) * 5000)
    sc_sp, sc_or, k_ses = 210.0 / cap, 210.0 / ocap, 210.0 / scap
    # chart 1: spend bars + CPA line
    svg1 = "<svg class='chart' viewBox='0 0 640 258' role='img'>" + axis_groups(cap, 10000, lambda v: '$' + format(int(v), ','))
    pts = []
    for k, d in enumerate(data):
        svg1 += bar_el(75.9 + 94 * k, '58.3', 105.0 + 94 * k, d['spend'], sc_sp, '#3730e6', money(d['spend']), d['rr'])
    cp = []
    for k, d in enumerate(data):
        cp.append((105.0 + 94 * k, 232 - 1.252 * d['cpa']))
    svg1 += line_el('#c23f95', cp)
    for k, d in enumerate(data):
        svg1 += pt_el(105.0 + 94 * k, cp[k][1], '#c23f95', '$%d' % round(d['cpa']), d['rr'])
    svg1 += ''.join(xl_el(105.0 + 94 * k, labels[k]) for k in range(6)) + "</svg>"
    # chart 2: orders bars + sessions line
    svg2 = "<svg class='chart' viewBox='0 0 640 258' role='img'>" + axis_groups(ocap, ocap / 3, lambda v: format(int(round(v)), ','))
    for k, d in enumerate(data):
        svg2 += bar_el(75.9 + 94 * k, '58.3', 105.0 + 94 * k, d['orders'], sc_or, '#14848c', num(d['orders']), d['rr'])
    sp = [(105.0 + 94 * k, 232 - k_ses * d['sess']) for k, d in enumerate(data)]
    svg2 += line_el('#b8790f', sp)
    for k, d in enumerate(data):
        svg2 += pt_el(105.0 + 94 * k, sp[k][1], '#b8790f', num(d['sess']), d['rr'])
    svg2 += ''.join(xl_el(105.0 + 94 * k, labels[k]) for k in range(6)) + "</svg>"
    note = ("<p class='ss'>Blended across all channels, Mon&ndash;Sun calendar weeks%s. Blended CPA uses the same all-paid-channel numerator as the hero card "
            "(Search, PMax, Meta, TikTok, ChatGPT Ads + Radio + the agency SEO fee).</p>" % (
                ("; the current week (%s) is a run-rate from %d of 7 days &mdash; spend, orders and traffic &times; 7 &divide; %d, drawn lighter with a dashed outline, "
                 "and its CPA is the week-to-date figure" % (labels[5].replace(' (RR)', ''), el, el)) if partial else ''))
    return (note + "\n<div class='trend-grid'>\n<div class='card'><h3>Spend &amp; CPA</h3>" + svg1
            + "<div class='legend'><span><span class='dot' style='background:#3730e6'></span>Spend ($)</span><span><span class='dot' style='background:#c23f95'></span>Blended CPA</span></div></div>\n"
            "<div class='card'><h3>Orders &amp; Traffic</h3>" + svg2
            + "<div class='legend'><span><span class='dot' style='background:#14848c'></span>SERVCO Orders</span><span><span class='dot' style='background:#b8790f'></span>GA4 Sessions</span></div></div>\n</div>")


# ------------------------------------------------------------------ budget table
BUDGET_ROWS = [  # key, label, dot, tag class, tag text
    ('search', 'Google Search', 'var(--c-steel)', 'conversion', 'Conversion'),
    ('pmax', 'Google PMax', 'var(--c-steel-l)', 'conversion', 'Conversion'),
    ('meta', 'Meta', 'var(--c-violet)', 'conversion', 'Conversion'),
    ('tiktok', 'TikTok', 'var(--c-teal)', 'conversion', 'Conversion'),
    ('chatgpt', 'ChatGPT Ads', 'var(--c-coral)', 'conversion', 'Conversion'),
    ('twitch', 'Twitch', 'var(--c-slate)', 'awareness', 'Awareness'),
    ('demand_gen', 'Demand Gen', 'var(--c-slate)', 'awareness', 'Awareness'),
    ('radio', 'Radio (NZME)', 'var(--c-amber)', 'awareness', 'Awareness'),
    ('seo', 'SEO management (agency)', 'var(--c-slate)', 'na', 'Fixed fee'),
]


def r_budget(c, H):
    R = c.R
    k = mkey(R)
    bud = dict(c.cfg['budgets'][k])
    bud['radio'] = c.cfg['radio_budget'].get(k, 0)
    bud['seo'] = c.cfg['seo_fee_monthly']
    first = R.replace(day=1)
    ix = [c.idx[d.isoformat()] for d in daterange(first, R)]
    mtd = {ch: sum(c.dr['channels'][ch]['cost'][i] for i in ix) for ch in CH}
    mtd['chatgpt'] = sum(c.dr['chatgpt_cost'][i] for i in ix)
    mtd['twitch'] = c.cfg['manual_mtd'].get('twitch', 0)
    mtd['demand_gen'] = c.cfg['manual_mtd'].get('demand_gen', 0)
    mtd['radio'] = sum(c.dr['radio_cost'][i] for i in ix)
    mtd['seo'] = sum(c.cfg['seo_fee_monthly'] / dim(R) for _ in ix)
    elapsed = R.day / dim(R) * 100
    out = ("<table><tr><th>Channel</th><th class='r'>Budget</th><th class='r'>MTD spend</th><th class='r'>% spent</th><th class='r'>% month elapsed</th>"
           "<th class='r'>Pace</th><th class='r'>Remaining</th></tr>")
    tb = tm = 0.0
    for key, label, dot, tcls, ttxt in BUDGET_ROWS:
        b, s_ = bud.get(key, 0), mtd.get(key, 0)
        tb += b
        tm += s_
        head = "<tr><td><span class='dot' style='background:%s'></span>%s<span class='tag %s'>%s</span></td>" % (dot, label, tcls, ttxt)
        if b == 0 and s_ == 0:
            out += head + ("<td class='r'>$0</td><td class='r'>$0</td><td class='r'>&mdash;</td><td class='r'>%.1f%%</td><td class='r'><span class='flat'>Paused</span></td><td class='r'>&mdash;</td></tr>" % elapsed)
            continue
        pct = s_ / b * 100 if b else 0
        pace = pct - elapsed
        pc = 'up' if abs(pace) <= 2 else 'down'
        ptxt = '0.0%' if abs(pace) < 0.05 else (('+%.1f%%' % pace) if pace > 0 else ('&minus;%.1f%%' % abs(pace)))
        out += head + ("<td class='r'>%s</td><td class='r'>%s</td><td class='r'>%.1f%%<span class='mini-bar'><i style='width:%d%%'></i></span></td><td class='r'>%.1f%%</td>"
                       "<td class='r'><span class='%s'>%s</span></td><td class='r'>%s</td></tr>" % (money(b), money(s_), pct, min(100, round(pct)), elapsed, pc, ptxt, money(b - s_)))
    out += ("<tr class='tot'><td>Total</td><td class='r'>%s</td><td class='r'>%s</td><td class='r'>%.1f%%</td><td class='r'>%.1f%%</td><td class='r'></td><td class='r'>%s</td></tr></table>"
            % (money(tb), money(tm), tm / tb * 100, elapsed, money(tb - tm)))
    return out


# ------------------------------------------------------------------ Trends patches
def sub1(pattern, repl, text, what):
    new, n = re.subn(pattern, repl, text, count=1, flags=re.S)
    if n != 1:
        raise SystemExit('build.py: could not find %s in the Trends chart' % what)
    return new


def parse_week_label(label, R):
    mt = re.match(r'^([A-Z][a-z]{2}) (\d+)-(?:([A-Z][a-z]{2}) )?(\d+)(?: \((MTD|RR)\))?$', label.strip())
    if not mt:
        raise SystemExit('build.py: cannot parse Trends week label %r' % label)
    m1 = MON.index(mt.group(1)) + 1
    m2 = MON.index(mt.group(3)) + 1 if mt.group(3) else m1
    y1 = R.year if m1 <= R.month else R.year - 1
    y2 = y1 + (1 if m2 < m1 else 0)
    return dt.date(y1, m1, int(mt.group(2))), dt.date(y2, m2, int(mt.group(4))), mt.group(5)


def col_labels(svg):
    return {m.group(1): m.group(2) for m in re.finditer(r"<text x='([\d.]+)' y='248' fill='var\(--c-axis\)'[^>]*>([^<]*)</text>", svg)}


def text_at(svg, xc, fill, size=None):
    pat = r"<text x='%s' y='[\d.]+' fill='%s'%s[^>]*>([^<]*)</text>" % (re.escape(xc), re.escape(fill), (" font-size='%s'" % size) if size else '')
    m = re.search(pat, svg)
    return m.group(1) if m else None


def set_bar(svg, x0, xc, val, scale, label):
    pat = r"<rect x='%s' y='[\d.]+' width='87.4' height='[\d.]+' fill='#3730e6'/><text x='%s' y='[\d.]+'([^>]*)>[^<]*</text>" % (re.escape(x0), re.escape(xc))
    return sub1(pat, lambda m: "<rect x='%s' y='%.1f' width='87.4' height='%.1f' fill='#3730e6'/><text x='%s' y='%.1f'%s>%s</text>" % (
        x0, 232 - val * scale, val * scale, xc, 232 - val * scale + 16, m.group(1), label), svg, 'bar at x=' + x0)


def set_gap(svg, xc, adds, cpa, k):
    cy = 232 - k * adds
    svg = sub1(r"<circle cx='%s' cy='[\d.]+' r='4' fill='#c23f95'/><text x='%s' y='[\d.]+'([^>]*)>[^<]*</text><text x='%s' y='[\d.]+'([^>]*)>[^<]*</text>" % ((re.escape(xc),) * 3),
               lambda m: "<circle cx='%s' cy='%.1f' r='4' fill='#c23f95'/><text x='%s' y='%.1f'%s>%s</text><text x='%s' y='%.1f'%s>$%.2f</text>" % (
                   xc, cy, xc, cy - 9, m.group(1), num(adds), xc, cy - 23, m.group(2), cpa), svg, 'gross adds/CPA point at x=' + xc)
    return svg, cy


def set_poly_y(svg, color, ys):
    ms = re.findall(r"<polyline[^>]*stroke='%s'[^>]*points='([^']*)'" % re.escape(color), svg)
    if len(ms) != 1:
        raise SystemExit('build.py: expected one %s polyline' % color)
    pts = [p.split(',') for p in ms[0].split(' ')]
    for p in pts:
        if p[0] in ys:
            p[1] = '%.1f' % ys[p[0]]
    return svg.replace("points='%s'" % ms[0], "points='%s'" % ' '.join(','.join(p) for p in pts))


def set_cpc(svg, xc, cy, val):
    return sub1(r"<circle cx='%s' cy='[\d.]+' r='4' fill='#b8790f'/><text x='%s' y='[\d.]+'([^>]*)>\$[\d.]+</text>" % (re.escape(xc), re.escape(xc)),
                lambda m: "<circle cx='%s' cy='%.1f' r='4' fill='#b8790f'/><text x='%s' y='%.1f'%s>$%.2f</text>" % (xc, cy, xc, cy + 18, m.group(1), val), svg, 'CPC point at x=' + xc)


def swap_axis(svg, cap, step, fmt):
    svg = sub1(r"<g stroke='var\(--c-grid\)' stroke-width='1'>.*?</g><g fill='var\(--c-axis\)'[^>]*text-anchor='end'>.*?</g>",
               lambda m: axis_groups(cap, step, fmt), svg, 'axis')
    return svg


def patch_trends(html, c, H):
    t0 = html.index('id="view-trends"')
    t1 = html.index('id="view-history"', t0)
    seg = html[t0:t1]
    svgs = re.findall(r"<svg.*?</svg>", seg, flags=re.S)
    if len(svgs) != 6:
        raise SystemExit('build.py: expected 6 Trends charts, found %d' % len(svgs))
    S = list(svgs)
    # ----- monthly (0 spend/GA/CPA, 2 spend/impr/clicks, 4 CTR/CPC)
    mlabels = col_labels(S[0])
    cols = {}
    for xc, lab in mlabels.items():
        mt = re.match(r'^([A-Z][a-z]{2}) (\d{4})(?: \((RR)\))?$', lab)
        if not mt:
            raise SystemExit('build.py: cannot parse Trends month label %r' % lab)
        y, mo = int(mt.group(2)), MON.index(mt.group(1)) + 1
        first = dt.date(y, mo, 1)
        key = mkey(first)
        if mt.group(3):  # run-rate month
            f = dim(first) / c.R.day
            w = c.W(first, c.R)
            spend = w['media'] * f + c.radio_month_total(first) + c.cfg['seo_fee_monthly']
            orders = w['orders'] * f
            cpa = spend / orders
        elif key in c.mom:
            spend, orders, cpa = c.mom[key]['spend'], c.mom[key]['gas'], c.mom[key]['cpa']
        else:
            w = c.W(first, first.replace(day=dim(first)))
            spend, orders, cpa = w['spend'], w['orders'], w['cpa']
        cols[xc] = dict(spend=spend, orders=orders, cpa=cpa)
    mcap = 150000 if max(v['spend'] for v in cols.values()) * 1.02 <= 150000 else 200000
    for k in (0, 2):
        S[k] = swap_axis(S[k], mcap, 50000, lambda v: '$' + format(int(v), ','))
    for xc, v in cols.items():
        x0 = '%.1f' % (float(xc) - 43.7)
        for k in (0, 2):
            S[k] = set_bar(S[k], x0, xc, v['spend'], 210.0 / mcap, money(v['spend']))
        S[0], _ = set_gap(S[0], xc, v['orders'], v['cpa'], 0.037162)
    clicks2 = {xc: text_at(S[2], xc, '#b8790f', '10') for xc in cols}
    ycpc = {}
    for xc, v in cols.items():
        cl = float((clicks2[xc] or '0').replace(',', '') or 0)
        if cl <= 0:
            continue
        cpc = v['spend'] / cl
        cy = 231.989 - 21.9327 * cpc
        ycpc[xc] = cy
        S[4] = set_cpc(S[4], xc, cy, cpc)
    S[4] = set_poly_y(S[4], '#b8790f', ycpc)
    # ----- weekly (1 spend/GA/CPA, 3 spend/impr/clicks, 5 CTR/CPC)
    wlabels = col_labels(S[1])
    wcols = {}
    for xc, lab in wlabels.items():
        a, b, flag = parse_week_label(lab, c.R)
        w = c.W(a, min(b, c.R))
        wcols[xc] = dict(spend=w['spend'], orders=w['orders'], cpa=w['cpa'])
    wcap = max(30000, math.ceil(max(v['spend'] for v in wcols.values()) * 1.02 / 10000) * 10000)
    for k in (1, 3):
        S[k] = swap_axis(S[k], wcap, 10000, lambda v: '$' + format(int(v), ','))
    ys1 = {}
    for xc, v in wcols.items():
        x0 = '%.1f' % (float(xc) - 43.7)
        for k in (1, 3):
            S[k] = set_bar(S[k], x0, xc, v['spend'], 210.0 / wcap, money(v['spend']))
        S[1], cy = set_gap(S[1], xc, v['orders'], v['cpa'], 0.1498)
        ys1[xc] = cy
    S[1] = set_poly_y(S[1], '#c23f95', ys1)
    clicks3 = {xc: text_at(S[3], xc, '#b8790f', '10') for xc in wcols}
    ycpc = {}
    for xc, v in wcols.items():
        cl = float((clicks3[xc] or '0').replace(',', '') or 0)
        if cl <= 0:
            continue
        cpc = v['spend'] / cl
        cy = 231.09 - 32.33 * cpc
        ycpc[xc] = cy
        S[5] = set_cpc(S[5], xc, cy, cpc)
    S[5] = set_poly_y(S[5], '#b8790f', ycpc)
    for o, n in zip(svgs, S):
        assert seg.count(o) == 1
        seg = seg.replace(o, n)
    return html[:t0] + seg + html[t1:]


# ------------------------------------------------------------------ markers + assembly
REGIONS = ['big', 'lead', 'kpis', 'bars', 'rsc', 'trend', 'budget']


def region_bounds(html):
    """Locate each owned region in an un-marked page (used by --init)."""
    hs = html.index("<div class='hero'>")
    out = {}
    m = re.compile(r"<div class='big dp'[^>]*>.*?</div>", re.S).search(html, hs); out['big'] = (m.start(), m.end())
    m = re.compile(r"<b>Run-rate spend is.*?and \$[\d,]+\.", re.S).search(html, hs); out['lead'] = (m.start(), m.end())
    ks = html.index("<div class='kpis'>", hs); ke = html.index("<div class='split'", ks); out['kpis'] = (ks, ke)
    bs = html.index("<div class='bar-row'>", ke); be = html.index("</div><div class='card'><h3>Radio (NZME)", bs) + 6
    out['bars'] = (bs, be)
    rs = html.index("<div class='card'><h3>Radio (NZME)", be)
    re_ = html.index("SEO fee to date", rs)
    re_ = html.index("</div></div>", re_) + len("</div></div>")
    out['rsc'] = (rs, re_ - 6)   # card incl. its own closing </div>, excl. the split's </div>
    ts = html.index("<p class='ss'>Blended across all channels", re_)
    te = html.index('\n</div>\n\n<div class="view" id="view-channels">', ts)
    out['trend'] = (ts, te)
    vb = html.index('id="view-budget"')
    tb = html.index('<table>', vb); te2 = html.index('</table>', tb) + len('</table>')
    out['budget'] = (tb, te2)
    return out


def init_markers(html):
    if '<!--c:big-->' in html:
        return html
    b = region_bounds(html)
    for name in sorted(b, key=lambda n: -b[n][0]):
        s, e = b[name]
        html = html[:s] + '<!--c:%s-->' % name + html[s:e] + '<!--/c:%s-->' % name + html[e:]
    return html


def replace_region(html, name, new):
    pat = re.compile(r"<!--c:%s-->.*?<!--/c:%s-->" % (name, name), re.S)
    if not pat.search(html):
        raise SystemExit('build.py: region marker <!--c:%s--> is missing (run: python3 build.py --init)' % name)
    return pat.sub(lambda m: '<!--c:%s-->%s<!--/c:%s-->' % (name, new, name), html, count=1)


def build(html):
    c = Calc(html)
    H = hero(c)
    out = html
    # dr-data (radio_cost kept in sync with the config rule)
    out = out[:c.dr_start] + json.dumps(c.dr, separators=(',', ':')) + out[c.dr_end:]
    gens = dict(big=r_big, lead=r_lead, kpis=r_kpis, bars=r_bars, rsc=r_rsc, trend=r_trend, budget=r_budget)
    for name in REGIONS:
        out = replace_region(out, name, gens[name](c, H))
    out = patch_trends(out, c, H)
    return out, c, H


def text_of(s):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s)).strip()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    html = open(INDEX, encoding='utf-8').read()
    if mode == '--init':
        new = init_markers(html)
        open(INDEX, 'w', encoding='utf-8').write(new)
        print('markers present')
        return
    html = init_markers(html) if '<!--c:big-->' not in html else html
    new, c, H = build(html)
    if mode == '--print':
        m = H['m']
        print('through %s | MTD spend %s  orders %s  CPA $%.2f | LM CPA $%.2f | RR spend %s  RR CPA $%.2f | CPA target $%.2f' % (
            c.R, money(m['spend']), num(m['orders']), m['cpa'], H['lm']['cpa'], money(H['rr_spend']), H['rr_cpa'], H['target_cpa']))
        return
    if mode == '--check':
        if new == html:
            print('OK: every calculated number on the page matches build.py (data through %s).' % c.R)
            return
        a = text_of(html).split(' ')
        b = text_of(new).split(' ')
        diffs = [(tag, ' '.join(a[i1:i2]), ' '.join(b[j1:j2])) for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if tag != 'equal']
        print('MISMATCH: %d text differences between the page and build.py:' % len(diffs))
        for tag, old, newt in diffs[:40]:
            print('  page: %-40s  build.py: %s' % (old[:40], newt[:60]))
        if not diffs:
            print('  (only markup / chart-geometry differences)')
        sys.exit(1)
    open(INDEX, 'w', encoding='utf-8').write(new)
    m = H['m']
    print('built: through %s | MTD spend %s orders %s CPA $%.2f | RR spend %s RR CPA $%.2f | target $%.2f' % (
        c.R, money(m['spend']), num(m['orders']), m['cpa'], money(H['rr_spend']), H['rr_cpa'], H['target_cpa']))


if __name__ == '__main__':
    main()

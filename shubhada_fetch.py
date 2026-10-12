"""Fetch attendance punches and Performance Charts from shubhadahealth.com
for a month, instead of exporting and uploading them by hand (owner,
2026-10-12). Logs in with the login kept in the vault (vault.py).

READ-ONLY by design. The site's pages carry editing controls right next to
the data - a "Change" link on every punch, "Add Target", Leave Request Entry,
Manage Employee Shift - and this module never clicks any of them. It only
fills the report forms (dates, employee) and presses the two report buttons,
"Show" and "Get Performance", which display data and change nothing.

How the site behaves, found by looking at it live on 2026-10-12:
  - An Angular app on https://shubhadahealth.com:7007 whose data comes over a
    socket.io connection, so the reports are read from the page's table, not
    from a web API.
  - Pages can't be opened by address ("Cannot GET /empattendance"); inside a
    logged-in app, history.pushState + popstate switches pages.
  - The date boxes READ typed dates month-first (09/01/2026 = 1 September)
    but SHOW them day-first (01-09-2026). Typing 01-09-2026 asks for
    9 January - which the site then reports on.
  - Attendance Report (/empattendance): one "Show" returns every employee for
    every day in the range - all of September 2026 was 1,222 rows, 33
    people, in 4 seconds. Two rows per person per day (two shifts).
  - Emp Performance Chart (/emppermnchart): one employee at a time; pick the
    name from the box's list, "Get Performance", 22 rows in about 2 s.

It drives its OWN Chrome profile (.shubhadahealth_profile), so it never
collides with the WhatsApp sender or the order automation project.
"""

import calendar
import json
import time
import datetime as dt

BASE = 'https://shubhadahealth.com:7007'
PROFILE_DIR_NAME = '.shubhadahealth_profile'

# The JS run in the page. Each is a function taking one argument object.
_JS_SETV = """
const setv = (el, v) => { el.focus(); el.value = v; el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true})); el.dispatchEvent(new Event('blur', {bubbles: true})); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
"""

_JS_GO = """async (path) => {
  history.pushState({}, '', path); dispatchEvent(new PopStateEvent('popstate', {state: {}}));
  await new Promise(r => setTimeout(r, 2500));
  return location.pathname;
}"""

_JS_ATTENDANCE = "async (a) => {" + _JS_SETV + """
  const f = document.querySelector('[placeholder="Select From Date"]'), t = document.querySelector('[placeholder="Select To Date"]');
  if (!f || !t) return {error: 'the attendance form is not on the page'};
  setv(f, a.from_us); setv(t, a.to_us);
  await sleep(500);
  const show = [...document.querySelectorAll('button')].find(b => b.innerText.trim() === 'Show');
  if (!show) return {error: 'no Show button'};
  show.click();
  const t0 = Date.now(); let n = -1, stable = 0;
  while (Date.now() - t0 < 60000) {
    await sleep(1500);
    const tb = document.querySelector('table'); const c = tb ? tb.querySelectorAll('tr').length : 0;
    if (c === n && c > 1) { if (++stable >= 2) break; } else stable = 0;
    n = c;
  }
  const tb = document.querySelector('table');
  const head = tb ? [...tb.querySelectorAll('th')].map(h => h.innerText.trim()) : [];
  const rows = tb ? [...tb.querySelectorAll('tr')].slice(1).map(r => [...r.cells].map(c => c.innerText.replace(/\\s*Change\\s*/g, '').replace(/\\s+/g, ' ').trim())) : [];
  return {from: f.value, to: t.value, head, rows};
}"""

_JS_EMPLOYEES = "async () => {" + _JS_SETV + """
  const inputs = [...document.querySelectorAll('input')]; const emp = inputs[inputs.length - 1];
  // the list only opens on a real focus + click, not on an empty input event (checked live)
  emp.blur(); await sleep(300);
  emp.value = ''; emp.dispatchEvent(new Event('input', {bubbles: true}));
  emp.dispatchEvent(new FocusEvent('focusin', {bubbles: true})); emp.focus(); emp.click();
  await sleep(1500);
  const names = [...document.querySelectorAll('mat-option')].map(o => o.innerText.trim()).filter(Boolean);
  document.body.click(); emp.blur();
  return names;
}"""

_JS_CHART = "async (a) => {" + _JS_SETV + """
  const f = document.querySelector('[placeholder="Select From Date"]'), t = document.querySelector('[placeholder="Select To Date"]');
  if (!f || !t) return {error: 'the performance form is not on the page'};
  setv(f, a.from_us); setv(t, a.to_us);
  const inputs = [...document.querySelectorAll('input')]; const emp = inputs[inputs.length - 1];
  emp.focus(); emp.value = a.name; emp.dispatchEvent(new Event('input', {bubbles: true}));
  await sleep(1200);
  const pick = [...document.querySelectorAll('mat-option')].find(o => o.innerText.trim() === a.name);
  if (!pick) return {error: 'name not in the list'};
  pick.click();
  await sleep(800);
  const snap = () => { const tb = document.querySelector('table'); return tb ? [...tb.querySelectorAll('tr')].slice(1).map(r => [...r.cells].map(c => c.innerText.trim())) : []; };
  const before = JSON.stringify(snap());
  const btn = [...document.querySelectorAll('button')].find(b => b.innerText.trim() === 'Get Performance');
  if (!btn) return {error: 'no Get Performance button'};
  btn.click();
  // a new table - or, after a while, the same one (two people can have identical charts)
  let rows = []; const t0 = Date.now();
  while (Date.now() - t0 < 20000) {
    await sleep(1200); rows = snap();
    if (rows.length && JSON.stringify(rows) !== before) break;
    if (rows.length && Date.now() - t0 > 7000) break;
  }
  return {from: f.value, to: t.value, emp: emp.value, rows};
}"""


def month_range(month):
    """('09/01/2026', '09/30/2026') month-first for typing, plus ISO dates.
    The current month stops at today."""
    y, m = int(month[:4]), int(month[5:7])
    last = calendar.monthrange(y, m)[1]
    end = dt.date(y, m, last)
    today = dt.date.today()
    if end > today:
        end = today
    start = dt.date(y, m, 1)
    return start.strftime('%m/%d/%Y'), end.strftime('%m/%d/%Y'), start, end


def _num(s):
    s = str(s or '').strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _hhmm(s):
    """'01/09/2026 : 09:18' -> ('2026-09-01', '09:18')."""
    try:
        d, t = [x.strip() for x in str(s).split(' : ', 1)]
        day = dt.datetime.strptime(d, '%d/%m/%Y').date().isoformat()
        return day, t[:5]
    except Exception:
        return None, None


def parse_attendance(head, rows):
    """Table rows -> [{site_name, date, branch, in, out, flag}]."""
    idx = {h: i for i, h in enumerate(head)}
    need = ['Employee', 'Attendance Date', 'In Date Time', 'Out Date Time']
    if not all(h in idx for h in need):
        raise ValueError(f'The attendance table looks different now: {head}')
    out = []
    for r in rows:
        if len(r) < len(head):
            continue
        day, tin = _hhmm(r[idx['In Date Time']])
        _, tout = _hhmm(r[idx['Out Date Time']])
        if not day:
            try:
                day = dt.datetime.strptime(r[idx['Attendance Date']], '%d/%m/%Y').date().isoformat()
            except ValueError:
                continue
        out.append({'site_name': r[idx['Employee']], 'date': day, 'branch': r[idx.get('Branch', 0)] if 'Branch' in idx else '',
                    'in': tin or '', 'out': tout or '', 'flag': r[idx['Access Flag']] if 'Access Flag' in idx else ''})
    return out


def parse_chart(rows):
    """Performance Chart rows -> ({category: achieved}, {category: target})."""
    achieved, target = {}, {}
    for r in rows:
        if len(r) < 4 or not r[1]:
            continue
        achieved[r[1]] = _num(r[3])
        target[r[1]] = _num(r[2])
    return achieved, target


def fetch(month, creds, profile_dir, log, want_attendance=True, want_charts=True, stop=lambda: False):
    """Logs in (from the vault) if needed and returns
    {'attendance': [...], 'charts': {site_name: {'rows', 'targets'}}, 'employees': [...]}.
    `log(msg)` reports progress; `stop()` lets the caller cut a long run short."""
    from playwright.sync_api import sync_playwright
    from_us, to_us, start, end = month_range(month)
    result = {'attendance': None, 'charts': {}, 'employees': [], 'chart_errors': {},
              'range': [start.isoformat(), end.isoformat()]}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir=str(profile_dir), channel='chrome',
                                                   headless=False, args=['--start-maximized'], no_viewport=True)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            log('Opening shubhadahealth.com')
            page.goto(BASE + '/', wait_until='domcontentloaded', timeout=60000)
            page.wait_for_timeout(4000)
            if '/login' in page.url or page.locator('input[name="pass"]').count():
                if not creds:
                    raise RuntimeError('Not logged in, and no shubhadahealth login is saved in the vault (Salary > Edit).')
                log('Logging in with the login from the vault')
                page.fill('input[name="user"]', creds['username'])
                page.fill('input[name="pass"]', creds['password'])
                page.click('button[type="submit"]')
                deadline = time.time() + 30
                while time.time() < deadline and ('/login' in page.url or page.locator('input[name="pass"]').count()):
                    page.wait_for_timeout(1000)
                if '/login' in page.url or page.locator('input[name="pass"]').count():
                    raise RuntimeError('The site did not accept the login - check the username and password in the vault.')
                page.wait_for_timeout(3000)
            log('Logged in')

            if want_attendance and not stop():
                log(f'Attendance: {start:%d %b} to {end:%d %b %Y}')
                page.evaluate(_JS_GO, '/empattendance')
                page.wait_for_timeout(1500)
                res = page.evaluate(_JS_ATTENDANCE, {'from_us': from_us, 'to_us': to_us})
                if res.get('error'):
                    raise RuntimeError(f"Attendance report: {res['error']}")
                rows = parse_attendance(res['head'], res['rows'])
                days = sorted({r['date'] for r in rows})
                if rows and (days[0] < start.isoformat() or days[-1] > end.isoformat()):
                    raise RuntimeError(f'The site answered for {days[0]} to {days[-1]}, not the month asked for - nothing saved.')
                result['attendance'] = rows
                log(f"Attendance: {len(rows)} punches, {len({r['site_name'] for r in rows})} people, {len(days)} days")

            if want_charts and not stop():
                page.evaluate(_JS_GO, '/emppermnchart')
                page.wait_for_timeout(1500)
                names = page.evaluate(_JS_EMPLOYEES)
                result['employees'] = names
                log(f'Performance Charts: {len(names)} people to read')
                for i, name in enumerate(names, 1):
                    if stop():
                        log('Stopped before finishing the charts.')
                        break
                    res = page.evaluate(_JS_CHART, {'from_us': from_us, 'to_us': to_us, 'name': name})
                    if res.get('error'):
                        result['chart_errors'][name] = res['error']
                        log(f'{i}/{len(names)} {name}: {res["error"]}')
                        continue
                    achieved, target = parse_chart(res['rows'])
                    result['charts'][name] = {'rows': achieved, 'targets': target}
                    if i % 6 == 0 or i == len(names):
                        log(f'Performance Charts: {i} of {len(names)} read')
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    return result

"""CRM reader: read-only reports from shubhadahealth.com for the Ask page and WhatsApp.

The shop's CRM / ERP (shubhadahealth.com) has reports the reckoner does not hold itself: who is owed what,
when a supplier was last paid, the monthly GST register. This module opens that site in its OWN Chrome
profile (.crm_profile - a separate logged-in session, never shared with the salary fetcher), signs in from
the vault when it has to (vault.py - the login the owner saved in Salary > Edit; the password is read by this
program only, never printed, logged or sent to the AI), reads one report, and closes the browser again.

READ-ONLY, by construction: every click goes through _click(), which accepts only the labels in ALLOWED_CLICKS
(menus, the report's Generate / Excel buttons, View Ledger, Next page) and refuses anything that looks like a
change (Set Already Paid, voucher, create, alter, delete, save, submit ...). The pages are reached by clicking
through the menus the way a person does (the site refuses direct addresses).

Reports (tools for Ask - see tool_specs):
  crm_gst_report(month)          Procurement > Consolidated GST Report for one month: the figures, and the Excel
                                 file the site produces, which is handed to the requester's WhatsApp chat
  crm_outstanding_payable()      Accounts > Statement Of Accounts > Out Standing - Payable: what is owed, party-wise
  crm_party_last_paid(party)     that party's ledger: the last payments and what has been bought since

One browser job runs at a time. Each takes 20-90 seconds, so results are kept for a few minutes.
Switched off with CRM_ENABLED=0; CRM_HEADLESS=0 shows the browser window (the default is hidden).
"""
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import vault

logger = logging.getLogger('reckoner_server')

ROOT = Path(__file__).resolve().parent
BASE = 'https://shubhadahealth.com:7007'
PLATFORM = 'shubhadahealth'
PROFILE_DIR = ROOT / '.crm_profile'
DOWNLOAD_DIR = ROOT / 'data' / 'crm_downloads'
CACHE_TTL_S = 600                 # a repeated question inside ten minutes is answered from memory
GST_TTL_S = 900
KEEP_DOWNLOADS_DAYS = 30
LOCK_WAIT_S = 240                 # a second request waits for the first browser job instead of failing
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December']
SECTIONS = {'PURCHASE DETAILS': 'purchases', 'SALES DETAILS': 'sales', 'CREDIT NOTE DETAILS': 'credit_notes',
            'WHOLE SALES DETAILS (REGISTERED VENDOR)': 'wholesale_registered',
            'WHOLE SALES DETAILS (NON-REGISTERED VENDOR)': 'wholesale_non_registered'}


class CrmError(Exception):
    """The message is safe to show the owner."""


# ---------------------------------------------------------------------------
# Read-only guard
# ---------------------------------------------------------------------------
ALLOWED_CLICKS = {'menu', 'Dashboard', 'Reports', 'Procurement', 'Consolidated GST Report', 'Generate', 'Excel',
                  'Accounts', 'Statement Of Accounts', 'Out Standing - Payable', 'View Ledger', 'Next page',
                  'Open calendar', 'Login'}
_CHANGES = re.compile(r'paid|delet|remov|alter|creat|voucher|save|submit|approv|reject|cancel|confirm|update|modif|'
                      r'chang|post|add|edit|clear|reset|pay\b', re.I)


def check_click(label):
    """The label of something about to be clicked must be on the allow-list and must not look like a change."""
    if label not in ALLOWED_CLICKS or _CHANGES.search(label):
        raise CrmError('Refused to click "%s": the CRM reader only opens report pages.' % label)
    return label


def _click(page, label, **kw):
    check_click(label)
    page.get_by_text(label, exact=True).first.click(timeout=kw.get('timeout', 20000))


# ---------------------------------------------------------------------------
# Parsing what the pages show (pure functions - tested without a browser)
# ---------------------------------------------------------------------------
def _num(s):
    try:
        return float(str(s).replace(',', '').strip())
    except ValueError:
        return None


def parse_gst_text(text):
    """The Consolidated GST Report page text -> {'net_gst_payable', 'sections': {name: totals}, 'net_by_rate'}.
    Totals rows read: Total, TRADEVAL, DISCOUNT, ASSCVAL, TAXAMT, IGST, CGST, SGST."""
    out = {'net_gst_payable': None, 'sections': {}, 'net_by_rate': []}
    section = None
    for raw in str(text or '').replace('\r', '').split('\n'):
        line = raw.strip().replace('\xa0', ' ')
        if not line:
            continue
        head = re.sub(r'\s+', ' ', line.split('\t')[0]).strip().upper()
        m = re.match(r'NET GST PAYABLE DETAILS.*?Rs:\s*(-?[\d,\.]+)', head, re.I)
        if m:
            out['net_gst_payable'] = _num(m.group(1))
            section = 'net'
            continue
        key = next((v for k, v in SECTIONS.items() if head.startswith(k)), None)
        if key:
            section = key
            continue
        cells = [c.strip() for c in raw.split('\t')]
        if section == 'net':
            if len(cells) >= 5 and cells[1].upper().startswith('GST@'):
                out['net_by_rate'].append({'rate': cells[1], 'igst': _num(cells[2]), 'cgst': _num(cells[3]), 'sgst': _num(cells[4])})
            continue
        if section and len(cells) >= 9 and cells[1].lower() == 'total':
            v = [_num(c) for c in cells[2:9]]
            if None not in v:
                out['sections'][section] = {'trade_value': v[0], 'discount': v[1], 'taxable_value': v[2], 'tax_amount': v[3],
                                            'igst': v[4], 'cgst': v[5], 'sgst': v[6]}
    return out                          # the page's own "Rs:" figure is the net payable - it is never worked out here


def parse_payables(text):
    """The Out Standing - Payable page text -> {'total', 'parties': [{'name', 'amount'}]} (phone numbers dropped)."""
    out = {'total': None, 'parties': []}
    m = re.search(r'Total\s*=\s*([\d,\.]+)', str(text or ''))
    if m:
        out['total'] = _num(m.group(1))
    for line in str(text or '').replace('\r', '').split('\n'):
        parts = [p.strip() for p in line.split('\t') if p.strip()]
        if len(parts) >= 3 and re.fullmatch(r'\d+', parts[0]):
            amount = _num(parts[2])
            if amount is not None:
                name = re.sub(r'\s*\([\d,\s]*\)\s*$', '', parts[1]).strip()
                out['parties'].append({'name': name, 'amount': amount})
    return out


def match_parties(parties, wanted):
    """Parties whose name holds every word of `wanted` (case-insensitively; a word may be the start of a name word)."""
    words = [w for w in re.split(r'\W+', str(wanted or '').casefold()) if w]
    if not words:
        return []
    hits = []
    for p in parties:
        toks = [t for t in re.split(r'\W+', p['name'].casefold()) if t]
        if all(any(t == w or t.startswith(w) for t in toks) for w in words):
            hits.append(p)
    exact = [p for p in hits if p['name'].casefold() == str(wanted).strip().casefold()]
    return exact or hits


def parse_ledger(rows, today=None):
    """Ledger lines [date, particulars, voucher no, voucher type, debit, credit, balance, bill details] ->
    what the owner wants to know: closing balance, the last payments, and what was bought since the last one."""
    today = today or date.today()
    lines = []
    for r in rows:
        try:
            d = datetime.strptime(r[0], '%d/%m/%Y').date()
        except (ValueError, IndexError):
            continue
        if len(r) < 7:
            continue
        lines.append({'date': d, 'part': r[1], 'vno': r[2], 'type': r[3], 'debit': _num(r[4]) or 0.0,
                      'credit': _num(r[5]) or 0.0, 'balance': r[6]})
    if not lines:
        return {'lines': 0}
    pay = [l for l in lines if l['type'].lower().startswith('payment')]
    out = {'lines': len(lines), 'from': lines[0]['date'].isoformat(), 'to': lines[-1]['date'].isoformat(),
           'closing_balance': lines[-1]['balance'], 'payments_this_year': len(pay),
           'paid_this_year': round(sum(l['debit'] for l in pay), 2),
           'recent_payments': [{'date': l['date'].isoformat(), 'amount': round(l['debit'], 2), 'via': l['part'], 'voucher': l['vno'],
                                'balance_after': l['balance']} for l in pay[-8:]]}
    if pay:
        last = pay[-1]['date']
        after = [l for l in lines if l['date'] > last and l['type'].lower() == 'purchase']
        out['last_payment_date'] = last.isoformat()
        out['days_since_last_payment'] = (today - last).days
        out['bought_after_last_payment_day'] = {'bills': len(after), 'amount': round(sum(l['credit'] for l in after), 2)}
        same_day = [l for l in lines if l['date'] == last and l['type'].lower() == 'purchase']
        if same_day:        # bills dated the payment day itself: they may be booked before or after the payment
            out['bought_on_last_payment_day'] = {'bills': len(same_day), 'amount': round(sum(l['credit'] for l in same_day), 2)}
    return out


def month_arg(month, today=None):
    """'2026-10' -> (2026, 10). Refuses anything else, and a month that has not started."""
    today = today or date.today()
    m = re.fullmatch(r'\s*(\d{4})-(\d{1,2})\s*', str(month or ''))
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise CrmError('Say the month as YYYY-MM, for example 2026-10.')
    y, mo = int(m.group(1)), int(m.group(2))
    if (y, mo) > (today.year, today.month):
        raise CrmError('%s %d has not started yet - there is no GST report for it.' % (MONTHS[mo - 1], y))
    if y < 2016:
        raise CrmError('That month is too far back - the CRM calendar starts in 2016.')
    return y, mo


# ---------------------------------------------------------------------------
# The browser
# ---------------------------------------------------------------------------
_job_lock = threading.Lock()
_cache = {}
_cache_lock = threading.Lock()


def _enabled_env():
    return os.environ.get('CRM_ENABLED', '1').strip().lower() not in ('0', 'false', 'no', 'off')


def _headless():
    return os.environ.get('CRM_HEADLESS', '1').strip().lower() not in ('0', 'false', 'no', 'off')


def configured():
    """True when the vault holds the shubhadahealth login (checked without printing it)."""
    try:
        return bool(vault.summary().get(PLATFORM, {}).get('configured'))
    except Exception:
        return False


def _cached(key, ttl):
    with _cache_lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def _remember(key, value):
    with _cache_lock:
        _cache[key] = (time.time(), value)


def _is_login_page(page):
    try:
        return '/login' in page.url or page.locator('input[name="pass"]').count() > 0
    except Exception:
        return False


def _ensure_login(page, creds):
    page.goto(BASE + '/', wait_until='domcontentloaded', timeout=60000)
    page.wait_for_timeout(4000)
    if not _is_login_page(page):
        return
    logger.info('[crm] signing in with the saved login')
    page.wait_for_selector('input[name="user"]', timeout=20000)
    page.fill('input[name="user"]', creds['username'])
    page.fill('input[name="pass"]', creds['password'])
    page.keyboard.press('Tab')
    # The Login button has no type attribute in the page's HTML, so it is found by its text; it stays disabled
    # until both boxes are filled and click() waits for that.
    page.locator('form button', has_text='Login').first.click(timeout=20000)
    deadline = time.time() + 30
    while time.time() < deadline and _is_login_page(page):
        page.wait_for_timeout(1000)
    if _is_login_page(page):
        raise CrmError('shubhadahealth did not accept the saved login. Please check it in the vault (Salary > Edit).')
    page.wait_for_timeout(3000)


def _run_job(fn, *args):
    """Runs fn(page, *args) in a fresh browser signed in to the CRM; one job at a time."""
    if not configured():
        raise CrmError('No shubhadahealth login is saved in the vault yet (Salary > Edit > Vault).')
    if not _job_lock.acquire(timeout=LOCK_WAIT_S):
        raise CrmError('Another CRM report is still being fetched - please try again in a minute.')
    try:
        creds = vault.get_credentials(PLATFORM)
        if not creds:
            raise CrmError('No shubhadahealth login is saved in the vault yet (Salary > Edit > Vault).')
        from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR), channel='chrome', headless=_headless(),
                viewport={'width': 1600, 'height': 900}, accept_downloads=True)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.set_default_timeout(30000)
                try:
                    _ensure_login(page, creds)
                    return fn(page, *args)
                except PWTimeout as exc:
                    logger.warning('[crm] timed out: %s', str(exc).splitlines()[0][:120])
                    raise CrmError('shubhadahealth did not answer in time - please try again in a minute.')
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    finally:
        _job_lock.release()


def _wait_report_done(page, limit=240, quiet_looks=3):
    """The GST report fills section by section, each showing "Please wait fetching data..." until it is in.
    Done = no such marker for several looks in a row."""
    t0, clear = time.time(), 0
    while time.time() - t0 < limit:
        page.wait_for_timeout(2000)
        clear = clear + 1 if 'please wait' not in (page.inner_text('body') or '').lower() else 0
        if clear >= quiet_looks:
            return time.time() - t0
    raise CrmError('The report was still loading after %d seconds - please try again.' % limit)


def _pick_month(page, year, month):
    """The month box is read-only: its calendar opens on a list of years, then months."""
    check_click('Open calendar')
    page.get_by_role('button', name='Open calendar').first.click(timeout=15000)
    page.wait_for_timeout(800)
    cal = page.locator('mat-calendar, .mat-calendar').first
    target = '%s %d' % (MONTHS[month - 1], year)
    cell = cal.get_by_role('button', name=target, exact=True)
    if not cell.count():
        cal.get_by_role('button', name=str(year), exact=True).first.click(timeout=10000)
        page.wait_for_timeout(600)
        cell = cal.get_by_role('button', name=target, exact=True)
    cell.first.click(timeout=10000)
    page.wait_for_timeout(600)
    shown = page.locator('input').first.input_value()
    if shown != '%02d/%d' % (month, year):
        raise CrmError('Could not set the month to %s (the page shows %s).' % (target, shown))


def _clean_downloads():
    try:
        cutoff = time.time() - KEEP_DOWNLOADS_DAYS * 86400
        for f in DOWNLOAD_DIR.glob('*'):
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# The reports
# ---------------------------------------------------------------------------
def _gst_job(page, year, month):
    _click(page, 'Reports')
    page.wait_for_selector('text=Procurement', timeout=20000)
    _click(page, 'Procurement')
    page.wait_for_selector('text=Consolidated GST Report', timeout=20000)
    _click(page, 'Consolidated GST Report')
    page.wait_for_selector('text=Generate', timeout=20000)
    _pick_month(page, year, month)
    _click(page, 'Generate')
    waited = _wait_report_done(page)
    parsed = parse_gst_text(page.inner_text('body') or '')
    if not parsed['sections'] or parsed['net_gst_payable'] is None:
        raise CrmError('The GST report page did not show its figures - please try again.')
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _clean_downloads()
    with page.expect_download(timeout=90000) as info:
        _click(page, 'Excel')
    download = info.value
    name = re.sub(r'[\\/:*?"<>|]+', '_', download.suggested_filename or 'GST Report %04d-%02d.xlsx' % (year, month))
    path = DOWNLOAD_DIR / name
    download.save_as(str(path))
    parsed.update({'month': '%04d-%02d' % (year, month), 'file': str(path), 'file_name': path.name,
                   'file_size_kb': round(path.stat().st_size / 1024), 'seconds': round(waited)})
    return parsed


def _open_payables(page):
    _click(page, 'menu')
    page.wait_for_selector('text=Accounts', timeout=20000)
    _click(page, 'Accounts')
    page.wait_for_selector('text=Statement Of Accounts', timeout=20000)
    _click(page, 'Statement Of Accounts')
    page.wait_for_selector('text=Out Standing - Payable', timeout=20000)
    _click(page, 'Out Standing - Payable')
    t0 = time.time()
    while time.time() - t0 < 120:
        page.wait_for_timeout(2000)
        parsed = parse_payables(page.inner_text('body') or '')
        if parsed['total'] is not None and parsed['parties']:
            return parsed
    raise CrmError('The payables list did not load - please try again.')


_LEDGER_ROWS_JS = r"""() => [...document.querySelectorAll('table tr, mat-row, [role="row"]')].map(tr =>
  [...tr.querySelectorAll('td, mat-cell, [role="cell"]')].map(c => (c.innerText || '').trim().replace(/\s+/g, ' ')))
  .filter(r => r.length >= 7 && /^\d{2}\/\d{2}\/\d{4}$/.test(r[0]))"""


def _payables_job(page):
    return _open_payables(page)


def _ledger_job(page, party):
    parsed = _open_payables(page)
    hits = match_parties(parsed['parties'], party)
    if len(hits) != 1:
        return {'matches': [h['name'] for h in hits[:8]], 'parties_listed': len(parsed['parties'])}
    name = hits[0]['name']
    row = page.locator('tr, mat-row, [role="row"]', has_text=re.compile(r'\b' + re.escape(name) + r'\s*\(')).first
    check_click('View Ledger')
    row.get_by_text('View Ledger').first.click(timeout=15000)
    page.wait_for_selector('text=Ledger Name', timeout=60000)
    rows, last_len = [], -1
    for _ in range(60):                                  # a ledger is paged 20 lines at a time
        page.wait_for_timeout(1000)
        rows += page.evaluate(_LEDGER_ROWS_JS)
        nxt = page.get_by_role('button', name='Next page')
        if nxt.count() == 0 or nxt.first.is_disabled():
            break
        check_click('Next page')
        nxt.first.click(timeout=10000)
    info = parse_ledger(rows)
    info.update({'party': name, 'listed_outstanding': hits[0]['amount']})
    return info


def gst_report(month):
    y, mo = month_arg(month)
    key = ('gst', y, mo)
    hit = _cached(key, GST_TTL_S)
    if hit and Path(hit['file']).exists():
        return dict(hit, from_memory=True)
    result = _run_job(_gst_job, y, mo)
    _remember(key, result)
    return result


def outstanding_payable():
    hit = _cached(('payables',), CACHE_TTL_S)
    if hit:
        return hit
    result = _run_job(_payables_job)
    _remember(('payables',), result)
    return result


def party_ledger(party):
    key = ('ledger', re.sub(r'\W+', ' ', str(party or '').casefold()).strip())
    hit = _cached(key, CACHE_TTL_S)
    if hit:
        return hit
    result = _run_job(_ledger_job, party)
    if 'party' in result:
        _remember(key, result)
    return result


# ---------------------------------------------------------------------------
# Delivery of a downloaded file (the WhatsApp side registers itself here)
# ---------------------------------------------------------------------------
_deliver = None


def set_deliver(fn):
    """fn(owner_key, path, caption) -> True when the file was queued for the requester's chat."""
    global _deliver
    _deliver = fn


# ---------------------------------------------------------------------------
# Tools for Ask
# ---------------------------------------------------------------------------
PREFIX = 'crm_'


def tool_crm_gst_report(month):
    r = gst_report(month)
    out = {'month': r['month'], 'net_gst_payable_rupees': r['net_gst_payable'], 'sections': r['sections'],
           'net_by_rate': r['net_by_rate'], 'file_name': r['file_name'], 'file_size_kb': r['file_size_kb'],
           'source': 'shubhadahealth.com > Reports > Procurement > Consolidated GST Report, as it stands now'}
    caption = 'GST report %s - net GST payable Rs %s' % (r['month'], format(r['net_gst_payable'], ',.2f'))
    queued = False
    try:
        import mrp_basket
        owner = mrp_basket.OWNER.get()
        queued = bool(_deliver and _deliver(owner, r['file'], caption))
    except Exception as exc:
        logger.warning('[crm] could not queue the file: %s: %s', type(exc).__name__, exc)
    out['file_delivery'] = ('the Excel file is being sent to the requester on WhatsApp right after this answer' if queued
                            else 'the Excel file is saved on the shop PC (data/crm_downloads) but could not be queued for WhatsApp')
    return out


def tool_crm_outstanding_payable(top=15):
    r = outstanding_payable()
    try:
        top = max(1, min(int(top), 80))
    except (TypeError, ValueError):
        top = 15
    ranked = sorted(r['parties'], key=lambda p: -p['amount'])
    return {'total_outstanding_rupees': r['total'], 'parties_owed': len(r['parties']), 'top': ranked[:top],
            'source': 'shubhadahealth.com > Accounts > Statement Of Accounts > Out Standing - Payable, as it stands now'}


def tool_crm_party_last_paid(party):
    r = party_ledger(party)
    if 'party' not in r:
        if not r.get('matches'):
            return {'found': False, 'note': 'no party in the payables list matches that name (only parties with an amount owed are listed)'}
        return {'found': False, 'several_parties_match': r['matches'], 'note': 'say which one is meant'}
    return dict(r, source='shubhadahealth.com ledger of that party for the current financial year (from 1 April), as it stands now')


_SPECS = [
    {'name': PREFIX + 'gst_report', 'fn': tool_crm_gst_report,
     'description': ('The monthly Consolidated GST Report from the shop CRM (shubhadahealth.com): net GST payable, and purchases, '
                     'sales, credit notes and wholesale tax totals for ONE month. It also downloads the Excel file the CRM '
                     'produces and sends it to the requester on WhatsApp. Use for "October GST report", "send the GST file", '
                     '"how much GST do we owe for September". Takes about 1-2 minutes. Month as YYYY-MM.'),
     'parameters': {'type': 'object', 'properties': {'month': {'type': 'string', 'description': 'YYYY-MM, e.g. 2026-10; work it out from today\'s date for "this month", "last month" or a bare month name'}},
                    'required': ['month']}},
    {'name': PREFIX + 'outstanding_payable', 'fn': tool_crm_outstanding_payable,
     'description': ('What the shop owes its suppliers right now, party by party (CRM > Accounts > Statement Of Accounts > '
                     'Out Standing - Payable): the total and the biggest parties. Use for "outstanding payable", "how much do we owe", '
                     '"who do we owe the most". Takes about a minute.'),
     'parameters': {'type': 'object', 'properties': {'top': {'type': 'integer', 'description': 'how many parties to list, biggest first (default 15)'}}, 'required': []}},
    {'name': PREFIX + 'party_last_paid', 'fn': tool_crm_party_last_paid,
     'description': ('One supplier\'s ledger from the CRM: when and how much the shop LAST PAID them, the recent payments, the closing '
                     'balance and what has been bought since the last payment. Use for "when did we last pay <supplier>", "last paid details". '
                     'Takes 1-2 minutes. Only suppliers with an amount owed can be found.'),
     'parameters': {'type': 'object', 'properties': {'party': {'type': 'string', 'description': 'the supplier\'s name or part of it'}}, 'required': ['party']}},
]
_BY_NAME = {s['name']: s for s in _SPECS}


def tool_specs(voice=False):
    """The CRM tools, offered only when they are switched on and the vault holds the login. Not in voice
    conversations: a report takes a minute or more."""
    if voice or not _enabled_env() or not configured():
        return []
    return [{'name': s['name'], 'description': s['description'], 'parameters': s['parameters']} for s in _SPECS]


def is_crm_tool(name):
    return name in _BY_NAME


def run(name, args):
    spec = _BY_NAME.get(name)
    if spec is None:
        return {'error': 'unknown tool %s' % name}
    try:
        return spec['fn'](**(args if isinstance(args, dict) else {}))
    except CrmError as exc:
        return {'error': str(exc)}
    except TypeError as exc:
        return {'error': 'bad arguments: %s' % exc}
    except Exception as exc:
        logger.error('[crm] %s failed: %s: %s', name, type(exc).__name__, exc)
        return {'error': 'the CRM report could not be fetched (%s)' % type(exc).__name__}

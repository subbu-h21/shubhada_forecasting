"""Offline tests for crm_reader.py (read-only CRM reports for the Ask page).

No browser, no website, no real login, no model call: the pages are made-up text shaped like the live
shubhadahealth pages (made-up suppliers and figures), the browser is a scripted fake, and the vault is a stub.

Run with the reckoner's venv:
    venv/Scripts/python.exe test_crm_reader.py
"""
import os
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('OPENROUTER_API_KEY', 'sk-test-not-real')
sys.stdout.reconfigure(encoding='utf-8')

import crm_reader as C

fails = []


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  ' + str(extra)))
    if not cond:
        fails.append(name)


# --- the read-only guard ---------------------------------------------------------------------------------
check('every label the reader clicks passes its own change filter', all(not C._CHANGES.search(l) for l in C.ALLOWED_CLICKS), [l for l in C.ALLOWED_CLICKS if C._CHANGES.search(l)])
check('an allowed label is accepted', C.check_click('Generate') == 'Generate' and C.check_click('View Ledger') == 'View Ledger')
for bad in ('Set Already Paid', 'Voucher Entry', 'Multiple Bank Vouchers', 'Create Ledgers', 'Display/Alter', 'Save', 'Delete', 'Submit',
            'Opening Balance', 'Approve', 'Pay Now', 'Export To Excel', 'Cancel Bill'):
    try:
        C.check_click(bad)
        refused = False
    except C.CrmError:
        refused = True
    check('refuses to click "%s"' % bad, refused)


class Clicker:
    def __init__(self):
        self.clicked = []

    def get_by_text(self, text, exact=False):
        outer = self

        class L:
            @property
            def first(self_):
                return self_

            def click(self_, timeout=0):
                outer.clicked.append(text)
        return L()


pg = Clicker()
C._click(pg, 'Reports')
try:
    C._click(pg, 'Set Already Paid')
    ok = False
except C.CrmError:
    ok = True
check('_click clicks an allowed label and never reaches the page for a refused one', pg.clicked == ['Reports'] and ok, pg.clicked)

# --- GST page text ------------------------------------------------------------------------------------------
HEAD = 'Sl.No\tTAXRATE\tTRADEVAL\tDISCOUNT\tASSCVAL\tTAXAMT\tIGST\tCGST\tSGST'


def block(title, total, extra=''):
    return (f'{title}      \r\n{extra}{HEAD}\r\n1\t5\t100.00\t0.00\t100.00\t5.00\t0.00\t2.50\t2.50\r\n'
            f'\tTotal\t{total}\r\n\r\n\r\nBranch\tStart Bill No\tEnd Bill No\r\nMain\t1\t9\r\n')


GST = ('menu\r\nDashboardReports PanelConsolidated GST Report\r\nGenerate\r\n'
       + block('PURCHASE DETAILS', '1000.00\t50.00\t950.00\t45.00\t1.00\t22.00\t22.00', 'Excel\r\n')
       + block('SALES DETAILS', '2000.00\t0.00\t2000.00\t100.00\t0.00\t50.00\t50.00')
       + block('CREDIT NOTE DETAILS', '100.00\t0.00\t100.00\t5.00\t0.00\t2.50\t2.50')
       + block('WHOLE SALES DETAILS (Registered Vendor)', '300.00\t0.00\t300.00\t15.00\t0.00\t7.50\t7.50')
       + block('WHOLE SALES DETAILS (Non-Registered Vendor)', '40.00\t0.00\t40.00\t2.00\t0.00\t1.00\t1.00')
       + 'NET GST PAYABLE DETAILS       Rs:-1234.50\r\nSl.No\tRATE\tIGST\tCGST\tSGST\r\n'
         '1\tGST@5%\t-1.00\t10.00\t10.00\r\n2\tGST@18%\t0.00\t3.25\t3.25\r\n\tTotal\t-1.00\t13.25\t13.25\r\n')
g = C.parse_gst_text(GST)
check('the page\'s own net GST payable figure is read (a credit shows negative)', g['net_gst_payable'] == -1234.5, g['net_gst_payable'])
check('all five sections are found', set(g['sections']) == {'purchases', 'sales', 'credit_notes', 'wholesale_registered', 'wholesale_non_registered'}, set(g['sections']))
check('a section\'s totals are read column by column', g['sections']['purchases'] == {'trade_value': 1000.0, 'discount': 50.0, 'taxable_value': 950.0, 'tax_amount': 45.0, 'igst': 1.0, 'cgst': 22.0, 'sgst': 22.0}, g['sections']['purchases'])
check('sales and credit-note totals are not mixed up', g['sections']['sales']['tax_amount'] == 100.0 and g['sections']['credit_notes']['tax_amount'] == 5.0)
check('the net-by-rate lines are kept', [r['rate'] for r in g['net_by_rate']] == ['GST@5%', 'GST@18%'] and g['net_by_rate'][0]['cgst'] == 10.0)
check('a page still loading (no figures) gives nothing to trust', C.parse_gst_text('Generate\r\nPlease wait fetching data...')['sections'] == {} and C.parse_gst_text('')['net_gst_payable'] is None)

# --- payables ---------------------------------------------------------------------------------------------------
PAY = ('menu\r\nOutStanding Payables\r\nSearch Name\r\nExport To Excel\r\nTotal= 1500\r\nSl.No\tEntity Name\tAmount\t\r\n'
       '1\tALPHA SUPPLIERS (9000000001,9000000002)\t900\tView Ledger\r\n2\tBETA MEDICAL AGENCIES (9000000003)\t400\tView Ledger\r\n'
       '3\tBETA PHARMA ()\t200\tView Ledger\r\n')
p = C.parse_payables(PAY)
check('payables: total and parties, with phone numbers dropped', p['total'] == 1500.0 and p['parties'] == [
    {'name': 'ALPHA SUPPLIERS', 'amount': 900.0}, {'name': 'BETA MEDICAL AGENCIES', 'amount': 400.0}, {'name': 'BETA PHARMA', 'amount': 200.0}], p)
check('payables: no phone digits survive', '9000000' not in str(p))
names = p['parties']
check('a party is found by a word of its name', [x['name'] for x in C.match_parties(names, 'alpha')] == ['ALPHA SUPPLIERS'])
check('two parties sharing a word are both returned', len(C.match_parties(names, 'beta')) == 2)
check('more words narrow it down', [x['name'] for x in C.match_parties(names, 'beta med')] == ['BETA MEDICAL AGENCIES'])
check('the exact name wins over a longer one', [x['name'] for x in C.match_parties(names + [{'name': 'BETA', 'amount': 1.0}], 'beta')] == ['BETA'])
check('a name that is nowhere gives nothing, and so does no name', C.match_parties(names, 'zzzz') == [] and C.match_parties(names, '  ') == [])

# --- a ledger -------------------------------------------------------------------------------------------------------------
LED = [['01/10/2026', 'PURCHASE ACCOUNT', '1', 'Purchase', '0', '1000', '1000 Cr', 'Invoice 1'],
       ['07/10/2026', 'SOME BANK', '2', 'Payment', '1000', '0', '0 Cr', ''],
       ['07/10/2026', 'PURCHASE ACCOUNT', '3', 'Purchase', '0', '300', '300 Cr', 'Invoice 2'],
       ['09/10/2026', 'PURCHASE ACCOUNT', '4', 'Purchase', '0', '200', '500 Cr', 'Invoice 3'],
       ['09/10/2026', 'SALES AC', '5', 'Sales', '40', '0', '460 Cr', 'back to supplier'],
       ['not a date', 'x', '6', 'Purchase', '0', '9', '1', ''], ['short row']]
L = C.parse_ledger(LED, today=date(2026, 10, 12))
check('ledger: lines, closing balance and the payments made', L['lines'] == 5 and L['closing_balance'] == '460 Cr' and L['payments_this_year'] == 1 and L['paid_this_year'] == 1000.0, L)
check('ledger: the last payment, how long ago, and via what', L['last_payment_date'] == '2026-10-07' and L['days_since_last_payment'] == 5 and L['recent_payments'][-1]['via'] == 'SOME BANK')
check('ledger: bought after the payment day, and on it, kept apart',
      L['bought_after_last_payment_day'] == {'bills': 1, 'amount': 200.0} and L['bought_on_last_payment_day'] == {'bills': 1, 'amount': 300.0}, L)
check('ledger: a return to the supplier is not counted as a payment', all(r['via'] != 'SALES AC' for r in L['recent_payments']))
check('ledger: no payments at all is stated, not invented', 'last_payment_date' not in C.parse_ledger([LED[0]], today=date(2026, 10, 12)) and C.parse_ledger([], today=date(2026, 10, 12)) == {'lines': 0})

# --- months ------------------------------------------------------------------------------------------------------------------
today = date(2026, 10, 30)
check('a month is read as YYYY-MM', C.month_arg('2026-10', today) == (2026, 10) and C.month_arg(' 2026-9 ', today) == (2026, 9))
for bad in ('October', '10/2026', '2026-13', '2026-00', '', None):
    try:
        C.month_arg(bad, today); ok = False
    except C.CrmError:
        ok = True
    check('a bad month "%s" is refused with a plain message' % bad, ok)
try:
    C.month_arg('2026-11', today); ok = False
except C.CrmError as e:
    ok = 'has not started' in str(e) and 'November 2026' in str(e)
check('next month is refused', ok)
try:
    C.month_arg('2010-01', today); ok = False
except C.CrmError:
    ok = True
check('a month before the calendar starts is refused', ok)


# --- waiting for the report ------------------------------------------------------------------------------------------------------
class WaitPage:
    def __init__(self, texts):
        self.texts = list(texts)
        self.waits = 0

    def inner_text(self, sel):
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]

    def wait_for_timeout(self, ms):
        self.waits += 1


wp = WaitPage(['x Please wait fetching data...', 'Please wait fetching data', 'done', 'please wait again', 'done', 'done', 'done'])
C._wait_report_done(wp, limit=60)
check('the report is done only after several quiet looks in a row (a late "please wait" restarts the count)', wp.waits == 7, wp.waits)
t0 = time.time()
try:
    C._wait_report_done(WaitPage(['Please wait fetching data...']), limit=0.01)
    ok = False
except C.CrmError as e:
    ok = 'still loading' in str(e)
check('a report that never finishes is reported, not waited on forever', ok and time.time() - t0 < 5)


# --- the payables route: asking again when the list stays empty ----------------------------------------------------------
EMPTY_PAGE = ' | '.join(['menu', 'OutStanding Payables', 'Search Name', 'Total= 0', 'Sl.No   Entity Name   Amount'])


class PayPage:
    """A page whose payables list is empty until the route has been walked `empty_walks` times."""

    def __init__(self, empty_walks):
        self.empty_walks = empty_walks
        self.clicks = []

    def wait_for_selector(self, sel, timeout=0):
        pass

    def wait_for_timeout(self, ms):
        pass

    def get_by_text(self, text, exact=False):
        outer = self

        class L:
            @property
            def first(s):
                return s

            def click(s, timeout=0):
                outer.clicks.append(text)
        return L()

    def inner_text(self, sel):
        walks = self.clicks.count('Out Standing - Payable')
        return EMPTY_PAGE if walks <= self.empty_walks else PAY


orig_wait, orig_tries, orig_settle = C.LIST_WAIT_S, C.LIST_TRIES, C.SETTLE_S
C.LIST_WAIT_S, C.LIST_TRIES, C.SETTLE_S = 0.05, 2, 0
pp = PayPage(empty_walks=0)
got = C._open_payables(pp)
check('a list that arrives at once is read after one walk of the menus',
      got['total'] == 1500.0 and pp.clicks == ['menu', 'Accounts', 'Statement Of Accounts', 'Out Standing - Payable'], pp.clicks)
pp = PayPage(empty_walks=1)
got = C._open_payables(pp)
check('a list that stays empty is asked for again: back to the dashboard, then the same route',
      got['total'] == 1500.0 and pp.clicks == ['menu', 'Accounts', 'Statement Of Accounts', 'Out Standing - Payable', 'Dashboard',
                                              'menu', 'Accounts', 'Statement Of Accounts', 'Out Standing - Payable'], pp.clicks)
pp = PayPage(empty_walks=99)
try:
    C._open_payables(pp); ok = False
except C.CrmError as e:
    ok = 'did not load' in str(e)
check('a list that never arrives gives up after the set number of tries with a plain message',
      ok and pp.clicks.count('Out Standing - Payable') == 2 and pp.clicks.count('Dashboard') == 1, pp.clicks)
PARTIAL = '\r\n'.join(l for l in PAY.split('\r\n') if not l.startswith('3\t'))      # two of the three rows have arrived


class FillingPage(PayPage):
    """The first `partial_looks` looks show a half-filled table, then the full one."""

    def __init__(self, partial_looks, final=None):
        super().__init__(empty_walks=0)
        self.looks, self.partial_looks, self.final = 0, partial_looks, final or PAY

    def inner_text(self, sel):
        self.looks += 1
        return PARTIAL if self.looks <= self.partial_looks else self.final


C.LIST_WAIT_S = 5
fp = FillingPage(partial_looks=3)
got = C._open_payables(fp)
check('a table that is still filling is not accepted: the rows must add up to the page\'s own total',
      len(got['parties']) == 3 and got['complete'] is True and C._rows_add_up(got), (len(got['parties']), got.get('complete')))
check('the half-filled table itself does not add up (so the check can tell)', not C._rows_add_up(C.parse_payables(PARTIAL)) and C._rows_add_up(C.parse_payables(PAY)))
fp = FillingPage(partial_looks=10 ** 9)
t0 = time.time()
C.LIST_WAIT_S = 30
got = C._open_payables(fp)
check('rows that never add up but have stopped changing are returned after three identical looks, marked incomplete',
      got['complete'] is False and len(got['parties']) == 2 and fp.looks <= 6 and time.time() - t0 < 5, (got.get('complete'), fp.looks))
import vault
os.environ.pop('CRM_ENABLED', None)
vault_summary_backup = vault.summary
vault.summary = lambda: {'shubhadahealth': {'label': 'x', 'configured': True, 'username_masked': 'ab***cd'}}
C._cache.clear()
orig_job = C._run_job
C._run_job = lambda fn, *a: dict(C.parse_payables(PARTIAL), complete=False)
warned = C.run('crm_outstanding_payable', {})
C._run_job = orig_job
vault.summary = vault_summary_backup
check('an incomplete list is flagged to the model so it says so', 'warning' in warned and 'incomplete' in warned['warning'], warned)
C._cache.clear()
C.LIST_WAIT_S, C.LIST_TRIES, C.SETTLE_S = orig_wait, orig_tries, orig_settle
check('the hidden browser is given Indian region settings, not its US default (the shop pages are dd/mm/yyyy)',
      "locale='en-IN'" in open(C.__file__, encoding='utf-8').read() and "timezone_id='Asia/Kolkata'" in open(C.__file__, encoding='utf-8').read())

# --- choosing the month in the calendar -------------------------------------------------------------------------------------------------
class Cal:
    """Just enough of the page: Open calendar, a year list, then the month cells; the box shows the pick."""

    def __init__(self, opens_on_months=False, breaks=False):
        self.box = '10/2026'
        self.in_months = opens_on_months
        self.log = []
        self.breaks = breaks
        outer = self

        class Btn:
            def __init__(self, label):
                self.label = label

            def count(self):
                if self.label == 'Open calendar':
                    return 1
                if outer.in_months and self.label[:1].isalpha():
                    return 1
                return 1 if self.label.isdigit() else 0

            @property
            def first(self):
                return self

            def click(self, timeout=0):
                outer.log.append(self.label)
                if self.label.isdigit():
                    outer.in_months = True
                elif self.label[:1].isalpha() and self.label != 'Open calendar':
                    mon, yr = self.label.split()
                    outer.box = ('%02d/%s' % (C.MONTHS.index(mon) + 1, yr)) if not outer.breaks else outer.box

        class Scope:
            def get_by_role(self, role, name='', exact=False):
                return Btn(name)

            @property
            def first(self):
                return self

        class Input:
            def input_value(self_):
                return outer.box

            @property
            def first(self_):
                return self_

        self.scope, self.input = Scope(), Input()

    def get_by_role(self, role, name='', exact=False):
        return self.scope.get_by_role(role, name=name)

    def locator(self, sel):
        return self.scope if 'calendar' in sel else self.input

    def wait_for_timeout(self, ms):
        pass


cp = Cal()
C._pick_month(cp, 2026, 9)
check('the calendar is opened, the year picked, then the month, and the box is checked', cp.log == ['Open calendar', '2026', 'September 2026'] and cp.box == '09/2026', cp.log)
try:
    C._pick_month(Cal(breaks=True), 2026, 9); ok = False
except C.CrmError as e:
    ok = 'Could not set the month' in str(e)
check('a month that did not take is reported, never a wrong report', ok)

# --- tools, with the browser stubbed -------------------------------------------------------------------------------------------------------
tmp = Path(tempfile.mkdtemp())
f = tmp / 'GST Report From 1-10-2026 To 31-10-2026.xlsx'
f.write_bytes(b'xlsx-bytes')
jobs = []


def fake_job(fn, *args):
    jobs.append((fn.__name__, args))
    if fn is C._gst_job:
        return dict(g, month='%04d-%02d' % args, file=str(f), file_name=f.name, file_size_kb=1, seconds=40)
    if fn is C._payables_job:
        return dict(p)
    if fn is C._ledger_job:
        hits = C.match_parties(p['parties'], args[0])
        return dict(L, party=hits[0]['name'], listed_outstanding=hits[0]['amount']) if len(hits) == 1 else {'matches': [h['name'] for h in hits], 'parties_listed': 3}
    raise AssertionError(fn)


orig_run_job, C._run_job = C._run_job, fake_job
vault_state = {'configured': True}
import vault
orig_summary = vault.summary
vault.summary = lambda: {'shubhadahealth': {'label': 'x', 'configured': vault_state['configured'], 'username_masked': 'ab***cd'}}
os.environ.pop('CRM_ENABLED', None)
delivered = []
C.set_deliver(lambda owner, path, caption: delivered.append((owner, path, caption)) or True)

import mrp_basket
tok = mrp_basket.OWNER.set('whatsapp-owner-2')
r = C.run('crm_gst_report', {'month': '2026-10'})
mrp_basket.OWNER.reset(tok)
check('the GST tool returns the figures, never the file path', r['net_gst_payable_rupees'] == -1234.5 and r['month'] == '2026-10' and 'file' not in r and str(tmp) not in str(r), r)
check('the Excel file is queued for the requester\'s own chat, with a caption carrying the figure',
      delivered and delivered[0][0] == 'whatsapp-owner-2' and delivered[0][1] == str(f) and '2026-10' in delivered[0][2] and '-1,234.50' in delivered[0][2], delivered)
check('the answer says the file is being sent only because it was queued', 'being sent' in r['file_delivery'])
before = len(jobs)
C.run('crm_gst_report', {'month': '2026-10'})
check('asking again for the same month within minutes is answered from memory, no second browser job', len(jobs) == before)
r2 = C.run('crm_gst_report', {'month': '2026-09'})
check('another month is a new job', len(jobs) == before + 1 and r2['month'] == '2026-09')
C.set_deliver(lambda owner, path, caption: False)
C._cache.clear()
r3 = C.run('crm_gst_report', {'month': '2026-10'})
check('if the file cannot be queued the answer says it is only saved, not sent', 'could not be queued' in r3['file_delivery'] and 'being sent' not in r3['file_delivery'])
C.set_deliver(None)
check('with no WhatsApp side registered the same', 'could not be queued' in C.run('crm_gst_report', {'month': '2026-10'})['file_delivery'])
check('a bad or future month comes back as a plain error for the model', 'YYYY-MM' in C.run('crm_gst_report', {'month': 'October'})['error']
      and 'has not started' in C.run('crm_gst_report', {'month': '2999-01'})['error'])

o = C.run('crm_outstanding_payable', {'top': 2})
check('payables: the total, the number of parties and the biggest first', o['total_outstanding_rupees'] == 1500.0 and o['parties_owed'] == 3 and [x['name'] for x in o['top']] == ['ALPHA SUPPLIERS', 'BETA MEDICAL AGENCIES'], o)
n = len(jobs)
C.run('crm_outstanding_payable', {})
check('payables are kept for a few minutes', len(jobs) == n)
pl = C.run('crm_party_last_paid', {'party': 'alpha'})
check('last paid: one clear match gives the ledger summary', pl['party'] == 'ALPHA SUPPLIERS' and pl['last_payment_date'] == '2026-10-07' and 'source' in pl, pl)
sev = C.run('crm_party_last_paid', {'party': 'beta'})
check('last paid: several matches are listed so the model asks which', sev['found'] is False and sev['several_parties_match'] == ['BETA MEDICAL AGENCIES', 'BETA PHARMA'], sev)
none = C.run('crm_party_last_paid', {'party': 'zzzz'})
check('last paid: no match says so', none['found'] is False and 'no party' in none['note'])
check('an unknown tool and wrong arguments are explained, never raised',
      'unknown tool' in str(C.run('crm_nothing', {})['error']) and 'bad arguments' in C.run('crm_gst_report', {'colour': 'red'})['error'])


def boom(fn, *a):
    raise RuntimeError('browser exploded')


C._run_job = boom
C._cache.clear()
failed = C.run('crm_outstanding_payable', {})
check('an unexpected failure becomes a short message naming only its kind - no traceback, no detail',
      failed['error'] == 'the CRM report could not be fetched (RuntimeError)', failed)
check('a failure tells the model not to try again (each try takes minutes)', 'Do not call this tool again' in failed.get('note', ''), failed)


def plain_problem(fn, *a):
    raise C.CrmError('shubhadahealth did not accept the saved login. Please check it in the vault (Salary > Edit).')


C._run_job = plain_problem
C._cache.clear()
failed2 = C.run('crm_gst_report', {'month': '2026-10'})
check('a plain CRM problem is passed on as written, with the same no-retry note',
      failed2['error'].startswith('shubhadahealth did not accept') and 'Do not call this tool again' in failed2['note'], failed2)
C._run_job = fake_job


class ShotPage:
    url = 'https://shubhadahealth.com:7007/accledger?secret=1#frag'

    def __init__(self, fails=False):
        self.fails = fails

    def screenshot(self, path):
        if self.fails:
            raise RuntimeError('no screen')
        Path(path).write_bytes(b'png')


orig_dl, C.DOWNLOAD_DIR = C.DOWNLOAD_DIR, Path(tempfile.mkdtemp())
C._save_debug(ShotPage(), 'something broke (first line only is logged)')
check('a failed job keeps its last screen on the shop PC', (C.DOWNLOAD_DIR / 'last_failure.png').read_bytes() == b'png')
C._save_debug(ShotPage(fails=True), 'x')
check('a screenshot that cannot be taken never hides the original failure', True)
check('only the path of the page is ever logged, never its query', C._where(ShotPage()) == 'https://shubhadahealth.com:7007/accledger' and 'secret' not in C._where(ShotPage()))
C.DOWNLOAD_DIR = orig_dl

# --- gating ----------------------------------------------------------------------------------------------------------------------------------------
check('the tools are offered when the vault holds the login', {t['name'] for t in C.tool_specs()} == {'crm_gst_report', 'crm_outstanding_payable', 'crm_party_last_paid'})
check('not in voice conversations (a report takes minutes)', C.tool_specs(voice=True) == [])
vault_state['configured'] = False
check('not offered when no login is saved', C.tool_specs() == [])
vault_state['configured'] = True
os.environ['CRM_ENABLED'] = '0'
check('CRM_ENABLED=0 switches them off', C.tool_specs() == [])
os.environ.pop('CRM_ENABLED')
check('a broken vault means no tools, not a crash', (setattr(vault, 'summary', lambda: (_ for _ in ()).throw(RuntimeError('bad key'))) or C.tool_specs()) == [])
vault.summary = lambda: {'shubhadahealth': {'label': 'x', 'configured': True, 'username_masked': 'ab***cd'}}

# the real job runner refuses cleanly with no login saved, and never launches a browser
C._run_job = orig_run_job
vault_state['configured'] = False
vault.summary = lambda: {'shubhadahealth': {'label': 'x', 'configured': False, 'username_masked': ''}}
try:
    C._run_job(lambda page: None); ok = False
except C.CrmError as e:
    ok = 'vault' in str(e)
check('the job runner refuses with a plain message when the vault holds no login', ok)
vault.summary = lambda: {'shubhadahealth': {'label': 'x', 'configured': True, 'username_masked': 'ab***cd'}}
orig_get = vault.get_credentials
vault.get_credentials = lambda platform: None
try:
    C._run_job(lambda page: None); ok = False
except C.CrmError as e:
    ok = 'vault' in str(e)
vault.get_credentials = orig_get
check('...and when the login cannot be read', ok)
check('the lock is free again after a refused job', C._job_lock.acquire(blocking=False) and (C._job_lock.release() or True))

# --- plugged into Ask ---------------------------------------------------------------------------------------------------------------------------------
C._run_job = fake_job
C._cache.clear()
import ask as ask_mod
names = {t['name'] for t in ask_mod._available_tools()}
check('Ask offers the CRM tools', {'crm_gst_report', 'crm_outstanding_payable', 'crm_party_last_paid'} <= names)
check('Ask withholds them in voice mode', not any(t['name'].startswith('crm_') for t in ask_mod._available_tools(voice=True)))
check('Ask runs a CRM tool that was offered', ask_mod._run_allowed_tool('crm_outstanding_payable', {}, names)['parties_owed'] == 3)
check('Ask refuses a CRM tool that was withheld', 'not available in this mode' in str(ask_mod._run_allowed_tool('crm_outstanding_payable', {}, set())))
prompt = ask_mod._build_system_prompt()
check('the prompt gains the CRM section, with its read-only and WhatsApp-file rules',
      'CRM REPORTS' in prompt and 'READ-ONLY' in prompt and 'never "attached"' in prompt)
vault.summary = lambda: {'shubhadahealth': {'label': 'x', 'configured': False, 'username_masked': ''}}
check('and loses it, with the tools, when there is no saved login',
      'CRM REPORTS' not in ask_mod._build_system_prompt() and not any(t['name'].startswith('crm_') for t in ask_mod._available_tools()))
vault.summary = orig_summary

print()
print('ALL PASSED' if not fails else f'{len(fails)} FAILED: ' + '; '.join(fails))
sys.exit(1 if fails else 0)

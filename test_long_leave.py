"""Long leave in the salary calculator (the owner, 9 Nov 2026): loss of pay up to 3 working days in a month is base / 30 a day, as before; MORE than
that prorates the whole month's pay by the working days present or on paid / sick leave, so a month away pays nothing. Also the two doors the
leave app uses (the months to read again, and what a long leave would cost). Made-up data in a throwaway folder; never the real data.

    venv/Scripts/python.exe test_long_leave.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('OPENROUTER_API_KEY', 'sk-test-not-real')
os.environ['WHATSAPP_ASK_ENABLED'] = '0'

try:
    sys.stdout.reconfigure(encoding='utf-8')          # a slip has Kannada in it
except Exception:  # noqa: BLE001
    pass

import salary  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else '   <-- %s' % (detail,)))
    if not ok:
        FAILS.append(name)


tmp = Path(tempfile.mkdtemp(prefix='long-leave-'))
salary.SETTINGS_PATH = tmp / 'settings.json'
salary.LEAVE_DIR = tmp / 'leave'
salary.LEAVE_DIR.mkdir()
salary.SETTINGS_PATH.write_text(json.dumps({'roles': {'Asha Patil': 'Biller', 'Ravi Kumar': 'Fetcher'}, 'salaries': {'Asha Patil': 10500, 'Ravi Kumar': 6650}}))

R = dict(salary.DEFAULT_RULES)
INFO = {'month': '2026-11', 'holidays': []}
NOLV = {'lop': [], 'sunday': {'sessions': 0}, 'unnoticed': {'weight': 0, 'items': []}}


def lv(n, **kw):
    return dict({'lop': [{'date': '2026-11-%02d' % (d + 2), 'why': 'x'} for d in range(n)], 'sunday': {'sessions': 0}, 'unnoticed': {'weight': 0, 'items': []}}, **kw)


# --- the month's working days ------------------------------------------------------------------------------------------------------------------
check('November 2026 has 25 working days (30 days, 5 Sundays)', salary.working_days_in_month('2026-11') == 25)
check('October 2026 has 26 (31 days, 4 Sundays, and 2 October is a holiday)', salary.working_days_in_month('2026-10') == 26)
check('a holiday the leave app sends counts out too', salary.working_days_in_month('2026-11', ['2026-11-10']) == 24)
check('a month that is not a month has none', salary.working_days_in_month('') == 0 and salary.working_days_in_month('2026-13') == 0)
check('the owner\'s limit is 3 working days', salary.DEFAULT_RULES['long_leave_days'] == 3 and salary.RULE_LIMITS['long_leave_days'] == (0, 31))

# --- the cut ------------------------------------------------------------------------------------------------------------------------------------
full = 30000
cut, day, wd, long_ = salary.leave_cut(full, 10500, R, '2026-11', [], 3)
check('up to 3 days it is the old rule: base / 30 a day (10,500: 350 a day, 3 days = 1,050)', (round(cut), round(day), wd, long_) == (1050, 350, 25, False), (cut, day, wd, long_))
cut, day, wd, long_ = salary.leave_cut(full, 10500, R, '2026-11', [], 4)
check('4 days is LONG: the whole month prorated (30,000 x 4 / 25 = 4,800)', (round(cut), round(day), wd, long_) == (4800, 1200, 25, True), (cut, day, wd, long_))
check('half days count by their weight (3.4 days is long, 2.6 is not)', salary.leave_cut(full, 10500, R, '2026-11', [], 3.4)[3] is True and salary.leave_cut(full, 10500, R, '2026-11', [], 2.6)[3] is False)
cut, day, wd, long_ = salary.leave_cut(full, 10500, R, '2026-11', [], 25)
check('the whole month away pays nothing (the cut is the whole pay)', round(cut) == full and long_)
check('more days than the month has never cut more than the month', round(salary.leave_cut(full, 10500, R, '2026-11', [], 40)[0]) == full)
check('no month known (an old call): the old rule, whatever the days', salary.leave_cut(full, 10500, R, None, [], 10)[3] is False and round(salary.leave_cut(full, 10500, R, None, [], 10)[0]) == 3500)
check('the limit can be changed (7 days: 5 are short again)', salary.leave_cut(full, 10500, dict(R, long_leave_days=7), '2026-11', [], 5)[3] is False)

# --- one slip ----------------------------------------------------------------------------------------------------------------------------------
m = salary.slip_money(10500, R, 1.0, 0, None, lv(2), INFO, False)
check('2 days of loss of pay: as before (full marks, 30,000 less 700)', m['payable'] == 29300 and not m['long_leave'] and m['lop_per_day'] == 350, m)
m = salary.slip_money(10500, R, 1.0, 0, None, lv(10), INFO, False)
check('10 days away of 25: paid for 15 of 25 (30,000 x 15 / 25 = 18,000)', m['payable'] == 18000 and m['long_leave'] and m['lop_amount'] == 12000 and m['working_days'] == 25, m)
m = salary.slip_money(10500, R, 1.0, 0, None, lv(25), INFO, False)
check('a month away pays nothing', m['payable'] == 0 and m['lop_amount'] == 30000, m)
m = salary.slip_money(10500, R, 0.0, 0, None, lv(10), INFO, False)
check('the performance and discipline already earned are prorated with everything else (55% of 30,000 = 16,500 x 15 / 25 = 9,900)', m['calculated'] == 16500 and m['payable'] == 9900, m)
m = salary.slip_money(10500, R, 0.0, 0, None, lv(10), INFO, True)
check('in the trial nobody is paid below the full pay BEFORE the leave is taken off: 30,000 x 15 / 25', m['payable'] == 18000, m)
m = salary.slip_money(10500, R, 1.0, 0, None, lv(10, sunday={'sessions': 2}, unnoticed={'weight': 1, 'items': []}), INFO, False)
check('a Sunday worked and a no-notice day still count on top, as before (18,000 + 1,200 - 525 = 18,675; the no-notice day is 1.5 x base / 30)', m['payable'] == 18675, m)

# --- the slip text and the cut list -----------------------------------------------------------------------------------------------------------
slip = {'employee': 'Asha Patil', 'role': 'Biller', 'month': '2026-11', 'days_active': 12, 'trial': False, 'perf_score': 90, 'floor_hit': False, 'gate': None,
        'groups': [], 'items': [], 'discipline': {'timed': False, 'charged': 0, 'incidents': 0, 'free': 3},
        'leave': lv(10, paid=[], sick=[], half={}, left=None, notes=[]), 'rated': True,
        'money': salary.slip_money(10500, R, 1.0, 0, None, lv(10), INFO, False)}
try:
    text = salary.slip_message(slip, R)
except Exception as exc:  # noqa: BLE001 - a slip shape this test does not fill in must not hide the money checks above
    text = 'slip text could not be built here: %s' % type(exc).__name__
check('the slip says long leave in words: how many days of the month, and the cut', 'Long leave (10 days away of the month' in text and 'paid for 15 of 25 days' in text and 'minus ₹12,000' in text, text[:600])
cl = salary.cuts_list([slip])
check('the cut list carries the same cut (12,000) and a day\'s share of the month (1,200)', cl['rows'][0]['loss_of_pay']['amount'] == 12000 and cl['rows'][0]['day_pay'] == 1200, cl)
rows, no_base = salary.cuts_without_slip([], '2026-11', {'Ravi Kumar': dict(lv(10), paid=[], sick=[], half={}, left=None)}, INFO)
check('a person with no slip yet: a long absence is prorated from the full pay (6,650 -> 19,000 x 10 / 25 = 7,600)', rows and rows[0]['loss_of_pay']['amount'] == 7600, rows)
rows, _ = salary.cuts_without_slip([], '2026-11', {'Ravi Kumar': dict(lv(2), paid=[], sick=[], half={}, left=None)}, INFO)
check('...and a short one is base / 30 as always (6,650 / 30 x 2 = 443)', rows and rows[0]['loss_of_pay']['amount'] == 443, rows)

# --- what a long leave would cost: the owner's WhatsApp note --------------------------------------------------------------------------------------
export = {'month': '2026-11', 'fetched_at': '2026-11-09T10:00:00', 'sundayPayFactor': 1.2, 'unnoticedFactor': 1.5, 'holidays': [],
          'employees': [{'emp_code': 'E1', 'name': 'Asha Patil', 'grp': 'A', 'active': 1}, {'emp_code': 'E2', 'name': 'Ravi Kumar', 'grp': 'B', 'active': 1}],
          'leaveDays': [{'emp_code': 'E1', 'date': '2026-11-03', 'pay_type': 'salary_cut', 'leave_type': 'regular'}],
          'unnoticed': [], 'sundayWork': []}
(salary.LEAVE_DIR / '2026-11.json').write_text(json.dumps(export))
imp = salary.leave_impact('Asha Patil', [{'month': '2026-11', 'lopDays': 9}])
check('9 more days on top of 1 already booked = 10 of 25: the extra cut is 12,000 less the 350 already cut = 11,650... as a long leave 12,000 - 350',
      imp['months'][0]['long_leave'] and imp['months'][0]['already_days'] == 1 and imp['full_pay'] == 30000 and imp['extra_cut'] == 11650, imp)
imp = salary.leave_impact('Asha Patil', [{'month': '2026-11', 'lopDays': 1}])
check('1 more day (2 in all) is still short: 350', imp['months'][0]['long_leave'] is False and imp['extra_cut'] == 350, imp)
check('a person with no base salary saved says so, never a guess', 'no base salary' in salary.leave_impact('Nobody Known', [{'month': '2026-11', 'lopDays': 5}]).get('error', ''))
check('a bad month or no days is ignored', salary.leave_impact('Asha Patil', [{'month': 'nov', 'lopDays': 5}, {'month': '2026-11', 'lopDays': 0}])['months'] == [])

# --- the doors the leave app uses ------------------------------------------------------------------------------------------------------------------
import server  # noqa: E402
import leave_notify  # noqa: E402

TOKEN = 't' * 40
leave_notify.token = lambda create=True: TOKEN          # the leave app's shared token, as a test knows it
cl = server.app.test_client()
H = {'X-Notify-Token': TOKEN, 'Content-Type': 'application/json'}
LOCAL = {'REMOTE_ADDR': '127.0.0.1'}
refreshed = []
server._refresh_leave = lambda month, force=False: refreshed.append((month, force)) or (None if month != '2026-12' else 'the leave app is closed')

r = cl.post('/api/leave/changed', data=json.dumps({'months': ['2026-11']}), headers={'Content-Type': 'application/json'}, environ_base=LOCAL)
check('no token: refused', r.status_code == 403)
r = cl.post('/api/leave/changed', data=json.dumps({'months': ['2026-11']}), headers=H, environ_base={'REMOTE_ADDR': '192.168.1.9'})
check('the right token from another computer: refused (this PC only)', r.status_code == 403)
r = cl.post('/api/leave/changed', data=json.dumps({'months': ['2026-11', 'x', '2026-11', '2026-12']}), headers=H, environ_base=LOCAL)
check('the months are read again at once (forced), once each, junk ignored', r.status_code == 200 and refreshed == [('2026-11', True), ('2026-12', True)] and r.get_json()['refreshed'] == ['2026-11'], (r.get_json(), refreshed))
check('...and a month that could not be read is said, so the sender knows', r.get_json()['errors'] == {'2026-12': 'the leave app is closed'}, r.get_json())
r = cl.post('/api/leave/impact', data=json.dumps({'name': 'Asha Patil', 'items': [{'month': '2026-11', 'lopDays': 9}]}), headers=H, environ_base=LOCAL)
check('the impact door gives the owner\'s figures', r.status_code == 200 and r.get_json()['extra_cut'] == 11650, r.get_json())
check('...and refuses without the token', cl.post('/api/leave/impact', data='{}', headers={'Content-Type': 'application/json'}, environ_base=LOCAL).status_code == 403)

# --- after a sales upload (the owner, 12 Nov 2026): the month's absences go to the leave app at once - stand-ins, nothing real is reached -----------
import leave_absences  # noqa: E402

synced, refreshed[:] = [], []
server.get_cached_data = lambda force=False: None
server._salary_inputs['v'] = {'stand-in': True}
real_months, real_slips, real_sync = server.salary_mod.months_available, server.salary_mod.compute_slips, leave_absences.sync
server.salary_mod.months_available = lambda inputs: ['2026-10', '2026-11', '2026-12']
server.salary_mod.compute_slips = lambda inputs, month: {'slips': [{'employee': 'x', 'month': month}]}
leave_absences.sync = lambda slips, month: synced.append(month) or {'added': 1}
got = server.absences_after_upload(today=server.pd.Timestamp('2026-11-12'))
check('after an upload mid-month: this month\'s leave is read again (forced) and its absences posted', got == {'2026-11': {'added': 1}} and synced == ['2026-11']
      and refreshed == [('2026-11', True)], (got, synced, refreshed))
synced[:], refreshed[:] = [], []
got = server.absences_after_upload(today=server.pd.Timestamp('2026-12-03'))
check('...in the first days of a month, last month too; a month the leave app cannot give is never posted from a stale copy',
      synced == ['2026-11'] and got == {'2026-11': {'added': 1}, '2026-12': None}, (got, synced))
server.salary_mod.compute_slips = lambda inputs, month: 1 / 0
check('...and a failure never reaches the upload (it is only logged)', server.absences_after_upload(today=server.pd.Timestamp('2026-11-12')) == {})
server.salary_mod.months_available, server.salary_mod.compute_slips, leave_absences.sync = real_months, real_slips, real_sync

print()
print('%d failed' % len(FAILS) if FAILS else 'ALL PASSED')
sys.exit(1 if FAILS else 0)

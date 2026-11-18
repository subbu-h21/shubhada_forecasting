"""The owner's "pay salary" (18 Nov 2026): the month's pay list the Shop Assistant puts on the bank payment sheet - salary.pay_month / data_until /
pay_list and the /api/assistant/pay-list door. Made-up slips and a throwaway settings file; never the real data, the leave app or a model.

    venv/Scripts/python.exe test_pay_list.py
"""
import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('OPENROUTER_API_KEY', 'sk-test-not-real')
os.environ['WHATSAPP_ASK_ENABLED'] = '0'

import pandas as pd  # noqa: E402

import salary  # noqa: E402
import server  # noqa: E402

KEY = 'k' * 40
os.environ['ASSISTANT_BRIDGE_KEY'] = KEY          # after the import: server loaded .env with setdefault
FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'   <-- {detail}'))
    if not cond:
        FAILS.append(name)


tmp = Path(tempfile.mkdtemp(prefix='pay-list-'))
salary.SETTINGS_PATH = tmp / 'settings.json'
salary.SETTINGS_PATH.write_text(json.dumps({
    'roles': {'Asha Patil': 'Biller', 'Ravi Kumar': 'Fetcher', 'Old Till': 'Not staff', 'Gone Away': 'Biller'},
    'salaries': {'Asha Patil': 10500, 'Ravi Kumar': 7000, 'Old Till': 5000, 'Gone Away': 7000, 'No Pay Set': 0}}))
RULES = salary.load_settings()['rules']
NO_LEAVE = {'lop': [], 'sunday': {'sessions': 0}, 'unnoticed': {'weight': 0, 'items': []}}


def slip(name, base, perf=1.0, charged=0, leave=None, trial=False, role='Biller'):
    lv = dict(NO_LEAVE, **(leave or {}))
    money = salary.slip_money(base, RULES, perf, charged, None, lv, {'month': '2026-11'}, trial) if base else None
    return {'employee': name, 'role': role, 'month': '2026-11', 'days_active': 24, 'perf_score': round(perf * 100), 'trial': trial,
            'discipline': {'charged': charged}, 'leave': lv, 'money': money}


# --- which month "pay salary" means ---------------------------------------------------------------------------------------------------------
M = ['2026-08', '2026-09', '2026-10', '2026-11']
check('said on the 30th: THIS month', salary.pay_month(M, '', datetime.date(2026, 11, 30)) == '2026-11')
check('said on the 25th: this month already', salary.pay_month(M, '', datetime.date(2026, 11, 25)) == '2026-11')
check('said on the 2nd of the next month: LAST month', salary.pay_month(M, '', datetime.date(2026, 12, 2)) == '2026-11')
check('said in the middle of a month: last month, not the unfinished one', salary.pay_month(M, '', datetime.date(2026, 11, 18)) == '2026-10')
check('said on 31 Dec with no December slips yet: falls back to November', salary.pay_month(M, '', datetime.date(2026, 12, 31)) == '2026-11')
check('last month has no slips either: the latest month that has', salary.pay_month(M, '', datetime.date(2027, 3, 10)) == '2026-11')
check('a month the owner named is used', salary.pay_month(M, '2026-09', datetime.date(2026, 11, 30)) == '2026-09')
check('a named month with no slips is NOT swapped for another', salary.pay_month(M, '2026-07', datetime.date(2026, 11, 30)) is None)
check('no months at all', salary.pay_month([], '', datetime.date(2026, 11, 30)) is None)

# --- how far the month's data reaches -------------------------------------------------------------------------------------------------------
seen = pd.DataFrame({'Employee': ['Asha Patil'] * 3, 'Day': ['2026-10-30', '2026-10-31', '2026-11-12'], 'Calendar_Month': ['2026-10', '2026-10', '2026-11']})
check('a month whose data reaches its last working day is complete', salary.data_until({'seen_daily': seen}, '2026-10') == ('2026-10-31', True))
check('a month whose data stops on the 12th is not', salary.data_until({'seen_daily': seen}, '2026-11') == ('2026-11-12', False))
check('a month ending on a Sunday is complete on the Saturday', salary.data_until(
    {'seen_daily': pd.DataFrame({'Employee': ['A'], 'Day': ['2027-01-30'], 'Calendar_Month': ['2027-01']})}, '2027-01') == ('2027-01-30', True))
check('no data for the month: no date, not complete', salary.data_until({'seen_daily': seen}, '2026-09') == ('', False)
      and salary.data_until(None, '2026-09') == ('', False))

# --- the list -------------------------------------------------------------------------------------------------------------------------------
full_asha = round(10500 * 100 / RULES['fixed_pct'])
slips = [slip('Ravi Kumar', 7000, perf=0.5, leave={'lop': [{'date': '2026-11-10', 'why': 'leave not approved'}]}, role='Fetcher'),
         slip('Asha Patil', 10500), slip('No Pay Set', 0),
         slip('Sunday Sita', 9000, leave={'sunday': {'sessions': 2}, 'unnoticed': {'weight': 1, 'items': [{'date': '2026-11-05'}]}})]
out = salary.pay_list(slips, '2026-11')
rows = {r['name']: r for r in out['rows']}
check('one row per slip that has a base salary, by name', [r['name'] for r in out['rows']] == ['Asha Patil', 'Ravi Kumar', 'Sunday Sita'], out['rows'])
check('a slip with no base salary is named, not paid', out['no_base'] == ['No Pay Set'])
check('someone with a base salary but no slip is named (and "Not staff" is not)', out['no_slip'] == ['Gone Away'], out['no_slip'])
check('full marks, no cuts: to be paid = the full monthly pay', rows['Asha Patil']['payable'] == full_asha == rows['Asha Patil']['full'], rows['Asha Patil'])
r = rows['Ravi Kumar']
check('a loss-of-pay day and half performance: the amount is the slip\'s own', r['payable'] == slips[0]['money']['payable']
      and r['loss_of_pay'] == {'days': 1, 'amount': slips[0]['money']['lop_amount'], 'long_leave': False, 'working_days': slips[0]['money']['working_days']}
      and r['performance'] == {'score': 50, 'pay': slips[0]['money']['perf_pay'], 'max': slips[0]['money']['perf_max']} and r['loss_of_pay']['amount'] > 0, r)
r = rows['Sunday Sita']
check('Sunday work adds, a day away with no notice cuts', r['sunday']['sessions'] == 2 and r['sunday']['amount'] > 0 and r['unnoticed'] == {'days': 1, 'amount': slips[3]['money']['unnoticed_amount']}
      and r['payable'] == r['calculated'] - r['unnoticed']['amount'] + r['sunday']['amount'], r)
check('the total is the sum of what is to be paid', out['total'] == sum(x['payable'] for x in out['rows']) and out['people'] == 3)
check('no slips: an empty list, nobody named twice', salary.pay_list([], '2026-11')['rows'] == [] and salary.pay_list([], '2026-11')['total'] == 0)

# --- the door -------------------------------------------------------------------------------------------------------------------------------
cl = server.app.test_client()


def get(path, key=KEY, addr='127.0.0.1'):
    return cl.get(path, headers={'X-Assistant-Key': key} if key else {}, environ_base={'REMOTE_ADDR': addr})


calls = []
server.get_cached_data = lambda force=False: (None, 0)
server._salary_inputs['v'] = {'seen_daily': seen}
salary.months_available = lambda inputs: list(M)
salary.compute_slips = lambda inputs, month: (calls.append(month), {'slips': slips if month == '2026-11' else [], 'needs_role': ['New Person']})[1]
server._refresh_leave = lambda month, force=False: (calls.append(('leave', month, force)), None)[1]
server.leave_absences.sync = lambda *a, **k: None
check('no key: refused', get('/api/assistant/pay-list', key='').status_code == 403)
check('another computer: refused', get('/api/assistant/pay-list', addr='192.168.1.20').status_code == 403)
r = get('/api/assistant/pay-list?month=2026-11')
d = r.get_json()
check('the named month: the list, how far its data reaches, and the leave app read again first', r.status_code == 200 and d['month'] == '2026-11' and d['people'] == 3
      and d['data_until'] == '2026-11-12' and d['month_complete'] is False and d['leave_fetched'] is True and d['needs_role'] == ['New Person']
      and calls[:2] == [('leave', '2026-11', True), '2026-11'], (d, calls))
r = get('/api/assistant/pay-list?month=2026-07')
check('a month with no slips: said so, with the months that have', r.status_code == 404 and 'no salary slips for 2026-07' in r.get_json()['error']
      and r.get_json()['months'] == M, r.get_json())
server._refresh_leave = lambda month, force=False: 'the leave app is not running'
check('the leave app could not be read: the list still comes, marked', get('/api/assistant/pay-list?month=2026-11').get_json()['leave_fetched'] is False)
salary.months_available = lambda inputs: []
check('no salary months at all: said so', get('/api/assistant/pay-list').status_code == 404)

# --- the owner-confirmed spellings of 18 Nov 2026: a typo'd name is never a second person on the pay list ---------------------------------------
import run_reckoner as rk  # noqa: E402

typos = {'Sudha Nagesh Devadiga': 'Sudha N Devadiga', 'Sharath Nagendra Naik As': 'Sharath Nagendra Naik', 'Manjunata D Gosavi0': 'Manjunata D Gosavi',
         'Abhishek Seetaram N': 'Abhishek Seetaram Naik', 'Abhishek Seetaram Naikg': 'Abhishek Seetaram Naik', 'Parashuram T Naik000': 'Parashuram T Naik',
         'Manjunata D Gosavi[': 'Manjunata D Gosavi', 'Manjunata D Gosavi6': 'Manjunata D Gosavi', 'Sharath Nagendra Naik 70364777': 'Sharath Nagendra Naik'}
check('each confirmed spelling becomes the one name the salary calculator knows (also in capitals and with spaces)',
      list(rk._norm_name(pd.Series([' ' + k.upper() + ' ' for k in typos]))) == list(typos.values()))
check('...and the real names are left alone, and no alias points at another alias', list(rk._norm_name(pd.Series(list(set(typos.values()))))) == list(set(typos.values()))
      and not [v for v in rk.EMPLOYEE_ALIASES.values() if v in rk.EMPLOYEE_ALIASES])
check('a different person who only looks alike is NOT merged (owner-confirmed 2026-09-29)', rk._norm_name(pd.Series(['Raghavendra C Shet'])).iloc[0] == 'Raghavendra C Shet')

# --- a NEW JOINER is paid from their first day, trial or not (owner's exception, 18 Nov 2026) ---------------------------------------------------
SCORED = ['2026-08', '2026-09', '2026-10', '2026-11']
j = salary.joiner_share('2026-10-30', '2026-10', (), SCORED)
check('started 30 Oct: 2 of the 26 working days of October (Sundays and Gandhi Jayanti are not working days)', j == {'from': '2026-10-30', 'days': 2, 'of': 26, 'share': 2 / 26}, j)
check('no joiner: first seen on the 1st, an earlier month, or the month is not the first-seen month',
      salary.joiner_share('2026-10-01', '2026-10', (), SCORED) is None and salary.joiner_share('2026-09-20', '2026-10', (), SCORED) is None
      and salary.joiner_share(None, '2026-10', (), SCORED) is None and salary.joiner_share('2026-10-30', '2026-11', (), SCORED) is None)
check('a month whose PREVIOUS month has no names in the sales export never makes anyone a joiner (Jun / Jul 2026 had none, so all looked new in August)',
      salary.joiner_share('2026-08-03', '2026-08', (), ['2026-08', '2026-09']) is None)
check('days off the leave app lists are not counted, and a joiner who starts on the last working day is paid that one day',
      salary.joiner_share('2026-10-26', '2026-10', ('2026-10-27',), SCORED)['days'] == 5 and salary.joiner_share('2026-10-31', '2026-10', (), SCORED)['days'] == 1)
RULES2 = salary.load_settings()['rules']
NOLV2 = {'lop': [], 'sunday': {'sessions': 0}, 'unnoticed': {'weight': 0, 'items': []}}
full_pay = salary.slip_money(7000, RULES2, 1.0, 0, None, NOLV2, {'month': '2026-10'}, True)
joined = salary.slip_money(7000, RULES2, 0.0, 0, None, NOLV2, {'month': '2026-10'}, True, j)
check('a normal trial slip pays the full monthly pay; a new joiner gets 2/26 of it, even in the trial',
      full_pay['payable'] == full_pay['full'] and joined['payable'] == round(joined['full'] * 2 / 26) and 'join' not in full_pay
      and joined['join'] == {'from': '2026-10-30', 'days': 2, 'of': 26}, (full_pay, joined))
j_nov = salary.joiner_share('2026-11-06', '2026-11', (), SCORED)
mid = salary.slip_money(7000, RULES2, 1.0, 0, None, NOLV2, {'month': '2026-11'}, False, j_nov)
check('outside the trial it is the calculated pay for those days only', mid['payable'] == round(mid['calculated'] * j_nov['share']), mid)
check('the slip the person is sent says when they joined and for how many days they are paid', "You joined on 30 Oct: this month is paid for 2 of the month's 26 working days."
      in salary.slip_message({'employee': 'New Person', 'role': 'Biller', 'month': '2026-10', 'days_active': 2, 'trial': True, 'perf_score': None, 'floor_hit': False, 'gate': None,
                              'items': [], 'groups': [], 'discipline': {'late': 0, 'long_lunch': 0, 'early_exit': 0, 'incidents': 0, 'free': 3, 'charged': 0, 'timed': False},
                              'leave': dict(NOLV2, paid=[], sick=[], left=None, half={}), 'money': joined, 'notes': [], 'commitment': None, 'shift': None}, RULES2))
check('the pay list carries it, so the owner sees it before paying', salary.pay_list([{'employee': 'New Person', 'role': 'Biller', 'days_active': 2, 'trial': True, 'perf_score': None,
      'leave': NOLV2, 'money': joined}], '2026-10')['rows'][0]['joined'] == joined['join'])

print()
print('ALL PASSED' if not FAILS else '%d FAILED: %s' % (len(FAILS), '; '.join(FAILS)))
sys.exit(1 if FAILS else 0)

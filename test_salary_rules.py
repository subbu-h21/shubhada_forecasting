"""The owner's thumb rules of 2 Nov 2026 in the salary calculator: a Sunday session worked pays 1.2 x half a day's pay on top, a day (or half day)
the admin marks as away with no notice costs 1.5 x the day's pay (and replaces any other cut for that day), and the slip says how many leave
days are left this year. Reads a made-up leave-app export from a throwaway folder; never the real data.

    venv/Scripts/python.exe test_salary_rules.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import salary  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else '   <-- %s' % (detail,)))
    if not ok:
        FAILS.append(name)


tmp = Path(tempfile.mkdtemp(prefix='salary-rules-'))
salary.SETTINGS_PATH = tmp / 'settings.json'
salary.LEAVE_DIR = tmp / 'leave'
salary.LEAVE_DIR.mkdir()
salary.SETTINGS_PATH.write_text(json.dumps({'roles': {'Asha Patil': 'Biller', 'Ravi Kumar': 'Fetcher'}, 'salaries': {'Asha Patil': 30000, 'Ravi Kumar': 15000}}))

# --- the money ---------------------------------------------------------------------------------------------------------------------------
check('a day\'s pay is base / 30: a full Sunday (2 sessions) pays 1.2 x a day, one session 0.6 x',
      salary.thumb_rule_money(30000, 30, 2, 0)[0] == 1200 and salary.thumb_rule_money(30000, 30, 1, 0)[0] == 600)
check('an unnoticed full day costs 1.5 x a day, a half day 0.75 x', salary.thumb_rule_money(30000, 30, 0, 1)[1] == 1500 and salary.thumb_rule_money(30000, 30, 0, 0.5)[1] == 750)
check('nothing worked and nothing marked: nothing', salary.thumb_rule_money(30000, 30, 0, 0) == (0, 0))
check('the leave app\'s own factors are used when it sends them', salary.thumb_rule_money(30000, 30, 2, 1, 1.5, 2.0) == (1500, 2000))
check('the defaults are the owner\'s rules', (salary.SUNDAY_FACTOR, salary.UNNOTICED_FACTOR) == (1.2, 1.5))

# --- reading the leave app's month -------------------------------------------------------------------------------------------------------------
export = {'month': '2026-11', 'fetched_at': '2026-11-30T10:00:00', 'sundaySessionPay': {'A': 150, 'B': 75}, 'sundayPayFactor': 1.2, 'unnoticedFactor': 1.5,
          'employees': [{'emp_code': 'E1', 'name': 'Asha Patil', 'grp': 'A', 'active': 1, 'branch': 'Hospet Road', 'leaveLeft': {'label': 'Apr 2026 – Mar 2027', 'paid': 4, 'sick': 1}},
                        {'emp_code': 'E2', 'name': 'Ravi Kumar', 'grp': 'B', 'active': 1, 'branch': 'Shivaji Chowk', 'leaveLeft': {'label': 'Apr 2026 – Mar 2027', 'paid': 6, 'sick': 2}}],
          'leaveDays': [{'emp_code': 'E2', 'date': '2026-11-05', 'pay_type': 'paid', 'leave_type': 'regular'},
                        {'emp_code': 'E2', 'date': '2026-11-06', 'pay_type': 'salary_cut', 'leave_type': 'regular'}],
          'unnoticed': [{'emp_code': 'E1', 'date': '2026-11-10', 'portion': 'full', 'weight': 1}, {'emp_code': 'E1', 'date': '2026-11-11', 'portion': 'half', 'weight': 0.5}],
          'sundayWork': [{'emp_code': 'E2', 'sessions': 2, 'amount': 150, 'dates': ['2026-11-08 morning', '2026-11-08 evening']}]}
(salary.LEAVE_DIR / '2026-11.json').write_text(json.dumps(export))
by_emp, info = salary.leave_for_month('2026-11')
asha, ravi = by_emp['Asha Patil'], by_emp['Ravi Kumar']
check('the unnoticed days are read per person, with their weights (a half day counts half)', asha['unnoticed']['weight'] == 1.5 and [i['portion'] for i in asha['unnoticed']['items']] == ['full', 'half'], asha['unnoticed'])
check('what each person has left this leave year comes with it', asha['left'] == {'label': 'Apr 2026 – Mar 2027', 'paid': 4, 'sick': 1} and ravi['left']['paid'] == 6, (asha['left'], ravi['left']))
check('the leave app\'s factors come through; ordinary leave still reads as before', info['sunday_factor'] == 1.2 and info['unnoticed_factor'] == 1.5
      and ravi['paid'] == ['2026-11-05'] and ravi['lop'] == ['2026-11-06'] and ravi['sunday']['sessions'] == 2)
check('someone with nothing unusual has no unnoticed days', salary.leave_for_month('2026-11')[0]['Ravi Kumar']['unnoticed'] == {'weight': 0, 'items': []})

# --- one person's slip: a marked day replaces every other cut for that day ---------------------------------------------------------------------------
logged = [{'type': 'unplanned_leave', 'date': '2026-11-10', 'employee': 'Asha Patil'}, {'type': 'unplanned_leave', 'date': '2026-11-20', 'employee': 'Asha Patil'}]
lv = salary._leave_for('Asha Patil', '2026-11', by_emp, info, logged, None, {}, [])
check('a day marked unnoticed is not cut a second time as an ordinary absence', [i['date'] for i in lv['lop']] == ['2026-11-20'], lv['lop'])
check('...and the slip says why, and carries what is left', any('Away with no notice' in n and '1.5 days' in n for n in lv['notes']) and lv['left']['paid'] == 4, lv['notes'])
lv2 = salary._leave_for('Someone Else', '2026-11', by_emp, info, [], None, {}, [])
check('a person the leave app does not know still gets a clean, complete record', lv2['unnoticed'] == {'weight': 0, 'items': []} and lv2['left'] is None and lv2['sunday']['sessions'] == 0)

# --- national holidays are kept like Sundays (the owner, 3 Nov 2026): the few who work them punch in, nobody else is absent ---------------
days = ['2026-10-01', '2026-10-02', '2026-10-03']
punched = {'A': days, 'B': days, 'C': days}
check('2 October is not a working day, even when 3 people punched in for holiday work', salary._open_days(punched, '2026-10') == ['2026-10-01', '2026-10-03'])
check('...and a holiday the leave app sends (the admin added it) is not either', salary._open_days(punched, '2026-10', ['2026-10-03']) == ['2026-10-01'])

# --- the salary-cut list for the owner (4 Nov 2026) ---------------------------------------------------------------------------------------------------
slips = [
    {'employee': 'Meena Shetty', 'money': {'lop_per_day': 600, 'lop_amount': 1200, 'unnoticed_amount': 900},
     'leave': {'lop': [{'date': '2026-11-05', 'why': 'leave not approved'}, {'date': '2026-11-06', 'why': 'no paid days left'}],
               'unnoticed': {'weight': 1, 'items': [{'date': '2026-11-02', 'portion': 'full', 'weight': 1}]}}},
    {'employee': 'Ravi Kumar', 'money': {'lop_per_day': 800, 'lop_amount': 0, 'unnoticed_amount': 600},
     'leave': {'lop': [], 'unnoticed': {'weight': 0.5, 'items': [{'date': '2026-11-02', 'portion': 'half', 'weight': 0.5}]}}},
    {'employee': 'Nobody Cut', 'money': {'lop_per_day': 500, 'lop_amount': 0, 'unnoticed_amount': 0}, 'leave': {'lop': [], 'unnoticed': {'weight': 0, 'items': []}}},
    {'employee': 'No Base Person', 'money': None, 'leave': {'lop': [{'date': '2026-11-03', 'why': 'absent without leave'}], 'unnoticed': {'weight': 0, 'items': []}}},
    {'employee': 'Sita Rao', 'money': {'lop_per_day': 500, 'lop_amount': 500, 'unnoticed_amount': 0}, 'leave': {'lop': [{'date': '2026-11-04', 'why': 'absent without leave'}], 'unnoticed': {'weight': 0, 'items': []}}},
]
cl = salary.cuts_list(slips)
check('cuts list: only people with a cut, largest first, with the loss of pay and the unnoticed parts apart',
      [r['name'] for r in cl['rows']] == ['Meena Shetty', 'Ravi Kumar', 'Sita Rao'] and cl['rows'][0]['loss_of_pay']['amount'] == 1200 and cl['rows'][0]['unnoticed']['amount'] == 900, cl)
check('...each total is the two parts added; the month total and the head count are right', [r['total'] for r in cl['rows']] == [2100, 600, 500] and cl['total'] == 3200 and cl['people'] == 3, cl)
check('...someone with cut days but no base salary saved is listed separately, with no amount', cl['no_base'] == [{'name': 'No Base Person', 'loss_of_pay_days': 1, 'unnoticed_days': 0}], cl['no_base'])
check('...and a month with no cuts is an empty list', salary.cuts_list([slips[2]]) == {'rows': [], 'total': 0, 'people': 0, 'no_base': []})

# --- the real base is the fixed 35% of the full monthly pay; cuts start from 1 Nov 2026 (owner, 4 Nov 2026) ---------------------------------------------------
R35 = dict(salary.DEFAULT_RULES)
NOLV = {'lop': [], 'sunday': {'sessions': 0}, 'unnoticed': {'weight': 0, 'items': []}}
mn = salary.slip_money(6650, R35, 1.0, 0, None, NOLV, {}, False)
check('the base 6,650 stands for a full monthly pay of 19,000: full marks and no incidents pay exactly that', mn['full'] == 19000 and mn['fixed'] == 6650 and mn['calculated'] == 19000
      and mn['perf_max'] == 8550 and mn['disc_max'] == 3800 and mn['payable'] == 19000, mn)
mn = salary.slip_money(6650, R35, 0.5, 2, None, NOLV, {}, False)
check('half the performance and two incidents charged: the shares come from the full pay, not from the base', mn['perf_pay'] == 4275 and mn['disc_pay'] == 3420 and mn['calculated'] == 14345, mn)
check('...and after the trial that is what is paid - a salary cut, effective from the first month after the trial', mn['payable'] == 14345 and salary.DEFAULT_RULES['trial_until'] == '2026-10')
mt = salary.slip_money(6650, R35, 0.5, 2, None, NOLV, {}, True)
check('in the trial nobody is paid below the full pay', mt['payable'] == 19000 and mt['vs_base'] == -4655, mt)
LV = {'lop': [{'date': '2026-11-05', 'why': 'x'}, {'date': '2026-11-06', 'why': 'x'}], 'sunday': {'sessions': 2}, 'unnoticed': {'weight': 1, 'items': []}}
ml = salary.slip_money(6650, R35, 1.0, 0, None, LV, {}, False)
check('a day\'s cut uses the real base / 30 (6,650: 222 a day; a no-notice day 1.5 x = 332), taken off the pay', ml['lop_per_day'] == 222 and ml['lop_amount'] == 443 and ml['unnoticed_amount'] == 332, ml)
check('...but a Sunday worked still pays from the full pay (19,000 / 30 x 0.5 x 1.2 = 380 a session), as before', ml['sunday_pay'] == 760 and ml['payable'] == 18984, ml)
cm = {'checks': [], 'met': 1, 'of': 2, 'counted': True}
check('the target bonus is a share of the full pay too (5% of 19,000 = 950, half reached = 475)', salary.slip_money(6650, R35, 1.0, 0, cm, NOLV, {}, False)['commit_bonus'] == 475)

# --- shop work from the CRM (items shelved, DC, new products ...) counts for everyone, against what the shop expects (owner, 4 Nov 2026) ---------------------------
RS = dict(salary.DEFAULT_RULES)
check('every role still adds up to 100, and Billers and Fetchers now carry a small shop-work share', all(sum(w.values()) == 100 for w in salary.GROUP_WEIGHTS.values())
      and salary.GROUP_WEIGHTS['Biller']['B'] == 5 and salary.GROUP_WEIGHTS['Fetcher']['B'] == 5, salary.GROUP_WEIGHTS)
check('a full month at the expected number earns 100; half of it 50; nothing 0', [round(salary.shop_work_score(v, 26, RS, 'tgt_shelved')) for v in (180, 90, 0)] == [100, 50, 0])
check('a part month is judged on the days worked (13 days: 90 shelved is the full number)', round(salary.shop_work_score(90, 13, RS, 'tgt_shelved')) == 100)
check('far above the number is capped at the item cap (120)', salary.shop_work_score(1961, 26, RS, 'tgt_shelved') == 120)
check('no active days, or no expected number, gives no mark', salary.shop_work_score(5, 0, RS, 'tgt_shelved') is None and salary.shop_work_score(5, 20, dict(RS, tgt_shelved=0), 'tgt_shelved') is None)
check('the expected numbers are on the Rules screen, and a typed one is kept', all(k in salary.DEFAULT_RULES for k in salary.SHOP_TARGET_RULE.values())
      and salary.update_settings(rules={'tgt_shelved': 250})[1] is None)
check('...the rule the owner typed is the one used', salary.load_settings()['rules']['tgt_shelved'] == 250)
salary.update_settings(rules={'tgt_shelved': 180})
check('...and put back', salary.load_settings()['rules']['tgt_shelved'] == 180)

# --- a person's own slip for the assistant: found by their WhatsApp number, only a month the owner really sent (4 Nov 2026) ------------------------------------
PH = {'Meena Shetty': '919000000001', 'Ravi Kumar': '9000000002', 'No Number': ''}
check('a number is matched however it is written', [salary.employee_by_phone(PH, n) for n in ('9000000001', '+91 90000 00001', '919000000001', '09000000001', '9000000002')]
      == ['Meena Shetty'] * 4 + ['Ravi Kumar'])
check('...an unknown, empty or short number matches nobody', [salary.employee_by_phone(PH, n) for n in ('9000000000', '', None, '12345')] == [None, None, None, None])
SENDS = {'2026-09': {'Meena Shetty': {'sent': True}}, '2026-10': {'Meena Shetty': {'sent': True}, 'Ravi Kumar': {'sent': False, 'reason': 'x'}},
         '2026-11': {'Ravi Kumar': {'sent': True}}}
check('the newest month really sent to that person; a failed send or another person\'s month does not count', salary.latest_sent_month(SENDS, 'Meena Shetty') == '2026-10'
      and salary.latest_sent_month(SENDS, 'Ravi Kumar') == '2026-11' and salary.latest_sent_month(SENDS, 'Nobody') is None and salary.latest_sent_month({}, 'Meena Shetty') is None)

# --- a person marked away who has no slip yet (nothing in the CRM this month) is still in the cut list (4 Nov 2026: a person away from 2 Nov) -------------------------------
LVX = {'Asha Patil': {'lop': [{'date': '2026-11-05', 'why': 'leave not approved'}], 'sunday': {'sessions': 0},
                      'unnoticed': {'weight': 1, 'items': [{'date': '2026-11-02', 'portion': 'full', 'weight': 1}]}},
       'Ravi Kumar': {'lop': [], 'sunday': {'sessions': 0}, 'unnoticed': {'weight': 0, 'items': []}},
       'Nobody Listed': {'lop': [{'date': '2026-11-03', 'why': 'x'}], 'sunday': {'sessions': 0}, 'unnoticed': {'weight': 0, 'items': []}}}
xr, xn = salary.cuts_without_slip([], '2026-11', LVX, {})
check('no slip, cut days: the amounts are what a slip would give (30,000 base: 1,000 a day; a no-notice day 1.5 x)', len(xr) == 1 and xr[0]['name'] == 'Asha Patil' and xr[0]['day_pay'] == 1000
      and xr[0]['loss_of_pay']['amount'] == 1000 and xr[0]['unnoticed']['amount'] == 1500 and xr[0]['total'] == 2500, xr)
check('...someone with no base salary saved is named without an amount, and someone with no cut days is left out', xn == [{'name': 'Nobody Listed', 'loss_of_pay_days': 1, 'unnoticed_days': 0}])
check('...a person who already has a slip is not counted twice', salary.cuts_without_slip([{'employee': 'Asha Patil'}], '2026-11', LVX, {})[0] == [])
check('...and the leave app\'s own no-notice factor is used when it sends one', salary.cuts_without_slip([], '2026-11', LVX, {'unnoticed_factor': 2.0})[0][0]['unnoticed']['amount'] == 2000)
merged = salary.cuts_list([slips[0]], (xr, xn))
check('the cut list holds both the people with slips and the ones without, largest first, with one total', [r['name'] for r in merged['rows']] == ['Asha Patil', 'Meena Shetty'] and merged['total'] == 2500 + 2100 and merged['people'] == 2
      and merged['no_base'][0]['name'] == 'Nobody Listed', merged)

# --- half-day leave (the owner, 6 Nov 2026): a morning half counts 0.4 of a day, an evening half 0.6 ----------------------------------------------
half_export = dict(export, month='2026-12', unnoticed=[], sundayWork=[],
                   leaveDays=[{'emp_code': 'E2', 'date': '2026-12-07', 'pay_type': 'salary_cut', 'leave_type': 'regular', 'portion': 'evening', 'weight': 0.6},
                              {'emp_code': 'E2', 'date': '2026-12-08', 'pay_type': 'paid', 'leave_type': 'regular', 'portion': 'morning', 'weight': 0.4},
                              {'emp_code': 'E1', 'date': '2026-12-09', 'pay_type': 'salary_cut', 'leave_type': 'regular', 'portion': 'full', 'weight': 1}])
(salary.LEAVE_DIR / '2026-12.json').write_text(json.dumps(half_export))
hb, hinfo = salary.leave_for_month('2026-12')
check('half days are read with their half and weight; a full day is not in the half list',
      hb['Ravi Kumar']['half'] == {'2026-12-07': {'portion': 'evening', 'weight': 0.6}, '2026-12-08': {'portion': 'morning', 'weight': 0.4}}
      and hb['Asha Patil']['half'] == {} and hb['Ravi Kumar']['lop'] == ['2026-12-07'], hb['Ravi Kumar'])
rl = salary._leave_for('Ravi Kumar', '2026-12', hb, hinfo, [], None, {}, [])
check('a loss-of-pay evening half is one item weighing 0.6, saying which half', rl['lop'] == [{'date': '2026-12-07', 'why': 'leave not approved / no paid days left (evening half)', 'portion': 'evening', 'weight': 0.6}], rl['lop'])
check('...the approved morning half counts 0.4 of a paid day on the slip', any('Approved leave: 0.4 paid day' in n for n in rl['notes']), rl['notes'])
mh = salary.slip_money(6650, R35, 1.0, 0, None, dict(rl, sunday={'sessions': 0}), {}, False)
check('...and is cut at 0.6 of a day\'s pay (6,650 / 30 x 0.6 = 133)', mh['lop_amount'] == 133 and mh['payable'] == 19000 - 133, mh)
check('a morning half of loss of pay would be 0.4 (89); whole days still count 1', salary.lop_days([{'date': 'x', 'why': 'y', 'weight': 0.4}, {'date': 'z', 'why': 'w'}]) == 1.4
      and round(salary.slip_money(6650, R35, 1.0, 0, None, {'lop': [{'date': 'x', 'why': 'y', 'weight': 0.4}], 'sunday': {'sessions': 0}, 'unnoticed': {'weight': 0, 'items': []}}, {}, False)['lop_amount']) == 89)
hc = salary.cuts_list([{'employee': 'Ravi Kumar', 'money': mh, 'leave': rl}])
check('the cut list says 0.6 days for the half day', hc['rows'][0]['loss_of_pay']['days'] == 0.6 and hc['rows'][0]['loss_of_pay']['amount'] == 133, hc)
hx, _ = salary.cuts_without_slip([], '2026-12', {'Ravi Kumar': hb['Ravi Kumar']}, hinfo)
check('...and so does a person with no slip yet (15,000 base: 500 a day, 0.6 = 300)', hx and hx[0]['loss_of_pay']['days'] == 0.6 and hx[0]['loss_of_pay']['amount'] == 300, hx)

print()
print('ALL PASSED' if not FAILS else '%d FAILED: %s' % (len(FAILS), '; '.join(FAILS)))
sys.exit(1 if FAILS else 0)

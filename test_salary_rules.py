"""The owner's thumb rules of 2 Nov 2026 in the salary calculator: a Sunday session worked pays 1.2 x half a day's pay on top, a day (or half day)
the boss marks as away with no notice costs 1.5 x the day's pay (and replaces any other cut for that day), and the slip says how many leave
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

print()
print('ALL PASSED' if not FAILS else '%d FAILED: %s' % (len(FAILS), '; '.join(FAILS)))
sys.exit(1 if FAILS else 0)

"""Absent-with-no-information days go on record in the leave app (leave_absences.py). Made-up data; nothing is sent anywhere.

    venv/Scripts/python.exe test_leave_absences.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('OPENROUTER_API_KEY', 'sk-test-not-real')
os.environ['WHATSAPP_ASK_ENABLED'] = '0'

import leave_absences  # noqa: E402
import salary  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + ('' if ok else f'  {detail}'))
    if not ok:
        FAILS.append(name)


MONTH = '2026-11'
EXPORT = {'month': MONTH, 'employees': [{'emp_code': 'E001', 'name': 'Asha Patil'}, {'emp_code': 'E002', 'name': 'Ravi Kumar'}]}
SLIPS = [
    {'employee': 'Asha Patil', 'leave': {'lop': [
        {'date': '2026-11-05', 'why': 'absent without leave'},
        {'date': '2026-11-06', 'why': 'leave not approved / no paid days left'},   # applied leave - not an absence
        {'date': '2026-11-09', 'why': 'absent without leave (logged)'},
        {'date': '2026-10-30', 'why': 'absent without leave'},                      # another month
    ]}},
    {'employee': 'Ravi Kumar', 'leave': {'lop': []}},
    {'employee': 'Someone Else', 'leave': {'lop': [{'date': '2026-11-05', 'why': 'absent without leave'}]}},   # not in the leave app
]

salary.load_leave = lambda month: EXPORT if month == MONTH else None
salary.match_site_names = lambda names: {n: n for n in names if n in ('Asha Patil', 'Ravi Kumar')}

rows = leave_absences.rows_from_slips(SLIPS, MONTH)
check('only absent-without-leave days of this month, for people the leave app knows',
      rows == [{'emp_code': 'E001', 'date': '2026-11-05'}, {'emp_code': 'E001', 'date': '2026-11-09'}], rows)
check('no saved leave export -> nothing to post', leave_absences.rows_from_slips(SLIPS, '2026-09') == [])

posted = []
leave_absences.post = lambda month, rows, timeout=5: posted.append((month, rows)) or {'added': len(rows), 'removed': 0}
leave_absences._last.clear()
leave_absences.sync(SLIPS, MONTH)
leave_absences.sync(SLIPS, MONTH)
check('an unchanged list is posted once, not on every Salary screen opening', len(posted) == 1, posted)
SLIPS[1]['leave']['lop'].append({'date': '2026-11-10', 'why': 'absent without leave'})
leave_absences.sync(SLIPS, MONTH)
check('a changed list is posted again', len(posted) == 2 and len(posted[1][1]) == 3, posted)


def boom(*a, **k):
    raise RuntimeError('The leave app is not running')


leave_absences.post = boom
leave_absences._last.clear()
check('a leave app that is down never breaks the slips', leave_absences.sync(SLIPS, MONTH) is None)
check('nothing to post without slips', leave_absences.sync([], MONTH) is None)

halves = [{'employee': 'Asha Patil', 'leave': {'lop': [
    {'date': '2026-11-04', 'why': 'absent half the day (morning half not seen)', 'portion': 'morning', 'weight': 0.4},
    {'date': '2026-11-05', 'why': 'marked absent by the admin (evening half)', 'portion': 'evening', 'weight': 0.6},   # already in the leave app
    {'date': '2026-11-06', 'why': 'leave not approved / no paid days left (evening half)', 'portion': 'evening', 'weight': 0.6},
]}}]
check('a half day not seen is posted with its half; the owner\'s own marks and leave halves are not',
      leave_absences.rows_from_slips(halves, MONTH) == [{'emp_code': 'E001', 'date': '2026-11-04', 'portion': 'morning'}],
      leave_absences.rows_from_slips(halves, MONTH))

print('\nFAILED: ' + ', '.join(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)

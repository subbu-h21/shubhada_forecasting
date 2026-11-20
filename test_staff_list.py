"""The staff list branchwise (the owner, 20 Nov 2026): who works at which branch, seniors (Group A) and juniors (Group B), read live from the
leave app's Employees page through its /api/export/status. Made-up staff; the leave app is never called.

    venv/Scripts/python.exe test_staff_list.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('OPENROUTER_API_KEY', 'sk-test-not-real')
os.environ['WHATSAPP_ASK_ENABLED'] = '0'

import ask_tools as T  # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else '   <-- %s' % (detail,)))
    if not ok:
        FAILS.append(name)


STATUS = {'today': '2026-11-20', 'leaveYear': 'Apr 2026 - Mar 2027', 'waiting_for_admin': [], 'employees': [
    {'emp_code': 'E001', 'name': 'Abhishek seetaram naik', 'group': 'A', 'branch': 'Shivaji Chowk', 'paid_days_left': 3, 'upcoming': []},
    {'emp_code': 'E002', 'name': 'Akshata', 'group': 'B', 'branch': 'Hospet Road', 'paid_days_left': 1, 'upcoming': []},
    {'emp_code': 'E006', 'name': 'Dattatraya V Hegde', 'group': 'A', 'branch': 'Hospet Road', 'paid_days_left': 0, 'upcoming': []},
    {'emp_code': 'E003', 'name': 'Archana.Gopal.Marathi', 'group': 'B', 'branch': 'Shivaji Chowk', 'paid_days_left': 0, 'upcoming': []},
    {'emp_code': 'E005', 'name': 'Chaitra g naik', 'group': 'B', 'branch': 'Hospet Road', 'paid_days_left': 2, 'upcoming': []},
]}
real = T.leave_app.fetch_status
T.leave_app.fetch_status = lambda timeout=5: STATUS

got = T.get_staff_list()
check('everyone, by branch then group', got['available'] and got['total_working'] == 5
      and [b['branch'] for b in got['branches']] == ['Hospet Road', 'Shivaji Chowk'], got)
hospet = got['branches'][0]
check('Hospet Road: 1 senior, 2 juniors, names with codes', hospet['people'] == 3
      and hospet['groups']['A (seniors)'] == {'count': 1, 'names': ['Dattatraya V Hegde (E006)']}
      and hospet['groups']['B (juniors)']['names'] == ['Akshata (E002)', 'Chaitra g naik (E005)'], hospet)
check('no leave balance or pay in the list', 'paid_days_left' not in str(got), got)

got = T.get_staff_list(branch='shivaji')
check('one branch by part of its name', [b['branch'] for b in got['branches']] == ['Shivaji Chowk'] and got['total_working'] == 2, got)
got = T.get_staff_list(group='a')
check('seniors only, both branches', got['total_working'] == 2
      and all(list(b['groups']) == ['A (seniors)'] for b in got['branches']), got)
got = T.get_staff_list(branch='hospet', group='B')
check('juniors at Hospet Road', got['total_working'] == 2 and got['branches'][0]['groups']['B (juniors)']['count'] == 2, got)

T.leave_app.fetch_status = lambda timeout=5: {**STATUS, 'employees': [{k: v for k, v in e.items() if k != 'branch'} for e in STATUS['employees']]}
got = T.get_staff_list()
check('an older leave app without branches: said plainly, nothing made up', got['available'] is False and 'restart' in got['reason'], got)


def down(timeout=5):
    raise RuntimeError('The leave app is not running at http://127.0.0.1:3010. Start it with its start.bat.')


T.leave_app.fetch_status = down
got = T.get_staff_list()
check('the leave app closed: the reason, no list', got == {'available': False, 'reason': 'The leave app is not running at http://127.0.0.1:3010. Start it with its start.bat.'}, got)
T.leave_app.fetch_status = real

spec = next((t for t in T.TOOLS if t['name'] == 'get_staff_list'), None)
check('offered as a tool, with branch and group', spec is not None and set(spec['parameters']['properties']) == {'branch', 'group'}, spec)

print()
print('%d failed' % len(FAILS) if FAILS else 'ALL PASSED')
sys.exit(1 if FAILS else 0)

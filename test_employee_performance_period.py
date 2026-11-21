"""Staff performance says which months its totals cover (the owner, 21 Nov 2026): Subbii quoted Keerthana's all-history revenue
(1.37 crore, April to November) as if it were November's. Made-up data; the real files are never read.

    venv/Scripts/python.exe test_employee_performance_period.py
"""
import os
import sys
from pathlib import Path

import pandas as pd

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


BILLED = pd.DataFrame([{'Employee': 'Asha', 'Bills': 10, 'Revenue': 5000.0, 'Patients': 8, 'Avg_Bill_Value': 500.0}])
PERF = {'billed_by': BILLED, 'given_by': None, 'self_serve_hourly': None, 'fetch_pairs': None, 'created_by': None}
T.rk.compute_distributor_lines = lambda p: (None, None)
T.rk.build_employee_performance = lambda s, p, lines: PERF


def with_months(months):
    sales = pd.DataFrame({'Calendar_Month': months})
    T._load = lambda: (sales, pd.DataFrame())
    return T.get_employee_performance()


got = with_months(['2026-04', '2026-11', '2026-05', '2026-11'])
check('the totals say the months they cover', got.get('period') == '2026-04 to 2026-11', got)
check('and that they are not one month', 'not one month' in got.get('covers', '') and 'get_employee_monthly' in got['covers'], got)
check('the figures themselves are unchanged', got['billed_by'][0]['Revenue'] == 5000.0, got)

got = with_months(['2026-11'])
check('one month of data is named as that month', got.get('period') == '2026-11', got)

check('the tool description tells the model the same', 'never one month' in T.get_employee_performance.__doc__
      and 'get_employee_monthly' in T.get_employee_performance.__doc__)

print('\nALL PASSED' if not FAILS else '\n%d FAILED' % len(FAILS))
sys.exit(1 if FAILS else 0)

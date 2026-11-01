"""Tests for run_reckoner.fix_date_column - a row keyed month-first (MM/DD/YYYY) in a day-first register is put back
in its real month, and a real date is never moved (found 1 Nov 2026: 94 purchase lines sat in the wrong months).

Run: venv/Scripts/python.exe test_date_swap.py
"""
import sys

import pandas as pd

import run_reckoner as rk

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'   <-- {detail}'))
    if not cond:
        FAILS.append(name)


def dates(values, year, month):
    return rk.fix_date_column(pd.DataFrame({'Date': values}), year, month).dt.strftime('%Y-%m-%d %H:%M').tolist()


july = ['15/07/2026'] * 60
got = dates(['29/06/2026', '07/11/2026', '07/03/2026'] + july, 2026, 7)[:3]
check('a month-first "07/11" in a July file is 11 July - even after a day-first "29/06" (the pandas one-format trap)',
      got == ['2026-06-29 00:00', '2026-07-11 00:00', '2026-07-03 00:00'], got)
got = dates([pd.Timestamp('2026-12-05 10:30')] + [pd.Timestamp('2026-05-20 09:00')] * 60, 2026, 5)[:2]
check('a date Excel stored as a real date is swapped too, keeping its time', got == ['2026-05-12 10:30', '2026-05-20 09:00'], got)
oct_file = ['10/09/2026'] + ['25/09/2026'] * 59 + ['15/10/2026'] * 500 + ['10/06/2026'] * 2
got = dates(oct_file, 2026, 10)
check('a real "10/09" in a file that covers September stays 10 September', got[0] == '2026-09-10 00:00', got[0])
check('a rare "10/06" in an October file becomes 6 October', got[-1] == '2026-10-06 00:00', got[-1])
got = dates(['31/07/2026', '13/08/2026'] + july, 2026, 7)[:2]
check('a day above 12 can never be a swap and stays as it is', got == ['2026-07-31 00:00', '2026-08-13 00:00'], got)
got = dates(['05/08/2026'] + july, 2026, 7)[:1]
check('a stray date whose swap does not land in the file\'s month stays as it is', got == ['2026-08-05 00:00'], got)

print()
print('ALL PASSED' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}')
sys.exit(1 if FAILS else 0)

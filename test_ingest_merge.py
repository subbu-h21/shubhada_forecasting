"""Tests for run_reckoner.ingest - files are merged BILL BY BILL, so a bill is held once however the files overlap.

Each scenario below is one that went wrong with the old "one chosen file per month" rule (13 Nov 2026): a few days uploaded on top of
the month so far was thrown away, the CRM's purchase export (it spans two months) was rejected and a whole month of purchases was
lost, and a sales file that started before the 1st counted 25-31 Oct twice.

Runs the real ingest() on small made-up Excel files in a throwaway folder; the real data is never touched.
Run: venv/Scripts/python.exe test_ingest_merge.py
"""
import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

import run_reckoner as rk

FAILS = []
TMP = Path(tempfile.mkdtemp(prefix='ingest-merge-'))
SALE_COLS = ['Date', 'Inv.No', 'Patient', 'Product', 'MRP', 'Qty', 'Factor', 'Tax Rate', 'Disc Percentage', 'Disc Amount',
             'Saved Amount', 'Item Total', 'Mobile', 'Given by', 'Billed By']


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'   <-- {detail}'))
    if not cond:
        FAILS.append(name)


def fresh(name):
    """A new empty reckoner in its own folder (every path the ingest uses is pointed there)."""
    base = TMP / name
    (base / 'raw').mkdir(parents=True)
    (base / 'processed').mkdir(parents=True)
    rk.RAW_DIR, rk.PROC_DIR = base / 'raw', base / 'processed'
    rk.SALES_MASTER, rk.PURCH_MASTER = rk.PROC_DIR / 'sales_master.csv', rk.PROC_DIR / 'purchase_master.csv'
    rk.MANIFEST_PATH, rk.SALES_NORMALIZED_MARKER = rk.PROC_DIR / 'manifest.json', rk.PROC_DIR / '.sales_normalized'
    rk.BACKUPS_DIR, rk.HOLIDAYS_PATH, rk.CONFIG_PATH = base / 'backups', base / 'holidays.json', base / 'server_config.json'
    rk.SALE_DAY_MIN_ROWS = 4          # tiny days in these tests
    return base


def sales_df(days, bills=3, lines=2, series='2627WS', qty=1, factor=1, first=1):
    rows, n = [], first
    for d in days:
        for _ in range(bills):
            for k in range(lines):
                rows.append({'Date': pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=n), 'Inv.No': f'{series}{n}', 'Patient': f'P{n}',
                             'Product': f'ITEM{k}', 'MRP': 10.0, 'Qty': qty, 'Factor': factor, 'Tax Rate': 5, 'Disc Percentage': 0.0,
                             'Disc Amount': 0.0, 'Saved Amount': 0.0, 'Item Total': 10.0 * qty, 'Mobile': None, 'Given by': 'X', 'Billed By': 'Y'})
            n += 1
    return pd.DataFrame(rows, columns=SALE_COLS)


def purch_df(days, bills=3, lines=2, supplier='SUP A', first=1):
    rows, n = [], first
    for d in days:
        for _ in range(bills):
            for k in range(lines):
                rows.append({'Date': pd.Timestamp(d), 'Inv.No': f'INV{n}', 'Invoice Amount': 100.0, 'Supplier': supplier, 'Product': f'ITEM{k}',
                             'MRP': 10.0, 'Sale Rate': 8.0, 'Qty': 5, 'Factor': 10, 'Tax Rate': 5, 'Ratio': 1, 'Free Qty': 0,
                             'Disc Percentage': 0.0, 'Disc Amount': 0.0, 'Item Total': 40.0, 'Bill Disc': 0.0, 'Entered By': 'Z'})
            n += 1
    return pd.DataFrame(rows)


def days(first, last):
    return [d.strftime('%Y-%m-%d') for d in pd.date_range(first, last)]


def put(name, df, mtime):
    path = rk.RAW_DIR / name
    df.to_excel(path, index=False)
    os.utime(path, (mtime, mtime))
    return path


def run(dry_run=False):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rk.ingest(dry_run=dry_run)
    return buf.getvalue()


def sales():
    return pd.read_csv(rk.SALES_MASTER)


def purchases():
    return pd.read_csv(rk.PURCH_MASTER)


def lines_per_bill(df):
    return set(df.groupby('Inv.No').size().tolist())


T0 = 1_800_000_000

# --- 1. a first file, and the same file again --------------------------------------------------------------------------------------
fresh('first')
ALL = sales_df(days('2026-11-13', '2026-11-16'))
put('a.xlsx', ALL[ALL['Date'] < '2026-11-16'], T0)
log = run()
check('a first file goes in whole', len(sales()) == 18 and 'Merged a.xlsx as SALE' in log and '18 new lines added' in log, log)
check('the manifest records it as merged', rk.load_manifest()['files']['a.xlsx']['status'] == 'merged')
check('nothing new: said so, nothing changes', 'No new files' in run() and len(sales()) == 18)

# --- 2. a top-up of a few days on top of the month so far (was: skipped as a "narrower duplicate month") --------------------------------
put('b.xlsx', ALL[ALL['Date'] >= '2026-11-15'], T0 + 10)             # 15th (already stored) and 16th (new): 2 days against 3
log = run()
s = sales()
check('a narrower top-up file is MERGED, not skipped: the new day is in', len(s) == 24 and (s['Date'].astype(str).str[:10] == '2026-11-16').sum() == 6, log)
check('...and the day it shares with the stored data is not doubled', lines_per_bill(s) == {2} and 'already there' in log, log)

# --- 3. the same lines in another file change nothing ------------------------------------------------------------------------------
before = sales().sort_values(list(SALE_COLS[:3])).reset_index(drop=True)
put('copy of everything.xlsx', ALL, T0 + 20)
log = run()
after = sales().sort_values(list(SALE_COLS[:3])).reset_index(drop=True)
check('a file holding only lines already stored adds nothing', '0 new lines added' in log and len(after) == 24, log)
check('...and every stored value is exactly as it was', before[SALE_COLS].astype(str).equals(after[SALE_COLS].astype(str)))

# --- 4. a bill edited after the first export: the newer upload's copy replaces the old one -----------------------------------------
fresh('edit')
put('old.xlsx', ALL, T0)
run()
total_before = float(sales()['Item Total'].sum())
edited = ALL.copy()
edited.loc[edited['Inv.No'] == '2627WS2', ['Qty', 'Item Total']] = [3, 30.0]      # bill 2: 2 lines, 10 -> 30 each
put('newer.xlsx', edited[edited['Date'] >= '2026-11-13'], T0 + 10)
log = run()
s = sales()
check('an edited bill replaces its old copy (no double count)', len(s) == 24 and lines_per_bill(s) == {2}
      and abs(float(s['Item Total'].sum()) - (total_before + 40)) < 1e-6, f'{len(s)} rows, {float(s["Item Total"].sum())} vs {total_before + 40}')
check('...and the message says so', '2 lines of corrected bills replaced 2 old ones' in log, log)

# --- 5. a stored bill the file no longer lists: removed only when the file clearly carries the whole day ---------------------------------
fresh('gone')
DAY = sales_df(['2026-11-13'], bills=10)                              # 20 lines
put('full.xlsx', DAY, T0)
run()
put('without_bill_10.xlsx', DAY[DAY['Inv.No'] != '2627WS10'], T0 + 10)     # 18 lines = 90% of the day
log = run()
check('a bill deleted in the CRM is removed when the file carries the whole day', len(sales()) == 18 and '2627WS10' not in set(sales()['Inv.No'])
      and '2 lines of bills no longer in the export removed' in log, log)
fresh('part')
put('full.xlsx', DAY, T0)
run()
put('half_a_day.xlsx', DAY[DAY['Inv.No'].isin([f'2627WS{i}' for i in range(1, 6)])], T0 + 10)      # 10 lines of 20
log = run()
check('a file holding only PART of a day removes nothing', len(sales()) == 20 and 'part of that day' in log, log)

# --- 6. a stray misdated line does not wipe the day it names ------------------------------------------------------------------------
fresh('stray')
put('full.xlsx', DAY, T0)
run()
stray = sales_df(['2026-11-13'], bills=1, lines=1, series='2627WS99')                      # one lone line dated 13 Nov
later = sales_df(['2026-11-14'], bills=4, first=100)
put('with_a_stray.xlsx', pd.concat([stray, later], ignore_index=True), T0 + 10)
run()
check('one stray line on a stored day leaves that day alone', len(sales()) == 20 + 1 + 8, f'{len(sales())} rows')

# --- 7. the purchase export that spans two months (was: filed under the older month and REJECTED) ------------------------------------
fresh('purchases')
OCT = purch_df(days('2026-10-20', '2026-10-31'))                                         # 72 lines, 12 days
put('october.xlsx', OCT, T0)
run()
spanning = pd.concat([OCT[OCT['Date'] >= '2026-10-28'], purch_df(days('2026-11-13', '2026-11-15'), bills=1, first=500)], ignore_index=True)
put('spanning_export.xlsx', spanning, T0 + 10)                                           # 24 October lines + 6 November: dominant month Oct
log = run()
p = purchases()
check('an export running across two months is merged, not rejected', len(p) == 72 + 6 and (p['Date'].astype(str).str[:7] == '2026-11').sum() == 6, log)
check('...with its October lines recognised as already there', '24 already there' in log and '6 new lines added' in log, log)
check('...and the master keeps its midnight date text', p['Date'].astype(str).str.endswith('00:00:00').all(), p['Date'].head(2).tolist())
two =pd.concat([purch_df(['2026-11-16'], bills=1, supplier='SUP A', first=900), purch_df(['2026-11-16'], bills=1, supplier='SUP B', first=900)], ignore_index=True)
put('two_suppliers.xlsx', two, T0 + 20)
run()
p = purchases()
check('...both are stored', len(p) == 72 + 6 + 4 and p[p['Inv.No'] == 'INV900']['Supplier'].nunique() == 2, f'{len(p)} rows')
put('two_suppliers_again.xlsx', two, T0 + 30)
check('...and putting them in again changes nothing', '0 new lines added' in run() and len(purchases()) == 72 + 6 + 4)

# --- 8. a sales file that starts before the 1st (was: 25-31 Oct counted twice) -----------------------------------------------------
fresh('double')
ALL2 = sales_df(days('2026-10-25', '2026-10-31'), bills=1)
BIG = pd.concat([ALL2, sales_df(days('2026-11-01', '2026-11-05'), bills=3, series='2627WH')], ignore_index=True)
put('october.xlsx', ALL2, T0)
run()
oct_total = float(sales()['Item Total'].sum())
put('november_from_25_oct.xlsx', BIG, T0 + 10)
run()
s = sales()
check('a November file that starts on 25 Oct does not count those days twice', lines_per_bill(s) == {2} and len(s) == len(BIG), f'{len(s)} vs {len(BIG)}')
check('...October is still the same money', abs(float(s[s['Date'].astype(str).str[:7] == '2026-10']['Item Total'].sum()) - oct_total) < 1e-6)

# --- 9. lines with no invoice number are not lost or doubled -------------------------------------------------------------------------
fresh('keyless')
k = sales_df(['2026-11-13'], bills=1, lines=3)
k['Inv.No'] = None
k.loc[2, 'Product'] = 'OTHER'                                                          # two identical lines and one different
put('k1.xlsx', k, T0)
run()
k2 = pd.concat([k, k.iloc[[2]].assign(Product='THIRD')], ignore_index=True)
put('k2.xlsx', k2, T0 + 10)
run()
check('lines without an invoice number are neither dropped nor doubled when a file overlaps them', len(sales()) == 4, f'{len(sales())} rows')

# --- 10. wholesale bills are converted to units once, however often they are merged ----------------------------------------------------
fresh('wb')
wb = sales_df(['2026-11-13'], bills=2, lines=1, series='2627WB', qty=2, factor=10)
put('wb1.xlsx', wb, T0)
run()
put('wb2.xlsx', pd.concat([wb, sales_df(['2026-11-14'], bills=1, lines=1, series='2627WB', qty=2, factor=10).assign(**{'Inv.No': '2627WB77'})], ignore_index=True), T0 + 10)
log = run()
s = sales()
check('a wholesale bill met again is the same bill (not converted a second time)', len(s) == 3 and set(s[s['Inv.No'].isin(['2627WB1', '2627WB2'])]['Qty']) == {20.0}
      and '2 already there' in log, f'{s[["Inv.No", "Qty"]].values.tolist()} | {log}')

# --- 11. two files in one batch holding the same bill: the NEWER file wins, whatever the names sort like -----------------------------------
fresh('order')
a, b = sales_df(['2026-11-13'], bills=1, lines=1), sales_df(['2026-11-13'], bills=1, lines=1)
b['Item Total'], b['Qty'] = 99.0, 9
put('a_name_sorts_first_but_is_newer.xlsx', b, T0 + 100)
put('z_name_sorts_last_but_is_older.xlsx', a, T0)
run()
check('in one batch the newer upload wins', float(sales()['Item Total'].iloc[0]) == 99.0 and len(sales()) == 1, sales()['Item Total'].tolist())

# --- 12. a dry run writes nothing --------------------------------------------------------------------------------------------------
fresh('dry')
put('a.xlsx', ALL, T0)
log = run(dry_run=True)
check('a dry run says what it would do and writes nothing', 'Would merge a.xlsx' in log and not rk.SALES_MASTER.exists() and not rk.MANIFEST_PATH.exists()
      and not rk.BACKUPS_DIR.exists(), log)

# --- 13. a backup is taken BEFORE anything is written ------------------------------------------------------------------------------
fresh('backup')
put('a.xlsx', ALL, T0)
run()
put('b.xlsx', sales_df(['2026-11-20'], first=300), T0 + 10)
run()
backups = sorted(p for p in rk.BACKUPS_DIR.iterdir())
newest = pd.read_csv(backups[-1] / 'processed' / 'sales_master.csv')
check('the newest backup holds the data as it was before the last upload', len(newest) == 24 and len(sales()) == 30, f'backup {len(newest)} rows, now {len(sales())}')

# --- 14. the bill key: 1234, 1234.0 and '1234.0' are one bill ----------------------------------------------------------------------
k = rk._bill_keys(pd.DataFrame({'Inv.No': [1234, 1234.0, '1234.0', ' 1234 ', None], 'Supplier': ['S'] * 5}), 'purchase')
check('an invoice number read as 1234, 1234.0 or "1234.0" is the same bill; a blank one is a line of its own',
      len(set(k.iloc[:4])) == 1 and k.iloc[4].startswith('LINE'), k.tolist())

print()
print('ALL PASSED' if not FAILS else '%d FAILED: %s' % (len(FAILS), '; '.join(FAILS)))
sys.exit(1 if FAILS else 0)

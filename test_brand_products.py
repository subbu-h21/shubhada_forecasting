"""Offline test for get_brand_products (the shop's products by brand word, for matching a CDSCO failed-batch alert).

Made-up purchases, sales and product list - the real data files are not read.

    venv/Scripts/python.exe test_brand_products.py
"""
import sys
import tempfile
from pathlib import Path

import pandas as pd

import ask_tools as T

sys.stdout.reconfigure(encoding='utf-8')
fails = []


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  ' + str(extra)[:400]))
    if not cond:
        fails.append(name)


COLS = ['PRODUCT', 'MANUFACTURER', 'KIND', 'CATEGORY', 'SCHEDULE H1', 'H1 DRUG', 'COMPOSITION', 'SALTS (normalised)', 'DOSAGE FORM',
        'THERAPEUTIC CLASS', 'REVIEW', 'CONFIDENCE']
ROWS = [('THYROX 100MG TIN', 'MACLEODS', 'medicine', 'Thyroid', '', '', 'Levothyroxine sodium 100 mcg', 'levothyroxine', 'tablet', '', '', '0.9'),
        ('THYROX 25 TIN', 'MACLEODS', 'medicine', 'Thyroid', '', '', 'Levothyroxine sodium 25 mcg', 'levothyroxine', 'tablet', '', '', '0.9')]
tmp = Path(tempfile.mkdtemp())
T.ENRICHED_XLSX = tmp / 'none.xlsx'
T.ENRICHED_CSV = tmp / 'enriched.csv'
pd.DataFrame(ROWS, columns=COLS).to_csv(T.ENRICHED_CSV, index=False)
T._enriched_cache.clear()

purch = pd.DataFrame([
    {'Date': '2026-11-06 00:00:00', 'Inv.No': 'P1', 'Supplier': 'SAROJ PHARMA', 'Product': 'THYROX 100MG TIN', 'Qty': 5, 'Free Qty': 1, 'Item Total': 900},
    {'Date': '2026-10-01 00:00:00', 'Inv.No': 'P2', 'Supplier': 'A.K.PHARMA', 'Product': 'THYROX 100MG TIN', 'Qty': 2, 'Free Qty': 0, 'Item Total': 300},
    {'Date': '2025-01-01 00:00:00', 'Inv.No': 'P0', 'Supplier': 'OLD AGENCY', 'Product': 'THYROX 25 TIN', 'Qty': 9, 'Free Qty': 0, 'Item Total': 100},
    {'Date': '2026-11-01 00:00:00', 'Inv.No': 'P3', 'Supplier': 'X', 'Product': 'DOLO 650 TAB', 'Qty': 9, 'Free Qty': 0, 'Item Total': 100},
])
sales = pd.DataFrame([
    {'Date': '2026-11-12 10:00:00', 'Inv.No': 'S1', 'Patient': 'A Person', 'Mobile': '9999999999', 'Product': 'THYROX 100MG TIN', 'Item Total': 50},
    {'Date': '2026-11-10 10:00:00', 'Inv.No': 'S2', 'Patient': 'B Person', 'Mobile': '8888888888', 'Product': 'THYROX 25 TIN', 'Item Total': 50},
])
T._load = lambda: (sales, purch)
T._cache.pop('brand_index', None)

r = T.run_tool('get_brand_products', {'words': ['Thyrox', 'dolo', 'xx', 'nothing']})
p = r.get('products', {})
th = {x['product']: x for x in p.get('thyrox', [])}
check('the brand word finds our products', set(th) == {'THYROX 100MG TIN', 'THYROX 25 TIN'}, r)
check('bought: times, quantity with free, the latest date and supplier first', th['THYROX 100MG TIN']['bought_times'] == 2
      and th['THYROX 100MG TIN']['bought_qty'] == 8 and th['THYROX 100MG TIN']['last_bought'] == '2026-11-06'
      and th['THYROX 100MG TIN']['suppliers'] == ['SAROJ PHARMA', 'A.K.PHARMA'], th['THYROX 100MG TIN'])
check('a purchase older than 12 months does not count; a sale in 90 days does', th['THYROX 25 TIN']['bought_times'] == 0
      and th['THYROX 25 TIN']['sold_lines_90d'] == 1, th['THYROX 25 TIN'])
check('company and salts from the product list', th['THYROX 100MG TIN']['manufacturer'] == 'MACLEODS' and th['THYROX 100MG TIN']['salts'] == ['levothyroxine'])
check('a product the list does not know still comes, without salts', p.get('dolo') and p['dolo'][0]['salts'] == [])
check('a word shorter than 3 letters is skipped; an unknown word gives none', 'xx' not in p and p.get('nothing') == [])
text = str(r)
check('no patient data and no rupee figures', '9999999999' not in text and 'Person' not in text and 'Item Total' not in text and '900' not in text, text)

print()
print('ALL PASSED' if not fails else '%d FAILED: %s' % (len(fails), ', '.join(fails)))
sys.exit(1 if fails else 0)

"""find_alternatives and get_category_sales (ask_tools): substitutes for a product and sales by category, from the ENRICHED
product master (composition with strengths, dosage form, therapeutic class, category). Offline: a made-up product list
and made-up sales.

    venv/Scripts/python.exe test_alternatives.py
"""
import sys
import tempfile
from pathlib import Path

import pandas as pd

import ask_tools as T

sys.stdout.reconfigure(encoding='utf-8')
FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else '  <-- %s' % (detail,)))
    if not ok:
        FAILS.append(name)


COLS = ['PRODUCT', 'MANUFACTURER', 'KIND', 'CATEGORY', 'SCHEDULE H1', 'H1 DRUG', 'COMPOSITION', 'SALTS (normalised)',
        'DOSAGE FORM', 'THERAPEUTIC CLASS', 'INDICATIONS', 'CONFIDENCE']
ROWS = [
    ('GLYCOMET 500MG TAB', 'USV', 'medicine', 'Diabetes', '', '', 'Metformin 500 mg', 'metformin', 'tablet', 'Biguanide antidiabetic', 'type 2 diabetes', '0.98'),
    ('GLYCOMET 500MG SR TAB', 'USV', 'medicine', 'Diabetes', '', '', 'Metformin 500 mg', 'metformin', 'tablet', 'Biguanide antidiabetic', 'type 2 diabetes', '0.98'),
    ('GLYCIPHAGE 500MG TAB', 'FRANCO', 'medicine', 'Diabetes', '', '', 'Metformin hydrochloride 500 mg', 'metformin', 'tablet', 'Biguanide antidiabetic', '', '0.97'),
    ('OKAMET 500MG TAB', 'CIPLA', 'medicine', 'Diabetes', '', '', 'Metformin 500 mg', 'metformin', 'tablet', 'Biguanide antidiabetic', '', '0.97'),
    ('GLYCIPHAGE SR 500 TAB', 'FRANCO', 'medicine', 'Diabetes', '', '', 'Metformin hydrochloride 500 mg', 'metformin', 'tablet', 'Biguanide antidiabetic', '', '0.97'),
    ('GLYCOMET 850MG TAB', 'USV', 'medicine', 'Diabetes', '', '', 'Metformin 850 mg', 'metformin', 'tablet', 'Biguanide antidiabetic', '', '0.98'),
    ('METFORMIN SYRUP', 'ACME', 'medicine', 'Diabetes', '', '', 'Metformin 500 mg/5 ml', 'metformin', 'syrup', 'Biguanide antidiabetic', '', '0.9'),
    ('GLIMY 1 TAB', 'ACME', 'medicine', 'Diabetes', '', '', 'Glimepiride 1 mg', 'glimepiride', 'tablet', 'Sulfonylurea antidiabetic', '', '0.9'),
    ('JANUVIA 50 TAB', 'MSD', 'medicine', 'Diabetes', '', '', 'Sitagliptin 50 mg', 'sitagliptin', 'tablet', 'Biguanide antidiabetic', '', '0.4'),
    ('MIXTARD 30 FLEX PEN', 'NOVO', 'medicine', 'Diabetes', '', '', 'Human insulin (30% soluble + 70% isophane) 100 IU/ml', 'human insulin soluble', 'injection', 'Premixed insulin', '', '0.93'),
    ('XSULIN 30/70 VAIL', 'ERIS', 'medicine', 'Diabetes', '', '', 'Human insulin (30% soluble / 70% isophane) 100 IU/mL', 'human insulin soluble', 'injection', 'Premixed insulin', '', '0.9'),
    ('INSUGEN 50/50 100 IU', 'BIOCON', 'medicine', 'Diabetes', '', '', 'Insulin human 100 IU/mL', 'human insulin soluble', 'injection', 'Premixed insulin', '', '0.9'),
    ('INSUGEN R INJ', 'BIOCON', 'medicine', 'Diabetes', '', '', 'Human insulin (soluble) 100 IU/mL', 'human insulin soluble', 'injection', 'Short-acting insulin', '', '0.95'),
    ('ALGESIA CR TAB', 'MACLEODS', 'medicine', 'Fever & Pain', T.H1_IN_FORCE, 'Tramadol', 'Tramadol 100 mg', 'tramadol', 'tablet', 'Opioid analgesic', '', '0.9'),
    ('ULTRACET TAB', 'J&J', 'medicine', 'Fever & Pain', '', '', 'Tramadol 37.5 mg; Paracetamol 325 mg', 'paracetamol; tramadol', 'tablet', 'Opioid analgesic', '', '0.9'),
    ('XxxxxxxGLYCOMET OLD', 'USV', 'medicine', 'Diabetes', '', '', 'Metformin 500 mg', 'metformin', 'tablet', 'Biguanide antidiabetic', '', '0.9'),
    ('DOLO 650 TAB', 'MICRO', 'medicine', 'Fever & Pain', '', '', 'Paracetamol 650 mg', 'paracetamol', 'tablet', 'Analgesic', '', '0.99'),
]
tmp = Path(tempfile.mkdtemp())
T.ENRICHED_XLSX = tmp / 'none.xlsx'                      # no xlsx: the CSV is used as it is
T.ENRICHED_CSV = tmp / 'enriched.csv'
pd.DataFrame(ROWS, columns=COLS).to_csv(T.ENRICHED_CSV, index=False)
T._enriched_cache.clear()
T._latest_mrp = lambda: {'GLYCIPHAGE 500MG TAB': (39.95, 10.0, '2026-10-20', 'purchase'), 'OKAMET 500MG TAB ': (26.49, 10.0, '2026-10-01', 'purchase')}
T._recent_bills = lambda: {'OKAMET 500MG TAB': 50, 'GLYCIPHAGE 500MG TAB': 5, 'GLYCOMET 500MG SR TAB': 900}


def alt(q, **kw):
    return T.run_tool('find_alternatives', dict({'product': q}, **kw))


# --- strengths and release ------------------------------------------------------------------------------------------
check('strength: mg read from the composition', T._strength_sig('Metformin 500 mg') == ('500mg',))
check('strength: an insulin mix written with % or with / is the same mix', T._strength_sig('Human insulin (30% soluble + 70% isophane) 100 IU/ml')
      == T._strength_sig('Human insulin (30% soluble / 70% isophane) 100 IU/mL', 'XSULIN 30/70'), T._strength_sig('Human insulin (30% soluble + 70% isophane) 100 IU/ml'))
check('strength: a 50/50 in the NAME counts when the composition leaves it out', 'r50/50' in T._strength_sig('Insulin human 100 IU/mL', 'INSUGEN 50/50 100 IU'))
check('strength: a different mix is "different", never "same"', T._same_strength(('100iu',), ('100iu', 'r50/50')) == 'different'
      and T._same_strength(('100iu', '30%', '70%', 'r30/70'), ('100iu',)) == 'different')
check('release: SR / XR / sustained are read; an ordinary tablet is ""', T._release('GLYCOMET 500MG SR TAB') == 'SR' and T._release('X', 'extended release') == 'ER'
      and T._release('GLYCOMET 500MG TAB') == '' and T._release('CROCIN 650 FAST RELEASE') == '')

# --- find_alternatives ----------------------------------------------------------------------------------------------
r = alt('Glycomet 500')
check('the plain tablet is picked for "Glycomet 500" (not the best-selling SR one)', r['product'] == 'GLYCOMET 500MG TAB', r.get('product'))
names = [x['product'] for x in r['same_salts']]
check('same salts, same strength, same form first; the shop\'s better seller first among them', names[:2] == ['OKAMET 500MG TAB', 'GLYCIPHAGE 500MG TAB'], names)
sr = next(x for x in r['same_salts'] if x['product'] == 'GLYCIPHAGE SR 500 TAB')
check('an SR tablet is NOT a direct substitute for a plain one', sr['same_form'] is False and sr['release'] == 'SR', sr)
check('a different strength is marked different', next(x for x in r['same_salts'] if x['product'] == 'GLYCOMET 850MG TAB')['strength'] == 'different')
check('another form (syrup) is marked', next(x for x in r['same_salts'] if x['product'] == 'METFORMIN SYRUP')['same_form'] is False)
check('direct substitutes are counted (same salts + strength + form + release)', r['direct_substitutes'] == 2, r['direct_substitutes'])
check('MRP per strip from the latest purchase (names matched without spaces / case)', next(x for x in r['same_salts'] if x['product'] == 'OKAMET 500MG TAB')['mrp_per_strip'] == 26.49)
check('a struck-out name ("Xxxxxxx...") is never offered', not any('XXX' in n.upper() for n in names), names)
check('no sales figures in the answer, only "sold here recently"', 'bills' not in str(r) and r['same_salts'][0]['sold_here_recently'] is True)
check('the note says a different strength / form / release is not a substitute, and only a doctor switches medicines',
      'NOT a direct substitute' in r['note'] and 'only the doctor' in r['note'])
r = alt('Glycomet 500 SR')
check('"Glycomet 500 SR" picks the SR tablet, and other SR brands are its direct substitutes', r['product'] == 'GLYCOMET 500MG SR TAB'
      and r['same_salts'][0]['product'] == 'GLYCIPHAGE SR 500 TAB' and r['direct_substitutes'] == 1, (r['product'], r['same_salts'][:1]))
r = alt('Mixtard 30')
st = {x['product']: x['strength'] for x in r['same_salts']}
check('insulin: Mixtard 30 = another 30/70; plain (R) and 50/50 insulin are DIFFERENT', st.get('XSULIN 30/70 VAIL') == 'same'
      and st.get('INSUGEN R INJ') == 'different' and st.get('INSUGEN 50/50 100 IU') == 'different', st)
check('insulin: every one says to confirm the type on the pack', all('insulin' in x.get('please_check', '') for x in r['same_salts']))
r = alt('Glimy 1', same_use=True)
check('no same-salt brand: other medicines for the same use are listed, and none has the same salts',
      r['same_salts'] == [] and r.get('same_use_different_medicine') is not None, r)
r = alt('Glycomet 500', same_use=True)
use = [x['product'] for x in r.get('same_use_different_medicine', [])]
check('same_use: a different salt with the same class and form', use == ['JANUVIA 50 TAB'], use)
check('...and a doubtful composition is marked please_check', 'not sure' in r['same_use_different_medicine'][0].get('please_check', ''))
r = alt('Algesia')
check('an H1 drug carries the H1 house rule', r['schedule_h1'] is True and 'h1_policy' in r)
check('a combination is not a substitute for a single salt', all(x['product'] != 'ULTRACET TAB' for x in r['same_salts']))
r = alt('Glykomet 500mg tab')
check('a misspelt name: not found, with names it may have meant', r['found'] is False and 'GLYCOMET 500MG TAB' in r['did_you_mean'], r)

# --- match_products: what a prescription says, matched against the product list ------------------------------------
def match(*items):
    return T.run_tool('match_products', {'items': list(items)})['results']


m = match({'name': 'Glycomt 500', 'form': 'tablet'}, {'name': 'Glycomet', 'form': 'tablet'}, {'name': 'Glycomet 500 SR', 'form': 'tablet'},
          {'name': 'Xyzzol 10'}, {'name': 'Mixtard 30', 'form': 'injection'}, {'name': 'Insugen 50/50', 'form': 'injection'}, {'name': '?'},
          {'name': 'Dolo 650', 'others': ['Dolo 500', 'Glycomet 850']})
check('a spelling slip still finds the real product, with its composition', m[0]['status'] == 'matched' and m[0]['product'] == 'GLYCOMET 500MG TAB'
      and m[0]['composition'] == 'Metformin 500 mg', m[0])
check('a name with no strength that fits several strengths: "choose", with the options', m[1]['status'] == 'choose'
      and {'GLYCOMET 500MG TAB', 'GLYCOMET 850MG TAB'} <= {c['product'] for c in m[1]['candidates']}, m[1])
check('SR in the prescription: the SR product', m[2]['status'] == 'matched' and m[2]['product'] == 'GLYCOMET 500MG SR TAB', m[2])
check('no real product near the name: not_found, with the nearest names', m[3]['status'] == 'not_found' and 'product' not in m[3], m[3])
check('insulin: Mixtard 30 is the 30/70 pen; a 50/50 is matched by its ratio', m[4]['product'] == 'MIXTARD 30 FLEX PEN' and m[4]['status'] == 'matched'
      and m[5]['product'] == 'INSUGEN 50/50 100 IU' and m[5]['status'] == 'matched', (m[4], m[5]))
check('an unreadable line stays unreadable', m[6]['status'] == 'unreadable')
check('other readings get their own product when it is a different one', [a['product'] for a in m[7]['other_readings']] == ['GLYCOMET 850MG TAB'], m[7]['other_readings'])
check('no sales figures in a match', 'bills' not in str(m))

# --- get_category_sales ---------------------------------------------------------------------------------------------
sales = pd.DataFrame([
    # Product, Item Total, Inv.No, Qty, Calendar_Month, Date
    ('GLYCOMET 500MG TAB', 1000.0, 'WS-1', 10, '2026-09', '2026-09-05'),
    ('DOLO 650 TAB', 300.0, 'WS-1', 15, '2026-09', '2026-09-05'),
    ('GLYCOMET 500MG TAB', 2000.0, 'WS-2', 20, '2026-10', '2026-10-03'),
    ('OKAMET 500MG TAB', 500.0, 'WH-3', 10, '2026-10', '2026-10-25'),
    ('DOLO 650 TAB', 600.0, 'WS-4', 30, '2026-10', '2026-10-04'),
    ('GLYCOMET 500MG TAB', 1500.0, 'WS-5', 15, '2026-11', '2026-11-04'),
    ('UNKNOWN THING', 100.0, 'WS-5', 1, '2026-11', '2026-11-04'),
], columns=['Product', 'Item Total', 'Inv.No', 'Qty', 'Calendar_Month', 'Date'])
T._load = lambda: (sales, pd.DataFrame())
T._factor_map = lambda: {'GLYCOMET 500MG TAB': 10, 'DOLO 650 TAB': 15, 'OKAMET 500MG TAB': 10}
T.rk.extract_branch = lambda inv: {'WS': 'Shivaji Chowk', 'WH': 'Hospet Road'}.get(str(inv)[:2], 'Unknown branch')
r = T.run_tool('get_category_sales', {})
rows = {x['category']: x for x in r['rows']}
check('category sales: the latest month by default, value with GST, strips and bills', r['months'] == ['2026-11'] and rows['Diabetes']['sales_value'] == 1500.0
      and rows['Diabetes']['strips'] == 1.5 and rows['Diabetes']['bills'] == 1, r)
check('a product missing from the list is grouped as such', 'Not in the product list' in rows)
check('the running month is compared with the SAME days of the month before (Oct 1-4, not all October)',
      rows['Diabetes']['previous_month_value'] == 2000.0 and 'days 1-4' in r['previous_month'], (rows['Diabetes'], r.get('previous_month')))
r = T.run_tool('get_category_sales', {'month': '2026-10'})
rows = {x['category']: x for x in r['rows']}
check('a finished month is compared with the whole month before', rows['Diabetes']['sales_value'] == 2500.0 and rows['Diabetes']['previous_month_value'] == 1000.0
      and rows['Diabetes']['change_pct'] == 150.0 and r['previous_month'] == '2026-09', rows)
check('shares add up to 100', abs(sum(x['share_pct'] for x in r['rows']) - 100) < 0.1)
r = T.run_tool('get_category_sales', {'month': 'all', 'branch': 'hospet'})
check('a branch filter and all months', r['total_sales_value'] == 500.0 and r['rows'][0]['category'] == 'Diabetes', r)
r = T.run_tool('get_category_sales', {'category': 'diab', 'month': '2026-10'})
check('one category: month by month and its top products', r['groups'] == ['Diabetes'] and [m['month'] for m in r['month_by_month']] == ['2026-09', '2026-10', '2026-11']
      and r['top_products'][0]['Product'] == 'GLYCOMET 500MG TAB' and r['share_of_all_sales_pct'] == round(2500 / 3100 * 100, 1), r)
r = T.run_tool('get_category_sales', {'by': 'therapeutic_class', 'month': '2026-10'})
check('by therapeutic class', r['by'] == 'therapeutic_class' and r['rows'][0]['therapeutic_class'] == 'Biguanide antidiabetic', r['rows'][:1])
check('an unknown category: the list of groups', 'groups' in T.run_tool('get_category_sales', {'category': 'zzz'}))
check('a month with no data says so', 'error' in T.run_tool('get_category_sales', {'month': '2025-01'}))
check('both tools are registered for the Ask page and the shop assistant', {'find_alternatives', 'get_category_sales'} <= set(T.TOOL_FNS))

print()
print('ALL PASSED' if not FAILS else '%d FAILED: %s' % (len(FAILS), '; '.join(FAILS)))
sys.exit(1 if FAILS else 0)

"""find_products (ask_tools): a doctor's question by brand, salt or combination of salts, from the product list with its active ingredients."""
import sys

import pandas as pd

import ask_tools as T

FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else '  <-- %s' % (detail,)))
    if not ok:
        FAILS.append(name)


ROWS = [
    ('DOLO 650 TAB', 'MICRO LABS', 'Paracetamol', '', '', ''),
    ('DOLOPAR 650MG TAB', 'MICRO LABS', 'Paracetamol', '', '', ''),
    ('CALPOL 500 TAB', 'GSK', 'Paracetamol', '', '', ''),
    ('PARAFEN TAB', 'ACME', 'Paracetamol, Ibuprofen', '', '', ''),
    ('AUGMENTIN 625 TAB', 'GSK', 'Amoxicillin, Clavulanic Acid', '', '', ''),
    ('MOXCLAV 625 TAB', 'ACME', 'Clavulanic Acid, Amoxicillin', '', '', ''),
    ('MOXCLAV DUO TAB', 'ACME', 'Amoxicillin, Clavulanic Acid, Lactobacillus', '', '', ''),
    ('AMLONG TL TAB', 'MICRO LABS', 'Amlodipine, Telmisartan', '', '', ''),
    ('ALGESIA CR TAB', 'MACLEODS', 'Tramadol', T.H1_IN_FORCE, 'Tramadol', ''),
    ('SOME CREAM', 'ACME', '', '', '', ''),
]
pm = pd.DataFrame(ROWS, columns=['PRODUCT', 'MANUFACTURER', 'ACTIVE INGREDIENTS', 'SCHEDULE H1', 'H1 DRUG', 'H1 CHECK'])
pm['_key'] = pm['PRODUCT'].str.upper()
pm = pm.set_index('_key', drop=False)
T._product_master = lambda: pm
T._latest_mrp = lambda: {'DOLO 650 TAB': (32.28, 15.0, '2026-10-20', 'purchase'), 'AMLONG TL TAB': (263.0, 10.0, '2026-10-01', 'purchase'),
                         'ALGESIA CR TAB': (137.16, 10.0, '2026-10-02', 'purchase')}


def find(q, **kw):
    return T.run_tool('find_products', dict({'query': q}, **kw))


check('salts are understood from a combination, with strengths and forms dropped', [T._salt_tokens(q) for q in (
    'Amoxicillin 500 + clavulanic acid 125', 'telmisartan and amlodipine', 'paracetamol/ibuprofen tablets', 'Paracetamol 650 mg', 'amoxicillin, clavulanic acid')]
    == [['amoxicillin', 'clavulanic acid'], ['telmisartan', 'amlodipine'], ['paracetamol', 'ibuprofen'], ['paracetamol'], ['amoxicillin', 'clavulanic acid']])
r = find('Dolo 650')
check('a brand name finds the product, with its salt, MRP per strip and strip size', [p['product'] for p in r['products']][:2] == ['DOLO 650 TAB', 'DOLOPAR 650MG TAB'][:1] + ['DOLOPAR 650MG TAB']
      and r['products'][0]['active_ingredients'] == 'Paracetamol' and r['products'][0]['mrp_per_strip'] == 32.28 and r['products'][0]['strip_size'] == 15.0, r)
r = find('paracetamol')
check('a salt finds every product that contains it, a single-salt product before a combination', [p['product'] for p in r['products']][:4] == ['CALPOL 500 TAB', 'DOLO 650 TAB', 'DOLOPAR 650MG TAB', 'PARAFEN TAB'] and r['products'][-1]['exact_combination'] is False
      and r['products'][-1]['also_contains'] == 'Ibuprofen', [(p['product'], p.get('exact_combination')) for p in r['products']])
r = find('amoxicillin + clavulanic acid')
names = [p['product'] for p in r['products']]
check('a combination: the exact pair first (either order), the one with an extra salt after it and flagged', names == ['AUGMENTIN 625 TAB', 'MOXCLAV 625 TAB', 'MOXCLAV DUO TAB']
      and [p['exact_combination'] for p in r['products']] == [True, True, False] and r['products'][2]['also_contains'] == 'Lactobacillus', r)
check('the same pair typed the other way round finds the same products', [p['product'] for p in find('clavulanic acid and amoxicillin')['products']] == names)
r = find('tramadol')
check('Schedule H1 is flagged, with the drug and the house rule', r['products'][0]['schedule_h1'] is True and r['products'][0]['h1_drug'] == 'Tramadol' and 'no exceptions' in r['h1_policy'], r)
check('no H1 product: no policy text', 'h1_policy' not in find('paracetamol'))
check('a product with no MRP on record has none, not zero', find('calpol')['products'][0]['mrp_per_strip'] is None)
check('nothing matches: an empty list, no crash', find('zzzz qqqq')['products'] == [])
check('an empty question is asked back', 'Which' in find('  ')['error'])
check('no sales, cost, stock or patient field in a result', not any(w in str(find('paracetamol')).lower() for w in ('sold', 'revenue', 'cogs', 'stock', 'patient', 'margin')))
check('the tool is registered with a description and a query parameter', any(t['name'] == 'find_products' and t['parameters']['required'] == ['query'] and t['description'] for t in T.TOOLS))

print()
print('ALL PASSED' if not FAILS else '%d FAILED: %s' % (len(FAILS), '; '.join(FAILS)))
sys.exit(1 if FAILS else 0)

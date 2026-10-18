"""Checks for get_product_sales_history (the sale-by-sale view of one product).
Run: venv\\Scripts\\python.exe test_product_sales_history.py"""
import sys

sys.stdout.reconfigure(encoding='utf-8')
import ask_tools as T  # noqa: E402

fails = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'  -> {detail}'))
    if not ok:
        fails.append(name)


NAME = 'GLYCOMET GP 2 TAB'
r = T.run_tool('get_product_sales_history', {'product': NAME, 'limit': 5})
check('exact product found, no error', r.get('found') is True and 'error' not in r, r)
check('returns the requested lines newest first',
      r['shown'] == 5 and [x['Date'] for x in r['sales']] == sorted([x['Date'] for x in r['sales']], reverse=True), r['sales'][:2])
line = r['sales'][0]
check('each line has date, branch, qty, given by, billed by',
      all(k in line for k in ('Date', 'Time', 'Branch', 'Qty_units', 'Strips', 'Given by', 'Billed By')), line)
check('staff names are real (not blank)', all(x['Given by'] and x['Billed By'] for x in r['sales']), r['sales'])
check('NO patient name or mobile anywhere in the output',
      not ({'Patient', 'Mobile', 'Mobile Number', 'Phone'} & set(str(r).replace("'", ' ').replace(':', ' ').split())) and 'Patient' not in line and 'Mobile' not in line, line)

# the guard still blocks patient fields for this tool, and blocks staff fields for every OTHER tool
try:
    T._guard_no_pii({'x': [{'Patient': 'A'}]}, allow=T.STAFF_ROW_COLUMNS)
    check('guard still blocks Patient even with staff fields allowed', False)
except ValueError:
    check('guard still blocks Patient even with staff fields allowed', True)
try:
    T._guard_no_pii({'x': [{'Given by': 'A'}]})
    check('other tools still cannot return Given by', False)
except ValueError:
    check('other tools still cannot return Given by', True)

# totals reconcile with the existing product tool
full = T.get_product_sales_history(NAME, limit=1)
ref = T.get_product(NAME)
check('total strips match get_product', abs(full['totals']['strips'] - ref['total_sold_strips']) < 0.2, (full['totals'], ref['total_sold_strips']))
check('total value matches get_product', abs(full['totals']['value_rs'] - ref['total_sold_value']) < 1, (full['totals'], ref['total_sold_value']))
check('by-branch strips add up to the total', abs(sum(b['strips'] for b in full['by_branch']) - full['totals']['strips']) < 0.3, full['by_branch'])
check('truncation is reported honestly', full['truncated'] is True and full['shown'] == 1 and full['total_lines'] > 1000, full['total_lines'])

# filters
b = T.get_product_sales_history(NAME, branch='Hospet', limit=3)
check('branch filter returns only that branch', {x['Branch'] for x in b['sales']} == {'Hospet Road'}, b['sales'])
m = T.get_product_sales_history(NAME, month='2026-06', limit=3)
check('June lines have no time and the note says so', all(x['Time'] is None for x in m['sales']) and '2026-06' in (m['time_note'] or ''), m['time_note'])
a = T.get_product_sales_history(NAME, month='2026-09', limit=3)
check('September lines carry a time of day', all(x['Time'] for x in a['sales']) and a['time_note'] is None, a['sales'])
d = T.get_product_sales_history(NAME, date_from='2026-09-10', date_to='2026-09-10', limit=100)
check('single-day filter stays on that day', d['total_lines'] > 0 and {x['Date'] for x in d['sales']} == {'2026-09-10'}, d['total_lines'])
z = T.get_product_sales_history(NAME, month='2020-01')
check('no sales in the period -> clear message, not an error', z.get('total_lines') == 0 and 'note' in z, z)

# name matching
p = T.get_product_sales_history('glycomet gp 2 tab', limit=1)
check('name matching ignores case', p['found'] is True and p['product'].strip().upper() == NAME, p.get('product'))
c = T.get_product_sales_history('glycomet', limit=1)
check('an ambiguous name returns candidates to choose from', c.get('kind') == 'choose_one' and len(c['matches']) > 1, c)
n = T.get_product_sales_history('zzzz not a product')
check('an unknown name says so', n.get('found') is False and 'hint' in n, n)
w = T.get_product_sales_history(NAME, branch='Wholesale', limit=3)
check('wholesale lines are included with sane strips', w['total_lines'] > 0 and all(x['Strips'] > 0 for x in w['sales']), w['sales'])

# ---- "who last gave X, and to whom": get_product_patient_history (customer names) ----
ph = T.run_tool('get_product_patient_history', {'product': NAME, 'limit': 12})
check('buyer history found, no error', ph.get('found') is True and 'error' not in ph, {k: v for k, v in ph.items() if k != 'sales'})
rows = ph['sales']
check('lines are NEWEST FIRST by date and time',
      [(r['Date'], r['Time'] or '') for r in rows] == sorted([(r['Date'], r['Time'] or '') for r in rows], reverse=True), [(r['Date'], r['Time']) for r in rows[:4]])
check('each line carries the customer name (except wholesale), staff names, branch and qty',
      all((r.get('Patient') or r.get('Branch') == 'B2B / Wholesale') and r.get('Given by') and r.get('Billed By')
          and r.get('Branch') and r.get('Qty_units') is not None for r in rows),
      [(r.get('Branch'), bool(r.get('Patient'))) for r in rows])
wh = T.run_tool('get_product_patient_history', {'product': NAME, 'limit': 400})
wl = [r for r in wh['sales'] if r['Branch'] == 'B2B / Wholesale']
check('wholesale lines have no customer name and the tool explains why',
      wl and all(not r.get('Patient') for r in wl) and 'wholesale' in (wh.get('name_note') or '').lower(), (len(wl), wh.get('name_note')))
check('every retail line has a customer name', all(r.get('Patient') for r in wh['sales'] if r['Branch'] != 'B2B / Wholesale'))
check('September-October lines have a time of day', any(r['Time'] for r in rows), [r['Time'] for r in rows])
check('same total line count as the staff-only tool', ph['total_lines'] == T.get_product_sales_history(NAME, limit=1)['total_lines'], (ph['total_lines']))
bh = T.run_tool('get_product_patient_history', {'product': NAME, 'branch': 'Shivaji', 'limit': 5})
check('buyer history filters by branch and keeps customer names', bh['sales'] and {r['Branch'] for r in bh['sales']} == {'Shivaji Chowk'} and all(r.get('Patient') for r in bh['sales']), bh.get('total_lines'))
check('buyer history honours a limit of 5', len(bh['sales']) == 5, len(bh['sales']))
pp = T.run_tool('get_product_patient_history', {'product': 'glycomet', 'limit': 3})
check('buyer history also asks which product when the name is ambiguous', pp.get('kind') == 'choose_one', {k: v for k, v in pp.items() if k != 'sales'})
check('the staff-only tool still has no customer name',
      'Patient' not in T.get_product_sales_history(NAME, limit=3)['sales'][0] and 'Mobile' not in T.get_product_sales_history(NAME, limit=3)['sales'][0])
import ask  # noqa: E402
check('voice mode still withholds the customer-name tool', 'get_product_patient_history' not in {t['name'] for t in ask._available_tools(voice=True)})

print('\nALL PASSED' if not fails else f'\n{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)

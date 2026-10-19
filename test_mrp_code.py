"""Checks for naming a product by 'first 3 letters + MRP' in get_mrp_total.
Run: venv\\Scripts\\python.exe test_mrp_code.py"""
import random
import sys

sys.stdout.reconfigure(encoding='utf-8')
import ask_tools as T  # noqa: E402

fails = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'  -> {detail}'))
    if not ok:
        fails.append(name)


by_key, by_code = T._code_catalog()
fm = T._factor_map()

# 1. a code that matches exactly one product resolves to it, using the pack's own MRP
unique = next((k, v[0]) for k, v in by_key.items() if len(v) == 1 and k[0].isalpha() and 100 <= k[1] <= 20000 and k[1] % 100 != 0)
(code, cents), name = unique
mrp_text = f'{cents / 100:.2f}'
r = T.get_mrp_total([{'product': f'{code.lower()} {mrp_text}', 'units': 30}])
it = r['items'][0]
check('unique code resolves to its one product', it['found'] and it['product'] == name.strip() and it['candidates'] == 1, it)
f = float(fm.get(name, 1) or 1)
check('total = pack MRP / strip size x units, done in code', abs(it['mrp_total'] - round(cents / 100 * 30 / f, 2)) < 0.011 and abs(r['grand_total_mrp'] - it['mrp_total']) < 0.011, (it['mrp_total'], cents, f))
check('it says it used the MRP typed on the pack', 'typed' in it['mrp_from'] and it['matched_by'] == 'first 3 letters + MRP', it)

# 2. different ways of typing the same code give the same answer
for variant in (f'{code}{mrp_text}', f'{code}-{mrp_text}', f'{code}/{mrp_text}', f'  {code.lower()}   {mrp_text} '):
    rr = T.get_mrp_total([{'product': variant, 'units': 30}])['items'][0]
    check(f'typed as {variant!r} gives the same product', rr['found'] and rr['product'] == name.strip(), rr)

# 3. a crowded code returns the best seller first and lists the others with their own totals
crowded = max(by_key.items(), key=lambda kv: len(kv[1]))
(ccode, ccents), cnames = crowded
rc = T.get_mrp_total([{'product': f'{ccode} {ccents / 100:.2f}', 'units': 15}])['items'][0]
pop = T._load()[0]['Product'].astype('string').value_counts()
check('crowded code: the best-selling candidate is used', rc['product'] == cnames[0].strip() and rc['candidates'] == len(cnames), (rc['product'], cnames[:3]))
check('crowded code: other candidates listed with totals', len(rc['similar']) == min(4, len(cnames) - 1) and all('mrp_total' in x for x in rc['similar']), rc['similar'])
check('crowded code: candidates really are in most-sold-first order', all(pop.get(cnames[i], 0) >= pop.get(cnames[i + 1], 0) for i in range(len(cnames) - 1)), [pop.get(n, 0) for n in cnames[:5]])

# 4. a wrong MRP -> clear 'not found' with the closest real ones
bad = T.get_mrp_total([{'product': f'{code} {(cents + 777) / 100:.2f}', 'units': 30}])['items'][0]
check('wrong MRP -> not found, with a reason', bad['found'] is False and 'paise' in bad['reason'], bad)

# 5. rounding the MRP to whole rupees must NOT silently match (the paise matter)
if cents % 100:
    rnd = T.get_mrp_total([{'product': f'{code} {round(cents / 100)}', 'units': 30}])['items'][0]
    check('a rounded MRP does not pretend to match', not rnd.get('found') or rnd.get('product') != name.strip() or rnd.get('matched_by') != 'first 3 letters + MRP', rnd)

# 6. ordinary names still work exactly as before
nm = T.get_mrp_total([{'product': 'Ecosprin AV 75', 'units': 30}])['items'][0]
check('a normal product name still works', nm['found'] and 'ECOSPRIN' in nm['product'].upper() and 'matched_by' not in nm, nm)
check('a name of 4+ letters is never read as a code', T._CODE_RE.match('Dolo 650') is None and T._CODE_RE.match('Aciloc 150') is None and T._CODE_RE.match('ECO 85.50') is not None)

# 7. whole-rupee numbers that are also a strength: both readings shown
both = None
for (c3, cs), names in by_key.items():
    if cs % 100 == 0 and c3.isalpha():
        r2 = T.get_mrp_total([{'product': f'{c3} {cs // 100}', 'units': 10}])['items'][0]
        if r2.get('found') and r2.get('also_possible_by_name'):
            both = r2
            break
check('a whole-rupee code that is also a name+strength shows both readings', both is not None and both['also_possible_by_name']['product'] != both['product'], both)

# 8. the measured success rate on real sale lines (letters of the compact name + exact MRP)
s, _ = T._load()
random.seed(3)
idx = random.sample(range(len(s)), 20000)
exact = first = top3 = n = 0
for i in idx:
    row = s.iloc[i]
    prod, m = row['Product'], row['MRP']
    if prod is None or str(prod) == 'nan':
        continue
    try:
        m = float(m)
    except (TypeError, ValueError):
        continue
    c = ''.join(ch for ch in str(prod).upper().lstrip('X*#- ') if ch.isalnum())[:3] if not str(prod).upper().startswith('XXXX') else ''.join(ch for ch in str(prod).upper().lstrip('X') if ch.isalnum())[:3]
    hits, _ = T._code_candidates(c, m)
    if not hits:
        continue
    n += 1
    names = [h.strip() for h in hits]
    exact += len(hits) == 1 and names[0] == str(prod).strip()
    first += names[0] == str(prod).strip()
    top3 += str(prod).strip() in names[:3]
print(f'\nmeasured on {n:,} random real sale lines: exactly one {exact / n * 100:.1f}% | best-seller is the right one {first / n * 100:.1f}% | right one in top 3 {top3 / n * 100:.1f}%')
check('success rate is in the range measured earlier (first 92%, top 3 99%)', first / n > 0.88 and top3 / n > 0.97, (first / n, top3 / n))

print('\nALL PASSED' if not fails else f'\n{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)

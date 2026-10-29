"""Checks for reorder.py - the 15-day auto reorder formula. Every number below was worked out by hand first.
Offline, no real data. Run: venv\\Scripts\\python.exe test_reorder.py"""
import sys

sys.stdout.reconfigure(encoding='utf-8')
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import reorder as R  # noqa: E402

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'   <-- {detail}'))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------------------------------------
# 1. the least stock the books allow
# ---------------------------------------------------------------------------------------------
lb, opening = R.stock_lower_bound([10, 0, 0, 10, 0, 0], [3, 3, 3, 3, 3, 3])
check('steady buying, never below zero: closing = bought - sold = 20 - 18 = 2, no opening stock needed', (lb, opening) == (2.0, 0.0), (lb, opening))
lb, opening = R.stock_lower_bound([0, 0, 0], [5, 5, 5])
check('sold 15 and bought nothing: the shelf must have held 15 before, and holds 0 now', (lb, opening) == (0.0, 15.0), (lb, opening))
lb, opening = R.stock_lower_bound([100, 0, 0], [1, 1, 1])
check('over-bought: 100 in, 3 out -> at least 97 left', (lb, opening) == (97.0, 0.0), (lb, opening))
lb, opening = R.stock_lower_bound([0, 0, 20, 0], [5, 5, 0, 5])
check('dips to -10, then 20 arrives: opening 10 was needed, closing = 5 - (-10) = 15', (lb, opening) == (15.0, 10.0), (lb, opening))
check('no days at all -> zero, not a crash', R.stock_lower_bound([], []) == (0.0, 0.0))

# ---------------------------------------------------------------------------------------------
# 2. sharing an order between the branches
# ---------------------------------------------------------------------------------------------
check('7 packs shared 60 : 30 : 10 -> 4 + 2 + 1 (largest remainder, total exact)', R.allocate(7, [60, 30, 10]) == [4, 2, 1], R.allocate(7, [60, 30, 10]))
check('10 packs shared 1 : 1 : 1 -> 4 + 3 + 3, still 10', sum(R.allocate(10, [1, 1, 1])) == 10 and sorted(R.allocate(10, [1, 1, 1])) == [3, 3, 4])
check('a branch with no demand gets nothing', R.allocate(5, [3, 0, 2]) == [3, 0, 2])
check('nothing to share / no demand at all -> zeros', R.allocate(0, [1, 2]) == [0, 0] and R.allocate(5, [0, 0]) == [0, 0])
random_totals = all(sum(R.allocate(t, w)) == t for t in (1, 2, 9, 37, 100) for w in ([1, 2, 3, 4], [0.1, 0.1, 5], [7, 0, 0, 1]))
check('whatever the split, the packs given out add up to the packs ordered', random_totals)

# ---------------------------------------------------------------------------------------------
# 3. turning a need into an order (with and without an offer)
# ---------------------------------------------------------------------------------------------
o = R.plan_order(7.2, 3.0, 0.5)
check('no offer: need 7.2 packs -> order 8 (rounded up so stock is not below 15 days)', (o['paid'], o['free'], o['received'], o['how']) == (8, 0.0, 8.0, 'loose'), o)
check('...and the stock after is (3 + 8) / 0.5 per day = 22 days', abs(o['cover_after'] - 22.0) < 1e-9, o)
o = R.plan_order(0.3, 5.0, 0.4)
check('a need under half a pack is not worth an order', o['paid'] == 0 and o['how'] == 'none', o)
S5 = {'base': 5, 'free': 1.0}
o = R.plan_order(5.5, 0.0, 0.5, S5)
check('offer 5+1, need 5.5: pay for 5, get 1 free = 6 packs', (o['paid'], o['free'], o['received'], o['how']) == (5, 1.0, 6.0, 'offer'), o)
o = R.plan_order(9.0, 0.0, 0.6, S5)
check('offer 5+1, need 9 (1.5 offers): 6 packs = 10 days or 12 packs = 20 days - a tie, the larger offer (pay 10) wins', (o['paid'], o['free']) == (10, 2.0), o)
o = R.plan_order(20.0, 0.0, 20 / 15, S5)
check('offer 5+1, need 20: 3 offers (13.5 days) beats 4 offers (18 days): pay 15, get 3 free', (o['paid'], o['free'], o['received']) == (15, 3.0, 18.0), o)
o = R.plan_order(1.2, 0.3, 0.1, {'base': 10, 'free': 2.0})
check('slow seller (1 pack per 10 days) with offer 10+2: one offer would be 123 days of stock - order loose and say so', o['how'] == 'loose (offer too big)' and o['paid'] == 2 and o['free'] == 0.0, o)
o = R.plan_order(20.0, 0.0, 20 / 15, S5, max_cover=30)
check('every order the offer path gives stays at or under the 30-day cap', o['cover_after'] <= 30)
o = R.plan_order(3.0, 0.0, 0.2, S5)
check('offer 5+1 for a need of 3 at 0.2 packs/day: 6 packs = 30 days is exactly at the cap, so it is taken', o['how'] == 'offer' and o['received'] == 6.0 and abs(o['cover_after'] - 30.0) < 1e-9, o)
o = R.plan_order(3.0, 0.0, 0.19, S5)
check('...but 0.19/day makes it 31.6 days: over the cap, order the 3 loose', o['how'] == 'loose (offer too big)' and o['paid'] == 3, o)

# ---------------------------------------------------------------------------------------------
# 4. which offer is the current one
# ---------------------------------------------------------------------------------------------
end = pd.Timestamp('2026-10-16')


def lines(rows):
    return pd.DataFrame(rows, columns=['Day', 'Qty', 'Free', 'Supplier']).assign(Day=lambda d: pd.to_datetime(d['Day']))


sc = R.detect_scheme(lines([('2026-10-01', 5, 1, 'SUPPLIER A'), ('2026-09-01', 5, 1, 'SUPPLIER A'), ('2026-08-10', 10, 2, 'SUPPLIER B'), ('2026-10-05', 5, 0, 'SUPPLIER C')]), end)
check('5+1 seen twice beats 10+2 seen once; a line with no free quantity is not an offer', sc and (sc['base'], sc['free'], sc['seen'], sc['supplier']) == (5, 1.0, 2, 'SUPPLIER A'), sc)
sc = R.detect_scheme(lines([('2026-10-01', 5, 1, 'A'), ('2026-10-10', 10, 2, 'B')]), end)
check('a tie in how often goes to the most recent offer (10+2)', sc and sc['base'] == 10 and sc['supplier'] == 'B', sc)
check('an offer last seen more than 120 days ago is not the current one', R.detect_scheme(lines([('2026-05-01', 5, 1, 'A')]), end) is None)
check('no lines / no free quantity -> no offer', R.detect_scheme(None, end) is None and R.detect_scheme(lines([('2026-10-01', 5, 0, 'A')]), end) is None)

# ---------------------------------------------------------------------------------------------
# 5. how far to trust the stock estimate
# ---------------------------------------------------------------------------------------------
check('bought often and about what was sold: High', R.confidence(1.0, 8, False) == 'High')
check('bought a few times: Medium', R.confidence(1.0, 3, False) == 'Medium')
check('sales mostly met from the stock held before April: count the rack first', R.confidence(0.3, 10, False).startswith('Low'))
check('never bought / bought once: count the rack first', R.confidence(1.0, 0, True).startswith('Low') and R.confidence(1.0, 1, False).startswith('Low'))

# ---------------------------------------------------------------------------------------------
# 6. the whole thing on a tiny made-up business (40 days, 2 branches)
# ---------------------------------------------------------------------------------------------
days = pd.date_range('2026-09-01', periods=40)
sale_rows = []
n = 0
for day in days:
    for prod, branch, qty in (('ALPHA TAB', 'WS', 2), ('BETA SYP', 'WS', 3), ('BETA SYP', 'WH', 2), ('GAMMA CAP', 'WH', 12)):
        n += 1
        sale_rows.append({'Date': day, 'Inv.No': f'{branch}{n}', 'Product': prod, 'Qty': qty})
sales = pd.DataFrame(sale_rows)
branch_of = lambda inv: 'Shivaji Chowk' if str(inv).startswith('WS') else 'Hospet Road'      # noqa: E731
buy = pd.DataFrame([
    # ALPHA: 50 + 50 packs of 1 unit, no offer
    {'Date': '2026-09-01', 'Product': 'ALPHA TAB', 'Qty': 50, 'Free Qty': 0, 'Factor': 1, 'Item Total': 500, 'Supplier': 'S1', 'Source_Month': '2026-09'},
    {'Date': '2026-09-21', 'Product': 'ALPHA TAB', 'Qty': 50, 'Free Qty': 0, 'Factor': 1, 'Item Total': 500, 'Supplier': 'S1', 'Source_Month': '2026-09'},
    # BETA: packs of 10 units, offer 20+4, bought once
    {'Date': '2026-09-01', 'Product': 'BETA SYP', 'Qty': 20, 'Free Qty': 4, 'Factor': 10, 'Item Total': 2000, 'Supplier': 'S2', 'Source_Month': '2026-09'},
    # GAMMA: packs of 10, offer 5+1 several times, plus one bill with an impossible date (swapped day/month, in the future)
    {'Date': '2026-09-01', 'Product': 'GAMMA CAP', 'Qty': 5, 'Free Qty': 1, 'Factor': 10, 'Item Total': 500, 'Supplier': 'S3', 'Source_Month': '2026-09'},
    {'Date': '2026-09-15', 'Product': 'GAMMA CAP', 'Qty': 5, 'Free Qty': 1, 'Factor': 10, 'Item Total': 500, 'Supplier': 'S3', 'Source_Month': '2026-09'},
    {'Date': '2026-12-05', 'Product': 'GAMMA CAP', 'Qty': 5, 'Free Qty': 1, 'Factor': 10, 'Item Total': 500, 'Supplier': 'S3', 'Source_Month': '2026-10'},
])
df, info = R.build_reorder(sales, buy, branch_of)
x = df.set_index('Product')
a = x.loc['ALPHA TAB']
check('window and demand days are read from the sales', str(info['start'].date()) == '2026-09-01' and str(info['end'].date()) == '2026-10-10' and info['demand_days'] == 40, info)
check('ALPHA: sold 80, bought 100 -> at least 20 on the shelf; + 3 days x 2 = 26 units, 26 packs', a['Least_Stock_Packs'] == 20 and abs(a['Stock_Est_Packs'] - 26) < 1e-9, a[['Least_Stock_Packs', 'Stock_Est_Packs']].tolist())
check('ALPHA: target 15 days x 2 = 30, need 4, no offer -> order 4 loose, all to Shivaji (its only branch)',
      a['Target_Packs'] == 30 and abs(a['Need_Packs'] - 4) < 1e-9 and (a['Order_Paid_Packs'], a['How']) == (4, 'loose') and a['Order_Shivaji Chowk'] == 4 and a['Order_Hospet Road'] == 0, a.to_dict())
check('ALPHA: cover now 13 days (26 / 2), after the order 15 days', abs(a['Cover_Now_Days'] - 13.0) < 1e-9 and abs(a['Cover_After_Days'] - 15.0) < 1e-9, a[['Cover_Now_Days', 'Cover_After_Days']].tolist())
b = x.loc['BETA SYP']
check('BETA: 5 units/day over both branches = 0.5 packs/day; bought 240 units, sold 200 -> 4 packs at least, +1.5 = 5.5 packs',
      abs(b['Demand_Per_Day_Units'] - 5) < 1e-9 and abs(b['Least_Stock_Packs'] - 4) < 1e-9 and abs(b['Stock_Est_Packs'] - 5.5) < 1e-9, b[['Demand_Per_Day_Units', 'Least_Stock_Packs', 'Stock_Est_Packs']].tolist())
check('BETA: need 7.5 - 5.5 = 2 packs; the 20+4 offer would leave 59 days -> loose 2 and "offer too big"', abs(b['Need_Packs'] - 2) < 1e-9 and b['Order_Paid_Packs'] == 2 and b['How'] == 'loose (offer too big)' and b['Offer'] == '20+4', b.to_dict())
check('BETA: 2 packs shared 3 : 2 between Shivaji and Hospet -> 1 + 1 (largest remainder)', (b['Order_Shivaji Chowk'], b['Order_Hospet Road']) == (1, 1), b[['Order_Shivaji Chowk', 'Order_Hospet Road']].tolist())
g = x.loc['GAMMA CAP']
# bought 3 x (5+1) x 10 = 180 units (the December-dated bill counted inside the window), sold 12 x 40 = 480 -> sold far more than bought
check('GAMMA: the bill dated in December is counted inside the window, not dropped (3 bills of 60 units = 180 received)', abs(g['Received_vs_Sold'] - 180 / 480) < 1e-9, g['Received_vs_Sold'])
check('GAMMA: sold mostly from stock held before the window -> "count the rack first"; opening stock needed is large', g['Confidence'].startswith('Low') and g['Opening_Needed_Days'] > 20, g[['Confidence', 'Opening_Needed_Days']].tolist())
check('GAMMA: the 5+1 offer is seen (3 lines)', g['Offer'] == '5+1' and g['Offer_Supplier'] == 'S3')
check('every order is shared out exactly', all(df[f'Order_{b_}'].sum() >= 0 for b_ in R.BRANCHES) and
      (df[[f'Order_{b_}' for b_ in R.BRANCHES]].sum(axis=1) == df['Order_Received_Packs']).all(), df[[f'Order_{b_}' for b_ in R.BRANCHES]].sum(axis=1).tolist())
check('the estimated cost is the paid packs x the last price per pack', abs(a['Est_Cost_Rs'] - 4 * 10.0) < 1e-9, a['Est_Cost_Rs'])

# a product that sold only long ago (outside the last 92 days) has no demand: never ordered, no crash
days2 = pd.date_range('2026-05-01', periods=120)
rows2 = [{'Date': d, 'Inv.No': f'WS{i}', 'Product': 'OMEGA TAB', 'Qty': 1} for i, d in enumerate(days2)]
rows2 += [{'Date': d, 'Inv.No': f'WS9{i}', 'Product': 'ZETA TAB', 'Qty': 5} for i, d in enumerate(days2[:10])]
buy2 = pd.DataFrame([{'Date': '2026-05-01', 'Product': 'ZETA TAB', 'Qty': 5, 'Free Qty': 1, 'Factor': 10, 'Item Total': 100, 'Supplier': 'S', 'Source_Month': '2026-05'}])
df2, _ = R.build_reorder(pd.DataFrame(rows2), buy2, branch_of)
z = df2.set_index('Product').loc['ZETA TAB']
check('a product with no sales in the last 92 days: no demand, no order, no crash', z['Demand_Per_Day_Units'] == 0 and z['Order_Paid_Packs'] == 0 and z['Need_Packs'] == 0, z.to_dict())
o2 = df2.set_index('Product').loc['OMEGA TAB']
check('...and a never-purchased product that sells daily is ordered, with "count the rack first"', o2['Order_Paid_Packs'] > 0 and o2['Confidence'].startswith('Low') and o2['Buy_Days'] == 0, o2.to_dict())


# ---------------------------------------------------------------------------------------------
# 7. units: a strip counted as one tablet, a box counted as one piece
# ---------------------------------------------------------------------------------------------
sale_u = pd.DataFrame([
    # BRILINTA: 14 tablets a strip. Two lines in tablets (Factor 14), two lines billed as whole strips with Factor 1
    {'Product': 'BRILINTA', 'Qty': 14, 'Factor': 14, 'MRP': 519.37, 'Item Total': 479.0},
    {'Product': 'BRILINTA', 'Qty': 28, 'Factor': 14, 'MRP': 519.37, 'Item Total': 958.0},
    {'Product': 'BRILINTA', 'Qty': 2, 'Factor': 1, 'MRP': 519.37, 'Item Total': 958.0},
    {'Product': 'BRILINTA', 'Qty': 1, 'Factor': 1, 'MRP': 519.37, 'Item Total': 479.0},
    # SYRUP: sold by the bottle, Factor 1 is right
    {'Product': 'SYRUP', 'Qty': 3, 'Factor': 1, 'MRP': 100.0, 'Item Total': 270.0},
    # VICKS: sold by the piece at Rs 2
    {'Product': 'VICKS CHOC', 'Qty': 10, 'Factor': 1, 'MRP': 2.0, 'Item Total': 19.0},
])
pf = R.pack_factors(sale_u, pd.DataFrame([{'Product': 'BRILINTA', 'Factor': 14}, {'Product': 'BRILINTA', 'Factor': 1}, {'Product': 'SYRUP', 'Factor': 1}]))
check('pack size = the commonest Factor of 2 or more (BRILINTA 14); a product only ever at Factor 1 has none', pf.get('BRILINTA') == 14 and 'SYRUP' not in pf.index and 'VICKS CHOC' not in pf.index, pf.to_dict())
units, n_fixed = R.repair_sales_units(sale_u, pf)
check('tablet lines stay as they are (14 and 28)', units.iloc[0] == 14 and units.iloc[1] == 28, units.tolist())
check('lines billed as whole strips with Factor 1 become tablets: 2 strips = 28, 1 strip = 14', units.iloc[2] == 28 and units.iloc[3] == 14 and n_fixed == 2, (units.tolist(), n_fixed))
check('a bottle (Factor 1) and a loose piece are left alone', units.iloc[4] == 3 and units.iloc[5] == 10)

buy_u = pd.DataFrame([
    {'Product': 'BRILINTA', 'Qty': 10, 'Free Qty': 1, 'Factor': 14, 'MRP': 519.37},      # a proper line: 11 strips = 154 tablets
    {'Product': 'BRILINTA', 'Qty': 10, 'Free Qty': 0, 'Factor': 1, 'MRP': 519.37},       # the Factor 1 slip: still 10 strips = 140 tablets
    {'Product': 'BRILINTA', 'Qty': 2, 'Free Qty': 0, 'Factor': 14, 'MRP': 540.0},        # MRP went up 4%: still one strip each
    {'Product': 'VICKS CHOC', 'Qty': 800, 'Free Qty': 0, 'Factor': 1, 'MRP': 2.0},       # 800 loose pieces
    {'Product': 'VICKS CHOC', 'Qty': 1, 'Free Qty': 0, 'Factor': 1, 'MRP': 1600.0},      # ONE BOX of 800, booked as Qty 1
    {'Product': 'VICKS CHOC', 'Qty': 3, 'Free Qty': 0, 'Factor': 1, 'MRP': 3.5},         # MRP 1.75 x: neither one piece nor a box
    {'Product': 'VICKS CHOC', 'Qty': 5, 'Free Qty': 0, 'Factor': 1, 'MRP': 0.5},         # MRP a quarter: a different pack under the same name
    {'Product': 'NEVER SOLD', 'Qty': 4, 'Free Qty': 1, 'Factor': 10, 'MRP': 50.0},       # no sales to compare with: its own Factor is trusted
])
rp = R.repair_purchase_units(buy_u, sale_u, pf)
check('a proper strip line: (10 + 1 free) x 14 = 154 tablets', rp['Received'].iloc[0] == 154 and rp['k'].iloc[0] == 1, rp.iloc[0].to_dict())
check('the Factor 1 slip with the strip MRP is repaired: 10 x 14 = 140 tablets', rp['Received'].iloc[1] == 140 and not rp['Inconsistent'].iloc[1], rp.iloc[1].to_dict())
check('a 4% MRP rise is price drift, not a different pack', rp['k'].iloc[2] == 1 and rp['Received'].iloc[2] == 28, rp.iloc[2].to_dict())
check('800 loose pieces = 800', rp['Received'].iloc[3] == 800 and rp['k'].iloc[3] == 1)
check('ONE BOX priced 1600 against a Rs 2 piece = 800 pieces (carton of k = 800)', rp['k'].iloc[4] == 800 and rp['Received'].iloc[4] == 800 and rp['PackUnits'].iloc[4] == 800, rp.iloc[4].to_dict())
check('MRP 1.75 x the piece price is neither: flagged inconsistent', bool(rp['Inconsistent'].iloc[5]), rp.iloc[5].to_dict())
check('MRP a quarter of the piece price is flagged inconsistent too', bool(rp['Inconsistent'].iloc[6]), rp.iloc[6].to_dict())
check('a product never sold has nothing to compare with: its own Factor is used (5 x 10 = 50)', rp['Received'].iloc[7] == 50 and not rp['Inconsistent'].iloc[7], rp.iloc[7].to_dict())

# the whole thing: a Vicks-like product. Sold 20 pieces a day for 40 days = 800; bought 800 pieces + 1 box of 800.
days3 = pd.date_range('2026-09-01', periods=40)
sales3 = pd.DataFrame([{'Date': d, 'Inv.No': f'WS{i}', 'Product': 'VICKS CHOC', 'Qty': 20, 'Factor': 1, 'MRP': 2.0, 'Item Total': 38.0} for i, d in enumerate(days3)])
buy3 = pd.DataFrame([
    {'Date': '2026-09-01', 'Product': 'VICKS CHOC', 'Qty': 800, 'Free Qty': 0, 'Factor': 1, 'MRP': 2.0, 'Item Total': 1268.0, 'Supplier': 'AMAZON', 'Source_Month': '2026-09'},
    {'Date': '2026-09-20', 'Product': 'VICKS CHOC', 'Qty': 1, 'Free Qty': 0, 'Factor': 1, 'MRP': 1600.0, 'Item Total': 1262.0, 'Supplier': 'AMAZON', 'Source_Month': '2026-09'}])
d3, _ = R.build_reorder(sales3, buy3, branch_of)
v = d3.set_index('Product').loc['VICKS CHOC']
check('Vicks-like: received 1600 against 800 sold; the order pack is the BOX of 800, not one piece', v['Received_vs_Sold'] == 2.0 and v['Pack_Size'] == 800, v[['Received_vs_Sold', 'Pack_Size']].tolist())
check('Vicks-like: 800 left + 3 days x 20 = 860 pieces = 1.075 boxes; the target is 1 box (a regular seller keeps at least one) -> no order (it was a Rs 1.7 lakh nonsense order before)',
      abs(v['Stock_Est_Packs'] - 1.075) < 1e-9 and v['Order_Paid_Packs'] == 0 and v['Unit_Check'] == 'ok', v[['Stock_Est_Packs', 'Order_Paid_Packs', 'Unit_Check']].tolist())

# a product whose purchase units cannot be reconciled is never ordered automatically
buy4 = pd.DataFrame([{'Date': '2026-09-01', 'Product': 'VICKS CHOC', 'Qty': 3, 'Free Qty': 0, 'Factor': 1, 'MRP': 3.5, 'Item Total': 9.0, 'Supplier': 'X', 'Source_Month': '2026-09'}])
d4, i4 = R.build_reorder(sales3, buy4, branch_of)
v4 = d4.set_index('Product').loc['VICKS CHOC']
check('units that cannot be reconciled: flagged "order by hand" and no automatic order, however low the stock looks', v4['Unit_Check'].startswith('units inconsistent') and v4['Order_Paid_Packs'] == 0, v4[['Unit_Check', 'Order_Paid_Packs', 'Stock_Est_Packs']].tolist())
check('the run reports how many lines needed repair or were inconsistent', i4['purchase_lines_inconsistent'] == 1 and 'sales_lines_repaired' in i4, i4)


# ---------------------------------------------------------------------------------------------
# 8. the two files end on different days
# ---------------------------------------------------------------------------------------------
cnt = pd.Series([400, 450, 420, 0, 430, 410, 440, 420], index=pd.date_range('2026-10-01', periods=8))
check('last_complete_day: a full last day is kept', R.last_complete_day(cnt) == pd.Timestamp('2026-10-08'))
check('last_complete_day: a half-uploaded last day (90 lines against ~420) is dropped', R.last_complete_day(cnt.set_axis(cnt.index).where(cnt.index != cnt.index[-1], 90)) == pd.Timestamp('2026-10-07'))
check('last_complete_day: a quiet day (Sunday-like, 60% of normal) is kept; empty days never count',
      R.last_complete_day(cnt.where(cnt.index != cnt.index[-1], 250)) == pd.Timestamp('2026-10-08') and R.last_complete_day(pd.Series([0, 0], index=pd.date_range('2026-10-01', periods=2))) is None)

d_sales = pd.DataFrame([{'Date': d, 'Product': 'X'} for d in pd.date_range('2026-10-01', periods=10) for _ in range(20)])
d_buy = pd.DataFrame([{'Date': d, 'Product': 'X'} for d in list(pd.date_range('2026-10-01', periods=6)) for _ in range(10)] +
                     [{'Date': '2026-12-05', 'Product': 'X'}, {'Date': '2026-10-30', 'Product': 'X'}])      # two swapped-date bills
as_of, facts = R.data_as_of(d_sales, d_buy)
check('data_as_of: sales run to 10 Oct, purchases only to 6 Oct -> use 6 Oct; the December / 30 Oct bills do not stretch it',
      as_of == pd.Timestamp('2026-10-06') and facts['sales_through'] == pd.Timestamp('2026-10-10') and facts['purchases_through'] == pd.Timestamp('2026-10-06'), (as_of, facts))

# the same business with as_of: sales after the as-of day are not counted at all
sales_a = pd.DataFrame([{'Date': d, 'Inv.No': f'WS{i}', 'Product': 'ALPHA TAB', 'Qty': 2} for i, d in enumerate(pd.date_range('2026-09-01', periods=40))])
buy_a = pd.DataFrame([{'Date': '2026-09-01', 'Product': 'ALPHA TAB', 'Qty': 50, 'Free Qty': 0, 'Factor': 1, 'Item Total': 500, 'Supplier': 'S1', 'Source_Month': '2026-09'}])
da, ia = R.build_reorder(sales_a, buy_a, branch_of, as_of='2026-09-30')
aa = da.set_index('Product').loc['ALPHA TAB']
check('as_of 30 Sep: the window ends there (30 days), 60 units sold, 50 bought, 10 short -> stock = 3 days x 2 = 6 at least',
      str(ia['end'].date()) == '2026-09-30' and ia['demand_days'] == 30 and abs(aa['Least_Stock_Packs']) < 1e-9 and abs(aa['Stock_Est_Packs'] - 6) < 1e-9 and abs(aa['Demand_Per_Day_Units'] - 2) < 1e-9, (ia, aa[['Least_Stock_Packs', 'Stock_Est_Packs']].tolist()))


# ---------------------------------------------------------------------------------------------
# 9. the owner's batch rule: latest batch >= 81% sold, confirmed by 90-day sold / bought >= 81%, order = 15 days of demand
# ---------------------------------------------------------------------------------------------
check('latest batch 100, stock left 19 -> 81% sold (the boundary)', abs(R.latest_batch_sold_share(100, 19) - 0.81) < 1e-9)
check('stock left bigger than the whole batch -> the latest batch is untouched (older stock sells first)', R.latest_batch_sold_share(100, 250) == 0.0)
check('nothing left -> 100% sold; a negative lower bound is treated as nothing', R.latest_batch_sold_share(100, 0) == 1.0 and R.latest_batch_sold_share(100, -5) == 1.0)
check('sell-through: sold 81 of 100 bought = 0.81; bought nothing but sold some = above any limit; neither = 0',
      abs(R.sell_through(81, 100) - 0.81) < 1e-9 and R.sell_through(5, 0) == float('inf') and R.sell_through(0, 0) == 0.0)


def batch_row(**kw):
    """One product as build_reorder reports it, for batch_rule()."""
    row = {'Product': 'P', 'Pack_Size': 10.0, 'Monthly_Demand_Packs': 30.0, 'Latest_Batch_Units': 1000.0, 'Least_Stock_Units': 100.0,
           'Sold_Window_Units': 9000.0, 'Bought_Window_Units': 9000.0, 'Sale_Days_In_Window': 80, 'Unit_Check': 'ok', 'Offer_Base': 0,
           'Offer_Free': 0.0, 'Last_Cost_Per_Pack': 20.0}
    for b_ in R.BRANCHES:
        row[f'Demand_{b_}_Per_Month_Packs'] = 0.0
    row['Demand_Shivaji Chowk_Per_Month_Packs'] = 20.0
    row['Demand_Hospet Road_Per_Month_Packs'] = 10.0
    row.update(kw)
    return row


def run_batch(**kw):
    return R.batch_rule(pd.DataFrame([batch_row(**kw)])).iloc[0]


r1 = run_batch()
check('batch rule: 90% of the latest batch sold, sell-through 100% -> ORDER; 15 days of 30 packs/month = 15 packs, not reduced by the 10 packs left',
      r1['Batch_Status'] == 'ORDER' and r1['Batch_Order_Paid_Packs'] == 15 and r1['Batch_How'] == 'loose', r1.to_dict())
check('...the 15 packs are shared 2 : 1 between Shivaji and Hospet and cost 15 x 20 = 300; stock after = 10 left + 15 = 25 packs = 25 days',
      (r1['Batch_Order_Shivaji Chowk'], r1['Batch_Order_Hospet Road']) == (10, 5) and r1['Batch_Est_Cost_Rs'] == 300 and abs(r1['Batch_Cover_After_Days'] - 25) < 1e-9, r1.to_dict())
check('exactly 81% sold triggers', run_batch(Least_Stock_Units=190.0)['Batch_Status'] == 'ORDER')
t = run_batch(Least_Stock_Units=191.0)
check('80.9% sold does not: "not triggered" and no order', t['Batch_Status'].startswith('not triggered') and t['Batch_Order_Paid_Packs'] == 0, t['Batch_Status'])
check('stock left 400 of a 1000 batch (60% sold): not triggered', run_batch(Least_Stock_Units=400.0)['Batch_Status'].startswith('not triggered'))
c = run_batch(Sold_Window_Units=8000.0, Bought_Window_Units=10000.0)
check('triggered but 90-day sold / bought = 80%: NOT confirmed, no order', c['Batch_Status'].startswith('triggered, NOT confirmed') and c['Batch_Order_Paid_Packs'] == 0, c['Batch_Status'])
check('90-day sold / bought = exactly 81% confirms', run_batch(Sold_Window_Units=8100.0, Bought_Window_Units=10000.0)['Batch_Status'] == 'ORDER')
check('nothing bought in the window but sales go on (older batch): confirmed', run_batch(Bought_Window_Units=0.0)['Batch_Status'] == 'ORDER')
check('a product with no purchase batch in the files is listed as such, not ordered', run_batch(Latest_Batch_Units=np.nan)['Batch_Status'].startswith('no purchase batch'))
check('no sales in the window: no order', run_batch(Monthly_Demand_Packs=0.0)['Batch_Status'] == 'no sales in the window')
check('sold on only 2 days: left for the owner to decide', run_batch(Sale_Days_In_Window=2)['Batch_Status'].endswith('owner decides'))
check('units inconsistent: order by hand', run_batch(Unit_Check='units inconsistent - order by hand')['Batch_Status'].endswith('order by hand'))
check('a slow seller (1 pack a month = half a pack in 15 days) is ordered 1 whole pack; 1.5 packs wanted -> 2', run_batch(Monthly_Demand_Packs=1.0)['Batch_Order_Paid_Packs'] == 1 and run_batch(Monthly_Demand_Packs=3.0)['Batch_Order_Paid_Packs'] == 2)

# offers: the order is a whole number of offers; the one whose quantity is nearest to the 15 days wins
o1 = run_batch(Monthly_Demand_Packs=30.0, Offer_Base=5, Offer_Free=1.0)       # 15 packs wanted; one offer = 6: 12 (3 short) or 18 (3 over) -> tie -> the larger
check('5+1 and 15 packs wanted: 2 offers = 12 or 3 offers = 18 are equally near -> 3 offers: pay 15, 3 free', (o1['Batch_Order_Paid_Packs'], o1['Batch_Order_Free_Packs'], o1['Batch_How']) == (15, 3.0, 'offer'), o1.to_dict())
o2 = run_batch(Monthly_Demand_Packs=16.0, Offer_Base=5, Offer_Free=1.0)       # 8 packs wanted: 6 (2 short) or 12 (4 over)
check('5+1 and 8 packs wanted: 1 offer (6) is nearer than 2 (12) -> pay 5, 1 free', (o2['Batch_Order_Paid_Packs'], o2['Batch_Order_Free_Packs']) == (5, 1.0), o2.to_dict())
o3 = run_batch(Monthly_Demand_Packs=20.0, Offer_Base=5, Offer_Free=1.0)       # 10 packs wanted: 6 (4 short) or 12 (2 over) -> 2 offers: "5 or 10"
check('5+1 and 10 packs wanted: 2 offers (12) are nearer than 1 (6) -> pay 10, 2 free', (o3['Batch_Order_Paid_Packs'], o3['Batch_Order_Free_Packs']) == (10, 2.0), o3.to_dict())
o4 = run_batch(Monthly_Demand_Packs=3.0, Offer_Base=60, Offer_Free=9.0)       # 1.5 packs wanted, an offer of 69 would leave over 30 days
check('an offer far bigger than 15 days (60+9 for a 3-a-month seller) is not taken: bought loose', o4['Batch_How'] == 'loose (offer too big)' and o4['Batch_Order_Paid_Packs'] == 2, o4.to_dict())

# the real-data path: latest batch, bought / sold in the window, least stock
days9 = pd.date_range('2026-09-01', periods=40)
mk = lambda prod, q: [{'Date': d, 'Inv.No': f'WS{prod}{i}', 'Product': prod, 'Qty': q} for i, d in enumerate(days9)]      # noqa: E731
sales9 = pd.DataFrame(mk('FAST TAB', 3) + mk('SLOW TAB', 2))
buy9 = pd.DataFrame([
    {'Date': '2026-09-01', 'Product': 'FAST TAB', 'Qty': 50, 'Free Qty': 0, 'Factor': 1, 'Item Total': 500, 'Supplier': 'S', 'Source_Month': '2026-09'},
    {'Date': '2026-09-21', 'Product': 'FAST TAB', 'Qty': 50, 'Free Qty': 0, 'Factor': 1, 'Item Total': 500, 'Supplier': 'S', 'Source_Month': '2026-09'},
    {'Date': '2026-09-01', 'Product': 'SLOW TAB', 'Qty': 50, 'Free Qty': 0, 'Factor': 1, 'Item Total': 500, 'Supplier': 'S', 'Source_Month': '2026-09'},
    {'Date': '2026-09-21', 'Product': 'SLOW TAB', 'Qty': 40, 'Free Qty': 10, 'Factor': 1, 'Item Total': 400, 'Supplier': 'S', 'Source_Month': '2026-09'},
])
d9, _ = R.build_reorder(sales9, buy9, branch_of, demand_days=90)
b9 = R.batch_rule(d9).set_index('Product')
f9, s9 = b9.loc['FAST TAB'], b9.loc['SLOW TAB']
check('FAST: latest batch = the 21 Sep delivery of 50; bought 100 and sold 120 in the window; nothing certainly left',
      f9['Latest_Batch_Units'] == 50 and f9['Bought_Window_Units'] == 100 and f9['Sold_Window_Units'] == 120 and f9['Least_Stock_Units'] == 0 and str(f9['Latest_Batch_Date'].date()) == '2026-09-21',
      f9[['Latest_Batch_Units', 'Bought_Window_Units', 'Sold_Window_Units', 'Least_Stock_Units']].tolist())
check('FAST: 100% of the latest batch sold, sell-through 120% -> ORDER 15 days x 3 = 45, all to Shivaji (its only branch)',
      f9['Batch_Status'] == 'ORDER' and f9['Batch_Order_Paid_Packs'] == 45 and f9['Batch_Order_Shivaji Chowk'] == 45, f9.to_dict())
check('SLOW: latest batch counts paid + free (50 units); 100 bought, 80 sold, 20 left = 60% of that batch sold -> not triggered',
      s9['Latest_Batch_Units'] == 50 and s9['Least_Stock_Units'] == 20 and s9['Batch_Status'] == 'not triggered: latest batch only 60% sold', s9[['Latest_Batch_Units', 'Least_Stock_Units', 'Batch_Status']].tolist())
check('the window is a parameter: with a 10-day window the demand is the last 10 days only', abs(R.build_reorder(sales9, buy9, branch_of, demand_days=10)[0].set_index('Product').loc['FAST TAB', 'Demand_Per_Day_Units'] - 3) < 1e-9)

# ---------------------------------------------------------------------------------------------
# 8. the order by distributor
# ---------------------------------------------------------------------------------------------
toy = pd.DataFrame({
    'Product': ['A TAB', 'B TAB', 'C TAB', 'D TAB', 'E TAB'],
    'Last_Supplier': ['Y', 'Y', 'Y', 'Y', R.NOT_BOUGHT],
    'Offer_Supplier': ['X', 'Y', 'X', '', np.nan],
    'Offer': ['5+1', '10+2', '5+1', '', ''],
    'How': ['offer', 'offer', 'loose (offer too big)', 'loose', 'loose']})
asg = R.assign_distributor(toy).set_index('Product')
check("an offer another supplier gave: the order goes to that supplier (their scheme), with the reason", asg.loc['A TAB', 'Distributor'] == 'X' and 'X' in asg.loc['A TAB', 'Distributor_Why'] and 'Y' in asg.loc['A TAB', 'Distributor_Why'], asg.loc['A TAB'].to_dict())
check("an offer from the same supplier it was last bought from: stays with them, no remark", asg.loc['B TAB', 'Distributor'] == 'Y' and asg.loc['B TAB', 'Distributor_Why'] == '')
check("an offer too big and bought loose: the offer is not used, so the last supplier keeps it", asg.loc['C TAB', 'Distributor'] == 'Y' and asg.loc['C TAB', 'Distributor_Why'] == '')
check("no offer at all (blank or missing): the last supplier; never bought since April stays unassigned", asg.loc['D TAB', 'Distributor'] == 'Y' and asg.loc['E TAB', 'Distributor'] == R.NOT_BOUGHT, asg['Distributor'].to_dict())

import os  # noqa: E402
import re  # noqa: E402
import tempfile  # noqa: E402

from openpyxl import load_workbook  # noqa: E402

import reorder_report as RR  # noqa: E402


def toy_order(product, dist, cost, why='', offer='', how=''):
    return {'Product': product, 'Pack_Size': 10, 'Offer': offer, 'How': how or ('offer' if offer else 'loose'), 'Order_Paid_Packs': 5, 'Order_Free_Packs': 1.0 if offer else 0.0, 'Order_Received_Packs': 6.0 if offer else 5.0,
            'Order_Value_Rs': cost, 'Order_Shivaji Chowk': 3, 'Order_Hospet Road': 2, 'Order_Herur Branch': 0, 'Order_B2B / Wholesale': 0,
            'Confidence': 'High', 'Note': '', 'Distributor': dist, 'Distributor_Why': why}


long_name = 'ZZ LONG NAME TRADERS AND DISTRIBUTORS PRIVATE LTD'
book = pd.DataFrame([toy_order('P1', 'SMALL: CO', 100), toy_order('P2', 'BIG PHARMA', 900, offer='5+1'), toy_order('P3', 'BIG PHARMA', 300, why="offer 5+1 is BIG PHARMA's (last bought from SMALL: CO)", offer='5+1'),
                     toy_order('P4', R.NOT_BOUGHT, 50), toy_order('P5', long_name, 400, offer='10+1', how='loose (offer too big)'), toy_order('P6', long_name[:31] + ' X', 10)])
with tempfile.TemporaryDirectory() as tmp:
    path = RR.write_distributor_book(os.path.join(tmp, 'o.xlsx'), book, pd.Timestamp('2026-10-27'), pd.Timestamp('2026-10-29'))
    wb = load_workbook(path)
names = wb.sheetnames
check('Summary first, then the distributors biggest cost first, "no distributor yet" last', names[0] == 'Summary' and names[1] == 'BIG PHARMA' and names[-1] == RR.NO_DISTRIBUTOR_SHEET, names)
check('every sheet name is legal for Excel (31 characters, no : ? * / [ ] \\) and unique', all(len(n) <= 31 and not re.search(r'[\[\]:*?/\\]', n) for n in names) and len({n.lower() for n in names}) == len(names), names)
big = wb['BIG PHARMA']
check('a distributor sheet lists its own products only, with the offer, the packs to pay for and the free packs', [big.cell(row=r, column=2).value for r in (6, 7)] == ['P2', 'P3'] and big['D6'].value == '5+1' and big['E6'].value == 5 and big['F6'].value == 1, [c.value for c in big[6]])
check("the total at the foot is the sum of the lines and the print area is the order form only (A to G)", big['I8'].value == 1200 and big.print_area == "'BIG PHARMA'!$A$1:$G$8", (big['I8'].value, big.print_area))
check('the reason an offer was moved to this distributor is in the Note column', 'last bought from SMALL: CO' in big['O7'].value, big['O7'].value)
sm = wb['Summary']
rows = {sm.cell(row=r, column=2).value: sm.cell(row=r, column=6).value for r in range(5, 12) if sm.cell(row=r, column=2).value}
loose_offer = [wb[n].cell(row=6, column=4).value for n in names if n.startswith('ZZ LONG')]
check('an offer too big to use (bought loose) is not on the order form, so the distributor sees only schemes being ordered', len(loose_offer) == 2 and all(v is None for v in loose_offer), loose_offer)
check('Summary has one row per distributor and its total is the whole order', rows.get('BIG PHARMA') == 1200 and rows.get('Total') == 1760, rows)
check('Summary links to each sheet', sm['H5'].hyperlink is not None and 'BIG PHARMA' in sm['H5'].hyperlink.location, sm['H5'].hyperlink)

print()
print(f'{len(FAILS)} FAILED: {FAILS}' if FAILS else 'ALL PASSED')
sys.exit(1 if FAILS else 0)

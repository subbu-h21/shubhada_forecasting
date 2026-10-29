"""Auto reorder formula, branch-wise (DRAFT for the owner to confirm) - hold 15 days of stock, always.

What the books know and do not know
-----------------------------------
There is NO stock-on-hand report. What exists is every purchase (paid + free units) and every sale since
1 April, for the whole business. Purchases are booked mostly at Hospet Road and goods are transferred on to
Shivaji Chowk and Herur, with no transfer record - so stock can only be reasoned about for the business as a
whole, and then shared out to the branches by how much each one sells.

The formula, per product
------------------------
 1. DEMAND   Each branch's sales in the last 92 days / 92 = units a day (D_b). The business's D = sum of the
             branches, B2B / wholesale included (it draws on the same goods).
 2. STOCK    Running balance of (received - sold) day by day. The opening stock must have been big enough that
             the balance never went below zero, so the LEAST stock you can hold today is
                 closing balance - lowest balance (if the lowest was below zero).
             Add FLOOR_DAYS of demand (the few days of stock normally left just before a delivery arrives):
                 stock = least_stock + FLOOR_DAYS x D.
             Free units count as received (they are extra stock, not billed).
 3. TARGET   TARGET_DAYS (15) x D, in packs, and at least 1 pack for a product that sells regularly.
 4. NEED     target - stock; nothing if less than half a pack.
 5. OFFER    If the product is bought on an offer (5+1, 10+2 ...) the order is a whole number of offers: pay for
             5 or 10 (get 1 or 2 free). Of the two nearest choices take the one whose stock after the delivery is
             closest to 15 days, never above MAX_COVER_DAYS (30). If even that is too much for a slow seller,
             order loose (no offer) and say so.
 6. BRANCHES The delivered packs are shared to Shivaji Chowk / Hospet Road / Herur / B2B by their share of D
             (whole packs, largest remainder).
Confidence says how far to trust the stock estimate: where sales were mostly met from the stock held before
April, or the product was seldom bought, the rack should be counted before ordering.

Pure functions first (tested in test_reorder.py), then build_reorder() for the real data.
"""
import math
from collections import Counter

import numpy as np
import pandas as pd

TARGET_DAYS = 15            # the owner's rule: any day, about 15 days of stock
FLOOR_DAYS = 3              # days of stock normally left at the lowest point, just before a delivery
DEMAND_DAYS = 92            # demand = the last ~3 months
MAX_COVER_DAYS = 30         # never take an offer that leaves more than this (the old "one month" ceiling)
MIN_NEED_PACKS = 0.5        # a need smaller than this is not worth an order
REGULAR_MIN_SALE_DAYS = 3   # sold on at least this many days in the window = "sells regularly" (hold at least 1 pack)
SCHEME_WINDOW_DAYS = 120    # offers seen in purchases this recent are taken as the current offer
BRANCHES = ['Shivaji Chowk', 'Hospet Road', 'Herur Branch', 'B2B / Wholesale']
NOT_BOUGHT = 'Not bought since April'   # the "supplier" of a product with no purchase on file: the owner picks one


# ---------------------------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------------------------
def stock_lower_bound(received, sold):
    """received, sold: units per day (equal-length arrays, oldest first).
    -> (least stock you can be holding now, opening stock the books require).
    The balance C(t) = running (received - sold); the opening stock O must keep O + C(t) >= 0 on every day,
    so O >= -min(C); the closing stock is then O + C(T) >= C(T) - min(0, min C)."""
    c = np.cumsum(np.asarray(received, dtype=float) - np.asarray(sold, dtype=float))
    low = min(0.0, float(c.min())) if len(c) else 0.0
    return float(c[-1] - low) if len(c) else 0.0, float(-low)


def detect_scheme(lines, as_of, window_days=SCHEME_WINDOW_DAYS):
    """The current offer for a product from its recent purchase lines.
    lines: DataFrame with Day, Qty (paid packs), Free (free packs), Supplier. Only lines with a free quantity
    inside the window count; the most frequent (paid, free) pair wins (a tie goes to the most recent).
    -> dict(base, free, seen, supplier, last) or None.  base = paid packs per offer (the order is a multiple of it)."""
    if lines is None or len(lines) == 0:
        return None
    d = lines[(lines['Free'] > 0) & (lines['Qty'] > 0) & (lines['Day'] >= as_of - pd.Timedelta(days=window_days))]
    if d.empty:
        return None
    pairs = Counter(zip(d['Qty'].astype(int), d['Free'].round(2)))
    last = {}
    for q, f, day in zip(d['Qty'].astype(int), d['Free'].round(2), d['Day']):
        last[(q, f)] = max(last.get((q, f), day), day)
    (base, free), n = max(pairs.items(), key=lambda kv: (kv[1], last[kv[0]]))
    sup = d[(d['Qty'].astype(int) == base) & (d['Free'].round(2) == free)].sort_values('Day')['Supplier'].iloc[-1]
    return {'base': int(base), 'free': float(free), 'seen': int(n), 'supplier': str(sup), 'last': last[(base, free)]}


def plan_order(need_packs, stock_packs, d_packs, scheme=None, target_days=TARGET_DAYS, max_cover=MAX_COVER_DAYS):
    """Turn a need (in packs) into an order. -> dict(paid, free, received, cover_after, how).
    how: 'none' | 'loose' | 'offer' | 'loose (offer too big)'."""
    if need_packs < MIN_NEED_PACKS:
        return {'paid': 0, 'free': 0.0, 'received': 0.0, 'cover_after': _cover(stock_packs, d_packs), 'how': 'none'}
    loose = math.ceil(need_packs - 1e-9)
    if not scheme:
        return {'paid': loose, 'free': 0.0, 'received': float(loose), 'cover_after': _cover(stock_packs + loose, d_packs), 'how': 'loose'}
    base, free = scheme['base'], scheme['free']
    unit = base + free
    ratio = need_packs / unit
    options = []
    for n in sorted({max(1, math.floor(ratio)), max(1, math.ceil(ratio))}):
        cov = _cover(stock_packs + n * unit, d_packs)
        if cov <= max_cover + 1e-9:                                   # tolerant of floating-point noise (6 / 0.2 = 30.000000000000004)
            options.append((round(abs(cov - target_days), 6), -n, n, cov))
    if options:
        _, _, n, cov = min(options)
        return {'paid': int(n * base), 'free': float(n * free), 'received': float(n * unit), 'cover_after': cov, 'how': 'offer'}
    return {'paid': loose, 'free': 0.0, 'received': float(loose), 'cover_after': _cover(stock_packs + loose, d_packs), 'how': 'loose (offer too big)'}


def _cover(packs, d_packs):
    return float('inf') if d_packs <= 0 else float(packs) / d_packs


def allocate(total, weights):
    """Share a whole number of packs by weights, in whole packs, the total kept exact (largest remainder)."""
    w = np.asarray(weights, dtype=float)
    total = int(round(total))
    if total <= 0 or w.sum() <= 0:
        return [0] * len(w)
    raw = w / w.sum() * total
    base = np.floor(raw).astype(int)
    for i in np.argsort(-(raw - base), kind='stable')[:total - int(base.sum())]:
        base[i] += 1
    return [int(x) for x in base]


def assign_distributor(orders):
    """Who each product on the order is bought from. Normally the supplier it was last bought from. When the order
    is built on an offer (5+1 ...) that another supplier gave, that supplier gets it: the quantity only makes sense
    with their scheme. orders: needs Last_Supplier, Offer_Supplier, Offer, How. Adds Distributor and
    Distributor_Why (empty when it is simply the last supplier)."""
    d = orders.copy()
    offer_from = d['Offer_Supplier'].fillna('')
    moved = (d['How'] == 'offer') & (offer_from != '') & (offer_from != d['Last_Supplier'])
    d['Distributor'] = np.where(moved, offer_from, d['Last_Supplier'])
    d['Distributor_Why'] = [f"offer {o} is {s}'s (last bought from {last})" if m else ''
                            for m, o, s, last in zip(moved, d['Offer'], offer_from, d['Last_Supplier'])]
    return d


def confidence(ratio_received_to_sold, buy_days, never_bought):
    """How far the stock estimate can be trusted for an ORDER decision."""
    if never_bought or buy_days <= 1 or ratio_received_to_sold < 0.5:
        return 'Low - count the rack first'
    if buy_days >= 5 and ratio_received_to_sold >= 0.7:
        return 'High'
    return 'Medium'



# ---------------------------------------------------------------------------------------------
# Units: the sales and purchase files do not always count the same thing
# ---------------------------------------------------------------------------------------------
# A strip product (Brilinta: 14 tablets) is sold in TABLETS - the sales Factor says 14 - but some lines carry
# Factor 1 and are priced per whole strip (the Qty is strips). Purchases have the same slip, and loose goods
# (Vicks chocolate, plasters, gloves) are bought as boxes but sold by the piece, both under one product name.
# Left alone, those lines make the stock look tiny or huge and the order nonsense. The test that settles it is
# the printed MRP: sales MRP is always the price of one PACK, so a purchase line whose MRP is k x that pack
# MRP is a carton of k packs, and one whose MRP equals it is one pack.
MRP_SAME_LOW, MRP_SAME_HIGH = 0.5, 1.5      # purchase MRP / sales pack MRP in this range = the same pack (prices drift)
CARTON_TOLERANCE = 0.06                     # a carton multiple must be this close to a whole number
UNIT_HOLD_SHARE = 0.5                       # most (over half) of the units received sit on lines whose MRP fits no pack: no automatic order
UNIT_NOTE_SHARE = 0.15                      # some lines odd (usually a price change or a second pack size): order, but say so


def _col(df, name):
    """A numeric column, or NaN when the frame does not have it."""
    return pd.to_numeric(df[name], errors='coerce') if name in df.columns else pd.Series(np.nan, index=df.index)


def pack_factors(sales, purch):
    """Units in one pack of each product (the commonest Factor of 2 or more seen in sales or purchases; 1 if none)."""
    both = pd.concat([sales[['Product', 'Factor']], purch[['Product', 'Factor']]]) if 'Factor' in sales.columns else purch[['Product', 'Factor']]
    both = both[pd.to_numeric(both['Factor'], errors='coerce') >= 2]
    return both.groupby('Product')['Factor'].agg(lambda x: x.mode().iat[0])


def repair_sales_units(sales, pf):
    """Sales Qty in UNITS. A line recorded with Factor 1 for a pack product but priced like a whole pack has its
    Qty in packs: multiply by the pack size. -> (units Series, number of lines repaired)."""
    qty = _col(sales, 'Qty').fillna(0)
    factor = _col(sales, 'Factor').fillna(1)
    mrp = _col(sales, 'MRP')
    total = _col(sales, 'Item Total')
    p_f = sales['Product'].map(pf)
    unit_price = total / qty.replace(0, np.nan)
    packs_priced = (unit_price / mrp).between(0.4, 1.12)
    fix = (factor <= 1) & p_f.notna() & packs_priced & (qty > 0)
    return qty.where(~fix, qty * p_f), int(fix.sum())


def repair_purchase_units(purch, sales, pf):
    """Units received per purchase line (paid + free) and whether the line makes sense.
    k = purchase MRP / the product's normal sales pack MRP: about 1 -> one pack; a whole number >= 2 -> a carton of k
    packs. Units = packs x k x pack size. -> DataFrame(k, Received, PaidUnits, Inconsistent, PackUnits)."""
    m_star = _col(sales, 'MRP').where(lambda m: m > 0).groupby(sales['Product']).median()
    qty = _col(purch, 'Qty').fillna(0)
    free = _col(purch, 'Free Qty').fillna(0)
    mrp = _col(purch, 'MRP')
    ref = purch['Product'].map(m_star)
    p_f = purch['Product'].map(pf).fillna(1)
    ratio = mrp / ref
    k = pd.Series(1.0, index=purch.index)
    rounded = ratio.round()
    carton = (ratio >= MRP_SAME_HIGH) & ((ratio - rounded).abs() <= CARTON_TOLERANCE * rounded)
    k = k.where(~carton, rounded)
    odd = ref.notna() & mrp.notna() & ((ratio < MRP_SAME_LOW) | ((ratio >= MRP_SAME_HIGH) & ~carton))
    # no sales MRP to compare with (never sold) or no MRP on the line: trust the line's own Factor
    own = _col(purch, 'Factor').replace(0, np.nan).fillna(1)
    units_per_pack = pd.Series(np.where(ref.notna() & mrp.notna(), k * p_f, own), index=purch.index)
    return pd.DataFrame({'k': k, 'PackUnits': units_per_pack, 'Received': (qty + free) * units_per_pack,
                         'PaidUnits': qty * units_per_pack, 'Inconsistent': odd})



# ---------------------------------------------------------------------------------------------
# The owner's batch rule (a second method, to compare with the 15-day-target formula above)
# ---------------------------------------------------------------------------------------------
# "Latest batch qty - latest batch sale crosses 81% of its purchase qty -> candidate for order; confirmed if, over 90 days,
#  all batches sold / all batches bought is also above 81%; then 90-day sales / 90 x 15 = the 15-day order qty."
# The reports carry NO batch numbers, so: a batch = one delivery (all purchase lines of a product on its latest purchase
# day, paid + free), and sales are assumed to take the OLDEST stock first. Under that assumption the latest batch is
# touched only when everything older is gone, so "latest batch >= 81% sold" is the same as
# "all the stock left is at most 19% of the latest batch".
BATCH_SOLD_TRIGGER = 0.81       # the latest batch is this much sold -> candidate
BATCH_SELL_THROUGH = 0.81       # confirmed when the last 90 days sold / bought is at least this
BATCH_DEMAND_DAYS = 90
BATCH_ORDER_DAYS = 15


def latest_batch_sold_share(batch_units, stock_units):
    """Share of the latest batch already sold, oldest-first: 1 - (stock left, at most the batch) / batch.
    Stock at or above the whole batch means the latest batch is untouched (0)."""
    if batch_units is None or batch_units <= 0:
        return 0.0
    return 1.0 - min(max(float(stock_units), 0.0), float(batch_units)) / float(batch_units)


def sell_through(sold_units, bought_units):
    """Sold / bought over the same days. Nothing bought but something sold = sold from older stock: above any limit."""
    if bought_units is None or bought_units <= 0:
        return float('inf') if sold_units and sold_units > 0 else 0.0
    return float(sold_units) / float(bought_units)


def batch_rule(df, trigger=BATCH_SOLD_TRIGGER, confirm=BATCH_SELL_THROUGH, order_days=BATCH_ORDER_DAYS, max_cover=MAX_COVER_DAYS):
    """df: build_reorder(..., demand_days=BATCH_DEMAND_DAYS) output. Adds the Batch_* columns.
    Batch_Status says WHY a product is or is not on the order list. The order is order_days x daily demand (NOT reduced by
    the stock left), in whole offers where the product is bought on an offer, and at least 1 pack."""
    recs = df.to_dict('records')
    rows = []
    for r in recs:
        f = float(r['Pack_Size'])
        d_packs = float(r['Monthly_Demand_Packs']) / 30.0
        has_batch = pd.notna(r['Latest_Batch_Units'])
        share = latest_batch_sold_share(r['Latest_Batch_Units'], r['Least_Stock_Units']) if has_batch else np.nan
        thru = sell_through(r['Sold_Window_Units'], r['Bought_Window_Units'])
        regular = int(r['Sale_Days_In_Window']) >= REGULAR_MIN_SALE_DAYS
        row = {'Batch_Sold_Share': share, 'Sell_Through_Window': thru, 'Batch_Order_Paid_Packs': 0, 'Batch_Order_Free_Packs': 0.0,
               'Batch_Order_Received_Packs': 0.0, 'Batch_Cover_After_Days': np.nan, 'Batch_How': 'none', 'Batch_Est_Cost_Rs': 0.0}
        for b in BRANCHES:
            row[f'Batch_Order_{b}'] = 0
        if d_packs <= 0:
            status = 'no sales in the window'
        elif not has_batch:
            status = 'no purchase batch in the files - check the rack'
        elif share < trigger - 1e-9:
            status = 'not triggered: latest batch only %.0f%% sold' % (share * 100)
        elif thru < confirm - 1e-9:
            status = 'triggered, NOT confirmed: sold only %.0f%% of what was bought' % (thru * 100)
        elif not regular:
            status = 'triggered and confirmed, but sold on fewer than %d days - owner decides' % REGULAR_MIN_SALE_DAYS
        elif str(r['Unit_Check']).startswith('units inconsistent'):
            status = 'triggered and confirmed, but units inconsistent - order by hand'
        else:
            status = 'ORDER'
            q = max(order_days * d_packs, 1.0)
            scheme = {'base': int(r['Offer_Base']), 'free': float(r['Offer_Free'])} if r['Offer_Base'] > 0 else None
            plan = plan_order(q, 0.0, d_packs, scheme, order_days, max_cover)      # stock 0: the 15 days are bought in full
            left = float(r['Least_Stock_Units']) / f
            row.update({'Batch_Order_Paid_Packs': plan['paid'], 'Batch_Order_Free_Packs': plan['free'],
                        'Batch_Order_Received_Packs': plan['received'], 'Batch_Cover_After_Days': _cover(left + plan['received'], d_packs),
                        'Batch_How': plan['how'],
                        'Batch_Est_Cost_Rs': plan['paid'] * float(r['Last_Cost_Per_Pack']) if pd.notna(r['Last_Cost_Per_Pack']) else np.nan})
            for b, sh in zip(BRANCHES, allocate(plan['received'], [float(r[f'Demand_{b}_Per_Month_Packs']) for b in BRANCHES])):
                row[f'Batch_Order_{b}'] = sh
        row['Batch_Status'] = status
        rows.append(row)
    return df.join(pd.DataFrame(rows, index=df.index))


# ---------------------------------------------------------------------------------------------
# The real data
# ---------------------------------------------------------------------------------------------
def last_complete_day(counts, frac=0.5, back=7):
    """counts: lines per day (Series indexed by day). The last day that looks fully uploaded: its line count is at
    least `frac` of the median of the `back` busy days before it. A half-uploaded last day is dropped."""
    c = counts[counts > 0].sort_index()
    for i in range(len(c) - 1, -1, -1):
        prev = c.iloc[max(0, i - back):i]
        if len(prev) < 3 or c.iloc[i] >= frac * float(prev.median()):
            return c.index[i]
    return c.index[0] if len(c) else None


def data_as_of(sales, purch):
    """The day up to which BOTH files are complete. The sales and purchase reports are uploaded separately, so one can
    run days ahead of the other; stock is only meaningful where both are in. Purchase lines dated after the last sales
    day are swapped day/month entries (Nov, Dec), not real deliveries, and are ignored here.
    -> (as_of Timestamp, dict(sales_through, purchases_through))."""
    sd = pd.to_datetime(sales['Date'], format='mixed', errors='coerce').dt.normalize().dropna()
    pdy = pd.to_datetime(purch['Date'], format='mixed', errors='coerce').dt.normalize().dropna()
    s_end = last_complete_day(sd.value_counts())
    pdy = pdy[pdy <= sd.max()]
    p_end = last_complete_day(pdy.value_counts())
    return min(s_end, p_end), {'sales_through': s_end, 'purchases_through': p_end}


def build_reorder(sales, purch, branch_of, factor_of=None, target_days=TARGET_DAYS, floor_days=FLOOR_DAYS, as_of=None, demand_days=DEMAND_DAYS):
    """sales, purch: the reckoner's frames. branch_of: function Inv.No -> branch label (run_reckoner.extract_branch).
    as_of: the last day to use (see data_as_of); default = the last sales day. -> (per-product DataFrame, info dict)."""
    s = sales.copy()
    p = purch.copy()
    s['Day'] = pd.to_datetime(s['Date'], format='mixed', errors='coerce').dt.normalize()
    p['Day'] = pd.to_datetime(p['Date'], format='mixed', errors='coerce').dt.normalize()
    if as_of is not None:
        s = s[s['Day'] <= pd.Timestamp(as_of)]
    start, end = s['Day'].min(), (pd.Timestamp(as_of) if as_of is not None else s['Day'].max())
    days = pd.date_range(start, end)
    s['Product'] = s['Product'].astype('string').str.strip()
    p['Product'] = p['Product'].astype('string').str.strip()
    s['Branch'] = s['Inv.No'].apply(branch_of)

    pf = pack_factors(s, p)
    s['Qty'], sales_lines_repaired = repair_sales_units(s, pf)
    fixed = repair_purchase_units(p, s, pf)
    p['Free'] = pd.to_numeric(p['Free Qty'], errors='coerce').fillna(0)
    p['Qty'] = pd.to_numeric(p['Qty'], errors='coerce').fillna(0)
    p['Received'], p['PaidUnits'], p['PackUnits'], p['Inconsistent'] = fixed['Received'], fixed['PaidUnits'], fixed['PackUnits'], fixed['Inconsistent']
    p['Factor'] = p['PackUnits']
    # a bill with a missing / impossible date (swapped day-month: Feb, Mar, Nov, Dec) is counted inside the window
    month_start = pd.to_datetime(p['Source_Month'].astype(str) + '-01', errors='coerce')
    p['Day'] = p['Day'].fillna(month_start).clip(lower=start, upper=end)

    sold = s.groupby(['Product', 'Day'])['Qty'].sum().unstack('Day', fill_value=0).reindex(columns=days, fill_value=0)
    recv = p.groupby(['Product', 'Day'])['Received'].sum().unstack('Day', fill_value=0).reindex(columns=days, fill_value=0)
    prods = sold.index
    recv = recv.reindex(prods, fill_value=0)
    cum = (recv - sold).cumsum(axis=1)
    low = cum.min(axis=1).clip(upper=0)
    least = cum.iloc[:, -1] - low                                   # least stock the books allow, in units

    win = days[-demand_days:]
    in_win = s[s['Day'].isin(win)]
    sold_win = sold[win].sum(axis=1)                                # units sold / bought in the window, all branches
    bought_win = recv[win].sum(axis=1)
    got = p[p['Received'] > 0]                                      # the latest batch = every purchase line on a product's latest purchase day
    got = got[got['Day'] == got.groupby('Product')['Day'].transform('max')]
    batch_day = got.groupby('Product')['Day'].max().reindex(prods)
    batch_units = got.groupby('Product')['Received'].sum().reindex(prods)
    dem = in_win.groupby(['Product', 'Branch'])['Qty'].sum().unstack('Branch', fill_value=0).reindex(prods, fill_value=0)
    for b in BRANCHES:
        if b not in dem.columns:
            dem[b] = 0.0
    dem = dem[BRANCHES] / float(len(win))                           # units a day per branch
    d_total = dem.sum(axis=1)
    sale_days = in_win.groupby('Product')['Day'].nunique().reindex(prods, fill_value=0)

    # pack size and the latest purchase facts
    pl = p[p['Qty'] > 0].sort_values('Day', kind='stable')      # a free-only line has no price to learn from
    last = pl.groupby('Product').tail(1).set_index('Product')
    pack = last['PackUnits'].reindex(prods)          # units in the pack the supplier sends (a strip, or a carton for loose goods)
    pack = pack.fillna(pf.reindex(prods))
    if factor_of:
        pack = pack.fillna(pd.Series({k: factor_of.get(k, 1) for k in prods}))
    pack = pack.fillna(1).replace(0, 1)
    bad_share = (p['Inconsistent'] * p['Received']).groupby(p['Product']).sum() / p.groupby('Product')['Received'].sum().replace(0, np.nan)
    bad_share = bad_share.reindex(prods).fillna(0)
    unit_flag = bad_share > UNIT_HOLD_SHARE
    mrp_pack = _col(s, 'MRP').where(lambda m: m > 0).groupby(s['Product']).median().reindex(prods)   # the printed price of one pack
    supplier = last['Supplier'].reindex(prods) if 'Supplier' in last.columns else pd.Series(index=prods, dtype=object)
    cost_per_pack = (last['Item Total'] / last['Qty'].replace(0, np.nan)).reindex(prods)   # last price paid per paid pack (incl. tax)
    buy_days = p[p['Qty'] + p['Free'] > 0].groupby('Product')['Day'].nunique().reindex(prods, fill_value=0)
    tot_sold = sold.sum(axis=1)
    tot_recv = recv.sum(axis=1)
    ratio = (tot_recv / tot_sold.replace(0, np.nan)).fillna(0)

    out = []
    scheme_lines = {k: g for k, g in p[p['Free'] > 0][['Product', 'Day', 'Qty', 'Free', 'Supplier']].groupby('Product')}
    for prod in prods:
        d = float(d_total[prod])
        f = float(pack[prod])
        stock_units = float(least[prod]) + floor_days * d
        stock_packs = stock_units / f
        d_packs = d / f
        regular = int(sale_days[prod]) >= REGULAR_MIN_SALE_DAYS
        tgt = max(target_days * d_packs, 1.0 if (regular and d > 0) else 0.0)
        need = max(0.0, tgt - stock_packs) if d > 0 else 0.0
        sch = detect_scheme(scheme_lines.get(prod), end)
        if bool(unit_flag[prod]):                       # the purchase and sales units cannot be reconciled: no automatic order
            need = 0.0
        plan = plan_order(need, stock_packs, d_packs, sch, target_days)
        shares = allocate(plan['received'], [float(dem.loc[prod, b]) for b in BRANCHES])
        row = {'Product': prod, 'Pack_Size': f, 'Demand_Per_Day_Units': d, 'Monthly_Demand_Packs': d_packs * 30,
               'Least_Stock_Packs': float(least[prod]) / f, 'Stock_Est_Packs': stock_packs,
               'Cover_Now_Days': _cover(stock_packs, d_packs), 'Target_Packs': tgt, 'Need_Packs': need,
               'Offer': (f"{sch['base']}+{int(sch['free']) if float(sch['free']).is_integer() else sch['free']}" if sch else ''),
               'Offer_Supplier': sch['supplier'] if sch else '', 'Offer_Last_Seen': sch['last'] if sch else pd.NaT,
               'Order_Paid_Packs': plan['paid'], 'Order_Free_Packs': plan['free'], 'Order_Received_Packs': plan['received'],
               'Cover_After_Days': plan['cover_after'], 'How': plan['how'],
               'Est_Cost_Rs': (plan['paid'] * float(cost_per_pack[prod])) if pd.notna(cost_per_pack[prod]) else np.nan,
               'Received_vs_Sold': float(ratio[prod]), 'Buy_Days': int(buy_days[prod]),
               'Confidence': confidence(float(ratio[prod]), int(buy_days[prod]), int(buy_days[prod]) == 0),
               'Unit_Check': ('units inconsistent - order by hand' if bool(unit_flag[prod])
                              else 'some purchase lines odd (price change / other pack?)' if bad_share[prod] > UNIT_NOTE_SHARE else 'ok'),
               'Opening_Needed_Days': (float(-low[prod]) / d) if d > 0 else np.nan,
               'Excess_Packs_Over_Target': max(0.0, stock_packs - max(target_days * d_packs, 0.0)),
               'Last_Cost_Per_Pack': float(cost_per_pack[prod]) if pd.notna(cost_per_pack[prod]) else np.nan,
               'MRP_Per_Pack': float(mrp_pack[prod]) if pd.notna(mrp_pack[prod]) else np.nan,
               'Order_Value_At_MRP_Rs': (plan['paid'] * float(mrp_pack[prod])) if pd.notna(mrp_pack[prod]) else np.nan,
               'Last_Supplier': supplier[prod] if pd.notna(supplier[prod]) else '',
               'Offer_Base': sch['base'] if sch else 0, 'Offer_Free': sch['free'] if sch else 0.0,
               'Least_Stock_Units': float(least[prod]), 'Latest_Batch_Date': batch_day[prod], 'Latest_Batch_Units': batch_units[prod],
               'Bought_Window_Units': float(bought_win[prod]), 'Sold_Window_Units': float(sold_win[prod]),
               'Sale_Days_In_Window': int(sale_days[prod])}
        for b, sh in zip(BRANCHES, shares):
            row[f'Order_{b}'] = sh
            row[f'Demand_{b}_Per_Month_Packs'] = float(dem.loc[prod, b]) / f * 30
        out.append(row)
    df = pd.DataFrame(out)
    info = {'start': start, 'end': end, 'demand_days': len(win), 'target_days': target_days, 'floor_days': floor_days,
            'late_purchase_lines': int((pd.to_datetime(purch['Date'], format='mixed', errors='coerce') > end).sum()),
            'sales_lines_repaired': sales_lines_repaired, 'purchase_lines_inconsistent': int(p['Inconsistent'].sum()),
            'purchase_lines_carton': int((fixed['k'] >= 2).sum())}
    return df, info

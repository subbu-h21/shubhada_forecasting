"""Auto reorder workbook, branch-wise: python reorder_report.py  ->  reports/auto_reorder_<as-of date>.xlsx

Runs reorder.build_reorder on the reckoner's own sales and purchase data and writes the sheets the owner works from:
Read me, Order - all (one purchase order for the business), a sheet per branch (what each should receive from it),
Overstock, Count check (about 30 products to count by hand to test the stock estimate), Unit check, All products.
The reports/ folder is git-ignored (it holds business figures); nothing here is committed or sent anywhere.

Two more workbooks from the same data:  python reorder_report.py batch       -> the owner's batch rule (batch_rule_order_<date>.xlsx)
                                         python reorder_report.py distributor -> the same order, one sheet per distributor (order_by_distributor_<date>.xlsx)
"""
import os
import re
import sys

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink
from openpyxl.worksheet.properties import PageSetupProperties

import ask_tools as T
import reorder as R
import run_reckoner as rk

HERE = os.path.dirname(os.path.abspath(__file__))
HEAD_FILL = PatternFill('solid', fgColor='1F4E5F')
HEAD_FONT = Font(bold=True, color='FFFFFF')
SHEET_OF = {'Shivaji Chowk': 'Shivaji Chowk', 'Hospet Road': 'Hospet Road', 'Herur Branch': 'Herur', 'B2B / Wholesale': 'B2B'}
COUNT_PER_CLASS = 10
NO_PRICE_COST_OF_MRP = 0.75         # a product with no purchase since April: cost is guessed as this share of its MRP


def note_for(r):
    notes = []
    if r['Unit_Check'] != 'ok':
        notes.append(r['Unit_Check'])
    if r['How'] == 'loose (offer too big)':
        notes.append(f"offer {r['Offer']} would leave too much - bought loose")
    if r['Cover_After_Days'] > 40:
        notes.append(f"slow seller: one pack lasts about {r['Cover_After_Days']:.0f} days")
    if pd.isna(r['Last_Cost_Per_Pack']):
        notes.append(f'not bought since April - cost taken as {int(NO_PRICE_COST_OF_MRP * 100)}% of MRP')
    if r['Confidence'].startswith('Low'):
        notes.append('count the rack before ordering')
    return '; '.join(notes)


def orders_from(live):
    """The products to order, each with its cost (a product never bought since April: a share of its MRP), by supplier."""
    orders = live[live['Order_Paid_Packs'] > 0].copy()
    orders['Order_Value_Rs'] = orders['Est_Cost_Rs'].fillna(orders['Order_Value_At_MRP_Rs'] * NO_PRICE_COST_OF_MRP)
    return orders.sort_values(['Last_Supplier', 'Product'])


def write_sheet(writer, name, df, widths=None, number_formats=None, wrap=()):
    df.to_excel(writer, sheet_name=name, index=False)
    ws = writer.sheets[name]
    for c in ws[1]:
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(wrap_text=True, vertical='center')
    ws.row_dimensions[1].height = 42
    ws.freeze_panes = 'B2'
    if len(df):
        ws.auto_filter.ref = ws.dimensions
    for i, col in enumerate(df.columns, start=1):
        w = (widths or {}).get(col)
        if w is None:
            body = df[col].dropna().astype(str).str.len()
            w = min(46, max(10, (int(body.quantile(0.95)) + 2) if len(body) else 10, len(str(col)) * 0.6))
        ws.column_dimensions[get_column_letter(i)].width = w
        fmt = (number_formats or {}).get(col)
        if fmt:
            for cell in ws[get_column_letter(i)][1:]:
                cell.number_format = fmt
        if col in wrap:
            for cell in ws[get_column_letter(i)][1:]:
                cell.alignment = Alignment(wrap_text=True, vertical='top')
    return ws


def build(out_dir=None):
    sales, purch = T._load()
    as_of, facts = R.data_as_of(sales, purch)
    df, info = R.build_reorder(sales, purch, rk.extract_branch, factor_of=T._factor_map(), as_of=as_of)
    s = sales.copy()
    s['Day'] = pd.to_datetime(s['Date'], format='mixed', errors='coerce').dt.normalize()
    s['Product'] = s['Product'].astype('string').str.strip()
    win_start = as_of - pd.Timedelta(days=R.DEMAND_DAYS - 1)
    sales_3m = s[(s['Day'] >= win_start) & (s['Day'] <= as_of)].groupby('Product')['Item Total'].sum()
    df['Sales_Last_92_Days_Rs'] = df['Product'].map(sales_3m).fillna(0)
    live = df[(df['Demand_Per_Day_Units'] > 0) & ~df['Product'].str.upper().str.startswith('XXXX')].copy()      # XXXX... are placeholder names, not products
    live['Last_Supplier'] = live['Last_Supplier'].replace('', R.NOT_BOUGHT)
    live['Note'] = live.apply(note_for, axis=1)
    live['Cash_Above_Target_Rs'] = live['Excess_Packs_Over_Target'] * live['Last_Cost_Per_Pack']

    orders = orders_from(live)

    # ---- Order - all
    cols = {'Last_Supplier': 'Last supplier', 'Product': 'Product', 'Pack_Size': 'Units in a pack', 'Monthly_Demand_Packs': 'Sells per month (packs)',
            'Stock_Est_Packs': 'Stock now (packs, estimate)', 'Cover_Now_Days': 'Cover now (days)', 'Offer': 'Offer', 'Order_Paid_Packs': 'ORDER (packs to pay for)',
            'Order_Free_Packs': 'Free packs', 'Order_Received_Packs': 'You receive (packs)', 'Cover_After_Days': 'Cover after (days)', 'How': 'How',
            'Order_Value_Rs': 'Cost (Rs)', 'Order_Shivaji Chowk': 'Shivaji Chowk', 'Order_Hospet Road': 'Hospet Road', 'Order_Herur Branch': 'Herur',
            'Order_B2B / Wholesale': 'B2B', 'Confidence': 'Trust in stock estimate', 'Note': 'Note'}
    order_all = orders[list(cols)].rename(columns=cols)
    one_dec = '#,##0.0'
    fmts = {'Sells per month (packs)': one_dec, 'Stock now (packs, estimate)': one_dec, 'Cover now (days)': '0', 'Cover after (days)': '0', 'Cost (Rs)': '#,##0',
            'Units in a pack': '#,##0', 'ORDER (packs to pay for)': '#,##0', 'Free packs': '#,##0', 'You receive (packs)': '#,##0'}

    # ---- a sheet per branch
    branch_sheets = {}
    for b in R.BRANCHES:
        d = orders[orders[f'Order_{b}'] > 0].copy()
        total_d = d['Demand_Per_Day_Units'] * 30 / d['Pack_Size']
        d['Share'] = np.where(total_d > 0, d[f'Demand_{b}_Per_Month_Packs'] / total_d, 0)
        d = d.sort_values('Product')
        branch_sheets[b] = d[['Product', 'Pack_Size', f'Demand_{b}_Per_Month_Packs', 'Share', f'Order_{b}', 'Order_Received_Packs', 'Offer', 'Confidence', 'Note']].rename(columns={
            'Pack_Size': 'Units in a pack', f'Demand_{b}_Per_Month_Packs': 'This branch sells per month (packs)', 'Share': 'Share of all sales',
            f'Order_{b}': 'PACKS FOR THIS BRANCH', 'Order_Received_Packs': 'Whole order (packs)', 'Confidence': 'Trust in stock estimate'})

    # ---- overstock
    over = live[(live['Cover_Now_Days'] > 30) & (live['Monthly_Demand_Packs'] >= 0.5) & (live['Cash_Above_Target_Rs'] >= 500)].sort_values('Cash_Above_Target_Rs', ascending=False)
    overstock = over[['Product', 'Pack_Size', 'Monthly_Demand_Packs', 'Stock_Est_Packs', 'Cover_Now_Days', 'Target_Packs', 'Excess_Packs_Over_Target', 'Cash_Above_Target_Rs',
                      'Received_vs_Sold', 'Confidence', 'Last_Supplier']].rename(columns={
        'Pack_Size': 'Units in a pack', 'Monthly_Demand_Packs': 'Sells per month (packs)', 'Stock_Est_Packs': 'Stock now (packs, at least)', 'Cover_Now_Days': 'Cover now (days)',
        'Target_Packs': '15-day target (packs)', 'Excess_Packs_Over_Target': 'Packs above target', 'Cash_Above_Target_Rs': 'Cash above target (Rs, at last cost)',
        'Received_vs_Sold': 'Bought / sold', 'Confidence': 'Trust in stock estimate', 'Last_Supplier': 'Last supplier'})
    over_fmts = {'Sells per month (packs)': one_dec, 'Stock now (packs, at least)': one_dec, 'Cover now (days)': '0', '15-day target (packs)': one_dec, 'Packs above target': one_dec,
                 'Cash above target (Rs, at last cost)': '#,##0', 'Bought / sold': '0.00'}

    # ---- count check: a spread of products to count by hand
    pool = live[(live['Monthly_Demand_Packs'] >= 3) & (live['Unit_Check'] == 'ok') & (live['Sales_Last_92_Days_Rs'] > 0)]
    picks = []
    for label, sub in (('Order today (High trust)', pool[(pool['Order_Paid_Packs'] > 0) & (pool['Confidence'] == 'High')]),
                       ('No order - plenty in stock', pool[(pool['Order_Paid_Packs'] == 0) & (pool['Cover_Now_Days'] > 30) & (pool['Confidence'] == 'High')]),
                       ('Count the rack first (Low trust)', pool[pool['Confidence'].str.startswith('Low')])):
        sub = sub.sample(n=min(COUNT_PER_CLASS, len(sub)), random_state=7) if len(sub) else sub
        picks.append(sub.assign(Why=label))
    chk = pd.concat(picks)
    chk = chk[['Product', 'Pack_Size', 'Why', 'Least_Stock_Packs', 'Stock_Est_Packs', 'Cover_Now_Days']].rename(columns={
        'Pack_Size': 'Units in a pack', 'Why': 'Why this product', 'Least_Stock_Packs': 'Books say at least (packs)', 'Stock_Est_Packs': 'Estimate with 3-day floor (packs)',
        'Cover_Now_Days': 'Cover now (days)'})
    chk['Counted now (packs, ALL shops + store)'] = None
    chk['Difference'] = None
    chk_fmts = {'Books say at least (packs)': one_dec, 'Estimate with 3-day floor (packs)': one_dec, 'Cover now (days)': '0'}

    # ---- unit check
    odd = live[live['Unit_Check'] != 'ok'].sort_values('Sales_Last_92_Days_Rs', ascending=False)
    unit = odd[['Product', 'Unit_Check', 'Sales_Last_92_Days_Rs', 'Pack_Size', 'Received_vs_Sold']].rename(columns={
        'Unit_Check': 'What is odd', 'Sales_Last_92_Days_Rs': 'Sales, last 92 days (Rs)', 'Pack_Size': 'Units in a pack used', 'Received_vs_Sold': 'Bought / sold'})
    unit_fmts = {'Sales, last 92 days (Rs)': '#,##0', 'Bought / sold': '0.00', 'Units in a pack used': '#,##0'}

    # ---- all products
    allp = live.sort_values('Product')[['Product', 'Pack_Size', 'Monthly_Demand_Packs', 'Stock_Est_Packs', 'Cover_Now_Days', 'Target_Packs', 'Need_Packs', 'Offer',
                                        'Order_Paid_Packs', 'Order_Free_Packs', 'Cover_After_Days', 'Confidence', 'Unit_Check']].rename(columns={
        'Pack_Size': 'Units in a pack', 'Monthly_Demand_Packs': 'Sells per month (packs)', 'Stock_Est_Packs': 'Stock now (packs, estimate)', 'Cover_Now_Days': 'Cover now (days)',
        'Target_Packs': '15-day target (packs)', 'Need_Packs': 'Need (packs)', 'Order_Paid_Packs': 'ORDER (packs to pay for)', 'Order_Free_Packs': 'Free packs',
        'Cover_After_Days': 'Cover after (days)', 'Confidence': 'Trust in stock estimate', 'Unit_Check': 'Unit check'})
    all_fmts = {'Sells per month (packs)': one_dec, 'Stock now (packs, estimate)': one_dec, 'Cover now (days)': '0', '15-day target (packs)': one_dec, 'Need (packs)': one_dec, 'Cover after (days)': '0'}

    # ---- read me
    reg = live[live['Monthly_Demand_Packs'] >= 2]
    n_orders = len(orders)
    cost_known = orders['Est_Cost_Rs'].sum()
    no_price = int(orders['Est_Cost_Rs'].isna().sum())
    cost_all = orders['Order_Value_Rs'].sum()
    days_in = (as_of - info['start']).days + 1
    bought = purch.loc[pd.to_datetime(purch['Date'], format='mixed', errors='coerce') <= as_of, 'Item Total'].sum()
    sold_rs = s.loc[s['Day'] <= as_of, 'Item Total'].sum()
    per_day = bought / days_in
    herur = sales[sales['Inv.No'].apply(rk.extract_branch) == 'Herur Branch']
    lines = [
        ('AUTO REORDER - 15 days of stock, branch-wise (DRAFT for the owner to confirm)', 'title'),
        (f"Sales and purchases are both complete up to {as_of:%d %b %Y} (sales files run to {facts['sales_through']:%d %b}, purchase files to {facts['purchases_through']:%d %b}; "
         f"the later days are left out because stock can only be worked out where both are in). Window starts {info['start']:%d %b %Y}.", ''),
        ('', ''),
        ('THE FORMULA, for every product', 'h'),
        (f"1. DEMAND - sales of the last {R.DEMAND_DAYS} days divided by {R.DEMAND_DAYS} = units sold a day. Done per branch (Shivaji, Hospet, Herur, B2B); the business's demand is the sum.", ''),
        ("2. STOCK - no stock report exists, so stock is worked out from the books: every pack received (paid + free) minus every sale, day by day since 1 April. "
         "Stock before April is unknown, but the balance can never have been below zero, so the books tell the LEAST stock you must be holding now. "
         f"Then {R.FLOOR_DAYS} days of demand are added (stock normally left just before a delivery arrives).", ''),
        (f"3. TARGET - {R.TARGET_DAYS} days of demand, in packs; a product that sells regularly always keeps at least 1 pack.", ''),
        ("4. NEED = target - stock (nothing if it is less than half a pack).", ''),
        ("5. OFFERS - if the product was bought on an offer in the last 4 months (5+1, 10+1, 10+2, 9+1 ...), the order is a whole number of offers: pay for 5 or 10 and get 1 or 2 free. "
         f"Of the two nearest choices the one that leaves stock closest to {R.TARGET_DAYS} days is taken, never more than {R.MAX_COVER_DAYS} days. If even that is too much for a slow seller, it is bought loose and marked 'offer too big'.", ''),
        ("6. BRANCHES - the packs received are shared to Shivaji / Hospet / Herur / B2B in proportion to what each sells (whole packs). The purchase is booked at one place and goods are transferred; "
         "the branch sheets say how many packs each should receive from the order.", ''),
        ('', ''),
        ('WHAT THE NUMBERS SAY TODAY', 'h'),
        (f"Products that sold in the last {R.DEMAND_DAYS} days: {len(live):,}.  Products to order today: {n_orders:,}, costing about Rs {cost_all:,.0f} "
         f"(Rs {cost_known:,.0f} at last purchase price; the other {no_price} were not bought since April, so their cost is guessed at {int(NO_PRICE_COST_OF_MRP * 100)}% of MRP).", ''),
        (f"That is about {cost_all / per_day:.1f} days of the usual buying (Rs {per_day:,.0f} a day since April). Most products already hold more than {R.TARGET_DAYS} days, so buying stays light for a few weeks while stock settles to {R.TARGET_DAYS} days.", ''),
        (f"Of the {len(reg):,} products that sell 2 or more packs a month, {100 * (reg['Cover_Now_Days'] > R.TARGET_DAYS).mean():.0f}% already hold more than {R.TARGET_DAYS} days and "
         f"{100 * (reg['Cover_Now_Days'] > 30).mean():.0f}% more than 30 days. Cash held above the {R.TARGET_DAYS}-day target is at least Rs {live['Cash_Above_Target_Rs'].sum():,.0f} across all products; "
         f"the 'Overstock' sheet lists the ones holding more than 30 days (Rs {over['Cash_Above_Target_Rs'].sum():,.0f}). Both are MINIMUMS, because the stock figure is a lower bound.", ''),
        (f"HERUR: only {len(herur)} Herur bill lines exist in the sales files (Rs {herur['Item Total'].sum():,.0f} since April), so Herur's own demand is invisible and it gets no packs below. Goods transferred to Herur and sold there "
         "show up here as stock that is still on the shelf, so the overstock figures include them. If Herur is billed elsewhere, its sales need to come into the reckoner for this to be right.", ''),
        (f"For scale, 1 April to {as_of:%d %b}: purchases Rs {bought:,.0f} (at cost) and sales Rs {sold_rs:,.0f}.", ''),
        ('', ''),
        ('HOW MUCH TO TRUST IT', 'h'),
        ("High - bought often and about what was sold: the estimate is good.  Medium - bought a few times.  Low - sales were mostly met from stock held before April, or the product was hardly bought: "
         "COUNT THE RACK before ordering.", ''),
        ("The stock figure cannot see: goods returned to suppliers, expired or damaged goods written off, or goods sold under another name. Each makes true stock lower than the books say.", ''),
        ("Sheet 'Count check' lists 30 products to count by hand (all shops + store). The count can never be LESS than 'Books say at least'; if it is, goods were returned, expired, damaged or sold under another name - "
         "tell me by how much and I will adjust the stock rule.", ''),
        ('', ''),
        ('ASSUMPTIONS TO CONFIRM', 'h'),
        (f"a) {R.FLOOR_DAYS} days of stock is normally left just before a delivery.   b) Demand = the last {R.DEMAND_DAYS} days.   c) An offer may leave up to {R.MAX_COVER_DAYS} days of stock.   "
         "d) B2B and Herur take their goods from the same pool as the shops.   e) Stock is shared to the branches by sales share (no transfer records exist).   f) A regular seller keeps at least 1 pack.", ''),
        (f"Units: {info['sales_lines_repaired']:,} sales lines billed per strip with Factor 1 were converted to tablets; {info['purchase_lines_carton']:,} purchase lines whose MRP is a whole-number multiple "
         f"of the strip MRP were read as cartons; {info['purchase_lines_inconsistent']:,} purchase lines had an MRP that fits no pack (a price change or a different pack) - products where most of the stock is like that "
         "are not ordered automatically (sheet 'Unit check').", ''),
    ]
    return df, live, order_all, fmts, branch_sheets, overstock, over_fmts, chk, chk_fmts, unit, unit_fmts, allp, all_fmts, lines, as_of


def main():
    (df, live, order_all, fmts, branch_sheets, overstock, over_fmts, chk, chk_fmts, unit, unit_fmts, allp, all_fmts, lines, as_of) = build()
    out_dir = os.path.join(HERE, 'reports')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f'auto_reorder_{as_of:%Y-%m-%d}.xlsx')
    with pd.ExcelWriter(path, engine='openpyxl') as w:
        pd.DataFrame({'Read me': [t for t, _ in lines]}).to_excel(w, sheet_name='Read me', index=False)
        ws = w.sheets['Read me']
        ws.column_dimensions['A'].width = 150
        for row, (_, kind) in zip(ws.iter_rows(min_row=2), lines):
            c = row[0]
            c.alignment = Alignment(wrap_text=True, vertical='top')
            if kind == 'title':
                c.font = Font(bold=True, size=14)
            elif kind == 'h':
                c.font = Font(bold=True, color='FFFFFF')
                c.fill = HEAD_FILL
        ws['A1'].font = HEAD_FONT
        ws['A1'].fill = HEAD_FILL
        write_sheet(w, 'Order - all', order_all, widths={'Product': 36, 'Last supplier': 26, 'Note': 52, 'How': 20, 'Trust in stock estimate': 24}, number_formats=fmts)
        for b, d in branch_sheets.items():
            if d.empty:
                continue                                    # a branch with no sales in the files (Herur) gets no sheet
            write_sheet(w, SHEET_OF[b], d, widths={'Product': 38, 'Note': 52, 'Trust in stock estimate': 24},
                        number_formats={'This branch sells per month (packs)': '#,##0.0', 'Share of all sales': '0%'})
        write_sheet(w, 'Overstock', overstock, widths={'Product': 38, 'Last supplier': 26, 'Trust in stock estimate': 24}, number_formats=over_fmts)
        ws = write_sheet(w, 'Count check', chk, widths={'Product': 38, 'Why this product': 32}, number_formats=chk_fmts)
        n = len(chk)
        diff_col = get_column_letter(list(chk.columns).index('Difference') + 1)
        cnt_col = get_column_letter(list(chk.columns).index('Counted now (packs, ALL shops + store)') + 1)
        est_col = get_column_letter(list(chk.columns).index('Books say at least (packs)') + 1)
        for r in range(2, n + 2):
            ws[f'{diff_col}{r}'] = f'=IF({cnt_col}{r}="","",{cnt_col}{r}-{est_col}{r})'
        write_sheet(w, 'Unit check', unit, widths={'Product': 38, 'What is odd': 52}, number_formats=unit_fmts)
        write_sheet(w, 'All products', allp, widths={'Product': 38, 'Unit check': 40, 'Trust in stock estimate': 24}, number_formats=all_fmts)
    print(path)
    return path


# ---------------------------------------------------------------------------------------------
# The owner's batch rule: python reorder_report.py batch  ->  reports/batch_rule_order_<as-of date>.xlsx
# ---------------------------------------------------------------------------------------------
def batch_note(r):
    notes = []
    if r['Batch_Cover_After_Days'] > 40:
        notes.append(f"slow seller: stock after the order lasts {r['Batch_Cover_After_Days']:.0f} days")
    if r['Batch_How'] == 'loose (offer too big)':
        notes.append(f"offer {r['Offer']} would be too much - bought loose")
    if pd.isna(r['Last_Cost_Per_Pack']):
        notes.append('not bought since April - no price')
    if r['Confidence'].startswith('Low'):
        notes.append('stock is a guess - count the rack')
    return '; '.join(notes)


def batch_main():
    sales, purch = T._load()
    as_of, facts = R.data_as_of(sales, purch)
    df, info = R.build_reorder(sales, purch, rk.extract_branch, factor_of=T._factor_map(), as_of=as_of, demand_days=R.BATCH_DEMAND_DAYS)
    b = R.batch_rule(df)
    b = b[~b['Product'].str.upper().str.startswith('XXXX')].copy()
    b['Last_Supplier'] = b['Last_Supplier'].replace('', R.NOT_BOUGHT)
    b['Stock_Left_Packs'] = b['Least_Stock_Units'] / b['Pack_Size']
    b['Note'] = b.apply(batch_note, axis=1)
    orders = b[b['Batch_Status'] == 'ORDER'].sort_values(['Last_Supplier', 'Product'])
    formula = b[b['Order_Paid_Packs'] > 0]
    both = orders[orders['Order_Paid_Packs'] > 0]
    one_dec = '#,##0.0'

    names = {'Last_Supplier': 'Last supplier', 'Product': 'Product', 'Pack_Size': 'Units in a pack', 'Monthly_Demand_Packs': 'Sells per month (packs)',
             'Latest_Batch_Date': 'Latest batch arrived', 'Batch_Sold_Share': 'Latest batch sold', 'Stock_Left_Packs': 'Stock left (packs, at least)',
             'Sell_Through_Window': '90-day sold / bought', 'Offer': 'Offer', 'Batch_Order_Paid_Packs': 'ORDER (packs to pay for)', 'Batch_Order_Free_Packs': 'Free packs',
             'Batch_Order_Received_Packs': 'You receive (packs)', 'Batch_Cover_After_Days': 'Cover after (days)', 'Batch_How': 'How', 'Batch_Est_Cost_Rs': 'Cost (Rs)',
             'Batch_Order_Shivaji Chowk': 'Shivaji Chowk', 'Batch_Order_Hospet Road': 'Hospet Road', 'Batch_Order_Herur Branch': 'Herur', 'Batch_Order_B2B / Wholesale': 'B2B',
             'Confidence': 'Trust in stock estimate', 'Note': 'Note'}
    order_all = orders[list(names)].rename(columns=names)
    order_all['90-day sold / bought'] = order_all['90-day sold / bought'].replace(np.inf, np.nan)
    fmts = {'Sells per month (packs)': one_dec, 'Latest batch arrived': 'dd-mmm-yy', 'Latest batch sold': '0%', 'Stock left (packs, at least)': one_dec,
            '90-day sold / bought': '0%', 'Cover after (days)': '0', 'Cost (Rs)': '#,##0', 'Units in a pack': '#,##0'}

    branch_sheets = {}
    for br in R.BRANCHES:
        d = orders[orders[f'Batch_Order_{br}'] > 0].sort_values('Product')
        branch_sheets[br] = d[['Product', 'Pack_Size', f'Demand_{br}_Per_Month_Packs', f'Batch_Order_{br}', 'Batch_Order_Received_Packs', 'Offer', 'Confidence', 'Note']].rename(columns={
            'Pack_Size': 'Units in a pack', f'Demand_{br}_Per_Month_Packs': 'This branch sells per month (packs)', f'Batch_Order_{br}': 'PACKS FOR THIS BRANCH',
            'Batch_Order_Received_Packs': 'Whole order (packs)', 'Confidence': 'Trust in stock estimate'})

    decide = b[b['Batch_Status'].str.contains('owner decides|order by hand')].sort_values('Monthly_Demand_Packs', ascending=False)
    decide = decide[['Product', 'Pack_Size', 'Monthly_Demand_Packs', 'Sale_Days_In_Window', 'Batch_Sold_Share', 'Batch_Status']].rename(columns={
        'Pack_Size': 'Units in a pack', 'Monthly_Demand_Packs': 'Sells per month (packs)', 'Sale_Days_In_Window': 'Days with a sale (of 90)', 'Batch_Sold_Share': 'Latest batch sold',
        'Batch_Status': 'Why not in the order'})
    nob = b[b['Batch_Status'].str.startswith('no purchase batch') & (b['Monthly_Demand_Packs'] > 0)].sort_values('Monthly_Demand_Packs', ascending=False)
    nob = nob[['Product', 'Pack_Size', 'Monthly_Demand_Packs', 'Sale_Days_In_Window', 'MRP_Per_Pack']].rename(columns={
        'Pack_Size': 'Units in a pack', 'Monthly_Demand_Packs': 'Sells per month (packs)', 'Sale_Days_In_Window': 'Days with a sale (of 90)', 'MRP_Per_Pack': 'MRP per pack (Rs)'})

    cmp_rows = []
    for label, d in (('Batch rule orders, 15-day formula does not', orders[orders['Order_Paid_Packs'] == 0]),
                     ('15-day formula orders, batch rule does not', formula[formula['Batch_Status'] != 'ORDER'])):
        x = d[['Product', 'Pack_Size', 'Monthly_Demand_Packs', 'Cover_Now_Days', 'Latest_Batch_Units', 'Batch_Sold_Share', 'Batch_Order_Paid_Packs', 'Order_Paid_Packs',
               'Batch_Status', 'Confidence']].copy()
        x.insert(0, 'Difference', label)
        cmp_rows.append(x)
    cmp = pd.concat(cmp_rows).sort_values(['Difference', 'Monthly_Demand_Packs'], ascending=[True, False]).rename(columns={
        'Pack_Size': 'Units in a pack', 'Monthly_Demand_Packs': 'Sells per month (packs)', 'Cover_Now_Days': 'Cover now (days, 15-day formula)', 'Latest_Batch_Units': 'Latest batch (units)',
        'Batch_Sold_Share': 'Latest batch sold', 'Batch_Order_Paid_Packs': 'Batch rule: order (packs)', 'Order_Paid_Packs': '15-day formula: order (packs)',
        'Batch_Status': 'Batch rule status', 'Confidence': 'Trust in stock estimate'})

    reg = b[(b['Monthly_Demand_Packs'] >= 2) & (b['Sale_Days_In_Window'] >= R.REGULAR_MIN_SALE_DAYS) & b['Latest_Batch_Units'].notna() & (b['Unit_Check'] == 'ok')].copy()
    reg['trigger_days'] = (1 - R.BATCH_SOLD_TRIGGER) * reg['Latest_Batch_Units'] / reg['Pack_Size'] / (reg['Monthly_Demand_Packs'] / 30)
    short = b['Batch_Status'].str.replace(r'^not triggered.*', 'latest batch less than 81% sold (not yet)', regex=True)
    short = short.str.replace(r'^triggered, NOT confirmed.*', 'triggered but 90-day sold/bought under 81%', regex=True)
    short = short.str.replace(r'^triggered and confirmed, but sold on fewer.*', 'triggered, but sold on under 3 days - owner decides', regex=True)
    short = short.str.replace(r'^triggered and confirmed, but units.*', 'triggered, but units inconsistent - order by hand', regex=True)
    status = short.value_counts()
    rejected = int(b['Batch_Status'].str.startswith('triggered, NOT confirmed').sum())
    lines = [
        ("BATCH RULE - the owner's rule applied to the sales and purchase files (DRAFT: the files have no batch numbers)", 'title'),
        (f"Stock as on {as_of:%d %b %Y} (sales are in to {facts['sales_through']:%d %b}, purchases to {facts['purchases_through']:%d %b}; the later days are left out). "
         f"Window: the last {R.BATCH_DEMAND_DAYS} days.", ''),
        ('', ''),
        ('THE RULE, as I read it', 'h'),
        (f"1. CANDIDATE - the latest batch of the product is {int(R.BATCH_SOLD_TRIGGER * 100)}% or more sold (batch quantity - batch sale has fallen to "
         f"{int(round((1 - R.BATCH_SOLD_TRIGGER) * 100))}% or less of the quantity bought).", ''),
        (f"2. CONFIRMED - over the last {R.BATCH_DEMAND_DAYS} days, all batches sold / all batches bought is also {int(R.BATCH_SELL_THROUGH * 100)}% or more.", ''),
        (f"3. ORDER - sales of the last {R.BATCH_DEMAND_DAYS} days / {R.BATCH_DEMAND_DAYS} = one day of demand; x {R.BATCH_ORDER_DAYS} = the {R.BATCH_ORDER_DAYS}-day order. "
         "It is NOT reduced by the stock still left. Offers: a whole number of offers, the quantity nearest to the 15 days (5+1: pay 5, 10 or 15 ...), never leaving more than 30 days.", ''),
        ('', ''),
        ('WHAT THE FILES CANNOT GIVE (and what I assumed)', 'h'),
        ("The sales and purchase reports have no batch number or expiry. So: a batch = one delivery (all purchase lines of the product on its latest purchase day, free units included), "
         "and sales are assumed to use the OLDEST stock first. Then the latest batch is touched only when older stock is gone, and 'latest batch 81% sold' means "
         "'all the stock left is 19% or less of the latest batch'.", ''),
        ("The stock left is the books' minimum (bought - sold since April; stock held before April is unknown). Where the rack held more, the real batch is less sold than shown - "
         "see 'Trust in stock estimate'.", ''),
        ("'At the time of batch selection' (the moment a bill picks the batch) cannot be seen in the files; the rule is checked on the stock as on the date above.", ''),
        ('', ''),
        ('RESULT TODAY', 'h'),
        (f"Products to order: {len(orders):,}, about Rs {orders['Batch_Est_Cost_Rs'].sum():,.0f}. The 15-day-target formula (same {R.BATCH_DEMAND_DAYS}-day demand) orders "
         f"{len(formula):,} products, about Rs {formula['Est_Cost_Rs'].sum():,.0f}; {len(both):,} are on both lists, {len(orders) - len(both):,} only on the batch rule's, "
         f"{len(formula) - len(both):,} only on the formula's (sheet 'Compare').", ''),
        ('Where every product stands: ' + '; '.join(f'{k}: {v:,}' for k, v in status.items()) + '.', ''),
        (f"WHEN IT FIRES: the 81% trigger fires when the stock falls to 19% of the latest batch. For products that sell 2+ packs a month that is a median of "
         f"{reg['trigger_days'].median():.1f} days of stock; {100 * (reg['trigger_days'] < 5).mean():.0f}% of them fire with under 5 days left (little time for the delivery), "
         f"{100 * (reg['trigger_days'] > 15).mean():.0f}% fire with more than 15 days left (early). The size of the latest batch decides - a small top-up purchase fires late, "
         "a big bulk purchase fires early.", ''),
        (f"The 90-day confirmation rejected {rejected} products: when the stock is nearly gone, selling 81% of what was bought follows by itself (with these files). "
         "With real batch numbers it would catch the case where an old batch is still on the shelf while the newer one sells.", ''),
    ]
    out_dir = os.path.join(HERE, 'reports')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f'batch_rule_order_{as_of:%Y-%m-%d}.xlsx')
    with pd.ExcelWriter(path, engine='openpyxl') as w:
        pd.DataFrame({'Read me': [t for t, _ in lines]}).to_excel(w, sheet_name='Read me', index=False)
        ws = w.sheets['Read me']
        ws.column_dimensions['A'].width = 150
        for row, (_, kind) in zip(ws.iter_rows(min_row=2), lines):
            c = row[0]
            c.alignment = Alignment(wrap_text=True, vertical='top')
            if kind == 'title':
                c.font = Font(bold=True, size=14)
            elif kind == 'h':
                c.font, c.fill = Font(bold=True, color='FFFFFF'), HEAD_FILL
        ws['A1'].font, ws['A1'].fill = HEAD_FONT, HEAD_FILL
        write_sheet(w, 'Order - all', order_all, widths={'Product': 36, 'Last supplier': 26, 'Note': 52, 'How': 20, 'Trust in stock estimate': 24}, number_formats=fmts)
        for br, d in branch_sheets.items():
            if not d.empty:
                write_sheet(w, SHEET_OF[br], d, widths={'Product': 38, 'Note': 52, 'Trust in stock estimate': 24}, number_formats={'This branch sells per month (packs)': one_dec})
        write_sheet(w, 'Owner decides', decide, widths={'Product': 38, 'Why not in the order': 70}, number_formats={'Sells per month (packs)': one_dec, 'Latest batch sold': '0%'})
        write_sheet(w, 'No batch in files', nob, widths={'Product': 38}, number_formats={'Sells per month (packs)': one_dec, 'MRP per pack (Rs)': '#,##0.00'})
        write_sheet(w, 'Compare', cmp, widths={'Product': 36, 'Difference': 44, 'Batch rule status': 60, 'Trust in stock estimate': 24},
                    number_formats={'Sells per month (packs)': one_dec, 'Cover now (days, 15-day formula)': '0', 'Latest batch sold': '0%', 'Latest batch (units)': '#,##0'})
    print(path)
    return path


# ---------------------------------------------------------------------------------------------
# The order, one sheet per distributor: python reorder_report.py distributor  ->  reports/order_by_distributor_<as-of date>.xlsx
# ---------------------------------------------------------------------------------------------
SHOP = 'Shubhada Pharma'
INTERNAL_FILL = PatternFill('solid', fgColor='7F7F7F')
NO_DISTRIBUTOR_SHEET = 'No distributor yet'
FORM_COLS = ['Sl', 'Product', 'Units in a pack', 'Offer', 'ORDER (packs to pay for)', 'Free packs', 'Total packs']          # columns A-G: what goes to the distributor
INTERNAL_COLS = ['Cost (Rs)', 'Shivaji Chowk', 'Hospet Road', 'Herur', 'B2B', 'Trust in stock estimate', 'Note']          # columns I-O: for the owner only
INTERNAL_WIDTHS = [11, 10, 10, 8, 8, 24, 56]


def distributor_sheet_name(name, used):
    """An Excel sheet name (31 characters, none of []:*?/\\) that no earlier sheet in `used` has."""
    base = re.sub(r'[\[\]:*?/\\]', ' ', str(name))[:31].strip(" '") or 'Distributor'
    out, n = base, 2
    while out.lower() in used:
        tail = f' ({n})'
        out, n = base[:31 - len(tail)] + tail, n + 1
    used.add(out.lower())
    return out


def _whole(x):
    return int(x) if float(x).is_integer() else float(x)


def _rs(x):
    return None if pd.isna(x) else round(float(x))


def _goto(cell, sheet):
    """Make the cell a link to the top of another sheet of the same workbook."""
    cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f"'{sheet}'!A1")
    cell.style = 'Hyperlink'


def write_order_sheet(wb, sheet, distributor, d, as_of, made_on, choose_distributor=False):
    """One distributor's order form. Columns A-G are the print area (what the distributor needs); I-O are the owner's own."""
    ws = wb.create_sheet(sheet)
    ws['A1'] = 'PRODUCTS NOT BOUGHT SINCE APRIL - choose a distributor for each' if choose_distributor else f'PURCHASE ORDER - {distributor}'
    ws['A1'].font = Font(bold=True, size=14)
    ws['A2'] = f'{SHOP}   |   prepared {made_on:%d %b %Y}   |   stock worked out as on {as_of:%d %b %Y}'
    ws['A3'] = f'{len(d)} product{"s" if len(d) != 1 else ""}'
    ws['I1'] = '< Summary'
    _goto(ws['I1'], 'Summary')
    ws['I4'] = 'FOR THE OWNER ONLY - not part of the print'
    ws['I4'].font = Font(italic=True, color='7F7F7F')
    head = 5
    for i, h in enumerate(FORM_COLS, start=1):
        c = ws.cell(row=head, column=i, value=h)
        c.fill, c.font, c.alignment = HEAD_FILL, HEAD_FONT, Alignment(wrap_text=True, vertical='center', horizontal='center' if i != 2 else 'left')
    for i, h in enumerate(INTERNAL_COLS, start=9):
        c = ws.cell(row=head, column=i, value=h)
        c.fill, c.font, c.alignment = INTERNAL_FILL, HEAD_FONT, Alignment(wrap_text=True, vertical='center')
    if choose_distributor:
        c = ws.cell(row=head, column=8, value='Distributor (you choose)')
        c.fill, c.font, c.alignment = INTERNAL_FILL, HEAD_FONT, Alignment(wrap_text=True, vertical='center')
    ws.row_dimensions[head].height = 42
    for n, (_, r) in enumerate(d.iterrows(), start=1):
        row = head + n
        values = [n, r['Product'], _whole(r['Pack_Size']), r['Offer'] if r['How'] == 'offer' else '', _whole(r['Order_Paid_Packs']), _whole(r['Order_Free_Packs']), _whole(r['Order_Received_Packs']), None,
                  _rs(r['Order_Value_Rs']), _whole(r['Order_Shivaji Chowk']), _whole(r['Order_Hospet Road']), _whole(r['Order_Herur Branch']),
                  _whole(r['Order_B2B / Wholesale']), r['Confidence'], '; '.join(x for x in (r['Distributor_Why'], r['Note']) if x)]
        for col, v in enumerate(values, start=1):
            if v is not None and v != '':
                ws.cell(row=row, column=col, value=v)
        ws.cell(row=row, column=9).number_format = '#,##0'
        for col in (1, 3, 4, 5, 6, 7):
            ws.cell(row=row, column=col).alignment = Alignment(horizontal='center')
        ws.cell(row=row, column=15).alignment = Alignment(wrap_text=True, vertical='top')
    last = head + len(d)
    total = last + 1
    ws.cell(row=total, column=2, value=f'{len(d)} product{"s" if len(d) != 1 else ""}').font = Font(bold=True)
    ws.cell(row=total, column=9, value=_rs(d['Order_Value_Rs'].sum())).font = Font(bold=True)
    ws.cell(row=total, column=9).number_format = '#,##0'
    for col, w in zip(range(1, 8), [5, 42, 9, 8, 12, 8, 9]):
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.column_dimensions['H'].width = 26 if choose_distributor else 3
    for col, w in zip(range(9, 16), INTERNAL_WIDTHS):
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.freeze_panes = f'C{head + 1}'
    ws.print_title_rows = f'{head}:{head}'
    ws.print_area = f'A1:G{total}'
    ws.page_setup.orientation = 'portrait'
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    return ws


def write_distributor_book(path, orders, as_of, made_on):
    """orders: reorder.assign_distributor(orders_from(live)). A Summary sheet, then one order sheet per distributor
    (biggest cost first), then the products with no distributor yet."""
    wb = Workbook()
    summ = wb.active
    summ.title = 'Summary'
    by = orders.groupby('Distributor').agg(products=('Product', 'size'), paid=('Order_Paid_Packs', 'sum'), free=('Order_Free_Packs', 'sum'), cost=('Order_Value_Rs', 'sum'))
    by = by.sort_values('cost', ascending=False)
    if R.NOT_BOUGHT in by.index:
        by = pd.concat([by.drop(R.NOT_BOUGHT), by.loc[[R.NOT_BOUGHT]]])
    total_cost = by['cost'].sum()
    used = {'summary'}
    sheets = {}
    for name in by.index:
        sheet = NO_DISTRIBUTOR_SHEET if name == R.NOT_BOUGHT else distributor_sheet_name(name, used)
        used.add(sheet.lower())
        sheets[name] = sheet
        write_order_sheet(wb, sheet, name, orders[orders['Distributor'] == name].sort_values('Product'), as_of, made_on, choose_distributor=(name == R.NOT_BOUGHT))

    summ['A1'] = f'ORDER BY DISTRIBUTOR - {SHOP}'
    summ['A1'].font = Font(bold=True, size=14)
    summ['A2'] = f'Prepared {made_on:%d %b %Y} from stock worked out as on {as_of:%d %b %Y}.  {len(orders):,} products, about Rs {total_cost:,.0f}.'
    heads = ['#', 'Distributor', 'Products', 'Packs to pay for', 'Free packs', 'Cost (Rs)', 'Share of cost', 'Order sheet']
    for i, h in enumerate(heads, start=1):
        c = summ.cell(row=4, column=i, value=h)
        c.fill, c.font, c.alignment = HEAD_FILL, HEAD_FONT, Alignment(wrap_text=True, vertical='center')
    for n, (name, r) in enumerate(by.iterrows(), start=1):
        row = 4 + n
        shown = 'NO DISTRIBUTOR YET (not bought since April)' if name == R.NOT_BOUGHT else name
        for col, v in enumerate([n, shown, int(r['products']), _whole(r['paid']), _whole(r['free']), round(float(r['cost'])), float(r['cost']) / total_cost if total_cost else 0], start=1):
            summ.cell(row=row, column=col, value=v)
        summ.cell(row=row, column=6).number_format = '#,##0'
        summ.cell(row=row, column=7).number_format = '0.0%'
        _goto(summ.cell(row=row, column=8, value='open'), sheets[name])
    end = 4 + len(by) + 1
    summ.cell(row=end, column=2, value='Total').font = Font(bold=True)
    for col, v in ((3, len(orders)), (4, _whole(by['paid'].sum())), (5, _whole(by['free'].sum())), (6, round(float(total_cost)))):
        summ.cell(row=end, column=col, value=v).font = Font(bold=True)
    summ.cell(row=end, column=6).number_format = '#,##0'
    moved = int((orders['Distributor_Why'] != '').sum())
    notes = [
        'HOW TO USE',
        'Each distributor has its own sheet: columns A to G are the order to send (print area, one page wide); the grey columns to the right are for you (cost, split between the shops, notes).',
        "A product goes to the distributor it was last bought from. " + (f"{moved} products whose quantity is built on another distributor's offer (5+1 ...) are under that distributor instead - the Note column says so. " if moved else '')
        + 'Move a line by hand if you prefer another distributor.',
        f"Cost = packs to pay for x the price of the last purchase. On the '{NO_DISTRIBUTOR_SHEET}' sheet the cost is a guess ({int(NO_PRICE_COST_OF_MRP * 100)}% of MRP): those products have no purchase on file, so pick a distributor for each.",
        'The numbers are fixed values: if you change a quantity, the cost and totals do not recalculate.',
    ]
    for i, t in enumerate(notes):
        c = summ.cell(row=end + 2 + i, column=1, value=t)
        c.font = Font(bold=True) if i == 0 else Font()
        summ.merge_cells(start_row=end + 2 + i, start_column=1, end_row=end + 2 + i, end_column=8)
        c.alignment = Alignment(wrap_text=True, vertical='top')
        summ.row_dimensions[end + 2 + i].height = 15 if i == 0 else 32
    for col, w in zip('ABCDEFGH', [5, 46, 10, 12, 10, 12, 10, 11]):
        summ.column_dimensions[col].width = w
    summ.freeze_panes = 'A5'
    wb.save(path)
    return path


def distributor_main():
    res = build()
    live, as_of = res[1], res[-1]
    orders = R.assign_distributor(orders_from(live))
    out_dir = os.path.join(HERE, 'reports')
    os.makedirs(out_dir, exist_ok=True)
    path = write_distributor_book(os.path.join(out_dir, f'order_by_distributor_{as_of:%Y-%m-%d}.xlsx'), orders, as_of, pd.Timestamp.today().normalize())
    print(path)
    return path


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    if 'batch' in sys.argv[1:]:
        batch_main()
    elif 'distributor' in sys.argv[1:]:
        distributor_main()
    else:
        main()

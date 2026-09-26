r"""
Conversational-BI tool layer for the Pharmacy Ready Reckoner
============================================================
The assistant (a model via OpenRouter, or Gemini on Vertex) can only call the
functions below - it never touches the raw Sale/Purchase tables directly. So:

  * real product / supplier / employee names and real rupee figures DO go out
    (business info, sent to the configured model provider),
  * patient identity (name, mobile number) is withheld by default - a guard
    (_guard_no_pii) refuses any tool payload that carries a PII_COLUMNS key -
  * EXCEPT the tools explicitly asked for, listed in PII_ALLOWED_TOOLS:
    identify_person, get_patient_history, and get_product_patient_history -
    each can surface a real patient's name/mobile/purchase history. Calling
    one sends that to the external AI provider - the deliberate, understood
    tradeoff of those tools, not a leak. Every other tool still pseudonymizes
    (customers, via get_top_customers/get_customer_trends) or omits
    (patients) identity.

Adding heavier measures later (proxy names, category labels, magnitude
buckets) means editing only this file - the model loop in ask.py never changes.

Everything here is read-only over data\processed and reuses run_reckoner's
analysis functions, so the numbers always match the report. The one
exception is get_employee_targets, which reads on-demand exports from the
separate shubhadahealth.com system (see that function's docstring) - never
fetched live (no stored credentials for that site), only what's been
manually exported and dropped in data/employee_targets/.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

import run_reckoner as rk

# Column names that must never appear in anything handed to the model.
PII_COLUMNS = {
    'Patient', 'Mobile', 'Mobile Number', 'Mobile No', 'Phone', 'Phone Number',
    'Patient Mobile', 'Contact Number', 'Given by', 'Given By', 'Billed By',
}

_cache = {}


# ---------------------------------------------------------------------------
# Data loading (once per data version) and cheap cached rollups
# ---------------------------------------------------------------------------
def _master_files_mtime():
    paths = [rk.SALES_MASTER, rk.PURCH_MASTER, rk.MANIFEST_PATH]
    mtimes = [p.stat().st_mtime for p in paths if p.exists()]
    return max(mtimes) if mtimes else None


def _load():
    # Re-read whenever the master files changed on disk since the last call -
    # from ANY source (a CLI `python run_reckoner.py`, a mobile upload, or
    # this same process's own /api/ask route). A one-shot `python ask.py`
    # run only ever calls this once anyway, so the check is free there; a
    # long-running server process (server.py's /api/ask) would otherwise
    # answer every question from data as of its own startup, forever.
    current_mtime = _master_files_mtime()
    if 'sales' not in _cache or _cache.get('_mtime') != current_mtime:
        sales = pd.read_csv(rk.SALES_MASTER, low_memory=False) if rk.SALES_MASTER.exists() else pd.DataFrame()
        purch = pd.read_csv(rk.PURCH_MASTER, low_memory=False) if rk.PURCH_MASTER.exists() else pd.DataFrame()
        purch = rk.ensure_purch_defaults(purch)  # older master CSVs may predate an optional column
        # SALES_MASTER is normalized once, at ingest time (see
        # run_reckoner.normalize_sale_units docstring) - never call
        # normalize_sale_units() again here, it would double-convert B2B rows.
        _cache.clear()  # drop every derived rollup below too - they're stale now
        _cache['sales'] = sales
        _cache['purch'] = purch
        _cache['_mtime'] = current_mtime
    return _cache['sales'], _cache['purch']


def _factor_map():
    if 'factor_map' not in _cache:
        s, p = _load()
        _cache['factor_map'] = rk.build_product_factor_map(s, p)
    return _cache['factor_map']


def _profit():
    if 'profit' not in _cache:
        s, p = _load()
        _cache['profit'] = rk.build_profit_margin(s, p)   # (known, unknown)
    return _cache['profit']


def _over_under():
    if 'over_under' not in _cache:
        s, p = _load()
        _cache['over_under'] = rk.build_over_under(s, p)
    return _cache['over_under']


def _distributors():
    if 'dist' not in _cache:
        _, p = _load()
        lines, _ = rk.compute_distributor_lines(p)
        _cache['dist'] = rk.build_distributor_summary(lines)
    return _cache['dist']


def _latest_month():
    s, _ = _load()
    return sorted(s['Source_Month'].unique())[-1]


def _mobile_col():
    """The sales mobile-number column, if this export has one - same
    detection rule as run_reckoner's own customer-loyalty analysis, so a
    customer's identity resolves the same way everywhere."""
    if 'mobile_col' not in _cache:
        s, _ = _load()
        _cache['mobile_col'] = rk.find_col(s, rk.MOBILE_CANDIDATES)
    return _cache['mobile_col']


def _mobile_code(mobile):
    """One-way pseudonym for a mobile number: 'Cust_' + a 6-hex-char SHA-256
    digest of the digits only (so '+91 98765 43210' and '9876543210' hash the
    same). Stable across calls/runs (no salt) so the same customer gets the
    same code every time - needed to track one customer across months/tools -
    but the raw number can never be recovered from it."""
    digits = re.sub(r'\D', '', str(mobile))
    if not digits:
        return None
    return 'Cust_' + hashlib.sha256(digits.encode()).hexdigest()[:6]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _records(df, cols=None):
    """DataFrame -> list of small dicts, JSON-safe, PII columns dropped."""
    if cols is not None:
        df = df[[c for c in cols if c in df.columns]]
    df = df.drop(columns=[c for c in df.columns if c in PII_COLUMNS], errors='ignore')
    return json.loads(df.replace({np.nan: None}).to_json(orient='records'))


def _records_with_pii(df, cols=None):
    """Same as _records but does NOT drop PII_COLUMNS - only for the two
    tools in PII_ALLOWED_TOOLS that intentionally return patient identity."""
    if cols is not None:
        df = df[[c for c in cols if c in df.columns]]
    return json.loads(df.replace({np.nan: None}).to_json(orient='records'))


def _guard_no_pii(payload):
    """Last line of defence: refuse to return anything that carries a PII key.
    Raises so a bug surfaces loudly instead of leaking silently."""
    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)
    bad = PII_COLUMNS.intersection(keys(payload))
    if bad:
        raise ValueError(f'PII guard tripped - tool tried to return {sorted(bad)}')
    return payload


def _round(x, n=2):
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# The tools the model may call
# ---------------------------------------------------------------------------
def get_overview():
    """High-level KPIs: history, next-month forecast total, gross profit &
    margin, margin by channel (retail vs B2B), over-purchased & dead-stock
    counts."""
    s, p = _load()
    profit, _unknown = _profit()
    ou = _over_under()
    channel = rk.build_channel_profit(s, p)
    forecast, target_month, months = rk.build_demand_forecast(s)
    pretax = profit['Pretax_Revenue'].sum()
    return _guard_no_pii({
        'months_of_history': list(months),
        'forecasting_month': target_month,
        'overall_gross_profit': _round(profit['Gross_Profit'].sum()),
        'overall_margin_pct': _round(profit['Gross_Profit'].sum() / pretax * 100, 1) if pretax else 0,
        'margin_by_channel': _records(channel, ['Branch', 'Type', 'Revenue', 'Gross_Profit', 'Margin_Pct']),
        'over_purchased_products': int((ou['Status'] == 'Over-purchased').sum()),
        'dead_stock_products': int((ou['Status'] == 'Purchased, never sold (dead stock)').sum()),
        'predicted_next_month_sales_value': _round(forecast['Predicted_Value'].sum()),
    })


def get_channel_profit():
    """Gross profit & margin split by sales channel: the two retail branches
    (Shivaji Chowk, Hospet Road) vs B2B / Wholesale."""
    s, p = _load()
    return _guard_no_pii(_records(
        rk.build_channel_profit(s, p),
        ['Branch', 'Type', 'Invoices', 'Revenue', 'COGS', 'Gross_Profit', 'Margin_Pct', 'Revenue_Cost_Unknown']))


def search_products(query, limit=15):
    """Find real product names containing `query` (case-insensitive). Use this
    to get an exact product name before calling get_product."""
    s, p = _load()
    names = pd.Index(sorted(set(s['Product'].dropna()) | set(p['Product'].dropna())))
    hits = [n for n in names if query.lower() in str(n).lower()][:int(limit)]
    return _guard_no_pii({'query': query, 'matches': hits})


def get_product(name):
    """Full aggregate picture for one product: sold vs purchased (in strips),
    average selling price, average cost, gross margin, and a month-by-month
    trend. No individual bills, no patient data."""
    s, p = _load()
    factor_map = _factor_map()
    sp = s[s['Product'] == name]
    pp = p[p['Product'] == name].copy()
    if sp.empty and pp.empty:
        return _guard_no_pii({'product': name, 'found': False,
                              'hint': 'call search_products to get the exact name'})
    factor = float(factor_map.get(name, 1)) or 1.0
    sold_units = float(sp['Qty'].sum()) if not sp.empty else 0.0
    sold_value = float(sp['Item Total'].sum()) if not sp.empty else 0.0
    if not pp.empty:
        pp['Factor'] = pp['Factor'].replace(0, 1).fillna(1)
        purch_strips = float(pp['Qty'].sum())
        purch_units = float((pp['Qty'] * pp['Factor']).sum())
        purch_value = float(pp['Item Total'].sum())
        cost_pretax = float((pp['Qty'] * pp['Sale Rate'] - pp['Disc Amount'].fillna(0)).sum())
        cost_per_unit = cost_pretax / purch_units if purch_units else None
    else:
        purch_strips = purch_units = purch_value = 0.0
        cost_per_unit = None

    pretax_rev = float((sp['Item Total'] / (1 + sp['Tax Rate'].fillna(0) / 100)).sum()) if not sp.empty else 0.0
    gross = (pretax_rev - sold_units * cost_per_unit) if cost_per_unit is not None else None

    s_m = sp.groupby('Source_Month').agg(sold_units=('Qty', 'sum'), sold_value=('Item Total', 'sum')) if not sp.empty else pd.DataFrame()
    p_m = pp.groupby('Source_Month').agg(purch_strips=('Qty', 'sum'), purch_value=('Item Total', 'sum')) if not pp.empty else pd.DataFrame()
    monthly = pd.concat([s_m, p_m], axis=1).fillna(0)
    if not monthly.empty and 'sold_units' in monthly:
        monthly['sold_strips'] = (monthly['sold_units'] / factor).round(1)
        monthly = monthly.drop(columns=['sold_units'], errors='ignore')
    monthly = monthly.round(2).reset_index().rename(columns={'index': 'month', 'Source_Month': 'month'})

    return _guard_no_pii({
        'product': name, 'found': True, 'pack_size_factor': factor,
        'total_sold_strips': _round(sold_units / factor, 1),
        'total_sold_value': _round(sold_value),
        'avg_selling_price_per_unit': _round(sold_value / sold_units, 2) if sold_units else None,
        'total_purchased_strips': _round(purch_strips, 1),
        'total_purchase_value': _round(purch_value),
        'avg_cost_per_unit_pretax': _round(cost_per_unit, 4) if cost_per_unit is not None else None,
        'gross_profit': _round(gross) if gross is not None else None,
        'gross_margin_pct': _round(gross / pretax_rev * 100, 1) if (gross is not None and pretax_rev) else None,
        'monthly': monthly.to_dict('records'),
    })


TOP_KINDS = ('dead_stock', 'over_purchased', 'under_purchased',
             'best_margin', 'worst_margin', 'top_profit', 'top_distributors')


def get_top(kind, n=10):
    """Ranked lists. `kind` is one of: dead_stock, over_purchased,
    under_purchased (by value tied up / shortfall); best_margin, worst_margin,
    top_profit (product profit & margin); top_distributors (embedded margin)."""
    n = int(n)
    if kind not in TOP_KINDS:
        return _guard_no_pii({'error': f'kind must be one of {list(TOP_KINDS)}'})
    if kind == 'top_distributors':
        d = _distributors().head(n)
        return _guard_no_pii(_records(d, ['Supplier', 'Invoices', 'Total_Invoice_Value',
                                           'Embedded_Profit', 'Margin_Pct', 'Months_Active']))
    if kind in ('dead_stock', 'over_purchased', 'under_purchased'):
        ou = _over_under()
        status = {'dead_stock': 'Purchased, never sold (dead stock)',
                  'over_purchased': 'Over-purchased', 'under_purchased': 'Under-purchased'}[kind]
        sub = ou[ou['Status'] == status].copy()
        sub['value'] = sub['Net_Value_Approx'].abs()
        sub = sub.sort_values('value', ascending=False).head(n)
        return _guard_no_pii(_records(sub.rename(columns={'Sold_Qty_Strips': 'sold_strips',
                                                          'Purch_Qty_Strips': 'purchased_strips'}),
                                      ['Product', 'purchased_strips', 'sold_strips', 'value']))
    profit, _ = _profit()
    asc = (kind == 'worst_margin')
    key = 'Gross_Profit' if kind == 'top_profit' else 'Margin_Pct'
    sub = profit.sort_values(key, ascending=asc).head(n)
    return _guard_no_pii(_records(sub, ['Product', 'Qty_Sold_Strips', 'Pretax_Revenue', 'Gross_Profit', 'Margin_Pct']))


def get_forecast(product=None, n=15):
    """Next-month demand forecast. With no product: the top-N products by
    predicted value, plus the per-branch footfall forecast. With a product:
    that product's predicted quantity, value and trend."""
    s, _ = _load()
    forecast, target_month, _ = rk.build_demand_forecast(s)
    if product:
        row = forecast[forecast['Product'] == product]
        if row.empty:
            return _guard_no_pii({'product': product, 'found': False,
                                  'hint': 'call search_products for the exact name'})
        return _guard_no_pii({'forecasting_month': target_month, 'found': True,
                              **_records(row, ['Product', 'Trend', 'Predicted_Qty_Strips',
                                               'Avg_Price', 'Predicted_Value'])[0]})
    _daily, _monthly, ff, _tm = rk.build_footfall(s)
    return _guard_no_pii({
        'forecasting_month': target_month,
        'top_products_by_predicted_value': _records(
            forecast.head(int(n)), ['Product', 'Trend', 'Predicted_Qty_Strips', 'Avg_Price', 'Predicted_Value']),
        'footfall_forecast_by_branch': _records(
            ff, ['Branch', 'Predicted_Avg_Daily', 'Predicted_Total_Footfall', 'Growth_Pct']),
    })


def get_purchase_issues(n=10):
    """Latest-month purchase-entry problems and supplier-terms slippage:
    PTR-above-MRP entries, scheme (free-goods) shortfalls, and discount
    shortfalls - counts plus the top few by impact. Supplier names only."""
    _, p = _load()
    month = _latest_month()
    ptr_high, mrp_missing, _gifts, variance = rk.build_purchase_errors(p, month)
    scheme, _ = rk.build_scheme_consistency(p, month)
    disc, _ = rk.build_discount_consistency(p, month)
    return _guard_no_pii({
        'latest_month': month,
        'ptr_above_mrp': {'count': len(ptr_high),
                          'top': _records(ptr_high.rename(columns={'Sale Rate': 'PTR'}).head(n),
                                          ['Date', 'Supplier', 'Product', 'MRP', 'PTR', 'Excess'])},
        'mrp_missing_count': len(mrp_missing),
        'mrp_variance_count': len(variance),
        'scheme_shortfalls': {'count': len(scheme),
                              'top': _records(scheme.head(n), ['Supplier', 'Product', 'Shortfall_Qty'])},
        'discount_shortfalls': {'count': len(disc),
                                'top': _records(disc.head(n), ['Supplier', 'Product', 'Disc_Gap_pct_pts', 'Value_Lost_Approx'])},
    })


def get_thin_margin_purchases(supplier=None, n=15):
    """Purchase lines with thin-to-negative embedded margin, ALL history (not
    just the latest month): where MRP/Rate is at or below ~1.10 (embedded
    margin ~9% or less, or already negative because Rate is above MRP - a
    guaranteed loss even sold at full MRP). Returns the worst lines by rupee
    impact plus a by-supplier rollup - a supplier with many flagged lines
    across different products is a genuine pricing pattern with that party
    (worth asking for a credit note or renegotiating), not a one-off entry
    typo. Each line includes who entered it (Entered By/Created By) so the
    owner can judge whether a specific line is a data-entry mistake to fix
    internally or a party issue to take up with the supplier - do NOT guess
    which one it is yourself. Optional `supplier` filters to one party
    (case-insensitive substring)."""
    _, p = _load()
    flagged, by_supplier = rk.build_thin_margin_purchases(p)
    if supplier:
        s_lower = supplier.strip().lower()
        flagged = flagged[flagged['Supplier'].str.lower().str.contains(s_lower, na=False)]
        by_supplier = by_supplier[by_supplier['Supplier'].str.lower().str.contains(s_lower, na=False)]
    entered_col = 'Entered By' if 'Entered By' in flagged.columns else next(
        (c for c in ('Created By', 'Entry By') if c in flagged.columns), None)
    line_cols = ['Date', 'Supplier', 'Product', 'Qty', 'MRP', 'Sale Rate',
                 'MRP_Rate_Ratio', 'Embedded_Margin_Pct', 'Value_Impact']
    if entered_col:
        line_cols.append(entered_col)
    return _guard_no_pii({
        'ratio_threshold': rk.THIN_MARGIN_RATIO,
        'total_flagged_lines': len(flagged),
        'total_value_impact': _round(flagged['Value_Impact'].sum()),
        'worst_lines': _records(flagged.head(n), line_cols),
        'by_supplier': _records(by_supplier.head(n), ['Supplier', 'Flagged_Lines', 'Distinct_Products',
                                                       'Worst_Ratio', 'Worst_Margin_Pct', 'Total_Value_Impact']),
    })


def get_employee_performance():
    """Staff performance, by whichever of these columns this export has:
    'Billed By' (revenue/bills/avg-bill-value per employee - who rang up the
    sale), 'Item Given By' (lines/qty/value dispensed per employee - who
    physically fetched the item from the shelf/rack and handed it to the
    billing counter; this is a fulfillment/enabling role, not itself a sales
    metric, but a bill can't be completed without it - a high Given-By
    volume supports and correlates with sales throughput, it does not
    compete with Billed By), 'Created By' (purchase entries, PTR-above-MRP
    error count, and embedded-margin quality per employee who keyed them
    in). Real employee names - this is an internal staff-review tool the
    owner reads, not customer-facing, unlike get_top_customers/
    get_customer_trends which pseudonymize identity. Sections not present in
    this export yet are simply omitted."""
    s, p = _load()
    lines, _ = rk.compute_distributor_lines(p)
    perf = rk.build_employee_performance(s, p, lines)
    out = {'available': any(v is not None for v in perf.values())}
    if perf['billed_by'] is not None:
        out['billed_by'] = _records(perf['billed_by'], ['Employee', 'Bills', 'Revenue', 'Patients', 'Avg_Bill_Value'])
    if perf['given_by'] is not None:
        out['given_by'] = _records(perf['given_by'], ['Employee', 'Lines', 'Qty', 'Value'])
    if perf['created_by'] is not None:
        cols = ['Employee', 'Entries', 'Lines', 'Value', 'PTR_Errors']
        if 'Margin_Pct' in perf['created_by'].columns:
            cols.append('Margin_Pct')
        out['created_by'] = _records(perf['created_by'], cols)
    return _guard_no_pii(out)


# ---------------------------------------------------------------------------
# On-demand employee KPI/target report from the SEPARATE shubhadahealth.com
# system - not part of the reckoner's own data, and never fetched live: that
# site needs a login, and no credential for it is stored anywhere in this
# codebase (a deliberate choice - see this module's docstring). Instead, the
# owner exports a report from that site (Employee Performance Report -> pick
# employee -> Export) whenever a question needs it and drops the file in
# EMPLOYEE_TARGETS_DIR; this tool reads whatever's there, fresh, each call.
# Nothing here is cached beyond the file itself, and no history accumulates.
# ---------------------------------------------------------------------------
EMPLOYEE_TARGETS_DIR = Path(__file__).parent / 'data' / 'employee_targets'
_TARGET_FILENAME_RE = re.compile(r'^Performance Chart of (.+)\.xlsx$', re.IGNORECASE)


def get_employee_targets(name=None):
    """On-demand employee KPI/target report from the separate
    shubhadahealth.com system: target vs achieved quantity/amount, an
    incentive 'Earned Amount', and Performance % - per KPI category (Sales,
    Sales-Packed, DC Created/Checked, Purchase Created, Re-Order Items/
    Placed/Collected/Return, Stock Transfer, Dump List, Item Shelfed,
    Sales-Return, Non Cash Receipt). NOT part of the reckoner's own data, and
    NOT live - only available once the owner has exported it from that site
    (Employee Performance Report -> pick employee -> Export) and dropped the
    file in data/employee_targets/. If nothing matches, say so plainly and
    ask the owner to export+drop the file, then ask again - do not guess or
    substitute reckoner figures instead. `name` filters to exports whose
    employee name contains it (case-insensitive); omit to list every export
    currently available."""
    if not EMPLOYEE_TARGETS_DIR.exists():
        return {'available': False, 'reason': 'no employee_targets folder yet'}
    matches = []
    for f in EMPLOYEE_TARGETS_DIR.glob('*.xlsx'):
        m = _TARGET_FILENAME_RE.match(f.name)
        if m:
            matches.append((m.group(1).strip(), f))
    if name:
        name_lower = name.strip().lower()
        matches = [(emp, f) for emp, f in matches if name_lower in emp.lower()]
    if not matches:
        who = f' for "{name}"' if name else ''
        return {'available': False,
                'reason': f'no exported performance file{who} in data/employee_targets/ - export it from '
                          'shubhadahealth.com (Employee Performance Report -> pick employee -> Export) and '
                          'drop the file there, then ask again'}

    def load_one(emp_name, path):
        df = pd.read_excel(path, sheet_name='data')
        df = df.rename(columns={'Category Name': 'Category', 'Achived Qty': 'Achieved_Qty',
                                 'Achived Amount': 'Achieved_Amount', 'Factor': 'Conversion_Factor'})
        categories = _records(df, ['Category', 'Target Qty', 'Achieved_Qty', 'Target Amount',
                                    'Achieved_Amount', 'Conversion_Factor', 'Earned Amount', 'Performance %'])
        return {
            'employee': emp_name, 'exported_file': path.name,
            'exported_at': datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M'),
            # shubhadahealth.com's export file does NOT include the From/To date
            # range that was selected on-screen when it was generated - only
            # exported_at (when the file was created) is known, not what period
            # the numbers themselves cover. Say so rather than implying a period.
            'period_note': 'the source export does not record which date range these numbers cover - '
                            'only when the file was exported (exported_at); do not assume it is the current month',
            'total_earned_amount': _round(df['Earned Amount'].sum()) if 'Earned Amount' in df.columns else None,
            'categories': categories,
        }

    matches.sort(key=lambda x: x[1].stat().st_mtime, reverse=True)
    return {'available': True, 'reports': [load_one(emp, f) for emp, f in matches]}


# ---------------------------------------------------------------------------
# Employee attendance / leave-pattern - also from shubhadahealth.com, also
# on-demand (no credential stored for that site, same as get_employee_targets
# above). That page has no Export button, so a snapshot is saved manually
# while the owner is logged in there live (each snapshot explicitly records
# the from/to date range it covers - unlike the targets export, which does
# not - see get_employee_targets' period_note). Nothing is fetched live by
# this tool; it only reads whatever snapshot already exists on disk.
# ---------------------------------------------------------------------------
EMPLOYEE_ATTENDANCE_DIR = Path(__file__).parent / 'data' / 'employee_attendance'
WEEKDAY_NAMES_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def get_employee_attendance(name=None):
    """On-demand employee attendance / leave-pattern from the separate
    shubhadahealth.com system: which days in the snapshot's date range the
    employee has NO punch record (absent), which weekday each falls on,
    whether absences cluster into consecutive-day blocks (more likely a
    planned/multi-day leave) or are scattered single days, and whether
    absences correlate with weekends (a consistent day-off pattern) or not.
    The covered period (from_date/to_date) is always given explicitly -
    NEVER assume it means the current month. NOT live - only available once
    a snapshot has been saved (while the owner is logged into that site) to
    data/employee_attendance/. If nothing matches, say so plainly and ask
    the owner to pull up that employee's Attendance Transaction report live,
    then ask again - don't guess or substitute reckoner figures instead.
    `name` filters to snapshots whose employee name contains it
    (case-insensitive); omit to list every snapshot currently available."""
    if not EMPLOYEE_ATTENDANCE_DIR.exists():
        return {'available': False, 'reason': 'no employee_attendance folder yet'}
    files = list(EMPLOYEE_ATTENDANCE_DIR.glob('*.json'))
    if name:
        name_lower = name.strip().lower()
        files = [f for f in files if name_lower in f.stem.lower()]
    if not files:
        who = f' for "{name}"' if name else ''
        return {'available': False,
                'reason': f'no attendance snapshot{who} in data/employee_attendance/ - pull up that employee\'s '
                          'Attendance Transaction report live on shubhadahealth.com and ask for it to be saved, '
                          'then ask again'}

    def load_one(path):
        snap = json.loads(path.read_text(encoding='utf-8'))
        from_d = datetime.strptime(snap['from_date'], '%Y-%m-%d').date()
        to_d = datetime.strptime(snap['to_date'], '%Y-%m-%d').date()
        # A day that hasn't finished yet has no punch simply because it isn't
        # over - that's not an absence. Cap the range at yesterday whenever
        # to_date reaches today or later, so "today" never gets miscounted.
        last_complete_day = datetime.now().date() - timedelta(days=1)
        effective_to = min(to_d, last_complete_day)
        present_dates = sorted({datetime.strptime(p['date'], '%d/%m/%Y').date() for p in snap['punches']})
        present_set = set(present_dates)
        all_days = [from_d + timedelta(days=i) for i in range((effective_to - from_d).days + 1)] if effective_to >= from_d else []
        absent_dates = [d for d in all_days if d not in present_set]

        # Group consecutive absent dates into blocks - a 2+ day block reads
        # very differently (planned leave) than isolated single-day gaps.
        blocks, current = [], []
        for d in absent_dates:
            if current and (d - current[-1]).days == 1:
                current.append(d)
            else:
                if current:
                    blocks.append(current)
                current = [d]
        if current:
            blocks.append(current)

        weekend_absences = sum(1 for d in absent_dates if d.weekday() >= 5)
        weekday_absences = len(absent_dates) - weekend_absences
        present_in_range = [d for d in present_dates if from_d <= d <= effective_to]
        avg_punches_per_present_day = round(len(snap['punches']) / len(present_dates), 1) if present_dates else None

        return {
            'employee': snap['employee'], 'branch': snap.get('branch'),
            'from_date': snap['from_date'], 'to_date': snap['to_date'], 'snapshot_saved_at': snap.get('saved_at'),
            'days_in_range': len(all_days), 'days_present': len(present_in_range), 'days_absent': len(absent_dates),
            'absent_dates': [{'date': d.isoformat(), 'weekday': WEEKDAY_NAMES_SHORT[d.weekday()]} for d in absent_dates],
            'absence_blocks': [{'start': b[0].isoformat(), 'end': b[-1].isoformat(), 'days': len(b)} for b in blocks if len(b) >= 2],
            'weekend_absences': weekend_absences, 'weekday_absences': weekday_absences,
            'avg_punches_per_present_day': avg_punches_per_present_day,
        }

    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return {'available': True, 'reports': [load_one(f) for f in files]}


def get_top_customers(n=10, by='spend'):
    """Top customers ranked by total spend or visit count. Each customer is
    identified only by a one-way pseudonymous code ('Cust_xxxxxx') derived
    from their mobile number - the real number never leaves this function.
    `by` is 'spend' (total revenue) or 'visits' (unique bills). Returns
    available=False if this export has no mobile number column yet."""
    s, _ = _load()
    col = _mobile_col()
    if not col:
        return _guard_no_pii({'available': False, 'reason': 'no mobile number column in the data yet'})
    if by not in ('spend', 'visits'):
        return _guard_no_pii({'error': "by must be 'spend' or 'visits'"})
    d = s[s[col].notna()].copy()
    d['customer'] = d[col].map(_mobile_code)
    g = d.groupby('customer').agg(
        spend=('Item Total', 'sum'), visits=('Inv.No', 'nunique'),
        months_active=('Source_Month', 'nunique')).reset_index()
    g['spend'] = g['spend'].round(2)
    g = g.sort_values(by, ascending=False).head(int(n))
    return _guard_no_pii({'available': True, 'by': by,
                          'customers': _records(g, ['customer', 'spend', 'visits', 'months_active'])})


def get_customer_trends(churn_limit=20):
    """Month-by-month new/returning/dropped-off customer counts (matches the
    Customer Loyalty report tab), plus the highest-spend customers who bought
    in an earlier month but not in the latest month (churn candidates) -
    each shown only as a pseudonymous 'Cust_xxxxxx' code. Returns
    available=False if this export has no mobile number column yet."""
    s, _ = _load()
    trend, mobile_col = rk.build_customer_loyalty(s)
    if trend is None:
        return _guard_no_pii({'available': False, 'reason': 'no mobile number column in the data yet'})
    d = s[s[mobile_col].notna()].copy()
    d[mobile_col] = d[mobile_col].astype(str).str.strip()
    months = sorted(d['Source_Month'].unique())
    latest = months[-1]
    before = d[d['Source_Month'] != latest]
    active_latest = set(d[d['Source_Month'] == latest][mobile_col])
    churned = before[~before[mobile_col].isin(active_latest)]
    churned_spend = churned.groupby(mobile_col)['Item Total'].sum().sort_values(ascending=False)
    churned_top = [{'customer': _mobile_code(m), 'past_spend': _round(v)}
                   for m, v in churned_spend.head(int(churn_limit)).items()]
    return _guard_no_pii({
        'available': True,
        'latest_month': latest,
        'monthly_trend': _records(trend),
        'churned_count': int(churned_spend.shape[0]),
        'churned_top': churned_top,
    })


# ---------------------------------------------------------------------------
# Patient-identifying tools - deliberate exceptions to the PII guard. The
# owner explicitly asked for real name/mobile/per-transaction lookup by
# phone number, by product, and now by a bare NAME (which could be either an
# employee or a customer, and needs disambiguating first); PII_ALLOWED_TOOLS
# (near run_tool, below) is what actually exempts these from _guard_no_pii -
# see this module's docstring for the full tradeoff.
# ---------------------------------------------------------------------------
def identify_person(name, limit=20):
    """Given a bare NAME (not a phone number) - figure out whether it's an
    EMPLOYEE or a CUSTOMER before pulling any detail, since the same name
    could be either and the right follow-up tool differs:
      - kind='employee': name matched Billed By / Item Given By / Created By.
        Follow up with get_employee_performance() for their full detail, and
        get_employee_targets() + get_employee_attendance() too if the
        question wants minute detail, targets, earned/incentive amounts, or
        leave/attendance behavior.
      - kind='customer_candidates': name matched one or more Patient names
        (partial/case-insensitive), each returned with their real mobile
        number, spend, and line count, highest-spend first, capped at
        `limit` (total_matches gives the true count - a common first name
        can match 50+ people, so ask the owner to narrow it, e.g. with a
        fuller name or branch, rather than dumping all of them). If there's
        exactly one candidate, follow up with get_patient_history using
        their mobile for full detail. If there are several, list them (name,
        approx spend) and ask the user which one before pulling anyone's
        full history.
      - kind='not_found': no employee or patient name matched.
    Always try this FIRST when a question names a person, before assuming
    which kind of lookup applies."""
    s, p = _load()
    name_lower = (name or '').strip().lower()
    if not name_lower:
        return {'error': 'name is required'}

    lines, _ = rk.compute_distributor_lines(p)
    perf = rk.build_employee_performance(s, p, lines)
    employee_matches = set()
    for section in ('billed_by', 'given_by', 'created_by'):
        df = perf.get(section)
        if df is not None and 'Employee' in df.columns:
            hits = df[df['Employee'].str.lower().str.contains(name_lower, na=False)]
            employee_matches.update(hits['Employee'].tolist())
    if employee_matches:
        return {
            'name_searched': name, 'kind': 'employee',
            'employee_names_matched': sorted(employee_matches),
            'hint': 'call get_employee_performance() for full detail on these employees',
        }

    if 'Patient' not in s.columns:
        return {'name_searched': name, 'kind': 'not_found',
                'reason': 'no matching employee, and no Patient column in the data'}
    col = _mobile_col()
    hits = s[s['Patient'].notna() & s['Patient'].str.lower().str.contains(name_lower, na=False)].copy()
    if hits.empty:
        return {'name_searched': name, 'kind': 'not_found', 'reason': 'no matching employee or patient name found'}

    hits['_mobile'] = hits[col] if col else None
    g = hits.groupby('Patient').agg(
        Lines=('Product', 'count'), Spend=('Item Total', 'sum'),
        Mobile=('_mobile', lambda x: next((v for v in x if pd.notna(v)), None)),
    ).reset_index().sort_values('Spend', ascending=False)
    total_matches = len(g)
    candidates = _records_with_pii(g.head(int(limit)), ['Patient', 'Mobile', 'Lines', 'Spend'])
    return {
        'name_searched': name, 'kind': 'customer_candidates',
        'total_matches': total_matches, 'candidates': candidates,
        'hint': ('exactly one candidate - call get_patient_history with their Mobile for full detail'
                 if total_matches == 1 else
                 f'{total_matches} candidates (showing top {len(candidates)} by spend) - ask the user '
                 'which one before calling get_patient_history for anyone; if too many, ask them to '
                 'narrow the name or give a phone number instead'),
    }


def get_patient_history(mobile, limit=100):
    """Full, row-level sale history for ONE patient, found by (all or part
    of) their mobile number - every product they've bought, with their real
    name and mobile number included, most recent first. Use this whenever
    the question gives a phone number to search by. Returns available=False
    if this export has no mobile number column yet, or found=False if no
    sales match that number."""
    s, _ = _load()
    col = _mobile_col()
    if not col:
        return {'available': False, 'reason': 'no mobile number column in the data yet'}
    digits = re.sub(r'\D', '', str(mobile))
    if not digits:
        return {'error': 'no digits found in the given phone number'}
    mobile_digits = s[col].astype(str).str.replace(r'\D', '', regex=True)
    match = s[(mobile_digits != '') & mobile_digits.str.contains(digits, na=False)].copy()
    if match.empty:
        return {'available': True, 'mobile_searched': mobile, 'found': False, 'sales': []}
    match['Branch'] = match['Inv.No'].apply(rk.extract_branch)
    match['Qty'] = rk.to_strips(match['Qty'], match['Product'], _factor_map())  # overwrite in place - a second 'Qty' column breaks to_json(orient='records')
    match = match.sort_values('Date', ascending=False)
    match = match.rename(columns={col: 'Mobile'})
    sales = _records_with_pii(match, ['Date', 'Patient', 'Mobile', 'Branch', 'Inv.No', 'Product', 'Qty', 'MRP', 'Item Total'])
    patient_names = sorted({r['Patient'] for r in sales if r.get('Patient')})
    return {
        'available': True, 'mobile_searched': mobile, 'found': True,
        'patient_names': patient_names,
        'total_lines': len(sales), 'total_products': int(match['Product'].nunique()),
        'total_spend': _round(match['Item Total'].sum()),
        'sales': sales[:int(limit)],
    }


def get_product_patient_history(product, limit=100):
    """Row-level sale history for ONE product - every individual transaction
    (not aggregated), with the real buyer's name and mobile number included,
    most recent first. Use this whenever a question about a product's sales
    should show WHO bought it, not just totals. Call search_products first
    to get the exact product name."""
    s, _ = _load()
    col = _mobile_col()
    match = s[s['Product'] == product].copy()
    if match.empty:
        return {'product': product, 'found': False, 'hint': 'call search_products for the exact name'}
    match['Branch'] = match['Inv.No'].apply(rk.extract_branch)
    match['Qty'] = rk.to_strips(match['Qty'], match['Product'], _factor_map())
    match = match.sort_values('Date', ascending=False)
    cols = ['Date', 'Patient']
    if col:
        match = match.rename(columns={col: 'Mobile'})
        cols.append('Mobile')
    cols += ['Branch', 'Inv.No', 'Qty', 'MRP', 'Item Total']
    sales = _records_with_pii(match, cols)
    return {
        'product': product, 'found': True,
        'total_lines': len(sales),
        'sales': sales[:int(limit)],
    }


QUERY_DIMS = {'product': 'Product', 'branch': 'Branch', 'month': 'Source_Month'}
QUERY_METRICS = ('revenue', 'pretax_revenue', 'qty_strips', 'invoices', 'avg_price')


def query_sales(group_by=None, metric='revenue', product=None, branch=None, month=None, top_n=15):
    """Flexible aggregation over sales - the open-ended power tool. group_by:
    any of ['product','branch','month']. metric: revenue, pretax_revenue,
    qty_strips, invoices, avg_price. Optional filters product/branch/month
    (contains-match for product/branch, exact 'YYYY-MM' for month). Returns the
    top_n groups by the metric. Aggregates only - never individual bills."""
    s, _ = _load()
    if metric not in QUERY_METRICS:
        return _guard_no_pii({'error': f'metric must be one of {list(QUERY_METRICS)}'})
    df = s.copy()
    df['Branch'] = df['Inv.No'].apply(rk.extract_branch)
    if product:
        df = df[df['Product'].str.contains(product, case=False, na=False)]
    if branch:
        df = df[df['Branch'].str.contains(branch, case=False, na=False)]
    if month:
        df = df[df['Source_Month'] == month]
    if df.empty:
        return _guard_no_pii({'rows': [], 'note': 'no sales match those filters'})

    df['_pretax'] = df['Item Total'] / (1 + df['Tax Rate'].fillna(0) / 100)
    df['_strips'] = rk.to_strips(df['Qty'], df['Product'], _factor_map())

    def measure(g):
        if metric == 'revenue':
            return float(g['Item Total'].sum())
        if metric == 'pretax_revenue':
            return float(g['_pretax'].sum())
        if metric == 'qty_strips':
            return float(g['_strips'].sum())
        if metric == 'invoices':
            return int(g['Inv.No'].nunique())
        if metric == 'avg_price':
            q = g['Qty'].sum()
            return float(g['Item Total'].sum() / q) if q else 0.0

    dims = [QUERY_DIMS[d] for d in (group_by or []) if d in QUERY_DIMS]
    if not dims:
        return _guard_no_pii({'metric': metric, 'value': _round(measure(df))})
    rows = []
    for keys, g in df.groupby(dims):
        keys = keys if isinstance(keys, tuple) else (keys,)
        row = {d.lower().replace('source_', ''): k for d, k in zip(dims, keys)}
        row[metric] = _round(measure(g))
        rows.append(row)
    rows.sort(key=lambda r: (r[metric] is not None, r[metric]), reverse=True)
    return _guard_no_pii({'metric': metric, 'group_by': group_by, 'rows': rows[:int(top_n)]})


# ---------------------------------------------------------------------------
# Registry: provider-neutral tool specs (name / description / JSON-schema args)
# ask.py converts these into whatever the model SDK wants.
# ---------------------------------------------------------------------------
def _schema(props, required=()):
    return {'type': 'object', 'properties': props, 'required': list(required)}


TOOLS = [
    {'name': 'get_overview', 'fn': get_overview,
     'description': get_overview.__doc__, 'parameters': _schema({})},
    {'name': 'get_channel_profit', 'fn': get_channel_profit,
     'description': get_channel_profit.__doc__, 'parameters': _schema({})},
    {'name': 'search_products', 'fn': search_products,
     'description': search_products.__doc__,
     'parameters': _schema({'query': {'type': 'string'},
                            'limit': {'type': 'integer'}}, ['query'])},
    {'name': 'get_product', 'fn': get_product,
     'description': get_product.__doc__,
     'parameters': _schema({'name': {'type': 'string'}}, ['name'])},
    {'name': 'get_top', 'fn': get_top,
     'description': get_top.__doc__,
     'parameters': _schema({'kind': {'type': 'string', 'enum': list(TOP_KINDS)},
                            'n': {'type': 'integer'}}, ['kind'])},
    {'name': 'get_forecast', 'fn': get_forecast,
     'description': get_forecast.__doc__,
     'parameters': _schema({'product': {'type': 'string'}, 'n': {'type': 'integer'}})},
    {'name': 'get_purchase_issues', 'fn': get_purchase_issues,
     'description': get_purchase_issues.__doc__,
     'parameters': _schema({'n': {'type': 'integer'}})},
    {'name': 'get_thin_margin_purchases', 'fn': get_thin_margin_purchases,
     'description': get_thin_margin_purchases.__doc__,
     'parameters': _schema({'supplier': {'type': 'string'}, 'n': {'type': 'integer'}})},
    {'name': 'get_employee_performance', 'fn': get_employee_performance,
     'description': get_employee_performance.__doc__, 'parameters': _schema({})},
    {'name': 'get_employee_targets', 'fn': get_employee_targets,
     'description': get_employee_targets.__doc__,
     'parameters': _schema({'name': {'type': 'string'}})},
    {'name': 'get_employee_attendance', 'fn': get_employee_attendance,
     'description': get_employee_attendance.__doc__,
     'parameters': _schema({'name': {'type': 'string'}})},
    {'name': 'get_top_customers', 'fn': get_top_customers,
     'description': get_top_customers.__doc__,
     'parameters': _schema({'n': {'type': 'integer'}, 'by': {'type': 'string', 'enum': ['spend', 'visits']}})},
    {'name': 'get_customer_trends', 'fn': get_customer_trends,
     'description': get_customer_trends.__doc__,
     'parameters': _schema({'churn_limit': {'type': 'integer'}})},
    {'name': 'query_sales', 'fn': query_sales,
     'description': query_sales.__doc__,
     'parameters': _schema({
         'group_by': {'type': 'array', 'items': {'type': 'string', 'enum': list(QUERY_DIMS)}},
         'metric': {'type': 'string', 'enum': list(QUERY_METRICS)},
         'product': {'type': 'string'}, 'branch': {'type': 'string'},
         'month': {'type': 'string'}, 'top_n': {'type': 'integer'}})},
    {'name': 'identify_person', 'fn': identify_person,
     'description': identify_person.__doc__,
     'parameters': _schema({'name': {'type': 'string'}, 'limit': {'type': 'integer'}}, ['name'])},
    {'name': 'get_patient_history', 'fn': get_patient_history,
     'description': get_patient_history.__doc__,
     'parameters': _schema({'mobile': {'type': 'string'}, 'limit': {'type': 'integer'}}, ['mobile'])},
    {'name': 'get_product_patient_history', 'fn': get_product_patient_history,
     'description': get_product_patient_history.__doc__,
     'parameters': _schema({'product': {'type': 'string'}, 'limit': {'type': 'integer'}}, ['product'])},
]

TOOL_FNS = {t['name']: t['fn'] for t in TOOLS}

# The only tools allowed to return patient identity (name/mobile) - see this
# module's docstring. Every other tool still goes through _guard_no_pii.
PII_ALLOWED_TOOLS = {'identify_person', 'get_patient_history', 'get_product_patient_history'}


def run_tool(name, args):
    """Dispatch a model tool call to the local function, guarding output -
    except for PII_ALLOWED_TOOLS, which intentionally return real patient
    identity and are exempted from that guard on purpose."""
    if name not in TOOL_FNS:
        return {'error': f'unknown tool {name}'}
    try:
        result = TOOL_FNS[name](**(args or {}))
        return result if name in PII_ALLOWED_TOOLS else _guard_no_pii(result)
    except Exception as e:  # surface tool errors to the model, don't crash the loop
        return {'error': f'{type(e).__name__}: {e}'}


def scrub_question(text):
    """No-op passthrough. Used to strip a 10-digit phone number out of the
    user's own question before it reached the model - but the owner
    explicitly asked for phone-number-based patient lookup (see
    get_patient_history), which needs that exact number to reach the model
    unaltered, so scrubbing it here would silently break that feature. Kept
    as a function (not deleted) so ask.py's call site doesn't need to change
    if scrubbing something else here is ever needed again."""
    return text or ''

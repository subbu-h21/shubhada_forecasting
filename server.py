r"""
Pharmacy Ready Reckoner - Mobile Server
=========================================
Serves the same analysis as run_reckoner.py over your home WiFi, so you can
open it on your phone's browser. Reuses every calculation from
run_reckoner.py directly - no logic is duplicated.

Start it:
    python server.py
Then on your phone (same WiFi as this PC), open:
    http://<this-PC's-LAN-IP>:8420
"""
import io
import json
import secrets
import threading
import time
from functools import wraps
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, request, Response, jsonify

import run_reckoner as rk

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / 'server_config.json'

app = Flask(__name__)

# compute_all() takes real time (branch-wise forecast alone runs the
# per-product forecast 3x). Recomputing it on every page load makes the
# mobile page feel hung, so cache the result and only recompute when data
# actually changed (a file was ingested) or the client explicitly asks to.
_cache_lock = threading.Lock()
_cache = {'data': None, 'stamp': None}


def get_cached_data(force=False):
    with _cache_lock:
        if force or _cache['data'] is None:
            _cache['data'] = compute_all()
            _cache['stamp'] = time.time()
        return _cache['data'], _cache['stamp']


def invalidate_cache():
    with _cache_lock:
        _cache['data'] = None


def load_or_create_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    cfg = {'username': 'owner', 'password': secrets.token_hex(4)}
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    return cfg


CONFIG = load_or_create_config()


def check_auth(username, password):
    return username == CONFIG['username'] and password == CONFIG['password']


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return Response(
                'Login required', 401,
                {'WWW-Authenticate': 'Basic realm="Pharmacy Reckoner"'})
        return f(*args, **kwargs)
    return decorated


def df_records(df, limit=None):
    d = df.replace({np.nan: None})
    if limit:
        d = d.head(limit)
    return json.loads(d.to_json(orient='records'))


def compute_all():
    sales = pd.read_csv(rk.SALES_MASTER) if rk.SALES_MASTER.exists() else pd.DataFrame()
    purch = pd.read_csv(rk.PURCH_MASTER) if rk.PURCH_MASTER.exists() else pd.DataFrame()
    if sales.empty or purch.empty:
        return None

    forecast, target_month, all_months = rk.build_demand_forecast(sales)
    branch_summary, branch_forecast = rk.build_branch_report(sales)
    footfall_daily, footfall_monthly, footfall_forecast, footfall_target_month = rk.build_footfall(sales)
    monthly_trend, trend_prediction, trend_target_month = rk.build_monthly_trend(sales, purch, footfall_forecast)
    over_under = rk.build_over_under(sales, purch)
    profit, profit_unknown = rk.build_profit_margin(sales, purch)
    latest_month = all_months[-1]
    ptr_high, mrp_missing, gifts, variance = rk.build_purchase_errors(purch, latest_month)
    scheme_missed, _ = rk.build_scheme_consistency(purch, latest_month)
    disc_missed, _ = rk.build_discount_consistency(purch, latest_month)

    total_pretax_rev = profit['Pretax_Revenue'].sum()
    overall_margin = round(profit['Gross_Profit'].sum() / total_pretax_rev * 100, 1) if total_pretax_rev > 0 else 0

    summary = {
        'months': all_months,
        'latest_month': latest_month,
        'target_month': target_month,
        'products_forecast': len(forecast),
        'predicted_value': round(forecast['Predicted_Value'].sum(), 2),
        'over_purchased_count': int((over_under['Status'] == 'Over-purchased').sum()),
        'dead_stock_count': int((over_under['Status'] == 'Purchased, never sold (dead stock)').sum()),
        'gross_profit': round(profit['Gross_Profit'].sum(), 2),
        'overall_margin_pct': overall_margin,
        'unknown_cost_revenue': round(profit_unknown['Revenue'].sum(), 2),
        'ptr_high_count': len(ptr_high),
        'mrp_missing_count': len(mrp_missing),
        'mrp_variance_count': len(variance),
        'scheme_shortfall_count': len(scheme_missed),
        'discount_shortfall_count': len(disc_missed),
    }

    forecast_out = forecast.rename(columns={'Predicted_Qty': 'qty', 'Predicted_Value': 'value',
                                             'Trend': 'trend', 'Avg_Price': 'avg_price'})
    over_under_out = over_under.rename(columns={'Sold_Qty': 'sold_qty', 'Purch_Qty': 'purch_qty',
                                                 'Net_Qty': 'net_qty', 'Status': 'status',
                                                 'Net_Value_Approx': 'value_impact'})
    profit_out = profit.rename(columns={'Qty_Sold': 'qty_sold', 'Gross_Profit': 'gross_profit',
                                         'Margin_Pct': 'margin_pct', 'Pretax_Revenue': 'revenue'})
    ptr_out = ptr_high.rename(columns={'Sale Rate': 'ptr', 'Item Total': 'item_total',
                                        'Excess': 'excess', 'Source_Month': 'month'})
    scheme_out = scheme_missed.rename(columns={'Free Qty': 'free_qty', 'Qty': 'qty',
                                                'FreeRatio': 'free_ratio', 'Typical_Ratio': 'typical_ratio',
                                                'Shortfall_Qty': 'shortfall'})
    disc_out = disc_missed.rename(columns={'Disc Percentage': 'disc_pct', 'Typical_Disc': 'typical_disc',
                                            'Disc_Gap_pct_pts': 'gap', 'Value_Lost_Approx': 'value_lost',
                                            'Qty': 'qty'})

    branch_summary_out = branch_summary.rename(columns={'Total_Revenue': 'total_revenue', 'Growth_Pct': 'growth_pct',
                                                          'Invoices': 'invoices', 'Patients': 'patients'})
    branch_summary_out.columns = [c if not c.startswith('Revenue_') else c.replace('Revenue_', 'rev_')
                                   for c in branch_summary_out.columns]
    branch_forecast_out = branch_forecast.rename(columns={'Predicted_Qty': 'qty', 'Predicted_Value': 'value',
                                                           'Trend': 'trend', 'Avg_Price': 'avg_price'})

    footfall_daily_out = footfall_daily.copy()
    footfall_daily_out['Day'] = footfall_daily_out['Day'].astype(str)
    footfall_forecast_out = footfall_forecast.rename(columns={
        'Latest_Avg_Daily': 'latest_avg_daily', 'Predicted_Avg_Daily': 'predicted_avg_daily',
        'Predicted_Total_Footfall': 'predicted_total', 'Growth_Pct': 'growth_pct'})

    monthly_trend_out = monthly_trend.rename(columns={
        'Over_Purchased': 'over_purchased', 'Under_Purchased': 'under_purchased', 'Balanced': 'balanced',
        'Dead_Stock': 'dead_stock', 'Footfall_Total': 'footfall_total', 'Footfall_Avg_Daily': 'footfall_avg_daily'})
    trend_prediction_out = trend_prediction.rename(columns={
        'Metric': 'metric', 'Goal': 'goal', 'Latest': 'latest', 'Previous': 'previous',
        'Predicted_Next': 'predicted_next', 'On_Track': 'on_track'})

    return {
        'summary': summary,
        'branch_summary': df_records(branch_summary_out),
        'branch_forecast': df_records(branch_forecast_out[['Branch', 'Product', 'trend', 'qty', 'value']]),
        'footfall_daily': df_records(footfall_daily_out.rename(columns={'Day': 'day', 'Footfall': 'footfall'})),
        'footfall_forecast': df_records(footfall_forecast_out),
        'footfall_target_month': footfall_target_month,
        'monthly_trend': df_records(monthly_trend_out),
        'trend_prediction': df_records(trend_prediction_out),
        'trend_target_month': trend_target_month,
        'forecast': df_records(forecast_out[['Product', 'trend', 'qty', 'avg_price', 'value']]),
        'over_under': df_records(over_under_out[['Product', 'status', 'purch_qty', 'sold_qty', 'net_qty', 'value_impact']]),
        'profit': df_records(profit_out[['Product', 'qty_sold', 'revenue', 'gross_profit', 'margin_pct']]),
        'profit_unknown': df_records(profit_unknown.rename(columns={'Qty_Sold': 'qty_sold', 'Revenue': 'revenue'})),
        'ptr_high': df_records(ptr_out[['month', 'Date', 'Inv.No', 'Supplier', 'Product', 'MRP', 'ptr', 'Qty', 'excess']]),
        'mrp_variance': df_records(variance.rename(columns={'min': 'lo', 'max': 'hi', 'count': 'lines', 'ratio': 'ratio'})),
        'scheme_shortfall': df_records(scheme_out[['Date', 'Inv.No', 'Supplier', 'Product', 'qty', 'free_qty', 'free_ratio', 'typical_ratio', 'shortfall']]),
        'discount_shortfall': df_records(disc_out[['Date', 'Inv.No', 'Supplier', 'Product', 'qty', 'disc_pct', 'typical_disc', 'gap', 'value_lost']]),
    }


@app.route('/api/data')
@requires_auth
def api_data():
    force = request.args.get('refresh') == '1'
    data, stamp = get_cached_data(force=force)
    if data is None:
        return jsonify({'error': 'No data ingested yet. Run run_reckoner.py at least once, or upload files below.'}), 400
    data = dict(data)
    data['computed_at'] = stamp
    return jsonify(data)


@app.route('/api/products')
@requires_auth
def api_products():
    sales = pd.read_csv(rk.SALES_MASTER, usecols=['Product']) if rk.SALES_MASTER.exists() else pd.DataFrame(columns=['Product'])
    purch = pd.read_csv(rk.PURCH_MASTER, usecols=['Product']) if rk.PURCH_MASTER.exists() else pd.DataFrame(columns=['Product'])
    names = sorted(set(sales['Product'].dropna()) | set(purch['Product'].dropna()))
    return jsonify(names)


@app.route('/api/product')
@requires_auth
def api_product():
    name = request.args.get('name', '')
    if not name:
        return jsonify({'error': 'name is required'}), 400

    sales = pd.read_csv(rk.SALES_MASTER) if rk.SALES_MASTER.exists() else pd.DataFrame()
    purch = pd.read_csv(rk.PURCH_MASTER) if rk.PURCH_MASTER.exists() else pd.DataFrame()

    s = sales[sales['Product'] == name].copy()
    p = purch[purch['Product'] == name].copy()

    if s.empty and p.empty:
        return jsonify({'error': f'No records found for "{name}"'}), 404

    if not s.empty:
        s['Branch'] = s['Inv.No'].apply(rk.extract_branch)
    s_hist = s.sort_values('Date', ascending=False)
    p_hist = p.sort_values('Date', ascending=False)

    total_sold_qty = s['Qty'].sum() if not s.empty else 0
    total_sold_value = s['Item Total'].sum() if not s.empty else 0
    if not p.empty:
        p['Factor'] = p['Factor'].replace(0, 1).fillna(1)
        total_purch_units = (p['Qty'] * p['Factor']).sum()
        total_purch_value = p['Item Total'].sum()
    else:
        total_purch_units, total_purch_value = 0, 0

    summary = {
        'product': name,
        'total_sold_qty': float(total_sold_qty),
        'total_sold_value': round(float(total_sold_value), 2),
        'avg_selling_price': round(float(total_sold_value / total_sold_qty), 2) if total_sold_qty else None,
        'total_purchased_units': round(float(total_purch_units), 1),
        'total_purchase_value': round(float(total_purch_value), 2),
        'avg_cost_per_unit': round(float(total_purch_value / total_purch_units), 4) if total_purch_units else None,
        'sale_lines': len(s), 'purchase_lines': len(p),
    }

    sales_out = df_records(s_hist.rename(columns={'Item Total': 'item_total'})[
        ['Date', 'Inv.No', 'Patient', 'Branch', 'Qty', 'MRP', 'Disc Percentage', 'item_total']
    ]) if not s.empty else []
    purch_out = df_records(p_hist.rename(columns={'Item Total': 'item_total', 'Sale Rate': 'ptr'})[
        ['Date', 'Inv.No', 'Supplier', 'Qty', 'Factor', 'MRP', 'ptr', 'Free Qty', 'item_total']
    ]) if not p.empty else []

    return jsonify({'summary': summary, 'sales': sales_out, 'purchases': purch_out})


@app.route('/api/upload', methods=['POST'])
@requires_auth
def api_upload():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No files received'}), 400
    saved = []
    for f in files:
        if not f.filename.lower().endswith('.xlsx'):
            continue
        dest = rk.RAW_DIR / f.filename
        f.save(dest)
        saved.append(f.filename)
    log = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(log):
        rk.ingest()
    invalidate_cache()
    return jsonify({'saved': saved, 'log': log.getvalue()})


@app.route('/')
@requires_auth
def index():
    return Response((ROOT / 'mobile_dashboard.html').read_text(encoding='utf-8'), mimetype='text/html')


if __name__ == '__main__':
    import socket
    hostname = socket.gethostname()
    try:
        lan_ip = socket.gethostbyname(hostname)
    except Exception:
        lan_ip = '<this PC\'s IP>'
    print()
    print('=' * 60)
    print('Pharmacy Ready Reckoner - Mobile Server')
    print('=' * 60)
    print(f'Username: {CONFIG["username"]}')
    print(f'Password: {CONFIG["password"]}')
    print(f'(saved in server_config.json - change it any time)')
    print()
    print(f'On your phone (same WiFi), open: http://{lan_ip}:8420')
    print('Press Ctrl+C to stop the server.')
    print('=' * 60)
    print()
    print('Warming up (computing reports once)...')
    get_cached_data()
    print('Ready.')
    print()
    from waitress import serve
    serve(app, host='0.0.0.0', port=8420)

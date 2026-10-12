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
import logging
import os
import secrets
import threading
import time
import traceback
import urllib.parse
import urllib.request
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, request, Response, jsonify
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

import ask as ask_mod
import ask_limits
import run_reckoner as rk
import salary as salary_mod

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / 'server_config.json'

# --- Logging --------------------------------------------------------------
# Without this, a crash or a repeated silent error while nobody's watching
# the console window (overnight, or a minimized/closed terminal) leaves no
# record anywhere. A small rotating file is enough for a single-PC tool -
# console output is kept too, for whoever does have the window open.
LOG_PATH = ROOT / 'server.log'
logger = logging.getLogger('reckoner_server')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _file_handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    _file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(_file_handler)
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(_console_handler)

# Master CSVs may still be in the pre-normalization format (B2B rows stored
# in strips) from before this fix - migrate once, up front, before anything
# in this process reads SALES_MASTER directly (compute_all/api_product do).
rk.ensure_sales_master_normalized()

app = Flask(__name__)


@app.errorhandler(Exception)
def _handle_unexpected_error(e):
    # HTTPException (404 on a typo'd/bot-scanned URL, 405, a 400 from bad
    # input) is Flask/Werkzeug's own normal control flow, not a bug - it's
    # also an Exception subclass, so without this check every routine 404
    # would get logged as a crash (drowning out real ones) and re-served as
    # a wrong, scarier 500. Let Flask handle its own HTTP errors normally;
    # only genuine unhandled exceptions reach the logging below.
    if isinstance(e, HTTPException):
        return e
    logger.error('Unhandled error on %s %s:\n%s', request.method, request.path, traceback.format_exc())
    return jsonify({'error': 'Something went wrong on the server. Check server.log for details.'}), 500

# --- Sessions -----------------------------------------------------------
# Basic Auth (the old scheme here) has no real logout - once a browser has
# cached the credentials, it keeps silently resending them forever, so a
# phone left logged in stays logged in for anyone who picks it up. This
# replaces it with a server-issued token that expires SESSION_TIMEOUT_SECONDS
# after the last authenticated request touches it (a sliding window, not a
# fixed one) - both "1hr of inactivity" and "1hr after the tab closes"
# fall out of that one rule, since a closed tab just stops sending requests.
SESSION_TIMEOUT_SECONDS = 60 * 60

_sessions_lock = threading.Lock()
_sessions = {}  # token -> {'username': str, 'last_active': float}


def _create_session(username):
    token = secrets.token_hex(32)
    with _sessions_lock:
        _sessions[token] = {'username': username, 'last_active': time.time()}
    return token


def _touch_session(token):
    """Refresh a session's activity clock. Returns False if the token is
    unknown or has been idle past the timeout (and drops it in that case)."""
    with _sessions_lock:
        sess = _sessions.get(token)
        if not sess:
            return False
        if time.time() - sess['last_active'] > SESSION_TIMEOUT_SECONDS:
            del _sessions[token]
            return False
        sess['last_active'] = time.time()
        return True


def _drop_session(token):
    with _sessions_lock:
        _sessions.pop(token, None)

# compute_all() takes real time (branch-wise forecast alone runs the
# per-product forecast 3x). Recomputing it on every page load makes the
# mobile page feel hung, so cache the result and only recompute when data
# actually changed or the client explicitly asks to.
#
# "Changed" isn't only "this server's own upload/holiday handler ran" - the
# README's own monthly instruction is to run `python run_reckoner.py`
# directly, which this process has no other way to hear about. Rather than
# trust every write path to remember to call invalidate_cache() (a mobile
# upload does; a CLI run started separately never did), the cache checks the
# master files' own modification time on every request and treats a change
# there - from ANY source - as a reason to recompute.
_cache_lock = threading.Lock()
_cache = {'data': None, 'stamp': None, 'source_mtime': None}
_salary_inputs = {'v': None}  # filled by compute_all()


def _master_files_mtime():
    paths = [rk.SALES_MASTER, rk.PURCH_MASTER, rk.MANIFEST_PATH]
    mtimes = [p.stat().st_mtime for p in paths if p.exists()]
    return max(mtimes) if mtimes else None


def get_cached_data(force=False):
    with _cache_lock:
        current_mtime = _master_files_mtime()
        stale = _cache['data'] is not None and current_mtime != _cache['source_mtime']
        if force or _cache['data'] is None or stale:
            if stale and not force:
                logger.info('Master data changed on disk since last compute (e.g. a CLI run_reckoner.py run) - recomputing.')
            _cache['data'] = compute_all()
            _cache['stamp'] = time.time()
            _cache['source_mtime'] = current_mtime
        return _cache['data'], _cache['stamp']


def invalidate_cache():
    with _cache_lock:
        _cache['data'] = None


def load_or_create_config():
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text())
        if 'credentials' not in cfg:
            # Migrate the old single-login format to the multi-credential list.
            cfg = {'credentials': [{'username': cfg['username'], 'password': cfg['password']}]}
            CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
        return cfg
    cfg = {'credentials': [{'username': 'owner', 'password': secrets.token_hex(4)}]}
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    return cfg


def add_credential(username, password):
    cfg = load_or_create_config()
    cfg['credentials'] = [c for c in cfg['credentials'] if c['username'] != username]
    cfg['credentials'].append({'username': username, 'password': password})
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    global CONFIG
    CONFIG = cfg


CONFIG = load_or_create_config()


def check_auth(username, password):
    # secrets.compare_digest instead of == - a plain string compare leaks
    # timing information about how many leading characters matched.
    return any(c['username'] == username and secrets.compare_digest(c['password'], password)
               for c in CONFIG['credentials'])


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-Session-Token')
        if not token or not _touch_session(token):
            return jsonify({'error': 'Session expired or not logged in'}), 401
        return f(*args, **kwargs)
    return decorated


# --- Login throttling ----------------------------------------------------
# This app is reachable to anyone on the home WiFi / Tailscale tailnet, and
# had no brute-force protection at all - unlimited login attempts against a
# default 8-hex-char generated password. A simple per-source-IP counter with
# a short lockout after repeated failures is enough for a single-pharmacy
# tool without adding a dependency.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 60

_login_attempts_lock = threading.Lock()
_login_attempts = {}  # ip -> {'count': int, 'locked_until': float}


@app.route('/api/login', methods=['POST'])
def api_login():
    ip = request.remote_addr or 'unknown'
    now = time.time()
    with _login_attempts_lock:
        entry = _login_attempts.get(ip)
        if entry and entry['locked_until'] > now:
            wait = int(entry['locked_until'] - now) + 1
            return jsonify({'error': f'Too many attempts - try again in {wait}s'}), 429

    body = request.get_json(force=True, silent=True) or {}
    username, password = body.get('username', ''), body.get('password', '')
    if not check_auth(username, password):
        with _login_attempts_lock:
            entry = _login_attempts.setdefault(ip, {'count': 0, 'locked_until': 0})
            entry['count'] += 1
            if entry['count'] >= LOGIN_MAX_ATTEMPTS:
                entry['locked_until'] = time.time() + LOGIN_LOCKOUT_SECONDS
                entry['count'] = 0
                logger.warning('Login locked out for %s after %d failed attempts (user "%s")', ip, LOGIN_MAX_ATTEMPTS, username)
        return jsonify({'error': 'Incorrect username or password'}), 401

    with _login_attempts_lock:
        _login_attempts.pop(ip, None)
    token = _create_session(username)
    logger.info('Login OK: user "%s" from %s', username, ip)
    return jsonify({'token': token, 'username': username, 'timeout_seconds': SESSION_TIMEOUT_SECONDS})


@app.route('/api/logout', methods=['POST'])
def api_logout():
    token = request.headers.get('X-Session-Token')
    if token:
        _drop_session(token)
    return jsonify({'ok': True})


@app.route('/api/ping', methods=['POST'])
@requires_auth
def api_ping():
    # No-op endpoint - @requires_auth already refreshed the session's activity
    # clock. The mobile page calls this on real user interaction (click/key/
    # touch/scroll) so an actively-used-but-quiet tab (browsing already-loaded
    # data, no new API calls) doesn't time out from under someone still there.
    return jsonify({'ok': True})


def df_records(df, limit=None):
    d = df.replace({np.nan: None})
    if limit:
        d = d.head(limit)
    return json.loads(d.to_json(orient='records'))


def compute_all():
    # SALES_MASTER is normalized once, at ingest time - never call
    # normalize_sale_units() again here, it would double-convert B2B rows.
    sales = pd.read_csv(rk.SALES_MASTER) if rk.SALES_MASTER.exists() else pd.DataFrame()
    purch = pd.read_csv(rk.PURCH_MASTER) if rk.PURCH_MASTER.exists() else pd.DataFrame()
    purch = rk.ensure_purch_defaults(purch)  # older master CSVs may predate an optional column
    if sales.empty or purch.empty:
        return None
    sales, purch = rk.add_calendar_month(sales), rk.add_calendar_month(purch)
    sales, purch = rk.canonicalize_employee_names(sales), rk.canonicalize_employee_names(purch)

    forecast, target_month, all_months = rk.build_demand_forecast(sales)
    branch_summary, branch_forecast = rk.build_branch_report(sales)
    optimum_stock = rk.build_optimum_stock(sales)
    footfall_daily, footfall_monthly, footfall_forecast, footfall_target_month = rk.build_footfall(sales)
    dist_lines, _ = rk.compute_distributor_lines(purch)
    employee_perf = rk.build_employee_performance(sales, purch, dist_lines)
    employee_monthly, _ = rk.build_employee_monthly(sales, purch, dist_lines)
    employee_scorecard = rk.build_employee_scorecard(sales, purch, dist_lines, employee_monthly)
    customer_loyalty, mobile_col = rk.build_customer_loyalty(sales)
    refill_due = rk.build_refill_due(sales)
    hourly_branch, employee_windows, hours_included_months, hours_excluded_months, _ = rk.build_hours_staff(sales)
    monthly_trend, trend_prediction, trend_target_month = rk.build_monthly_trend(sales, purch, footfall_forecast)
    daywise_forecast, dow_index, daywise_target_month = rk.build_daywise_forecast(sales, footfall_forecast)
    over_under = rk.build_over_under(sales, purch)
    profit, profit_unknown = rk.build_profit_margin(sales, purch)
    # Salary needs a few of these frames again on every Salary screen action
    # (logging an incident, a rating) - keep them so those stay instant
    # instead of recomputing the whole reckoner. A salary problem must never
    # take the rest of the dashboard down with it.
    try:
        _salary_inputs['v'] = salary_mod.build_inputs(sales, employee_monthly, employee_scorecard, profit)
    except Exception:
        logger.error('Salary inputs failed:\n%s', traceback.format_exc())
        _salary_inputs['v'] = None
    channel_profit = rk.build_channel_profit(sales, purch)
    distributor_summary = rk.build_distributor_summary(dist_lines)
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
        'refill_overdue_count': int((refill_due['Status'].isin(['Overdue', 'Likely lost'])).sum()) if refill_due is not None else 0,
    }

    # qty fields sent to mobile are in strips (Qty / Factor), matching Excel
    forecast_out = forecast.rename(columns={'Predicted_Qty_Strips': 'qty', 'Predicted_Value': 'value',
                                             'Trend': 'trend', 'Avg_Price': 'avg_price'})
    over_under_out = over_under.rename(columns={'Sold_Qty_Strips': 'sold_qty', 'Purch_Qty_Strips': 'purch_qty',
                                                 'Net_Qty_Strips': 'net_qty', 'Status': 'status',
                                                 'Net_Value_Approx': 'value_impact'})
    profit_out = profit.rename(columns={'Qty_Sold_Strips': 'qty_sold', 'Gross_Profit': 'gross_profit',
                                         'Margin_Pct': 'margin_pct', 'Pretax_Revenue': 'revenue'})
    ptr_out = ptr_high.rename(columns={'Sale Rate': 'ptr', 'Item Total': 'item_total',
                                        'Excess': 'excess', 'Calendar_Month': 'month'})
    scheme_out = scheme_missed.rename(columns={'Free Qty': 'free_qty', 'Qty': 'qty',
                                                'FreeRatio': 'free_ratio', 'Typical_Ratio': 'typical_ratio',
                                                'Shortfall_Qty': 'shortfall'})
    disc_out = disc_missed.rename(columns={'Disc Percentage': 'disc_pct', 'Typical_Disc': 'typical_disc',
                                            'Disc_Gap_pct_pts': 'gap', 'Value_Lost_Approx': 'value_lost',
                                            'Qty': 'qty'})

    distributor_summary_out = distributor_summary.rename(columns={
        'Total_Invoice_Value': 'total_invoice_value', 'Embedded_Profit': 'embedded_profit',
        'Margin_Pct': 'margin_pct', 'Invoices': 'invoices', 'Months_Active': 'months_active'})

    branch_summary_out = branch_summary.rename(columns={'Total_Revenue': 'total_revenue', 'Growth_Pct': 'growth_pct',
                                                          'Invoices': 'invoices', 'Patients': 'patients'})
    branch_summary_out.columns = [c if not c.startswith('Revenue_') else c.replace('Revenue_', 'rev_')
                                   for c in branch_summary_out.columns]
    branch_forecast_out = branch_forecast.rename(columns={'Predicted_Qty_Strips': 'qty', 'Predicted_Value': 'value',
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

    daywise_out = daywise_forecast.copy()
    daywise_out['Date'] = daywise_out['Date'].astype(str)
    daywise_out = daywise_out.rename(columns={'Weekday': 'weekday', 'Predicted_Footfall': 'footfall',
                                               'Predicted_Revenue': 'revenue', 'Date': 'date'})

    hourly_branch_out = None
    if hourly_branch is not None:
        hourly_branch_out = df_records(hourly_branch.rename(columns={
            'Branch': 'branch', 'Hour': 'hour', 'Avg_Bills': 'avg_bills', 'Avg_Billers': 'avg_billers',
            'Avg_Bills_Per_Biller': 'avg_bills_per_biller', 'Days_Observed': 'days_observed'}))
    employee_windows_out = None
    if employee_windows is not None:
        employee_windows_out = df_records(employee_windows.rename(columns={
            'Employee': 'employee', 'Days_Active': 'days_active', 'Start_Time': 'start_time',
            'End_Time': 'end_time', 'Span_H': 'span_hours', 'Median_Bills_Per_Day': 'median_bills_per_day',
            'Total_Bills': 'total_bills'})[['employee', 'days_active', 'start_time', 'end_time', 'span_hours',
                                            'median_bills_per_day', 'total_bills']])

    return {
        'summary': summary,
        'branch_summary': df_records(branch_summary_out),
        'branch_forecast': df_records(branch_forecast_out[['Branch', 'Product', 'trend', 'qty', 'value']]),
        'optimum_stock': df_records(optimum_stock.assign(Avg_Per_Day=optimum_stock['Avg_Per_Day'].round(2)).rename(columns={
            'Optimum_Stock_Strips': 'optimum_strips', 'Months_Seen': 'months_seen', 'Avg_Per_Day': 'avg_per_day'})[
            ['Branch', 'Product', 'optimum_strips', 'avg_per_day', 'months_seen']]),
        'footfall_daily': df_records(footfall_daily_out.rename(columns={'Day': 'day', 'Footfall': 'footfall'})),
        'footfall_forecast': df_records(footfall_forecast_out),
        'footfall_target_month': footfall_target_month,
        'monthly_trend': df_records(monthly_trend_out),
        'trend_prediction': df_records(trend_prediction_out),
        'trend_target_month': trend_target_month,
        'daywise_forecast': df_records(daywise_out),
        'daywise_target_month': daywise_target_month,
        'employee_billed_by': df_records(employee_perf['billed_by']) if employee_perf['billed_by'] is not None else None,
        'employee_given_by': df_records(employee_perf['given_by']) if employee_perf['given_by'] is not None else None,
        'employee_created_by': df_records(employee_perf['created_by']) if employee_perf['created_by'] is not None else None,
        'self_serve_hourly': df_records(employee_perf['self_serve_hourly'].rename(columns={
            'Branch': 'branch', 'Hour': 'hour', 'Lines': 'lines', 'Self_Serve_Pct': 'self_serve_pct'}))
            if employee_perf['self_serve_hourly'] is not None else None,
        'fetch_pairs': df_records(employee_perf['fetch_pairs'].head(30).rename(columns={
            'Biller': 'biller', 'Fetcher': 'fetcher', 'Lines': 'lines', 'Share_Of_Biller_Pct': 'share_of_biller_pct'}))
            if employee_perf['fetch_pairs'] is not None else None,
        'customer_loyalty': df_records(customer_loyalty) if customer_loyalty is not None else None,
        'customer_loyalty_col': mobile_col,
        'employee_monthly': df_records(employee_monthly.rename(
            columns={c: c.lower() for c in employee_monthly.columns}).rename(columns={'calendar_month': 'month'}))
            if employee_monthly is not None else None,
        'employee_scorecard': df_records(employee_scorecard.rename(
            columns={c: c.lower() for c in employee_scorecard.columns}).rename(columns={'calendar_month': 'month'}))
            if employee_scorecard is not None else None,
        'score_weights': rk.SCORE_WEIGHTS,
        'refill_due': df_records(refill_due[refill_due['Status'] != 'Not due yet'].rename(columns={
            'Mobile': 'mobile', 'Patient': 'patient', 'Product': 'product', 'Purchases_Seen': 'purchases_seen',
            'Avg_Gap_Days': 'avg_gap_days', 'Min_Gap_Days': 'min_gap_days', 'Max_Gap_Days': 'max_gap_days',
            'Last_Purchase': 'last_purchase', 'Next_Due': 'next_due', 'Days_Until_Due': 'days_until_due',
            'Status': 'status'})) if refill_due is not None else None,
        'hourly_branch': hourly_branch_out,
        'employee_windows': employee_windows_out,
        'hours_included_months': hours_included_months,
        'hours_excluded_months': hours_excluded_months,
        'forecast': df_records(forecast_out[['Product', 'trend', 'qty', 'avg_price', 'value']]),
        'over_under': df_records(over_under_out[['Product', 'status', 'purch_qty', 'sold_qty', 'net_qty', 'value_impact']]),
        'distributor_summary': df_records(distributor_summary_out[['Supplier', 'invoices', 'total_invoice_value', 'embedded_profit', 'margin_pct', 'months_active']]),
        'profit': df_records(profit_out[['Product', 'qty_sold', 'revenue', 'gross_profit', 'margin_pct']]),
        'profit_unknown': df_records(profit_unknown.rename(columns={'Qty_Sold_Strips': 'qty_sold', 'Revenue': 'revenue'})),
        'channel_profit': df_records(channel_profit.rename(columns={
            'Branch': 'channel', 'Type': 'type', 'Invoices': 'invoices', 'Revenue': 'revenue',
            'COGS': 'cogs', 'Gross_Profit': 'gross_profit', 'Margin_Pct': 'margin_pct',
            'Revenue_Cost_Unknown': 'revenue_cost_unknown'})),
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


@app.route('/api/holidays_calendar')
@requires_auth
def api_holidays_calendar():
    month = request.args.get('month')
    if not month:
        data, _ = get_cached_data()
        if data is None:
            return jsonify({'error': 'No data yet'}), 400
        month = data['daywise_target_month']
    ty, tm = map(int, month.split('-'))
    days_count = rk.days_in_month(ty, tm)
    holidays = rk.load_holidays()
    dates = pd.date_range(pd.Timestamp(ty, tm, 1), periods=days_count, freq='D')
    out = []
    for d in dates:
        day_type, label = rk.classify_special_day(d, holidays)
        out.append({
            'date': str(d.date()), 'weekday': rk.WEEKDAY_NAMES[d.weekday()],
            'day_type': day_type, 'label': label,
            'is_custom': str(d.date()) in holidays,
        })
    return jsonify({'month': month, 'days': out})


@app.route('/api/holidays', methods=['POST'])
@requires_auth
def api_holidays_post():
    body = request.get_json(force=True, silent=True) or {}
    date_str = body.get('date')
    name = body.get('name')
    if not date_str:
        return jsonify({'error': 'date is required'}), 400
    try:
        pd.Timestamp(date_str)
    except Exception:
        return jsonify({'error': 'invalid date'}), 400
    rk.save_holiday(date_str, name)
    invalidate_cache()
    logger.info('Holiday %s: %s (%s)', 'set' if name else 'cleared', date_str, name or '')
    return jsonify({'ok': True, 'date': date_str, 'name': name})


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

    # SALES_MASTER is normalized once, at ingest time - never call
    # normalize_sale_units() again here, it would double-convert B2B rows.
    sales = pd.read_csv(rk.SALES_MASTER) if rk.SALES_MASTER.exists() else pd.DataFrame()
    purch = pd.read_csv(rk.PURCH_MASTER) if rk.PURCH_MASTER.exists() else pd.DataFrame()
    purch = rk.ensure_purch_defaults(purch)  # older master CSVs may predate an optional column
    sales, purch = rk.add_calendar_month(sales), rk.add_calendar_month(purch)

    s = sales[sales['Product'] == name].copy()
    p = purch[purch['Product'] == name].copy()

    if s.empty and p.empty:
        return jsonify({'error': f'No records found for "{name}"'}), 404

    if not s.empty:
        s['Branch'] = s['Inv.No'].apply(rk.extract_branch)
    s_hist = s.sort_values('Date', ascending=False)
    p_hist = p.sort_values('Date', ascending=False)

    # Factor (pack size) is a physical attribute of the product - one
    # representative value covers it. Sales Qty is individual units (needs
    # /Factor to become strips); Purchase Qty is already in packs/strips.
    factor_source = s['Factor'] if not s.empty else (p['Factor'] if not p.empty else pd.Series([1]))
    mode = factor_source.mode()
    factor = float(mode.iat[0]) if len(mode) else 1.0
    if not factor:
        factor = 1.0

    total_sold_qty = s['Qty'].sum() if not s.empty else 0
    total_sold_value = s['Item Total'].sum() if not s.empty else 0
    if not p.empty:
        p['Factor'] = p['Factor'].replace(0, 1).fillna(1)
        total_purch_units = (p['Qty'] * p['Factor']).sum()
        total_purch_strips = p['Qty'].sum()
        total_purch_value = p['Item Total'].sum()
    else:
        total_purch_units, total_purch_strips, total_purch_value = 0, 0, 0

    summary = {
        'product': name,
        'factor': factor,
        'total_sold_qty': round(float(total_sold_qty) / factor, 1),
        'total_sold_value': round(float(total_sold_value), 2),
        'avg_selling_price': round(float(total_sold_value / total_sold_qty), 2) if total_sold_qty else None,
        'total_purchased_units': round(float(total_purch_strips), 1),
        'total_purchase_value': round(float(total_purch_value), 2),
        'avg_cost_per_unit': round(float(total_purch_value / total_purch_units), 4) if total_purch_units else None,
        'sale_lines': len(s), 'purchase_lines': len(p),
    }

    sales_hist_out = s_hist.copy()
    if not sales_hist_out.empty:
        sales_hist_out['Qty'] = (sales_hist_out['Qty'] / factor).round(1)
    sales_out = df_records(sales_hist_out.rename(columns={'Item Total': 'item_total'})[
        ['Date', 'Inv.No', 'Patient', 'Branch', 'Qty', 'MRP', 'Disc Percentage', 'item_total']
    ]) if not s.empty else []
    purch_out = df_records(p_hist.rename(columns={'Item Total': 'item_total', 'Sale Rate': 'ptr'})[
        ['Date', 'Inv.No', 'Supplier', 'Qty', 'Factor', 'MRP', 'ptr', 'Free Qty', 'item_total']
    ]) if not p.empty else []

    # Monthly trend for the chart - qty in strips both sides: sales Qty/Factor,
    # purchase Qty already in packs (same fix as Over-Under Purchased, just
    # expressed as strips instead of individual units here).
    s_monthly = s.groupby('Calendar_Month').agg(sold_qty=('Qty', 'sum'), sold_value=('Item Total', 'sum')) if not s.empty else pd.DataFrame()
    if not s_monthly.empty:
        s_monthly['sold_qty'] = (s_monthly['sold_qty'] / factor).round(1)
    if not p.empty:
        p_monthly = p.groupby('Calendar_Month').agg(purch_qty=('Qty', 'sum'), purch_value=('Item Total', 'sum'))
    else:
        p_monthly = pd.DataFrame()
    monthly = pd.concat([s_monthly, p_monthly], axis=1).fillna(0).reset_index().rename(columns={'index': 'month', 'Calendar_Month': 'month'})
    monthly = monthly.sort_values('month')
    for col in ['sold_qty', 'sold_value', 'purch_qty', 'purch_value']:
        if col not in monthly.columns:
            monthly[col] = 0
    monthly_out = df_records(monthly[['month', 'sold_qty', 'sold_value', 'purch_qty', 'purch_value']])

    return jsonify({'summary': summary, 'sales': sales_out, 'purchases': purch_out, 'monthly': monthly_out})


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
        # secure_filename strips path separators and '..' segments - without
        # it, an uploaded filename is used to build the save path as-is, and
        # a crafted name could write outside data/raw/.
        safe_name = secure_filename(f.filename)
        if not safe_name:
            continue
        dest = rk.RAW_DIR / safe_name
        f.save(dest)
        saved.append(safe_name)
    log = io.StringIO()
    import contextlib
    try:
        with contextlib.redirect_stdout(log):
            rk.ingest()
    except Exception:
        logger.error('Ingest failed during upload of %s:\n%s', saved, traceback.format_exc())
        return jsonify({'saved': saved, 'log': log.getvalue(),
                         'error': 'The file was saved but could not be processed - see server.log.'}), 500
    invalidate_cache()
    logger.info('Upload processed: %s', saved)
    return jsonify({'saved': saved, 'log': log.getvalue()})


# --- Salary (People > Salary) ---------------------------------------------
def _salary_payload(month=None):
    get_cached_data()  # recomputes first if the master files changed on disk
    inputs = _salary_inputs['v']
    months = salary_mod.months_available(inputs)
    if not month or month not in months:
        month = months[-1] if months else None
    result = salary_mod.compute_slips(inputs, month) if month else {'slips': [], 'needs_role': []}
    settings = salary_mod.load_settings()
    staff = sorted({s['employee'] for s in result['slips']} | set(result['needs_role'])
                   | {n for n, r in settings['roles'].items() if r != 'Not staff'})
    charts = salary_mod.load_charts().get(month, {}) if month else {}
    for sl in result['slips']:
        sl['share'] = {'phone': settings['phones'].get(sl['employee'], ''),
                       'text': salary_mod.slip_message(sl, settings['rules'])}
        sl['plan'] = salary_mod.action_plan(sl, settings['rules'])
    return {
        'month': month, 'months': months, 'slips': result['slips'],
        'sends': salary_mod.load_sends().get(month, {}) if month else {},
        'phones': settings['phones'], 'shares': salary_mod.load_shares().get(month, {}) if month else {}, 'needs_role': result['needs_role'],
        'summary': salary_mod.summary(result), 'staff': staff,
        'rules': settings['rules'], 'default_rules': salary_mod.DEFAULT_RULES, 'roles': settings['roles'],
        'salaries': settings['salaries'], 'half_day': settings['half_day'], 'shifts': settings['shifts'], 'role_names': salary_mod.ROLES, 'group_weights': salary_mod.GROUP_WEIGHTS,
        'groups': [{'code': c, 'name': n, 'kn': k} for c, n, k in salary_mod.GROUPS],
        'incident_types': salary_mod.INCIDENT_TYPES, 'rating_keys': salary_mod.RATING_KEYS,
        'incidents': sorted([r for r in salary_mod.load_incidents() if r.get('month') == month],
                            key=lambda r: (r['date'], r['logged_at']), reverse=True),
        'ratings': salary_mod.load_ratings().get(month, {}) if month else {},
        'charts': {e: {k: v for k, v in c.items() if k != 'rows'} for e, c in charts.items()},
        'commit': {
            'template': settings['commit_template'],
            'links': salary_mod.commit_links(inputs, month) if month else {},
            'pending': salary_mod.load_pending(),
            'approved': settings['commitments'],
            'targets': [{'code': c, 'label': l} for c, _, l, _ in salary_mod.COMMIT_TARGETS],
        },
        'targets': {
            'auto': salary_mod.auto_targets(inputs),
            'set': settings['targets'],
            'offered': {r: [c for c, _, _, rs in salary_mod.COMMIT_TARGETS if r in rs] for r in salary_mod.ROLES},
            'labels': {c: l for c, _, l, _ in salary_mod.COMMIT_TARGETS},
        },
    }


@app.route('/api/salary')
@requires_auth
def api_salary():
    if _salary_inputs['v'] is None and get_cached_data()[0] is None:
        return jsonify({'error': 'No data ingested yet.'}), 400
    return jsonify(_salary_payload(request.args.get('month')))


def _json_body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


@app.route('/api/salary/settings', methods=['POST'])
@requires_auth
def api_salary_settings():
    b = _json_body()
    _, err = salary_mod.update_settings(rules=b.get('rules'), roles=b.get('roles'), salaries=b.get('salaries'),
                                        half_day=b.get('half_day'), shifts=b.get('shifts'), targets=b.get('targets'),
                                        phones=b.get('phones'))
    if err:
        return jsonify({'error': err}), 400
    logger.info('Salary settings changed: %s', ', '.join(k for k in ('rules', 'roles', 'salaries', 'half_day', 'shifts', 'targets', 'phones') if b.get(k)))
    return jsonify(_salary_payload(b.get('month')))


@app.route('/api/salary/incident', methods=['POST'])
@requires_auth
def api_salary_incident():
    b = _json_body()
    if b.get('delete'):
        if not salary_mod.delete_incident(str(b['delete'])):
            return jsonify({'error': 'That entry was already removed.'}), 404
    else:
        _, err = salary_mod.add_incident(b.get('date'), b.get('employee'), b.get('type'), b.get('note'))
        if err:
            return jsonify({'error': err}), 400
    return jsonify(_salary_payload(b.get('month')))


@app.route('/api/salary/rating', methods=['POST'])
@requires_auth
def api_salary_rating():
    b = _json_body()
    err = salary_mod.save_rating(b.get('month'), b.get('employee'), b.get('ratings'))
    if err:
        return jsonify({'error': err}), 400
    return jsonify(_salary_payload(b.get('month')))


@app.route('/api/salary/commit/template', methods=['POST'])
@requires_auth
def api_salary_commit_template():
    b = _json_body()
    err = salary_mod.set_commit_template(b.get('url'))
    if err:
        return jsonify({'error': err}), 400
    logger.info('Commitment form template link saved')
    return jsonify(_salary_payload(b.get('month')))


@app.route('/api/salary/commit/import', methods=['POST'])
@requires_auth
def api_salary_commit_import():
    f = request.files.get('file')
    if not f or not f.filename.lower().endswith(('.xlsx', '.csv')):
        return jsonify({'error': 'Choose the answers sheet you downloaded (.xlsx).'}), 400
    data = f.read()
    if len(data) > 5 * 1024 * 1024:
        return jsonify({'error': 'That file is too big for the answers sheet.'}), 400
    res, err = salary_mod.import_commitments(data, f.filename)
    if err:
        return jsonify({'error': err}), 400
    logger.info('Commitment answers imported: %s', res)
    out = _salary_payload(request.form.get('month'))
    out['import_result'] = res
    return jsonify(out)


@app.route('/api/salary/commit/decide', methods=['POST'])
@requires_auth
def api_salary_commit_decide():
    b = _json_body()
    err = salary_mod.decide_commitment(str(b.get('id')), b.get('action'), b.get('employee') or None)
    if err:
        return jsonify({'error': err}), 400
    logger.info('Commitment %s: %s', b.get('action'), b.get('employee') or b.get('id'))
    return jsonify(_salary_payload(b.get('month')))


@app.route('/api/salary/shared', methods=['POST'])
@requires_auth
def api_salary_shared():
    b = _json_body()
    err = salary_mod.record_share(b.get('month'), b.get('employee'))
    if err:
        return jsonify({'error': err}), 400
    logger.info('Salary slip opened in WhatsApp: %s %s', b.get('month'), b.get('employee'))
    return jsonify({'ok': True, 'shares': salary_mod.load_shares().get(b.get('month'), {})})


# --- Send slips by WhatsApp automatically -----------------------------------
# Drives WhatsApp Web in the reckoner's OWN Chrome profile (linked once by a QR
# scan), with the order automation project's proven driver (salary_whatsapp.py):
# the typed message is read back and must match before Send is pressed, and a
# send is confirmed from the conversation itself. One run at a time; a person
# already sent this month is skipped unless the owner asks to send again.
WA_PROFILE_DIR = ROOT / '.whatsapp_profile'
WA_QR_WAIT_SECONDS = 180
_wa_lock = threading.Lock()
_wa_state = {'running': False, 'stage': '', 'log': [], 'results': [], 'month': None, 'test': False}


def _wa_log(message):
    with _wa_lock:
        _wa_state['log'] = (_wa_state['log'] + [f"{time.strftime('%H:%M:%S')} {message}"])[-60:]
    logger.info('[whatsapp] %s', message)


def _wa_stage(stage):
    with _wa_lock:
        _wa_state['stage'] = stage


class _WaUnlinked(Exception):
    """Raised past the per-person handler: once WhatsApp is unlinked, nothing
    more can go out, so the run stops instead of failing everyone left."""


def _wa_wait_linked(page, wa):
    """WhatsApp Web shows its QR code only after a while, and before that its
    page reads like a linked one - so watch for the QR for a good 40 seconds
    before trusting the link, then give the owner time to scan it."""
    deadline = time.time() + 40
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        if not wa.is_linked(page):
            _wa_stage('scan the QR code')
            _wa_log('Scan the QR code in the Chrome window: on your phone, WhatsApp > Linked devices > '
                    f'Link a device. Waiting up to {max(1, WA_QR_WAIT_SECONDS // 60)} minute'
                    + ('s.' if WA_QR_WAIT_SECONDS >= 120 else '.'))
            qr_deadline = time.time() + WA_QR_WAIT_SECONDS
            while time.time() < qr_deadline and not wa.is_linked(page):
                page.wait_for_timeout(2000)
            if not wa.is_linked(page):
                raise RuntimeError('WhatsApp was not linked - the QR code was not scanned in time.')
            _wa_log('Linked. Waiting for WhatsApp to finish loading.')
            page.wait_for_timeout(15000)
            return
    _wa_log('WhatsApp is linked.')


def _run_wa_send(month, jobs, test):
    """jobs: [(employee, number, text)]. Every person is tried on their own -
    one chat that won't open must not cost the others."""
    import salary_whatsapp as wa
    from playwright.sync_api import sync_playwright
    results = []
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(WA_PROFILE_DIR), channel='chrome', headless=False,
                args=['--start-maximized'], no_viewport=True)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                _wa_stage('opening WhatsApp')
                page.goto(wa.WEB_URL, wait_until='domcontentloaded', timeout=90000)
                _wa_wait_linked(page, wa)
                for i, (emp, number, text) in enumerate(jobs, 1):
                    _wa_stage(f'sending {i} of {len(jobs)}: {emp}')
                    try:
                        if not wa.open_chat(page, number, '', on_progress=_wa_log):
                            if not wa.is_linked(page):
                                # stop rather than fail everyone left: nothing more can go out
                                for left_emp, _, _ in jobs[i - 1:]:
                                    results.append({'employee': left_emp, 'sent': False,
                                                    'reason': 'not attempted - WhatsApp got unlinked'})
                                raise _WaUnlinked('WhatsApp got unlinked part-way - scan the QR again and send the rest.')
                            sent, reason = False, 'chat did not open (number not on WhatsApp?)'
                        else:
                            out = wa.send_message(page, text, dry_run=False, on_progress=_wa_log)
                            sent, reason = bool(out.get('sent')), out.get('reason') or ''
                    except _WaUnlinked:
                        raise
                    except Exception as exc:
                        logger.error('WhatsApp send to %s failed:\n%s', emp, traceback.format_exc())
                        sent, reason = False, str(exc)[:160]
                    results.append({'employee': emp, 'sent': sent, 'reason': reason})
                    if not test:
                        salary_mod.record_send(month, emp, sent, reason)
                    with _wa_lock:
                        _wa_state['results'] = list(results)
                done = sum(r['sent'] for r in results)
                _wa_stage(f'finished: sent {done} of {len(jobs)}')
                _wa_log(f'Finished: sent {done} of {len(jobs)}.')
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    except Exception as exc:
        logger.error('WhatsApp sending stopped:\n%s', traceback.format_exc())
        _wa_stage('stopped')
        _wa_log(f'Stopped: {exc}')
        with _wa_lock:
            _wa_state['results'] = results
    finally:
        with _wa_lock:
            _wa_state['running'] = False


@app.route('/api/salary/whatsapp/send', methods=['POST'])
@requires_auth
def api_salary_whatsapp_send():
    b = _json_body()
    payload = _salary_payload(b.get('month'))
    month = payload['month']
    by_name = {s['employee']: s for s in payload['slips']}
    wanted = [e for e in (b.get('employees') or []) if e in by_name]
    if not wanted:
        return jsonify({'error': 'Choose whose slips to send.'}), 400
    test_number = salary_mod.normalise_phone(b.get('test_number') or '')
    test = bool(b.get('test_number'))
    if test and not 11 <= len(test_number) <= 15:
        return jsonify({'error': 'Type your own 10-digit WhatsApp number for the test.'}), 400
    sends = payload['sends']
    jobs, skipped = [], []
    for emp in wanted:
        sl = by_name[emp]
        if test:
            jobs.append((emp, test_number, 'TEST - not sent to the employee\n\n' + sl['share']['text']))
            break                                  # a test sends one slip, to the owner's own number
        if not sl['share']['phone']:
            skipped.append({'employee': emp, 'reason': 'no WhatsApp number saved'})
        elif sends.get(emp, {}).get('sent') and not b.get('resend'):
            skipped.append({'employee': emp, 'reason': 'already sent this month'})
        else:
            jobs.append((emp, sl['share']['phone'], sl['share']['text']))
    if not jobs:
        return jsonify({'error': 'Nothing to send - ' + '; '.join(f"{s['employee']}: {s['reason']}" for s in skipped[:5])}), 400
    with _wa_lock:
        if _wa_state['running']:
            return jsonify({'error': 'Already sending - wait for it to finish.'}), 409
        _wa_state.update(running=True, stage='starting', log=[], results=[], month=month, test=test)
    logger.info('WhatsApp salary send started: %s %s people%s', month, len(jobs), ' (test)' if test else '')
    threading.Thread(target=_run_wa_send, args=(month, jobs, test), daemon=True).start()
    return jsonify({'started': True, 'count': len(jobs), 'skipped': skipped, 'test': test})


@app.route('/api/salary/whatsapp/status')
@requires_auth
def api_salary_whatsapp_status():
    with _wa_lock:
        st = {k: (list(v) if isinstance(v, list) else v) for k, v in _wa_state.items()}
    st['sends'] = salary_mod.load_sends().get(request.args.get('month') or st.get('month') or '', {})
    return jsonify(st)


@app.route('/api/salary/chart', methods=['POST'])
@requires_auth
def api_salary_chart():
    month, employee = request.form.get('month'), request.form.get('employee')
    if request.form.get('delete'):
        salary_mod.delete_chart(month, employee)
        return jsonify(_salary_payload(month))
    f = request.files.get('file')
    if not f or not f.filename.lower().endswith('.xlsx'):
        return jsonify({'error': 'Choose the Performance Chart .xlsx file.'}), 400
    data = f.read()
    if len(data) > 5 * 1024 * 1024:
        return jsonify({'error': 'That file is too big for a Performance Chart.'}), 400
    _, err = salary_mod.save_chart(data, f.filename, month, employee)
    if err:
        return jsonify({'error': err}), 400
    logger.info('Performance chart uploaded: %s %s', month, employee)
    return jsonify(_salary_payload(month))


@app.route('/api/ask', methods=['POST'])
@requires_auth
def api_ask():
    body = request.get_json(force=True, silent=True) or {}
    question = (body.get('question') or '').strip()[:4000]
    if not question:
        return jsonify({'error': 'question is required'}), 400
    try:
        ask_limits.check()   # daily + burst cap; this page is also the front door for order questions
    except ask_limits.LimitReached as e:
        return jsonify({'error': str(e)}), 429
    # Optional: earlier turns of the same conversation (voice follow-ups like
    # "and last month?"), and the voice flag (spoken-answer rules, no
    # patient-identity tools - see ask.py's VOICE_PROMPT).
    history = body.get('history') if isinstance(body.get('history'), list) else []
    history = [{'question': str(h.get('question', ''))[:3000], 'answer': str(h.get('answer', ''))[:3000]}
               for h in history[-ask_mod.MAX_HISTORY_TURNS:] if isinstance(h, dict)]
    voice = body.get('voice') is True
    speak = body.get('speak') is True  # push-to-talk: answer is read aloud as-is
    cost = {}  # filled with OpenRouter's reported charge for this answer (USD)
    try:
        answer = ask_mod.ask(question, history=history, voice=voice, speak=speak, cost_out=cost)
    except Exception as e:
        # Missing/bad API key, no network, model provider error, etc. - not a
        # bug in this server, so log it quietly and hand the reason to the
        # page rather than a generic 500.
        logger.warning('Ask failed for "%s": %s: %s', question, type(e).__name__, e)
        return jsonify({'error': f'Could not reach the model ({type(e).__name__}: {e}).'}), 502
    return jsonify({'answer': answer, 'cost': cost.get('usd')})


# The assistant's slow shared rollups (next-month forecast ~80s, profit,
# over/under) are cached in ask_tools once computed. Warming them in the
# background - at startup and when a voice question begins - keeps a spoken
# question from waiting on them. One warm-up at a time.
_warm_lock = threading.Lock()


def _warm_ask_cache():
    if not _warm_lock.acquire(blocking=False):
        return
    try:
        ask_mod.T.warm_cache()
    except Exception:
        logger.warning('Ask cache warm-up failed:\n%s', traceback.format_exc())
    finally:
        _warm_lock.release()


def warm_ask_cache_in_background():
    threading.Thread(target=_warm_ask_cache, daemon=True).start()


# --- Voice: GPT-Live-1 (trial, 2026-10-04) --------------------------------
# GPT-Live-1 only does the TALKING - listening, turn-taking, interruptions,
# speaking. It has no pharmacy data. Every business question it hears is
# handed back ("client delegation") to the Ask page, which sends it to the
# same /api/ask above (voice=True) and passes the answer back to be spoken.
# So voice and typed answers come from the same tools and numbers.
#
# The browser connects its microphone straight to OpenAI over WebRTC, but the
# session is created HERE with OPENAI_API_KEY from .env - the key never
# reaches the phone. The phone's data channel is limited to the three
# commands the page actually sends.
#
# Browsers only allow the microphone on https pages or on the PC itself
# (localhost) - over a plain http LAN/Tailscale address the Ask page explains
# that instead of failing silently.
# Switched OFF by the owner (2026-10-04) as too expensive - push-to-talk below
# replaces it. All the code stays: set VOICE_LIVE_ENABLED=1 in .env and
# restart to bring the 🎤 button back on the Ask page.
VOICE_LIVE_ENABLED = os.environ.get('VOICE_LIVE_ENABLED', '0').strip().lower() in ('1', 'true', 'yes', 'on')
VOICE_MODEL = 'gpt-live-1'
VOICE_NAME = os.environ.get('VOICE_NAME', 'marin')
VOICE_MAX_MINUTES = 10  # the page ends a session after this, to cap spend
VOICE_ALLOWED_CLIENT_EVENTS = ['session.commentary.append', 'session.thinking.append', 'session.close']
VOICE_INSTRUCTIONS = """\
# Personality
You are the voice of "Ask the Reckoner", the business analyst for Shubhada
Pharma in Karnataka, India: retail branches in Sirsi (Shivaji Chowk and
Hospet Road), a small Herur counter, and a B2B / wholesale channel. You talk
with the owner. Be warm, calm and brief, like a trusted manager. Speak at a
relaxed pace.

# Language
Reply in the language the user is speaking - English, Kannada or Hindi. If
unsure, use English. Say money in rupees, rounded, using Indian number words
(thousand, lakh, crore), for example "about 1 lakh 20 thousand rupees".

# Backchannel policy
While the user is talking, stay quiet except for a very short acknowledgement
if they pause mid-thought.

# Interruption policy
If the user interrupts, stop speaking at once and listen. If they change the
question, treat the new one as the current task.

# Delegation policy
You do NOT have the pharmacy's numbers. The backend analyst does: it reads
the real sales, purchase, profit, stock, supplier, staff and customer
reports.
- Delegate every question that needs a figure, a name, a list, a trend, a
  comparison or a recommendation about the business. Delegate before
  answering. Never guess or invent a number while waiting.
- Do not delegate greetings, thanks, or a request to repeat what you said.
- If a question is genuinely ambiguous, ask one short clarifying question;
  otherwise delegate and let the analyst decide.
- When you delegate, say one short line such as "Let me check the
  reckoner." Then wait. If it takes long, you may say "Still checking" once.
- When the result arrives, say it plainly in a few sentences, leading with
  the answer. Do not read long lists - give the top two or three and offer
  more.
- Patient-level lookups (a patient's history, who bought a product) are not
  available by voice; say they can use the typed Ask page.
- Never give dosage or treatment advice for a specific patient; say a
  pharmacist should assess that patient directly.
"""


@app.route('/api/voice/session', methods=['POST'])
@requires_auth
def api_voice_session():
    if not VOICE_LIVE_ENABLED:
        return jsonify({'error': 'Live voice is switched off (set VOICE_LIVE_ENABLED=1 in .env to use it).'}), 404
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return jsonify({'error': 'Voice is not set up on the PC yet (OPENAI_API_KEY is missing from .env).'}), 503
    body = request.get_json(force=True, silent=True) or {}
    sdp = body.get('sdp')
    if not isinstance(sdp, str) or not sdp.startswith('v=0') or len(sdp) > 50_000:
        return jsonify({'error': 'invalid voice connection offer'}), 400
    try:
        from openai import OpenAI
        result = OpenAI(api_key=api_key).live.create(
            session={
                'model': VOICE_MODEL,
                'instructions': VOICE_INSTRUCTIONS,
                'audio': {'output': {'voice': VOICE_NAME}},
                'delegation': {'type': 'client'},
                'client': {'data_channel': {'allowed_client_events': VOICE_ALLOWED_CLIENT_EVENTS,
                                            'allowed_server_events': 'all'}},
            },
            transport={'type': 'webrtc', 'sdp': sdp})
    except Exception as e:
        logger.warning('Voice session failed to start: %s: %s', type(e).__name__, e)
        return jsonify({'error': f'Could not start voice ({type(e).__name__}: {e}).'}), 502
    # Prepare the history and slow rollups now, while the user is still
    # speaking, so the first delegated question doesn't wait on them.
    warm_ask_cache_in_background()
    logger.info('Voice session started: %s', result.session.id)
    return jsonify({'session_id': result.session.id, 'sdp': result.transport.sdp,
                    'max_minutes': VOICE_MAX_MINUTES})


# --- Push-to-talk voice: speech-to-text + the same assistant + text-to-speech
# The cheaper, stitched alternative to GPT-Live (2026-10-04): billed only for
# the seconds actually spoken and read out, never for an open session. The
# Ask page records a WAV, sends it to /api/speech/transcribe, asks /api/ask
# with speak=True (answer in the owner's language, speakable words), then
# plays /api/speech/tts sentence by sentence. Both speech models run through
# the existing OpenRouter key. Defaults were picked by a round-trip test on
# 2026-10-04: MAI-Transcribe-2 was the fastest and best on mixed
# Kannada-English; Gemini 3.1 Flash TTS was the only voice whose Kannada came
# back word-perfect. Both are allowed by this account's OpenRouter
# zero-data-retention setting (Gemini 3.8 TTS is not). Override in .env.
SPEECH_STT_MODEL = os.environ.get('SPEECH_STT_MODEL', 'microsoft/mai-transcribe-2')
SPEECH_TTS_MODEL = os.environ.get('SPEECH_TTS_MODEL', 'google/gemini-3.1-flash-tts-preview')
SPEECH_TTS_VOICE = os.environ.get('SPEECH_TTS_VOICE', 'Kore')
SPEECH_MAX_AUDIO_BYTES = 4_000_000  # ~2 minutes of 16 kHz mono WAV
SPEECH_MAX_TTS_CHARS = 1500


def _openrouter_client():
    from openai import OpenAI
    key = os.environ.get('OPENROUTER_API_KEY')
    if not key:
        raise RuntimeError('OPENROUTER_API_KEY is missing from .env')
    return OpenAI(base_url='https://openrouter.ai/api/v1', api_key=key,
                  default_headers={'X-Title': 'Pharmacy Reckoner - Push to talk'})


def _pcm_to_wav(pcm, rate):
    import wave
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit little-endian, as OpenRouter's pcm format is
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


# Hands-free mode hears everything said near the phone, so each utterance is
# screened before it becomes a paid question (and before it counts against
# ask_limits): one tiny call to the fast model, ~$0.00004 and ~1s (tested
# 2026-10-10 on English/Kannada questions vs counter talk). It fails OPEN - if
# the check itself errors, the utterance is treated as a question, because
# ignoring the owner is worse than answering chatter once.
SPEECH_GATE_PROMPT = """\
You screen speech picked up by a pharmacy owner's hands-free voice assistant
(named suubbiii, also called subbi) in a busy shop. Decide whether this
utterance was said TO the assistant: a question or request about the pharmacy
business (sales, stock, profit, purchases, orders, suppliers, staff, customers,
forecasts), a follow-up to the assistant's last answer, or addressing it by
name. Background chatter, talking to a customer or colleague, and noise are
NOT for the assistant. Plain acknowledgements (okay, yes, haan, thank you) are
NOT for the assistant unless its last answer asked the owner a question. If
unsure, answer YES. Reply with exactly YES or NO."""


def _speech_gate(text, last_answer):
    model = os.environ.get('OPENROUTER_MODEL_FAST', 'google/gemini-3.1-flash-lite')
    user = (f"The assistant's last answer was: {last_answer}\n\n" if last_answer else '') + f'Utterance: {text}'
    resp = _openrouter_client().chat.completions.create(
        model=model, max_tokens=20, temperature=0, extra_body={'usage': {'include': True}},
        messages=[{'role': 'system', 'content': SPEECH_GATE_PROMPT}, {'role': 'user', 'content': user}])
    verdict = (resp.choices[0].message.content or '').strip().upper()
    return not verdict.startswith('NO'), ask_mod.usage_cost(resp.usage)


@app.route('/api/speech/transcribe', methods=['POST'])
@requires_auth
def api_speech_transcribe():
    if (request.content_length or 0) > SPEECH_MAX_AUDIO_BYTES:
        return jsonify({'error': 'That recording is too long - keep a question under a minute.'}), 413
    audio = request.get_data(cache=False)
    if not audio.startswith(b'RIFF'):
        return jsonify({'error': 'expected a WAV recording'}), 400
    # Prepare the history and slow rollups now, while speech-to-text runs, so
    # the question that follows doesn't wait on them.
    warm_ask_cache_in_background()
    try:
        result = _openrouter_client().audio.transcriptions.create(
            model=SPEECH_STT_MODEL, file=('question.wav', audio, 'audio/wav'))
    except Exception as e:
        logger.warning('Speech-to-text failed: %s: %s', type(e).__name__, e)
        return jsonify({'error': f'Could not transcribe ({type(e).__name__}).'}), 502
    text = (getattr(result, 'text', '') or '').strip()
    cost = ask_mod.usage_cost(getattr(result, 'usage', None))
    addressed = True
    if request.args.get('gate') == '1' and text:
        # The page sends the assistant's last answer (URL-encoded, as headers
        # can't carry Kannada) so a follow-up like "and last month?" passes.
        last_answer = urllib.parse.unquote(request.headers.get('X-Voice-Context', ''))[:600]
        try:
            addressed, gate_cost = _speech_gate(text, last_answer)
            cost += gate_cost
        except Exception as e:
            logger.warning('Speech check failed (treating as a question): %s: %s', type(e).__name__, e)
    return jsonify({'text': text, 'addressed': addressed, 'cost': cost})


@app.route('/api/speech/tts', methods=['POST'])
@requires_auth
def api_speech_tts():
    body = request.get_json(force=True, silent=True) or {}
    text = str(body.get('text') or '').strip()[:SPEECH_MAX_TTS_CHARS]
    if not text:
        return jsonify({'error': 'text is required'}), 400
    # Gemini TTS only returns raw PCM; other providers can return MP3.
    fmt = 'pcm' if 'gemini' in SPEECH_TTS_MODEL else 'mp3'
    try:
        result = _openrouter_client().audio.speech.create(
            model=SPEECH_TTS_MODEL, voice=SPEECH_TTS_VOICE, input=text, response_format=fmt)
    except Exception as e:
        logger.warning('Text-to-speech failed: %s: %s', type(e).__name__, e)
        return jsonify({'error': f'Could not create speech ({type(e).__name__}).'}), 502
    audio = result.content
    # The speech endpoint doesn't return its cost; OpenRouter's generation id
    # lets the page look it up a few seconds later (/api/speech/costs).
    headers = {'X-Generation-Id': result.response.headers.get('x-generation-id', '')}
    if fmt == 'mp3':
        return Response(audio, mimetype='audio/mpeg', headers=headers)
    # e.g. 'audio/pcm;rate=24000;channels=1'
    ctype = result.response.headers.get('content-type', '')
    rate = next((int(p.split('=', 1)[1]) for p in ctype.split(';') if p.strip().startswith('rate=')), 24000)
    return Response(_pcm_to_wav(audio, rate), mimetype='audio/wav', headers=headers)


@app.route('/api/speech/costs', methods=['POST'])
@requires_auth
def api_speech_costs():
    """Actual charges (USD) for earlier text-to-speech calls, by OpenRouter
    generation id. OpenRouter only has them a few seconds after the call, so
    an id it doesn't know yet comes back as null and the page asks again."""
    body = request.get_json(force=True, silent=True) or {}
    ids = [str(i) for i in (body.get('ids') or []) if isinstance(i, str) and i.startswith('gen-')][:20]
    key = os.environ.get('OPENROUTER_API_KEY', '')
    costs = {}
    for gen_id in ids:
        try:
            req = urllib.request.Request(
                'https://openrouter.ai/api/v1/generation?' + urllib.parse.urlencode({'id': gen_id}),
                headers={'Authorization': f'Bearer {key}'})
            with urllib.request.urlopen(req, timeout=10) as r:
                costs[gen_id] = float(json.loads(r.read())['data'].get('total_cost') or 0)
        except Exception:
            costs[gen_id] = None
    return jsonify({'costs': costs})


@app.route('/')
def index():
    # Public shell - it's just markup/JS, no pharmacy data. The page itself
    # shows a login form and every /api/* call is what's actually gated.
    return Response((ROOT / 'mobile_dashboard.html').read_text(encoding='utf-8'), mimetype='text/html')


@app.route('/ask')
def ask_page():
    # Public shell, same pattern as index() above - the page checks for a
    # session token client-side and calls /api/ask (gated) to do anything.
    html = (ROOT / 'ask_dashboard.html').read_text(encoding='utf-8')
    if VOICE_LIVE_ENABLED:  # the page hides the 🎤 live-voice button unless told otherwise
        html = html.replace('<body>', '<body data-live-voice="1">', 1)
    return Response(html, mimetype='text/html')


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
    for c in CONFIG['credentials']:
        print(f'Login: {c["username"]} / {c["password"]}')
    print(f'(saved in server_config.json - change it any time)')
    print(f'Auto-logout after {SESSION_TIMEOUT_SECONDS // 60} minutes of inactivity (or after closing the tab).')
    print()
    print(f'On your phone (same WiFi), open: http://{lan_ip}:8420')
    print('Press Ctrl+C to stop the server.')
    print('=' * 60)
    print()
    print('Warming up (computing reports once)...')
    get_cached_data()
    warm_ask_cache_in_background()  # Ask / voice answers: forecast etc., in the background
    print('Ready.')
    print()
    logger.info('Server starting on http://%s:8420', lan_ip)
    from waitress import serve
    serve(app, host='0.0.0.0', port=8420)

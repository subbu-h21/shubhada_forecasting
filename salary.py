"""
Salary calculator - the rules the owner agreed in the two Salary Review rounds
(Sept-Oct 2026). Every rule below was chosen by the owner, item by item:

  Salary = Fixed 35% + Performance 45% + Discipline 20% of the person's base.

  Performance (0-120%) = role-weighted average of groups (Sales, Fetching,
  Back-office, Quality, Customer, Team, Owner's rating). Each measured item is
  scored against that person's OWN best month up to the slip month, and 90% of
  that best already counts as full marks (100). Under 60% -> no performance
  pay. A sale is credited 80% to the biller and 20% to the fetcher.

  Discipline starts full and loses 5% of the discipline part per incident
  after 3 free ones a month (a %, so a Rs 4,000 and a Rs 20,000 person are
  treated alike) - a late arrival or a long break counts 1, an early exit 0.5
  (exit is read from the last bill/fetch, so it's weaker evidence). Unplanned leave
  costs a fixed amount that goes to admin savings, and it only ever comes
  out of the bonus parts, never the fixed part.

  Everyone can have agreed timings in Setup (start, break out, break back,
  end; owner, 2026-10-10) and is then judged against them, not against their
  own earliest days - which called ordinary days late (Parashuram: usual
  10:30 vs a 08:48 target). Late = first stamp 15+ min after the start, early
  = last stamp 15+ min before the end, long break = the break gap 15+ min
  longer than agreed. Half-day / contract staff have no break counted, and
  nothing is counted for them until their shift is entered; their base can be
  anything the owner enters. Full-day staff without timings keep the
  Scorecard's own-routine timing until theirs are entered.

  First 3 months are a trial: slips are calculated and shown, but nobody is
  paid less than their base.

Owner-entered data (base salaries, roles, the incident log, the monthly 1-5
ratings and uploaded shubhadahealth Performance Charts) lives in
data/processed/salary/, so the reckoner's own backups pick it up.
"""
import re
import threading
import time
import uuid

import numpy as np
import pandas as pd

import run_reckoner as rk

SALARY_DIR = rk.PROC_DIR / 'salary'
SETTINGS_PATH = SALARY_DIR / 'settings.json'
LOG_PATH = SALARY_DIR / 'incidents.json'
RATINGS_PATH = SALARY_DIR / 'ratings.json'
CHARTS_PATH = SALARY_DIR / 'charts.json'
CHART_FILES_DIR = SALARY_DIR / 'charts'

MONTH_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# Every number the owner may want to tune later. Shown and editable on the
# dashboard's Salary > Rules screen; these are only the starting values.
DEFAULT_RULES = {
    'fixed_pct': 35, 'perf_pct': 45, 'disc_pct': 20,
    'full_at_pct': 90,          # this % of own best already earns full marks
    'item_cap_pct': 120, 'perf_cap_pct': 120,
    'floor_pct': 60,            # performance under this -> no performance pay
    'biller_share_pct': 80,     # rest of each sale goes to the fetcher
    'free_incidents': 3, 'pct_per_incident': 5, 'early_exit_weight': 0.5,
    'unplanned_leave_rs': 200,  # per day, to admin savings
    'marks_per_mistake': 10, 'marks_per_complaint': 10,
    'marks_per_compliment': 5, 'marks_per_cover': 5,
    'returns_gate_pct': 3,      # chart Sales-Return / Sales above this -> no performance pay
    'trial_until': '2026-12',
    'min_days_active': 10,      # fewer active days -> month too short to score volumes
    'min_role_lines': 100,      # a role counts only if it was really their work that month
}
RULE_LIMITS = {k: (0, 100) for k in ('fixed_pct', 'perf_pct', 'disc_pct', 'biller_share_pct')}
RULE_LIMITS.update({'full_at_pct': (50, 100), 'item_cap_pct': (100, 200), 'perf_cap_pct': (100, 200),
                    'floor_pct': (0, 100), 'free_incidents': (0, 31), 'pct_per_incident': (0, 100),
                    'early_exit_weight': (0, 1), 'unplanned_leave_rs': (0, 20000),
                    'marks_per_mistake': (0, 100), 'marks_per_complaint': (0, 100),
                    'marks_per_compliment': (0, 100), 'marks_per_cover': (0, 100),
                    'returns_gate_pct': (0, 100), 'min_days_active': (1, 31), 'min_role_lines': (0, 10000)})

ROLES = ['Biller', 'Fetcher', 'Back-office', 'Both']
# Owner-approved (Round 2, scaled so each role adds up to 100).
GROUP_WEIGHTS = {
    'Biller': dict(S=33, F=7, B=0, Q=20, C=13, T=13, O=14),
    'Fetcher': dict(S=38, F=31, B=0, Q=12, C=4, T=8, O=7),
    'Back-office': dict(S=21, F=0, B=46, Q=13, C=0, T=8, O=12),
    'Both': dict(S=19, F=19, B=33, Q=11, C=4, T=7, O=7),
}
GROUPS = [('S', 'Sales', 'ಮಾರಾಟ'), ('F', 'Fetching', 'ಔಷಧಿ ತರುವುದು'), ('B', 'Back-office', 'ಹಿಂದಿನ ಕೆಲಸ'),
          ('Q', 'Accuracy', 'ನಿಖರತೆ'), ('C', 'Customer', 'ಗ್ರಾಹಕ'), ('T', 'Team', 'ತಂಡ'),
          ('O', "Owner's rating", 'ಮಾಲೀಕರ ಅಭಿಪ್ರಾಯ')]

# The owner logs these on the day they happen.
INCIDENT_TYPES = {
    'wrong_item': {'label': 'Wrong item or quantity given', 'kn': 'ತಪ್ಪು ಐಟಂ / ಪ್ರಮಾಣ', 'code': 'Q2'},
    'cash_short': {'label': 'Cash short at the till', 'kn': 'ಗಲ್ಲಾ ಹಣ ಕೊರತೆ', 'code': 'Q3'},
    'near_expiry': {'label': 'Near-expiry stock left on rack', 'kn': 'ಅವಧಿ ಹತ್ತಿರದ ಸ್ಟಾಕ್', 'code': 'Q4'},
    'complaint': {'label': 'Customer complaint', 'kn': 'ಗ್ರಾಹಕರ ದೂರು', 'code': 'C1'},
    'compliment': {'label': 'Compliment / asked for by name', 'kn': 'ಮೆಚ್ಚುಗೆ', 'code': 'C2'},
    'covered': {'label': 'Covered for an absent colleague', 'kn': 'ಗೈರು ಸಹೋದ್ಯೋಗಿಯ ಕೆಲಸ', 'code': 'T3'},
    'unplanned_leave': {'label': 'Unplanned leave (1 day)', 'kn': 'ತಿಳಿಸದೆ ರಜೆ', 'code': 'D6'},
}
RATING_KEYS = {'O1': 'Behaviour with customers', 'O2': 'Cleanliness, uniform, rack order',
               'O3': 'Responsibility and initiative', 'O4': 'Learning'}

# Roles as confirmed in Salary Review Round 2 (2026-10). Names are the
# reckoner's canonical spellings, after EMPLOYEE_ALIASES.
SEED_ROLES = {
    'Keerthana M': 'Biller', 'Deepa Manjunatha Gouda': 'Both', 'Manjunata D Gosavi': 'Fetcher',
    'Archana.Gopal.Marathi': 'Biller', 'Renuka.D.H': 'Biller', 'Pooja Naik': 'Both', 'Akshata Naik': 'Biller',
    'Fazil Unchalli': 'Both', 'Parashuram T Naik': 'Both', 'Rakshita K': 'Biller', 'Harsha Naik': 'Both',
    'Nagashree Hegde': 'Biller', 'Harsha N': 'Both', 'Mohan Gowda': 'Both', 'Narendra Devadiga': 'Fetcher',
    'Junied Soudagar': 'Fetcher', 'Tanuja R Naik': 'Biller', 'Laxmi R Palankar': 'Both',
    'Abhishek Seetaram Naik': 'Fetcher', 'Raghavendra S Palankar': 'Back-office',
    'Netravati Prakash Kothari': 'Back-office', 'Sharath Nagendra Naik': 'Fetcher', 'Kiran Naik': 'Both',
    'Deeksha Dinesh Naik': 'Biller', 'Manish Shekhar Poojari': 'Both', 'Asha Vinayak Bhovi': 'Both',
    'Manish Naik': 'Fetcher', 'Ganesh Hegde': 'Both', 'Raghavendra C Shet': 'Both', 'Sandhya S Naik': 'Both',
    'Dattatraya V Hegde': 'Back-office', 'Lohit Gowda': 'Fetcher', 'Harshita Suresh Naik': 'Both',
    'Nivedita M K': 'Both',
}
# Confirmed half-day / contract workers (owner, 2026-10-10). Shift times are
# entered on Setup; until they are, no late/early is counted for them (judging
# against their own usual time would call half their days late by definition).
SEED_HALF_DAY = ['Deeksha Dinesh Naik', 'Fazil Unchalli', 'Renuka.D.H', 'Tanuja R Naik', 'Kiran Naik',
                 'Manish Shekhar Poojari', 'Lohit Gowda']
OTHER_SHIFT_MIN = 120  # arriving this long before the agreed start = a different shift that day, not judged
SHIFT_KEYS = ('start', 'break_out', 'break_in', 'end')
TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
NOT_STAFF = {'Nelemav Seva Sahakari Sangh'}  # Herur till login, not a person (owner, Round 2)

# shubhadahealth Performance Chart rows -> item code. "Sales" itself is left
# out (the reckoner measures sales from the bills); "Purchase Created" is used
# only when the reckoner has no purchase-entry data for that person.
CHART_ITEMS = [
    ('Sales-Packed', 'S', 'S7', 'Sales-Packed'),
    ('Purchase Created', 'B', 'B1', 'Purchase entry (chart)'),
    ('DC Created', 'B', 'B2', 'DC created'), ('DC Checked', 'B', 'B2', 'DC checked'),
    ('Re-Order Items', 'B', 'B3', 'Re-order items'), ('Re-Order Placed', 'B', 'B3', 'Re-order placed'),
    ('Re-Order Collected', 'B', 'B3', 'Re-order collected'),
    ('Purchase Return Created', 'B', 'B4', 'Purchase return created'),
    ('Purchase Return Placed', 'B', 'B4', 'Purchase return placed'),
    ('Purchase Return-Handover', 'B', 'B4', 'Purchase return handover'),
    ('Stock Transfer Created', 'B', 'B5', 'Stock transfer created'),
    ('Stock Transfer Handover', 'B', 'B5', 'Stock transfer handover'),
    ('Stock Tranfer Received', 'B', 'B5', 'Stock transfer received'),
    ('Dump List Added', 'B', 'B6', 'Dump list added'), ('Dump List Removed', 'B', 'B6', 'Dump list removed'),
    ('Item Shelfed', 'B', 'B7', 'Items shelved'), ('New Product Created', 'B', 'B8', 'New products created'),
    ('Non Cash Receipt', 'B', 'B9', 'Non-cash receipts'),
]

_lock = threading.Lock()


# ---------------------------------------------------------------- storage --
def _load(path, default):
    return rk._load_json_resilient(path, default)


def _save(path, obj):
    import json
    SALARY_DIR.mkdir(parents=True, exist_ok=True)
    rk._atomic_write_text(path, json.dumps(obj, indent=1, ensure_ascii=True))  # ASCII-safe on any Windows codepage


def load_settings():
    s = _load(SETTINGS_PATH, {})
    rules = dict(DEFAULT_RULES)
    rules.update({k: v for k, v in s.get('rules', {}).items() if k in DEFAULT_RULES})
    roles = dict(SEED_ROLES)
    roles.update(s.get('roles', {}))
    stored_half = s.get('half_day', {})
    half = sorted({n for n in SEED_HALF_DAY if n not in stored_half} | {n for n, v in stored_half.items() if v is not None})
    shifts = {n: dict(v) for n, v in s.get('shifts', {}).items() if v}
    for n, v in stored_half.items():   # times saved before shifts had their own place
        if v and (v.get('start') or v.get('end')) and n not in shifts:
            shifts[n] = {'start': v.get('start', ''), 'end': v.get('end', '')}
    return {'rules': rules, 'roles': roles, 'salaries': s.get('salaries', {}), 'half_day': half, 'shifts': shifts}


def update_settings(rules=None, roles=None, salaries=None, half_day=None, shifts=None):
    """Validates and merges; returns (settings, error)."""
    with _lock:
        s = _load(SETTINGS_PATH, {})
        if rules:
            new = dict(s.get('rules', {}))
            for k, v in rules.items():
                if k not in DEFAULT_RULES:
                    return None, f'Unknown rule "{k}".'
                if k == 'trial_until':
                    if v not in ('', None) and not MONTH_RE.match(str(v)):
                        return None, 'Trial end must be a month like 2026-12.'
                    new[k] = v or ''
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    return None, f'"{k}" must be a number.'
                lo, hi = RULE_LIMITS[k]
                if not lo <= v <= hi:
                    return None, f'"{k}" must be between {lo} and {hi}.'
                new[k] = v
            merged = dict(DEFAULT_RULES, **new)
            if round(merged['fixed_pct'] + merged['perf_pct'] + merged['disc_pct'], 6) != 100:
                return None, 'Fixed + Performance + Discipline must add up to 100.'
            s['rules'] = new
        if roles:
            r = dict(s.get('roles', {}))
            for name, role in roles.items():
                if role not in ROLES + ['Not staff']:
                    return None, f'Unknown role "{role}".'
                r[str(name)[:120]] = role
            s['roles'] = r
        if salaries:
            sal = dict(s.get('salaries', {}))
            for name, v in salaries.items():
                if v in ('', None):
                    sal.pop(str(name), None)
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    return None, f'Salary for {name} must be a number.'
                if not 0 < v <= 1_000_000:
                    return None, f'Salary for {name} looks wrong ({v:,.0f}).'
                sal[str(name)[:120]] = round(v, 2)
            s['salaries'] = sal
        if half_day:
            # stored explicitly (None = not half-day) so un-ticking a seeded person sticks
            hd = dict(s.get('half_day', {}))
            for name, on in half_day.items():
                hd[str(name)[:120]] = {} if on else None
            s['half_day'] = hd
        if shifts:
            sh = dict(s.get('shifts', {}))
            for name, v in shifts.items():
                name = str(name)[:120]
                v = {k: str((v or {}).get(k) or '') for k in SHIFT_KEYS}
                for t in v.values():
                    if t and not TIME_RE.match(t):
                        return None, f'Times for {name} must look like 17:00.'
                if v['start'] and v['end'] and v['start'] >= v['end']:
                    return None, f'End time for {name} must be after the start.'
                if bool(v['break_out']) != bool(v['break_in']):
                    return None, f'Enter both break times for {name}, or neither.'
                if v['break_out'] and v['break_out'] >= v['break_in']:
                    return None, f'Break back for {name} must be after break out.'
                if v['break_out'] and v['start'] and v['end'] and not v['start'] < v['break_out'] < v['break_in'] < v['end']:
                    return None, f'The break for {name} must fall between their start and end.'
                sh[name] = v if any(v.values()) else None
            s['shifts'] = {n: v for n, v in sh.items() if v}
        _save(SETTINGS_PATH, s)
    return load_settings(), None


def load_incidents():
    return _load(LOG_PATH, [])


def add_incident(date, employee, kind, note=''):
    if not DATE_RE.match(str(date or '')):
        return None, 'Pick the date it happened.'
    if kind not in INCIDENT_TYPES:
        return None, 'Pick what happened.'
    if not employee:
        return None, 'Pick the person.'
    rec = {'id': uuid.uuid4().hex[:12], 'date': date, 'month': date[:7], 'employee': str(employee)[:120],
           'type': kind, 'note': str(note or '')[:300], 'logged_at': time.strftime('%Y-%m-%d %H:%M')}
    with _lock:
        log = load_incidents()
        log.append(rec)
        _save(LOG_PATH, log)
    return rec, None


def delete_incident(rec_id):
    with _lock:
        log = load_incidents()
        kept = [r for r in log if r.get('id') != rec_id]
        if len(kept) == len(log):
            return False
        _save(LOG_PATH, kept)
    return True


def load_ratings():
    return _load(RATINGS_PATH, {})


def save_rating(month, employee, ratings):
    if not MONTH_RE.match(str(month or '')):
        return 'Bad month.'
    clean = {}
    for k, v in (ratings or {}).items():
        if k not in RATING_KEYS:
            return f'Unknown rating "{k}".'
        if v in ('', None):
            continue
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 'Ratings are 1 to 5.'
        if not 1 <= v <= 5:
            return 'Ratings are 1 to 5.'
        clean[k] = v
    with _lock:
        r = load_ratings()
        r.setdefault(month, {})
        if clean:
            r[month][str(employee)[:120]] = clean
        else:
            r[month].pop(str(employee), None)
        _save(RATINGS_PATH, r)
    return None


def load_charts():
    return _load(CHARTS_PATH, {})


def parse_chart(path):
    """{category: achieved qty} from a shubhadahealth Performance Chart xlsx."""
    try:
        df = pd.read_excel(path)
    except Exception:
        raise ValueError('Could not open that file as an Excel sheet. Download the chart again from shubhadahealth.com and upload that file.')
    cols = {c.lower().strip(): c for c in df.columns.astype(str)}
    cat = next((cols[c] for c in cols if 'categ' in c), None)
    ach = next((cols[c] for c in cols if c.startswith('achi') and 'qty' in c), None)
    if not cat or not ach:
        raise ValueError('This does not look like a Performance Chart - it needs "Category Name" and "Achived Qty" columns.')
    out = {}
    for _, r in df.iterrows():
        name = str(r[cat]).strip()
        val = pd.to_numeric(r[ach], errors='coerce')
        if name and name.lower() != 'nan':
            out[name] = None if pd.isna(val) else float(val)
    if not out:
        raise ValueError('The chart has no rows.')
    return out


def save_chart(file_bytes, filename, month, employee):
    if not MONTH_RE.match(str(month or '')):
        return None, 'Pick the month this chart covers.'
    if not employee:
        return None, 'Pick whose chart this is.'
    CHART_FILES_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r'[^A-Za-z0-9]+', '-', employee).strip('-') or 'employee'
    dest = CHART_FILES_DIR / f'{month}_{slug}.xlsx'
    tmp = dest.with_name(dest.name + '.upload')
    tmp.write_bytes(file_bytes)
    try:
        rows = parse_chart(tmp)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return None, str(e) if isinstance(e, ValueError) else 'Could not read that file as a Performance Chart.'
    tmp.replace(dest)
    with _lock:
        c = load_charts()
        c.setdefault(month, {})[employee] = {'rows': rows, 'file': dest.name, 'original_name': str(filename)[:200],
                                             'uploaded_at': time.strftime('%Y-%m-%d %H:%M')}
        _save(CHARTS_PATH, c)
    return rows, None


def delete_chart(month, employee):
    with _lock:
        c = load_charts()
        rec = c.get(month, {}).pop(employee, None)
        if rec is None:
            return False
        _save(CHARTS_PATH, c)
    if rec.get('file'):
        (CHART_FILES_DIR / rec['file']).unlink(missing_ok=True)
    return True


# ------------------------------------------------------------ heavy inputs --
def build_inputs(sales, employee_monthly, employee_scorecard, profit):
    """Everything slow, computed once per data refresh: per-person sales credit
    (80/20 by default - re-split cheaply at slip time), margin of what they
    sold, branch sales per day. Returns None without employee data."""
    if employee_monthly is None or employee_monthly.empty:
        return None
    billed_col = rk.find_col(sales, rk.BILLED_BY_CANDIDATES)
    given_col = rk.find_col(sales, rk.GIVEN_BY_CANDIDATES)
    s = sales[['Calendar_Month', 'Inv.No', 'Patient', 'Product', 'Item Total', 'Tax Rate', 'Disc Amount', 'Date']].copy()
    s['B'] = rk._norm_name(sales[billed_col]) if billed_col else None
    s['G'] = rk._norm_name(sales[given_col]) if given_col else None
    s['Branch'] = sales['Inv.No'].map(rk.extract_branch)
    s['Pretax'] = s['Item Total'] / (1 + s['Tax Rate'].fillna(0) / 100)
    margin = profit.set_index('Product')['Margin_Pct'] if profit is not None and not profit.empty else pd.Series(dtype=float)
    s['GP'] = s['Pretax'] * s['Product'].map(margin) / 100   # NaN where cost unknown
    s['Disc'] = s['Disc Amount'].fillna(0)

    # One row per (person, month, side): side B = they billed it, G = they fetched it,
    # BG = same person did both (always 100% theirs).
    parts = []
    for side, mask, col in (('B', s['B'].notna() & (s['B'] != s['G']), 'B'),
                            ('G', s['G'].notna() & (s['G'] != s['B']), 'G'),
                            ('BG', s['B'].notna() & (s['B'] == s['G']), 'B')):
        t = s[mask]
        if t.empty:
            continue
        known = t['GP'].notna()
        g = t.assign(Employee=t[col], PretaxKnown=t['Pretax'].where(known)).groupby(['Employee', 'Calendar_Month']).agg(
            Rev=('Item Total', 'sum'), Disc=('Disc', 'sum'), GP=('GP', 'sum'), PretaxKnown=('PretaxKnown', 'sum'),
            Bills=('Inv.No', 'nunique'), Lines=('Product', 'size')).reset_index()
        g['Side'] = side
        parts.append(g)
    credit = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    # Customers per person-month: unique patients on any bill they took part in (S3 = both roles).
    cust = pd.concat([s[['B', 'Calendar_Month', 'Patient']].rename(columns={'B': 'Employee'}),
                      s[['G', 'Calendar_Month', 'Patient']].rename(columns={'G': 'Employee'})])
    cust = cust.dropna(subset=['Employee']).groupby(['Employee', 'Calendar_Month'])['Patient'].nunique().rename('Customers')

    people_lines = pd.concat([s[['B', 'Branch']].rename(columns={'B': 'Employee'}),
                              s[['G', 'Branch']].rename(columns={'G': 'Employee'})]).dropna()
    home = people_lines.groupby('Employee')['Branch'].agg(lambda x: x.value_counts().idxmax())
    day = pd.to_datetime(s['Date'], format='mixed').dt.normalize()
    br = (s.groupby(['Branch', 'Calendar_Month'])['Item Total'].sum()
          / day.groupby([s['Branch'], s['Calendar_Month']]).nunique()).rename('Per_Day').reset_index()

    # Same rule as the Scorecard: a month is payable only if its sale export
    # carries the employee columns (Jun/Jul 2026 were exported without them).
    cov = (sales[billed_col].notna().groupby(sales['Calendar_Month']).mean() if billed_col
           else pd.Series(dtype=float))
    scored_months = sorted(cov[cov >= rk.SCORE_MIN_COVERAGE].index.tolist())

    # Daily first/last stamp per person - half-day staff are judged per day
    # against their agreed shift (the monthly counts assume a full day).
    act = rk._employee_activity(sales)
    daily, _ = rk._employee_shift_timing(act) if act is not None else (None, None)
    if daily is not None:
        daily = daily[['Employee', 'Day', 'Calendar_Month', 'Arrive_Min', 'Exit_Min', 'Lunch_Out_Min', 'Lunch_In_Min']].copy()

    sc = employee_scorecard.set_index(['Employee', 'Calendar_Month']) if employee_scorecard is not None and not employee_scorecard.empty else None
    return {'monthly': employee_monthly.copy(), 'credit': credit, 'customers': cust, 'home_branch': home,
            'branch_daily': br, 'scorecard': sc, 'scored_months': scored_months, 'daily': daily}


def months_available(inputs):
    if not inputs:
        return []
    m = inputs['monthly']
    active = set(m.loc[m['Days_Active'] > 0, 'Calendar_Month'].dropna())
    return [mo for mo in inputs['scored_months'] if mo in active]


# ------------------------------------------------------------------ slips --
def _vs_best(cur, hist, rules, lower_better=False):
    """Score 0..item cap against own best so far; None when there's nothing to
    compare with."""
    if cur is None or pd.isna(cur):
        return None
    hist = pd.Series(hist, dtype=float).dropna()
    full = rules['full_at_pct'] / 100
    cap = rules['item_cap_pct']
    if lower_better:
        if cur <= 0:
            return cap
        pos = hist[hist > 0]
        if pos.empty:
            return None
        return float(min(pos.min() / cur / full * 100, cap))
    if hist.empty or hist.max() <= 0:
        return None
    return float(min(cur / hist.max() / full * 100, cap))


def _person_metrics(inputs, rules):
    """Per (Employee, Month): every measured value the slip scores, credited by
    the current biller share."""
    m = inputs['monthly'].set_index(['Employee', 'Calendar_Month'])
    share = rules['biller_share_pct'] / 100
    c = inputs['credit']
    cols = ['Rev', 'Disc', 'GP', 'PretaxKnown', 'Bills']
    if not c.empty:
        w = c['Side'].map({'B': share, 'G': 1 - share, 'BG': 1.0})
        cc = c.assign(**{k: c[k] * w for k in cols})
        cr = cc.groupby(['Employee', 'Calendar_Month'])[cols].sum().add_prefix('C_')
        sold_lines = c.groupby(['Employee', 'Calendar_Month'])['Lines'].sum().rename('C_Lines')
        m = m.join(cr, how='left').join(sold_lines, how='left')
    for k in cols + ['Lines']:
        if 'C_' + k not in m:
            m['C_' + k] = np.nan
    m = m.join(inputs['customers'], how='left')
    days = m['Days_Active'].where(m['Days_Active'] >= rules['min_days_active'])
    out = pd.DataFrame(index=m.index)
    out['Days_Active'] = m['Days_Active']
    gross = m['C_Rev'] + m['C_Disc']
    out['S1'] = m['C_Rev'] / days
    out['S2'] = m['C_Bills'] / days
    out['S3'] = m['Customers'] / days
    out['S4'] = m['C_Rev'] / m['C_Bills'].where(m['C_Bills'] > 0)
    out['S5'] = m['C_GP'] / m['C_PretaxKnown'].where(m['C_PretaxKnown'] > 0) * 100
    out['S6'] = m['C_Disc'] / gross.where(gross > 0) * 100
    out['Sales_Lines'] = m['C_Lines']
    out['F1'] = m['Lines'] / days
    hours = (m['Exit_Min'] - m['Arrive_Min'] - m['Lunch_Len_Min'].fillna(0)) / 60
    out['F2'] = out['F1'] / hours.where(hours >= 2)
    out['F3'] = m['Distinct_Products']
    out['F4'] = m['Rare_Item_Pct']
    out['F5'] = m['Lines_Per_Bill']
    out['Fetch_Lines'] = m['Lines']
    out['B1'] = m['Lines_Entered'] / days
    out['Entry_Lines'] = m['Lines_Entered']
    out['Invoices'] = m['Invoices_Entered']
    out['PTR_Errors'] = m['PTR_Errors']
    for k in ('Days_Late', 'Days_Long_Lunch', 'Days_Left_Early', 'Days_Timed'):
        out[k] = m[k]
    return out.reset_index()


ITEM_DEFS = [  # code, group, label, lower is better, how to show the raw value
    ('S1', 'S', 'Sales per day (your credit)', False, lambda v: f'₹{v:,.0f}/day'),
    ('S2', 'S', 'Bills per day (your credit)', False, lambda v: f'{v:,.1f}/day'),
    ('S3', 'S', 'Customers served per day', False, lambda v: f'{v:,.1f}/day'),
    ('S4', 'S', 'Average bill value', False, lambda v: f'₹{v:,.0f}'),
    ('S5', 'S', 'Margin on what they sold', False, lambda v: f'{v:.1f}%'),
    ('S6', 'S', 'Discount per ₹100 sold (lower is better)', True, lambda v: f'₹{v:.2f}'),
    ('F1', 'F', 'Lines fetched per day', False, lambda v: f'{v:,.0f}/day'),
    ('F2', 'F', 'Lines fetched per hour present', False, lambda v: f'{v:,.1f}/hr'),
    ('F3', 'F', 'Different products fetched', False, lambda v: f'{v:,.0f}'),
    ('F4', 'F', 'Rare-item share', False, lambda v: f'{v:.1f}%'),
    ('F5', 'F', 'Lines per bill', False, lambda v: f'{v:.2f}'),
    ('B1', 'B', 'Purchase lines entered per day', False, lambda v: f'{v:,.0f}/day'),
]


def _hhmm(minutes):
    return f'{int(minutes) // 60:02d}:{int(minutes) % 60:02d}'


def _to_min(t):
    return int(t[:2]) * 60 + int(t[3:])


def _usual_times(inputs, emp, month):
    """Median start / break / end over this month and the two before, from
    the bill and fetch stamps - shown on Setup as a hint. None without data."""
    d = inputs.get('daily')
    if d is None:
        return None
    mine = d[d['Employee'] == emp]
    p = pd.Period(month, 'M')
    recent = mine[mine['Calendar_Month'].map(lambda m: p - 2 <= pd.Period(m, 'M') <= p)]
    if recent.empty:
        return None
    med = lambda c: _hhmm(recent[c].median()) if recent[c].notna().any() else ''
    return {'start': med('Arrive_Min'), 'break_out': med('Lunch_Out_Min'), 'break_in': med('Lunch_In_Min'),
            'end': med('Exit_Min')}


def _shift_discipline(inputs, emp, month, shift, half):
    """Late / early / long-break day counts against the agreed timings. A day
    they arrived OTHER_SHIFT_MIN+ before the start was another shift and
    isn't judged. None when the person has no timed days this month."""
    d = inputs.get('daily')
    if d is None:
        return None
    now = d[(d['Employee'] == emp) & (d['Calendar_Month'] == month)]
    if now.empty:
        return None
    tol = rk.SHIFT_TOLERANCE_MIN
    start, end = _to_min(shift['start']), _to_min(shift['end'])
    judged = now[now['Arrive_Min'] > start - OTHER_SHIFT_MIN]
    late = int((judged['Arrive_Min'] > start + tol).sum())
    early = int((judged['Exit_Min'] < end - tol).sum())
    long_break = 0
    if not half and shift.get('break_out') and shift.get('break_in'):
        agreed = _to_min(shift['break_in']) - _to_min(shift['break_out'])
        gap = judged['Lunch_In_Min'] - judged['Lunch_Out_Min']
        long_break = int((gap > agreed + tol).sum())   # days with no visible break aren't counted
    return {'late': late, 'early': early, 'long_break': long_break, 'judged': len(judged),
            'other_shift_days': len(now) - len(judged)}


def compute_slips(inputs, month):
    settings = load_settings()
    rules, roles, salaries = settings['rules'], settings['roles'], settings['salaries']
    half_day, shifts = set(settings['half_day']), settings['shifts']
    if not inputs or not MONTH_RE.match(str(month or '')):
        return {'slips': [], 'needs_role': []}
    pm = _person_metrics(inputs, rules)
    hist_all = pm[pm['Calendar_Month'] <= month]
    cur_all = pm[pm['Calendar_Month'] == month].set_index('Employee')
    log = [r for r in load_incidents() if r.get('month') == month]
    ratings = load_ratings().get(month, {})
    charts = load_charts()
    br = inputs['branch_daily']
    in_trial = bool(rules['trial_until']) and month <= rules['trial_until']

    slips, needs_role = [], []
    for emp, cur in cur_all.iterrows():
        if emp in NOT_STAFF or not emp or emp == 'Nan':
            continue
        role = roles.get(emp)
        if role == 'Not staff':
            continue
        if role not in GROUP_WEIGHTS:
            needs_role.append(emp)
            continue
        W = GROUP_WEIGHTS[role]
        hist = hist_all[hist_all['Employee'] == emp]
        items, notes = [], []

        def add(code, group, label, score, raw, basis):
            items.append({'code': code, 'group': group, 'label': label,
                          'score': None if score is None else round(score), 'raw': raw, 'basis': basis})

        short_month = cur['Days_Active'] < rules['min_days_active']
        if short_month:
            d_ = int(cur['Days_Active'])
            notes.append(f"Only {d_} active day{'s' if d_ != 1 else ''} - performance is scored from "
                         f"{rules['min_days_active']:g} days, so there is no performance pay this month.")
        does_sales = (cur.get('Sales_Lines') or 0) >= rules['min_role_lines']
        does_fetch = (cur.get('Fetch_Lines') or 0) >= rules['min_role_lines']
        does_entry = (cur.get('Entry_Lines') or 0) >= rules['min_role_lines']
        for code, group, label, lower, show in ITEM_DEFS:
            if not W[group]:
                continue
            if group == 'S' and not does_sales:
                continue
            if group == 'F' and not does_fetch:
                continue
            if code == 'B1' and not does_entry:
                continue
            v = cur.get(code)
            if v is None or pd.isna(v):
                continue
            add(code, group, label, _vs_best(v, hist[code], rules, lower), show(v), 'vs own best')

        # Performance Chart (shubhadahealth), scored against their own best chart month, per active day.
        chart = charts.get(month, {}).get(emp)
        gate = None
        if chart:
            rows = chart['rows']
            d = cur['Days_Active'] or None
            for cat, group, code, label in CHART_ITEMS:
                if not W[group] or rows.get(cat) is None:
                    continue
                if cat == 'Purchase Created' and does_entry:
                    continue  # the reckoner already measures their purchase entry (B1)
                past = []
                for mo, per in charts.items():
                    if mo <= month and emp in per and per[emp]['rows'].get(cat) is not None:
                        dd = pm[(pm['Employee'] == emp) & (pm['Calendar_Month'] == mo)]['Days_Active']
                        if len(dd) and dd.iloc[0]:
                            past.append(per[emp]['rows'][cat] / dd.iloc[0])
                score = _vs_best(rows[cat] / d, past, rules) if d else None
                if score is None:
                    continue  # never done this work in any chart month
                add(code, group, label, score, f'{rows[cat]:,.0f} this month', 'chart, vs own best')
            sales_n, ret_n = rows.get('Sales'), rows.get('Sales-Return')
            if sales_n and ret_n is not None:
                rp = ret_n / sales_n * 100
                gate = {'returns_pct': round(rp, 1), 'limit': rules['returns_gate_pct'],
                        'failed': rp > rules['returns_gate_pct']}
            if len([m for m in charts if emp in charts[m] and m <= month]) == 1:
                notes.append('First chart on record, so this month is their own best for chart items.')
        elif role == 'Back-office':
            notes.append(f'No Performance Chart uploaded for {month} - back-office work only counts what the reckoner sees.')

        # Accuracy
        num = lambda v: 0 if v is None or pd.isna(v) else int(v)
        mine = [r for r in log if r['employee'] == emp]
        count = lambda k: sum(1 for r in mine if r['type'] == k)
        if does_entry and cur.get('Invoices') and cur['Invoices'] > 0:
            per100 = num(cur.get('PTR_Errors')) / cur['Invoices'] * 100
            add('Q1', 'Q', 'PTR > MRP entry errors', max(0, 100 - per100 * rk.SCORE_PTR_ERROR_PENALTY),
                f"{num(cur.get('PTR_Errors'))} in {int(cur['Invoices'])} invoices", 'fewer = better')
        mistakes = count('wrong_item') + count('cash_short') + count('near_expiry')
        add('Q2-4', 'Q', 'Logged mistakes (wrong item, cash short, near-expiry)',
            max(0, 100 - mistakes * rules['marks_per_mistake']), f'{mistakes} logged',
            f"-{rules['marks_per_mistake']:g} marks each")
        # Customer
        if W['C']:
            comp, praise = count('complaint'), count('compliment')
            add('C1-2', 'C', 'Complaints and compliments',
                min(max(0, 100 - comp * rules['marks_per_complaint'] + praise * rules['marks_per_compliment']), rules['item_cap_pct']),
                f'{comp} complaint{"s" if comp != 1 else ""}, {praise} compliment{"s" if praise != 1 else ""}',
                f"-{rules['marks_per_complaint']:g} / +{rules['marks_per_compliment']:g} marks")
        # Team
        b = inputs['home_branch'].get(emp)
        if b is not None:
            bb = br[(br['Branch'] == b) & (br['Calendar_Month'] <= month)]
            cur_b = bb[bb['Calendar_Month'] == month]['Per_Day']
            if len(cur_b):
                covers = count('covered')
                t1 = _vs_best(cur_b.iloc[0], bb['Per_Day'], rules)
                if t1 is not None:
                    add('T1', 'T', f'{b} branch sales per day', min(t1 + covers * rules['marks_per_cover'], rules['item_cap_pct']),
                        f'₹{cur_b.iloc[0]:,.0f}/day' + (f', covered {covers} day{"s" if covers != 1 else ""}' if covers else ''),
                        "branch's own best" + (f", +{rules['marks_per_cover']:g} per cover" if covers else ''))
        # Owner's rating
        rt = ratings.get(emp, {})
        if rt:
            add('O1-4', 'O', 'Your 1-5 ratings', np.mean(list(rt.values())) * 20,
                ', '.join(f'{k} {v}' for k, v in sorted(rt.items())), '5 = 100 marks')
        elif W['O']:
            notes.append('Not rated yet this month - the rating part is left out until you rate.')

        groups = []
        for g, name, kn in GROUPS:
            sc = [i['score'] for i in items if i['group'] == g and i['score'] is not None]
            if W[g] and sc:
                groups.append({'code': g, 'name': name, 'kn': kn, 'weight': W[g], 'score': round(float(np.mean(sc)))})
        wsum = sum(x['weight'] for x in groups)
        perf = sum(x['score'] * x['weight'] for x in groups) / wsum if wsum and not short_month else None
        for x in groups:
            x['share_pct'] = round(x['weight'] / wsum * 100) if wsum else 0

        # Discipline
        exit_w = rules['early_exit_weight']
        late, long_lunch, early = num(cur.get('Days_Late')), num(cur.get('Days_Long_Lunch')), num(cur.get('Days_Left_Early'))
        timed = not pd.isna(cur.get('Days_Timed')) and (cur.get('Days_Timed') or 0) > 0
        is_half = emp in half_day
        sh = shifts.get(emp, {})
        agreed = bool(sh.get('start') and sh.get('end'))
        usual = _usual_times(inputs, emp, month)
        shift_info = {'half': is_half, 'agreed': agreed, 'usual': usual, 'other_shift_days': 0,
                      **{k: sh.get(k, '') for k in SHIFT_KEYS}}

        def times(x):
            brk = f", break {x['break_out']}-{x['break_in']}" if x.get('break_out') and not is_half else ''
            return f"{x['start']}-{x['end']}{brk}"
        if agreed:
            res = _shift_discipline(inputs, emp, month, sh, is_half)
            if res:
                late, early, long_lunch = res['late'], res['early'], res['long_break']
                timed = res['judged'] > 0
                n_other = shift_info['other_shift_days'] = res['other_shift_days']
                notes.append(f"Judged against the agreed {'half-day shift' if is_half else 'timings'} {times(sh)}"
                             + ('; no break counted' if is_half else ('' if sh.get('break_out') else '; no break agreed, so breaks not counted'))
                             + (f'; {n_other} day{"s" if n_other != 1 else ""} on another shift not judged' if n_other else '') + '.')
        elif is_half:
            late, early, long_lunch, timed = 0, 0, 0, False
            notes.append('Half-day: no late/early counted until you enter their agreed shift in Setup'
                         + (f" (usually {times(usual)} from the bills)." if usual else '.'))
        elif timed:
            notes.append('No agreed timings yet - judged against their own earliest routine days, which can call ordinary '
                         'days late. Enter their timings in Setup' + (f" (usually {times(usual)})." if usual else '.'))
        incidents = late + long_lunch + early * exit_w
        charged = max(0.0, incidents - rules['free_incidents'])
        leave_days = count('unplanned_leave')
        if not timed and not (is_half and not agreed):
            notes.append('Not enough bill/fetch stamps to time their day - no late/lunch/exit deductions.')

        base = salaries.get(emp)
        floor_hit = perf is not None and perf < rules['floor_pct']
        gate_hit = bool(gate and gate['failed'])
        perf_frac = 0 if (perf is None or floor_hit or gate_hit) else min(perf, rules['perf_cap_pct']) / 100
        if perf is None and not short_month:
            notes.append('Nothing measurable for performance this month.')
        money = None
        if base:
            fixed = base * rules['fixed_pct'] / 100
            perf_max = base * rules['perf_pct'] / 100
            disc_max = base * rules['disc_pct'] / 100
            perf_pay = perf_max * perf_frac
            disc_ded = min(disc_max, disc_max * charged * rules['pct_per_incident'] / 100)
            disc_pay = disc_max - disc_ded
            leave_due = leave_days * rules['unplanned_leave_rs']
            from_disc = min(disc_pay, leave_due)
            from_perf = min(perf_pay, leave_due - from_disc)
            disc_pay -= from_disc
            perf_pay -= from_perf
            leave_taken = from_disc + from_perf
            calculated = fixed + perf_pay + disc_pay
            payable = max(calculated, base) if in_trial else calculated
            money = {k: round(v) for k, v in dict(
                base=base, fixed=fixed, perf_max=perf_max, perf_pay=perf_pay, disc_max=disc_max,
                disc_deduction=disc_ded, disc_pay=disc_pay, leave_due=leave_due, admin_savings=leave_taken,
                calculated=calculated, payable=payable, vs_base=calculated - base).items()}
            if leave_due > leave_taken:
                notes.append(f'Leave penalty capped at the bonus parts: ₹{leave_due - leave_taken:,.0f} not taken (fixed pay is never cut).')
        slips.append({
            'employee': emp, 'role': role, 'month': month, 'days_active': int(cur['Days_Active']),
            'groups': groups, 'items': items, 'perf_score': None if perf is None else round(perf),
            'perf_paid_pct': round(perf_frac * 100), 'floor_hit': floor_hit, 'gate': gate,
            'shift': shift_info,
            'discipline': {'late': late, 'long_lunch': long_lunch, 'early_exit': early, 'incidents': incidents,
                           'free': rules['free_incidents'], 'charged': charged, 'leave_days': leave_days, 'timed': bool(timed)},
            'money': money, 'trial': in_trial, 'notes': notes,
            'log_count': len(mine), 'rated': bool(rt), 'has_chart': bool(chart),
        })
    slips.sort(key=lambda x: x['employee'])
    return {'slips': slips, 'needs_role': sorted(needs_role)}


def summary(result):
    sl = [s for s in result['slips'] if s['money']]
    return {
        'people': len(result['slips']), 'with_salary': len(sl),
        'base_total': sum(s['money']['base'] for s in sl),
        'calculated_total': sum(s['money']['calculated'] for s in sl),
        'payable_total': sum(s['money']['payable'] for s in sl),
        'admin_savings': sum(s['money']['admin_savings'] for s in sl),
        'below_floor': sum(1 for s in result['slips'] if s['floor_hit']),
        'not_rated': sum(1 for s in result['slips'] if not s['rated']),
    }

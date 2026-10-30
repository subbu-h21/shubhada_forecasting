"""Payments received, for the Ask page: a read-only view of the payment dashboard's ledger.

The payment dashboard (a separate app, "project 9 payment dashboard", its own server and login) reads
the shop's bank-alert emails (Karnataka Bank and Axis), records every credit it can read in a flat JSON
file and moves the email to Trash. This module only READS that file - it never writes to it, never talks
to that server, and needs no change in or restart of that app. The file is found at
PAYMENT_LEDGER_PATH (.env), else the payment dashboard's own backend folder (a sibling of the reckoner's parent). With no file there are no tools and the
Ask prompt is exactly what it was before.

What the ledger is: money RECEIVED into the shop's bank accounts (credits only). It is not sales. It
starts when the dashboard started reading (22 Sep 2026). A KBL alert carries no transaction date, so a
payment's time is when the alert reached the mailbox (within a minute or two of the payment); an Axis
alert carries its own date and time. Alerts the dashboard could not read are not in the ledger rows that
count - they are counted separately so an answer can say so.

Privacy: the ledger holds who paid (the UPI payer's name and UPI id). Totals, days, hours, accounts and
"largest payments" never carry a name or a UPI id. Only payments_find_payer returns a payer's name -
exactly like the patient lookups it is withheld from voice conversations - and even that never returns
the UPI id, the UPI reference or the raw bank narration. Amounts are rupees.
"""
import json
import logging
import os
import re
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

logger = logging.getLogger('reckoner_server')

PREFIX = 'payments_'
ROOT = Path(__file__).resolve().parent
# The reckoner lives in <projects>/project2/pharmacy-reckoner; the payment dashboard is <projects>/project 9 payment dashboard.
DEFAULT_PATH = ROOT.parent.parent / 'project 9 payment dashboard' / 'backend' / '.gmail_transactions.json'
IST = timezone(timedelta(hours=5, minutes=30))
MAX_GROUPS = 100                 # rows handed back for one grouping (the newest are kept)
IDENTITY_TOOLS = {PREFIX + 'find_payer'}          # withheld from voice conversations, like the patient lookups

_lock = threading.Lock()
_cache = {'key': None, 'rows': [], 'unread': [], 'info': {}}


def ledger_path():
    raw = os.environ.get('PAYMENT_LEDGER_PATH', '').strip()
    return Path(raw) if raw else DEFAULT_PATH


# ---------------------------------------------------------------------------
# Reading the ledger
# ---------------------------------------------------------------------------
def _email_time(value):
    """'Wed, 30 Sep 2026 10:20:11 +0530 (IST)' -> naive India time, or None."""
    try:
        return parsedate_to_datetime(str(value)).astimezone(IST).replace(tzinfo=None)
    except Exception:
        return None


def _txn_time(day, clock):
    """Axis style: '01-10-26' and '14:05:09' -> datetime, or None."""
    m = re.fullmatch(r'\s*(\d{1,2})-(\d{1,2})-(\d{2,4})\s*', str(day or ''))
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y += 2000 if y < 100 else 0
    t = re.fullmatch(r'\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*', str(clock or ''))
    try:
        return datetime(y, mo, d, int(t.group(1)) if t else 0, int(t.group(2)) if t else 0, int(t.group(3) or 0) if t else 0)
    except ValueError:
        return None


def _load():
    """-> (payments, unread alerts, info), refreshed whenever the file changes. Always safe: a missing or
    broken file just means no payments."""
    path = ledger_path()
    try:
        st = path.stat()
    except OSError:
        return [], [], {}
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _lock:
        if _cache['key'] == key:
            return _cache['rows'], _cache['unread'], _cache['info']
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        records = list(data.values()) if isinstance(data, dict) else []
    except Exception:
        logger.warning('[payments] the ledger file could not be read')
        return [], [], {}
    rows, unread = [], []
    for r in records:
        if not isinstance(r, dict):
            continue
        email_at = _email_time(r.get('gmail_date'))
        if r.get('status') == 'flagged':
            if email_at:
                unread.append(email_at)
            continue
        amount = r.get('amount')
        if r.get('status') != 'trashed' or not isinstance(amount, (int, float)) or isinstance(amount, bool):
            continue
        when = _txn_time(r.get('txn_date'), r.get('txn_time')) or email_at
        if not when:
            continue
        rows.append({'when': when, 'amount': float(amount), 'account': str(r.get('account') or ''),
                     'bank': str(r.get('bank_name') or ''), 'payer': str(r.get('counterparty_name') or '').strip(),
                     'balance': r.get('balance_after') if isinstance(r.get('balance_after'), (int, float)) else None,
                     'direction': str(r.get('direction') or '')})
    rows.sort(key=lambda x: x['when'])
    unread.sort()
    info = {'modified': datetime.fromtimestamp(st.st_mtime)}
    with _lock:
        _cache.update(key=key, rows=rows, unread=unread, info=info)
    return rows, unread, info


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _day(value, name):
    """'2026-10-05' -> date. Raises a message the model can act on."""
    if value in (None, ''):
        return None
    try:
        return datetime.strptime(str(value).strip()[:10], '%Y-%m-%d').date()
    except ValueError:
        raise ValueError('%s must be a date like 2026-10-05' % name)


def _window(rows, date_from, date_to, account=None, bank=None):
    lo, hi = _day(date_from, 'date_from'), _day(date_to, 'date_to')
    if lo and hi and lo > hi:
        lo, hi = hi, lo
    acc = str(account or '').strip().lower()
    bnk = str(bank or '').strip().lower()
    out = [r for r in rows
           if (not lo or r['when'].date() >= lo) and (not hi or r['when'].date() <= hi)
           and (not acc or acc in r['account'].lower()) and (not bnk or bnk in r['bank'].lower())]
    return out, lo, hi


def _money(x):
    return round(float(x), 2)


def _stamp(dt):
    return {'date': dt.strftime('%Y-%m-%d'), 'time': dt.strftime('%H:%M')}


def _key_for(dt, group_by):
    if group_by == 'day':
        return dt.strftime('%Y-%m-%d')
    if group_by == 'week':
        return (dt.date() - timedelta(days=dt.weekday())).strftime('%Y-%m-%d')     # the Monday that starts the week
    if group_by == 'month':
        return dt.strftime('%Y-%m')
    if group_by == 'hour':
        return '%02d:00' % dt.hour
    if group_by == 'weekday':
        return dt.strftime('%A')
    return ''


_GROUPS = ('none', 'day', 'week', 'month', 'hour', 'weekday', 'account')


def _unread_in(unread, lo, hi):
    return sum(1 for u in unread if (not lo or u.date() >= lo) and (not hi or u.date() <= hi))


def _note(rows_all, lo):
    first = rows_all[0]['when'] if rows_all else None
    if first and lo and lo < first.date():
        return 'The ledger only starts on %s, so nothing before that is recorded.' % first.strftime('%Y-%m-%d')
    return ''


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------
def payments_summary(date_from=None, date_to=None, group_by='day', account=None, bank=None):
    rows_all, unread, _info = _load()
    if not rows_all:
        return {'available': False, 'reason': 'no payments ledger is available right now'}
    group_by = str(group_by or 'day').lower()
    if group_by not in _GROUPS:
        return {'error': 'group_by must be one of ' + ', '.join(_GROUPS)}
    rows, lo, hi = _window(rows_all, date_from, date_to, account, bank)
    out = {'what': 'money received into the shop\'s bank accounts (credits only, not sales)',
           'period': {'from': (lo or rows_all[0]['when'].date()).isoformat(), 'to': (hi or rows_all[-1]['when'].date()).isoformat()},
           'payments': len(rows), 'total_received': _money(sum(r['amount'] for r in rows)),
           'unreadable_alerts_in_period': _unread_in(unread, lo, hi), 'groups': []}
    if rows:
        out['average_payment'] = _money(out['total_received'] / len(rows))
        big = max(rows, key=lambda r: r['amount'])
        out['largest_payment'] = dict(_stamp(big['when']), amount=_money(big['amount']), account=big['account'])
    note = _note(rows_all, lo)
    if note:
        out['note'] = note
    if group_by == 'none' or not rows:
        return out
    buckets = defaultdict(lambda: [0, 0.0])
    for r in rows:
        k = r['account'] + (' (%s)' % r['bank'] if r['bank'] else '') if group_by == 'account' else _key_for(r['when'], group_by)
        buckets[k][0] += 1
        buckets[k][1] += r['amount']
    keys = sorted(buckets) if group_by != 'weekday' else sorted(buckets, key=lambda d: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].index(d))
    if len(keys) > MAX_GROUPS:
        keys = keys[-MAX_GROUPS:]
        out['groups_note'] = 'only the latest %d groups are shown' % MAX_GROUPS
    out['group_by'] = group_by
    out['groups'] = [{'key': k, 'payments': buckets[k][0], 'total': _money(buckets[k][1])} for k in keys]
    return out


def payments_largest(date_from=None, date_to=None, n=10, min_amount=None, account=None):
    rows_all, unread, _info = _load()
    if not rows_all:
        return {'available': False, 'reason': 'no payments ledger is available right now'}
    rows, lo, hi = _window(rows_all, date_from, date_to, account)
    try:
        n = max(1, min(int(n), 50))
    except (TypeError, ValueError):
        n = 10
    if isinstance(min_amount, (int, float)):
        rows = [r for r in rows if r['amount'] >= min_amount]
    top = sorted(rows, key=lambda r: (-r['amount'], r['when']))[:n]
    out = {'what': 'the largest single payments received (no payer names are included)',
           'count_matching': len(rows), 'payments': [dict(_stamp(r['when']), amount=_money(r['amount']), account=r['account'], bank=r['bank']) for r in top]}
    note = _note(rows_all, lo)
    if note:
        out['note'] = note
    return out


def _tokens(text):
    return [t for t in re.split(r'\W+', str(text or '').casefold()) if t]


def payments_find_payer(name, date_from=None, date_to=None, limit=15):
    """Payments from a named payer (UPI payer name). The one tool that returns who paid."""
    rows_all, unread, _info = _load()
    if not rows_all:
        return {'available': False, 'reason': 'no payments ledger is available right now'}
    words = _tokens(name)
    if not words:
        return {'error': 'say whose payments to look for, e.g. "Ramesh"'}
    rows, lo, hi = _window(rows_all, date_from, date_to)
    hit = [r for r in rows if r['payer'] and all(w in _tokens(r['payer']) or any(t.startswith(w) for t in _tokens(r['payer'])) for w in words)]
    try:
        limit = max(1, min(int(limit), 40))
    except (TypeError, ValueError):
        limit = 15
    by = defaultdict(list)
    for r in hit:
        by[r['payer']].append(r)
    payers = sorted(by.items(), key=lambda kv: -sum(x['amount'] for x in kv[1]))
    out = {'what': 'payments received from payers whose UPI name matches (names are customer identity)',
           'matching_payers': len(payers), 'payments': len(hit), 'total_received': _money(sum(r['amount'] for r in hit)),
           'payers': []}
    for payer, items in payers[:5]:
        recent = sorted(items, key=lambda r: r['when'], reverse=True)[:limit if len(payers) <= 2 else 5]
        out['payers'].append({'payer': payer, 'payments': len(items), 'total': _money(sum(r['amount'] for r in items)),
                              'first': _stamp(min(r['when'] for r in items))['date'], 'last': _stamp(max(r['when'] for r in items))['date'],
                              'recent': [dict(_stamp(r['when']), amount=_money(r['amount']), account=r['account']) for r in recent]})
    if len(payers) > 5:
        out['note'] = 'more payers match than are shown - ask for a fuller name'
    if not hit:
        out['note'] = 'no payer name in the ledger matches that (it holds the name the payer\'s UPI app sent, which is often shortened)'
    return out


def payments_ledger_info():
    rows_all, unread, info = _load()
    if not rows_all:
        return {'available': False, 'reason': 'no payments ledger is available right now'}
    accounts = {}
    for r in rows_all:
        a = accounts.setdefault(r['account'] or '(unknown)', {'bank': r['bank'], 'payments': 0, 'total': 0.0, 'balance_shown': None, 'balance_as_of': None})
        a['payments'] += 1
        a['total'] += r['amount']
        if r['balance'] is not None:
            a['balance_shown'], a['balance_as_of'] = _money(r['balance']), r['when'].strftime('%Y-%m-%d %H:%M')
    for a in accounts.values():
        a['total'] = _money(a['total'])
    return {'what': 'the payment dashboard\'s ledger of bank credit alerts (money received)',
            'first_payment': rows_all[0]['when'].strftime('%Y-%m-%d %H:%M'), 'latest_payment': rows_all[-1]['when'].strftime('%Y-%m-%d %H:%M'),
            'payments': len(rows_all), 'total_received': _money(sum(r['amount'] for r in rows_all)),
            'unreadable_alerts': len(unread), 'accounts': accounts,
            'balance_note': 'a balance is the one printed in the latest alert for that account, as of the time shown - not a live bank balance'}


_DATES = {'date_from': {'type': 'string', 'description': 'first day, YYYY-MM-DD (inclusive); leave out for the start of the ledger'},
          'date_to': {'type': 'string', 'description': 'last day, YYYY-MM-DD (inclusive); leave out for today'}}

_SPECS = [
    {'name': PREFIX + 'summary', 'fn': payments_summary,
     'description': ('Money RECEIVED into the shop\'s bank accounts (UPI / bank credits read from the bank alerts - NOT sales): '
                     'total, number of payments, average and largest, optionally grouped by day, week, month, hour of day, '
                     'weekday or bank account. Use for "how much UPI came in today / yesterday / this month", "daily payments '
                     'received", "which hour do most payments arrive". No payer names. The ledger starts 22 Sep 2026.'),
     'parameters': {'type': 'object', 'properties': dict(_DATES, group_by={'type': 'string', 'enum': list(_GROUPS), 'description': 'default day'},
                                                         account={'type': 'string', 'description': 'part of a masked account number, to limit to one account'},
                                                         bank={'type': 'string', 'description': 'KBL or Axis, to limit to one bank'}),
                    'required': []}},
    {'name': PREFIX + 'largest', 'fn': payments_largest,
     'description': ('The largest single payments received (amount, date, time, account) in a period - never the payer\'s name. '
                     'Use for "biggest payments this week", "any payment over 10000 today".'),
     'parameters': {'type': 'object', 'properties': dict(_DATES, n={'type': 'integer', 'description': 'how many, default 10, at most 50'},
                                                         min_amount={'type': 'number', 'description': 'only payments of at least this many rupees'},
                                                         account={'type': 'string', 'description': 'part of a masked account number'}),
                    'required': []}},
    {'name': PREFIX + 'find_payer', 'fn': payments_find_payer,
     'description': ('Payments received FROM A NAMED PAYER (the name the payer\'s UPI app sent, often shortened): their count, total and '
                     'latest payments. This returns customer identity - use it only when the owner asks about a person by name '
                     '("payments from Ramesh"), never to list payers.'),
     'parameters': {'type': 'object', 'properties': dict(_DATES, name={'type': 'string', 'description': 'the payer\'s name or part of it'},
                                                         limit={'type': 'integer', 'description': 'recent payments to list, default 15'}),
                    'required': ['name']}},
    {'name': PREFIX + 'ledger_info', 'fn': payments_ledger_info,
     'description': ('What the payments ledger covers: first and latest payment, how many payments and rupees, how many alerts could not '
                     'be read, and each bank account with the balance printed in its latest alert (as of that time, not live). '
                     'Use for "what is the bank balance" or "how up to date is this".'),
     'parameters': {'type': 'object', 'properties': {}, 'required': []}},
]
_BY_NAME = {s['name']: s for s in _SPECS}


def available():
    return bool(_load()[0])


def tool_specs(voice=False):
    """The tools to offer: none unless the ledger has payments; the payer lookup is withheld in voice conversations."""
    if not available():
        return []
    return [{'name': s['name'], 'description': s['description'], 'parameters': s['parameters']}
            for s in _SPECS if not (voice and s['name'] in IDENTITY_TOOLS)]


def is_payment_tool(name):
    return name in _BY_NAME


def run(name, args):
    spec = _BY_NAME.get(name)
    if spec is None:
        return {'error': 'unknown tool %s' % name}
    try:
        return spec['fn'](**(args if isinstance(args, dict) else {}))
    except TypeError as e:
        return {'error': 'bad arguments: %s' % e}
    except ValueError as e:
        return {'error': str(e)}
    except Exception as e:
        logger.warning('[payments] %s failed: %s: %s', name, type(e).__name__, e)
        return {'error': 'the payments ledger could not be read (%s)' % type(e).__name__}

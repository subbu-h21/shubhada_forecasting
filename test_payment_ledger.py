"""Offline tests for payment_ledger.py (the Ask page's read-only view of the payment dashboard's ledger).

No network, no model call, no real data: the ledger is a small made-up file shaped like the payment
dashboard's .gmail_transactions.json (made-up payers, made-up UPI ids).

Run with the reckoner's venv:
    venv/Scripts/python.exe test_payment_ledger.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('OPENROUTER_API_KEY', 'sk-test-not-real')
sys.stdout.reconfigure(encoding='utf-8')

import payment_ledger as P

fails = []


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  ' + str(extra)))
    if not cond:
        fails.append(name)


def kbl(mid, gmail_date, amount, payer, balance=None, status='trashed', vpa='x@bank'):
    return {'message_id': mid, 'sender': 'alerts@example.bank', 'subject': 'Credit alert', 'gmail_date': gmail_date, 'status': status,
            'channel_label': None, 'bank_name': 'KBL', 'narration_raw': 'UPI/%s/%s' % (payer, vpa), 'account': 'x000001x',
            'direction': 'CREDITED', 'amount': amount, 'upi_ref': 'REF' + mid, 'counterparty_vpa': vpa, 'counterparty_name': payer,
            'balance_after': balance, 'txn_date': None, 'txn_time': None}


LEDGER = {
    'R1': kbl('R1', 'Wed, 30 Sep 2026 10:20:11 +0530 (IST)', 100.0, 'Asha Rao', 5000.0, vpa='asha@upi-one'),
    'R2': kbl('R2', 'Wed, 30 Sep 2026 22:30:00 +0000', 250.5, 'Asha Rao', 5250.5, vpa='asha@upi-one'),          # 04:00 IST next day
    'R3': kbl('R3', 'Thu, 1 Oct 2026 11:05:00 +0530 (IST)', 40.0, 'Bala Shetty', vpa='bala@upi-two'),
    'R4': dict(kbl('R4', 'Thu, 01 Oct 2026 14:06:00 +0530', 999.0, 'Asha Rao', 20000.0, vpa='asha@upi-one'),
               bank_name='Axis', account='XX0000', txn_date='01-10-26', txn_time='14:05:09', channel_label='PhonePe/GPay'),
    'R5': kbl('R5', 'Fri, 02 Oct 2026 09:00:00 +0530 (IST)', 10000.0, 'Chandra Pai', 15000.0, vpa='chandra@upi-three'),
    'R6': {'message_id': 'R6', 'sender': 'alerts@example.bank', 'subject': 'Alert', 'gmail_date': 'Fri, 02 Oct 2026 12:00:00 +0530',
           'status': 'flagged', 'error': 'pattern did not match'},
    'R7': {'message_id': 'R7', 'sender': 'alerts@example.bank', 'subject': 'Alert', 'gmail_date': 'Thu, 01 Oct 2026 16:00:00 +0530',
           'status': 'flagged', 'error': 'pattern did not match'},
    'R8': dict(kbl('R8', 'Fri, 02 Oct 2026 10:00:00 +0530', None, 'Nobody Known'), amount=None),                # no amount: not counted
    'R9': dict(kbl('R9', 'not a date', 5.0, 'Nobody Dated')),                                                     # no usable time: not counted
    'R10': kbl('R10', 'Mon, 05 Oct 2026 10:00:00 +0530 (IST)', 60.0, 'Chandra Pai', 15060.0, vpa='chandra@upi-three'),
    'R11': kbl('R11', 'Sat, 03 Oct 2026 12:00:00 +0530 (IST)', 30.0, 'Asha Menon', vpa='menon@upi-four'),
}
tmp = Path(tempfile.mkdtemp())
path = tmp / 'ledger.json'
path.write_text(json.dumps(LEDGER), encoding='utf-8')
os.environ['PAYMENT_LEDGER_PATH'] = str(path)


def summ(**kw):
    return P.run('payments_summary', kw)


# --- reading ----------------------------------------------------------------------------------
rows, unread, _ = P._load()
check('only readable, dated, amounted credits count (7 of 11 records)', len(rows) == 7, len(rows))
check('alerts that could not be read are counted apart, not in the totals', len(unread) == 2)
check('an alert in UTC is converted to India time (22:30 UTC = 04:00 next day)',
      [r for r in rows if r['amount'] == 250.5][0]['when'].strftime('%Y-%m-%d %H:%M') == '2026-10-01 04:00')
check('an Axis alert uses its own transaction date and time, not the email\'s',
      [r for r in rows if r['bank'] == 'Axis'][0]['when'].strftime('%Y-%m-%d %H:%M:%S') == '2026-10-01 14:05:09')
check('a KBL alert (no transaction date) uses the email time',
      [r for r in rows if r['amount'] == 100.0][0]['when'].strftime('%Y-%m-%d %H:%M:%S') == '2026-09-30 10:20:11')

# --- summary --------------------------------------------------------------------------------------
s = summ(group_by='none')
check('whole ledger: count and total', s['payments'] == 7 and s['total_received'] == 11479.5, s)
check('the whole-ledger period runs from the first to the last payment', s['period'] == {'from': '2026-09-30', 'to': '2026-10-05'}, s['period'])
check('the largest payment and the average come with it', s['largest_payment']['amount'] == 10000.0 and s['average_payment'] == round(11479.5 / 7, 2))
check('unreadable alerts are reported for the period', s['unreadable_alerts_in_period'] == 2)
s = summ(date_from='2026-10-01', date_to='2026-10-01', group_by='none')
check('a one-day window includes both ends and uses India time', s['payments'] == 3 and s['total_received'] == 1289.5, s)
check('unreadable alerts are counted only inside the window', s['unreadable_alerts_in_period'] == 1)
s = summ(group_by='day')
check('grouped by day', [(g['key'], g['payments'], g['total']) for g in s['groups']] ==
      [('2026-09-30', 1, 100.0), ('2026-10-01', 3, 1289.5), ('2026-10-02', 1, 10000.0), ('2026-10-03', 1, 30.0), ('2026-10-05', 1, 60.0)], s['groups'])
s = summ(group_by='month')
check('grouped by month', [(g['key'], g['payments'], g['total']) for g in s['groups']] == [('2026-09', 1, 100.0), ('2026-10', 6, 11379.5)], s['groups'])
s = summ(group_by='week')
check('grouped by week, labelled by the Monday that starts it',
      [(g['key'], g['payments'], g['total']) for g in s['groups']] == [('2026-09-28', 6, 11419.5), ('2026-10-05', 1, 60.0)], s['groups'])
s = summ(group_by='hour')
check('grouped by hour of day', {g['key']: g['payments'] for g in s['groups']}['10:00'] == 2 and s['groups'][0]['key'] == '04:00', s['groups'])
s = summ(group_by='weekday')
check('grouped by weekday, in week order', [g['key'] for g in s['groups']] == ['Monday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'], s['groups'])
s = summ(group_by='account')
check('grouped by account with its bank', {g['key']: g['payments'] for g in s['groups']} == {'x000001x (KBL)': 6, 'XX0000 (Axis)': 1}, s['groups'])
check('limited to one account', summ(account='xx0000', group_by='none')['total_received'] == 999.0)
check('limited to one bank', summ(bank='kbl', group_by='none')['payments'] == 6)
check('a window with nothing in it says so with zeros, not an error', summ(date_from='2026-12-01', group_by='day')['payments'] == 0)
check('asking from before the ledger starts carries a note', 'only starts on 2026-09-30' in summ(date_from='2026-08-01', group_by='none').get('note', ''))
check('dates given the wrong way round are swapped', summ(date_from='2026-10-02', date_to='2026-10-01', group_by='none')['payments'] == 4)
check('a bad date is explained to the model, not raised', 'YYYY' in str(summ(date_from='last tuesday')) or 'like 2026-10-05' in str(summ(date_from='last tuesday')))
check('an unknown grouping is explained', 'group_by must be' in str(summ(group_by='fortnight')))

# --- largest -----------------------------------------------------------------------------------------
big = P.run('payments_largest', {'n': 2})
check('the largest payments come biggest first with date, time and account',
      [p['amount'] for p in big['payments']] == [10000.0, 999.0] and big['payments'][0]['time'] == '09:00' and big['payments'][0]['account'] == 'x000001x', big)
check('a minimum amount narrows the list', P.run('payments_largest', {'min_amount': 500})['count_matching'] == 2)
check('a silly n is clamped', len(P.run('payments_largest', {'n': 'many'})['payments']) == 7 and len(P.run('payments_largest', {'n': 0})['payments']) == 1)

# --- who paid --------------------------------------------------------------------------------------------
who = P.run('payments_find_payer', {'name': 'asha'})
check('a name matches by whole word or start, across payers', who['matching_payers'] == 2 and who['payments'] == 4, who)
one = P.run('payments_find_payer', {'name': 'asha rao'})
check('one payer: their count, total, first and last day', one['matching_payers'] == 1 and one['payers'][0]['payments'] == 3
      and one['payers'][0]['total'] == 1349.5 and one['payers'][0]['first'] == '2026-09-30' and one['payers'][0]['last'] == '2026-10-01', one)
check('their recent payments are listed newest first', [r['amount'] for r in one['payers'][0]['recent']] == [999.0, 250.5, 100.0], one['payers'][0]['recent'])
check('a window applies to the payer lookup', P.run('payments_find_payer', {'name': 'asha rao', 'date_from': '2026-10-01'})['payments'] == 2)
none = P.run('payments_find_payer', {'name': 'zzzz'})
check('no match says so plainly', none['payments'] == 0 and 'no payer name' in none['note'])
check('no name is asked for', 'error' in P.run('payments_find_payer', {'name': '  '}))

# --- privacy -----------------------------------------------------------------------------------------------
everything = json.dumps([summ(group_by=g) for g in P._GROUPS] + [P.run('payments_largest', {}), P.run('payments_ledger_info', {})])
check('totals, days, accounts, largest and info never carry a payer name',
      not any(n in everything for n in ('Asha', 'Bala', 'Chandra', 'Nobody')), everything[:200])
lookups = json.dumps([who, one, none])
check('even the payer lookup never carries a UPI id, a reference or the bank narration',
      not any(x in lookups for x in ('upi-one', 'upi-two', 'upi-three', 'upi-four', 'REF', 'narration', 'UPI/', 'vpa')), lookups[:300])
check('the payer lookup is the only tool that names anyone', {s['name'] for s in P._SPECS if s['name'] in P.IDENTITY_TOOLS} == {'payments_find_payer'})

# --- coverage and balance ---------------------------------------------------------------------------------------
info = P.run('payments_ledger_info', {})
check('info: first and latest payment, counts, unreadable', info['first_payment'] == '2026-09-30 10:20' and info['latest_payment'] == '2026-10-05 10:00'
      and info['payments'] == 7 and info['unreadable_alerts'] == 2, info)
check('info: each account with the balance of its latest alert and when',
      info['accounts']['x000001x']['balance_shown'] == 15060.0 and info['accounts']['x000001x']['balance_as_of'] == '2026-10-05 10:00'
      and info['accounts']['XX0000']['balance_shown'] == 20000.0 and info['accounts']['XX0000']['bank'] == 'Axis', info['accounts'])
check('info says a balance is not live', 'not a live' in info['balance_note'])

# --- the file changes, goes missing, breaks ----------------------------------------------------------------------------
more = dict(LEDGER, R12=kbl('R12', 'Tue, 06 Oct 2026 09:00:00 +0530', 500.0, 'Late Payer'))
path.write_text(json.dumps(more), encoding='utf-8')
check('a changed file is picked up without a restart', summ(group_by='none')['payments'] == 8)
path.write_text('{ not json', encoding='utf-8')
check('a broken file gives no tools and a plain "not available", never a crash',
      P.tool_specs() == [] and summ().get('available') is False and not P.available())
os.environ['PAYMENT_LEDGER_PATH'] = str(tmp / 'nothing-here.json')
check('no file at all: no tools', P.tool_specs() == [] and P.run('payments_ledger_info', {}).get('available') is False)
path.write_text(json.dumps(LEDGER), encoding='utf-8')
os.environ['PAYMENT_LEDGER_PATH'] = str(path)
check('the file coming back brings the tools back', len(P.tool_specs()) == 4)
check('the default location is the payment dashboard\'s ledger, a sibling of the reckoner\'s parent folder',
      P.DEFAULT_PATH.parts[-3:] == ('project 9 payment dashboard', 'backend', '.gmail_transactions.json')
      and P.DEFAULT_PATH.parent.parent.parent == ROOT.parent.parent)

# --- dispatch and arguments ------------------------------------------------------------------------------------------------
check('an unknown tool is refused', 'unknown tool' in str(P.run('payments_nothing', {})))
check('wrong arguments are explained, not raised', 'bad arguments' in str(P.run('payments_summary', {'colour': 'red'})))
check('arguments that are not a dict are ignored', P.run('payments_summary', None)['payments'] == 7)
check('the tools are described with names, descriptions and parameters',
      all(set(t) == {'name', 'description', 'parameters'} and t['name'].startswith('payments_') for t in P.tool_specs()))
check('voice conversations lose only the payer lookup',
      {t['name'] for t in P.tool_specs(voice=True)} == {'payments_summary', 'payments_largest', 'payments_ledger_info'}
      and 'payments_find_payer' in {t['name'] for t in P.tool_specs()})

# --- plugged into Ask ---------------------------------------------------------------------------------------------------------
import ask as ask_mod
names = {t['name'] for t in ask_mod._available_tools()}
check('Ask offers the payment tools', {'payments_summary', 'payments_largest', 'payments_find_payer', 'payments_ledger_info'} <= names)
vnames = {t['name'] for t in ask_mod._available_tools(voice=True)}
check('Ask withholds the payer lookup in voice mode', 'payments_summary' in vnames and 'payments_find_payer' not in vnames)
check('Ask runs a payment tool when it was offered', ask_mod._run_allowed_tool('payments_summary', {'group_by': 'none'}, names)['payments'] == 7)
check('Ask refuses a payment tool that was withheld',
      'not available in this mode' in str(ask_mod._run_allowed_tool('payments_find_payer', {'name': 'asha'}, vnames)))
check('the prompt gains the PAYMENTS section while the ledger has payments',
      'PAYMENTS RECEIVED' in ask_mod._build_system_prompt() and 'NOT sales' in ask_mod._build_system_prompt())
os.environ['PAYMENT_LEDGER_PATH'] = str(tmp / 'nothing-here.json')
check('and loses it, with the tools, when it does not',
      'PAYMENTS RECEIVED' not in ask_mod._build_system_prompt() and not any(t['name'].startswith('payments_') for t in ask_mod._available_tools()))
os.environ['PAYMENT_LEDGER_PATH'] = str(path)

print()
print('ALL PASSED' if not fails else f'{len(fails)} FAILED: ' + '; '.join(fails))
sys.exit(1 if fails else 0)

"""Tells the leave app which working days someone was absent with no information, so the absence is on record there too.

The salary calculator already finds these from the punch-ins (a day the shop was open, with no punch-in, no bill and no leave applied; slip
reason "absent without leave") and cuts a day's pay for each. Here the same days are posted to the leave app
(POST /api/export/absences, same token and same PC as the rest of the link) so its calendar, activity log and the assistant show them.

The whole month is posted each time and the leave app follows the list, so a day drops off when the person turns out to have punched in or leave
was applied. Failures are silent: the slip does not depend on this, and the next Salary screen opening posts again.
"""
import hashlib
import json
import logging
import time
import urllib.error
import urllib.request

import leave_app
import leave_notify
import salary

logger = logging.getLogger(__name__)

REPOST_AFTER_S = 1800          # post an unchanged list again after this long (the leave app may have been restored from a backup)
_last = {}                     # month -> {'hash': str, 'at': time}
ABSENT_WHY = 'absent without leave'
HALF_WHY = 'absent half the day'      # half of a full-day shift not seen (the owner, 12 Nov 2026) - posted with its half; leave apps before that ignore it


def rows_from_slips(slips, month):
    """[{emp_code, date}] for every absent-without-leave day on the month's slips, and [{emp_code, date, portion}] for every half day not seen
    (people the leave app does not know are left out). Days the owner marked absent are already in the leave app, so they are not posted."""
    data = salary.load_leave(month)
    if not data:
        return []
    names = salary.match_site_names(sorted({e['name'] for e in data.get('employees', [])}))
    code_of = {names.get(e['name']): e['emp_code'] for e in data.get('employees', []) if names.get(e['name'])}
    rows = []
    for sl in slips:
        code = code_of.get(sl.get('employee'))
        if not code:
            continue
        for item in (sl.get('leave') or {}).get('lop', []):
            why, date = str(item.get('why', '')), str(item.get('date', ''))
            if not date.startswith(month):
                continue
            if why.startswith(ABSENT_WHY):
                rows.append({'emp_code': code, 'date': date})
            elif why.startswith(HALF_WHY) and item.get('portion') in ('morning', 'evening'):
                rows.append({'emp_code': code, 'date': date, 'portion': item['portion']})
    return sorted(rows, key=lambda r: (r['date'], r['emp_code']))


def post(month, rows, timeout=5):
    """Posts the month's list; returns the leave app's answer. Raises RuntimeError with a plain reason."""
    token = leave_notify.token(create=False)
    if not token:
        raise RuntimeError('The leave app link is not set up (no LEAVE_NOTIFY_TOKEN in .env).')
    req = urllib.request.Request(
        f"{leave_app.url()}/api/export/absences",
        data=json.dumps({'month': month, 'absences': rows}).encode('utf-8'), method='POST',
        headers={'Content-Type': 'application/json', 'X-Requested-With': 'fetch', 'X-Notify-Token': token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code in (404, 405):
            raise RuntimeError('The leave app is an older version without absence records - restart it with the new code.') from None
        raise RuntimeError(f'The leave app refused ({e.code}).') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError(f'The leave app is not running at {leave_app.url()}.') from None


def sync(slips, month):
    """Post the month's absences to the leave app when they changed (or every REPOST_AFTER_S). Never raises."""
    try:
        if not month or not slips:
            return None
        rows = rows_from_slips(slips, month)
        digest = hashlib.sha1(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        last = _last.get(month)
        if last and last['hash'] == digest and time.time() - last['at'] < REPOST_AFTER_S:
            return None
        answer = post(month, rows)
        _last[month] = {'hash': digest, 'at': time.time()}
        if answer.get('added') or answer.get('removed'):
            logger.info('Leave app: %s absences posted (+%s -%s)', month, answer.get('added'), answer.get('removed'))
        return answer
    except Exception as exc:     # the slips must never fail because of this
        logger.info('Leave app: absences not posted - %s', exc)
        return None

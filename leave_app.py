"""Reads a month of leave and Sunday work from the shop's leave app
(project 13, Node, on this PC) for the salary slips.

The leave app's GET /api/export/month answers only requests from this PC
that carry the shared secret - the same LEAVE_NOTIFY_TOKEN the leave app uses
to send WhatsApp through the reckoner (leave_notify.py). Each month's answer is
saved in data/processed/salary/leave/<month>.json, so slips still work when
the leave app is closed.

What the slips take from it (owner, 2026-10-15):
  - granted leave days: paid, sick (paid) or loss of pay (LOP = base / 30)
  - Sunday sessions worked and their extra pay (rates set in the leave app)
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import leave_notify

DEFAULT_URL = 'http://127.0.0.1:3001'


def url():
    return (os.environ.get('LEAVE_APP_URL') or leave_notify._env('LEAVE_APP_URL') or DEFAULT_URL).rstrip('/')


def fetch(month, timeout=5):
    """The month's export from the leave app; raises RuntimeError with a plain reason."""
    token = leave_notify.token(create=False)
    if not token:
        raise RuntimeError('The leave app link is not set up (no LEAVE_NOTIFY_TOKEN in .env).')
    req = urllib.request.Request(f"{url()}/api/export/month?month={urllib.parse.quote(month)}",
                                 headers={'X-Notify-Token': token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = json.loads(res.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError('The leave app is an older version without the salary export - restart it with the new code.') from None
        raise RuntimeError(f'The leave app refused ({e.code}). Is its NOTIFY_TOKEN the same as the reckoner\'s?') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError(f'The leave app is not running at {url()}. Start it with its start.bat.') from None
    if data.get('month') != month or not isinstance(data.get('leaveDays'), list):
        raise RuntimeError('The leave app sent something unexpected.')
    return data

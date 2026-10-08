"""A brake on /api/ask: a daily question cap and a short-burst cap.

Every question can trigger several paid model calls (a tool-calling loop plus a final
answer), and /api/ask had no limit at all. That matters more now that this page is also
the front door for order questions: the order dashboard has its own daily rupee cap, but
a question asked HERE never passes through it. This is the equivalent guard for this page.

It counts questions, not rupees (this project does not track model cost). Both limits are
set from the environment / .env, and 0 switches that limit off:
    ASK_DAILY_LIMIT_QUESTIONS   default 200   questions per calendar day, whole page
    ASK_PER_10_MIN              default 20    questions in any rolling 10 minutes

A question is counted when it is accepted, whether or not the model then succeeds - a
failed call can still have cost money. The daily count survives a restart (a small JSON file
in data/); the 10-minute window does not, which is fine for a burst guard.
"""
import datetime
import json
import os
import threading
import time
from collections import deque
from pathlib import Path

_PATH = Path(__file__).parent / 'data' / 'ask_daily.json'
_WINDOW_S = 600
_lock = threading.Lock()
_recent = deque()


class LimitReached(Exception):
    """The message is safe to show on the page."""


def _limit(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def _load_count(day):
    try:
        data = json.loads(_PATH.read_text(encoding='utf-8'))
        return int(data['count']) if data.get('date') == day else 0
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def _save_count(day, count):
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix('.tmp')
        tmp.write_text(json.dumps({'date': day, 'count': count}), encoding='utf-8')
        os.replace(tmp, _PATH)
    except OSError:
        pass   # a counter that cannot be saved must never stop the owner asking


def check(today=None):
    """Counts one question, or raises LimitReached."""
    daily = _limit('ASK_DAILY_LIMIT_QUESTIONS', 200)
    burst = _limit('ASK_PER_10_MIN', 20)
    now = time.monotonic()
    day = (today or datetime.date.today()).isoformat()
    with _lock:
        if burst:
            while _recent and now - _recent[0] > _WINDOW_S:
                _recent.popleft()
            if len(_recent) >= burst:
                minutes = max(1, int((_WINDOW_S - (now - _recent[0])) / 60) + 1)
                raise LimitReached(f'That is a lot of questions in a short time - try again in about {minutes} minute(s).')
        count = _load_count(day)
        if daily and count >= daily:
            raise LimitReached(f"Today's limit of {daily} questions has been reached; it resets at midnight. "
                               "(It guards the AI spend - ASK_DAILY_LIMIT_QUESTIONS in .env changes it.)")
        _recent.append(now)
        _save_count(day, count + 1)


def reset_for_tests():
    with _lock:
        _recent.clear()

"""The Ask page's way into the Shop Assistant (project-15 "Shubhada Brain", port 8030).

The Ask page used to answer from the reckoner's own tools only. With this module a typed or spoken question goes to the Shop Assistant's
owner chat instead - the same brain WhatsApp uses - so the Ask page can do all of the assistant's jobs: orders, racks, leave, the CRM
reports, the sales bill, DC checks, complaints, CCTV, quality checks and everything the reckoner knows. If the assistant is not
running, `ask()` raises Unavailable and the server answers from the reckoner's own tools exactly as before.

    ask(question, token, mode)     -> {'answer', 'cost', 'files': [...]}      mode: None (typed), 'speak' (read aloud), 'voice' (live voice)
    fetch_file(file_token)         -> (bytes, mimetype, name)                  a file the assistant sent (a report, a picture ...)
    available()                    -> True when the assistant's chat door answers (checked at most every 15 s)

Safety: every Ask page conversation is a chat whose id starts with "ask-". The assistant treats those chats as the owner's, EXCEPT that
they can never make a bank payment, pay salaries, make a supplier voucher or run Claude Code (chat_sim.lock_down there, tested in its
test_chat_api.py). Payments stay on WhatsApp and the phone app. The door is the assistant's own panel API: it answers only a program on
this PC (Host 127.0.0.1:8030, header X-Panel); no key is needed or stored here.

Settings (.env): ASK_VIA_ASSISTANT (default 1; 0 = the old reckoner-only brain), ASSISTANT_CHAT_URL (default http://127.0.0.1:8030),
ASSISTANT_CHAT_WAIT_S (default 240: the longest one question may take - a CRM report needs up to 90 s).
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = 'http://127.0.0.1:8030'
REPLY_MARK = '*Subbii:* '          # the assistant starts every reply with this (WhatsApp's way of marking it)
CHAT_PREFIX = 'ask-'               # the assistant locks down chats with this prefix (no payments, no Claude Code)
POLL_S = 0.4
CHECK_EVERY_S = 15
FILE_TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{10,64}$')
MODES = ('speak', 'voice')


class Unavailable(Exception):
    """The assistant could not be reached BEFORE it took the question: the reckoner answers it itself."""


class Failed(Exception):
    """The assistant took the question but could not give the answer back (do not ask it again behind the owner's back)."""


_lock = threading.Lock()
_cursors = {}                      # chat id -> (epoch, last message number seen): messages that arrive after a turn ended come with the next one
_status = {'at': 0.0, 'ok': False}


def enabled():
    return os.environ.get('ASK_VIA_ASSISTANT', '1').strip().lower() not in ('0', 'false', 'no', 'off')


def base_url():
    return (os.environ.get('ASSISTANT_CHAT_URL') or DEFAULT_URL).strip().rstrip('/')


def wait_seconds():
    try:
        return max(10.0, float(os.environ.get('ASSISTANT_CHAT_WAIT_S', '240')))
    except ValueError:
        return 240.0


def chat_id(token):
    """One assistant chat per login session (its memory of the conversation, its open steps). The token itself never leaves this PC's memory."""
    return CHAT_PREFIX + hashlib.sha256(str(token or 'anon').encode()).hexdigest()[:20]


def _call(method, path, payload=None, timeout=10.0):
    """-> parsed JSON. Raises OSError-family errors as Unavailable, an HTTP error as Unavailable (with its message)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base_url() + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json', 'X-Panel': '1'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode('utf-8')).get('error') or ''
        except Exception:  # noqa: BLE001
            msg = ''
        raise Unavailable('the assistant answered %s %s' % (e.code, msg)) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Unavailable('the assistant is not reachable (%s)' % type(e).__name__) from None


def available(force=False):
    now = time.time()
    with _lock:
        if not force and now - _status['at'] < CHECK_EVERY_S:
            return _status['ok']
    ok = False
    try:
        door = _call('GET', '/api/chat/who', timeout=2.5)
        # Only an assistant that says it locks down "ask-" chats may be used: an older one would let the Ask page make payments.
        ok = bool(door.get('who')) and door.get('ask_lockdown') == CHAT_PREFIX
    except Unavailable:
        ok = False
    with _lock:
        _status.update(at=time.time(), ok=ok)
    return ok


def _plain(text):
    """For a reply that is READ ALOUD: no markup characters (the voice would say them)."""
    return re.sub(r'\s*\n\s*', '\n', text.replace('*', '').replace('_', ' ')).strip()


def _file_token(url):
    tail = str(url or '').rsplit('/', 1)[-1]
    return tail if FILE_TOKEN_RE.match(tail) else None


def _gather(msgs, spoken):
    """The chat's messages -> (the answer text, the files, the cost in US dollars)."""
    parts, files, usd = [], [], 0.0
    for m in msgs:
        kind = m.get('kind')
        if kind in ('text', 'note'):
            t = str(m.get('text') or '')
            if t.startswith(REPLY_MARK):
                t = t[len(REPLY_MARK):]
            t = t.strip()
            if t:
                parts.append(t)
            usd += float((m.get('meta') or {}).get('usd') or 0)
        elif kind in ('photo', 'video', 'file'):
            tok = _file_token(m.get('url'))
            if tok and not spoken:
                files.append({'token': tok, 'name': str(m.get('name') or 'file'), 'kind': kind,
                              'caption': str(m.get('caption') or ''), 'size': m.get('size')})
            elif tok:
                parts.append('I have a file for you: %s. Please ask again in the typed Ask page to open it.' % (m.get('name') or 'file'))
    text = '\n\n'.join(parts)
    return (_plain(text) if spoken else text), files, usd


def ask(question, token, mode=None):
    """Ask the Shop Assistant, as the owner, in this login's own chat; wait for its reply. See the module docstring."""
    mode = mode if mode in MODES else None
    chat = chat_id(token)
    if not enabled() or not available():
        raise Unavailable('the Shop Assistant is not available')
    sent = _call('POST', '/api/chat/send', {'message': question, 'chat': chat, 'as': 'owner', 'mode': mode}, timeout=15)
    epoch, seq = sent.get('epoch'), int(sent.get('seq') or 0)
    with _lock:
        had_epoch, had_after = _cursors.get(chat, (None, 0))
    after = had_after if had_epoch == epoch else seq        # a late file or reply from the last turn comes first
    msgs, deadline, quiet = [], time.time() + wait_seconds(), 0
    while time.time() < deadline:
        time.sleep(POLL_S)
        try:
            j = _call('GET', '/api/chat/inbox?' + urllib.parse.urlencode(
                {'chat': chat, 'as': 'owner', 'after': after, 'epoch': epoch or ''}), timeout=10)
        except Unavailable as e:
            raise Failed('the Shop Assistant took the question but went quiet (%s)' % e) from None
        if j.get('epoch') != epoch:
            raise Failed('the Shop Assistant was restarted while answering - please ask again')
        new = j.get('messages') or []
        msgs += new
        if new:
            after = max(after, int(new[-1].get('seq') or 0))
        if not j.get('busy'):
            quiet += 1
            if msgs or quiet >= 3:
                break
        else:
            quiet = 0
    else:
        with _lock:
            _cursors[chat] = (epoch, after)
        raise Failed('the Shop Assistant is still working on it - ask again in a minute to see the answer')
    with _lock:
        _cursors[chat] = (epoch, after)
    text, files, usd = _gather(msgs, mode is not None)
    if not text and not files:
        raise Failed('the Shop Assistant gave no answer')
    return {'answer': text or '(see the file below)', 'cost': usd, 'files': files}


def fetch_file(file_token):
    """A file the assistant sent into an Ask page chat -> (bytes, mimetype, name). KeyError: not a valid token / no longer kept."""
    if not FILE_TOKEN_RE.match(str(file_token or '')):
        raise KeyError(file_token)
    req = urllib.request.Request(base_url() + '/api/chat/file/' + file_token, headers={'X-Panel': '1'})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            mime = resp.headers.get_content_type() or 'application/octet-stream'
            disp = resp.headers.get('Content-Disposition') or ''
    except urllib.error.HTTPError:
        raise KeyError(file_token) from None
    except (urllib.error.URLError, OSError) as e:
        raise Unavailable('the assistant is not reachable (%s)' % type(e).__name__) from None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disp)
    return body, mime, urllib.parse.unquote(m.group(1)) if m else 'file'


def reset_for_tests():
    with _lock:
        _cursors.clear()
        _status.update(at=0.0, ok=False)

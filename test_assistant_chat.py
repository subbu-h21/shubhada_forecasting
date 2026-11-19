"""Offline tests for the Ask page's link to the Shop Assistant (assistant_chat.py + /api/ask, /api/ask/status, /api/ask/file in server.py).

A stub "Shop Assistant" (a tiny HTTP server in a thread) plays its chat door (/api/chat/who, send, inbox, file). No model is called and no
real assistant is touched.

Run with the reckoner's venv:
    venv/Scripts/python.exe test_assistant_chat.py
"""
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('OPENROUTER_API_KEY', 'sk-test-not-real')
os.environ['WHATSAPP_ASK_ENABLED'] = '0'

import server  # noqa: E402
import assistant_chat as AC  # noqa: E402

FAILS = []
MARK = AC.REPLY_MARK


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'   <-- {detail}'))
    if not cond:
        FAILS.append(name)


# --- the stub assistant ------------------------------------------------------------------------------------------------
S = {'epoch': 'e1', 'seq': 0, 'out': [], 'busy_polls': 0, 'sent': [], 'headers': [], 'reply': None, 'files': {}, 'who_ok': True}


def reset_stub():
    S.update(epoch='e1', busy_polls=0, sent=[], headers=[], reply=None, who_ok=True)      # numbers only go up, as in the real chat


def post_msg(entry):
    S['seq'] += 1
    S['out'].append(dict(entry, seq=S['seq'], at=int(time.time() * 1000)))


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == '/api/chat/who':
            if S.get('old'):                                   # an assistant from before the lock-down existed
                return self._json(200, {'who': [{'id': 'owner'}]})
            return self._json(200, {'who': [{'id': 'owner'}], 'ask_lockdown': 'ask-'}) if S['who_ok'] else self._json(503, {'error': 'off'})
        if u.path == '/api/chat/inbox':
            after = int(q.get('after', ['0'])[0])
            if S['busy_polls'] > 0:
                S['busy_polls'] -= 1
                busy = True
                if S['busy_polls'] == 0 and S['reply']:
                    for m in S['reply']:
                        post_msg(m)
                    busy = False
            else:
                busy = False
            return self._json(200, {'epoch': S['epoch'], 'busy': busy, 'messages': [m for m in S['out'] if m['seq'] > after]})
        if u.path.startswith('/api/chat/file/'):
            tok = u.path.rsplit('/', 1)[-1]
            if tok not in S['files']:
                return self._json(404, {'error': 'gone'})
            body, mime, name = S['files'][tok]
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Disposition', 'attachment; filename="%s"' % name)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(404, {'error': 'no'})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or '{}')
        S['headers'].append(dict(self.headers))
        if self.path == '/api/chat/send':
            S['sent'].append(body)
            seq = S['seq']
            S['busy_polls'] = 2
            return self._json(200, {'epoch': S['epoch'], 'seq': seq, 'busy': True})
        self._json(404, {'error': 'no'})


httpd = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
URL = 'http://127.0.0.1:%d' % httpd.server_address[1]
os.environ['ASSISTANT_CHAT_URL'] = URL
os.environ.pop('ASK_VIA_ASSISTANT', None)
AC.POLL_S = 0.01
AC.CHECK_EVERY_S = 0
FILE_TOKEN = 'FILETOKEN1234567890'
S['files'][FILE_TOKEN] = (b'%PDF-fake', 'application/pdf', 'GST October.pdf')

# --- chat ids -----------------------------------------------------------------------------------------------------------
a, b = AC.chat_id('tok-1'), AC.chat_id('tok-2')
check('chat id starts with "ask-" (the assistant locks those chats down) and is short', a.startswith('ask-') and len(a) <= 40, a)
check('...is the same for one login and differs between logins', a == AC.chat_id('tok-1') and a != b)
check('...and never contains the login token', 'tok-1' not in a)

# --- a typed question -----------------------------------------------------------------------------------------------------
reset_stub()
S['reply'] = [{'kind': 'text', 'text': MARK + '*Dolo 650* is in A-RACK2.', 'meta': {'usd': 0.004}}]
out = AC.ask('where is dolo?', 'tok-1')
check('typed: the answer is the reply without the *Subbii:* mark', out['answer'] == '*Dolo 650* is in A-RACK2.', out)
check('typed: the cost comes from the reply', abs(out['cost'] - 0.004) < 1e-9, out['cost'])
sent = S['sent'][-1]
check('typed: sent as the owner in this login\'s ask- chat, no mode', sent['as'] == 'owner' and sent['chat'] == a and sent['mode'] is None and sent['message'] == 'where is dolo?', sent)
check('typed: carries the panel header the assistant\'s guard wants', S['headers'][-1].get('X-Panel') == '1', S['headers'][-1])

# --- spoken modes ----------------------------------------------------------------------------------------------------------
reset_stub()
S['reply'] = [{'kind': 'text', 'text': MARK + '*Sales* today are _good_ - ten thousand.\n\nAnd more.'}]
out = AC.ask('sales?', 'tok-1', mode='speak')
check('speak: asterisks and underscores are removed (a voice would say them)', '*' not in out['answer'] and '_' not in out['answer'], out['answer'])
check('speak: mode reaches the assistant', S['sent'][-1]['mode'] == 'speak', S['sent'][-1])
reset_stub()
S['reply'] = [{'kind': 'text', 'text': MARK + 'ok'}]
AC.ask('sales?', 'tok-1', mode='voice')
check('voice: mode reaches the assistant', S['sent'][-1]['mode'] == 'voice')
reset_stub()
S['reply'] = [{'kind': 'text', 'text': MARK + 'ok'}]
AC.ask('sales?', 'tok-1', mode='evil')
check('an unknown mode is dropped', S['sent'][-1]['mode'] is None, S['sent'][-1])

# --- files ---------------------------------------------------------------------------------------------------------------
reset_stub()
S['reply'] = [{'kind': 'text', 'text': MARK + 'Here is the GST report.'},
              {'kind': 'file', 'url': '/api/chat/file/' + FILE_TOKEN, 'name': 'GST October.pdf', 'caption': 'GST', 'size': 9}]
out = AC.ask('october gst', 'tok-1')
check('a file the assistant sent comes back as a file entry with its token', out['files'] == [{'token': FILE_TOKEN, 'name': 'GST October.pdf', 'kind': 'file', 'caption': 'GST', 'size': 9}], out['files'])
reset_stub()
S['reply'] = [{'kind': 'text', 'text': MARK + 'Here it is.'}, {'kind': 'file', 'url': '/api/chat/file/' + FILE_TOKEN, 'name': 'GST October.pdf'}]
out = AC.ask('october gst', 'tok-1', mode='speak')
check('by voice a file is never offered as a button (it is mentioned in words)', out['files'] == [] and 'GST October.pdf' in out['answer'], out)
reset_stub()
S['reply'] = [{'kind': 'text', 'text': MARK + 'x', 'meta': {}}, {'kind': 'file', 'url': '/etc/passwd', 'name': 'bad'}]
out = AC.ask('q', 'tok-1')
check('a file url that is not a plain token is ignored', out['files'] == [], out['files'])

# --- a message that arrives after the turn ended shows up with the next question ------------------------------------------------
reset_stub()
S['reply'] = [{'kind': 'text', 'text': MARK + 'First answer.'}]
AC.ask('one', 'tok-late')
post_msg({'kind': 'text', 'text': MARK + 'Your report is ready.'})        # a late delivery, after the turn
S['reply'] = [{'kind': 'text', 'text': MARK + 'Second answer.'}]
out = AC.ask('two', 'tok-late')
check('a late message from the earlier turn comes first, then the new answer', out['answer'] == 'Your report is ready.\n\nSecond answer.', out['answer'])
S['reply'] = [{'kind': 'text', 'text': MARK + 'Third answer.'}]
out = AC.ask('three', 'tok-late')
check('...and is not repeated', out['answer'] == 'Third answer.', out['answer'])

# --- an assistant that does not lock down ask- chats is never used ------------------------------------------------------------
reset_stub()
S['old'] = True
n_sent = len(S['sent'])
try:
    AC.ask('pay Ramesh 5000', 'tok-1')
    check('an older assistant without the lock-down is refused (nothing sent)', False, 'no exception')
except AC.Unavailable:
    check('an older assistant without the lock-down is refused (nothing sent)', len(S['sent']) == n_sent)
S['old'] = False

# --- failures -------------------------------------------------------------------------------------------------------------------
reset_stub()
S['who_ok'] = False
try:
    AC.ask('q', 'tok-1')
    check('assistant answers 503 on its door -> Unavailable', False, 'no exception')
except AC.Unavailable:
    check('assistant answers 503 on its door -> Unavailable (nothing sent)', S['sent'] == [])
S['who_ok'] = True
os.environ['ASK_VIA_ASSISTANT'] = '0'
try:
    AC.ask('q', 'tok-1')
    check('ASK_VIA_ASSISTANT=0 -> Unavailable', False, 'no exception')
except AC.Unavailable:
    check('ASK_VIA_ASSISTANT=0 -> Unavailable', True)
os.environ.pop('ASK_VIA_ASSISTANT')
os.environ['ASSISTANT_CHAT_URL'] = 'http://127.0.0.1:1'
try:
    AC.ask('q', 'tok-1')
    check('nothing listening -> Unavailable', False, 'no exception')
except AC.Unavailable:
    check('nothing listening -> Unavailable', True)
os.environ['ASSISTANT_CHAT_URL'] = URL

reset_stub()
S['reply'] = [{'kind': 'text', 'text': MARK + 'never'}]
real_inbox_epoch = S['epoch']


class Restart(AC.Failed):
    pass


# the assistant restarts between send and answer: its epoch changes
orig_call = AC._call


def call_with_restart(method, path, payload=None, timeout=10.0):
    r = orig_call(method, path, payload, timeout)
    if path.startswith('/api/chat/inbox'):
        r['epoch'] = 'e2'
    return r


AC._call = call_with_restart
try:
    AC.ask('q', 'tok-restart')
    check('assistant restarted mid-answer -> Failed (never silently asked again)', False, 'no exception')
except AC.Failed:
    check('assistant restarted mid-answer -> Failed (never silently asked again)', True)
AC._call = orig_call

reset_stub()
S['reply'] = None
S['busy_polls'] = 0
AC.wait_seconds = lambda: 0.3
S_send = S['sent']
try:
    # the stub stays "busy" forever: patch the inbox to always say busy
    def always_busy(method, path, payload=None, timeout=10.0):
        r = orig_call(method, path, payload, timeout)
        if path.startswith('/api/chat/inbox'):
            r['busy'] = True
        return r
    AC._call = always_busy
    AC.ask('slow', 'tok-slow')
    check('a question that takes too long -> Failed with a plain message', False, 'no exception')
except AC.Failed as e:
    check('a question that takes too long -> Failed with a plain message', 'still working' in str(e), str(e))
AC._call = orig_call
AC.wait_seconds = lambda: 240.0

# --- the server routes ----------------------------------------------------------------------------------------------------------
cl = server.app.test_client()
TOKEN = server._create_session('test-user')
H = {'X-Session-Token': TOKEN, 'Content-Type': 'application/json'}
asked_locally = []
server.ask_mod.ask = lambda question, **kw: (asked_locally.append(question), 'LOCAL ANSWER')[1]
import ask_limits  # noqa: E402
ask_limits.reset_for_tests()


def api_ask(body):
    return cl.post('/api/ask', data=json.dumps(body), headers=H)


reset_stub()
S['reply'] = [{'kind': 'text', 'text': MARK + 'From the assistant.', 'meta': {'usd': 0.002}}]
r = api_ask({'question': 'where is dolo?'})
j = r.get_json()
check('/api/ask: answered by the Shop Assistant', r.status_code == 200 and j['answer'] == 'From the assistant.' and j['source'] == 'assistant' and asked_locally == [], (r.status_code, j))
check('/api/ask: the chat belongs to this login only', S['sent'][-1]['chat'] == AC.chat_id(TOKEN), S['sent'][-1])
S['reply'] = [{'kind': 'text', 'text': MARK + 'spoken'}]
api_ask({'question': 'sales?', 'speak': True})
check('/api/ask: push-to-talk -> mode speak', S['sent'][-1]['mode'] == 'speak', S['sent'][-1])
api_ask({'question': 'sales?', 'voice': True})
check('/api/ask: live voice -> mode voice', S['sent'][-1]['mode'] == 'voice', S['sent'][-1])
n = len(S['sent'])
r = api_ask({'question': 'subbiii'})
check('/api/ask: just the name is answered at once, never sent to the assistant', r.get_json()['answer'] == server.ask_mod.GREETING_EN and len(S['sent']) == n, r.get_json())

S['who_ok'] = False
r = api_ask({'question': 'where is dolo?'})
j = r.get_json()
check('/api/ask: assistant off -> the reckoner answers itself, as before', r.status_code == 200 and j['answer'] == 'LOCAL ANSWER' and j['source'] == 'reckoner', (r.status_code, j))
S['who_ok'] = True

reset_stub()
S['reply'] = [{'kind': 'text', 'text': MARK + 'ok'}]
AC.wait_seconds = lambda: 0.3
orig_call2 = AC._call


def broken_inbox(method, path, payload=None, timeout=10.0):
    if path.startswith('/api/chat/inbox'):
        raise AC.Unavailable('went quiet')
    return orig_call2(method, path, payload, timeout)


AC._call = broken_inbox
asked_locally.clear()
r = api_ask({'question': 'pay Ramesh 5000'})
check('/api/ask: assistant took the question then failed -> an error, NOT a second answer from elsewhere', r.status_code == 502 and asked_locally == [], (r.status_code, r.get_json()))
AC._call = orig_call2
AC.wait_seconds = lambda: 240.0

r = cl.get('/api/ask/status', headers=H)
check('/api/ask/status: says the assistant is the brain', r.get_json()['brain'] == 'assistant', r.get_json())
S['who_ok'] = False
check('/api/ask/status: says the reckoner when the assistant is off', cl.get('/api/ask/status', headers=H).get_json()['brain'] == 'reckoner')
S['who_ok'] = True

r = cl.get('/api/ask/file/' + FILE_TOKEN, headers=H)
check('/api/ask/file: passes a file through with its name', r.status_code == 200 and r.data == b'%PDF-fake' and 'GST October.pdf' in r.headers['Content-Disposition'], (r.status_code, r.headers))
check('/api/ask/file: a PDF opens inline, never sniffed', r.headers['X-Content-Type-Options'] == 'nosniff' and r.headers['Content-Disposition'].startswith('inline'))
check('/api/ask/file: a token the assistant does not know -> 404', cl.get('/api/ask/file/NOTAREALTOKEN12345', headers=H).status_code == 404)
check('/api/ask/file: a malformed token -> 404', cl.get('/api/ask/file/..%2f..%2fetc', headers=H).status_code in (404,))
check('/api/ask/file: no login -> 401', cl.get('/api/ask/file/' + FILE_TOKEN).status_code == 401)
check('/api/ask/status: no login -> 401', cl.get('/api/ask/status').status_code == 401)

httpd.shutdown()
print()
print('%d failed' % len(FAILS) if FAILS else 'ALL PASSED')
sys.exit(1 if FAILS else 0)

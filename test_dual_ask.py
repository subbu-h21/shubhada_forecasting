"""Offline tests for dual-mode Ask (order_bridge.py, ask.py's merge, ask_limits.py).

No network beyond 127.0.0.1, no model call, no real data:
  * a stub "order dashboard" (a tiny HTTP server in a thread) plays the /ask-bridge/* routes;
  * the model is a scripted fake (openai.OpenAI is replaced);
  * the daily counter file points at a temp folder.

Run with the reckoner's venv:
    venv/Scripts/python.exe test_dual_ask.py
"""
import copy
import datetime
import json
import os
import sys
import tempfile
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
KEY = 'k' * 40
os.environ['OPENROUTER_API_KEY'] = 'sk-test-not-real'
for v in ('ORDER_ASK_URL', 'ORDER_ASK_KEY', 'ASK_DAILY_LIMIT_QUESTIONS', 'ASK_PER_10_MIN'):
    os.environ.pop(v, None)

import ask  # noqa: E402
import ask_limits  # noqa: E402
import order_bridge  # noqa: E402

print('testing:', ROOT)
print('  ask from', ask.__file__)
FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'   <-- {detail}'))
    if not cond:
        FAILS.append(name)


# --- the stub order dashboard -----------------------------------------------------------
STUB = {'mode': 'ok', 'calls': [], 'tools_hits': 0}
STUB_TOOLS = [
    {'name': 'find_order_lines', 'description': 'Look up order lines.',
     'parameters': {'type': 'object', 'properties': {'q': {'type': 'string'}}, 'required': []}},
    {'name': 'get_held_carts', 'description': 'Lines waiting in carts.',
     'parameters': {'type': 'object', 'properties': {}, 'required': []}},
]
STUB_CONTEXT = 'What you know about the business\n- OVERDUE means placed and late. (stub context)\n'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorised(self):
        if STUB['mode'] == '401' or self.headers.get('X-Ask-Bridge-Key') != KEY:
            self._send(401, {'detail': 'Unauthorized'})
            return False
        return True

    def do_GET(self):
        if not self._authorised():
            return
        STUB['tools_hits'] += 1
        self._send(200, {'tools': STUB_TOOLS, 'context': STUB_CONTEXT})

    def do_POST(self):
        if not self._authorised():
            return
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        STUB['calls'].append(body)
        if STUB['mode'] == '429':
            self._send(429, {'detail': 'too_many_requests:60'})
        elif STUB['mode'] == 'garbage':
            self._send(200, {'result': 'not a dict'})
        else:
            self._send(200, {'result': {'total_lines': 1, 'rows': [{'product': 'DOLO 650 TAB', 'party': 'DRK ENTERPRISES'}]}})


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
URL = f'http://127.0.0.1:{server.server_address[1]}'


def dual_on():
    os.environ['ORDER_ASK_URL'] = URL
    os.environ['ORDER_ASK_KEY'] = KEY
    order_bridge.reset_cache()


def dual_off():
    os.environ.pop('ORDER_ASK_URL', None)
    os.environ.pop('ORDER_ASK_KEY', None)
    order_bridge.reset_cache()


# --- 1. off by default -------------------------------------------------------------------
dual_off()
names = {t['name'] for t in ask._available_tools()}
check('dual mode off: no orders_ tools offered', not any(n.startswith('orders_') for n in names))
check('dual mode off: the reckoner tools are all still there', {t['name'] for t in ask.T.TOOLS} <= names)
check('dual mode off: prompt has no DUAL MODE section', 'DUAL MODE' not in ask._build_system_prompt())
check('dual mode off: a short key counts as off', (os.environ.__setitem__('ORDER_ASK_URL', URL), os.environ.__setitem__('ORDER_ASK_KEY', 'short'),
                                                    order_bridge.reset_cache(), order_bridge.tool_specs() == [])[-1])
dual_off()

# --- 2. on --------------------------------------------------------------------------------
dual_on()
tools = ask._available_tools()
names = {t['name'] for t in tools}
check('dual mode on: tools are offered under an orders_ prefix', {'orders_find_order_lines', 'orders_get_held_carts'} <= names, names)
check('dual mode on: the unprefixed name is not offered', 'find_order_lines' not in names)
check('dual mode on: reckoner tools still offered', {t['name'] for t in ask.T.TOOLS} <= names)
spec = next(t for t in tools if t['name'] == 'orders_find_order_lines')
check('order tool description is marked and keeps its schema', spec['description'].startswith('[ORDER TRACKING]') and spec['parameters']['properties']['q'] == {'type': 'string'}, spec)
prompt = ask._build_system_prompt()
check('dual mode on: prompt has the DUAL MODE section', 'DUAL MODE' in prompt and 'orders_' in prompt)
check("dual mode on: prompt carries the order system's own facts", 'OVERDUE means placed and late' in prompt)
check('the original reckoner prompt is untouched, right after the identity block', prompt.startswith(ask.identity_prompt() + ask.SYSTEM_PROMPT))
before = STUB['tools_hits']
for _ in range(5):
    ask._available_tools()
check('the tool list is cached (no refetch per call)', STUB['tools_hits'] == before, (STUB['tools_hits'], before))

# --- 3. voice mode ------------------------------------------------------------------------
vnames = {t['name'] for t in ask._available_tools(voice=True)}
check('voice mode keeps the order tools (no patient data in them)', 'orders_find_order_lines' in vnames)
check('voice mode still withholds the patient-identity tools', not (vnames & set(ask.T.PII_ALLOWED_TOOLS)), vnames & set(ask.T.PII_ALLOWED_TOOLS))

# --- 4. dispatch --------------------------------------------------------------------------
allowed = {t['name'] for t in ask._available_tools()}
STUB['calls'].clear()
res = ask._run_allowed_tool('orders_find_order_lines', {'q': 'dolo'}, allowed)
check('an orders_ call reaches the bridge with the PREFIX STRIPPED', STUB['calls'] == [{'name': 'find_order_lines', 'args': {'q': 'dolo'}}], STUB['calls'])
check('and returns the bridge result', res['total_lines'] == 1 and res['rows'][0]['party'] == 'DRK ENTERPRISES', res)

seen = []
real_run = ask.T.run_tool
ask.T.run_tool = lambda name, args: (seen.append((name, args)), {'ok': True})[1]
try:
    out = ask._run_allowed_tool('get_overview', {}, allowed)
finally:
    ask.T.run_tool = real_run
check('a reckoner tool still goes to the reckoner, not the bridge', seen == [('get_overview', {})] and out == {'ok': True} and not STUB['calls'][1:], (seen, STUB['calls']))
check('a name that was never offered is refused', 'not available' in ask._run_allowed_tool('orders_delete_everything', {}, allowed).get('error', ''))
check('args that are not a dict are tolerated', 'error' not in ask._run_allowed_tool('orders_get_held_carts', 'oops', allowed))

# --- 5. errors come back as something the model can explain -----------------------------
STUB['mode'] = '429'
check('bridge 429 -> a rate-limit message', 'rate-limiting' in ask.order_bridge.run('orders_get_held_carts', {}).get('error', ''))
STUB['mode'] = 'garbage'
check('a malformed bridge reply -> an error dict, no crash', 'error' in ask.order_bridge.run('orders_get_held_carts', {}))
STUB['mode'] = 'ok'
os.environ['ORDER_ASK_KEY'] = 'z' * 40     # wrong key, same length
check('a wrong key on a call -> "refused", and the key itself is not echoed', (lambda r: 'refused' in r.get('error', '') and ('z' * 10) not in json.dumps(r))(order_bridge.run('orders_get_held_carts', {})))
dual_on()

# --- 6. soft failure: wrong key / dashboard down -> just no order tools, never an exception -------
os.environ['ORDER_ASK_KEY'] = 'z' * 40
order_bridge.reset_cache()
check('wrong key: no order tools, no exception', not any(t['name'].startswith('orders_') for t in ask._available_tools()))
check('wrong key: prompt falls back to the plain one', 'DUAL MODE' not in ask._build_system_prompt())
dual_on()
os.environ['ORDER_ASK_URL'] = 'http://127.0.0.1:1'      # nothing listens there
order_bridge.reset_cache()
check('dashboard unreachable: no order tools, no exception', not any(t['name'].startswith('orders_') for t in ask._available_tools()))
check('dashboard unreachable: a call still returns an error dict', 'not reachable' in order_bridge.run('orders_get_held_carts', {}).get('error', ''))
dual_on()

# --- 7. the whole loop with a scripted model ---------------------------------------------------
import openai  # noqa: E402


class FakeCompletions:
    def __init__(self, script):
        self.script, self.requests = list(script), []

    def create(self, **kw):
        self.requests.append(copy.deepcopy(kw))
        return self.script.pop(0)


def reply(content=None, calls=None):
    tcs = None
    if calls:
        tcs = [types.SimpleNamespace(id=f'c{i}', function=types.SimpleNamespace(name=n, arguments=json.dumps(a)))
               for i, (n, a) in enumerate(calls)]
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content, tool_calls=tcs))])


def run_with(script, question='where is my dolo order?', **kw):
    fake = FakeCompletions(script)
    openai.OpenAI = lambda **k: types.SimpleNamespace(chat=types.SimpleNamespace(completions=fake))
    return ask.ask(question, **kw), fake


STUB['calls'].clear()
answer, fake = run_with([reply(calls=[('orders_find_order_lines', {'q': 'dolo'})]), reply('(ready)'), reply('Dolo 650 went to DRK.')])
check('end to end: the final answer is returned', answer == 'Dolo 650 went to DRK.', answer)
check('end to end: the order tool was called through the bridge', STUB['calls'] == [{'name': 'find_order_lines', 'args': {'q': 'dolo'}}], STUB['calls'])
tool_msg = next(m for m in fake.requests[1]['messages'] if m['role'] == 'tool')
check("end to end: the bridge's result reached the model", 'DRK ENTERPRISES' in tool_msg['content'], tool_msg)
first = fake.requests[0]
check('end to end: the model was OFFERED both systems\' tools', {'orders_find_order_lines', 'get_overview'} <= {t['function']['name'] for t in first['tools']})
check('end to end: its system prompt told it about both systems', 'DUAL MODE' in first['messages'][0]['content'])

# a question needing both systems: two tool calls, one to each side
STUB['calls'].clear()
seen = []
ask.T.run_tool = lambda name, args: (seen.append(name), {'sold': 40})[1]
try:
    answer, fake = run_with([reply(calls=[('orders_find_order_lines', {'q': 'dolo'}), ('get_overview', {})]), reply('(ready)'),
                             reply('Ordered 10 (order tracking), sold 40 (sales history).')])
finally:
    ask.T.run_tool = real_run
check('a mixed question can use both systems in one turn', len(STUB['calls']) == 1 and seen == ['get_overview'], (STUB['calls'], seen))

# the order dashboard goes down WHILE the tool list is cached (the realistic outage): the model is
# still offered the tools, the call fails soft with an error it can explain, and the page still answers
dual_on()
ask._available_tools()                                   # warm the cache while the dashboard is up
STUB['calls'].clear()
os.environ['ORDER_ASK_URL'] = 'http://127.0.0.1:1'       # ...now nothing listens
answer, fake = run_with([reply(calls=[('orders_find_order_lines', {'q': 'dolo'})]), reply('(ready)'), reply('Order tracking is offline right now.')])
tool_content = next(m for m in fake.requests[1]['messages'] if m['role'] == 'tool')['content']
check('mid-outage: the cached tool list still offers the order tools',
      any(t['function']['name'].startswith('orders_') for t in fake.requests[0]['tools']))
check('mid-outage: the call returns an explainable error, not a crash', 'not reachable' in tool_content, tool_content)
check('mid-outage: the page still returns an answer', answer == 'Order tracking is offline right now.', answer)
check('mid-outage: nothing reached the (now unreachable) bridge', STUB['calls'] == [])
dual_on()

# spoken mode keeps working end to end
answer, fake = run_with([reply(calls=[('orders_get_held_carts', {})]), reply('(ready)'), reply('Two carts are waiting.')], speak=True)
check('spoken mode works with order tools', answer == 'Two carts are waiting.', answer)
check('spoken mode: the speakable-answer rules and the dual section are both in the prompt',
      'DUAL MODE' in fake.requests[0]['messages'][0]['content'] and 'read' in fake.requests[0]['messages'][0]['content'].lower())

# --- 7b. subbi's identity (owner's instruction 2026-10-08: remember it always) ---------------
# The family facts are private and this repo is public, so they live in the gitignored
# owner_facts.txt; the test points ask at a stand-in file with made-up names.
_real_facts_path = ask.OWNER_FACTS_PATH
ask.OWNER_FACTS_PATH = Path(tempfile.mkdtemp(prefix='owner_facts_')) / 'owner_facts.txt'
ask.OWNER_FACTS_PATH.write_text('# stand-in facts for the test\n'
                                "Your boss is Test Owner. Test Owner's wife is Test Spouse, and Test Owner's son is Test Son.\n",
                                encoding='utf-8')
NAMES = ('subbi', 'Test Owner', 'Test Spouse', 'Test Son')
for label, kw, on in (('typed, dual mode off', {}, False), ('typed, dual mode on', {}, True),
                      ('voice', {'voice': True}, True), ('spoken (push-to-talk)', {'speak': True}, True)):
    (dual_on if on else dual_off)()
    p = ask._build_system_prompt(**kw)
    check(f'identity + family facts are in the prompt: {label}', all(n in p for n in NAMES), [n for n in NAMES if n not in p])
    check(f'...and come FIRST: {label}', p.startswith('Who you are and who you work for'))
dual_on()
p = ' '.join(ask._build_system_prompt().split())     # whitespace-normalised, so line wrapping can't matter
check('the family facts are stated as given, from the private file',
      "Your boss is Test Owner. Test Owner's wife is Test Spouse, and Test Owner's son is Test Son." in p, p[:300])
check('comment lines in the facts file are not sent to the model', 'stand-in facts' not in p)
check('it does not guess who is asking', 'You do not know which of these people is asking' in p)
check('it defers to the identify_person lookup rule (the names may also be employees/customers)',
      'They do NOT replace the lookup rule below' in p and 'still call identify_person first' in p)
check('the rule it defers to is still in the prompt', 'call identify_person FIRST' in p)
answer, fake = run_with([reply('(ready)'), reply('Hello, I am subbi.')])
check('the identity reaches the model on a real turn', 'Your name is subbi' in fake.requests[0]['messages'][0]['content'])
ask.OWNER_FACTS_PATH.unlink()
p = ask._build_system_prompt()
check('without the private file the name is still there, but no family facts and no rules about them',
      'Your name is subbi' in p and 'Test Owner' not in p and 'these people' not in p)
ask.OWNER_FACTS_PATH = _real_facts_path

# --- 8. the limiter ----------------------------------------------------------------------------
ask_limits._PATH = Path(tempfile.mkdtemp(prefix='ask_limits_')) / 'ask_daily.json'
os.environ['ASK_DAILY_LIMIT_QUESTIONS'] = '3'
os.environ['ASK_PER_10_MIN'] = '0'
ask_limits.reset_for_tests()
day = datetime.date(2026, 10, 8)
for _ in range(3):
    ask_limits.check(day)
try:
    ask_limits.check(day)
    check('daily cap: the 4th question is refused', False, 'no exception')
except ask_limits.LimitReached as e:
    check('daily cap: the 4th question is refused, with a readable message', 'limit of 3' in str(e), str(e))
try:
    ask_limits.check(day + datetime.timedelta(days=1))
    check('daily cap resets on a new day', True)
except ask_limits.LimitReached:
    check('daily cap resets on a new day', False)
ask_limits.check(day + datetime.timedelta(days=1))   # 2nd of the new day: still fine
ask_limits._PATH.write_text('{not json', encoding='utf-8')
try:
    ask_limits.check(day + datetime.timedelta(days=1))
    check('a corrupt counter file never blocks the owner', True)
except Exception as e:  # noqa: BLE001
    check('a corrupt counter file never blocks the owner', False, repr(e))

os.environ['ASK_DAILY_LIMIT_QUESTIONS'] = '0'
os.environ['ASK_PER_10_MIN'] = '2'
ask_limits.reset_for_tests()
ask_limits.check(day)
ask_limits.check(day)
try:
    ask_limits.check(day)
    check('burst cap: the 3rd question inside 10 minutes is refused', False, 'no exception')
except ask_limits.LimitReached as e:
    check('burst cap: the 3rd question inside 10 minutes is refused', 'short time' in str(e), str(e))
os.environ['ASK_PER_10_MIN'] = '0'
ask_limits.reset_for_tests()
for _ in range(30):
    ask_limits.check(day)
check('a limit of 0 switches that limit off', True)
os.environ['ASK_DAILY_LIMIT_QUESTIONS'] = 'banana'
try:
    ask_limits.check(day)
    check('a garbage setting falls back to the default instead of crashing', True)
except Exception as e:  # noqa: BLE001
    check('a garbage setting falls back to the default instead of crashing', False, repr(e))

server.shutdown()
print()
print(f'{len(FAILS)} FAILED: {FAILS}' if FAILS else 'ALL PASSED')
sys.exit(1 if FAILS else 0)

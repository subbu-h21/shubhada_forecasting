"""Offline tests for the Shop Assistant's connector (assistant_bridge.py + the /api/assistant/* routes).

Run with the reckoner's venv:
    venv/Scripts/python.exe test_assistant_bridge.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('OPENROUTER_API_KEY', 'sk-test-not-real')
os.environ['WHATSAPP_ASK_ENABLED'] = '0'

import server  # noqa: E402
import assistant_bridge  # noqa: E402

KEY = 'k' * 40
os.environ['ASSISTANT_BRIDGE_KEY'] = KEY          # after the import: server loaded .env with setdefault
FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'   <-- {detail}'))
    if not cond:
        FAILS.append(name)


cl = server.app.test_client()
LOCAL = {'REMOTE_ADDR': '127.0.0.1'}
GOOD = {'X-Assistant-Key': KEY}


def get(path, headers=None, addr='127.0.0.1'):
    return cl.get(path, headers=headers or {}, environ_base={'REMOTE_ADDR': addr})


def post(path, body, headers=None, addr='127.0.0.1'):
    return cl.post(path, data=json.dumps(body), headers=dict(headers or {}, **{'Content-Type': 'application/json'}),
                   environ_base={'REMOTE_ADDR': addr})


# --- the gate -------------------------------------------------------------------------------
check('no key sent -> refused', get('/api/assistant/ping').status_code == 403)
check('a wrong key -> refused', get('/api/assistant/ping', {'X-Assistant-Key': 'z' * 40}).status_code == 403)
r = get('/api/assistant/ping', GOOD, addr='192.168.1.20')
check('the right key from ANOTHER computer -> refused (this PC only)', r.status_code == 403 and 'this PC' in r.get_json()['error'], r.get_json())
r = get('/api/assistant/ping', GOOD)
check('the right key from this PC -> ok', r.status_code == 200 and r.get_json()['ok'] is True, r.get_json())
saved = os.environ.pop('ASSISTANT_BRIDGE_KEY')
r = get('/api/assistant/ping', GOOD)
check('no key set in .env -> the bridge is off (503) and says why', r.status_code == 503 and 'ASSISTANT_BRIDGE_KEY' in r.get_json()['error'])
os.environ['ASSISTANT_BRIDGE_KEY'] = 'short'
check('a key shorter than 32 characters counts as not set', get('/api/assistant/ping', {'X-Assistant-Key': 'short'}).status_code == 503)
os.environ['ASSISTANT_BRIDGE_KEY'] = saved
for path in ('/api/assistant/tools', '/api/assistant/context'):
    check(f'{path}: refused without the key', get(path).status_code == 403)
check('/api/assistant/run: refused without the key', post('/api/assistant/run', {'name': 'get_overview'}).status_code == 403)
check('/api/assistant/basket: refused without the key', post('/api/assistant/basket', {'action': 'classify', 'owner': 'x', 'text': 'calculate'}).status_code == 403)

# --- what it offers ---------------------------------------------------------------------------
d = get('/api/assistant/tools', GOOD).get_json()
names = {t['name'] for t in d['tools']}
check('tools: every reckoner Ask tool is offered', {t['name'] for t in server.ask_mod.T.TOOLS} <= names)
check('tools: the basket tools are offered too', set(d['basket_tools']) <= names and d['basket_tools'])
check('tools: the patient-data tools are named, so the assistant can withhold them', set(d['pii_tools']) == set(server.ask_mod.T.PII_ALLOWED_TOOLS))
check('tools: each is in the function-call shape (name, description, parameters)',
      all({'name', 'description', 'parameters'} <= set(t) for t in d['tools']))
check('tools: the other systems\' tools are NOT here (the assistant reaches those itself)',
      not any(n.startswith(('orders_', 'rack_', 'payments_', 'crm_')) for n in names))
ctx = get('/api/assistant/context', GOOD).get_json()
p = ctx['prompt']
check('context: carries the reckoner\'s own rules and today\'s date line', 'MRP TOTAL' in p and 'Today is' in p)
check('context: no identity block (the assistant has its own)', 'Your name is suubbiii' not in p)
check('context: the saved notes come separately (the assistant puts them last, as the Ask brain did)',
      'Notes saved' not in p and (ctx['notes'] == '' or ctx['notes'].startswith('\n\nNotes saved from previous conversations')))
check('context: prompt + connector sections + notes rebuilds the Ask brain\'s own text',
      server.ask_mod._build_system_prompt().startswith(server.ask_mod.identity_prompt() + p) and server.ask_mod._build_system_prompt().endswith(ctx['notes']))

# --- running tools ------------------------------------------------------------------------------
seen = []
real = server.ask_mod.T.run_tool
server.ask_mod.T.run_tool = lambda name, args: (seen.append((name, args)), {'ok': True, 'rows': [1]})[1]
try:
    r = post('/api/assistant/run', {'name': 'get_overview', 'args': {}}, GOOD)
    check('run: a reckoner tool runs and its result comes back', r.get_json() == {'ok': True, 'rows': [1]} and seen == [('get_overview', {})], (r.get_json(), seen))
    r = post('/api/assistant/run', {'name': 'orders_get_held_carts', 'args': {}}, GOOD)
    check('run: a tool the reckoner does not have is refused', 'no tool' in r.get_json().get('error', '') and len(seen) == 1)
    r = post('/api/assistant/run', {'name': 'get_overview', 'args': 'oops'}, GOOD)
    check('run: args that are not a dict are tolerated', r.status_code == 200 and seen[-1] == ('get_overview', {}))
finally:
    server.ask_mod.T.run_tool = real
server.ask_mod.T.run_tool = lambda name, args: (_ for _ in ()).throw(ValueError('boom'))
try:
    r = post('/api/assistant/run', {'name': 'get_overview'}, GOOD)
    check('run: a tool that crashes comes back as an error, not a 500', r.status_code == 200 and 'ValueError' in r.get_json()['error'], r.get_json())
finally:
    server.ask_mod.T.run_tool = real
r = post('/api/assistant/run', {'name': 'basket_list', 'args': {}}, GOOD)
check('run: a basket tool needs an owner key', 'owner' in r.get_json().get('error', ''))

# --- the basket ---------------------------------------------------------------------------------
r = post('/api/assistant/basket', {'action': 'classify', 'owner': 'wa-owner', 'text': 'calculate'}, GOOD)
check('basket classify: "calculate" is a basket command', r.get_json().get('kind') == 'command', r.get_json())
r = post('/api/assistant/basket', {'action': 'classify', 'owner': 'wa-owner', 'text': 'how much did we sell last month?'}, GOOD)
check('basket classify: a normal question is not', r.get_json().get('kind') is None, r.get_json())
r = post('/api/assistant/basket', {'action': 'interpret', 'owner': 'wa-owner', 'text': 'calculate'}, GOOD)
check('basket interpret: an empty basket says so', 'empty' in (r.get_json().get('reply') or {}).get('say', ''), r.get_json())
r = post('/api/assistant/basket', {'action': 'classify', 'text': 'calculate'}, GOOD)
check('basket: no owner key -> an error', 'owner' in r.get_json().get('error', ''))

print()
print('ALL PASSED' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}')
sys.exit(1 if FAILS else 0)

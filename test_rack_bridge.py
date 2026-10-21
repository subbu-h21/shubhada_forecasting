"""Checks for rack_bridge.py (Ask page -> Rack Management lookups) and its wiring in ask.py.

A fake Rack Management server runs inside this test (same endpoints and field names as the real app, which was
checked against a copy of it): nothing here touches the live rack app or its data, and no model is called.
Run with the reckoner's venv:
    venv\\Scripts\\python.exe test_rack_bridge.py
"""
import copy
import json
import os
import sys
import threading
import types
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8')
os.environ.setdefault('OPENROUTER_API_KEY', 'sk-test-not-real')
os.environ['OPENROUTER_MODEL_FAST'] = 'fake/fast-model'
os.environ['OPENROUTER_MODEL_REASONING'] = 'fake/reasoning-model'
for v in ('ORDER_ASK_URL', 'ORDER_ASK_KEY', 'RACK_ASK_URL', 'RACK_ASK_PIN'):
    os.environ.pop(v, None)

import openai  # noqa: E402
import ask  # noqa: E402
import rack_bridge as R  # noqa: E402

FAILS = []
PIN = 'test-pin-9876'


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'   <-- {detail}'))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------------------------------------
# The fake Rack Management app
# ---------------------------------------------------------------------------------------------
def item(name, pack, avg, mark='', unit='S', form='Tab/Cap', moved=0, placed=0, dest=''):
    return [name, pack, avg, mark, unit, form, moved, placed, dest]


def row(product, plan, dest='', **kw):
    base = {'product': product, 'pack': '10S', 'section': 'Allopathic', 'category': 'Tab/Cap', 'plan': plan, 'plan_dest': dest, 'plan_dest_name': '',
            'plan_extra': '', 'plan_extra_name': '', 'plan_moved': False, 'plan_main': '', 'plan_park': '', 'plan_tabs': 0, 'mark': '',
            'rack_id': '', 'box': '', 'placed': False}
    base.update(kw)
    return base


PRODUCTS = {
    'b1': [row('ECOSPRIN AV 75', 'rack', 'A-RACK1', plan_extra='A-EXTRA1', plan_tabs=6529.3, mark='B'),
           row('ECOSPRIN AV 75/2O CAP', 'bin', 'ALLO A-BIN1', plan_tabs=36.7, placed=True, box='ALLO A-BIN1', mark='B'),
           row('ANXIT 0.5', 'h1', 'H1-RACK1', plan_tabs=40),
           row('OLDDRUG TAB', 'skip', plan_park='PARK A-1'),
           row('DUPDRUG TAB', 'skip', plan_main='MAINDRUG TAB', plan_park='PARK A-1'),
           row('PONDS POWDER', 'rack', '', plan_tabs=15.3, category='Powder'),
           row('MOVED TAB', 'rack', 'A-RACK2', plan_dest_name='Fast shelf', plan_moved=True, placed=True, rack_id='A-RACK2', plan_tabs=500),
           row('MISPLACED TAB', 'bin', 'ALLO A-BIN2', plan_tabs=10, placed=True, rack_id='M001'),
           row('LIPSTICK X', '', '', category='Other')],
    'b2': [row('ECOSPRIN AV 75', 'rack', 'A-RACK1', plan_tabs=5598.7, placed=True, rack_id='A-RACK1')],
    'b3': [row('ECOSPRIN AV 75', '', ''), row('DOLO 650', '', '', placed=True, rack_id='G001')],
}
ITEMS_BIN1 = [item('AMLOVAS 2.5TAB', '15', 1.0, 'B', placed=1, dest='ALLO A-BIN1'), item('AMLOVAS AT TAB', '15', 2.3, 'B', moved=1, dest='ALLO A-BIN1')]
ITEMS_MANY = [item(f'PROD {i:02d}', '10S', 1.0, dest='A-RACK1') for i in range(1, 31)]


def plan_view(bid):
    if bid == 'b3':
        return {'bins': {}, 'racks': {}, 'kinds': {}, 'names': {}, 'dests': {'bins': [], 'racks': []}, 'generated': ''}
    return {'bins': {'Allopathic': {'A': {'1': ITEMS_BIN1, '2': [item('X BIN2 TAB', '10', 1.0)]}}, 'Ayurvedic': {'A': {'1': [item('AYUR ONE', '1', 1.0, unit='P')]}}},
            'racks': {'A-RACK1': ITEMS_MANY, 'B-RACK1': [item('B ONE', '10S', 1.0)], 'A-RACK2': [item('MOVED TAB', '10S', 500.0)], 'A-EXTRA1': [item('ECOSPRIN AV 75', '15S', 9.0)],
                      'H1-RACK1': [item('ANXIT 0.5', '10S', 40.0)], 'LIQ-RACK1': [item('COUGH SYP', '100ML', 5.0, unit='P')]},
            'kinds': {'ALLO A-BIN1': 'DBP'}, 'names': {'A-RACK2': 'Fast shelf'}, 'generated': '2026-10-21T10:32:30'}


def plan_dests(bid):
    if bid == 'b3':
        return {'bins': [], 'racks': [], 'kinds': {}, 'names': {}}
    return {'bins': ['ALLO A-BIN1', 'ALLO A-BIN2', 'AYUR A-BIN1'], 'racks': ['A-RACK1', 'B-RACK1', 'A-RACK2', 'H1-RACK1', 'LIQ-RACK1'], 'kinds': {'ALLO A-BIN1': 'DBP'}, 'names': {'A-RACK2': 'Fast shelf'}}


CARDS = {'branches': [{'id': 'b1', 'name': 'Shivaji Chowk', 'total': 100, 'placed': 4, 'pending': 96, 'plan': {'rack': 5, 'bin': 3, 'skip': 4, 'h1': 1, 'extra': 1}, 'bins': 3, 'racks': 4},
                      {'id': 'b2', 'name': 'Hospet Road', 'total': 90, 'placed': 1, 'pending': 89, 'plan': {'rack': 4, 'bin': 2, 'skip': 3, 'h1': 1, 'extra': 0}, 'bins': 3, 'racks': 4},
                      {'id': 'b3', 'name': 'Herur', 'total': 80, 'placed': 0, 'pending': 80, 'plan': None, 'bins': 0, 'racks': 0}]}
PARKED = {'parked': {'A': {'1': [['OLDDRUG TAB', '10S', 'NS', '', 'Tab/Cap'], ['DUPDRUG TAB', '10S', 'DUP', 'MAINDRUG TAB', 'Tab/Cap']]}}, 'total': 2, 'brought_back': 0, 'generated': 'x'}


class Fake:
    """State shared with the handler."""
    sessions = {}                 # cookie -> branch
    logins = 0
    calls = []                    # (method, path) of EVERY request that reached the server
    counter = 0
    down_code = 0                 # non-zero: answer every request with this HTTP status


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj, headers=None):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _session(self):
        for part in (self.headers.get('Cookie') or '').split(';'):
            if part.strip().startswith('sess='):
                return part.strip()
        return None

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        Fake.calls.append(('POST', u.path))
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0)) or 0) or b'{}')
        if Fake.down_code:
            return self._send(Fake.down_code, {'error': 'down'})
        if u.path == '/api/login':
            if body.get('pin') != PIN:
                return self._send(401, {'ok': False, 'error': 'Wrong PIN'})
            Fake.logins += 1
            Fake.counter += 1
            ck = f'sess=tok{Fake.counter}'
            Fake.sessions[ck] = 'b1'
            return self._send(200, {'ok': True}, {'Set-Cookie': ck + '; Path=/; HttpOnly'})
        ck = self._session()
        if ck not in Fake.sessions:
            return self._send(401, {'error': 'auth required'})
        if u.path == '/api/set_branch':
            Fake.sessions[ck] = body.get('branch')
            return self._send(200, {'ok': True})
        return self._send(200, {'ok': True, 'WRITE': u.path})        # a write that must never be asked for

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        Fake.calls.append(('GET', u.path))
        if Fake.down_code:
            return self._send(Fake.down_code, {'error': 'down'})
        if u.path == '/api/whoami':
            return self._send(200, {'auth': False, 'branches': [{'id': b['id'], 'name': b['name']} for b in CARDS['branches']]})
        ck = self._session()
        if ck not in Fake.sessions:
            return self._send(401, {'error': 'auth required'})
        bid = Fake.sessions[ck]
        if u.path == '/api/branch_cards':
            return self._send(200, CARDS)
        if u.path == '/api/plan_dests':
            return self._send(200, plan_dests(bid))
        if u.path == '/api/plan_view':
            return self._send(200, plan_view(bid))
        if u.path == '/api/plan_parked':
            return self._send(200, PARKED if bid != 'b3' else {'parked': {}, 'total': 0, 'brought_back': 0})
        if u.path == '/api/search':
            term = urllib.parse.parse_qs(u.query).get('q', [''])[0].upper()
            rows = [r for r in PRODUCTS.get(bid, []) if term in r['product'].upper()]
            return self._send(200, {'mode': 'product', 'query': term, 'count': len(rows), 'rows': rows})
        return self._send(200, {'LEAK': u.path, 'employees': [{'name': 'someone', 'phone': 'private'}]})   # anything else must never be asked for


srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f'http://127.0.0.1:{srv.server_address[1]}'


def configure(on=True, url=URL, pin=PIN):
    for k in ('RACK_ASK_URL', 'RACK_ASK_PIN'):
        os.environ.pop(k, None)
    if on:
        os.environ['RACK_ASK_URL'] = url
        os.environ['RACK_ASK_PIN'] = pin
    R.reset_cache()


def reset_fake():
    Fake.sessions.clear()
    Fake.calls.clear()
    Fake.logins = 0
    Fake.down_code = 0
    R.reset_cache()


def text(x):
    return json.dumps(x, ensure_ascii=False)


# ---------------------------------------------------------------------------------------------
# 1. off unless configured
# ---------------------------------------------------------------------------------------------
configure(False)
check('not configured: no rack tools are offered', R.tool_specs() == [])
check('not configured: a call is an error, and nothing is sent anywhere', 'error' in R.run('rack_counts', {}) and Fake.calls == [])
os.environ['RACK_ASK_PIN'] = '12'
check('a PIN under 4 characters does not count as configured', R.tool_specs() == [])
configure(True)
names = [t['name'] for t in R.tool_specs()]
check('configured: the three rack tools are offered', names == ['rack_find_product', 'rack_counts', 'rack_contents'], names)
check('...described for the model as rack & bin tools', all(t['description'].startswith('[RACK & BIN]') for t in R.tool_specs()))
check('unknown tool names are refused', 'error' in R.run('rack_assign', {'product': 'x'}) and 'error' in R.run('orders_x', {}))

# ---------------------------------------------------------------------------------------------
# 2. read-only by construction
# ---------------------------------------------------------------------------------------------
reset_fake()
for path in ('/api/plan_move', '/api/assign', '/api/set_pin', '/api/plan_rename', '/api/rollout_done', '/api/upload', '/api/set_rx_key'):
    try:
        R._open('POST', path, {'x': 1}, 'sess=tok1')
        check(f'POST {path} is refused', False, 'it was sent')
    except ValueError:
        check(f'POST {path} is refused in code (never sent)', True)
for path in ('/api/employees', '/api/audit', '/api/export', '/api/products', '/api/rack_audit', '/api/audit_export'):
    try:
        R._open('GET', path, None, 'sess=tok1')
        check(f'GET {path} is refused', False, 'it was sent')
    except ValueError:
        check(f'GET {path} (staff / audit / export data) is refused in code', True)
for method in ('PUT', 'DELETE', 'PATCH'):
    try:
        R._open(method, '/api/search', None, 'sess=tok1')
        check(f'{method} is refused', False, 'it was sent')
    except ValueError:
        check(f'{method} is refused in code (never sent)', True)
check('none of those refused calls reached the server', Fake.calls == [], Fake.calls)

# ---------------------------------------------------------------------------------------------
# 3. which rack / bin - plan and placement kept apart
# ---------------------------------------------------------------------------------------------
reset_fake()
r = R.run('rack_find_product', {'product': 'Ecosprin AV 75'})
check('find: answers for each branch that has a plan (not Herur)', [b['branch'] for b in r['branches']] == ['Shivaji Chowk', 'Hospet Road'], r)
shi = {p['product']: p for p in r['branches'][0]['products']}
hos = {p['product']: p for p in r['branches'][1]['products']}
eco = shi['ECOSPRIN AV 75']
check('rack product, not yet placed: "Planned for rack ..." + second home + "have not recorded"',
      'Planned for rack A-RACK1' in eco['where'] and 'extra rack A-EXTRA1' in eco['where'] and 'have not recorded placing it' in eco['where'] and eco['placed_by_staff'] is False, eco['where'])
check('...with its monthly sale, in words that do not make the rack sell anything', '(it sells about 6529 tablets/capsules a month)' in eco['where'], eco['where'])
check('the same product placed by staff at Hospet says so', 'Staff HAVE recorded placing it there' in hos['ECOSPRIN AV 75']['where'] and hos['ECOSPRIN AV 75']['placed_by_staff'], hos)
check('bin product placed by staff: "Planned for bin ... Staff HAVE recorded"', 'Planned for bin ALLO A-BIN1' in shi['ECOSPRIN AV 75/2O CAP']['where'] and 'Staff HAVE recorded' in shi['ECOSPRIN AV 75/2O CAP']['where'])
check('exact name ranks first', r['branches'][0]['products'][0]['product'] == 'ECOSPRIN AV 75')
check('plan date and the plan-vs-placed note are in the result', r['plan_built'] == '2026-10-21T10:32:30' and 'placed_by_staff' in r['note'])
r1 = R.run('rack_find_product', {'product': 'anxit', 'branch': 'Shivaji'})['branches'][0]['products'][0]
check('Schedule H1: its own rack, supervised, prescription rule stated', 'Schedule H1 rack' in r1['where'] and 'valid prescription' in r1['where'] and r1['planned_place'] == 'H1-RACK1', r1['where'])
r1 = R.run('rack_find_product', {'product': 'olddrug', 'branch': 'Shivaji'})['branches'][0]['products'][0]
check('no-sale product: no rack or bin, the PARK list is called virtual', 'no rack or bin' in r1['where'] and 'PARK A-1' in r1['where'] and 'not a physical shelf' in r1['where'] and r1['parked_in'] == 'PARK A-1', r1['where'])
r1 = R.run('rack_find_product', {'product': 'dupdrug', 'branch': 'Shivaji'})['branches'][0]['products'][0]
check('duplicate entry: says to use the other one', 'use "MAINDRUG TAB" instead' in r1['where'], r1['where'])
r1 = R.run('rack_find_product', {'product': 'ponds', 'branch': 'Shivaji'})['branches'][0]['products'][0]
check('a planned-"rack" item with NO rack code never says "Planned for rack ," ', 'Planned for rack' not in r1['where'] and 'no rack or bin for it yet' in r1['where'], r1['where'])
r1 = R.run('rack_find_product', {'product': 'moved tab', 'branch': 'Shivaji'})['branches'][0]['products'][0]
check('moved by hand, with the admin\'s name for the rack', 'A-RACK2 ("Fast shelf")' in r1['where'] and 'moved there by hand' in r1['where'], r1['where'])
r1 = R.run('rack_find_product', {'product': 'misplaced', 'branch': 'Shivaji'})['branches'][0]['products'][0]
check('staff recorded it somewhere other than the plan: both said', 'recorded it at M001' in r1['where'] and 'not the planned place' in r1['where'], r1['where'])
r1 = R.run('rack_find_product', {'product': 'lipstick', 'branch': 'Shivaji'})['branches'][0]['products'][0]
check('a product the plan does not list (branch HAS a plan): not "no plan yet"', 'not part of the rack plan' in r1['where'] and 'no rack plan yet' not in r1['where'], r1['where'])
r1 = R.run('rack_find_product', {'product': 'dolo', 'branch': 'Herur'})['branches'][0]['products'][0]
check('Herur has no plan: says so, and shows what staff recorded', 'no rack plan yet' in r1['where'] and 'recorded it at G001' in r1['where'], r1['where'])
check('branch words work: "hospet road", "Hospet", "shivaji chowk"', all(len(R.run('rack_find_product', {'product': 'eco', 'branch': b})['branches']) == 1 for b in ('hospet road', 'Hospet', 'shivaji chowk')))
bad = R.run('rack_find_product', {'product': 'dolo', 'branch': 'Mumbai'})
check('unknown branch: an error that lists the real branches', 'error' in bad and 'Hospet Road' in bad['error'], bad)
check('an empty product is a question back, not a crash', 'error' in R.run('rack_find_product', {'product': '  '}))
check('nothing found is an empty list, not an error', R.run('rack_find_product', {'product': 'zzzzzz'})['branches'][0]['matches'] == 0)

# ---------------------------------------------------------------------------------------------
# 4. how many bins / racks
# ---------------------------------------------------------------------------------------------
c = R.run('rack_counts', {})
b1, b2 = c['branches']
check('counts: each planned branch, bins from the plan (plastic bins)', (b1['plastic_bins'], b2['plastic_bins']) == (3, 3) and b1['branch'] == 'Shivaji Chowk', c)
check('counts: bins split by section, racks by kind, extra racks apart', b1['bins_by_section'] == {'ALLO': 2, 'AYUR': 1} and b1['racks'] == 5
      and b1['racks_by_kind'] == {'letter': 3, 'h1': 1, 'liq': 1} and b1['extra_racks'] == 1, b1)
check('counts: parking bins are reported apart and called virtual', b1['virtual_parking_bins'] == 1 and b1['parked_products'] == 2 and 'virtual' in c['note'])
check('counts: the total across branches is the sum', c['all_planned_branches_together'] == {'plastic_bins': 6, 'racks': 10, 'extra_racks': 2}, c.get('all_planned_branches_together'))
check('counts: a branch with no plan is named, with no made-up zeros as if real', c['branches_with_no_rack_plan_yet'] == ['Herur'])
h = R.run('rack_counts', {'branch': 'Herur'})['branches'][0]
check('counts for Herur say the plan is not built yet', 'not built yet' in h['rack_plan'] and h['products_planned'] is None, h)
l = R.run('rack_counts', {'branch': 'shivaji', 'letter': 'a'})['branches'][0]
check('counts for a letter: its bins (with products and DBP kind) and racks, and its parking bins',
      [x['code'] for x in l['letter_bins']] == ['ALLO A-BIN1', 'ALLO A-BIN2', 'AYUR A-BIN1'] and l['letter_bins'][0]['kind'] == 'DBP' and l['letter_bins'][0]['products'] == 2
      and {x['code'] for x in l['letter_racks']} == {'A-RACK1', 'A-RACK2', 'A-EXTRA1'} and l['letter_virtual_parking_bins'] == 1, l)

# ---------------------------------------------------------------------------------------------
# 5. what is in a bin / rack
# ---------------------------------------------------------------------------------------------
x = R.run('rack_contents', {'place': 'ALLO A-BIN1', 'branch': 'Shivaji'})['results'][0]
check('contents by exact code: products with strips/month and the staff-placed flag', x['found'] and x['products'] == 2 and x['items'][0]['placed_by_staff'] and x['items'][1]['moved_in_by_hand']
      and x['items'][0]['unit'] == 'strips' and x['reserved_for'] == 'diabetic / BP products' and x['kind'] == 'bin', x)
x = R.run('rack_contents', {'place': 'a bin 2', 'branch': 'Shivaji'})['results']
check('contents by how it is said ("a bin 2")', len(x) == 1 and x[0]['code'] == 'ALLO A-BIN2' and x[0]['items'][0]['product'] == 'X BIN2 TAB', x)
x = R.run('rack_contents', {'place': 'a bin 1', 'branch': 'Shivaji'})['results']
check('"a bin 1" fits an Allopathic and an Ayurvedic bin: both are returned, labelled', {y['code'] for y in x} == {'ALLO A-BIN1', 'AYUR A-BIN1'}, x)
x = R.run('rack_contents', {'place': 'fast shelf', 'branch': 'Shivaji'})['results'][0]
check('contents by the name the admin gave the rack', x['found'] and x['code'] == 'A-RACK2' and x['name'] == 'Fast shelf', x)
x = R.run('rack_contents', {'place': 'PARK A-1', 'branch': 'Shivaji'})['results'][0]
check('contents of a parking list: labelled virtual, with why each is parked', x['kind'] == 'virtual parking list' and [i['why_parked'] for i in x['items']] == ['no sale', 'duplicate - use MAINDRUG TAB'], x)
x = R.run('rack_contents', {'place': 'a-rack1', 'branch': 'Shivaji', 'limit': 5})['results'][0]
check('contents honours the limit and says how many more', x['products'] == 30 and x['shown'] == 5 and x['more'] == 25, x)
x = R.run('rack_contents', {'place': 'rack1', 'branch': 'Shivaji'})['results'][0]
check('a fragment that fits many places ("rack1": A-, B-, H1-, LIQ-RACK1) is not guessed - it lists them', x['found'] is False and len(x.get('ambiguous', [])) == 4 and 'full code' in x['note'], x)
x = R.run('rack_contents', {'place': 'zzz9', 'branch': 'Shivaji'})['results'][0]
check('a code that does not exist: not found, no suggestions when nothing is close', x['found'] is False and x['did_you_mean'] == [], x)
x = R.run('rack_contents', {'place': 'ALLO A-BIN9', 'branch': 'Shivaji'})['results'][0]
check('a near miss (ALLO A-BIN9) gets "did you mean" with the real bins', x['found'] is False and {'ALLO A-BIN1', 'ALLO A-BIN2'} <= set(x['did_you_mean']), x)
check('an empty place is a question back', 'error' in R.run('rack_contents', {'place': ''}))

# ---------------------------------------------------------------------------------------------
# 6. sessions: one per branch, re-login after the rack app restarts, PIN never leaks
# ---------------------------------------------------------------------------------------------
reset_fake()
R.run('rack_find_product', {'product': 'eco'})
R.run('rack_counts', {})
R.run('rack_contents', {'place': 'a bin 2', 'branch': 'Shivaji'})
check('it signs in once per branch it uses (2 branches -> 2 logins), then reuses the sessions', Fake.logins == 2, Fake.logins)
check('every sign-in then picks its branch (and nothing else is POSTed)', sorted(set(c for c in Fake.calls if c[0] == 'POST')) == [('POST', '/api/login'), ('POST', '/api/set_branch')])
Fake.sessions.clear()          # the rack app was restarted: its sessions live in memory
R.reset_cache()
r = R.run('rack_find_product', {'product': 'eco'})
check('after the rack app restarts it signs in again by itself and still answers', 'branches' in r and Fake.logins >= 4, (r, Fake.logins))
configure(True, pin='wrong-pin-0000')
reset_fake()
r = R.run('rack_counts', {})
check('a wrong PIN: a clear error naming the setting, never the PIN', 'RACK_ASK_PIN' in r.get('error', '') and 'wrong-pin-0000' not in text(r), r)
configure(True, url='http://127.0.0.1:9')           # nothing listens there
r = R.run('rack_counts', {})
check('the rack app switched off: "not reachable", no crash', 'not reachable' in r.get('error', ''), r)
configure(True)
reset_fake()
Fake.down_code = 500
r = R.run('rack_counts', {})
check('the rack app erroring (HTTP 500) is an error result', 'HTTP 500' in r.get('error', ''), r)
reset_fake()
everything = [R.run('rack_find_product', {'product': 'eco'}), R.run('rack_counts', {'letter': 'a'}), R.run('rack_contents', {'place': 'a-rack1'}), R.run('rack_nope', {}),
              R.run('rack_counts', {'branch': 'Mumbai'})]
check('the PIN appears in no result and no error', PIN not in text(everything))
check('and no session cookie either', 'sess=' not in text(everything) and 'tok1' not in text(everything) and 'tok2' not in text(everything))

# ---------------------------------------------------------------------------------------------
# 7. cached briefly, so a chatty question is not slow
# ---------------------------------------------------------------------------------------------
reset_fake()
R.run('rack_counts', {})
n1 = len(Fake.calls)
R.run('rack_counts', {})
check('a repeat of the same question inside the cache window makes no new requests', len(Fake.calls) == n1, (n1, len(Fake.calls)))
R.reset_cache()
R.run('rack_counts', {})
check('...and a cleared cache asks again', len(Fake.calls) > n1)

# ---------------------------------------------------------------------------------------------
# 8. ask.py: the tools and the prompt exist only when it is configured
# ---------------------------------------------------------------------------------------------
configure(False)
ask.T.get_notes = lambda n=20: {'notes': []}
check('not configured: ask offers no rack tools', not any(t['name'].startswith('rack_') for t in ask._available_tools()))
check('not configured: the system prompt has no rack section', 'RACK & BIN' not in ask._build_system_prompt())
configure(True)
reset_fake()
av = ask._available_tools()
check('configured: ask offers the three rack tools next to the others', {'rack_find_product', 'rack_counts', 'rack_contents'} <= {t['name'] for t in av} and len(av) > 20)
check('configured: also in spoken mode (the rack data holds no patient information)', {'rack_counts'} <= {t['name'] for t in ask._available_tools(voice=True)})
sp = ask._build_system_prompt(speak=True)
check('configured: the system prompt explains plan vs placed, bins vs racks, and that PARK is virtual',
      'RACK & BIN' in sp and 'PLAN' in sp and 'PLACED' in sp and 'virtual' in sp and 'plastic bin' in sp)
allowed = {t['name'] for t in av}
res = ask._run_allowed_tool('rack_counts', {}, allowed)
check('configured: ask routes a rack_ call to the bridge', res.get('branches') and res['branches'][0]['plastic_bins'] == 3, res)
check('a rack tool the conversation was not offered is still refused', 'error' in ask._run_allowed_tool('rack_counts', {}, {'get_overview'}))


class FakeCompletions:
    def __init__(self, script):
        self.script, self.requests = list(script), []

    def create(self, **kw):
        self.requests.append(copy.deepcopy(kw))
        return self.script.pop(0)


def reply(content=None, calls=None):
    tcs = None
    if calls:
        tcs = [types.SimpleNamespace(id=f'c{i}', function=types.SimpleNamespace(name=n, arguments=json.dumps(a))) for i, (n, a) in enumerate(calls)]
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content, tool_calls=tcs))], usage=None)


fake = FakeCompletions([reply(calls=[('rack_find_product', {'product': 'Ecosprin AV 75', 'branch': 'Hospet Road'})]), reply('(ready)'),
                        reply('Ecosprin AV 75 at Hospet Road is on rack A-RACK1, and staff have placed it there.')])
openai.OpenAI = lambda **k: types.SimpleNamespace(chat=types.SimpleNamespace(completions=fake))
answer = ask.ask('Which rack is Ecosprin AV 75 in at Hospet Road?', speak=True)
tool_msg = [m for m in fake.requests[1]['messages'] if m.get('role') == 'tool'][0]['content']
check('end to end: the model called rack_find_product and got the sentence it should read out', 'Staff HAVE recorded placing it there' in tool_msg and 'A-RACK1' in tool_msg, tool_msg[:300])
check('end to end: the request carried the rack tools and the rack prompt', 'rack_find_product' in [t['function']['name'] for t in fake.requests[0]['tools']] and 'RACK & BIN' in fake.requests[0]['messages'][0]['content'])
check('end to end: the answer is what the model said after reading it', answer.startswith('Ecosprin AV 75 at Hospet Road is on rack A-RACK1'))
configure(False)

srv.shutdown()
print()
print(f'{len(FAILS)} FAILED: {FAILS}' if FAILS else 'ALL PASSED')
sys.exit(1 if FAILS else 0)

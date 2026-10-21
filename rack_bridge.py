"""Rack & bin lookups: lets the Ask page answer "which rack or bin is this product in?", "how many bins
do we have?", "what is in bin A-3?" from the Rack Management app (a separate project, port 8765).

Rack Management keeps a PLAN of where every product should live (built from sales history: fast sellers on
racks, slow sellers in plastic bins, Schedule H1 on its own racks, no-sale products in a virtual PARK list) and
a record of what staff have actually PLACED so far. This module asks that app's own read-only screens - Find,
branch cards, the Rack & Bin plan, Parked - so an answer is exactly what staff see there. The rules are never
copied here: the rack project changes them every few days.

Switched on by a setting (environment or the local .env):
    RACK_ASK_PIN    the PIN of one staff login in Rack Management (Staff > add "Reckoner Ask" > Set PIN)
    RACK_ASK_URL    optional; default http://127.0.0.1:8765 (the rack app on this PC)

Read-only by construction: the only requests ever sent are the GET screens in READ_PATHS plus the login and the
branch switch (which changes only the session's own working branch). Assigning, moving, renaming and every other
write are refused in code before anything leaves this module. The PIN is never logged and never put in a result.

Failure is soft, like order_bridge: not configured -> no rack_* tools at all; the rack app off or the PIN wrong ->
the tool result is an error the model can explain. Nothing here raises into ask.py.
"""
import difflib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PREFIX = 'rack_'
DEFAULT_URL = 'http://127.0.0.1:8765'
MIN_PIN_CHARS = 4
CALL_TIMEOUT_S = 25
MAX_REPLY_BYTES = 8_000_000                  # a branch's whole plan is ~0.8 MB
MAX_ITEMS = 100

# The only screens ever read, and how long a copy is trusted (staff place products while the owner asks).
READ_TTL_S = {'/api/whoami': 300, '/api/branch_cards': 30, '/api/plan_dests': 30, '/api/plan_view': 60,
              '/api/plan_parked': 60, '/api/search': 0}
READ_PATHS = frozenset(READ_TTL_S)
SESSION_PATHS = frozenset({'/api/login', '/api/set_branch'})     # the only POSTs: sign in, pick the working branch

_lock = threading.RLock()
_cookies = {}               # branch id -> session cookie ("sess=...")
_cache = {}                 # (branch id, path+query) -> (time, data)


class RackError(Exception):
    """A message that is safe to show the owner."""


def _settings():
    """(base_url, pin), or ('', '') when rack lookups are not configured."""
    pin = (os.environ.get('RACK_ASK_PIN') or '').strip()
    url = (os.environ.get('RACK_ASK_URL') or DEFAULT_URL).strip().rstrip('/')
    if len(pin) < MIN_PIN_CHARS or not url.startswith(('http://', 'https://')):
        return '', ''
    return url, pin


# ---------------------------------------------------------------------------------------------
# Talking to the rack app (read-only, one session per branch, signs in again if the app restarted)
# ---------------------------------------------------------------------------------------------
def _open(method, path, body=None, cookie=''):
    url, _ = _settings()
    if not url:
        raise RackError('Rack Management is not connected to this page.')
    if (method == 'GET' and path.split('?')[0] not in READ_PATHS) or (method == 'POST' and path not in SESSION_PATHS) \
            or method not in ('GET', 'POST'):
        raise ValueError(f'{method} {path} is not a read-only rack screen')      # a write can never leave this module
    data = None if body is None else json.dumps(body).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if cookie:
        headers['Cookie'] = cookie
    req = urllib.request.Request(url + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=CALL_TIMEOUT_S) as resp:
        raw = resp.read(MAX_REPLY_BYTES + 1)
        set_cookie = resp.headers.get_all('Set-Cookie') or []
    if len(raw) > MAX_REPLY_BYTES:
        raise RackError('Rack Management sent a reply that is too large.')
    return json.loads(raw.decode('utf-8')), set_cookie


def _login(branch):
    _, pin = _settings()
    try:
        _, cookies = _open('POST', '/api/login', {'pin': pin})
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RackError('Rack Management refused the PIN this page uses (RACK_ASK_PIN) - check it is a staff PIN there.') from None
        raise
    cookie = next((c.split(';')[0] for c in cookies if c.startswith('sess=')), '')
    if not cookie:
        raise RackError('Rack Management did not start a session.')
    if branch:
        _open('POST', '/api/set_branch', {'branch': branch}, cookie)
    with _lock:
        _cookies[branch or ''] = cookie
    return cookie


def _fetch(path_query, branch):
    path = path_query.split('?')[0]
    if path == '/api/whoami':
        return _open('GET', path_query)[0]
    cookie = _cookies.get(branch or '') or _login(branch)
    for attempt in (1, 2):
        try:
            return _open('GET', path_query, cookie=cookie)[0]
        except urllib.error.HTTPError as e:
            if e.code != 401 or attempt == 2:
                raise
            with _lock:
                _cookies.pop(branch or '', None)
            cookie = _login(branch)                 # the rack app was restarted: sessions are in its memory


def _get(path, query='', branch=None):
    """A read-only screen of the rack app, cached briefly."""
    key = (branch or '', path + query)
    ttl = READ_TTL_S.get(path, 0)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and ttl and now - hit[0] < ttl:
            return hit[1]
    data = _fetch(path + query, branch)
    with _lock:
        _cache[key] = (now, data)
        if len(_cache) > 200:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
                _cache.pop(k, None)
    return data


def _call(fn):
    """Run a tool body and turn every failure into an error result the model can explain."""
    try:
        return fn()
    except RackError as e:
        return {'error': str(e)}
    except urllib.error.HTTPError as e:
        return {'error': f'Rack Management returned an error (HTTP {e.code}).'}
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return {'error': 'Rack Management is not reachable right now (is it running on this PC?).'}
    except Exception as e:  # noqa: BLE001
        return {'error': f'Could not read Rack Management ({type(e).__name__}).'}


# ---------------------------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------------------------
def _branches():
    """{branch id: name} - Shivaji Chowk, Hospet Road, ..."""
    return {b['id']: b['name'] for b in (_get('/api/whoami').get('branches') or [])}


def _cards(first_branch):
    return {c['id']: c for c in (_get('/api/branch_cards', branch=first_branch).get('branches') or [])}


def _pick_branches(text):
    """Branch ids for what the owner said ('hospet', 'Shivaji Chowk'); with nothing said, every branch that has a rack plan."""
    names = _branches()
    if not names:
        raise RackError('Rack Management has no branches set up.')
    first = next(iter(names))
    t = re.sub(r'[^a-z0-9 ]', '', str(text or '').lower()).strip()
    if t:
        hits = [b for b, n in names.items() if t in n.lower() or n.lower() in t or any(w and w in n.lower() for w in t.split())]
        if not hits:
            close = difflib.get_close_matches(t, [n.lower() for n in names.values()], n=1, cutoff=0.6)
            hits = [b for b, n in names.items() if close and n.lower() == close[0]]
        if not hits:
            raise RackError(f"Rack Management has no branch like '{text}'. Its branches are: {', '.join(names.values())}.")
        return hits[:1] if len(hits) == 1 else hits
    cards = _cards(first)
    planned = [b for b in names if (cards.get(b) or {}).get('plan')]
    return planned or list(names)[:1]


# ---------------------------------------------------------------------------------------------
# Words for a place (the rack app's own fields, said plainly - plan and placement never mixed up)
# ---------------------------------------------------------------------------------------------
def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return ''
    return str(int(round(x))) if x >= 10 else (f'{x:.1f}'.rstrip('0').rstrip('.') or '0')


def _label(code, name):
    return f'{code} ("{name}")' if name else code


def where_sentence(r, has_plan=True):
    """One plain sentence about where a product lives, from a row of the rack app's Find screen.
    has_plan: this branch has a rack plan at all (Herur and Sidhapur do not yet)."""
    plan = r.get('plan') or ''
    dest, name = r.get('plan_dest') or '', r.get('plan_dest_name') or ''
    rid, box = r.get('rack_id') or '', r.get('box') or ''
    recorded = rid or box
    tabs = r.get('plan_tabs') or 0
    parts = []
    if plan == 'skip':
        main, park = r.get('plan_main') or '', r.get('plan_park') or ''
        if main:
            parts.append(f'A duplicate entry: it gets no rack or bin of its own - use "{main}" instead.')
        else:
            parts.append('No sales in the period the plan was built from, so it has no rack or bin.')
        if park:
            parts.append(f'It is on the virtual parking list {park} - a holding list, not a physical shelf.')
        if recorded:
            parts.append(f'Staff have recorded it at {recorded}.')
    elif plan in ('bin', 'rack', 'h1') and not dest:
        # e.g. cosmetics / household goods: the plan keeps non-medicines off the medicine racks and has no shelf for them yet
        parts.append('The rack plan has no rack or bin for it yet (for example, non-medicine items such as cosmetics are kept off the medicine '
                     'racks and have no shelf plan yet).')
        if recorded:
            parts.append(f'Staff have recorded it at {recorded}.')
    elif plan in ('bin', 'rack', 'h1'):
        what = {'bin': 'bin', 'rack': 'rack', 'h1': 'Schedule H1 rack (supervised: no sale without a valid prescription)'}[plan]
        sells = f' (it sells about {_num(tabs)} tablets/capsules a month)' if tabs else ''
        parts.append(f'Planned for {what} {_label(dest, name)}{sells}.')
        if r.get('plan_extra'):
            parts.append(f'It also has a second home in the extra rack {_label(r["plan_extra"], r.get("plan_extra_name") or "")}.')
        if r.get('plan_moved'):
            parts.append('It was moved there by hand from its original planned place.')
        if recorded and recorded == dest:
            parts.append('Staff HAVE recorded placing it there.')
        elif recorded:
            parts.append(f'Staff recorded it at {recorded}, which is not the planned place.')
        else:
            parts.append('Staff have not recorded placing it there yet.')
    else:
        parts.append('This branch has no rack plan yet.' if not has_plan else
                     'It is not part of the rack plan (for example a non-medicine item, or not in the plan\'s product list).')
        if recorded:
            parts.append(f'Staff recorded it at {recorded}.')
    return ' '.join(parts)


def _row(r, has_plan=True):
    plan = r.get('plan') or ''
    out = {'product': r.get('product'), 'pack': r.get('pack'), 'section': r.get('section'), 'category': r.get('category'),
           'plan': plan or 'not in the plan', 'where': where_sentence(r, has_plan),
           'planned_place': r.get('plan_dest') or '', 'extra_rack': r.get('plan_extra') or '',
           'parked_in': r.get('plan_park') or '', 'staff_recorded_at': r.get('rack_id') or r.get('box') or '',
           'placed_by_staff': bool(r.get('placed'))}
    if r.get('plan_tabs'):
        out['avg_tablets_per_month'] = round(float(r['plan_tabs']), 1)
    if r.get('mark'):
        out['diabetic_or_bp'] = {'D': 'diabetic', 'B': 'BP / heart'}.get(r['mark'], r['mark'])
    return out


def _rank(term, rows):
    t = re.sub(r'[^A-Z0-9]', '', term.upper())

    def key(r):
        p = re.sub(r'[^A-Z0-9]', '', str(r.get('product', '')).upper())
        return (0 if p == t else 1 if p.startswith(t) else 2, not r.get('placed'), str(r.get('product', '')))
    return sorted(rows, key=key)


# ---------------------------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------------------------
def rack_find_product(product, branch=None, limit=8):
    """Which rack or bin is a product in (or planned for), in each branch."""
    def go():
        term = str(product or '').strip()
        if not term:
            return {'error': 'Which product?'}
        names = _branches()
        ids = _pick_branches(branch)
        cards = _cards(next(iter(names)))
        out = []
        for bid in ids:
            res = _get('/api/search', '?q=' + urllib.parse.quote(term), branch=bid)
            rows = _rank(term, res.get('rows') or [])
            has_plan = bool((cards.get(bid) or {}).get('plan'))
            out.append({'branch': names.get(bid, bid), 'matches': len(rows),
                        'products': [_row(r, has_plan) for r in rows[:max(1, min(int(limit or 8), 15))]]})
        pv = _get('/api/plan_view', branch=ids[0])
        return {'query': term, 'branches': out, 'plan_built': pv.get('generated', ''),
                'note': 'planned_place is where the PLAN says the product lives; placed_by_staff says whether staff have recorded putting it there. '
                        'A PARK code is a virtual holding list, not a physical place.'}
    return _call(go)


def _kind_of_rack(code):
    m = re.match(r'^(.*)-(RACK|EXTRA)\d+$', code)
    if not m:
        return 'other'
    if m.group(2) == 'EXTRA':
        return 'extra'
    head = m.group(1)
    return 'letter' if len(head) == 1 else head.lower()


def _branch_counts(bid, name, card, letter):
    dests = _get('/api/plan_dests', branch=bid)
    pv = _get('/api/plan_view', branch=bid)
    parked = _get('/api/plan_parked', branch=bid)
    bins = dests.get('bins') or []
    racks = dests.get('racks') or []
    extra = [c for c in (pv.get('racks') or {}) if '-EXTRA' in c]
    by_sec = {}
    for c in bins:
        by_sec[c.split(' ')[0]] = by_sec.get(c.split(' ')[0], 0) + 1
    by_kind = {}
    for c in racks:
        k = _kind_of_rack(c)
        by_kind[k] = by_kind.get(k, 0) + 1
    park_bins = sum(len(v) for v in (parked.get('parked') or {}).values())
    row = {'branch': name, 'rack_plan': 'built' if card.get('plan') else 'not built yet for this branch', 'plastic_bins': len(bins), 'bins_by_section': by_sec, 'racks': len(racks), 'racks_by_kind': by_kind,
           'extra_racks': len(extra), 'virtual_parking_bins': park_bins, 'parked_products': parked.get('total', 0),
           'products_planned': card.get('plan') or None,
           'products_placed_by_staff_so_far': card.get('placed', 0)}
    if letter:
        L = letter.strip().upper()[:1]
        sec_names = {'Allopathic': 'ALLO', 'Ayurvedic': 'AYUR'}
        lb = []
        for sec, letters in (pv.get('bins') or {}).items():
            for num, items in (letters.get(L) or {}).items():
                lb.append((sec_names.get(sec, sec), int(num) if str(num).isdigit() else 0, len(items), num))
        lb.sort()
        row['letter'] = L
        row['letter_bins'] = [{'code': f'{s} {L}-BIN{n}', 'products': c, 'kind': (pv.get('kinds') or {}).get(f'{s} {L}-BIN{n}', '')} for s, _, c, n in lb]
        row['letter_racks'] = [{'code': c, 'products': len(i)} for c, i in (pv.get('racks') or {}).items()
                               if re.match(rf'^{re.escape(L)}-(RACK|EXTRA)\d+$', c)]
        row['letter_virtual_parking_bins'] = len((parked.get('parked') or {}).get(L) or {})
    return row


def rack_counts(branch=None, letter=None):
    """How many plastic bins and racks each branch has (and, with a letter, which ones that letter uses)."""
    def go():
        names = _branches()
        ids = _pick_branches(branch)
        cards = _cards(next(iter(names)))
        rows = [_branch_counts(b, names.get(b, b), cards.get(b) or {}, letter) for b in ids]
        out = {'branches': rows, 'plan_built': _get('/api/plan_view', branch=ids[0]).get('generated', '')}
        if len(rows) > 1:
            out['all_planned_branches_together'] = {'plastic_bins': sum(r['plastic_bins'] for r in rows), 'racks': sum(r['racks'] for r in rows),
                                                    'extra_racks': sum(r['extra_racks'] for r in rows)}
        no_plan = [n for b, n in names.items() if not (cards.get(b) or {}).get('plan')]
        out['note'] = ('plastic_bins are the real bins (slow sellers); virtual_parking_bins are only a holding list for no-sale products, not physical. '
                       'extra_racks are second homes for fast movers and are counted apart from racks.')
        if no_plan and not branch:
            out['branches_with_no_rack_plan_yet'] = no_plan
        return out
    return _call(go)


def _norm(s):
    return re.sub(r'[^A-Z0-9]', '', str(s).upper())


def _places(bid):
    """{code: (kind, items, parked?)} for a branch, from the Rack & Bin plan and the parking list."""
    pv = _get('/api/plan_view', branch=bid)
    parked = _get('/api/plan_parked', branch=bid)
    out = {}
    sec_names = {'Allopathic': 'ALLO', 'Ayurvedic': 'AYUR'}
    for sec, letters in (pv.get('bins') or {}).items():
        for letter, nums in letters.items():
            for num, items in nums.items():
                out[f'{sec_names.get(sec, sec)} {letter}-BIN{num}'] = ('bin', items, False)
    for code, items in (pv.get('racks') or {}).items():
        out[code] = ('extra rack' if '-EXTRA' in code else 'rack', items, False)
    for letter, nums in (parked.get('parked') or {}).items():
        for num, items in nums.items():
            out[f'PARK {letter}-{num}'] = ('virtual parking list', items, True)
    return out, pv.get('names') or {}, pv.get('kinds') or {}


def rack_contents(place, branch=None, limit=40):
    """The products in one bin or rack (by code like 'ALLO A-BIN3' or 'A-RACK2', or the name the admin gave it)."""
    def go():
        q = _norm(place)
        if not q:
            return {'error': 'Which bin or rack?'}
        names = _branches()
        out = []
        n = max(1, min(int(limit or 40), MAX_ITEMS))
        for bid in _pick_branches(branch):
            places, given_names, kinds = _places(bid)
            norm_codes = {_norm(c): c for c in places}
            by_name = {_norm(v): c for c, v in given_names.items() if v}
            hits = [norm_codes[q]] if q in norm_codes else [by_name[q]] if q in by_name else \
                sorted(c for nc, c in norm_codes.items() if nc.endswith(q) and len(q) >= 4)
            if not hits:
                close = difflib.get_close_matches(q, list(norm_codes), n=4, cutoff=0.7)
                out.append({'branch': names.get(bid, bid), 'found': False, 'did_you_mean': [norm_codes[c] for c in close]})
                continue
            if len(hits) > 3:
                out.append({'branch': names.get(bid, bid), 'found': False, 'ambiguous': hits[:10],
                            'note': 'that fits many places - ask which one (give the full code, e.g. "ALLO A-BIN3")'})
                continue
            for code in hits[:3]:
                kind, items, parked = places[code]
                rows = []
                for it in items[:n]:
                    if parked:
                        rows.append({'product': it[0], 'pack': it[1], 'why_parked': 'no sale' if it[2] == 'NS' else f'duplicate - use {it[3]}'})
                    else:
                        rows.append({'product': it[0], 'pack': it[1], 'avg_per_month': it[2], 'unit': 'strips' if it[4] == 'S' else 'packs',
                                     **({'diabetic_or_bp': {'D': 'diabetic', 'B': 'BP / heart'}.get(it[3], it[3])} if it[3] else {}),
                                     'placed_by_staff': bool(it[7]), 'moved_in_by_hand': bool(it[6])})
                out.append({'branch': names.get(bid, bid), 'found': True, 'code': code, 'name': given_names.get(code, ''), 'kind': kind,
                            'reserved_for': 'diabetic / BP products' if kinds.get(code) == 'DBP' else '',
                            'products': len(items), 'shown': len(rows), 'more': max(0, len(items) - len(rows)), 'items': rows})
        return {'query': place, 'results': out}
    return _call(go)


# ---------------------------------------------------------------------------------------------
# What ask.py sees
# ---------------------------------------------------------------------------------------------
_B = {'branch': {'type': 'string', 'description': "Optional: 'Shivaji Chowk' or 'Hospet Road'. Leave empty for every branch that has a rack plan."}}
_SPECS = [
    {'name': 'rack_find_product', 'fn': rack_find_product,
     'description': ("[RACK & BIN] Which rack or bin a product is in - or planned for - in each branch (Rack Management app). Result 'where' says it "
                     "in words: planned place, whether staff have actually placed it, extra rack, parking list. Use for 'which rack is X in', "
                     "'where is X kept', 'which bin has X'."),
     'parameters': {'type': 'object', 'properties': {'product': {'type': 'string', 'description': 'product name as said, e.g. "Ecosprin AV 75"'}, **_B},
                    'required': ['product']}},
    {'name': 'rack_counts', 'fn': rack_counts,
     'description': ("[RACK & BIN] How many plastic bins and racks each branch has (also extra racks and the virtual parking list), from the rack plan. "
                     "With `letter`, the bins and racks that letter uses and how many products are in each. Use for 'how many bins / plastic boxes / "
                     "racks in total'."),
     'parameters': {'type': 'object', 'properties': {**_B, 'letter': {'type': 'string', 'description': 'Optional first letter, e.g. "A"'}}, 'required': []}},
    {'name': 'rack_contents', 'fn': rack_contents,
     'description': ("[RACK & BIN] The products in one bin or rack, by code (\"ALLO A-BIN3\", \"A-RACK2\", \"H1-RACK1\", \"PARK A-3\") or the name the "
                     "admin gave it. Use for 'what is in bin A-3', 'what is on the H1 rack'."),
     'parameters': {'type': 'object', 'properties': {'place': {'type': 'string'}, **_B, 'limit': {'type': 'integer'}}, 'required': ['place']}},
]
_BY_NAME = {s['name']: s for s in _SPECS}


def tool_specs():
    """The rack_* tools in the shape of ask_tools.TOOLS entries - empty when rack lookups are not configured."""
    if not _settings()[0]:
        return []
    return [{'name': s['name'], 'description': s['description'], 'parameters': s['parameters']} for s in _SPECS]


def is_rack_tool(name):
    return isinstance(name, str) and name.startswith(PREFIX)


def run(name, args):
    """Run one rack_* call. Always returns a dict; failures are errors the model can explain."""
    spec = _BY_NAME.get(name)
    if spec is None:
        return {'error': f'tool {name} is not a rack tool'}
    if not _settings()[0]:
        return {'error': 'Rack Management is not connected to this page.'}
    try:
        return spec['fn'](**(args if isinstance(args, dict) else {}))
    except TypeError as e:
        return {'error': f'bad arguments for {name}: {e}'}


def reset_cache():
    """For tests."""
    with _lock:
        _cookies.clear()
        _cache.clear()

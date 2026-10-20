"""Acceptance checks for the MRP basket ("lekka madu"): mrp_basket.py, the instant path in
ask.basket_reply, the /api/basket/* routes, the speech filter and the model's basket_* tools.

Real catalog (ask_tools._code_catalog) for the products; the model and speech-to-text are
scripted fakes, nothing goes out to the network. Run with the reckoner's venv:
    venv\\Scripts\\python.exe test_mrp_basket.py
"""
import copy
import json
import os
import random
import secrets
import sys
import types
import urllib.parse
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8')
os.environ.setdefault('OPENROUTER_API_KEY', 'sk-test-not-real')
os.environ['OPENROUTER_MODEL_FAST'] = 'fake/fast-model'
os.environ['OPENROUTER_MODEL_REASONING'] = 'fake/reasoning-model'
for v in ('ORDER_ASK_URL', 'ORDER_ASK_KEY'):
    os.environ.pop(v, None)

import openai  # noqa: E402
import ask  # noqa: E402
import ask_limits  # noqa: E402
import ask_tools as T  # noqa: E402
import mrp_basket as B  # noqa: E402
import server  # noqa: E402
import spoken_numbers as S  # noqa: E402

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'   <-- {detail}'))
    if not cond:
        FAILS.append(name)


def owner():
    return 'tok-' + secrets.token_hex(6)


def say(o, text, **kw):
    """What the owner hears for `text`, through the same door the Ask page uses (ask.basket_reply)."""
    r = ask.basket_reply(text, o) if not kw else B.interpret(o, text, **kw)
    return r


def D(x):
    return Decimal(str(x))


by_key, by_code = T._code_catalog()
fm = T._factor_map()
sales = T._load()[0]
pop = sales['Product'].astype('string').value_counts()


def line_total(cents, units, name):
    """The right answer, worked out independently of the code under test: exact decimal, half-up to the paisa."""
    f = Decimal(str(float(fm.get(name, 1) or 1)))
    return float((Decimal(cents) / 100 * Decimal(str(units)) / f).quantize(Decimal('0.01'), ROUND_HALF_UP))


# --- products to test with, picked from the real catalog ---------------------------------------
uniq = sorted(((k, v[0]) for k, v in by_key.items() if len(v) == 1 and k[0].isalpha() and 1000 <= k[1] <= 9999 and k[1] % 100 and k[1] % 10
                and float(fm.get(v[0], 1) or 1) > 1), key=lambda kv: -pop.get(kv[1], 0))
picks, seen = [], set()
for (code, cents), name in uniq:
    if name not in seen and 30 % int(float(fm.get(name, 1))) == 0:
        picks.append((code, cents, name))
        seen.add(name)
    if len(picks) == 4:
        break
check('found 4 unique real products to test with', len(picks) == 4, picks)


def code_text(code, cents):
    return f'{code.lower()} {cents / 100:.2f}'


(c1, m1, n1), (c2, m2, n2), (c3, m3, n3), (c4, m4, n4) = picks
units = [30, 30, 30, 30]
expected_lines = [line_total(m, u, n) for (c, m, n), u in zip(picks, units)]


# ======================================================================================
# 1. one item: unique code, the one short line, strips recalled from the strip size
# ======================================================================================
o = owner()
r = say(o, f'{code_text(c1, m1)}, 30')
f1 = int(float(fm.get(n1)))
check('unique code: found, the product is named', r and r['ok'] and B.speak_name(n1) in r['say'], r)
check('unique code: 30 tablets -> strips recalled from the strip size', f'{30 // f1} strip' in r['say'], (r['say'], f1))
check('unique code: this line\'s MRP is said in rupees and paise', B.money(expected_lines[0]) in r['say'], (r['say'], expected_lines[0]))
check('ONE short line, no grand total yet', r['say'].count('.') <= 2 and 'otal' not in r['say'], r['say'])
check('...and the snapshot holds no grand total until asked', r['basket']['grand_total_mrp'] is None and r['basket']['count'] == 1)
it = r['basket']['items'][0]
check('snapshot line is priced by the same maths as get_mrp_total', abs(it['mrp_total'] - expected_lines[0]) < 0.011, it)

# 2. "2 strips" = strips x strip size, same product, same money
r2 = say(owner(), f'{code_text(c1, m1)}, {30 // f1} strips')
check('"N strips" prices the same as the matching tablets', r2['basket']['items'][0]['mrp_total'] == it['mrp_total'] and r2['basket']['items'][0]['units'] == 30, r2['basket'])

# ======================================================================================
# 3. crowded code: best seller first, others named in one line, "change last" walks them
# ======================================================================================
(ccode, ccents), cnames = max(((k, v) for k, v in by_key.items() if k[0].isalpha()), key=lambda kv: len(kv[1]))
o = owner()
r = say(o, f'{ccode.lower()} {ccents / 100:.2f}, 15')
check('crowded code: the best-selling candidate is used', r['basket']['items'][0]['product'] == cnames[0].strip(), (r['basket']['items'][0]['product'], cnames[:3]))
check('crowded code: the line says it was the best seller of N and names others', 'Best seller of' in r['say'] and 'Others:' in r['say'], r['say'])
check('crowded code: still ONE short line (two sentences at most besides the others)', r['say'].count('\n') == 0 and len(r['say']) < 260, r['say'])
kept = min(len(cnames), B.MAX_CANDIDATES)
walked = [r['basket']['items'][0]['product']]
repriced = [r['basket']['items'][0]['mrp_total'] == line_total(ccents, 15, cnames[0])]
for k in range(1, kept + 1):
    rc = say(o, 'change last')
    walked.append(rc['basket']['items'][0]['product'])
    if k < kept:
        repriced.append(rc['basket']['items'][0]['mrp_total'] == line_total(ccents, 15, cnames[k]) and rc['basket']['items'][0]['units'] == 15)
check('change last: walks the candidates in best-seller order', walked[:kept] == [n.strip() for n in cnames[:kept]], (walked, cnames[:kept]))
check('change last: after the last candidate it goes back to the first', walked[kept] == walked[0], walked)
o2 = owner()
say(o2, f'{ccode.lower()} {ccents / 100:.2f}, 15')
check('change last says "Match 2 of N"', f'Match 2 of {kept}' in say(o2, 'change last')['say'])
check('change last re-prices every candidate with its own strip size (same 15 tablets, same pack MRP)', all(repriced), repriced)
o3 = owner()
say(o3, f'{code_text(c1, m1)}, 30')
rr = say(o3, 'change last')
check('change last with one candidate says so and changes nothing', rr['ok'] is False and 'Only one match' in rr['say'] and rr['basket']['count'] == 1, rr)

# ======================================================================================
# 4. wrong MRP -> says so, shows the closest MRPs, asks to repeat; nothing added
# ======================================================================================
o = owner()
r = say(o, f'{c1.lower()} {(m1 + 777) / 100:.2f}, 30')
check('wrong MRP: not found, nothing added', r['ok'] is False and r['basket']['count'] == 0, r)
check('wrong MRP: asks to say it again', 'Please say it again' in r['say'] or 'again' in r['say'], r['say'])
r = say(o, f'{c1.lower()} {(m1 + 1) / 100:.2f}, 30')
check('MRP one paisa off: the closest real MRP is shown', r['ok'] is False and f'{m1 / 100:.2f}' in r['say'], r['say'])
r = say(o, f'{c1.lower()} {round(m1 / 100)}, 30')
check('MRP rounded to whole rupees does not pretend to match', not (r['ok'] and B.speak_name(n1) in r['say']), r)
r = say(o, 'zqx 12.34, 30')
check('a code nobody has: not found, no crash', r['ok'] is False and 'No match' in r['say'], r)

# ======================================================================================
# 5. whole-rupee number that is also a strength: "Pan 40"
# ======================================================================================
o = owner()
r = say(o, 'pan 40, 30')
it = r['basket']['items'][0] if r['basket']['items'] else {}
check('"Pan 40": found (as the name + strength when no pack costs exactly Rs 40.00)', r['ok'] and 'PAN' in it.get('product', '').upper() and '40' in it.get('product', ''), r)
both = None
for (c3_, cs), names in by_key.items():
    if cs % 100 == 0 and c3_.isalpha():
        ob = owner()
        rb = say(ob, f'{c3_.lower()} {cs // 100}, 10')
        if rb and rb['ok'] and rb['basket']['items'] and rb['basket']['items'][0]['matches'] > len(names) and B.MSG['en']['others'] is not None:
            both = (c3_, cs, ob, rb)
            break
check('a whole-rupee code that is also a name+strength keeps both readings (one "change last" away)', both is not None)
if both:
    c3_, cs, ob, rb = both
    first = rb['basket']['items'][0]['product']
    seen_names = {first}
    for _ in range(rb['basket']['items'][0]['matches']):
        seen_names.add(say(ob, 'change last')['basket']['items'][0]['product'])
    alt = T.mrp_resolve(f'{c3_} {cs // 100}')['also_possible_by_name']
    check('...and "change last" reaches the by-name reading', alt is not None and alt['product'].strip() in seen_names, (alt, seen_names))

# ======================================================================================
# 5b. brand codes that are ALSO command or filler words (real catalog: ADD ALL AND CAN FOR NEW NOT ONE SIR SUM THE YES)
# ======================================================================================
CLASH = {'add', 'all', 'and', 'can', 'for', 'new', 'not', 'one', 'sir', 'sum', 'the', 'yes'}
clash = {}
for (cc, cs), names in sorted(by_key.items()):
    if cc.lower() in CLASH and cs % 100 and cc not in clash:
        clash[cc] = (cs, names[0].strip())
check('the catalog really has brand codes that are also command/filler words', len(clash) >= 8, sorted(clash))
bad = []
for cc, (cs, pname) in clash.items():
    for prefix in ('', 'add ', 'subbi, '):
        for opened in (False, True):
            o = owner()
            if opened:
                B.basket_add(o, code_text(c1, m1), units=30)
            r = ask.basket_reply(f'{prefix}{cc.lower()} {cs / 100:.2f}, 10', o)
            if not (r and r['ok'] and r['basket']['count'] == (2 if opened else 1) and r['basket']['items'][-1]['product'] == pname):
                bad.append((prefix, cc, opened, r and r['say']))
check(f'{len(clash)} command/filler-word brand codes still price as products (with/without "add", basket open or not)', not bad, bad[:3])
check('a command never carries a number: "sum 85.00, 10" is not the command "sum"', B.match_command('sum 85.00 10') is None and B.match_command('total 5') is None and B.match_command('sum') is not None)
o = owner()
B.basket_add(o, code_text(c1, m1), units=30)
check('...but the bare words still are commands', ask.basket_reply('sum', o)['action'] == 'total' and ask.basket_reply('new bill', o)['action'] == 'clear')
check('absurdly long numbers are refused politely, no crash',
      ask.basket_reply('eco ' + '9' * 50 + ', 30', owner()) is None and ask.basket_reply('eco 65.60, ' + '9' * 4000, owner()) is None)
crashes = []
for junk in ['', '   ', ',,,', '....', '65.60', '30', 'ಇಕೋ', 'a' * 5000, '9' * 5000, '‌', 'eco\n65.60,\n30',
             'eco 65.60, 30, 40, 50', 'eco , , 30', 'point point point']:
    try:
        B.classify(o, junk)
        B.interpret(o, junk)
        S.numberize(junk)
    except Exception as e:
        crashes.append((junk[:20], repr(e)))
check('a pile of junk never crashes the parser', not crashes, crashes)

# ======================================================================================
# 6. a multi-item sequence ending with each trigger word gives the right grand total
# ======================================================================================
want = sum((D(x) for x in expected_lines), D(0))
TRIGGERS_EN = ['calculate', 'Calculate.', 'total', 'Total.', 'MRP total', 'mrp total', 'M R P total', 'what is the total', "what's the total",
               'grand total', 'calculate the total', 'add up', 'subbi calculate', 'hey subbiii, total']
TRIGGERS_KN = ['lekka madu', 'lekka maadu', 'lekkamadu', 'Lekka Maadu.', 'lekha madu', 'leka madu', 'lekka maadi', 'lekka haaku', 'ಲೆಕ್ಕ ಮಾಡು', 'ಲೆಕ್ಕ ಮಾಡಿ',
               'ಲೆಕ್ಕಮಾಡು', 'ಲೆಕ್ಕ ಹಾಕು', 'ಒಟ್ಟು']
ok_all, bad = True, []
for trig in TRIGGERS_EN + TRIGGERS_KN:
    o = owner()
    for (c, m, n), u in zip(picks[:3], units[:3]):
        r = say(o, f'{code_text(c, m)}, {u}')
        if not r or not r['ok'] or 'otal' in r['say']:
            ok_all = False
            bad.append(('add', trig, r))
    t = say(o, trig)
    kn = trig in TRIGGERS_KN
    expect_money = B.money(sum((D(x) for x in expected_lines[:3]), D(0)), 'kn' if kn else 'en')
    if not (t and t['action'] == 'total' and abs(t['grand_total_mrp'] - float(sum((D(x) for x in expected_lines[:3]), D(0)))) < 0.005
            and expect_money in t['say'] and all(B.speak_name(n) in t['say'] for (_, _, n) in picks[:3])
            and ('ಒಟ್ಟು' in t['say'] if kn else 'Grand total' in t['say']) and t['basket']['count'] == 3):
        ok_all = False
        bad.append((trig, t))
check(f'{len(TRIGGERS_EN) + len(TRIGGERS_KN)} trigger words each give every line + the right grand total, in the language spoken', ok_all, bad[:2])

# the lines read out at the total are exactly the per-item lines
o = owner()
adds = [say(o, f'{code_text(c, m)}, {u}')['say'] for (c, m, n), u in zip(picks[:3], units[:3])]
t = say(o, 'calculate')
check('"calculate" reads every item with its line total, then the grand total', all(a in t['say'] for a in adds) and t['say'].index('Grand total') > t['say'].index(adds[-1]), t['say'])
check('the basket is kept after totalling', t['basket']['count'] == 3 and t['basket']['totalled'] and t['basket']['grand_total_mrp'] == t['grand_total_mrp'])
t2 = say(o, 'total')
check('totalling twice gives the same answer', t2['say'] == t['say'])
r = say(o, f'{code_text(c4, m4)}, 30')
check('adding after a total keeps the basket and un-totals it', r['basket']['count'] == 4 and not r['basket']['totalled'] and r['basket']['grand_total_mrp'] is None)
t3 = say(o, 'calculate')
check('...and the new total includes the new item', abs(t3['grand_total_mrp'] - float(sum((D(x) for x in expected_lines), D(0)))) < 0.005, t3['grand_total_mrp'])

# the grand total equals the existing tool's, item for item
gt = T.get_mrp_total([{'product': code_text(c, m), 'units': u} for (c, m, n), u in zip(picks, units)])
check('grand total equals get_mrp_total for the same items', abs(gt['grand_total_mrp'] - t3['grand_total_mrp']) < 0.005, (gt['grand_total_mrp'], t3['grand_total_mrp']))

# ======================================================================================
# 7. undo / remove last / change last / read list / new bill / clear
# ======================================================================================
o = owner()
for (c, m, n), u in zip(picks[:3], units[:3]):
    say(o, f'{code_text(c, m)}, {u}')
rl = say(o, 'read list')
check('read list: every item, numbered, no grand total', rl['action'] == 'list' and all(B.speak_name(n) in rl['say'] for (_, _, n) in picks[:3]) and 'otal' not in rl['say'], rl['say'])
r = say(o, 'undo')
check('undo removes the last item', r['basket']['count'] == 2 and B.speak_name(n3) in r['say'] and r['basket']['items'][-1]['product'] == n2.strip(), r)
r = say(o, 'remove last')
check('"remove last" removes the next-to-last', r['basket']['count'] == 1 and r['basket']['items'][0]['product'] == n1.strip(), r)
t = say(o, 'calculate')
check('the total after removals counts only what is left', abs(t['grand_total_mrp'] - expected_lines[0]) < 0.005, t)
r = say(o, 'undo')
r = say(o, 'undo')
check('undo on an empty basket says it is empty (no crash)', r['ok'] is False and 'empty' in r['say'], r)
check('calculate on an empty basket says it is empty', say(o, 'calculate')['say'] == 'The basket is empty.')
for word in ('new bill', 'clear', 'New bill.', 'clear all', 'ಹೊಸ ಬಿಲ್'):
    o = owner()
    say(o, f'{code_text(c1, m1)}, 30')
    say(o, f'{code_text(c2, m2)}, 30')
    r = say(o, word)
    check(f'"{word}" empties the basket', r['basket']['count'] == 0 and r['action'] == 'clear', r)
o = owner()
say(o, f'{code_text(c1, m1)}, 30')
say(o, 'calculate')
r = say(o, 'new bill')
check('new bill after a total starts a clean basket', r['basket']['count'] == 0 and not r['basket']['totalled'])
check('...and the next item starts at 1', say(o, f'{code_text(c2, m2)}, 30')['basket']['items'][0]['n'] == 1)
for word in ('ಕೊನೆಯದು ತೆಗೆ', 'koneyadu tege'):
    o = owner()
    say(o, f'{code_text(c1, m1)}, 30')
    say(o, f'{code_text(c2, m2)}, 30')
    r = say(o, word)
    check(f'Kannada "{word}" removes the last item', r['basket']['count'] == 1 and r['basket']['items'][0]['product'] == n1.strip(), r)
o = owner()
say(o, f'{code_text(c1, m1)}, 30')
r = say(o, 'ಲೆಕ್ಕ ಮಾಡು')
r2 = say(o, f'{code_text(c2, m2)}, 30')
check('Kannada is remembered: the next item line is in Kannada too', 'ಸ್ಟ್ರಿಪ್' in r2['say'], r2['say'])
r3 = say(o, 'undo')
check('an English command switches back to English', 'Removed' in r3['say'], r3['say'])

# ======================================================================================
# 8. two logins: separate baskets, through the real routes
# ======================================================================================
cl = server.app.test_client()
tokA, tokB = server._create_session('login-A'), server._create_session('login-B')
HA, HB = {'X-Session-Token': tokA}, {'X-Session-Token': tokB}


def post(path, body, h):
    r = cl.post(path, data=json.dumps(body), headers=dict(h, **{'Content-Type': 'application/json'}))
    return r.status_code, r.get_json()


st, a1 = post('/api/basket/say', {'text': f'{code_text(c1, m1)}, 30'}, HA)
st, a2 = post('/api/basket/say', {'text': f'{code_text(c2, m2)}, 30'}, HA)
st, b1 = post('/api/basket/say', {'text': f'{code_text(c3, m3)}, 30'}, HB)
check('route: login A has its two items', a2['basket']['count'] == 2 and [i['product'] for i in a2['basket']['items']] == [n1.strip(), n2.strip()], a2)
check('route: login B has only its own item', b1['basket']['count'] == 1 and b1['basket']['items'][0]['product'] == n3.strip(), b1)
st, ga = post('/api/basket/total', {}, HA)
st, gb = post('/api/basket/total', {}, HB)
check('route: each login totals only its own basket', abs(ga['grand_total_mrp'] - (expected_lines[0] + expected_lines[1])) < 0.011 and abs(gb['grand_total_mrp'] - expected_lines[2]) < 0.011, (ga, gb))
post('/api/basket/clear', {}, HB)
st, sa = cl.get('/api/basket', headers=HA).status_code, cl.get('/api/basket', headers=HA).get_json()
check('route: B clearing its basket does not touch A\'s', sa['basket']['count'] == 2, sa)
st, sb = 200, cl.get('/api/basket', headers=HB).get_json()
check('route: B\'s basket is empty after B clears', sb['basket']['count'] == 0)
# the same user logged in twice is two sessions = two baskets
tokA2 = server._create_session('login-A')
check('route: a second session of the same user starts with its own empty basket', cl.get('/api/basket', headers={'X-Session-Token': tokA2}).get_json()['basket']['count'] == 0)
# through the voice door (/api/ask) the same token owns the same basket
fake_calls = []
real_model, real_check = server.ask_mod.ask, ask_limits.check
server.ask_mod.ask = lambda q, **kw: (fake_calls.append((q, kw.get('owner'))), 'MODEL ANSWER')[1]
counted = []
ask_limits.check = lambda *a, **k: counted.append(1)
st, v = post('/api/ask', {'question': 'calculate', 'speak': True}, HA)
check('/api/ask: the voice path reaches the same basket A built', v['answer'].count('Grand total') == 1 and abs(v['basket']['grand_total_mrp'] - (expected_lines[0] + expected_lines[1])) < 0.011, v)
st, v = post('/api/ask', {'question': 'calculate', 'speak': True}, HB)
check('/api/ask: login B asking "calculate" gets ITS (empty) basket, not A\'s', v['answer'] == 'The basket is empty.', v)
# no auth, no basket
check('route: no session token -> 401', cl.get('/api/basket').status_code == 401 and cl.post('/api/basket/say', data='{}', content_type='application/json').status_code == 401)
check('route: an unknown action is a 404, not a crash', post('/api/basket/format-disk', {}, HA)[0] == 404)
check('basket functions refuse a missing login', B.basket_add(None, 'eco 65.60', units=30)['ok'] is False and B.basket_total('')['ok'] is False and B.snapshot(None)['count'] == 0)
# logout and expiry delete the basket
st, _ = post('/api/logout', {}, HA)
check('logout deletes that login\'s basket', not B.is_open(tokA) and B.snapshot(tokA)['count'] == 0)
tokE = server._create_session('login-E')
B.basket_add(tokE, code_text(c1, m1), units=30)
server._sessions[tokE]['last_active'] -= server.SESSION_TIMEOUT_SECONDS + 5
check('an expired session is refused...', not server._touch_session(tokE))
check('...and takes its basket with it', B.snapshot(tokE)['count'] == 0)
check('B (still logged in) kept its session', server._touch_session(tokB))

# ======================================================================================
# 9. basket lines do not use up the question cap, and never reach the model
# ======================================================================================
fake_calls.clear()
counted.clear()
tokC = server._create_session('login-C')
HC = {'X-Session-Token': tokC}
codes = [code_text(c, m) for c, m, n in picks]
statuses = []
for i in range(30):
    statuses.append(post('/api/ask', {'question': f'{codes[i % 4]}, 30', 'speak': True}, HC)[0])
post('/api/ask', {'question': 'calculate'}, HC)
post('/api/ask', {'question': 'new bill'}, HC)
check('30 basket lines + total + new bill: none refused, none counted against the daily/burst cap', set(statuses) == {200} and counted == [], (set(statuses), counted))
check('...and none of them reached the model', fake_calls == [], fake_calls)
st, v = post('/api/ask', {'question': 'which branch sells the most?', 'speak': True}, HC)
check('an ordinary question still goes to the model (counted once), with the login as owner',
      fake_calls == [('which branch sells the most?', tokC)] and len(counted) == 1 and v['answer'] == 'MODEL ANSWER', (fake_calls, counted, v))
check('an ordinary NAME item with no basket open still goes to the model as before',
      post('/api/ask', {'question': 'Ecosprin AV 75, 30'}, HC)[1]['answer'] == 'MODEL ANSWER')
server.ask_mod.ask, ask_limits.check = real_model, real_check

# ======================================================================================
# 10. the speech filter lets basket words through
# ======================================================================================
gate_calls = []
real_gate, real_tr, real_warm = server._speech_gate, server._transcribe, server.warm_ask_cache_in_background
server.warm_ask_cache_in_background = lambda: None
server._speech_gate = lambda t, la: (gate_calls.append(t), ('NO', 0.0))[1]      # the gate would reject everything it sees


def heard(text, h, last=''):
    server._transcribe = lambda audio: (text, 0.0, 'm')
    hh = dict(h, **{'Content-Type': 'audio/wav', 'X-Voice-Context': urllib.parse.quote(last)})
    return cl.post('/api/speech/transcribe?gate=1', data=b'RIFF' + b'\0' * 64, headers=hh).get_json()


tokS = server._create_session('login-S')
HS = {'X-Session-Token': tokS}
B.basket_add(tokS, code_text(c1, m1), units=30)       # a basket is open
swallowed = []
for w in ['calculate', 'total', 'Total.', 'lekka madu', 'lekka maadu', 'undo', 'clear', 'ಲೆಕ್ಕ ಮಾಡು', 'mrp total', 'new bill', 'remove last',
          'change last', 'read list', 'list', 'ಒಟ್ಟು', 'subbi calculate', 'okay total']:
    gate_calls.clear()
    j = heard(w, HS)
    if not (j['addressed'] and not j['short'] and not j['unclear'] and gate_calls == []):
        swallowed.append((w, j, list(gate_calls)))
check('while a basket is open, every trigger word gets past the one-or-two-word stray filter (and costs no gate call)', not swallowed, swallowed[:2])
tokN = server._create_session('login-N')
HN = {'X-Session-Token': tokN}
strong_ok = [w for w in ['calculate', 'lekka madu', 'mrp total', 'new bill', 'ಲೆಕ್ಕ ಮಾಡು'] if heard(w, HN)['addressed']]
check('with NO basket, the unmistakable ones ("calculate", "lekka madu", "mrp total", "new bill") still get through', len(strong_ok) == 5, strong_ok)
weak = [w for w in ['total', 'clear', 'reset', 'sum'] if heard(w, HN)['short'] and not heard(w, HN)['addressed']]
check('with NO basket, a lone "total"/"clear" is still just a stray word (hmm)', weak == ['total', 'clear', 'reset', 'sum'], weak)
check('"undo" is unmistakable, so it is answered even with no basket ("the basket is empty")', heard('undo', HN)['addressed'])
j = heard('okay', HS)
check('a plain "okay" is still a stray word even with a basket open', j['short'] and not j['addressed'], j)
j = heard('haan haan', HS)
check('"haan haan" is still a stray word', j['short'] and not j['addressed'], j)
j = heard('subbiii', HN)
check('calling the name is still addressed (greeting), not a stray word', j['addressed'] and not j['short'], j)
gate_calls.clear()
j = heard(f'{code_text(c1, m1)}, 30', HN)
check('an item ("eco 65.60, 30") is addressed with no gate call, even with no basket yet', j['addressed'] and not j['short'] and gate_calls == [], (j, gate_calls))
for spoken in ['eco sixty five point six zero thirty', 'E C O 65 60 30', f'{c1.lower()} {S.words_to_digits(str(m1 // 100))} {m1 % 100:02d} 30']:
    gate_calls.clear()
    j = heard(spoken, HN)
    check(f'spoken item {spoken!r} is addressed with no gate call', j['addressed'] and gate_calls == [], (j, gate_calls))
j = heard('remove', HS)
check('a half-said command ("remove") with a basket open earns "please repeat"', j['unclear'] and not j['addressed'], j)
j = heard('remove', HN)
check('...but with no basket it is just a stray word', j['short'] and not j['unclear'], j)
last = 'Removed Ecosprin AV 75. 2 items left.'
j = heard('Removed Ecosprin AV 75. 2 items left.', HS, last)
check('the phone hearing its own basket reply is an echo, not a command', j['echo'] and not j['addressed'], j)
server._transcribe = lambda audio: ('where is my dolo order', 0.0, 'm')
gate_calls.clear()
j = cl.post('/api/speech/transcribe?gate=1', data=b'RIFF' + b'\0' * 64, headers=dict(HS, **{'Content-Type': 'audio/wav'})).get_json()
check('a real question with a basket open still goes through the paid gate as before', gate_calls == ['where is my dolo order'] and j['addressed'] is False, (gate_calls, j))
j = heard('total sales', HS)
check('a two-word question fragment ("total sales") is still just "hmm" with a basket open, not "please repeat"', j['short'] and not j['unclear'] and not j['addressed'], j)
server._speech_gate, server._transcribe, server.warm_ask_cache_in_background = real_gate, real_tr, real_warm

# the brand letters said in Kannada script are not guessed at: the owner is asked to say them in English
o = owner()
for spoken in ('ಇಕೋ 65.60, 30', 'ಇಕೋ ಅರವತ್ತೈದು ಪಾಯಿಂಟ್ ಆರು ಸೊನ್ನೆ ಮೂವತ್ತು'):
    r = ask.basket_reply(spoken, o)
    check(f'Kannada-script brand letters {spoken!r}: nothing added, asked to say the letters in English', r and r['ok'] is False and 'ಇಂಗ್ಲಿಷ್' in r['say'] and r['basket']['count'] == 0, r)      # answered in Kannada, since Kannada was spoken
    check('...and the speech filter lets it through to that answer (not "ignored")', B.classify(tokS, spoken) == 'bad_letters')
check('Kannada talk that is not an item is left alone', B.classify(tokS, 'ಕಳೆದ ತಿಂಗಳ ಮಾರಾಟ ಎಷ್ಟು') is None and B.classify(tokS, 'ಮೂರು ಹತ್ತು') is None)
check('a call by name must come first: "5 subbi, eco 65.60" keeps its 5', ask.strip_name_call('5 subbi, eco 65.60') == '5 subbi, eco 65.60'
      and ask.strip_name_call('hey subbiii, eco 65.60 30') == 'eco 65.60 30' and ask.strip_name_call('ಸುಬ್ಬಿ ಲೆಕ್ಕ ಮಾಡು') == 'ಲೆಕ್ಕ ಮಾಡು')

# ordinary questions are never taken for basket words, even with a basket open
o = owner()
B.basket_add(o, code_text(c1, m1), units=30)
questions = ['total sales last month', 'what is the total profit', 'list the top 10 customers', 'undo the last order', 'clear stock please',
             'change in sales since January', 'last month sales', 'new products this month', 'calculate profit for dolo', 'next month forecast',
             'read the purchase list', 'how many bills today', 'top 10 customers', 'dolo 650 sales last month', 'pan 40 sales', 'which branch is best',
             'ಕಳೆದ ತಿಂಗಳ ಮಾರಾಟ ಎಷ್ಟು', 'show me dead stock', 'total number of bills', 'is the wholesale channel profitable?']
hijacked = [q for q in questions if ask.basket_reply(q, o) is not None]
check('20 ordinary questions are NOT taken for basket words (basket open)', not hijacked, hijacked)
check('...and the basket is untouched by them', B.snapshot(o)['count'] == 1)

# ======================================================================================
# 11. speech variants of the same MRP resolve to the same product
# ======================================================================================
r_, p_ = divmod(m1, 100)
rw = S.english_words(r_)
pw = ' '.join(S.english_words(int(d)) for d in f'{p_:02d}')
variants = [
    f'{c1.lower()} {r_}.{p_:02d}, 30', f'{c1.upper()} {r_}.{p_:02d}, 30', f'{c1}-{r_}.{p_:02d}, 30', f'{c1.lower()} {r_}.{p_:02d} 30',
    f'{c1.lower()} {r_}{p_:02d}, 30', f'{c1.lower()} {r_} {p_:02d}, 30', f'{c1.lower()} {r_} {p_:02d} 30', f'{c1.lower()} {r_} point {p_:02d} 30',
    f'{c1.lower()} {rw} point {pw}, thirty', f'{c1.lower()} {rw} point {S.english_words(p_)}, thirty', f'{c1.lower()} {rw} {S.english_words(p_)} thirty',
    f"{' '.join(c1)} {r_}.{p_:02d} 30", f'{c1.lower()} {r_}.{p_:02d}, 30 tablets', f'{c1.lower()} {r_}.{p_:02d}, 30 caps', f'{c1.lower()} {r_}.{p_:02d} 30 tabs',
    f'  Add {c1.lower()}   {r_}.{p_:02d} ,  30 please', f'{c1.lower()} rupees {r_} paise {p_:02d} 30', f'{c1.lower()} {r_} rupees {p_:02d} paise, 30',
    f'{c1.lower()} {r_}.{p_:02d}, {30 // f1} strips', f'{c1.lower()} {r_}.{p_:02d}, {S.english_words(30 // f1)} strips',
    f'subbi, {c1.lower()} {r_}.{p_:02d}, 30', ((f'{c1.lower()} {r_}.{p_:02d}, 30').translate({ord(str(d)): chr(0x0CE6 + d) for d in range(10)})),
    ((f'{c1.lower()} {r_}.{p_:02d}, 30').translate({ord(str(d)): chr(0x0966 + d) for d in range(10)})),
]
if p_ % 10 == 0:
    variants += [f'{c1.lower()} {r_}.{p_ // 10}, 30', f'{c1.lower()} {rw} point {S.english_words(p_ // 10)}, thirty']
want_prod, want_total = n1.strip(), expected_lines[0]
bad = []
for v in variants:
    o = owner()
    r = ask.basket_reply(v, o)
    items = r['basket']['items'] if r else []
    if not (r and r['ok'] and len(items) == 1 and items[0]['product'] == want_prod and abs(items[0]['mrp_total'] - want_total) < 0.011):
        bad.append((v, r and r['say']))
check(f'{len(variants)} speech variants of one MRP all resolve to the same product and total', not bad, bad[:3])
ECO = ask.basket_reply('eco 65.60, 30', owner())
eco_ok = ECO['ok'] and ECO['basket']['items'][0]['product'] == 'ECOSPRIN AV 75' and ECO['basket']['items'][0]['mrp_total'] == 131.2 and ECO['basket']['items'][0]['strips'] == 2
check('the owner\'s own example: "eco 65.60, 30" = Ecosprin AV 75, 2 strips, Rs 131.20', eco_ok and ECO['say'].startswith('Ecosprin AV 75, 2 strips, 131 rupees 20 paise.'), ECO)
for spoken in ['eco ಅರವತ್ತೈದು ಪಾಯಿಂಟ್ ಆರು ಸೊನ್ನೆ, ಮೂವತ್ತು', 'eco पैंसठ पॉइंट छह शून्य तीस', 'eco painsath point chhe sifar tees', 'eco ಸಿಕ್ಸ್ಟಿ ಫೈವ್ ಪಾಯಿಂಟ್ ಸಿಕ್ಸ್ ಝೀರೋ ಥರ್ಟಿ',
               'eco sixty five sixty, thirty', 'eco 65 60 2 strips', 'eco 65.6 30', 'eco 6560 30', 'eco 65,60 30', 'eco 65.60,30']:
    r = ask.basket_reply(spoken, owner())
    check(f'Kannada/Hindi/odd form {spoken!r} -> Ecosprin AV 75, 2 strips', bool(r and r['ok'] and r['basket']['items'][0]['product'] == 'ECOSPRIN AV 75' and r['basket']['items'][0]['mrp_total'] == 131.2), r and r['say'])
# a number that can be read two ways: the catalog decides
r = ask.basket_reply('eco 6560 30', owner())
check('"6560" is read as 65.60 because ECO 6560.00 does not exist', r['ok'] and r['basket']['items'][0]['mrp_per_strip'] == 65.6, r)
r = ask.basket_reply('eco 65 60', owner())
check('"eco 65 60" with no quantity: 65.60 is found, and it asks how many (not a wrong product)', r['ok'] is False and 'did not hear how many' in r['say'] and r['basket']['count'] == 0, r)
o = owner()
ask.basket_reply('eco 65 60', o)
r = ask.basket_reply('eco 65 60 30', o)
check('"eco 65 60 30" = 65.60 with 30 tablets', r['ok'] and r['basket']['items'][0]['mrp_total'] == 131.2)
r = ask.basket_reply('das 12.00, 10', owner())
check('a code that is also a number word (DAS = 10 in Hindi) is still read as a brand code', r is not None and 'D A S 12.00' in r['say'], r)
# one reading is a real pack MRP, the other only a name guess: the real pack wins
r = ask.basket_reply('pan 40, 30', owner())
check('"pan 40, 30" is read as quantity 30 of Pan 40 (not as an MRP of 40.30)', r['ok'] and r['basket']['items'][0]['units'] == 30, r)

# ======================================================================================
# 12. normal product names: unchanged outside a basket; an item inside one
# ======================================================================================
nm = T.get_mrp_total([{'product': 'Ecosprin AV 75', 'units': 30}])['items'][0]
check('get_mrp_total by name still gives 2 strips of 15', nm['found'] and nm['strips'] == 2 and 'ECOSPRIN AV 75' in nm['product'].upper(), nm)
o = owner()
check('a product NAME with no basket open is left for the model (nothing is added behind the owner\'s back)', ask.basket_reply('Ecosprin AV 75, 30', o) is None and B.snapshot(o)['count'] == 0)
ask.basket_reply('eco 65.60, 30', o)
r = ask.basket_reply('Ecosprin AV 75, 30', o)
check('...but inside an open basket the same words are the next item', r and r['ok'] and r['basket']['count'] == 2 and r['basket']['items'][1]['strips'] == 2, r)
st, rp = post('/api/basket/say', {'text': 'Ecosprin AV 75, 2 strips'}, HC)
check('the panel route takes a product name too', st == 200 and rp['ok'] and rp['basket']['items'][0]['strips'] == 2 and rp['basket']['items'][0]['units'] == 30, rp)

# ======================================================================================
# 13. the same maths as get_mrp_total over many real products; limits; bad quantities
# ======================================================================================
random.seed(5)
keys = random.sample([k for k in by_key if k[0].isalpha() and 100 <= k[1] <= 50000], 150)
mism = []
for (code, cents) in keys:
    o = owner()
    u = random.choice([10, 14, 15, 20, 30, 60, 100])
    said = f'{code} {cents / 100:.2f}'
    r = B.basket_add(o, said, units=u)
    g = T.get_mrp_total([{'product': said, 'units': u}])['items'][0]
    if not (r['ok'] and g['found'] and r['basket']['items'][0]['product'] == g['product'] and abs(r['basket']['items'][0]['mrp_total'] - g['mrp_total']) < 0.0051):
        mism.append((said, u, r['say'], g.get('product'), g.get('mrp_total')))
check('150 random real code+MRP items: basket line == get_mrp_total line (product and rupees)', not mism, mism[:2])
o = owner()
for bad_qty in (0, -5, 'x', None, 10 ** 9, 7.5):
    r = B.basket_add(o, code_text(c1, m1), units=bad_qty)
    check(f'quantity {bad_qty!r} is refused, nothing added', r['ok'] is False and r['basket']['count'] == 0, r)
r = B.basket_add(o, code_text(c1, m1), strips=1.5)
check('1.5 strips is allowed (a half strip is real)', r['ok'] and r['basket']['items'][0]['units'] == 1.5 * float(fm.get(n1)), r)
o = owner()
for _ in range(B.MAX_ITEMS):
    B.basket_add(o, code_text(c1, m1), units=30)
r = B.basket_add(o, code_text(c1, m1), units=30)
check(f'a basket stops at {B.MAX_ITEMS} items and says so', r['ok'] is False and 'full' in r['say'] and r['basket']['count'] == B.MAX_ITEMS, r['say'])
check('...and a total of that many items still works', B.basket_total(o)['ok'])
t = B.basket_total(o)
check('grand total of 60 identical lines = 60 x the line (no float drift)', abs(t['grand_total_mrp'] - round(expected_lines[0] * B.MAX_ITEMS, 2)) < 0.005, (t['grand_total_mrp'], expected_lines[0] * 60))
check('money is said in rupees and paise', B.money(131.2) == '131 rupees 20 paise' and B.money(1) == '1 rupee' and B.money(0.5) == '50 paise' and B.money(481.8, 'kn') == '481 ರೂಪಾಯಿ 80 ಪೈಸೆ')
check('names are spoken as words, short ones as letters', B.speak_name('ECOSPRIN AV 75') == 'Ecosprin AV 75' and B.speak_name('PAN 40 TAB') == 'Pan 40 Tab')

# ======================================================================================
# 13b. what the independent review found (all reproduced first, then fixed)
# ======================================================================================
# A. with a basket open, ordinary questions must never become bill lines
o = owner()
B.basket_add(o, code_text(c1, m1), units=30)
junk_q = ['sales of dolo 650', 'sales in 2025', 'profit in october 2025', 'customers above 5000', 'orders in 2026', 'stock of ecosprin av 75',
          'stock of pan 40', 'price of dolo 650', 'purchases of pan 40 last month', 'sold 25 dolo today', 'stock 40 pan', 'cost of eco 65']
taken = [q for q in junk_q if ask.basket_reply(q, o) is not None]
check('12 "word ... number" questions are NOT taken for bill lines while a basket is open', not taken, taken)
check('...and the basket still holds only the one real item', B.snapshot(o)['count'] == 1)
check('...but a product named exactly is still the next item', ask.basket_reply('Ecosprin AV 75, 30', o)['basket']['count'] == 2)
r = B.interpret(owner(), 'Ecosprn AV 75, 30', explicit_add=True)
check('typed on the basket panel a close spelling is still understood (nearest name)', r and r['ok'] and 'ECOSPRIN' in r['basket']['items'][0]['product'].upper(), r)
check('...but not from the voice/Ask path (no guessing there)', ask.basket_reply('Ecosprn AV 75, 30', o) is None)

# B. rounding: half-up to the paisa, in exact decimals
check('41.25 x 1 tablet of a 10-strip = 4.13 and 64.97 x 5 / 10 = 32.49 (they were 4.12 / 32.48)',
      T._mrp_line('X', 41.25, 10, 1, None, '')['mrp_total'] == 4.13 and T._mrp_line('X', 64.97, 10, 5, None, '')['mrp_total'] == 32.49)
random.seed(9)
bad = []
for (code_, cents_) in random.sample(list(by_key), 3000):
    nm_ = by_key[(code_, cents_)][0]
    f_ = float(fm.get(nm_, 1) or 1)
    for u_ in (1, 7, 10, 15, 30):
        want = ((Fraction(cents_, 100) * u_ / Fraction(str(f_))) * 100 + Fraction(1, 2)).__floor__() / 100
        got = T._mrp_line(nm_, cents_ / 100, f_, u_, None, '')['mrp_total']
        if abs(got - want) > 1e-9:
            bad.append((code_, cents_, u_, got, want))
check('15,000 real product/MRP/quantity lines: every line total is the exact half-up paisa', not bad, bad[:3])
o = owner()
for _ in range(3):
    B.basket_add(o, 'gly 19.46', units=10)
tt = B.basket_total(o)
check('a bill of rounded lines totals exactly the sum of those lines', abs(tt['grand_total_mrp'] - sum(i['mrp_total'] for i in tt['basket']['items'])) < 1e-9)

# C. a blank product is nothing, not "the shortest product name"
check('blank / punctuation-only / None product matches nothing', all(not T.mrp_resolve(x)['found'] for x in ('', '  ', '---', '  ---  ', None)))
o = owner()
rb1, rb2 = B.basket_add(o, '', units=3), B.basket_add(o, '  ---  ', strips=3)
check('basket_add with a blank product adds nothing', rb1['ok'] is False and rb2['ok'] is False and rb1['basket']['count'] == 0)
st, rj = post('/api/basket/add', {'product': '', 'units': 3}, {'X-Session-Token': server._create_session('blank-check')})
check('POST /api/basket/add with a blank product adds nothing', st == 200 and rj['ok'] is False and rj['basket']['count'] == 0, rj)
check('get_mrp_total with a blank product or a non-finite quantity is a clean "not found"',
      not T.get_mrp_total([{'product': '', 'units': 3}])['items'][0]['found']
      and not T.get_mrp_total([{'product': 'eco 65.60', 'units': float('nan')}])['items'][0]['found']
      and not T.get_mrp_total([{'product': 'eco 65.60', 'units': float('inf')}])['items'][0]['found'])

# D. the by-name reading survives a crowded code
capped = None
for (cc_, cs_), names_ in sorted(by_key.items()):
    if cs_ % 100 == 0 and cc_.isalpha() and len(names_) >= B.MAX_CANDIDATES:
        alt_ = T.mrp_resolve(f'{cc_} {cs_ // 100}')['also_possible_by_name']
        if alt_:
            capped = (cc_, cs_, alt_)
            break
check('the catalog has a crowded whole-rupee code with a by-name reading to test with', capped is not None)
if capped:
    cc_, cs_, alt_ = capped
    o = owner()
    rr = ask.basket_reply(f'{cc_.lower()} {cs_ // 100}, 10', o)
    seen_ = {rr['basket']['items'][0]['product']}
    for _ in range(B.MAX_CANDIDATES):
        seen_.add(say(o, 'change last')['basket']['items'][0]['product'])
    check(f'a crowded code ({cc_} {cs_ // 100}) still lets "change last" reach the by-name reading', alt_['product'].strip() in seen_ and rr['basket']['items'][0]['matches'] == B.MAX_CANDIDATES, (alt_['product'], seen_))

# E. Kannada (and Latin-Kannada) questions that merely contain a command word are questions
kn_q = ['ಟಾಪ್ ಗ್ರಾಹಕರ ಪಟ್ಟಿ ಹೇಳು', 'ಡೆಡ್ ಸ್ಟಾಕ್ ಪಟ್ಟಿ ಓದು', 'ಡೆಡ್ ಸ್ಟಾಕ್ ಪಟ್ಟಿ ತೋರಿಸು', 'dead stock list helu', 'ಮಾರಾಟದಲ್ಲಿ ಬದಲಾವಣೆ ಏನು', 'ಇದನ್ನು ತೆಗೆದುಕೊಳ್ಳಿ',
        'ಹೊಸ ಬಿಲ್ ಎಷ್ಟು', 'ಎಲ್ಲಾ ತೆಗೆದುಕೊಂಡರೆ ಎಷ್ಟು', 'tege last month sales', 'hosa bill eshtu', 'bere customers']
o = owner()
B.basket_add(o, code_text(c1, m1), units=30)
taken = [q for q in kn_q if ask.basket_reply(q, o) is not None]
check('11 Kannada / Latin-Kannada questions are NOT basket commands, even with a basket open', not taken, taken)
check('...and the basket is untouched', B.snapshot(o)['count'] == 1)
kn_cmds = {'ಕೊನೆಯದು ತೆಗೆ': 'remove_last', 'ಅದನ್ನು ತೆಗೆ': 'remove_last', 'koneyadu tege': 'remove_last', 'ಬದಲಿಸು': 'change_last', 'ಕೊನೆಯದು ಬದಲಿಸು': 'change_last',
           'ಪಟ್ಟಿ ಓದು': 'list', 'patti odu': 'list', 'ಹೊಸ ಬಿಲ್': 'clear', 'hosa bill': 'clear', 'ಕ್ಲಿಯರ್': 'clear', 'ಲೆಕ್ಕ ಮಾಡು': 'total', 'ಒಟ್ಟು ಎಷ್ಟು': 'total'}
wrong = {w: B.match_command(w) for w, c in kn_cmds.items() if not B.match_command(w) or B.match_command(w)[0] != c}
check('the real Kannada commands still match', not wrong, wrong)

# F. generic phrases are commands only while a basket is open (or typed on the panel)
nb = owner()
generic = ['and the total?', 'what is the total', 'total amount', 'total bill', 'next one', 'show me the next one', 'another one', 'new order', 'cancel order',
           'ಒಟ್ಟು ಎಷ್ಟು', 'read list', 'remove last', 'change last', 'clear all', 'total', 'grand total', 'list']
check('with no basket, 17 generic phrases ("and the total?", "next one", "new order"...) go to the model, not "basket is empty"',
      all(ask.basket_reply(g, nb) is None for g in generic), [g for g in generic if ask.basket_reply(g, nb) is not None])
check('...while a basket is open the same words mean the basket',
      all(ask.basket_reply(g, o) is not None for g in ('what is the total', 'total', 'grand total', 'read list')) and B.is_open(o))
check('...and on the basket panel an empty basket answers "The basket is empty."', B.interpret(nb, 'total', explicit_add=True)['say'] == 'The basket is empty.')
check('the unmistakable ones still work with no basket', all(ask.basket_reply(g, nb) is not None for g in ('calculate', 'mrp total', 'lekka madu', 'new bill', 'undo', 'ಲೆಕ್ಕ ಮಾಡು', 'ಹೊಸ ಬಿಲ್')))

# G. brand letters in script: only when it looks like the quick code
check('a script product name + strength, or a date, is left to the model (no "say the letters in English")',
      all(B.classify(nb, q) is None for q in ('ಡೋಲೋ 650, 30', 'ಎಕೋಸ್ಪ್ರಿನ್ 75 30 ಮಾತ್ರೆ', 'डोलो 650, 30', 'ಅಕ್ಟೋಬರ್ 15 2025')))
check('...but "ಇಕೋ 65.60, 30" (a short script word + an MRP with paise) still gets the guidance', B.classify(nb, 'ಇಕೋ 65.60, 30') == 'bad_letters')

# H. "please repeat" only for a scrap made of command words
check('"listen" / "totally" / "clearly" / "billing" / "changes" with a basket open are stray words, not "please repeat"',
      all(B.classify(o, w) is None for w in ('listen', 'totally', 'clearly', 'billing', 'changes')))
check('...while "remove" / "change" alone still get "please repeat"', B.classify(o, 'remove') == 'unclear' and B.classify(o, 'change') == 'unclear')

# I. a pause in "24, 50" means price and quantity, not 24.50
pause = None
for (cc_, cs_), names_ in sorted(by_key.items()):
    if cs_ % 100 == 50 and cc_.isalpha() and (cc_, cs_ - 50) not in by_key and not T._mrp_name_candidates(f'{cc_} {cs_ // 100}', fuzzy=False):
        pause = (cc_, cs_, names_[0].strip())
        break
check('the catalog has a code to test the "24, 50" pause with', pause is not None)
if pause:
    cc_, cs_, pname_ = pause
    r1 = ask.basket_reply(f'{cc_.lower()} {cs_ // 100}, 50', owner())
    r2 = ask.basket_reply(f'{cc_.lower()} {cs_ // 100} 50', owner())
    check(f'"{cc_.lower()} {cs_ // 100}, 50" (with a pause) is a price and a quantity: no match, not "found {pname_}"', r1['ok'] is False and 'No match' in r1['say'], r1['say'])
    check(f'"{cc_.lower()} {cs_ // 100} 50" (no pause) can be {cs_ / 100:.2f}: found, and it asks how many', r2['ok'] is False and 'did not hear how many' in r2['say'] and f'{cs_ / 100:.2f}' in r2['say'], r2['say'])

# J. nothing the model or a client sends can crash these
reset = B.OWNER.set(owner())
res = B.run('basket_add', {'product': 'eco ' + '9' * 400, 'units': 3})
res2 = B.run('basket_add', {'product': 'eco 65.60', 'units': 'x'})
res3 = B.run('basket_add', {'product': {'a': 1}, 'units': 3})
B.OWNER.reset(reset)
check('the model\'s basket_add with absurd input returns a result or an error - never raises', isinstance(res, dict) and isinstance(res2, dict) and isinstance(res3, dict), (res, res2, res3))
check('a 400-digit MRP is just "not found"', not T.mrp_resolve('eco ' + '9' * 400 + '.50')['found'])
okj = []
for path in ('/api/basket/say', '/api/basket/add', '/api/basket/total', '/api/basket/clear'):
    for raw in ('[1,2]', '"x"', 'null', '123', 'not json'):
        okj.append(cl.post(path, data=raw, headers=dict(HC, **{'Content-Type': 'application/json'})).status_code)
check('JSON bodies that are not objects (a list, a string, junk) never give a 500 on the basket routes', set(okj) == {200}, set(okj))

# ======================================================================================
# 14. the model's way in: basket_* tools bound to the login, same functions, 'say' read as is
# ======================================================================================
check('no login -> no basket tools offered to the model', not any(t['name'].startswith('basket_') for t in ask._available_tools()))
tok_a, tok_b = owner(), owner()
reset = B.OWNER.set(tok_a)
names = {t['name'] for t in ask._available_tools()}
check('with a login the six basket tools are offered', {'basket_add', 'basket_remove_last', 'basket_change_last', 'basket_list', 'basket_total', 'basket_clear'} <= names, names)
allowed = names
res = ask._run_allowed_tool('basket_add', {'product': 'ECO 65.60', 'units': 30}, allowed)
check('tool basket_add works on the logged-in basket', res['ok'] and B.snapshot(tok_a)['count'] == 1 and res['say'].startswith('Ecosprin AV 75, 2 strips'), res)
check('...and not on anyone else\'s', B.snapshot(tok_b)['count'] == 0)
res = ask._run_allowed_tool('basket_total', {'language': 'en'}, allowed)
check('tool basket_total returns the line to speak, with the grand total', res['grand_total_mrp'] == 131.2 and 'Grand total' in res['say'], res)
res = ask._run_allowed_tool('basket_add', {'product': 'ECO 65.60', 'units': 30, 'evil': 1}, allowed)
check('a bad argument from the model is an error result, not a crash', 'error' in res, res)
B.OWNER.reset(reset)
check('tools without a login are refused', 'error' in B.run('basket_add', {'product': 'ECO 65.60', 'units': 30}))

real_run_tool, real_notes = ask.T.run_tool, ask.T.get_notes
ask.T.get_notes = lambda n=20: {'notes': []}


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


tok_m = owner()
fake = FakeCompletions([reply(calls=[('basket_add', {'product': 'ECO 65.60', 'units': 30, 'language': 'en'})]), reply('(ready)'), reply('Ecosprin AV 75, 2 strips, 131 rupees 20 paise.')])
openai.OpenAI = lambda **k: types.SimpleNamespace(chat=types.SimpleNamespace(completions=fake))
try:
    answer = ask.ask('Please put thirty Ecosprin AV seventy five on my bill, the MRP is sixty five sixty and I want it quoted', speak=True, owner=tok_m)
    sysmsg = fake.requests[0]['messages'][0]['content']
    toolnames = [t['function']['name'] for t in fake.requests[0]['tools']]
    tool_msg = [m for m in fake.requests[1]['messages'] if m.get('role') == 'tool'][0]['content']
    check('model path: the model called basket_add and the item is in the login\'s basket', B.snapshot(tok_m)['count'] == 1 and 'basket_add' in toolnames, (B.snapshot(tok_m), toolnames))
    check('model path: the tool result handed to the model carries the exact line to speak', 'Ecosprin AV 75, 2 strips, 131 rupees 20 paise.' in tool_msg and 'grand_total' not in tool_msg, tool_msg)
    check('model path: the prompt tells it to speak the line as is and not to total unasked', 'MRP BASKET' in sysmsg and 'only when the owner asks' in sysmsg)
    check('model path: the answer is the model\'s reading of that line', answer.startswith('Ecosprin AV 75, 2 strips'), answer)
    fake2 = FakeCompletions([reply('(ready)'), reply('Sales were good.')])
    openai.OpenAI = lambda **k: types.SimpleNamespace(chat=types.SimpleNamespace(completions=fake2))
    ask.ask('How did we do last month?', speak=True)
    check('model path: with no login the model is not even offered the basket tools', not any(t['function']['name'].startswith('basket_') for t in fake2.requests[0]['tools']))
    check('model path: the owner context is reset after each ask (no leak into the next request)', B.OWNER.get() is None)
finally:
    ask.T.run_tool, ask.T.get_notes = real_run_tool, real_notes

# ======================================================================================
# 15. the pages
# ======================================================================================
html = (ROOT / 'mobile_dashboard.html').read_text(encoding='utf-8')
check('Ready Reckoner: has the MRP basket panel wired to the basket routes',
      'id="view-basket"' in html and "'/api/basket/say'" in html and "'/api/basket/' + action" in html
      and all(f'data-bk="{a}"' in html for a in ('total', 'remove-last', 'change-last', 'list', 'clear')))
check('Ready Reckoner: the basket panel can be opened from Home, and has a mic that uses the speech routes',
      'onclick="showBasket()"' in html and 'id="bk-mic"' in html and "'/api/speech/transcribe'" in html and "'/api/speech/tts'" in html)
tokP = server._create_session('page-check')
replies = {a: cl.post('/api/basket/' + a, data=json.dumps({'text': 'calculate'}), headers={'X-Session-Token': tokP, 'Content-Type': 'application/json'})
           for a in ('say', 'total', 'remove-last', 'change-last', 'list', 'clear', 'add')}
check('Ready Reckoner: every route the panel calls answers a logged-in session with a line to show',
      all(r.status_code == 200 and r.get_json().get('say') for r in replies.values()), {a: (r.status_code, r.get_json()) for a, r in replies.items() if r.status_code != 200})
check('Ready Reckoner panel: items queue instead of being dropped, errors show, a double-tapped mic and a logout are handled',
      'function bkQueue(' in html and 'BK.busy' not in html and "j.error || ('Server error '" in html and 'BK.micStarting' in html
      and 'function bkReset(' in html and "if (typeof bkReset === 'function') bkReset();" in html and 'bkCancelRec();      // leaving the basket panel' in html)
ask_html = (ROOT / 'ask_dashboard.html').read_text(encoding='utf-8')
check('Ask page: voice code untouched (stray-word and barge-in settings as the owner set them)', 'const TALK_BARGE_MIN_MS = 1300;' in ask_html and "speakWithBrowser('hmm', t)" in ask_html)

print()
print(f'{len(FAILS)} FAILED: {FAILS}' if FAILS else 'ALL PASSED')
sys.exit(1 if FAILS else 0)

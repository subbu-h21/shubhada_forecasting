"""The MRP basket ("lekka madu") - a running list of items whose MRP total is added up on
request. Owner's rule (20 Oct 2026), for the Ask page voice and the Ready Reckoner:

  1. Say one item at a time, "<first 3 letters of the brand> <MRP on the pack>, <quantity>"
     - "eco 65.60, 30" (30 tablets/capsules; "2 strips" works too).
  2. Each item is added to the basket and answered with ONE short line - product, strips and
     that line's MRP - so a wrong match is caught at once. No grand total yet.
  3. "calculate" / "total" / "mrp total" / "lekka madu" / "ಲೆಕ್ಕ ಮಾಡು" reads every line and the grand
     total. The basket stays until "new bill" / "clear".
  4. "remove last" / "undo", "change last" (next candidate), "read list", "new bill".

The basket is STATE IN CODE, one per login session token, never the model's memory: the model
(or the voice route, or the Ready Reckoner panel) only calls basket_add / basket_remove_last /
basket_change_last / basket_total / basket_list / basket_clear and reads the result. Every
rupee is worked out here, with ask_tools._mrp_line (the same maths as get_mrp_total) and
ask_tools.mrp_resolve (the same product matching). A basket belongs to the token that made it:
there is no way to name another one, and a logout or expiry deletes it (drop()).

What the owner says is turned into a command or an item by plain rules, not by a model, so a
basket line costs nothing, answers at once and does not count against the daily question cap
(see ask.basket_reply). Numbers come out of spoken_numbers.py in any of the forms speech-to-text
produces; where a number can be read two ways ("6560" = 65.60 or 6560) the readings are tried
against the catalog and the one that exists wins.
"""
import contextvars
import re
import threading
import time
from decimal import ROUND_HALF_UP, Decimal

import ask_tools as T
import spoken_numbers as S

MAX_ITEMS = 60               # a bill longer than this is a mistake, not a bill
MAX_UNITS = 100000
MAX_CANDIDATES = 8           # alternatives kept per item for "change last"
IDLE_SECONDS = 12 * 60 * 60  # a basket nobody touched for this long is forgotten (a session lasts as long)

OWNER = contextvars.ContextVar('mrp_basket_owner', default=None)   # set by ask.ask() for the model's tool calls

_lock = threading.RLock()
_baskets = {}                # session token -> basket dict


# ======================================================================================
# State: functions that take the owner (the session token) and nothing else to find a basket
# ======================================================================================
def _now():
    return time.time()


def _purge_idle():
    cutoff = _now() - IDLE_SECONDS
    for tok in [t for t, b in _baskets.items() if b['touched'] < cutoff]:
        del _baskets[tok]


def _get(owner, create=True):
    owner = owner if isinstance(owner, str) and owner else None
    if not owner:
        return None
    with _lock:
        _purge_idle()
        b = _baskets.get(owner)
        if b is None and create:
            b = _baskets[owner] = {'items': [], 'lang': 'en', 'totalled': False, 'touched': _now(), 'seq': 0}
        if b is not None:
            b['touched'] = _now()
        return b


def drop(owner):
    """Forget a login's basket (logout, or the session expired)."""
    with _lock:
        _baskets.pop(owner, None)


def is_open(owner):
    """True while the login has at least one item in its basket."""
    with _lock:
        b = _baskets.get(owner if isinstance(owner, str) else None)
        return bool(b and b['items'])


def _cand_line(item, cand=None):
    cand = cand or item['cands'][item['pick']]
    units = item['qty'] * cand['strip_size'] if item['basis'] == 'strips' else item['qty']
    return T._mrp_line(cand['product'], cand['mrp_per_strip'], cand['strip_size'], units, cand['date'], cand['source'])


def _grand(items):
    return sum((Decimal(str(_cand_line(i)['mrp_total'])) for i in items), Decimal(0)).quantize(Decimal('0.01'), ROUND_HALF_UP)


def snapshot(owner):
    """The basket as plain data (for the Ready Reckoner panel). The grand total is filled in
    only after "total" has been asked for - the owner wants it on request, not after every item."""
    b = _get(owner, create=False)
    if not b:
        return {'items': [], 'count': 0, 'totalled': False, 'grand_total_mrp': None, 'lang': 'en'}
    with _lock:
        rows = []
        for n, it in enumerate(b['items'], 1):
            ln = _cand_line(it)
            rows.append({'n': n, 'product': ln['product'], 'asked': it['asked'], 'strips': ln['strips'], 'units': ln['units'],
                         'strip_size': ln['strip_size'], 'mrp_per_strip': ln['mrp_per_strip'], 'mrp_total': ln['mrp_total'],
                         'match': it['pick'] + 1, 'matches': len(it['cands']),
                         'others': [c['product'].strip() for k, c in enumerate(it['cands']) if k != it['pick']][:4]})
        return {'items': rows, 'count': len(rows), 'totalled': b['totalled'], 'lang': b['lang'],
                'grand_total_mrp': float(_grand(b['items'])) if b['totalled'] else None}


# ======================================================================================
# Words the owner hears: short, speakable, English or Kannada, no tables
# ======================================================================================
def money(amount, lang='en'):
    d = Decimal(str(amount)).quantize(Decimal('0.01'), ROUND_HALF_UP)
    rupees, paise = int(d), int((d - int(d)) * 100)
    r_word = ('rupee' if rupees == 1 else 'rupees') if lang == 'en' else 'ರೂಪಾಯಿ'
    p_word = 'paise' if lang == 'en' else 'ಪೈಸೆ'
    if rupees and paise:
        return f'{rupees} {r_word} {paise} {p_word}'
    return f'{rupees} {r_word}' if rupees else f'{paise} {p_word}'


def speak_name(name):
    """ECOSPRIN AV 75 -> Ecosprin AV 75: long words read as words, two-letter ones as letters."""
    out = []
    for w in str(name).split():
        out.append((w.upper() if len(w) <= 2 else w.capitalize()) if w.isalpha() else w)
    return ' '.join(out)


def _plural(n, one, many):
    return one if n == 1 else many


def qty_phrase(ln, lang='en'):
    size, strips, units = ln['strip_size'], ln['strips'], ln['units']
    if size == 1:                      # a pack counted by the piece (cream, shampoo...): no strips
        return f'{units} unit' + ('' if units == 1 else 's') if lang == 'en' else f'{units} ಯೂನಿಟ್'
    if float(strips).is_integer():
        n = int(strips)
        return f"{n} {_plural(n, 'strip', 'strips')}" if lang == 'en' else f'{n} ಸ್ಟ್ರಿಪ್'
    if lang == 'en':
        return f'{units} tablets, {strips} strip'
    return f'{units} ಮಾತ್ರೆ, {strips} ಸ್ಟ್ರಿಪ್'


def line_text(item, lang):
    ln = _cand_line(item)
    return f"{speak_name(ln['product'])}, {qty_phrase(ln, lang)}, {money(ln['mrp_total'], lang)}."


def _count(k, lang):
    return f"{k} {_plural(k, 'item', 'items')}" if lang == 'en' else f'{k} ವಸ್ತು'


MSG = {
    'en': {
        'empty': 'The basket is empty.',
        'cleared': 'New bill. The basket is empty.',
        'repeat': 'Please repeat that.',
        'no_login': 'The basket needs a login.',
        'full': f'The basket is full, {MAX_ITEMS} items. Say calculate.',
        'bad_qty': 'I did not catch the quantity. Please say it again.',
        'bad_letters': 'Please say the first three letters in English, then the MRP, then the quantity.',
        'help': "I did not understand that. Say an item like 'eco 65.60, 30', or: calculate, remove last, change last, read list, new bill.",
        'left': lambda k: f'{_count(k, "en")} left.',
        'removed': lambda name, left: f'Removed {name}. {left}',
        'changed': lambda line, pos, n: f'Now {line} Match {pos} of {n}.',
        'only_one': lambda asked, line: f'Only one match for {asked}. {line}',
        'total': lambda lines, k, amount: f"{' '.join(lines)} Grand total for {_count(k, 'en')}: {amount}.",
        'list': lambda lines, k: f"{_count(k, 'en')}. " + ' '.join(f'{i}, {s}' for i, s in enumerate(lines, 1)),
        'others': lambda n, names, more: f" Best seller of {n}. Others: {', '.join(names)}" + (f', and {more} more.' if more else '.'),
        'others_name': lambda n, names, more: f" Closest of {n} names. Others: {', '.join(names)}" + (f', and {more} more.' if more else '.'),
        'not_found': lambda asked, closest: (f'No match for {asked}. ' + (f"Closest: {', '.join(closest)}. " if closest else
                                                                          'Check the MRP with the paise. ') + 'Please say it again.'),
        'name_not_found': lambda asked: f'I could not find {asked}. Please say it again.',
        'no_qty': lambda name, mrp: f'Found {name} at {mrp} rupees, but I did not hear how many. Please say the whole item again.',
    },
    'kn': {
        'empty': 'ಪಟ್ಟಿ ಖಾಲಿ ಇದೆ.',
        'cleared': 'ಹೊಸ ಬಿಲ್. ಪಟ್ಟಿ ಖಾಲಿ ಇದೆ.',
        'repeat': 'ದಯವಿಟ್ಟು ಮತ್ತೆ ಹೇಳಿ.',
        'no_login': 'ಪಟ್ಟಿಗೆ ಲಾಗಿನ್ ಬೇಕು.',
        'full': f'ಪಟ್ಟಿ ತುಂಬಿದೆ, {MAX_ITEMS} ವಸ್ತುಗಳು. ಲೆಕ್ಕ ಮಾಡು ಎನ್ನಿ.',
        'bad_qty': 'ಎಷ್ಟು ಎಂದು ಕೇಳಿಸಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೆ ಹೇಳಿ.',
        'bad_letters': 'ದಯವಿಟ್ಟು ಮೊದಲ ಮೂರು ಅಕ್ಷರ ಇಂಗ್ಲಿಷ್‌ನಲ್ಲಿ, ನಂತರ MRP, ನಂತರ ಎಷ್ಟು ಎಂದು ಹೇಳಿ.',
        'help': "ಅರ್ಥವಾಗಲಿಲ್ಲ. 'eco 65.60, 30' ತರಹ ಹೇಳಿ, ಅಥವಾ: ಲೆಕ್ಕ ಮಾಡು, ಕೊನೆಯದು ತೆಗೆ, ಬದಲಿಸು, ಪಟ್ಟಿ ಓದು, ಹೊಸ ಬಿಲ್.",
        'left': lambda k: f'ಇನ್ನೂ {_count(k, "kn")} ಇದೆ.',
        'removed': lambda name, left: f'{name} ತೆಗೆದಿದ್ದೇನೆ. {left}',
        'changed': lambda line, pos, n: f'ಈಗ {line} {n} ಆಯ್ಕೆಗಳಲ್ಲಿ {pos}ನೇದು.',
        'only_one': lambda asked, line: f'{asked} ಗೆ ಒಂದೇ ಆಯ್ಕೆ ಇದೆ. {line}',
        'total': lambda lines, k, amount: f"{' '.join(lines)} ಒಟ್ಟು {_count(k, 'kn')}, ಮೊತ್ತ {amount}.",
        'list': lambda lines, k: f"{_count(k, 'kn')}. " + ' '.join(f'{i}, {s}' for i, s in enumerate(lines, 1)),
        'others': lambda n, names, more: f" {n} ಆಯ್ಕೆಗಳಲ್ಲಿ ಹೆಚ್ಚು ಮಾರಾಟವಾದದ್ದು. ಇತರೆ: {', '.join(names)}" + (f', ಮತ್ತು {more} ಇನ್ನಷ್ಟು.' if more else '.'),
        'others_name': lambda n, names, more: f" {n} ಹೆಸರುಗಳಲ್ಲಿ ಹತ್ತಿರದ್ದು. ಇತರೆ: {', '.join(names)}" + (f', ಮತ್ತು {more} ಇನ್ನಷ್ಟು.' if more else '.'),
        'not_found': lambda asked, closest: (f'{asked} ಗೆ ಯಾವುದೇ ಉತ್ಪನ್ನ ಸಿಗಲಿಲ್ಲ. ' + (f"ಹತ್ತಿರದ್ದು: {', '.join(closest)}. " if closest else
                                                                                       'ಪೈಸೆ ಸೇರಿಸಿ MRP ನೋಡಿ. ') + 'ದಯವಿಟ್ಟು ಮತ್ತೆ ಹೇಳಿ.'),
        'name_not_found': lambda asked: f'{asked} ಸಿಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೆ ಹೇಳಿ.',
        'no_qty': lambda name, mrp: f'{name} {mrp} ರೂಪಾಯಿಗೆ ಸಿಕ್ಕಿದೆ, ಆದರೆ ಎಷ್ಟು ಎಂದು ಕೇಳಿಸಲಿಲ್ಲ. ಪೂರ್ತಿ ಮತ್ತೆ ಹೇಳಿ.',
    },
}


def _lang(b, lang):
    if lang in ('en', 'kn'):
        b['lang'] = lang
    return b['lang']


def _result(owner, say, ok=True, action='', **extra):
    out = {'ok': ok, 'action': action, 'say': say, 'basket': snapshot(owner)}
    out.update(extra)
    return out


# ======================================================================================
# The operations: basket_add, basket_remove_last, basket_change_last, basket_total, ...
# ======================================================================================
def _spoken_code(asked):
    m = re.match(r'^([A-Za-z0-9]{3})\s+(.+)$', asked)
    return f"{' '.join(m.group(1).upper())} {m.group(2)}" if m else asked


def _add_resolved(owner, b, lang, asked, res, qty, basis):
    """Put an already-resolved product in the basket and say the one line for it."""
    L = MSG[lang]
    if len(b['items']) >= MAX_ITEMS:
        return _result(owner, L['full'], ok=False, action='add')
    cands = list(res['candidates'])
    alt = res.get('also_possible_by_name')
    if alt and all(str(c['product']).strip() != str(alt['product']).strip() for c in cands):
        cands = cands[:MAX_CANDIDATES - 1] + [alt]     # "Pan 40": the by-name reading is one "change last" away
    cands = cands[:MAX_CANDIDATES]
    b['seq'] += 1
    item = {'id': b['seq'], 'asked': res['asked'], 'qty': float(qty), 'basis': basis, 'cands': cands, 'pick': 0}
    b['items'].append(item)
    b['totalled'] = False
    say = line_text(item, lang)
    if len(cands) > 1:
        names = [speak_name(c['product']) for c in cands[1:3]]
        say += L['others' if res.get('matched_by') else 'others_name'](len(cands), names, len(cands) - 1 - len(names))
    return _result(owner, say, action='add', matched_by=res.get('matched_by') or 'name')


def _not_found_say(lang, res):
    L = MSG[lang]
    if res.get('matched_by'):
        closest = [f"{speak_name(c['product'])} {c['mrp_per_strip']:.2f}" for c in res.get('closest', [])[:3]]
        return L['not_found'](_spoken_code(res['asked']), closest)
    return L['name_not_found'](res['asked'])


def _add_checked(owner, b, lang, res, qty, basis):
    """Validate the quantity, then put the resolved product in the basket (under the lock)."""
    L = MSG[lang]
    try:
        qty = float(qty)
        ok = 0 < qty <= MAX_UNITS and (basis == 'strips' or qty == int(qty))
    except (TypeError, ValueError, OverflowError):
        ok = False
    if not ok:
        return _result(owner, L['bad_qty'], ok=False, action='add')
    with _lock:
        return _add_resolved(owner, b, lang, res['asked'], res, qty, basis)


def basket_add(owner, product, units=None, strips=None, lang=None, fuzzy=True):
    """Add ONE item to the basket. `product` is what the owner said: a name ("Ecosprin AV 75") or
    the quick code - first 3 letters + the pack's exact MRP ("ECO 65.60"). `units` = tablets or
    capsules, or `strips` = number of strips (units = strips x the product's strip size).
    Matching and every rupee are worked out here."""
    b = _get(owner)
    if b is None:
        return {'ok': False, 'action': 'add', 'say': MSG['en']['no_login'], 'basket': snapshot(None)}
    with _lock:
        lang = _lang(b, lang)
    basis, qty = ('strips', strips) if strips not in (None, '') else ('units', units)
    res = T.mrp_resolve(product, fuzzy=fuzzy)     # catalog lookup: outside the lock
    if not res['found']:
        return _result(owner, _not_found_say(lang, res), ok=False, action='add', reason=res['reason'])
    return _add_checked(owner, b, lang, res, qty, basis)


def basket_remove_last(owner, lang=None):
    b = _get(owner)
    if b is None:
        return {'ok': False, 'action': 'remove_last', 'say': MSG['en']['no_login'], 'basket': snapshot(None)}
    with _lock:
        L = MSG[_lang(b, lang)]
        if not b['items']:
            return _result(owner, L['empty'], ok=False, action='remove_last')
        gone = b['items'].pop()
        b['totalled'] = False
        name = speak_name(_cand_line(gone)['product'])
        left = L['left'](len(b['items'])) if b['items'] else L['empty']
        return _result(owner, L['removed'](name, left), action='remove_last')


def basket_change_last(owner, lang=None):
    """Switch the last item to its next candidate (the next-best seller with that code and MRP,
    then the by-name reading); after the last one it goes back to the first."""
    b = _get(owner)
    if b is None:
        return {'ok': False, 'action': 'change_last', 'say': MSG['en']['no_login'], 'basket': snapshot(None)}
    with _lock:
        lang = _lang(b, lang)
        L = MSG[lang]
        if not b['items']:
            return _result(owner, L['empty'], ok=False, action='change_last')
        it = b['items'][-1]
        if len(it['cands']) == 1:
            return _result(owner, L['only_one'](_spoken_code(it['asked']), line_text(it, lang)), ok=False, action='change_last')
        it['pick'] = (it['pick'] + 1) % len(it['cands'])
        b['totalled'] = False
        return _result(owner, L['changed'](line_text(it, lang), it['pick'] + 1, len(it['cands'])), action='change_last')


def basket_list(owner, lang=None):
    """Read the items back, without a grand total."""
    b = _get(owner)
    if b is None:
        return {'ok': False, 'action': 'list', 'say': MSG['en']['no_login'], 'basket': snapshot(None)}
    with _lock:
        lang = _lang(b, lang)
        L = MSG[lang]
        if not b['items']:
            return _result(owner, L['empty'], ok=False, action='list')
        return _result(owner, L['list']([line_text(i, lang) for i in b['items']], len(b['items'])), action='list')


def basket_total(owner, lang=None):
    """Every item with its line total, then the GRAND TOTAL. The basket is kept afterwards."""
    b = _get(owner)
    if b is None:
        return {'ok': False, 'action': 'total', 'say': MSG['en']['no_login'], 'basket': snapshot(None)}
    with _lock:
        lang = _lang(b, lang)
        L = MSG[lang]
        if not b['items']:
            return _result(owner, L['empty'], ok=False, action='total')
        b['totalled'] = True
        grand = _grand(b['items'])
        say = L['total']([line_text(i, lang) for i in b['items']], len(b['items']), money(grand, lang))
        return _result(owner, say, action='total', grand_total_mrp=float(grand))


def basket_clear(owner, lang=None):
    """New bill: empty the basket."""
    b = _get(owner)
    if b is None:
        return {'ok': False, 'action': 'clear', 'say': MSG['en']['no_login'], 'basket': snapshot(None)}
    with _lock:
        L = MSG[_lang(b, lang)]
        b['items'].clear()
        b['totalled'] = False
        return _result(owner, L['cleared'], action='clear')


# ======================================================================================
# Reading the owner's words: commands first, then items
# ======================================================================================
_WORD = re.compile(r'[a-zऀ-ॿಀ-೿]+')
_POLITE = {'please', 'pls', 'kindly', 'now', 'ok', 'okay', 'ya', 'yes', 'just', 'sir', 'madam', 'the', 'a', 'an', 'me', 'my',
           'give', 'tell', 'show', 'do', 'then', 'also', 'can', 'you', 'could', 'will', 'subbi', 'subbiii', 'suubbiii'}
_FILLER = _POLITE | {'madu', 'maadu', 'maadi', 'madi', 'mado', 'maado', 'ಮಾಡು', 'ಮಾಡಿ', 'ಮಾಡೋ'}


def _skeleton(s):
    """Latin letters only, 'kh/dh/th/gh/bh...' -> k/d/t/g/b, doubled letters collapsed:
    'lekka maadu' / 'lekha madu' / 'leka madu' all -> 'lekamadu'."""
    s = re.sub(r'[^a-z]', '', s.lower())
    s = re.sub(r'([kgcjtdpb])h', r'\1', s)
    return re.sub(r'(.)\1+', r'\1', s)


_KN_CALC_TAIL = {'ಮಾಡು', 'ಮಾಡಿ', 'ಮಾಡೋ', 'ಮಾಡಿರಿ', 'ಹಾಕು', 'ಹಾಕಿ', 'ಹೇಳು', 'ಹೇಳಿ', 'ಕೊಡು', 'ಕೊಡಿ', 'ಎಷ್ಟು', 'ಏನು', 'ಆಯ್ತು'}
_TOTAL_EN = {'calculate', 'calculation', 'calculations', 'total', 'totals', 'grand', 'mrp', 'sum', 'amount', 'bill', 'up', 'add',
             'together', 'what', 'whats', 'is', 'it', 'this', 'and', 'all', 'm', 'r', 'p'}
_CLEAR_EN = {'new', 'bill', 'basket', 'list', 'clear', 'reset', 'start', 'over', 'again', 'fresh', 'next', 'customer', 'cancel',
             'empty', 'delete', 'everything', 'all', 'this', 'order'}
_REMOVE_EN = {'remove', 'delete', 'cancel', 'drop', 'erase', 'undo', 'take', 'out', 'last', 'previous', 'one', 'item', 'entry',
              'line', 'that', 'recent', 'latest', 'it', 'clear', 'this'}
_CHANGE_EN = {'change', 'switch', 'swap', 'last', 'previous', 'one', 'item', 'it', 'that', 'this', 'next', 'other', 'another',
              'match', 'wrong', 'not', 'correct', 'different', 'product', 'candidate'}
_LIST_EN = {'read', 'list', 'back', 'basket', 'items', 'show', 'what', 'whats', 'is', 'in', 'all', 'out', 'loud', 'again',
            'repeat', 'me', 'everything', 'one', 'by', 'it', 'bill'}
# Kannada / Latin-Kannada command words. Every rule below needs the WHOLE utterance to be made of
# these (a Kannada question that merely contains "ಪಟ್ಟಿ" or "ಬದಲಾವಣೆ" is not a command), exact words, no prefixes.
_KN_REMOVE_W = {'ತೆಗೆ', 'ತೆಗೆಯಿರಿ', 'ತೆಗೆಯಿ', 'ತೆಗೆದುಹಾಕು', 'ತೆಗೆದುಹಾಕಿ', 'ಅಳಿಸು', 'ಅಳಿಸಿ', 'ಅಳಿಸಿಹಾಕು', 'ಡಿಲೀಟ್', 'ರಿಮೂವ್'}
_KN_LAST_W = {'ಕೊನೆಯದು', 'ಕೊನೆಯದನ್ನು', 'ಕೊನೆಯ', 'ಕೊನೆಯದ', 'ಅದನ್ನು', 'ಇದನ್ನು', 'ಲಾಸ್ಟ್', 'ಹಿಂದಿನದು', 'ಹಿಂದಿನದನ್ನು'}
_KN_CHANGE_W = {'ಬದಲಿಸು', 'ಬದಲಿಸಿ', 'ಬದಲಾಯಿಸು', 'ಬದಲಾಯಿಸಿ', 'ಬದಲಾಯ್ಸು', 'ಬದಲಿಸಿರಿ', 'ಬದಲು', 'ಬದಲ್'}
_KN_OTHER_W = {'ಬೇರೆ', 'ಇನ್ನೊಂದು', 'ಮತ್ತೊಂದು'}
_KN_ALL_W = {'ಎಲ್ಲಾ', 'ಎಲ್ಲ', 'ಪೂರ್ತಿ', 'ಎಲ್ಲವನ್ನು', 'ಎಲ್ಲವನ್ನೂ'}
_KN_LIST_W = {'ಪಟ್ಟಿ', 'ಲಿಸ್ಟ್'}
_KN_READ_W = {'ಓದು', 'ಓದಿ', 'ಹೇಳು', 'ಹೇಳಿ', 'ತೋರಿಸು', 'ತೋರಿಸಿ'}
_LK_REMOVE_W = {'tege', 'tegi', 'tegedu', 'tegiri', 'tegeyiri', 'alisu', 'alisi'}
_LK_LAST_W = {'last', 'laast', 'kone', 'koneya', 'koneyadu', 'konedu', 'hindina', 'hindinadu', 'adannu', 'idannu', 'ondu'}
_COMMAND_WORDS = ({'remove', 'delete', 'undo', 'change', 'switch', 'clear', 'reset', 'total', 'calculate', 'list', 'bill', 'cancel',
                   'lekka', 'leka', 'badal', 'badlau', 'hosa', 'ottu'} | _LK_REMOVE_W | _KN_REMOVE_W | _KN_CHANGE_W | _KN_LIST_W
                  | {'ಲೆಕ್ಕ', 'ಒಟ್ಟು', 'ಟೋಟಲ್', 'ಹೊಸ', 'ಕ್ಲಿಯರ್'})


def _tokens(text):
    toks = _WORD.findall(S.prep_text(text).replace("'", '').replace('’', ''))     # what's -> whats
    # "m r p" spelled out -> mrp
    out, i = [], 0
    while i < len(toks):
        if toks[i:i + 3] == ['m', 'r', 'p']:
            out.append('mrp')
            i += 3
        else:
            out.append(toks[i])
            i += 1
    return out


def match_command(text):
    """-> (command, language, always) or None. command is total | clear | remove_last |
    change_last | list. `language` is 'kn'/'en' when the words themselves say so, else None.

    Only a WHOLE utterance made of command words counts - "total sales last month", "list the top
    10 customers" and a Kannada question that merely contains "ಪಟ್ಟಿ" are questions, not commands.
    `always` = unmistakably meant for the basket even when it is empty ("calculate", "mrp total",
    "lekka madu", "new bill", "undo"). Every other phrase ("total", "what is the total", "next
    one", "clear", "read list") is a command only while a basket is open (or on the basket
    panel): with no basket it is far more likely an ordinary follow-up or a stray word."""
    if re.search(r'\d', S.prep_text(text)):          # a command has no numbers; "sum 85.00, 10" is the brand SUM..., not "sum"
        return None
    toks = _tokens(text)
    t = [w for w in toks if w not in _FILLER]
    if not t or len(t) > 7:
        return None
    s = set(t)
    raw_skel = _skeleton(''.join(w for w in toks if w not in _POLITE))
    kn_script = any(re.search(r'[ಀ-೿]', w) for w in t)

    # ---- Kannada: lekka madu (calculate), written in Latin or Kannada letters ----
    if re.match(r'^leka(mad[a-z]{0,3}|hak[a-z]{0,3})?$', raw_skel):
        return 'total', 'kn', True
    if re.match(r'^otu(esht[a-z]?)?$', raw_skel):
        return 'total', 'kn', False
    for w in t:
        if w.startswith('ಲೆಕ್ಕ') or w.startswith('ಲೆಕ'):
            rest = [x for x in t if x != w]
            tail = w[len('ಲೆಕ್ಕ'):] if w.startswith('ಲೆಕ್ಕ') else w[len('ಲೆಕ'):]
            if all(x in _KN_CALC_TAIL for x in rest) and (not tail or tail in _KN_CALC_TAIL):
                return 'total', 'kn', True
        if w in ('ಒಟ್ಟು', 'ಟೋಟಲ್', 'ಟೊಟಲ್', 'ಕ್ಯಾಲ್ಕುಲೇಟ್') and all(x in _KN_CALC_TAIL or x == w for x in t):
            return 'total', 'kn', w == 'ಕ್ಯಾಲ್ಕುಲೇಟ್'

    # ---- remove last / undo ----
    if s <= _REMOVE_EN and (('undo' in s) or (s & {'remove', 'delete', 'cancel', 'drop', 'erase', 'take', 'clear'}
                                              and s & {'last', 'previous', 'recent', 'latest', 'that'})):
        return 'remove_last', 'en', 'undo' in s
    if s <= _KN_REMOVE_W | _KN_LAST_W and s & _KN_REMOVE_W and s & _KN_LAST_W:
        return 'remove_last', 'kn', False
    if s <= _LK_REMOVE_W | _LK_LAST_W and s & _LK_REMOVE_W and s & (_LK_LAST_W - {'ondu'}):
        return 'remove_last', 'kn', False

    # ---- change last ----
    if s <= _CHANGE_EN and ((s & {'change', 'switch', 'swap'} and s & {'last', 'previous', 'one', 'it', 'that', 'this'})
                            or ('next' in s and s & {'one', 'match', 'product', 'candidate'})
                            or (s & {'other', 'another'} and s & {'one', 'match', 'product'})
                            or ('wrong' in s and 'one' in s)
                            or ('not' in s and s & {'that', 'this'} and 'one' in s)):
        return 'change_last', 'en', False
    if s <= _KN_CHANGE_W | _KN_LAST_W | {'ಒಂದು'} and s & _KN_CHANGE_W:
        return 'change_last', 'kn', False
    if kn_script and s & _KN_OTHER_W and s <= _KN_OTHER_W | {'ಒಂದು', 'ಇದು', 'ಅದು', 'ಇದೆ', 'ಬೇಕು'}:
        return 'change_last', 'kn', False
    if re.match(r'^bad(a)?l[a-z]{0,6}$', t[0]) and all(x in _LK_LAST_W for x in t[1:]):
        return 'change_last', 'kn', False
    if t[0] in ('bere', 'innondu', 'mattondu') and all(x in {'ondu', 'idu', 'adu', 'ide', 'beku'} for x in t[1:]):
        return 'change_last', 'kn', False

    # ---- new bill / clear ----
    if s <= _CLEAR_EN and ('clear' in s or 'reset' in s or ('new' in s and s & {'bill', 'basket', 'list', 'order'})
                           or ('start' in s and s & {'over', 'again', 'new'}) or ('next' in s and 'customer' in s)
                           or (s & {'cancel', 'delete', 'empty'} and s & {'bill', 'basket', 'list', 'all', 'everything', 'order'})):
        return 'clear', 'en', s == {'new', 'bill'}
    if 'ಹೊಸ' in s and s <= {'ಹೊಸ', 'ಬಿಲ್', 'ಬಿಲ್ಲು', 'ಪಟ್ಟಿ', 'ಲಿಸ್ಟ್'} and s & {'ಬಿಲ್', 'ಬಿಲ್ಲು', 'ಪಟ್ಟಿ', 'ಲಿಸ್ಟ್'}:
        return 'clear', 'kn', bool(s & {'ಬಿಲ್', 'ಬಿಲ್ಲು'})
    if s & {'ಕ್ಲಿಯರ್', 'ರೀಸೆಟ್'} and s <= {'ಕ್ಲಿಯರ್', 'ರೀಸೆಟ್'} | _KN_ALL_W:
        return 'clear', 'kn', False
    if s & _KN_ALL_W and s & (_KN_REMOVE_W | {'ಕ್ಲಿಯರ್'}) and s <= _KN_ALL_W | _KN_REMOVE_W | {'ಕ್ಲಿಯರ್'}:
        return 'clear', 'kn', False
    if 'hosa' in s and s <= {'hosa', 'bill', 'billu', 'patti', 'list'} and s & {'bill', 'billu', 'patti', 'list'}:
        return 'clear', 'kn', bool(s & {'bill', 'billu'})
    if 'clear' in s and s <= {'ellaa', 'ella', 'purti', 'clear', 'alisi', 'alisu', 'tegi'}:
        return 'clear', 'kn', False

    # ---- read the list ----
    if s <= _LIST_EN and s & {'list', 'items', 'basket'} and (s & {'read', 'show', 'what', 'whats', 'back', 'repeat'} or len(s) == 1):
        return 'list', 'en', False
    if s & _KN_LIST_W and s & _KN_READ_W and s <= _KN_LIST_W | _KN_READ_W:
        return 'list', 'kn', False
    if s & {'patti', 'list'} and s & {'odu', 'odi', 'helu', 'heli', 'torisu', 'torisi'} and s <= {'patti', 'list', 'odu', 'odi', 'helu', 'heli', 'torisu', 'torisi'}:
        return 'list', 'kn', False
    if s == {'read', 'back'}:
        return 'list', 'en', False

    # ---- calculate / total ----
    if s <= {'add', 'up', 'it', 'all'} and {'add', 'up'} <= s:
        return 'total', 'en', False
    if s <= _TOTAL_EN and s & {'calculate', 'calculation', 'calculations', 'total', 'totals', 'sum'}:
        return 'total', 'en', bool(s & {'calculate', 'calculation', 'calculations', 'mrp'})
    return None


def _looks_unfinished_command(text):
    """A one- or two-word scrap made only of basket command words ("remove", "change", "lekka") -
    worth a "please repeat", not a "hmm". Whole words only: "listen", "billing", "totally" are not."""
    toks = _tokens(text)
    return (0 < len(toks) <= 2 and all(w in _COMMAND_WORDS or w in {'last', 'it', 'that', 'one', 'all', 'the', 'please', 'this'} for w in toks)
            and any(w in _COMMAND_WORDS for w in toks))


# ---- items ----------------------------------------------------------------------------
_LEAD_FILLER = {'add', 'next', 'item', 'also', 'and', 'then', 'ok', 'okay', 'please', 'pls', 'now', 'subbi', 'basket', 'to',
                'the', 'is', 'it', 'for', 'of', 'another', 'new'}
_TAIL_FILLER = {'please', 'pls', 'thanks', 'thank', 'you', 'ok', 'okay', 'only', 'ಧನ್ಯವಾದ'}
_SKIP = {'and', 'of', 'x', 'mrp', 'price', 'quantity', 'qty', 'number', 'for', 'is', 'at', 'ಮತ್ತು', 'ಮೇಲೆ', 'with'}
_STRIP_W = {'strip', 'strips', 'patta', 'pattas', 'patti', 'pattis', 'ಸ್ಟ್ರಿಪ್', 'ಸ್ಟ್ರಿಪ್ಸ್', 'ಸ್ಟ್ರಿಪ್ಪು', 'पट्टा', 'पट्टी', 'पट्टे', 'स्ट्रिप'}
_UNIT_W = {'tablet', 'tablets', 'tab', 'tabs', 'cap', 'caps', 'capsule', 'capsules', 'pcs', 'pc', 'piece', 'pieces', 'nos', 'no',
           'units', 'unit', 'ಮಾತ್ರೆ', 'ಮಾತ್ರೆಗಳು', 'ಟ್ಯಾಬ್ಲೆಟ್', 'ಟ್ಯಾಬ್ಲೆಟ್ಸ್', 'ಕ್ಯಾಪ್ಸೂಲ್', 'ಕ್ಯಾಪ್ಸೂಲ್ಸ್', 'गोली', 'गोलियां', 'टैबलेट', 'कैप्सूल'}
_RUPEE_W = {'rupees', 'rupee', 'rupaye', 'rupaiya', 'rs', 'inr', 'ರೂಪಾಯಿ', 'ರೂ', 'ರೂಪಾಯಿಗಳು', 'रुपये', 'रुपए', 'रुपया', 'रु'}
_PAISE_W = {'paise', 'paisa', 'paisey', 'ps', 'ಪೈಸೆ', 'ಪೈಸೆಗಳು', 'पैसे', 'पैसा'}
_GAP = r'[\s,.:;!?-]*'


def _mrp_options(s):
    """Ways to read one number as an MRP: [(rupees as Decimal, how)], most likely first."""
    if '.' in s:
        return [(Decimal(s), 'typed')] if len(s.split('.', 1)[1]) <= 2 else []
    n = len(s.lstrip('0') or '0')
    if n >= 4:
        return [(Decimal(s) / 100, 'paise-digits'), (Decimal(s), 'whole')]       # 6560 -> 65.60, else 6560
    if n == 3:
        return [(Decimal(s), 'whole'), (Decimal(s) / 100, 'paise-digits')]       # 650 -> 650, else 6.50
    return [(Decimal(s), 'whole')]


def _paise_options(s):
    """'60' -> [.60]; '6' -> [.60, .06] (sixty five six: 65.60 or 65.06)."""
    if not s.isdigit() or len(s) > 2:
        return []
    d = int(s)
    out = [Decimal(d) / 100] if len(s) == 2 else [Decimal(d * 10) / 100, Decimal(d) / 100]
    return [v for v in out if v < 1]


def _split_code(text):
    """The 3-letter code at the start of "eco 65.60, 30" - read BEFORE numbers are turned into
    digits (so a brand that is also a number word, like DAS, stays a brand) - or the same
    letters spelled one by one ("E C O"). -> (CODE, the rest of the text) or None."""
    t = S.prep_text(text)
    pos = 0
    while True:
        m = re.match(_GAP + r'([a-z]+)', t[pos:])
        if not m:
            return None
        # "add eco 65.60, 30": 'add' is filler - but "for 55.00, 10" / "new 40.50, 5" are the brands FOR... / NEW...
        if m.group(1) in _LEAD_FILLER and re.match(_GAP + r'[a-z]', t[pos + m.end():]):
            pos += m.end()
            continue
        break
    if len(m.group(1)) == 3:
        return m.group(1).upper(), t[pos + m.end():]
    letters, p = [], pos
    for _ in range(3):
        m = re.match(_GAP + r'([a-z])(?![a-z])', t[p:])
        if not m:
            return None
        letters.append(m.group(1))
        p += m.end()
    return ''.join(letters).upper(), t[p:]


def _split_qty(rest_text):
    """The tokens after the code -> ([{'v': '65.60', 'tag': 'R' | 'P' | None, 'comma': bool}], basis) or
    None (a word that belongs to no item). tag = the owner said "rupees" / "paise" next to it;
    comma = there was a pause (comma) between the previous number and this one; basis = 'strips'
    when a strip word was said."""
    toks = S.numberize(rest_text)
    while toks and toks[-1][0] == 'w' and toks[-1][1] in _TAIL_FILLER:
        toks.pop()
    nums, basis, pre, comma = [], 'units', None, False
    for kind, *val in toks:
        if kind == ',':
            comma = True
            continue
        if kind == 'n':
            if len(val[0].split('.')[0]) > 9:
                return None
            nums.append({'v': val[0], 'tag': pre, 'comma': comma and bool(nums)})
            pre, comma = None, False
            continue
        w = val[0]
        if w in _STRIP_W:
            basis = 'strips'
        elif w in _RUPEE_W or w in _PAISE_W:
            tag = 'R' if w in _RUPEE_W else 'P'
            if nums and nums[-1]['tag'] is None:
                nums[-1]['tag'] = tag            # "65 rupees"
            else:
                pre = tag                        # "rupees 65"
        elif w not in _UNIT_W and w not in _SKIP:
            return None
    return nums, basis


def _readings(nums):
    """Every way the numbers can be (MRP, quantity), most likely first: [(Decimal MRP, how,
    quantity str | None)]. A quantity is the LAST number; what comes before it is the MRP."""
    out = []
    rup = [n for n in nums if n['tag'] == 'R']
    pais = [n for n in nums if n['tag'] == 'P']
    plain_n = [n for n in nums if n['tag'] is None]
    plain = [n['v'] for n in plain_n]
    if rup or pais:                                               # "65 rupees 60 paise, 30"
        if not rup:
            return []
        qty = plain[-1] if plain else None
        if pais:
            for p in _paise_options(pais[0]['v']):
                if rup[0]['v'].isdigit():
                    out.append((Decimal(rup[0]['v']) + p, 'rupees-paise', qty))
        else:
            out += [(m, how, qty) for m, how in _mrp_options(rup[0]['v'])]
        return out
    v = plain
    if len(v) == 1:
        out += [(m, how, None) for m, how in _mrp_options(v[0])]
    elif len(v) == 2:
        out += [(m, how, v[1]) for m, how in _mrp_options(v[0])]                 # 65.60 , 30
        if not plain_n[1]['comma']:                    # "24, 50" (a pause) is a price and a quantity, not 24.50
            out += [(Decimal(v[0]) + p, 'rupees-paise', None) for p in _paise_options(v[1]) if v[0].isdigit()]   # 65 60 (no quantity)
    elif len(v) == 3:
        if v[0].isdigit():
            out += [(Decimal(v[0]) + p, 'rupees-paise', v[2]) for p in _paise_options(v[1])]    # 65 60 30
        if len(v[0]) == 1 and v[0].isdigit() and v[1].replace('.', '', 1).isdigit() and len(v[1].split('.')[0]) == 2:
            out.append((Decimal(v[0]) * 100 + Decimal(v[1]), 'typed' if '.' in v[1] else 'whole', v[2]))   # one twenty five (point five zero) 10
    elif len(v) == 4 and all(x.isdigit() for x in v[:3]) and len(v[0]) == 1 and len(v[1]) == 2:
        out += [(Decimal(v[0] + v[1]) + p, 'rupees-paise', v[3]) for p in _paise_options(v[2])]   # one twenty five sixty thirty
    return out


def parse_item(text):
    """Structure only (no catalog): the quick-code item "eco 65.60, 30" in any of the spoken
    forms. -> None, or {'code', 'basis', 'numbers', 'readings': [(said, qty | None, how, mrp)]}
    with the readings most likely first - the caller keeps the best one the catalog knows."""
    got = _split_code(text)
    if got is None:
        return None
    code, rest = got
    parts = _split_qty(rest)
    if parts is None:
        return None
    nums, basis = parts
    readings = _readings(nums)
    if not readings:
        return None
    out = []
    for mrp, how, qty in readings:
        said = f'{code} {int(mrp)}' if how == 'whole' else f'{code} {mrp:.2f}'     # no point = may also be a name + strength ("Pan 40")
        out.append((said, qty, how, mrp))
    return {'code': code, 'basis': basis, 'numbers': len(nums), 'readings': out}


def parse_name_item(text):
    """A product named in words, then the quantity: "Ecosprin AV 75, 30" -> ('ecosprin av 75',
    '30', 'units'). Only for a first word of 4+ letters - a three-letter one is the quick code
    and goes through parse_item."""
    toks = S.numberize(text)
    while toks and toks[0][0] == 'w' and toks[0][1] in _LEAD_FILLER:
        toks.pop(0)
    while toks and toks[-1][0] == 'w' and toks[-1][1] in _TAIL_FILLER:
        toks.pop()
    if len(toks) < 3 or toks[0][0] != 'w' or len(toks[0][1]) < 4:
        return None
    last_n = max((i for i, t in enumerate(toks) if t[0] == 'n'), default=0)
    if last_n == 0:
        return None
    basis = 'units'
    for t in toks[last_n + 1:]:
        if t[0] != 'w' or (t[1] not in _STRIP_W and t[1] not in _UNIT_W):
            return None
        if t[1] in _STRIP_W:
            basis = 'strips'
    name = toks[:last_n]
    while name and name[-1][0] == ',':
        name.pop()
    if len(name) > 6 or any(t[0] == ',' for t in name):
        return None                                              # several items in one breath: leave it to the model
    return ' '.join(t[1] for t in name), toks[last_n][1], basis


def _foreign_letters_item(text, opened=False):
    """True for "ಇಕೋ 65.60, 30": an item whose brand letters came as a short Kannada/Hindi-script word.
    Matching those sounds to the English letters is not done - the owner is asked to say the letters
    in English. Only when it looks like the quick code (an MRP with paise) or a basket is open: a
    script product name + a strength ("ಡೋಲೋ 650, 30") or a date is left to the model."""
    t = S.prep_text(text)
    m = re.match(r'[\s,.:;!?-]*([\u0900-\u097f\u0c80-\u0cff]+)(.*)$', t, re.S)
    if not m or len(m.group(1)) > 5 or any(tok[0] == 'n' for tok in S.numberize(m.group(1))):
        return False
    parts = _split_qty(m.group(2))
    if not (parts and len(parts[0]) >= 2 and _readings(parts[0])):
        return False
    return opened or any('.' in n['v'] for n in parts[0])


def classify(owner, text, panel=False):
    """What the owner's words are, decided by structure alone (no model): 'command' | 'item' |
    'name_item' | 'bad_letters' | 'unclear' | None. The speech route uses it to let a basket
    utterance past the stray-word and chatter filters; interpret() acts on it.
    With no basket open only the unmistakable commands count (see match_command) and a product
    NAME counts only when it matches a real product exactly - so an ordinary question never
    becomes a bill line. `panel` = typed on the basket panel: everything is meant for the basket."""
    cmd = match_command(text)
    opened = is_open(owner)
    if cmd:
        return 'command' if (cmd[2] or opened or panel) else None
    it = parse_item(text)
    if it and (it['numbers'] >= 2 or opened or panel):
        return 'item'
    if _foreign_letters_item(text, opened):
        return 'bad_letters'
    nm = parse_name_item(text) if (opened or panel) else None
    if nm and (panel or T._mrp_name_candidates(nm[0], fuzzy=False)):
        return 'name_item'
    if opened and _looks_unfinished_command(text):
        return 'unclear'
    return None


def detect_lang(text):
    return 'kn' if re.search(r'[ಀ-೿]', str(text or '')) else None


def _best_reading(item):
    """Resolve each reading against the catalog and keep the best: a real code + exact MRP
    with a quantity > the same without one > a name guess ("Pan 40") with a quantity.
    -> (resolve result, quantity | None) or (first not-found result, None)."""
    best, best_score, first = None, 0, None
    for said, qty, _how, _mrp in item['readings']:
        res = T.mrp_resolve(said)
        first = first or res
        if not res['found']:
            continue
        score = (3 if qty is not None else 2) if res['matched_by'] else (1 if qty is not None else 0.5)
        if score > best_score:
            best, best_score = (res, qty), score
    return best if best else (first, None)


def interpret(owner, text, lang=None, explicit_add=False):
    """Act on the owner's words if they are a basket command or item; None if they are not.
    -> the same dict the basket_* functions return ('say' is the line to speak).
    explicit_add: typed on the basket panel, so everything is meant for the basket: a weak command
    ("total") works on an empty basket, and a product named in words ("Ecosprin AV 75, 30") is an item
    even if its spelling is only close. From the Ask page / voice a name is an item only when a basket
    is already open AND it matches a real product exactly."""
    kind = classify(owner, text, panel=explicit_add)
    if kind is None:
        return None
    b = _get(owner)
    if b is None:
        return {'ok': False, 'action': kind, 'say': MSG['en']['no_login'], 'basket': snapshot(None)}
    if kind == 'command':
        cmd, cmd_lang, _strong = match_command(text)
        lang = detect_lang(text) or lang or cmd_lang
        return {'total': basket_total, 'clear': basket_clear, 'remove_last': basket_remove_last,
                'change_last': basket_change_last, 'list': basket_list}[cmd](owner, lang=lang)
    with _lock:
        lang = _lang(b, detect_lang(text) or lang)
    L = MSG[lang]
    if kind == 'unclear':
        return _result(owner, L['repeat'], ok=False, action='unclear')
    if kind == 'bad_letters':
        return _result(owner, L['bad_letters'], ok=False, action='add')
    if kind == 'name_item':
        name, qty, basis = parse_name_item(text)
        return basket_add(owner, name, **({'strips': qty} if basis == 'strips' else {'units': qty}), lang=lang, fuzzy=bool(explicit_add))
    it = parse_item(text)
    res, qty = _best_reading(it)                      # catalog lookups: outside the lock
    if res['found'] and qty is not None:
        return _add_checked(owner, b, lang, res, qty, 'strips' if it['basis'] == 'strips' else 'units')
    if res['found']:
        c = res['candidates'][0]
        return _result(owner, L['no_qty'](speak_name(c['product']), f"{c['mrp_per_strip']:.2f}"), ok=False, action='add')
    return _result(owner, _not_found_say(lang, res), ok=False, action='add', reason=res['reason'])


# ======================================================================================
# The model's view: tools that call the functions above for the logged-in owner
# ======================================================================================
_LANG_PARAM = {'language': {'type': 'string', 'enum': ['en', 'kn'], 'description': "'kn' if the owner spoke Kannada, else 'en'"}}


def _schema(props, required=()):
    return {'type': 'object', 'properties': props, 'required': list(required)}


_SAY = (" The result's 'say' is the exact line to speak - say it as it is, add nothing, and never add a grand total "
        "unless the owner asked for it.")
_SPECS = [
    {'name': 'basket_add', 'fn': lambda product, units=None, strips=None, language=None:
        basket_add(OWNER.get(), product, units=units, strips=strips, lang=language),
     'description': ("Add ONE item to the owner's MRP basket (the running bill whose MRP total is read out on request). "
                     "`product` = what the owner said: a name, or the quick code (first 3 letters + the pack's exact MRP, "
                     "e.g. 'ECO 65.60'). `units` = tablets/capsules, or `strips` = number of strips." + _SAY),
     'parameters': _schema({'product': {'type': 'string'}, 'units': {'type': 'number'}, 'strips': {'type': 'number'},
                            **_LANG_PARAM}, ['product'])},
    {'name': 'basket_remove_last', 'fn': lambda language=None: basket_remove_last(OWNER.get(), lang=language),
     'description': "Remove the last item from the MRP basket ('remove last', 'undo')." + _SAY,
     'parameters': _schema(_LANG_PARAM)},
    {'name': 'basket_change_last', 'fn': lambda language=None: basket_change_last(OWNER.get(), lang=language),
     'description': "Switch the last basket item to its next candidate product ('change last')." + _SAY,
     'parameters': _schema(_LANG_PARAM)},
    {'name': 'basket_list', 'fn': lambda language=None: basket_list(OWNER.get(), lang=language),
     'description': "Read the MRP basket's items back, without a total ('read list')." + _SAY,
     'parameters': _schema(_LANG_PARAM)},
    {'name': 'basket_total', 'fn': lambda language=None: basket_total(OWNER.get(), lang=language),
     'description': ("Read every MRP basket item with its line total, then the GRAND TOTAL ('calculate', 'total', 'MRP total', "
                     "'lekka madu'). The basket is kept afterwards." + _SAY),
     'parameters': _schema(_LANG_PARAM)},
    {'name': 'basket_clear', 'fn': lambda language=None: basket_clear(OWNER.get(), lang=language),
     'description': "Empty the MRP basket ('new bill', 'clear')." + _SAY,
     'parameters': _schema(_LANG_PARAM)},
]
TOOL_NAMES = {s['name'] for s in _SPECS}


def tool_specs():
    return _SPECS


def is_basket_tool(name):
    return name in TOOL_NAMES


def run(name, args):
    spec = next((s for s in _SPECS if s['name'] == name), None)
    if spec is None:
        return {'error': f'unknown tool {name}'}
    if not OWNER.get():
        return {'error': 'the MRP basket needs a logged-in session - not available here'}
    try:
        out = spec['fn'](**(args or {}))
    except TypeError as e:
        return {'error': f'bad arguments for {name}: {e}'}
    except Exception as e:                      # whatever the model sent, it gets an error result, not a crash
        return {'error': f'{type(e).__name__}: {e}'}
    # The model gets what it must say and nothing to retell: no item list, no running total.
    out = dict(out)
    out['items_in_basket'] = (out.pop('basket', None) or {}).get('count', 0)
    return out

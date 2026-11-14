"""Food and medicine advice (the owner's request, 13 Nov 2026): for a prescription or a bill - how to take each medicine
(empty stomach / before / with / after food), the foods to AVOID, to KEEP APART from the dose, to LIMIT, and the foods that
are GOOD with these medicines - as one patient-ready guide.

LIVE, nothing stored (the owner: "just make connection, don't download and keep any data"): every request asks a strong AI
model (FOOD_ADVICE_MODEL, default Claude Opus, through OpenRouter) about exactly the medicines on that prescription or bill,
in one call, and the answer is used for that request only - no food list is kept on disk or in memory. The model must answer
with foods from a FIXED list of Indian food groups (below), never free text, so several medicines can be combined by plain
rules here: the strictest level wins, a "good" food that another medicine says to avoid is dropped, a combination tablet gets
its own meal timing. The model marks what it is unsure of, and the guide then tells the staff to have the pharmacist confirm.
Only product names, compositions and drug classes are sent - never a patient's name, number or the bill amount.

The few records already written before that decision (data/food_advice/food_saved.json, 90 salts - the owner: "if you have
made any list of food already, make use of it") are used: the 12 that two models wrote independently and agreed on are used
as they are (no AI call for those); the others go to the live model as a draft to check and correct. Nothing is added to it.
"""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

SAVED_PATH = Path(__file__).parent / 'data' / 'food_advice' / 'food_saved.json'
MODEL_DEFAULT = 'anthropic/claude-opus-5.5'
TIMEOUT_S = 90

# --- the fixed vocabulary -----------------------------------------------------------------------------------------------
TIMINGS = {
    'empty_stomach': 'on an empty stomach (1 hour before or 2 hours after food)',
    'before_food': 'before food (15-30 minutes before the meal)',
    'with_food': 'with food (during the meal)',
    'after_food': 'after food',
    'with_or_after_food': 'with or just after food',
    'any_time': 'with or without food',
    'not_applicable': 'no meal timing (not taken by mouth)',
}
TIMING_CODES = ['empty_stomach', 'before_food', 'with_food', 'after_food', 'any_time', 'not_applicable']

FOODS = {           # what may be AVOIDED / kept apart / limited / kept steady
    'alcohol': 'Alcohol',
    'grapefruit': 'Grapefruit, pomelo (chakotha) or their juice',
    'milk_dairy': 'Milk, curd, buttermilk, paneer, cheese',
    'calcium_iron_antacid': 'Calcium or iron tablets, antacids, multivitamins with minerals',
    'tea_coffee': 'Tea, coffee, cola, energy drinks',
    'fruit_juice': 'Fruit juices (orange, apple, mosambi)',
    'high_potassium': 'High-potassium foods (banana, coconut water, oranges, tomato, potato, dry fruits) and salt substitutes',
    'green_leafy_vk': 'Green leafy vegetables (palak, methi, cabbage, broccoli)',
    'high_salt': 'Extra salt, pickles, papad, chips, salted snacks',
    'sugar_sweets': 'Sugar, sweets, jaggery, honey, sweet drinks',
    'fatty_fried': 'Fried and very fatty food',
    'tyramine': 'Aged cheese, fermented or preserved foods, soy sauce, processed meat',
    'soy': 'Soy foods (soybean, soy milk, tofu)',
    'high_fibre_near_dose': 'High-fibre food or bran (isabgol) right around the dose',
    'spicy_acidic': 'Spicy, sour or very acidic food',
    'high_purine': 'Red meat, organ meat, seafood',
    'liquorice': 'Liquorice (jeshtamadhu / mulethi)',
    'herbal_remedies': 'Herbal or ayurvedic medicines',
}
LEVELS = {          # strictest first
    'avoid': 'Avoid',
    'space': 'Keep apart from the dose',
    'steady': 'Keep the amount steady (no sudden big changes)',
    'limit': 'Limit',
}
LEVEL_RANK = {k: i for i, k in enumerate(LEVELS)}

GOODS = {           # foods / habits that go WELL with a medicine
    'plenty_water': 'Plenty of water through the day',
    'regular_meals': 'Meals at regular times - do not skip meals',
    'vegetables_millets': 'Vegetables, salads, whole grains and millets (ragi, jowar)',
    'low_sugar_fruits': 'Fruits low in sugar (guava, papaya, apple) in moderation',
    'potassium_foods': 'Potassium-rich foods (banana, coconut water, oranges)',
    'calcium_foods': 'Calcium-rich foods (milk, curd, ragi)',
    'vitamin_c_foods': 'Vitamin C foods (amla, lemon, guava, orange)',
    'iron_foods': 'Iron-rich foods (green leafy vegetables, dates, ragi)',
    'curd_buttermilk': 'Curd or buttermilk (good for the stomach)',
    'light_food': 'Light, home-cooked food; small frequent meals',
    'low_salt_food': 'Less salt in cooking',
    'high_fibre_food': 'Fibre-rich food (fruits, vegetables, whole grains)',
    'fat_with_dose': 'A little fat in the meal with the dose (ghee, milk, nuts) - helps it absorb',
    'protein_foods': 'Protein foods (dal, pulses, eggs, sprouts)',
    'glucose_handy': 'Keep glucose or sugar with you in case sugar goes too low',
}
GOOD_CONFLICTS = {  # a good food is dropped (avoid) or qualified (other levels) when another medicine restricts this food
    'potassium_foods': ['high_potassium'],
    'calcium_foods': ['milk_dairy', 'calcium_iron_antacid'],
    'curd_buttermilk': ['milk_dairy'],
    'iron_foods': ['green_leafy_vk', 'calcium_iron_antacid'],
    'vitamin_c_foods': ['fruit_juice', 'grapefruit'],
    'fat_with_dose': ['fatty_fried'],
    'high_fibre_food': ['high_fibre_near_dose'],
    'low_sugar_fruits': ['high_potassium'],
    'vegetables_millets': ['green_leafy_vk'],
    'glucose_handy': [],
}
SYSTEMIC_ROUTES = {'oral', 'sublingual', 'buccal', 'injectable', 'rectal', 'transdermal'}
KB_KINDS = {'medicine', 'supplement'}
ROUTE_WORDS = {'topical': 'used on the skin', 'external_use': 'used on the skin', 'ophthalmic': 'eye drops / eye ointment',
               'otic': 'ear drops', 'nasal': 'used in the nose', 'inhalation': 'inhaled', 'vaginal': 'used locally',
               'not_applicable': 'not a medicine taken by mouth'}
FOOTER = ("General guidance for these medicines from our pharmacy. The doctor's advice comes first - "
          "please ask our pharmacist if you are unsure.")


# --- the live question --------------------------------------------------------------------------------------------------
AVOID_ITEM = {'type': 'object', 'additionalProperties': False, 'required': ['food', 'level', 'gap_hours', 'why'],
              'properties': {'food': {'type': 'string', 'enum': list(FOODS)}, 'level': {'type': 'string', 'enum': list(LEVELS)},
                             'gap_hours': {'type': 'number'}, 'why': {'type': 'string'}}}
SALT_RECORD = {'type': 'object', 'additionalProperties': False,
               'required': ['salt', 'timing', 'timing_note', 'avoid', 'good', 'review'],
               'properties': {'salt': {'type': 'string'}, 'timing': {'type': 'string', 'enum': TIMING_CODES},
                              'timing_note': {'type': 'string'}, 'avoid': {'type': 'array', 'items': AVOID_ITEM},
                              'good': {'type': 'array', 'items': {'type': 'string', 'enum': list(GOODS)}},
                              'review': {'type': 'boolean'}}}
COMBO_RECORD = {'type': 'object', 'additionalProperties': False, 'required': ['product', 'timing', 'timing_note', 'review'],
                'properties': {'product': {'type': 'string'}, 'timing': {'type': 'string', 'enum': TIMING_CODES},
                               'timing_note': {'type': 'string'}, 'review': {'type': 'boolean'}}}
ANSWER = {'type': 'object', 'additionalProperties': False, 'required': ['salts', 'combinations'],
          'properties': {'salts': {'type': 'array', 'items': SALT_RECORD},
                         'combinations': {'type': 'array', 'items': COMBO_RECORD}}}

RULES = """You are a senior clinical pharmacist in India writing the food guidance that a retail pharmacy in Karnataka gives a
patient with their medicines. Use standard references (product labels / SmPC, BNF, Lexicomp, Indian National Formulary).
Accuracy matters more than length: a wrong "avoid" frightens patients off safe food, a missing one can harm them.

For EACH salt listed write one record in "salts" (the salt copied exactly):
- timing: when this salt is taken relative to meals. Insulin: regular / soluble human insulin and premixes 'before_food';
  rapid analogues 'with_food' (just before or with the meal); basal insulins 'any_time'. Other injections and vaccines:
  'not_applicable'. If food truly does not matter: 'any_time'.
- timing_note: at most 110 characters of practical plain English ("30-60 minutes before breakfast, with water only"), or ""
  when there is nothing to add. Never a dose or a number of tablets. About this salt on its own.
- avoid: ONLY clinically meaningful drug-food interactions, or diet rules firmly tied to what the drug treats (sugar for
  antidiabetics, salt for antihypertensives, purines for gout). The right level; gap_hours only for 'space' (dairy and
  calcium / iron 2 hours from ciprofloxacin, 4 hours from levothyroxine), 0 otherwise. Alcohol only for a real interaction
  (disulfiram-like reaction, sedation, low sugar, liver or stomach-bleeding risk). An empty list is right for most salts.
  why: plain words for the patient, at most 80 characters.
- good: only foods / habits that genuinely help a patient on this drug (regular_meals and glucose_handy for sulfonylureas
  and insulin; calcium_foods and fat_with_dose for vitamin D; vitamin_c_foods for iron; plenty_water where it matters).
  Never a good food this same drug restricts. Empty is fine.
- review: true when you are not sure of any part (a pharmacist will then confirm it).
- Not really a drug (flavour, excipient, herbal oil, food ingredient): any_time, empty lists.
- A salt may come with an EARLIER DRAFT written by another model: check it against the references, keep what is right,
  correct what is wrong - your record is the one used.

For EACH combination product listed (several salts in one tablet) also write one record in "combinations" (the product
copied exactly): the meal timing for the COMBINED product as it is labelled / usually advised in India, a short timing_note,
review = true if unsure. "combinations" is empty when none are listed."""


def vocabulary():
    return ('MEAL TIMING codes: ' + '; '.join('%s = %s' % (k, v) for k, v in TIMINGS.items() if k in TIMING_CODES)
            + '\nFOOD codes (for "avoid"): ' + '; '.join('%s = %s' % kv for kv in FOODS.items())
            + '\nLEVELS: avoid = do not take it during the treatment; space = keep it gap_hours apart from the dose; steady = '
              'keep the amount the same every day, no sudden big changes; limit = cut down'
            + '\nGOOD codes: ' + '; '.join('%s = %s' % kv for kv in GOODS.items()))


def model():
    return os.environ.get('FOOD_ADVICE_MODEL', '').strip() or MODEL_DEFAULT


def openrouter_call(system, user, schema):
    """One question to the model through OpenRouter -> (the parsed answer, cost in USD)."""
    key = os.environ.get('OPENROUTER_API_KEY')
    if not key:
        raise RuntimeError('OPENROUTER_API_KEY is missing from .env')
    m = model()
    body = {'model': m, 'max_tokens': 12000, 'usage': {'include': True},
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            'response_format': {'type': 'json_schema', 'json_schema': {'name': 'food_advice', 'strict': True, 'schema': schema}}}
    if m.startswith('anthropic/'):
        body['reasoning'] = {'max_tokens': 2000}
    req = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                                          'X-Title': 'Pharmacy Reckoner - food advice'})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        resp = json.loads(r.read())
    text = resp['choices'][0]['message']['content'] or ''
    return json.loads(text[text.find('{'):text.rfind('}') + 1]), float((resp.get('usage') or {}).get('cost') or 0)


def load_saved(path=None):
    """{salt: record} written before the live connection (read-only; never added to)."""
    p = Path(path) if path else SAVED_PATH
    try:
        return json.loads(p.read_text(encoding='utf-8')).get('salts') or {}
    except (OSError, ValueError):
        return {}


def needs_advice(row):
    return str(row.get('KIND') or '') in KB_KINDS and str(row.get('ROUTE') or '').lower() in SYSTEMIC_ROUTES | {''}


def ask_live(rows, call=None, saved=None):
    """The products' salts (and their combinations) -> ({'salts': {salt: record}, 'combos': {combo key: record}}, cost).
    Salts with a checked saved record use it; the rest (and any combination whose salts want different meal timings) are
    asked about live, in one call, with a saved draft when there is one. Nothing is kept. `call` is the model call (a stub
    in the tests); `saved` the saved records (food_saved.json by default)."""
    rows = [r for r in rows if needs_advice(r)]
    saved = load_saved() if saved is None else saved
    kb = {'salts': {}, 'combos': {}}
    salts, combos = {}, {}
    for r in rows:
        for s in sorted(r.get('_salts') or []):
            if (saved.get(s) or {}).get('checked'):
                kb['salts'][s] = saved[s]
            else:
                salts.setdefault(s, '%s (%s; %s, %s)' % (r.get('PRODUCT', ''), r.get('COMPOSITION', ''), r.get('THERAPEUTIC CLASS', ''),
                                                         r.get('DOSAGE FORM', '')))
    for r in rows:
        ss = sorted(r.get('_salts') or [])
        if len(ss) > 1 and not (all(s in kb['salts'] for s in ss) and merge_timing([kb['salts'][s]['timing'] for s in ss])):
            combos[r.get('PRODUCT', '')] = r
    if not salts and not combos:
        return kb, 0.0

    def line(s, ctx):
        d = saved.get(s)
        draft = '\n    EARLIER DRAFT: %s' % json.dumps({k: d[k] for k in ('timing', 'timing_note', 'avoid', 'good') if k in d},
                                                      ensure_ascii=False) if d else ''
        return '- %s  [from %s]%s' % (s, ctx, draft)

    user = (vocabulary() + '\n\nSALTS (with the product they come from):\n' + ('\n'.join(line(*kv) for kv in salts.items()) or '(none)')
            + '\n\nCOMBINATION PRODUCTS:\n' + ('\n'.join('- %s  [%s]' % (p, r.get('COMPOSITION', '')) for p, r in combos.items()) or '(none)'))
    answer, cost = (call or openrouter_call)(RULES, user, ANSWER)
    for rec in answer.get('salts') or []:
        name = str(rec.get('salt') or '').strip().lower()
        if name in salts and rec.get('timing') in TIMINGS:
            kb['salts'][name] = rec
    for rec in answer.get('combinations') or []:
        r = combos.get(str(rec.get('product') or '').strip())
        if r is not None and rec.get('timing') in TIMINGS:
            kb['combos'][combo_key(r['_salts'])] = rec
    return kb, cost


def combo_key(salts):
    return ' + '.join(sorted(s.strip().lower() for s in salts if s and s.strip()))


def merge_timing(codes):
    """The meal timing for a medicine made of several salts -> a TIMINGS code, or None when they really disagree
    (one wants an empty stomach, another food). 'any_time' and 'not_applicable' never decide anything."""
    c = {x for x in codes if x and x not in ('any_time', 'not_applicable')}
    if not c:
        return 'any_time' if 'any_time' in codes else ('not_applicable' if codes else 'any_time')
    if len(c) == 1:
        return next(iter(c))
    if c <= {'with_food', 'after_food'}:
        return 'with_or_after_food'
    if c <= {'empty_stomach', 'before_food'}:
        return 'empty_stomach'
    return None


def short_name(product):
    """'GLYCOMET 500MG TAB' -> 'Glycomet 500mg' - for the lists, where the full pack name is noise."""
    words = re.sub(r'\([^)]*\)', ' ', str(product or '')).split()        # "(TIN)", "(NEW)": pack words
    keep = [w for w in words if w.upper().strip('.') not in {'TAB', 'TABS', 'TABLET', 'TABLETS', 'CAP', 'CAPS', 'CAPSULE', 'CAPSULES',
                                                               'SYP', 'SYRUP', 'INJ', 'INJECTION', 'SUSP', 'SUSPENSION', '10S', '15S', '30S'}]
    units = {'MG', 'MCG', 'ML', 'GM', 'G', 'IU', 'KG'}
    return ' '.join(w.lower() if w.upper() in units or not w.isalpha() else (w if len(w) <= 2 else w.capitalize())
                    for w in (keep or words))


def product_advice(row, kb):
    """One product (a row of the enriched list: PRODUCT, KIND, ROUTE, SALTS, COMPOSITION, DOSAGE FORM, _salts) -> its advice:
    {'product', 'composition', 'kind': 'advice' | 'no_rules' | 'no_record', 'timing', 'timing_text', 'notes', 'avoid', 'good',
     'review'}."""
    salts = sorted(row.get('_salts') or [])
    out = {'product': row.get('PRODUCT', ''), 'short': short_name(row.get('PRODUCT', '')), 'composition': row.get('COMPOSITION', ''),
           'salts': salts, 'avoid': [], 'good': [], 'notes': [], 'review': False}
    route = str(row.get('ROUTE') or '').lower()
    if str(row.get('KIND') or '') not in KB_KINDS:
        out.update(kind='no_record', why='not a medicine (%s)' % str(row.get('KIND') or 'other').replace('_', ' '))
        return out
    if route and route not in SYSTEMIC_ROUTES:
        out.update(kind='no_rules', why=ROUTE_WORDS.get(route, 'not taken by mouth'))
        return out
    recs = [(s, kb['salts'].get(s)) for s in salts]
    missing = [s for s, r in recs if not r]
    recs = [(s, r) for s, r in recs if r]
    if not recs:
        out.update(kind='no_record', why='no food advice could be worked out for %s' % (', '.join(salts) or 'this product'))
        return out
    timing = merge_timing([r.get('timing') for _s, r in recs])
    notes = []
    combo = kb['combos'].get(combo_key(salts)) if len(salts) > 1 else None
    if combo and combo.get('timing') in TIMINGS:
        timing = combo['timing']
        if combo.get('timing_note'):
            notes.append(combo['timing_note'])
        out['review'] = out['review'] or bool(combo.get('review'))
    elif timing is None:
        out['review'] = True
    else:
        for _s, r in recs:
            n = str(r.get('timing_note') or '').strip()
            if n and n not in notes:
                notes.append(n)
    out['timing'] = timing or 'ask'
    out['timing_text'] = TIMINGS.get(timing, "at the times the doctor has written - please ask our pharmacist about meals")
    out['notes'] = notes[:3]
    for s, r in recs:
        for a in r.get('avoid') or []:
            if a.get('food') in FOODS and a.get('level') in LEVELS:
                out['avoid'].append({'food': a['food'], 'level': a['level'], 'gap_hours': a.get('gap_hours') or 0,
                                     'why': str(a.get('why') or '').strip(), 'salt': s})
        out['good'] += [g for g in (r.get('good') or []) if g in GOODS and g not in out['good']]
        out['review'] = out['review'] or bool(r.get('review'))
    if missing:
        out['notes'].append('no food advice could be worked out for %s' % ', '.join(missing))
        out['review'] = True
    out['kind'] = 'advice'
    return out


def combine(items):
    """Several products' advice (product_advice) -> one guide: {'how_to_take', 'restrict': [{food, level, gap_hours, label,
    medicines: [(short, why)]}], 'good': [{code, label, note}], 'no_rules', 'no_record'}."""
    advice = [i for i in items if i.get('kind') == 'advice']
    restrict = {}
    for i in advice:
        for a in i['avoid']:
            r = restrict.setdefault(a['food'], {'food': a['food'], 'label': FOODS[a['food']], 'level': a['level'], 'gap_hours': 0,
                                                'medicines': []})
            if LEVEL_RANK[a['level']] < LEVEL_RANK[r['level']]:
                r['level'] = a['level']
            if a['level'] == 'space':
                r['gap_hours'] = max(r['gap_hours'], float(a.get('gap_hours') or 0))
            gap = float(a.get('gap_hours') or 0) if a['level'] == 'space' else 0
            if not any(m[0] == i['short'] for m in r['medicines']):
                r['medicines'].append((i['short'], a['why'], gap))
    good = []
    for i in advice:
        for g in i['good']:
            if any(x['code'] == g for x in good):
                continue
            clash = [restrict[f] for f in GOOD_CONFLICTS.get(g, []) if f in restrict]
            if any(c['level'] == 'avoid' for c in clash):
                continue                                              # one medicine says avoid: never listed as good
            note = ''
            if clash:
                c = sorted(clash, key=lambda c: LEVEL_RANK[c['level']])[0]
                who = ', '.join(m[0] for m in c['medicines'])
                note = {'space': 'keep it apart from %s' % who, 'steady': 'keep the amount steady, because of %s' % who,
                        'limit': 'only a little, because of %s' % who}[c['level']]
            good.append({'code': g, 'label': GOODS[g], 'note': note})
    order = sorted(restrict.values(), key=lambda r: (LEVEL_RANK[r['level']], r['food'] != 'alcohol', r['label']))
    return {'how_to_take': advice, 'restrict': order, 'good': good,
            'no_rules': [i for i in items if i.get('kind') == 'no_rules'],
            'no_record': [i for i in items if i.get('kind') == 'no_record'],
            'review': any(i.get('review') for i in advice)}


def _hours(h):
    h = float(h or 0)
    return ('%d' % h if h == int(h) else '%g' % h) + (' hour' if h == 1 else ' hours')


def guide_text(guide, unmatched=()):
    """The guide as one WhatsApp-ready message (*bold* headings) - patient-ready, in plain English."""
    lines = ['*Food guide for these medicines*']
    if guide['how_to_take']:
        lines += ['', '*How to take*']
        for n, i in enumerate(guide['how_to_take'], 1):
            lines.append('%d. %s - %s.' % (n, i['short'], i['timing_text']))
            for note in i['notes']:
                lines.append('   %s' % note.rstrip('.') + '.')
    groups = [('avoid', '*Avoid*'), ('space', '*Keep apart from the dose*'), ('steady', '*Keep steady*'), ('limit', '*Limit*')]
    for level, head in groups:
        rows = [r for r in guide['restrict'] if r['level'] == level]
        if not rows:
            continue
        lines += ['', head]
        for r in rows:
            parts = []
            for m, w, g in r['medicines']:
                lead = ''
                if level == 'space':                     # each medicine its own gap; one that only limits this food says so
                    lead = ('at least %s from ' % _hours(g)) if g else 'limit with '
                parts.append('%s%s%s' % (lead, m, ' (%s)' % w.rstrip('.') if w else ''))
            lines.append('- %s: %s%s.' % (r['label'], 'keep ' if level == 'space' else '', '; '.join(parts)))
    lines += ['', '*Good to eat*']
    if guide['good']:
        for g in guide['good']:
            lines.append('- %s%s' % (g['label'], ' - %s' % g['note'] if g['note'] else ''))
    if not guide['restrict'] or not guide['good']:
        lines.append('- Normal home food is fine with these medicines' + (' (apart from the list above)' if guide['restrict'] else ''))
    if guide['no_rules']:
        lines += ['', '*No food rules*']
        lines += ['- %s - %s.' % (i['short'], i['why']) for i in guide['no_rules']]
    left = list(guide['no_record']) + [{'short': u, 'why': 'not found in our product list'} for u in unmatched]
    if left:
        lines += ['', '*Please ask our pharmacist about*']
        lines += ['- %s - %s.' % (i['short'], i['why']) for i in left]
    lines += ['', '_%s_' % FOOTER]
    return '\n'.join(lines)

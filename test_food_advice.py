"""get_food_advice (ask_tools) and food_advice.py: the food and medicine guide for a prescription or a bill - meal timing per
medicine, foods to avoid / keep apart / keep steady / limit, good foods, combined across the medicines by plain rules.
The food knowledge is asked LIVE per request (nothing stored), except the few records saved before (food_saved.json).
Offline: a made-up product list, a stand-in for the AI model, made-up saved records and made-up sales.

    venv/Scripts/python.exe test_food_advice.py
"""
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

import ask_tools as T
import food_advice as F

sys.stdout.reconfigure(encoding='utf-8')
FAILS = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else '  <-- %s' % (detail,)))
    if not ok:
        FAILS.append(name)


COLS = ['PRODUCT', 'KIND', 'COMPOSITION', 'SALTS (normalised)', 'DOSAGE FORM', 'ROUTE', 'THERAPEUTIC CLASS']
ROWS = [
    ('GLYCOMET 500MG TAB', 'medicine', 'Metformin 500 mg', 'metformin', 'tablet', 'oral', 'Biguanide'),
    ('THYRONORM 50MCG TAB', 'medicine', 'Levothyroxine 50 mcg', 'levothyroxine', 'tablet', 'oral', 'Thyroid hormone'),
    ('CIPLOX 500MG TAB', 'medicine', 'Ciprofloxacin 500 mg', 'ciprofloxacin', 'tablet', 'oral', 'Fluoroquinolone'),
    ('ALDACTONE 25MG TAB', 'medicine', 'Spironolactone 25 mg', 'spironolactone', 'tablet', 'oral', 'Diuretic'),
    ('LASIX 40MG TAB', 'medicine', 'Furosemide 40 mg', 'furosemide', 'tablet', 'oral', 'Diuretic'),
    ('SHELCAL 500 TAB', 'supplement', 'Calcium carbonate 1250 mg; Vitamin D3 250 IU', 'calcium carbonate; cholecalciferol', 'tablet', 'oral', 'Calcium'),
    ('PAN D CAP', 'medicine', 'Pantoprazole 40 mg; Domperidone 30 mg', 'domperidone; pantoprazole', 'capsule', 'oral', 'PPI + prokinetic'),
    ('GLYCOMET GP 1 TAB', 'medicine', 'Metformin 500 mg; Glimepiride 1 mg', 'glimepiride; metformin', 'tablet', 'oral', 'Antidiabetic'),
    ('ODDCOMBO TAB', 'medicine', 'Levothyroxine; Metformin', 'levothyroxine; metformin', 'tablet', 'oral', 'Made up'),
    ('BETNOVATE CREAM', 'medicine', 'Betamethasone 0.1%', 'betamethasone', 'cream', 'topical', 'Steroid'),
    ('HIMALAYA LIV 52', 'ayurvedic_herbal', 'Herbs', 'capers', 'tablet', 'oral', 'Herbal'),
    ('NEWDRUG 10 TAB', 'medicine', 'Newdrugium 10 mg', 'newdrugium', 'tablet', 'oral', 'New'),
]
KB = {'salts': {
    'metformin': {'timing': 'with_food', 'timing_note': 'With or just after meals', 'good': ['regular_meals', 'vegetables_millets'],
                  'avoid': [{'food': 'alcohol', 'level': 'limit', 'gap_hours': 0, 'why': 'low sugar risk'},
                            {'food': 'sugar_sweets', 'level': 'limit', 'gap_hours': 0, 'why': 'for sugar control'}]},
    'glimepiride': {'timing': 'with_food', 'timing_note': '', 'good': ['regular_meals', 'glucose_handy'],
                    'avoid': [{'food': 'alcohol', 'level': 'limit', 'gap_hours': 0, 'why': 'very low sugar'}]},
    'levothyroxine': {'timing': 'empty_stomach', 'timing_note': '30-60 minutes before breakfast', 'good': [],
                      'avoid': [{'food': 'milk_dairy', 'level': 'space', 'gap_hours': 4, 'why': 'blocks absorption'},
                                {'food': 'calcium_iron_antacid', 'level': 'space', 'gap_hours': 4, 'why': 'blocks absorption'}]},
    'ciprofloxacin': {'timing': 'any_time', 'timing_note': '', 'good': ['plenty_water', 'curd_buttermilk'],
                      'avoid': [{'food': 'milk_dairy', 'level': 'space', 'gap_hours': 2, 'why': 'binds the antibiotic'}]},
    'spironolactone': {'timing': 'with_food', 'timing_note': '', 'good': [],
                       'avoid': [{'food': 'high_potassium', 'level': 'avoid', 'gap_hours': 0, 'why': 'potassium builds up'}]},
    'furosemide': {'timing': 'any_time', 'timing_note': 'In the morning', 'good': ['potassium_foods'], 'avoid': []},
    'calcium carbonate': {'timing': 'after_food', 'timing_note': '', 'good': ['calcium_foods'], 'avoid': []},
    'cholecalciferol': {'timing': 'with_food', 'timing_note': '', 'good': ['fat_with_dose'], 'avoid': []},
    'pantoprazole': {'timing': 'before_food', 'timing_note': '', 'good': [], 'avoid': [], 'review': True},
    'domperidone': {'timing': 'before_food', 'timing_note': '', 'good': [], 'avoid': []},
    'betamethasone': {'timing': 'any_time', 'timing_note': '', 'good': [], 'avoid': []},
}, 'combos': {
    'GLYCOMET GP 1 TAB': {'timing': 'with_food', 'timing_note': 'With breakfast', 'review': False},
}}
CALLS = []


def fake_model(system, user, schema):
    """The AI model's stand-in: it knows the KB above and answers about every salt / combination it is asked."""
    CALLS.append(user)
    salts = [dict(r, salt=k, review=r.get('review', False)) for k, r in KB['salts'].items() if '- %s  [from' % k in user]
    combos = [dict(r, product=k) for k, r in KB['combos'].items() if '- %s  [' % k in user]
    return {'salts': salts, 'combinations': combos}, 0.01

tmp = Path(tempfile.mkdtemp())
T.ENRICHED_XLSX = tmp / 'none.xlsx'
T.ENRICHED_CSV = tmp / 'enriched.csv'
pd.DataFrame(ROWS, columns=COLS).assign(**{'SCHEDULE H1': '', 'MANUFACTURER': 'ACME'}).to_csv(T.ENRICHED_CSV, index=False)
T._enriched_cache.clear()
T._recent_bills = lambda: {'GLYCOMET 500MG TAB': 10}
T.FOOD_CALL = fake_model
F.SAVED_PATH = tmp / 'food_saved.json'
F.SAVED_PATH.write_text(json.dumps({'salts': {}}), encoding='utf-8')
SALES = pd.DataFrame({'Inv.No': ['2627WH100', '2627WH100', '2627WH100', '2627WS200'],
                      'Product': ['GLYCOMET 500MG TAB', 'THYRONORM 50MCG TAB', 'GLYCOMET 500MG TAB', 'LASIX 40MG TAB'],
                      'Date': pd.to_datetime(['2026-11-10', '2026-11-10', '2026-11-10', '2026-11-11']),
                      'Patient': ['A', 'A', 'A', 'B'], 'Mobile': ['9', '9', '9', '8']})
T._load = lambda: (SALES, None)


def food(**kw):
    return T.run_tool('get_food_advice', kw)


# --- the plain rules --------------------------------------------------------------------------------------------------
check('timing: one salt decides', F.merge_timing(['before_food']) == 'before_food')
check('timing: "with or without food" never decides', F.merge_timing(['any_time', 'after_food']) == 'after_food')
check('timing: with + after food = with or just after food', F.merge_timing(['with_food', 'after_food']) == 'with_or_after_food')
check('timing: empty stomach + before food = empty stomach (the stricter)', F.merge_timing(['empty_stomach', 'before_food']) == 'empty_stomach')
check('timing: empty stomach + with food = a real disagreement (None)', F.merge_timing(['empty_stomach', 'with_food']) is None)
check('a short name for the lists', F.short_name('GLYCOMET 500MG TAB') == 'Glycomet 500mg' and F.short_name('THYRONORM 50 MCG (TIN)') == 'Thyronorm 50 mcg',
      F.short_name('THYRONORM 50 MCG (TIN)'))

# --- one prescription -------------------------------------------------------------------------------------------------
r = food(products=['Glycomet 500', 'Thyronorm 50', 'Ciplox 500'])
text = r['text']
check('every medicine is found', [m['product'] for m in r['medicines']] == ['GLYCOMET 500MG TAB', 'THYRONORM 50MCG TAB', 'CIPLOX 500MG TAB'], r['medicines'])
check('how to take: each medicine with its meal timing', 'Glycomet 500mg - with food' in text and 'Thyronorm 50mcg - on an empty stomach' in text, text)
check('the salt\'s own note is shown', '30-60 minutes before breakfast.' in text, text)
check('milk: kept apart, each medicine with its own gap', 'Milk, curd, buttermilk, paneer, cheese: keep at least 4 hours from Thyronorm 50mcg '
      '(blocks absorption); at least 2 hours from Ciplox 500mg (binds the antibiotic).' in text, text)
check('alcohol / sugar limited for metformin', '*Limit*' in text and 'Alcohol: Glycomet 500mg (low sugar risk)' in text, text)
check('curd is good, but kept apart from the medicines that need it', 'Curd or buttermilk (good for the stomach) - keep it apart from' in text, text)
check('good foods are listed', 'Meals at regular times' in text and 'Plenty of water' in text, text)
check('the footer: general guidance, the doctor first', "doctor's advice comes first" in text)
check('no staff note when everything is clean', r['staff_note'] == '', r['staff_note'])

# --- conflicts between medicines --------------------------------------------------------------------------------------
r = food(products=['Aldactone 25', 'Lasix 40'])
check('banana / coconut water is NOT "good" when another medicine says avoid potassium', 'Potassium-rich foods' not in r['text']
      and 'High-potassium foods' in r['text'] and '*Avoid*' in r['text'], r['text'])
r = food(products=['Lasix 40'])
check('... but is good with furosemide alone', 'Potassium-rich foods' in r['text'], r['text'])
check('nothing to avoid: "normal home food is fine"', 'Normal home food is fine' in r['text'], r['text'])

# --- combinations -----------------------------------------------------------------------------------------------------
r = food(products=['Glycomet GP 1'])
check('a combination uses its own record when it has one', 'Glycomet GP 1 - with food' in r['text'] and 'With breakfast.' in r['text'], r['text'])
r = food(products=['Shelcal 500'])
check('a combination whose salts agree: with or just after food', 'with or just after food' in r['text'], r['text'])
r = food(products=['Pan D'])
check('two "before food" salts: before food', 'Pan D - before food' in r['text'], r['text'])
check('a record marked for review is told to the staff', 'pharmacist to confirm (Pan D)' in r['staff_note'], r['staff_note'])
r = food(products=['Oddcombo'])
check('salts that disagree with no combination record: ask the pharmacist, marked for review',
      'please ask our pharmacist about meals' in r['text'] and 'confirm' in r['staff_note'], (r['text'], r['staff_note']))

# --- what has no food rules -------------------------------------------------------------------------------------------
r = food(products=['Betnovate cream', 'Himalaya Liv 52', 'Newdrug 10', 'Zzqqx 5'])
check('a cream: no food rules (used on the skin)', '*No food rules*' in r['text'] and 'Betnovate Cream - used on the skin.' in r['text'], r['text'])
check('a herbal product: ask the pharmacist', 'Himalaya Liv 52 - not a medicine (ayurvedic herbal).' in r['text'], r['text'])
check('a salt the model gives nothing for: ask the pharmacist', 'Newdrug 10 - no food advice could be worked out for newdrugium.' in r['text'], r['text'])
check('a name not in the list: ask the pharmacist, and told to the staff', 'Zzqqx 5 - not found in our product list.' in r['text']
      and 'Not found in the product list: Zzqqx 5' in r['staff_note'], r)
r = food(products=['metformin'])
check('a generic (salt) name works too', 'with food' in r['text'] and r['medicines'][0]['product'] == 'Metformin', r['medicines'])

# --- a bill number ----------------------------------------------------------------------------------------------------
r = food(invoice_no='2627wh100')
check('a bill number: its products, each once', [m['product'] for m in r['medicines']] == ['GLYCOMET 500MG TAB', 'THYRONORM 50MCG TAB'], r['medicines'])
check('the staff note says which bill', 'bill 2627WH100 (2026-11-10)' in r['staff_note'], r['staff_note'])
check('no patient name or mobile in the answer', '"A"' not in json.dumps(r) and 'Patient' not in json.dumps(r) and "'9'" not in str(r))
r = food(invoice_no='2627WH999')
check('a bill not in the register says how far the register goes', r.get('found') is False and '2026-11-11' in r['reason'], r)
check('nothing asked: an error', 'error' in food())
check('the tool is registered for the Ask page and the shop assistant', 'get_food_advice' in T.TOOL_FNS)

# --- live: one question per request, nothing kept, the records saved before used -----------------------------------------
CALLS.clear()
r = food(products=['Glycomet 500', 'Thyronorm 50'])
check('one live question for the whole prescription', len(CALLS) == 1 and '- metformin  [from GLYCOMET 500MG TAB' in CALLS[0]
      and '- levothyroxine  [from' in CALLS[0], CALLS)
check('...with only product names, compositions and classes - no bill, no patient', 'Patient' not in CALLS[0] and '2627' not in CALLS[0])
check('...and its cost is given', r['cost_usd'] == 0.01, r.get('cost_usd'))
CALLS.clear()
food(products=['Glycomet 500'])
check('asked again: asked again (nothing was kept from the last request)', len(CALLS) == 1)
check('no food list is written anywhere', sorted(p.name for p in tmp.iterdir()) == ['enriched.csv', 'food_saved.json'], list(tmp.iterdir()))
F.SAVED_PATH.write_text(json.dumps({'salts': {
    'metformin': dict(KB['salts']['metformin'], checked=True, timing_note='Saved: with meals'),
    'levothyroxine': dict(KB['salts']['levothyroxine'], checked=False)}}), encoding='utf-8')
CALLS.clear()
r = food(products=['Glycomet 500'])
check('a salt both models agreed on before is used as it is - no AI call', CALLS == [] and 'Saved: with meals.' in r['text'] and r['cost_usd'] == 0, (CALLS, r['text']))
CALLS.clear()
r = food(products=['Glycomet 500', 'Thyronorm 50'])
check('a one-model saved record goes to the live model as a draft to check', len(CALLS) == 1 and 'metformin  [from' not in CALLS[0]
      and '- levothyroxine  [from' in CALLS[0] and 'EARLIER DRAFT: {"timing": "empty_stomach"' in CALLS[0], CALLS)
check('...and the guide has both', 'Saved: with meals.' in r['text'] and 'Thyronorm 50mcg - on an empty stomach' in r['text'], r['text'])
F.SAVED_PATH.write_text(json.dumps({'salts': {k: dict(KB['salts'][k], checked=True) for k in ('metformin', 'glimepiride')}}), encoding='utf-8')
CALLS.clear()
r = food(products=['Glycomet GP 1'])
check('a combination whose saved salts agree in timing: no question at all', CALLS == [] and 'with food' in r['text'], (CALLS, r['text']))
F.SAVED_PATH.write_text(json.dumps({'salts': {}}), encoding='utf-8')
T.FOOD_CALL = lambda system, user, schema: (_ for _ in ()).throw(TimeoutError('slow'))
r = food(products=['Glycomet 500'])
check('the AI service down: says so, never a guide without the knowledge', r.get('available') is False and 'TimeoutError' in r['reason']
      and 'text' not in r, r)
T.FOOD_CALL = fake_model

print()
print('ALL PASSED' if not FAILS else '%d FAILED: %s' % (len(FAILS), '; '.join(FAILS)))
sys.exit(1 if FAILS else 0)

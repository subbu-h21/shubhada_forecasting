"""Spoken numbers -> plain digits (English, Kannada, Hindi).

Speech-to-text writes a price in many ways: "65.60", "65.6", "6560", "65 60",
"sixty five point six zero", "ಅರವತ್ತೈದು ಪಾಯಿಂಟ್ ಆರು ಸೊನ್ನೆ", "पैंसठ पॉइंट छह शून्य", digits in
Kannada or Devanagari script. numberize() turns all of those into the same tokens, so the
caller only ever deals with ASCII digits:

    numberize("eco sixty five point six zero, thirty")
        -> [('w', 'eco'), ('n', '65.60'), (',',), ('n', '30')]

Token kinds: ('n', '65.60') a number (always a string of digits with an optional point),
('w', 'word') any other word, (',',) a comma or semicolon (the pause between two items).
Two numbers that follow each other stay two numbers ("sixty five sixty" -> 65, 60), because
a number word is only joined to the one before it where the spoken grammar allows it
("sixty" + "five" yes, "sixty five" + "sixty" no).

Pure functions, no data files, no imports from the rest of the project - the sale-bill
screen can reuse this as it is.
"""
import re
import unicodedata

# --- digits in other scripts ------------------------------------------------------------
_DIGITS = {}
for _base in (0x0CE6, 0x0966, 0x0660, 0x06F0, 0x09E6, 0xFF10):   # Kannada, Devanagari, Arabic-Indic x2, Bengali, full-width
    for _i in range(10):
        _DIGITS[_base + _i] = str(_i)


def ascii_digits(text):
    return str(text).translate(_DIGITS)


def prep_text(text):
    """Lower-case, composed Unicode, ASCII digits, no zero-width joiners (speech-to-text
    and keyboards sprinkle them into Kannada words), '₹' read as 'rs'."""
    t = unicodedata.normalize('NFC', str(text or ''))
    t = t.replace('‌', '').replace('‍', '').replace('₹', ' rs ')
    return ascii_digits(t).lower()


# --- the vocabulary -----------------------------------------------------------------------
# word -> (kind, value). Kinds: zero, unit (1-9), teen (10-19), tens (20..90), full (a
# complete 1-99 word that takes no unit after it - Hindi, and Kannada compounds), hundred
# (x100), thousand (x1000), hundredfused (Kannada 200..900, or "noora" = 100: may be followed
# by a number below 100).
_VOCAB = {}
POINT_WORDS = set()


def _add(words, kind, value):
    for w in words.split():
        _VOCAB[unicodedata.normalize('NFC', w)] = (kind, value)


def _add_point(words):
    POINT_WORDS.update(unicodedata.normalize('NFC', w) for w in words.split())


# English
_add('zero', 'zero', 0)
for _v, _w in enumerate('one two three four five six seven eight nine'.split(), 1):
    _add(_w, 'unit', _v)
for _v, _w in enumerate('ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), 10):
    _add(_w, 'teen', _v)
for _v, _w in zip(range(20, 100, 10), 'twenty thirty forty fifty sixty seventy eighty ninety'.split()):
    _add(_w, 'tens', _v)
_add('fourty', 'tens', 40)
_add('hundred hundreds', 'hundred', 100)
_add('thousand', 'thousand', 1000)
_add_point('point dot decimal')

# English words written in Kannada letters (speech-to-text does this: "ಡೆಡ್ ಸ್ಟಾಕ್")
for _v, _w in enumerate('ಒನ್ ಟೂ ತ್ರೀ ಫೋರ್ ಫೈವ್ ಸಿಕ್ಸ್ ಸೆವೆನ್ ಎಯ್ಟ್ ನೈನ್'.split(), 1):
    _add(_w, 'unit', _v)
_add('ಝೀರೋ ಜೀರೋ', 'zero', 0)
_add('ಟೆನ್', 'teen', 10)
for _v, _w in zip(range(20, 100, 10), 'ಟ್ವೆಂಟಿ ಥರ್ಟಿ ಫಾರ್ಟಿ ಫಿಫ್ಟಿ ಸಿಕ್ಸ್ಟಿ ಸೆವೆಂಟಿ ಎಯ್ಟಿ ನೈನ್ಟಿ'.split()):
    _add(_w, 'tens', _v)
_add('ಹಂಡ್ರೆಡ್', 'hundred', 100)
_add('ಪಾಯಿಂಟ್ ದಶಮಾಂಶ', 'point', 0)
_add_point('ಪಾಯಿಂಟ್ ದಶಮಾಂಶ')

# Kannada: units, teens, tens; compounds are built the way the language joins them
# (tens-word minus its last vowel + the unit, with the unit's first vowel in sign form).
_KN_UNITS = {'ಒಂದು': 1, 'ಎರಡು': 2, 'ಮೂರು': 3, 'ನಾಲ್ಕು': 4, 'ಐದು': 5, 'ಆರು': 6, 'ಏಳು': 7, 'ಎಂಟು': 8, 'ಒಂಬತ್ತು': 9}
_KN_TEENS = {'ಹತ್ತು': 10, 'ಹನ್ನೊಂದು': 11, 'ಹನ್ನೆರಡು': 12, 'ಹದಿಮೂರು': 13, 'ಹದಿನಾಲ್ಕು': 14, 'ಹದಿನೈದು': 15,
             'ಹದಿನಾರು': 16, 'ಹದಿನೇಳು': 17, 'ಹದಿನೆಂಟು': 18, 'ಹತ್ತೊಂಬತ್ತು': 19}
_KN_TENS = {'ಇಪ್ಪತ್ತು': 20, 'ಮೂವತ್ತು': 30, 'ನಲವತ್ತು': 40, 'ಐವತ್ತು': 50, 'ಅರವತ್ತು': 60, 'ಎಪ್ಪತ್ತು': 70,
            'ಎಂಬತ್ತು': 80, 'ತೊಂಬತ್ತು': 90}
_KN_SIGN = {'ಅ': '', 'ಆ': 'ಾ', 'ಇ': 'ಿ', 'ಈ': 'ೀ', 'ಉ': 'ು', 'ಊ': 'ೂ', 'ಎ': 'ೆ', 'ಏ': 'ೇ', 'ಐ': 'ೈ', 'ಒ': 'ೊ', 'ಓ': 'ೋ', 'ಔ': 'ೌ'}
_KN_SIGN_BACK = {v: k for k, v in _KN_SIGN.items() if v}


def _kn_join(tens_word, unit_word):
    first = unit_word[0]
    return tens_word[:-1] + (_KN_SIGN[first] + unit_word[1:] if first in _KN_SIGN else unit_word)


_add('ಸೊನ್ನೆ ಶೂನ್ಯ', 'zero', 0)
for _w, _v in _KN_UNITS.items():
    _add(_w, 'unit', _v)
for _w, _v in _KN_TEENS.items():
    _add(_w, 'teen', _v)
for _tw, _tv in _KN_TENS.items():
    _add(_tw, 'tens', _tv)
    for _uw, _uv in _KN_UNITS.items():
        _add(_kn_join(_tw, _uw), 'full', _tv + _uv)
_add('ಹತ್ತೊಂಭತ್ತು', 'full', 19)
_add('ನೂರು', 'hundred', 100)
_add('ಸಾವಿರ ಸಾವಿರದ', 'thousand', 1000)
_KN_HUNDRED_STEMS = {'ನೂರ': 100, 'ಇನ್ನೂರ': 200, 'ಮುನ್ನೂರ': 300, 'ನಾನೂರ': 400, 'ನಾಲ್ನೂರ': 400, 'ಐನೂರ': 500,
                     'ಆರುನೂರ': 600, 'ಏಳುನೂರ': 700, 'ಎಂಟುನೂರ': 800, 'ಒಂಬೈನೂರ': 900}
for _stem, _v in _KN_HUNDRED_STEMS.items():
    _add(_stem, 'hundredfused', _v)
    if _v > 100:
        _add(_stem + 'ು', 'full3', _v)       # ಇನ್ನೂರು = 200 on its own


def _fused(word):
    """A Kannada hundred glued to what follows it ('ನೂರೈವತ್ತು' = 150), else None."""
    for stem, base in _KN_HUNDRED_STEMS.items():
        if word.startswith(stem) and len(word) > len(stem):
            rest = word[len(stem):]
            if rest[0] in _KN_SIGN_BACK:
                rest = _KN_SIGN_BACK[rest[0]] + rest[1:]
            ent = _VOCAB.get(rest)
            if ent and ent[0] in ('unit', 'teen', 'tens', 'full'):
                return ('full3', base + ent[1])
    return None


# Hindi: every number below 100 has its own word. "value  Devanagari spellings | Roman spellings"
_HINDI = """
0 शून्य सिफर जीरो | shunya sifar
1 एक | ek
2 दो | doo
3 तीन | teen tin
4 चार | char chaar
5 पांच पाँच | paanch panch paach
6 छह छः छे छ | chhe chhah chah che
7 सात | saat
8 आठ | aath aat
9 नौ | nau
10 दस | das
11 ग्यारह | gyarah gyaarah giyarah
12 बारह | barah baarah
13 तेरह | terah
14 चौदह | chaudah
15 पंद्रह पन्द्रह | pandrah
16 सोलह | solah
17 सत्रह | satrah
18 अठारह | atharah
19 उन्नीस | unnis unnees
20 बीस | bees
21 इक्कीस | ikkis ikkees
22 बाईस | baais
23 तेईस | teis
24 चौबीस | chaubis chaubees
25 पच्चीस | pachchis pacchis pachees
26 छब्बीस | chhabbis chabbis
27 सत्ताईस | sattais
28 अट्ठाईस अठाईस | atthais athais
29 उनतीस | untis
30 तीस | tees
31 इकतीस | ikattis ikatis
32 बत्तीस | battis
33 तैंतीस | taintis
34 चौंतीस | chauntis
35 पैंतीस | paintis
36 छत्तीस | chhattis chattis
37 सैंतीस | saintis
38 अड़तीस | adtis
39 उनतालीस | untalis
40 चालीस | chalis chaalis
41 इकतालीस | iktalis
42 बयालीस | bayalis
43 तैंतालीस | taintalis
44 चवालीस चौवालीस | chavalis chauvalis
45 पैंतालीस | paintalis
46 छियालीस | chhiyalis chiyalis
47 सैंतालीस | saintalis
48 अड़तालीस | adtalis
49 उनचास | unchas
50 पचास | pachas pachaas
51 इक्यावन | ikyavan
52 बावन | baavan bavan
53 तिरपन तिरेपन | tirpan tirepan
54 चौवन | chauvan chaupan
55 पचपन | pachpan
56 छप्पन | chhappan chappan
57 सत्तावन | sattavan
58 अट्ठावन | atthavan athavan
59 उनसठ | unsath
60 साठ | saath
61 इकसठ | iksath
62 बासठ | baasath basath
63 तिरसठ | tirsath
64 चौंसठ | chaunsath chausath
65 पैंसठ | painsath paintsath
66 छियासठ | chhiyasath chiyasath
67 सड़सठ | sadsath
68 अड़सठ | adsath
69 उनहत्तर | unhattar
70 सत्तर | sattar
71 इकहत्तर | ikhattar
72 बहत्तर | bahattar
73 तिहत्तर | tihattar
74 चौहत्तर | chauhattar
75 पचहत्तर | pachhattar pachattar
76 छिहत्तर | chhihattar chihattar
77 सतहत्तर | satahattar
78 अठहत्तर | athahattar
79 उन्यासी | unyasi
80 अस्सी | assi
81 इक्यासी | ikyasi
82 बयासी | bayasi
83 तिरासी | tirasi
84 चौरासी | chaurasi
85 पचासी | pachasi
86 छियासी | chhiyasi chiyasi
87 सत्तासी | sattasi
88 अट्ठासी | atthasi athasi
89 नवासी | navasi
90 नब्बे | nabbe nabbey
91 इक्यानवे | ikyanve
92 बानवे | banve baanve
93 तिरानवे | tiranve
94 चौरानवे | chauranve
95 पंचानवे | panchanve
96 छियानवे | chhiyanve chiyanve
97 सत्तानवे | sattanve
98 अट्ठानवे | atthanve athanve
99 निन्यानवे | ninyanve
"""
for _line in _HINDI.strip().splitlines():
    _val, _rest = _line.split(' ', 1)
    _val = int(_val)
    _deva, _roman = (part.strip() for part in _rest.split('|'))
    for _w in (_deva + ' ' + _roman).split():
        if _val == 0:
            _add(_w, 'zero', 0)
        elif _val <= 9:
            _add(_w, 'unit', _val)
        else:
            _add(_w, 'full', _val)
_add('सौ sau', 'hundred', 100)
_add('हज़ार हजार hazaar hazar', 'thousand', 1000)
_add_point('दशमलव पॉइंट प्वाइंट पॉईंट पोईंट')
_add('पॉइंट', 'point', 0)

_NUMBER_KINDS = {'zero', 'unit', 'teen', 'tens', 'full', 'full3', 'hundred', 'thousand', 'hundredfused'}


def _entry(word):
    ent = _VOCAB.get(word)
    if ent and ent[0] in _NUMBER_KINDS:
        return ent
    return _fused(word)


_AFTER_SMALL = ('unit', 'teen', 'tens', 'full')


def _read_number(toks, i):
    """The whole number spelled out by the number words starting at toks[i], as (value,
    next index) - or None. Words are joined only where spoken grammar joins them."""
    total = cur = 0
    last = None
    j = i
    while j < len(toks):
        word = toks[j]
        ent = _entry(word)
        if ent is None:
            if word in ('and', 'ಮತ್ತು') and last in ('hundred', 'thousand', 'hundredfused') and j + 1 < len(toks) and _entry(toks[j + 1]):
                j += 1
                continue
            break
        kind, val = ent
        if last is None:
            if kind == 'zero':
                return 0, j + 1
            if kind == 'thousand':
                total, last = 1000, 'thousand'
            else:
                cur, last = val, kind
        elif kind == 'unit' and last in ('tens', 'hundred', 'hundredfused', 'thousand'):
            cur += val
            last = 'unit'
        elif kind in ('teen', 'tens', 'full') and last in ('hundred', 'hundredfused', 'thousand'):
            cur += val
            last = kind
        elif kind == 'hundred' and last in _AFTER_SMALL and 1 <= cur < 100:
            cur *= 100
            last = 'hundred'
        elif kind == 'thousand' and last in _AFTER_SMALL + ('hundred', 'hundredfused', 'full3'):
            total += cur * 1000
            cur = 0
            last = 'thousand'
        elif kind in ('hundredfused', 'full3') and last == 'thousand':
            cur += val
            last = kind
        else:
            break
        j += 1
    if last is None:
        return None
    return total + cur, j


def _digit_word(word):
    ent = _VOCAB.get(word)
    return str(ent[1]) if ent and ent[0] in ('zero', 'unit') else None


def _fraction(toks, j):
    """The digits after 'point', as (up to two digit characters, tokens used) - or
    (None, 0). "six zero" -> "60", "sixty" -> "60", "zero five" -> "05", "6" -> "6"."""
    if j >= len(toks):
        return None, 0
    t = toks[j]
    if t[0].isdigit():
        if '.' in t:
            return None, 0
        if len(t) <= 2:
            if len(t) == 1 and j + 1 < len(toks) and toks[j + 1].isdigit() and len(toks[j + 1]) == 1:
                return t + toks[j + 1], 2
            return t, 1
        return None, 0
    d = _digit_word(t)
    if d is not None:
        if j + 1 < len(toks):
            nxt = toks[j + 1]
            d2 = nxt if (nxt.isdigit() and len(nxt) == 1) else _digit_word(nxt)
            if d2 is not None:
                return d + d2, 2
        return d, 1
    ent = _entry(t)
    if ent and ent[0] in ('teen', 'tens', 'full'):
        got = _read_number(toks, j)           # "point twenty two" = .22, "point sixty" = .60
        if got and got[0] < 100:
            return str(got[0]), got[1] - j
    return None, 0


_TOKEN = re.compile(r'\d+(?:\.\d+)?|[a-zÀ-ɏऀ-ॿಀ-೿]+|[,;]|\.')


def numberize(text):
    """Split into number / word / comma tokens, turning every spoken or foreign-script
    number into plain digits (see the module docstring)."""
    t = prep_text(text)
    t = re.sub(r'(?<![\d.])(\d{1,3}),(\d{3})(?=\.\d)', r'\1\2', t)      # 1,250.00 -> 1250.00
    toks = _TOKEN.findall(t)
    out = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok in (',', ';'):
            out.append((',',))
            i += 1
        elif tok == '.':
            i += 1
        elif tok[0].isdigit():
            i += 1
            num = tok
            if '.' not in num and i < len(toks) and toks[i] in POINT_WORDS:
                frac, used = _fraction(toks, i + 1)
                if frac is not None:
                    num = f'{num}.{frac}'
                    i += 1 + used
            out.append(('n', num))
        elif tok in POINT_WORDS and _fraction(toks, i + 1)[0] is not None:
            frac, used = _fraction(toks, i + 1)
            out.append(('n', f'0.{frac}'))
            i += 1 + used
        else:
            got = _read_number(toks, i)
            if got is None:
                out.append(('w', tok))
                i += 1
                continue
            value, i = got
            num = str(value)
            if i < len(toks) and toks[i] in POINT_WORDS:
                frac, used = _fraction(toks, i + 1)
                if frac is not None:
                    num = f'{num}.{frac}'
                    i += 1 + used
            out.append(('n', num))
    return out


def words_to_digits(text):
    """numberize() written back as one string - handy for tests and for logging."""
    return ' '.join(t[1] if t[0] != ',' else ',' for t in numberize(text)).replace(' ,', ',')


def english_words(n):
    """0..999 in English words (used by the tests to cover every number)."""
    ones = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
    tens = 'x x twenty thirty forty fifty sixty seventy eighty ninety'.split()
    if n < 20:
        return ones[n]
    if n < 100:
        return tens[n // 10] + ('' if n % 10 == 0 else ' ' + ones[n % 10])
    return ones[n // 100] + ' hundred' + ('' if n % 100 == 0 else ' ' + english_words(n % 100))

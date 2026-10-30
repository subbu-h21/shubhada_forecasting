"""Ask by WhatsApp: the owner texts the shop's WhatsApp, Ask answers in the same chat.

What it does
  A background thread keeps WhatsApp Web open in its OWN Chrome profile
  (.whatsapp_ask_profile - a second linked device, separate from the salary-slip
  and leave-app sender, so the two never fight over one profile) on ONE chat:
  the owner's. Every new TEXT message from the owner is answered exactly as if it
  had been typed on the Ask page (same tools, same limits, same MRP basket), and
  the answer goes back into the same chat. Follow-ups ("and last month?") work:
  the last few exchanges are remembered for HISTORY_KEEP_MIN minutes.

Safety
  - Only the one chat is ever read, opened by number (/send?phone=). Every other
    chat on the account - staff, customers, groups - is never opened.
  - A message counts only when its sender is that chat's contact (the sender's
    name WhatsApp prints on each bubble equals the chat title) and it carries no
    delivery ticks. The shop's own messages in the same chat (clip finder, slips)
    are never answered. Documents and videos are not answered either, because
    other tools use the same chat for those.
  - Pictures: a vision model READS the photo (product, MRP, bill lines; never a
    patient's details) and that reading, with any caption, goes to Ask as a
    normal question. Voice notes: played silently in the page (the browser runs
    with --mute-audio), converted to WAV there, and transcribed by the Ask
    page's own speech-to-text; the reply starts with what was heard.
  - The numbers live in .env (WHATSAPP_ASK_NUMBER, comma separated, the first is the
    owner) - never in the code: the repo is public. Each person has their own Chrome
    window, state, conversation and basket (see build_listeners).
  - Within about two seconds of a new message a pre-decided gentle "got it" goes out
    (ACKS below; no model call), then the answer follows. While an answer is being
    worked out the chat is still watched, so a message sent meanwhile is noticed.
  - Messages older than WHATSAPP_ASK_MAX_AGE_MIN (30) when the listener starts
    are marked seen and not answered, so a restart never replays old questions.
  - The chat title is remembered on the first run; if the chat later shows a
    different name the listener stops answering and says so on its status.

Switched on by WHATSAPP_ASK_ENABLED=1 and WHATSAPP_ASK_NUMBER in .env.

The page selectors below were read off the live WhatsApp Web (2026-10-30): each
message is [role="row"] > [data-id]; the text and "[time, date] Sender:" live on
the [data-pre-plain-text] element; delivery ticks are aria-labels ("Read",
"Delivered", "Sent", "Pending") that only outgoing messages carry.
"""
import base64
import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import salary_whatsapp as wa

ROOT = Path(__file__).resolve().parent
PROFILE_DIR = ROOT / '.whatsapp_ask_profile'                 # the first person's; the others get _2, _3 ...
STATE_DIR = ROOT / 'data' / 'processed'
STATE_PATH = STATE_DIR / 'whatsapp_ask.json'                  # the first person's (the file the first version used)

logger = logging.getLogger('reckoner_server')

POLL_S = 1.5                 # how often the chat is looked at; the "got it" message follows within about this long
HISTORY_KEEP_MIN = 30        # a follow-up older than this starts a fresh conversation
SEEN_KEEP = 400              # message ids remembered across restarts
REPLY_MARK = '*Subbii:* '    # every reply starts with this, so the owner can tell it from the other automations
                             # (plain text, not an emoji: an emoji is drawn as a picture, which hides it from the
                             # sender's "did my message appear in the chat" check)
MAX_REPLY_CHARS = 3800
RETRY_REASONS = ('message_did_not_land', 'no_send_button')   # nothing was sent in these cases, so a retry is safe

# One call per poll: the open chat's title and every message row in it.
EXTRACT_JS = r"""() => {
  const main = document.querySelector('#main');
  if (!main) return {open: false, title: '', rows: []};
  const hdr = main.querySelector('header');
  const title = (hdr ? hdr.innerText : '').split('\n')[0].trim();
  const rows = [...main.querySelectorAll('[role="row"]')].map(el => {
    const msg = el.querySelector('[data-id]');
    if (!msg) return null;
    const copy = el.querySelector('[data-pre-plain-text]');
    const labels = [...el.querySelectorAll('[aria-label]')].map(x => x.getAttribute('aria-label') || '');
    const clock = ((el.innerText || '').trim().match(/(\d{1,2}:\d{2}\s*(?:[ap]m)?)\s*$/i) || [])[1] || '';
    return {id: msg.getAttribute('data-id'),
            pre: copy ? (copy.getAttribute('data-pre-plain-text') || '') : '',
            text: copy ? (copy.innerText || '') : '',
            image: !!el.querySelector('[data-testid="image-thumb"]'),
            voice: !!el.querySelector('[aria-label="Voice message"]'),
            clock,
            labels};
  }).filter(Boolean);
  return {open: true, title, rows};
}"""


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _truthy(value):
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def normalise_number(raw):
    """Digits only, with India's 91 added to a 10-digit number (same rule as salary_whatsapp's sender)."""
    digits = re.sub(r'\D', '', str(raw or ''))
    if len(digits) == 10:
        digits = '91' + digits
    return digits if 11 <= len(digits) <= 15 else ''


def parse_numbers(raw):
    """'9000000001, 9000000002' -> ['919000000001', '919000000002'] (valid ones only, no repeats)."""
    out = []
    for part in re.split(r'[,;\s]+', str(raw or '')):
        number = normalise_number(part)
        if number and number not in out:
            out.append(number)
    return out


def settings():
    numbers = parse_numbers(os.environ.get('WHATSAPP_ASK_NUMBER'))
    return {
        'enabled': _truthy(os.environ.get('WHATSAPP_ASK_ENABLED')),
        'numbers': numbers,
        'number': numbers[0] if numbers else '',
        'max_age_min': _int(os.environ.get('WHATSAPP_ASK_MAX_AGE_MIN'), 30),
        'dry_run': _truthy(os.environ.get('WHATSAPP_ASK_DRY_RUN')),
    }


def profile_dir_for(index):
    """Each person has their own Chrome profile: WhatsApp Web allows one open page per linked device."""
    return PROFILE_DIR if index == 0 else PROFILE_DIR.with_name(PROFILE_DIR.name + '_%d' % (index + 1))


def state_path_for(number, index=0):
    """Each person has their own remembered state. The first person keeps the file the first version
    used, so a restart never re-answers recent messages."""
    if index == 0:
        return STATE_PATH
    return STATE_DIR / ('whatsapp_ask_%s.json' % hashlib.sha1(str(number).encode()).hexdigest()[:8])


def _int(value, default):
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Reading the page (pure functions - tested without a browser)
# ---------------------------------------------------------------------------
_PRE = re.compile(r'^\[\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*([ap]m)?\s*,\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*\]\s*(.*?)\s*:?\s*$',
                  re.I | re.S)
_TICK = re.compile(r'^\s*(read|delivered|sent|pending)\s*$', re.I)


def parse_pre(pre):
    """'[2:56 pm, 30/10/2026] Contact Person: ' -> (datetime or None, 'Contact Person')."""
    m = _PRE.match(pre or '')
    if not m:
        return None, ''
    hour, minute, ampm, day, month, year, sender = m.groups()
    hour, minute = int(hour), int(minute)
    if ampm:
        hour = hour % 12 + (12 if ampm.lower() == 'pm' else 0)
    try:
        when = datetime(int(year), int(month), int(day), hour, minute)
    except ValueError:
        when = None
    return when, sender.strip()


def same_name(a, b):
    norm = lambda s: ' '.join(str(s or '').split()).casefold()
    return bool(norm(a)) and norm(a) == norm(b)


_CLOCK = re.compile(r'(\d{1,2}):(\d{2})\s*([ap]m)?', re.I)


def parse_clock(clock, now):
    """'3:38 pm' -> today at that time (yesterday if that would be in the future). A picture without a caption
    has no "[time, date] Sender:" stamp, only the time printed under it."""
    m = _CLOCK.search(clock or '')
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    if ampm:
        hour = hour % 12 + (12 if ampm.lower() == 'pm' else 0)
    try:
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None
    return when - timedelta(days=1) if when > now + timedelta(minutes=10) else when


def classify(info, now=None):
    """Messages in the open chat that the chat's contact sent, oldest first:
    [{'id', 'kind': 'text' | 'image' | 'voice', 'text' (the message, or a picture's caption), 'when'}].
    Anything else (the shop's own messages, videos, files, date separators) is left out."""
    now = now or datetime.now()
    out = []
    title = info.get('title', '')
    for row in info.get('rows') or []:
        if not row.get('id'):
            continue
        labels = row.get('labels') or []
        if any(_TICK.match(label or '') for label in labels):
            continue                                    # delivery ticks exist only on outgoing messages
        when, sender = parse_pre(row.get('pre'))
        text = (row.get('text') or '').strip()
        if sender:
            mine = same_name(sender, title)             # a text bubble names its sender in "[time, date] Sender:"
        else:                                           # a picture with no caption: its screen-reader label names the sender
            mine = 'You:' not in labels and any(
                (l or '').strip().endswith(':') and same_name((l or '').strip()[:-1], title) for l in labels)
        if not mine:
            continue                                    # sent by the shop's account (or someone unexpected)
        if row.get('image'):
            out.append({'id': row['id'], 'kind': 'image', 'text': text,
                        'when': when or parse_clock(row.get('clock'), now)})
        elif row.get('voice'):
            out.append({'id': row['id'], 'kind': 'voice', 'text': '', 'when': parse_clock(row.get('clock'), now)})
        elif text and sender:
            out.append({'id': row['id'], 'kind': 'text', 'text': text, 'when': when})
    return out


def pick_to_answer(messages, seen, now, max_age_min, first_poll):
    """-> (to_answer, to_mark_seen). Unseen messages are answered when they are recent enough;
    an old one (or, on the first poll after a start, one whose time cannot be read) is only marked seen."""
    answer, skip = [], []
    for msg in messages:
        if msg['id'] in seen:
            continue
        when = msg['when']
        if when is None:
            (skip if first_poll else answer).append(msg)
        elif max_age_min and (now - when).total_seconds() > max_age_min * 60:
            skip.append(msg)
        else:
            answer.append(msg)
    return answer, skip


# ---------------------------------------------------------------------------
# Writing the reply: Ask's markdown -> WhatsApp text
# ---------------------------------------------------------------------------
_RULE = re.compile(r'^\s*([-*_]\s*){3,}$')
_TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$')


def to_whatsapp(text, limit=MAX_REPLY_CHARS):
    """Ask answers in markdown; WhatsApp has its own: *bold*, no headings or tables, and a
    line starting "1. " or "- " is turned into a list by the message box (which then no longer
    matches what was typed, and the sender refuses to send it). So: **x** -> *x*, headings ->
    bold lines, bullets -> "•", "1." -> "1)", table rows -> "a | b | c"."""
    lines = []
    for raw in str(text or '').replace('\r', '').split('\n'):
        line = raw.rstrip()
        if _RULE.match(line):
            continue
        if '|' in line and _TABLE_SEP.match(line):
            continue
        line = line.replace('`', '')
        line = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)', r'\1 (\2)', line)
        m = re.match(r'^\s*#{1,6}\s+(.*)$', line)
        if m:
            line = '*' + m.group(1).strip().strip('*_') + '*'
        stripped = line.strip()
        if stripped.startswith('|') and stripped.endswith('|') and stripped.count('|') >= 2:
            line = ' | '.join(c.strip() for c in stripped.strip('|').split('|'))
        line = re.sub(r'^(\s*)[*+\-]\s+', lambda mm: ('   ' if len(mm.group(1)) >= 2 else '') + '• ', line)
        line = re.sub(r'^(\s*)(\d{1,3})[.)]\s+', r'\1\2) ', line)
        line = re.sub(r'\*\*(.+?)\*\*', r'*\1*', line)
        line = re.sub(r'__(.+?)__', r'*\1*', line)
        lines.append(line)
    out = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
    if len(out) > limit:
        cut = out.rfind('\n', 0, limit)
        out = out[:cut if cut > limit // 2 else limit].rstrip() + '\n… (shortened)'
    return out or '(no answer)'


# ---------------------------------------------------------------------------
# Remembered state
# ---------------------------------------------------------------------------
class State:
    """Message ids already handled and the chat's title, kept across restarts."""

    def __init__(self, path=STATE_PATH):
        self.path = Path(path)
        self.seen = deque(maxlen=SEEN_KEEP)
        self.chat_title = ''
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.seen.extend(str(i) for i in data.get('seen', []))
            self.chat_title = str(data.get('chat_title') or '')
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def add(self, ids):
        for i in ids:
            if i not in self.seen:
                self.seen.append(i)
        self.save()

    def set_title(self, title):
        self.chat_title = title
        self.save()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps({'seen': list(self.seen), 'chat_title': self.chat_title}), encoding='utf-8')
            os.replace(tmp, self.path)
        except OSError:
            pass   # a state file that cannot be saved must never stop an answer


# ---------------------------------------------------------------------------
# Answering with Ask
# ---------------------------------------------------------------------------
WA_OWNER = 'whatsapp-owner'   # the MRP basket key for this chat (the Ask page keys its basket by login)


def ask_answer(question, history, owner=WA_OWNER):
    """The same path as the Ask page's /api/ask: instant rule answers first, then the daily / burst
    cap, then the model. `owner` keys the MRP basket, so each person has a basket of their own.
    Raises nothing the caller must handle apart from a plain Exception."""
    import ask as ask_mod
    import ask_limits
    greeting = ask_mod.name_call_greeting(question)
    if greeting:
        return greeting
    hit = ask_mod.basket_reply(question, owner)
    if hit:
        return hit['say']
    try:
        ask_limits.check()
    except ask_limits.LimitReached as e:
        return str(e)
    return ask_mod.ask(question, history=history, owner=owner)


# ---------------------------------------------------------------------------
# The instant "got it" message: decided here, no model, so it goes out within a second or two
# ---------------------------------------------------------------------------
# Gentle and short; two wordings of each so repeated ones do not read like a machine. No emoji: an emoji is
# drawn as a picture, which hides the line from the sender's "did my message appear in the chat" check.
# Edit the wording freely - the first line of each reply is all the owner sees while the answer is worked out.
ACKS = {
    'en': {
        'text': ['Namaste. I have received your message - please give me a moment, I am looking into it.',
                 'Thank you, got it. One moment please, I am finding the answer for you.'],
        'voice': ['Namaste. I have received your voice note - listening to it now, one moment please.',
                  'Thank you, your voice note has reached me. One moment please while I listen to it.'],
        'image': ['Namaste. I have received your photo - looking at it now, one moment please.',
                  'Thank you, your photo has reached me. One moment please while I look at it.'],
    },
    'kn': {
        'text': ['ನಮಸ್ಕಾರ. ನಿಮ್ಮ ಸಂದೇಶ ಬಂತು - ಸ್ವಲ್ಪ ಸಮಯ ಕೊಡಿ, ನೋಡುತ್ತಿದ್ದೇನೆ.',
                 'ಧನ್ಯವಾದ, ಸಂದೇಶ ಸಿಕ್ಕಿತು. ಒಂದು ನಿಮಿಷ, ಉತ್ತರ ಹುಡುಕುತ್ತಿದ್ದೇನೆ.'],
        'voice': ['ನಮಸ್ಕಾರ. ನಿಮ್ಮ ಧ್ವನಿ ಸಂದೇಶ ಬಂತು - ಈಗ ಕೇಳುತ್ತಿದ್ದೇನೆ, ಸ್ವಲ್ಪ ಸಮಯ ಕೊಡಿ.',
                  'ಧನ್ಯವಾದ, ಧ್ವನಿ ಸಂದೇಶ ಸಿಕ್ಕಿತು. ಒಂದು ನಿಮಿಷ, ಕೇಳುತ್ತಿದ್ದೇನೆ.'],
        'image': ['ನಮಸ್ಕಾರ. ನಿಮ್ಮ ಫೋಟೋ ಬಂತು - ಈಗ ನೋಡುತ್ತಿದ್ದೇನೆ, ಸ್ವಲ್ಪ ಸಮಯ ಕೊಡಿ.',
                  'ಧನ್ಯವಾದ, ಫೋಟೋ ಸಿಕ್ಕಿತು. ಒಂದು ನಿಮಿಷ, ನೋಡುತ್ತಿದ್ದೇನೆ.'],
    },
}
ALL_ACKS = [text for lang in ACKS.values() for variants in lang.values() for text in variants]


def message_lang(text):
    """'kn' when the words are written in Kannada script, else 'en'."""
    return 'kn' if re.search(r'[ಀ-೿]', str(text or '')) else 'en'


def ack_text(kind, lang, n=0):
    """The pre-decided "got it" line for a text / voice / image message, in 'en' or 'kn'."""
    variants = ACKS.get(lang, ACKS['en']).get(kind) or ACKS['en']['text']
    return variants[n % len(variants)]


def needs_ack(text, kind, owner=WA_OWNER):
    """False for a message Ask answers instantly by rules - a call by name ("subbi"), a basket item or command
    ("eco 65.60, 30", "calculate") - because an acknowledgement before an instant answer would only be noise
    (a 15-item bill would otherwise get 15 of them). Voice notes and pictures always get one."""
    if kind != 'text':
        return True
    import ask as ask_mod
    import mrp_basket
    try:
        if ask_mod.name_call_greeting(text):
            return False
        if mrp_basket.classify(owner, ask_mod.strip_name_call(text)):
            return False
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Pictures: a vision model reads the photo, Ask answers what the owner wants to know
# ---------------------------------------------------------------------------
VISION_MODEL_DEFAULT = 'google/gemini-2.5-flash'
MAX_IMAGE_BYTES = 8_000_000
IMAGE_WAIT_S = 30            # WhatsApp shows a small preview first and the full picture a moment later

VISION_PROMPT = """You are reading a photo that the owner of an Indian retail pharmacy (Shubhada Pharma, Sirsi, Karnataka) sent from their phone. Describe only what is visible, in plain text, briefly:
- A medicine strip, box or bottle: the product name exactly as printed, composition and strength, pack size, manufacturer, MRP, batch number and expiry date. Say "not visible" for anything you cannot read.
- A bill, invoice or statement: the supplier, bill number and date, the main lines (product, quantity, rate, amount; at most 15 lines) and the totals.
- A prescription (printed or handwritten): list ONLY the medicines (name, strength, dose and duration if written). Do NOT copy the patient's name, age, address or phone number, or the doctor's details.
- A shelf, rack or stock: list the products you can read.
- Anything else: one or two plain sentences.
Never guess text you cannot read. Reply with the description only."""


def _openrouter_client():
    from openai import OpenAI
    api_key = os.environ.get('OPENROUTER_API_KEY')
    if not api_key:
        raise RuntimeError('OPENROUTER_API_KEY is missing from .env')
    return OpenAI(base_url='https://openrouter.ai/api/v1', api_key=api_key,
                  default_headers={'HTTP-Referer': 'https://github.com/subbu-h21/shubhada_forecasting',
                                   'X-Title': 'Pharmacy Reckoner - Ask by WhatsApp'})


def read_photo(image_bytes, mime='image/jpeg'):
    """-> (description, cost_usd). The model only READS the picture; the answer comes from Ask's own tools."""
    import ask as ask_mod
    model = os.environ.get('WHATSAPP_ASK_VISION_MODEL', '').strip() or VISION_MODEL_DEFAULT
    url = 'data:%s;base64,%s' % (mime or 'image/jpeg', base64.b64encode(image_bytes).decode('ascii'))
    resp = _openrouter_client().chat.completions.create(
        model=model, max_tokens=900, extra_body={'usage': {'include': True}},
        messages=[{'role': 'user', 'content': [{'type': 'text', 'text': VISION_PROMPT},
                                               {'type': 'image_url', 'image_url': {'url': url}}]}])
    text = (resp.choices[0].message.content or '').strip()
    if not text:
        raise RuntimeError('the picture reader returned nothing')
    return text, ask_mod.usage_cost(resp.usage)


def photo_question(description, caption):
    """The picture becomes a normal question for Ask: what it shows, plus what the owner wrote with it."""
    caption = (caption or '').strip()
    if caption:
        wanted = ('The owner wrote with the photo: "%s"\n'
                  'Answer that, using what the photo shows and the shop\'s own data.' % caption[:1500])
    else:
        wanted = ('The owner sent the photo without any words. In one line say what it is, then give the quick facts '
                  'you can look up for what is in it (for a medicine: its MRP, how it has been selling, any scheme in '
                  'the distributors\' scheme list). If it is a bill, say what it is and its totals.')
    return ('The owner sent a photo on WhatsApp. What the photo shows (read by an image reader, which can make '
            'mistakes - say so if something looks unclear):\n%s\n\n%s' % (description.strip()[:3000], wanted))


def one_line(text, limit=160):
    line = ' '.join(str(text or '').split())
    return line if len(line) <= limit else line[:limit].rsplit(' ', 1)[0] + '…'


_FETCH_IMAGE_JS = r"""async (id) => {
  const row = [...document.querySelectorAll('#main [role="row"]')].find(r => { const m = r.querySelector('[data-id]'); return m && m.getAttribute('data-id') === id; });
  if (!row) return {gone: true};
  const imgs = [...row.querySelectorAll('img')].filter(i => i.src.startsWith('blob:') && i.naturalWidth >= 320)
                                               .sort((a, b) => b.naturalWidth - a.naturalWidth);
  if (!imgs.length) return {ready: false};
  const blob = await (await fetch(imgs[0].src)).blob();
  const buf = new Uint8Array(await blob.arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return {ready: true, type: blob.type || 'image/jpeg', size: buf.length, b64: btoa(s)};
}"""


def fetch_image(page, row_id, wait_s=IMAGE_WAIT_S):
    """The full-size picture of one message row, as (bytes, mime), or None. The page already holds it as a
    blob: address (the bubble shows it), so it is read from the page - nothing is downloaded or saved."""
    deadline = time.time() + wait_s
    while True:
        out = page.evaluate(_FETCH_IMAGE_JS, row_id)
        if out.get('ready'):
            if out.get('size', 0) > MAX_IMAGE_BYTES:
                return None
            return base64.b64decode(out['b64']), out.get('type') or 'image/jpeg'
        if out.get('gone') or time.time() >= deadline:
            return None
        page.wait_for_timeout(1000)


# ---------------------------------------------------------------------------
# Voice notes: played (silently) in the page, captured as it starts, turned into a 16 kHz WAV for speech-to-text
# ---------------------------------------------------------------------------
MAX_VOICE_SECONDS = 120      # the speech-to-text step takes at most ~4 MB of WAV (the Ask page's own limit)

# WhatsApp keeps a voice note as an encrypted file and only decrypts it, into a hidden <audio> with a blob:
# address, when Play is pressed; the address is revoked as soon as playback stops. So the hook below starts
# reading that address the instant play() is called. The browser runs with --mute-audio, so nothing is heard.
_VOICE_HOOK_JS = r"""() => {
  if (window.__waVoice) return true;
  window.__waVoice = {n: 0, media: []};
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () {
    const src = this.currentSrc || this.src || '';
    const v = window.__waVoice;
    v.n += 1;
    v.media.push({el: this, raw: src.startsWith('blob:') ? fetch(src).then(r => r.arrayBuffer()).catch(e => ({error: String(e)})) : null});
    if (v.media.length > 4) v.media.shift();
    return play.apply(this, arguments);
  };
  return true;
}"""

_VOICE_COUNT_JS = "() => window.__waVoice.n"

_VOICE_GRAB_JS = r"""async ([before, maxSeconds]) => {
  const v = window.__waVoice;
  for (let i = 0; i < 40 && v.n <= before; i++) await new Promise(r => setTimeout(r, 200));
  if (v.n <= before) return {error: 'no audio started'};
  const m = v.media[v.media.length - 1];
  if (!m.raw) return {error: 'the audio had no readable source'};
  const raw = await m.raw;
  try { m.el.pause(); } catch (e) {}
  if (raw.error) return {error: 'the audio could not be read'};
  const ctx = new (window.AudioContext || window.webkitAudioContext)({sampleRate: 16000});
  let audio;
  try { audio = await ctx.decodeAudioData(raw.slice(0)); } catch (e) { ctx.close(); return {error: 'the audio could not be decoded'}; }
  ctx.close();
  if (audio.duration > maxSeconds) return {tooLong: true, seconds: audio.duration};
  const ch = audio.getChannelData(0), n = ch.length;
  const wav = new DataView(new ArrayBuffer(44 + n * 2));
  const w = (o, s) => { for (let i = 0; i < s.length; i++) wav.setUint8(o + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); wav.setUint32(4, 36 + n * 2, true); w(8, 'WAVE'); w(12, 'fmt '); wav.setUint32(16, 16, true);
  wav.setUint16(20, 1, true); wav.setUint16(22, 1, true); wav.setUint32(24, 16000, true); wav.setUint32(28, 32000, true);
  wav.setUint16(32, 2, true); wav.setUint16(34, 16, true); w(36, 'data'); wav.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) wav.setInt16(44 + i * 2, Math.max(-1, Math.min(1, ch[i])) * 0x7fff, true);
  const bytes = new Uint8Array(wav.buffer);
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return {seconds: audio.duration, b64: btoa(s)};
}"""

_SAFE_ID = re.compile(r'^[A-Za-z0-9_.:-]+$')


def fetch_voice(page, row_id):
    """Plays one voice-note row (silently) and returns (wav_bytes, ''), or (None, why) where why is
    'too_long' or a short reason for the log."""
    if not _SAFE_ID.match(str(row_id or '')):
        return None, 'odd message id'
    page.evaluate(_VOICE_HOOK_JS)
    before = page.evaluate(_VOICE_COUNT_JS)
    row = page.locator('#main [role="row"]', has=page.locator('[data-id="%s"]' % row_id))
    pause = row.locator('[aria-label="Pause voice message"]')
    if pause.count():                                   # already playing: stop it, then start it from the top
        pause.first.click(timeout=8000)
        page.wait_for_timeout(400)
    play = row.locator('[aria-label="Play voice message"]')
    if not play.count():
        return None, 'no play button on the voice note'
    play.first.click(timeout=8000)
    out = page.evaluate(_VOICE_GRAB_JS, [before, MAX_VOICE_SECONDS])
    if out.get('tooLong'):
        return None, 'too_long'
    if out.get('error') or not out.get('b64'):
        return None, out.get('error') or 'no audio'
    return base64.b64decode(out['b64']), ''


# ---------------------------------------------------------------------------
# The listener
# ---------------------------------------------------------------------------
def stale_chrome_command(profile_dir):
    """PowerShell that stops the Chrome processes holding exactly this profile folder (the folder name must be
    followed by a quote, space or the end - so '.whatsapp_ask_profile' never matches '.whatsapp_ask_profile_2')."""
    pattern = re.escape(str(profile_dir)).replace("'", "''") + r'("|\s|$)'
    return ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            "Where-Object { $_.CommandLine -match '%s' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" % pattern)


def kill_stale_chrome(profile_dir=PROFILE_DIR):
    """A Chrome left over from an earlier run still holds the profile (Windows keeps it alive after its
    parent dies). Only processes whose command line names THIS profile folder are touched."""
    if os.name != 'nt':
        return
    try:
        subprocess.run(['powershell', '-NoProfile', '-Command', stale_chrome_command(profile_dir)],
                       timeout=40, capture_output=True)
    except Exception:
        pass


class Listener:
    """One person's chat: its own Chrome window (profile), remembered state, conversation and basket."""

    def __init__(self, number, answer_fn=None, max_age_min=30, dry_run=False, poll_s=POLL_S,
                 state=None, now=datetime.now, vision_fn=read_photo, fetch_image_fn=fetch_image,
                 transcribe_fn=None, fetch_voice_fn=fetch_voice, profile_dir=PROFILE_DIR, owner_key=WA_OWNER,
                 label='owner'):
        self.number = number
        self.profile_dir = profile_dir
        self.owner_key = owner_key
        self.label = label                          # for the log and the status only - never the person's name
        self.answer_fn = answer_fn or (lambda q, h: ask_answer(q, h, self.owner_key))
        self.vision_fn = vision_fn
        self.fetch_image_fn = fetch_image_fn
        self.transcribe_fn = transcribe_fn          # wav bytes -> text; the server hands in the Ask page's own
        self.fetch_voice_fn = fetch_voice_fn
        self.max_age_min = max_age_min
        self.dry_run = dry_run
        self.poll_s = poll_s
        self.state = state or State()
        self.now = now
        self.history = []                 # [{'question','answer','at'}]
        self.pending = deque()            # messages seen (and, when it helps, acknowledged) but not answered yet
        self.last_lang = 'en'             # the language of the last words heard, for the "got it" on a voice note / photo
        self._ack_open = False            # a "got it" has gone out and no answer has followed yet
        self._ack_n = 0
        self._first_poll = True
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._status = {'state': 'stopped', 'detail': '', 'answered': 0, 'last_poll': None, 'last_error': ''}

    # -- status -------------------------------------------------------------
    def status(self):
        with self._lock:
            return dict(self._status)

    def _set(self, state=None, detail=None, **extra):
        with self._lock:
            if state is not None:
                self._status['state'] = state
            if detail is not None:
                self._status['detail'] = detail
            self._status.update(extra)

    # -- thread -------------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='whatsapp-ask-%s' % self.label, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        wait = 10
        while not self._stop.is_set():
            try:
                self._session()
                wait = 10
            except Exception as exc:
                logger.error('[whatsapp-ask] session failed:\n%s', traceback.format_exc())
                self._set('error', 'WhatsApp listener stopped: %s' % str(exc)[:160], last_error=str(exc)[:200])
            if self._stop.wait(wait):
                break
            wait = min(wait * 2, 300)
        self._set('stopped', 'stopped')

    # -- one browser session ------------------------------------------------
    def _session(self):
        from playwright.sync_api import sync_playwright
        self._set('starting', 'opening WhatsApp Web')
        kill_stale_chrome(self.profile_dir)
        args = ['--start-maximized', '--mute-audio', '--disable-background-timer-throttling',
                '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows']
        port = os.environ.get('WHATSAPP_ASK_DEBUG_PORT', '').strip()
        if port.isdigit() and self.label == 'owner':   # development only: lets a developer look at the page;
            args += ['--remote-debugging-port=' + port, '--remote-debugging-address=127.0.0.1']   # anyone on this PC could use it
            logger.warning('[whatsapp-ask] debugging port %s is OPEN (WHATSAPP_ASK_DEBUG_PORT) - remove it when done', port)
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir), channel='chrome', headless=False, no_viewport=True, args=args)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(wa.WEB_URL, wait_until='domcontentloaded', timeout=90000)
                self._wait_linked(page)
                self._open_chat(page)
                self._first_poll = True
                self._ack_open = False
                self.pending.clear()
                self._set('running', 'listening')
                logger.info('[whatsapp-ask] listening on the %s chat', self.label)
                while not self._stop.is_set():
                    self.step(page)
                    self._stop.wait(self.poll_s)
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass

    def _wait_linked(self, page):
        """WhatsApp Web draws its QR only after a few seconds, and until then reads like a linked page."""
        page.wait_for_timeout(8000)
        announced = False
        while not self._stop.is_set():
            if wa.is_linked(page):
                if announced:
                    logger.info('[whatsapp-ask] linked')
                    page.wait_for_timeout(15000)
                return
            if not announced:
                announced = True
                self._set('needs_scan', 'WhatsApp is not linked: scan the QR code in the Chrome window '
                                        '(phone: WhatsApp > Linked devices > Link a device)')
                logger.warning('[whatsapp-ask] not linked - the QR code needs scanning')
            page.wait_for_timeout(3000)

    def _open_chat(self, page):
        if not wa.open_chat(page, self.number, '', on_progress=lambda m: logger.info('[whatsapp-ask] %s', m)):
            raise RuntimeError('the %s chat did not open (WhatsApp unlinked, or the number is not on WhatsApp)' % self.label)
        page.wait_for_timeout(2500)

    # -- one poll -----------------------------------------------------------
    def _scan(self, page):
        """Look at the chat once: note every new message and, at once, tell the sender it has arrived.
        Nothing is answered here, so it is safe to call while an answer is being worked out."""
        info = page.evaluate(EXTRACT_JS)
        self._set(last_poll=self.now().strftime('%H:%M:%S'))
        title = info.get('title', '')
        if not info.get('open') or not title:
            self._open_chat(page)           # the chat was closed or navigated away from
            return
        if not self.state.chat_title:
            self.state.set_title(title)
            logger.info('[whatsapp-ask] %s chat recognised as "%s"', self.label, title)
        elif not same_name(title, self.state.chat_title):
            self._open_chat(page)           # someone clicked another chat in this window: go back by number
            info = page.evaluate(EXTRACT_JS)
            if not same_name(info.get('title', ''), self.state.chat_title):
                self._set('error', 'The chat opened for the %s\'s number is now named "%s", not "%s" - '
                                   'not answering until that is checked.' % (self.label, info.get('title', ''), self.state.chat_title))
                return
        messages = classify(info, self.now())
        todo, skip = pick_to_answer(messages, set(self.state.seen), self.now(), self.max_age_min, self._first_poll)
        self._first_poll = False
        if skip:
            self.state.add(m['id'] for m in skip)
        for msg in todo:
            self.state.add([msg['id']])     # marked BEFORE answering: a crash must never mean answering twice
            msg['acked'] = False
            self._acknowledge(page, msg)
            self.pending.append(msg)
        if self.status()['state'] == 'error':
            self._set('running', 'listening')

    def step(self, page):
        """Read the chat once and answer whatever is new. Separate from the loop so tests can drive it."""
        self._scan(page)
        while self.pending and not self._stop.is_set():
            self._answer(page, self.pending.popleft())

    def _acknowledge(self, page, msg):
        """The instant "got it": one pre-decided gentle line, no model call. At most one is outstanding at a time
        (a second message sent while the first is still being worked on needs no second one), and none goes out
        for a message Ask answers instantly by rules."""
        if msg.get('acked') or self._ack_open:
            return
        kind = msg.get('kind', 'text')
        if not needs_ack(msg.get('text', ''), kind, self.owner_key):
            return
        lang = message_lang(msg['text']) if kind == 'text' else self.last_lang
        self._ack_n += 1
        if self._send(page, REPLY_MARK + ack_text(kind, lang, self._ack_n), count=False):
            msg['acked'] = True
            self._ack_open = True

    def _slow(self, page, fn, *args):
        """Runs a slow call (the picture reader, speech-to-text, Ask itself) in a helper thread while this
        thread keeps looking at the chat, so a message sent meanwhile is still acknowledged straight away.
        Only this thread ever touches the page."""
        box = {}

        def work():
            try:
                box['value'] = fn(*args)
            except BaseException as exc:            # handed back to the caller below
                box['error'] = exc

        worker = threading.Thread(target=work, name='whatsapp-ask-work', daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(self.poll_s)
            if worker.is_alive() and not self._stop.is_set():
                try:
                    self._scan(page)
                except Exception as exc:            # a failed look must never lose the answer being worked out
                    logger.warning('[whatsapp-ask] %s: could not look at the chat while working: %s', self.label, exc)
        if 'error' in box:
            raise box['error']
        return box.get('value')

    def _question_for(self, page, msg):
        """-> (question for Ask, a first line telling the owner what was understood, text kept for follow-ups).
        The question is None when the message could not be understood; the first line then says why."""
        if msg.get('kind') == 'image':
            got = self.fetch_image_fn(page, msg['id'])
            if not got:
                return None, 'I could not open that picture (it may still have been loading). Please send it again.', ''
            data, mime = got
            try:
                description, _cost = self._slow(page, self.vision_fn, data, mime)
            except Exception as exc:
                logger.warning('[whatsapp-ask] picture reading failed: %s: %s', type(exc).__name__, exc)
                return None, 'Sorry, I could not read that picture (%s). Please try again in a minute.' % type(exc).__name__, ''
            lead = 'Photo read as: ' + one_line(description)
            caption = msg.get('text', '')
            return photo_question(description, caption), lead, lead + (' | ' + caption if caption else '')
        if msg.get('kind') == 'voice':
            if self.transcribe_fn is None:
                return None, 'Voice notes are not switched on yet - please type the question.', ''
            wav, why = self.fetch_voice_fn(page, msg['id'])
            if not wav:
                logger.warning('[whatsapp-ask] voice note not captured: %s', why)
                if why == 'too_long':
                    return None, 'That voice note is too long - please keep it under %d minutes.' % (MAX_VOICE_SECONDS // 60), ''
                return None, 'I could not play that voice note. Please send it again, or type the question.', ''
            try:
                heard = (self._slow(page, self.transcribe_fn, wav) or '').strip()
            except Exception as exc:
                logger.warning('[whatsapp-ask] speech-to-text failed: %s: %s', type(exc).__name__, exc)
                return None, 'Sorry, I could not make out that voice note (%s). Please try again.' % type(exc).__name__, ''
            if not heard:
                return None, 'I could not make out any words in that voice note. Please try again a little closer to the phone.', ''
            heard = heard[:4000]
            self.last_lang = message_lang(heard)
            return heard, 'I heard: “%s”' % one_line(heard, 300), heard
        text = msg['text'][:4000]
        self.last_lang = message_lang(text)
        return text, '', text

    def _answer(self, page, msg):
        logger.info('[whatsapp-ask] %s %s received (%d characters)', self.label, msg.get('kind', 'text'), len(msg.get('text', '')))
        try:
            self._acknowledge(page, msg)            # normally done already by the scan; covers a message that waited its turn
            question, lead, for_history = self._question_for(page, msg)
            if question is None:
                self._send(page, REPLY_MARK + to_whatsapp(lead))
                return
            cutoff = time.time() - HISTORY_KEEP_MIN * 60
            self.history = [h for h in self.history if h['at'] >= cutoff]
            history = [{'question': h['question'][:3000], 'answer': h['answer'][:3000]} for h in self.history[-4:]]
            try:
                answer = self._slow(page, self.answer_fn, question, history)
            except Exception as exc:
                logger.warning('[whatsapp-ask] Ask failed: %s: %s', type(exc).__name__, exc)
                answer = 'Sorry, I could not get an answer just now (%s). Please try again in a minute.' % type(exc).__name__
            else:
                self.history.append({'question': for_history, 'answer': str(answer), 'at': time.time()})
            self._send(page, REPLY_MARK + to_whatsapp((lead + '\n\n' if lead else '') + str(answer)))
        finally:
            self._ack_open = False                  # the answer (or the apology) is the end of that "got it"

    def _send(self, page, text, count=True):
        for attempt in (1, 2):
            out = wa.send_message(page, text, dry_run=self.dry_run,
                                  on_progress=lambda m: logger.info('[whatsapp-ask] %s', m))
            if out.get('sent') or out.get('dry_run'):
                if out.get('dry_run'):
                    wa.clear_composer(page)             # a dry run leaves nothing behind in the chat's box
                elif count:
                    with self._lock:
                        self._status['answered'] += 1
                return True
            if out.get('reason') not in RETRY_REASONS:
                break
        logger.error('[whatsapp-ask] the reply was not sent (%s)', out.get('reason'))
        self._set(last_error='reply not sent: %s' % out.get('reason'))
        return False


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
_listeners = []
START_STAGGER_S = 20         # the second window opens a little after the first, so the PC is not asked for both at once


def _label(index):
    return 'owner' if index == 0 else 'person %d' % (index + 1)


def build_listeners(cfg, transcribe_fn=None):
    """One Listener per number in WHATSAPP_ASK_NUMBER (comma separated; the first is the owner). Each has its own
    Chrome profile, remembered state, conversation and MRP basket, so two people never see each other's chat."""
    out = []
    for i, number in enumerate(cfg['numbers']):
        out.append(Listener(number, max_age_min=cfg['max_age_min'], dry_run=cfg['dry_run'], transcribe_fn=transcribe_fn,
                            state=State(state_path_for(number, i)), profile_dir=profile_dir_for(i),
                            owner_key='%s-%d' % (WA_OWNER, i + 1) if i else WA_OWNER, label=_label(i)))
    return out


def start_from_env(transcribe_fn=None):
    """Starts a listener per number when WHATSAPP_ASK_ENABLED=1 and WHATSAPP_ASK_NUMBER holds at least one valid
    number. Returns the list of listeners, or None. transcribe_fn (wav bytes -> text) switches voice notes on;
    the server passes the Ask page's own speech-to-text."""
    global _listeners
    cfg = settings()
    if not cfg['enabled']:
        return None
    if not cfg['numbers']:
        logger.warning('[whatsapp-ask] switched on, but WHATSAPP_ASK_NUMBER in .env is missing or not a valid number')
        return None
    _listeners = build_listeners(cfg, transcribe_fn)
    for i, listener in enumerate(_listeners):
        if i:
            threading.Timer(START_STAGGER_S * i, listener.start).start()
        else:
            listener.start()
    return _listeners


def status():
    """Overall state plus one entry per person (labels only - never names or numbers)."""
    if not _listeners:
        return {'state': 'off', 'detail': 'Ask by WhatsApp is switched off (WHATSAPP_ASK_ENABLED / WHATSAPP_ASK_NUMBER in .env)',
                'listeners': []}
    each = [dict(l.status(), label=l.label) for l in _listeners]
    order = ['error', 'needs_scan', 'starting', 'stopped', 'running']
    worst = min((e['state'] for e in each), key=lambda s: order.index(s) if s in order else 0)
    return {'state': worst, 'detail': '; '.join('%s: %s' % (e['label'], e['detail'] or e['state']) for e in each),
            'listeners': each}

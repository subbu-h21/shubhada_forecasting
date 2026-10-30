"""Offline tests for whatsapp_ask.py (Ask by WhatsApp).

No browser, no WhatsApp, no model call, no real data: the page is a scripted fake whose rows are
shaped like the live WhatsApp Web DOM (read off the real page 2026-10-30), the sender is a recorder,
and Ask is a stub.

Run with the reckoner's venv:
    venv/Scripts/python.exe test_whatsapp_ask.py
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('OPENROUTER_API_KEY', 'sk-test-not-real')
sys.stdout.reconfigure(encoding='utf-8')

import whatsapp_ask as W

fails = []


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  ' + str(extra)))
    if not cond:
        fails.append(name)


# --- settings ---------------------------------------------------------------
check('a 10-digit number gets India\'s 91', W.normalise_number('9000000001') == '919000000001')
check('spaces, + and dashes are dropped', W.normalise_number('+91 90000-00001') == '919000000001')
check('junk is refused', W.normalise_number('abc') == '' and W.normalise_number('12345') == '' and W.normalise_number(None) == '')
os.environ.pop('WHATSAPP_ASK_ENABLED', None)
check('off unless WHATSAPP_ASK_ENABLED is set', W.start_from_env() is None and W.status()['state'] == 'off')
os.environ['WHATSAPP_ASK_ENABLED'] = '1'
os.environ.pop('WHATSAPP_ASK_NUMBER', None)
check('switched on without a number starts nothing', W.start_from_env() is None)
os.environ.pop('WHATSAPP_ASK_ENABLED', None)

# --- reading the page ---------------------------------------------------------
w = W.parse_pre('[2:56 pm, 30/10/2026] Contact Person: ')
check('"[2:56 pm, 30/10/2026] Name: " -> time and sender', w == (datetime(2026, 10, 30, 14, 56), 'Contact Person'), w)
check('12:05 am is just after midnight', W.parse_pre('[12:05 am, 01/11/2026] A: ')[0] == datetime(2026, 11, 1, 0, 5))
check('12:30 pm stays at noon', W.parse_pre('[12:30 pm, 13/10/2026] A: ')[0] == datetime(2026, 10, 13, 12, 30))
check('a 24-hour stamp works', W.parse_pre('[14:56, 30/10/2026] A: ')[0] == datetime(2026, 10, 30, 14, 56))
check('an unreadable stamp gives no time and no sender', W.parse_pre('hello') == (None, ''))
check('a sender with a colon-free name only loses the trailing colon', W.parse_pre('[1:00 pm, 02/10/2026] Shubhada Pharma: ')[1] == 'Shubhada Pharma')
check('names compare ignoring case and spacing', W.same_name(' contact  PERSON', 'Contact Person') and not W.same_name('', ''))


def row(i, sender, text, when='[2:56 pm, 30/10/2026]', ticks=False, extra_labels=()):
    labels = [f'{sender}:'] + list(extra_labels) + ([' Read '] if ticks else []) + ['This is a disappearing message']
    return {'id': i, 'pre': f'{when} {sender}: ' if text is not None else '', 'text': text or '', 'labels': labels}


TITLE = 'Contact Person'
info = {'open': True, 'title': TITLE, 'rows': [
    row('AC01', TITLE, 'first question'),                                             # incoming text
    row('3EB1', 'Shop Account', 'a clip caption', ticks=True),                        # shop's own text (ticks)
    row('AC02', TITLE, None),                                                         # incoming picture / file (no text)
    row('3EB2', 'Shop Account', None, ticks=True),                                    # shop's own video
    row('AC03', TITLE, 'quoted reply', extra_labels=['You:']),                       # incoming reply that quotes the shop's message
    row('3EB3', 'Shop Account', 'sent but no tick yet', ticks=False),                # shop's own, sender differs from the title
    {'id': '', 'pre': '', 'text': '', 'labels': []},                                 # a separator without an id
]}
got = W.classify(info)
check('only the contact\'s own text messages are picked', [m['id'] for m in got] == ['AC01', 'AC03'], got)
check('a quote of the shop\'s message inside a reply does not make it outgoing', 'AC03' in [m['id'] for m in got])
check('a message the shop sent is never picked even before its tick shows', 'AC01' in [m['id'] for m in got] and '3EB3' not in [m['id'] for m in got])
check('a title that differs in case still matches', len(W.classify({**info, 'title': 'CONTACT person'})) == 2)
check('the wrong chat open picks nothing', W.classify({**info, 'title': 'Somebody Else'}) == [])

# --- which are new ----------------------------------------------------------------
now = datetime(2026, 10, 30, 15, 10)
ms = [{'id': 'a', 'text': 'x', 'when': now - timedelta(minutes=5)},
      {'id': 'b', 'text': 'y', 'when': now - timedelta(minutes=45)},
      {'id': 'c', 'text': 'z', 'when': None},
      {'id': 'd', 'text': 'w', 'when': now - timedelta(minutes=1)}]
ans, skip = W.pick_to_answer(ms, {'d'}, now, 30, first_poll=True)
check('recent unseen is answered, old and unreadable-time are only marked on the first poll',
      [m['id'] for m in ans] == ['a'] and [m['id'] for m in skip] == ['b', 'c'], (ans, skip))
ans, skip = W.pick_to_answer(ms, set(), now, 30, first_poll=False)
check('after the first poll an unreadable time is answered', [m['id'] for m in ans] == ['a', 'c', 'd'] and [m['id'] for m in skip] == ['b'])
ans, skip = W.pick_to_answer(ms, set(), now, 0, first_poll=True)
check('a max age of 0 switches the age rule off', [m['id'] for m in ans] == ['a', 'b', 'd'])

# --- the reply text -------------------------------------------------------------------
md = ("## Dolo 650 schemes\n\n"
      "**Best:** Supplier A 10+2\n"
      "*   **Rachana:** 8+2 (buy 8, get 2 free)\n"
      "- Suba: 4+1\n"
      "    * nested point\n"
      "1. first step\n"
      "2) second step\n"
      "| Branch | Qty |\n|---|---|\n| Sirsi | 40 |\n"
      "---\n"
      "See [the list](https://example.com/x) and `code`.\n\n\n\nlast line")
out = W.to_whatsapp(md)
lines = out.split('\n')
check('headings become bold lines', lines[0] == '*Dolo 650 schemes*', lines[0])
check('**bold** becomes *bold*', '*Best:* Supplier A 10+2' in out and '**' not in out, out)
check('no line starts the way WhatsApp\'s message box turns into a list',
      not any(l.lstrip().startswith(('- ', '* ', '+ ')) or l.lstrip()[:3].rstrip('. )').isdigit() and l.lstrip()[1:3] == '. ' for l in lines), lines)
check('bullets become a bullet character', '• *Rachana:* 8+2 (buy 8, get 2 free)' in out and '• Suba: 4+1' in out and '   • nested point' in out)
check('numbered lines use "1)"', '1) first step' in out and '2) second step' in out)
check('a table becomes lines with bars and loses its separator row', 'Branch | Qty' in out and 'Sirsi | 40' in out and '---' not in out)
check('links keep their address and code ticks go', 'the list (https://example.com/x)' in out and '`' not in out)
check('blank lines are squeezed to one', '\n\n\n' not in out)
big = W.to_whatsapp('\n'.join(f'line {i} ' + 'x' * 60 for i in range(200)))
check('a very long answer is cut at a line end and says so', len(big) <= W.MAX_REPLY_CHARS + 30 and big.endswith('(shortened)') and '\nline ' in big)
check('an empty answer still sends something', W.to_whatsapp('') == '(no answer)')

# --- remembered state --------------------------------------------------------------------
tmp = Path(tempfile.mkdtemp())
st = W.State(tmp / 's.json')
st.add(['a', 'b']); st.set_title('Contact Person')
st2 = W.State(tmp / 's.json')
check('seen ids and the chat title survive a restart', list(st2.seen) == ['a', 'b'] and st2.chat_title == 'Contact Person')
st.add(str(i) for i in range(W.SEEN_KEEP + 50))
check('the seen list stays bounded', len(W.State(tmp / 's.json').seen) == W.SEEN_KEEP)
(tmp / 'bad.json').write_text('{not json', encoding='utf-8')
check('a corrupt state file starts empty instead of crashing', list(W.State(tmp / 'bad.json').seen) == [])


# --- the listener, driven with a fake page --------------------------------------------------
class FakePage:
    def __init__(self, infos):
        self.infos = list(infos)
        self.last = self.infos[-1]
        self.reopened = 0

    def evaluate(self, js):
        return self.infos.pop(0) if self.infos else self.last

    def wait_for_timeout(self, ms):
        pass


sent = []          # real replies
acks = []          # the instant "got it" lines
events = []        # both, in the order they went out: 'ack' / the reply's first words
reopened = []
cleared = []
send_ok = [True]   # a test can switch sending off


def _fake_send(page, text, dry_run=False, on_progress=None):
    is_ack = any(a in text for a in W.ALL_ACKS)
    (acks if is_ack else sent).append((text, dry_run))
    events.append('ack' if is_ack else text[10:30])
    if not send_ok[0]:
        return {'sent': False, 'reason': 'no_send_button'}
    return {'dry_run': True} if dry_run else {'sent': True, 'reason': 'seen'}


W.wa.send_message = _fake_send
W.wa.open_chat = lambda page, number, text='', timeout_ms=0, on_progress=None: reopened.append(number) or True
W.wa.clear_composer = lambda page: cleared.append(1)

asked = []


def fake_answer(q, history):
    asked.append((q, list(history)))
    return f'**answer to** {q}'


NOW = datetime(2026, 10, 30, 15, 10)


def listener(infos, **kw):
    st = W.State(Path(tempfile.mkdtemp()) / 's.json')
    ln = W.Listener('919000000001', answer_fn=kw.pop('answer_fn', fake_answer), state=st, now=lambda: NOW, **kw)
    ln.page = FakePage(infos)
    return ln


def msgs(*pairs):
    return {'open': True, 'title': TITLE, 'rows': [row(i, TITLE, t, when='[3:05 pm, 30/10/2026]') for i, t in pairs]}


ln = listener([msgs(('AC01', 'old one'), ('AC02', 'new one')), msgs(('AC01', 'old one'), ('AC02', 'new one'))])
ln.state.add(['AC01'])
ln.step(ln.page)
check('a new message gets one reply, in WhatsApp style, marked as Subbii',
      len(sent) == 1 and sent[0][0] == '*Subbii:* *answer to* new one', sent)
ln.step(ln.page)
check('the same message is not answered again on the next poll', len(sent) == 1 and len(asked) == 1)
check('the chat title is remembered on the first look', ln.state.chat_title == TITLE)
check('the count of answers goes up', ln.status()['answered'] == 1)

sent.clear(); asked.clear()
ln = listener([msgs(('AC01', 'what are sales'), ('AC02', 'and last month?'))])
ln.step(ln.page)
check('two new messages are answered in order', [a[0] for a in asked] == ['what are sales', 'and last month?'])
check('the second one is given the first as history', asked[1][1] and asked[1][1][0]['question'] == 'what are sales', asked)

sent.clear(); asked.clear()
ln = listener([msgs(('AC01', 'hello'))])
ln.history = [{'question': 'ancient', 'answer': 'old', 'at': time.time() - 3600}]
ln.step(ln.page)
check('a conversation older than the keep time starts fresh', asked[0][1] == [], asked)

sent.clear(); asked.clear()
old = {'open': True, 'title': TITLE, 'rows': [row('AC09', TITLE, 'from this morning', when='[9:00 am, 30/10/2026]')]}
ln = listener([old])
ln.step(ln.page)
check('a message older than the age limit is skipped, not answered', sent == [] and 'AC09' in ln.state.seen)

sent.clear(); asked.clear()
mine = {'open': True, 'title': TITLE, 'rows': [row('3EB9', 'Shop Account', 'a slip text', when='[3:05 pm, 30/10/2026]', ticks=True)]}
ln = listener([mine])
ln.step(ln.page)
check('the shop\'s own message in the chat is never answered', sent == [] and asked == [])


def boom(q, history):
    raise RuntimeError('model down')


sent.clear()
ln = listener([msgs(('AC01', 'anything'))], answer_fn=boom)
ln.step(ln.page)
check('when Ask fails the owner is told so instead of silence', len(sent) == 1 and 'could not get an answer' in sent[0][0] and 'RuntimeError' in sent[0][0], sent)
check('a failed answer is not kept as history', ln.history == [])

sent.clear()
ln = listener([msgs(('AC01', 'dry'))], dry_run=True)
ln.step(ln.page)
check('a dry run types the reply but sends nothing and clears the box', sent and sent[0][1] is True and cleared and ln.status()['answered'] == 0)

reopened.clear(); sent.clear()
elsewhere = {'open': True, 'title': 'Another Person', 'rows': []}
ln = listener([elsewhere, msgs(('AC01', 'hi'))])
ln.state.set_title(TITLE)
ln.step(ln.page)
check('if another chat is open the listener goes back to the owner\'s by number', reopened == ['919000000001'] and len(sent) == 1, (reopened, sent))

reopened.clear(); sent.clear()
ln = listener([elsewhere, elsewhere])
ln.state.set_title(TITLE)
ln.step(ln.page)
check('a chat that stays differently named is not answered, and the status says why',
      sent == [] and ln.status()['state'] == 'error' and 'Another Person' in ln.status()['detail'], ln.status())

reopened.clear()
ln = listener([{'open': False, 'title': '', 'rows': []}])
ln.step(ln.page)
check('no chat open -> it opens one', reopened == ['919000000001'])

# --- pictures ---------------------------------------------------------------------------------------
NOW_P = datetime(2026, 10, 30, 15, 40)
check('"3:38 pm" is today at 15:38', W.parse_clock('3:38 pm', NOW_P) == datetime(2026, 10, 30, 15, 38))
check('"12:05 am" is just after midnight', W.parse_clock('12:05 am', NOW_P) == datetime(2026, 10, 30, 0, 5))
check('a time later than now is yesterday\'s', W.parse_clock('11:50 pm', NOW_P) == datetime(2026, 10, 29, 23, 50))
check('no time at all gives none', W.parse_clock('', NOW_P) is None and W.parse_clock('hello', NOW_P) is None)


def img_row(i, who, caption=None, clock='3:38 pm', ticks=False, image=True):
    labels = [f'{who}:', 'Open picture', 'Forward media', 'This is a disappearing message'] + ([' Read '] if ticks else [])
    pre = f'[3:38 pm, 30/10/2026] {who}: ' if caption else ''
    return {'id': i, 'pre': pre, 'text': caption or '', 'image': image, 'clock': clock, 'labels': labels}


pinfo = {'open': True, 'title': TITLE, 'rows': [
    img_row('AC11', TITLE),                              # the owner's picture, no caption
    img_row('AC12', TITLE, caption='stock?'),            # the owner's picture with a caption
    img_row('3EB5', 'You', ticks=True),                  # the shop's own picture
    img_row('AC13', 'Somebody Else'),                    # not the chat's contact
    {**img_row('AC14', TITLE), 'image': False},          # a video / file row: no picture marker
]}
got = W.classify(pinfo, NOW_P)
check('the owner\'s pictures are picked, with or without a caption', [(m['id'], m['kind']) for m in got] == [('AC11', 'image'), ('AC12', 'image')], got)
check('a captionless picture takes its time from the clock under it', got[0]['when'] == datetime(2026, 10, 30, 15, 38) and got[0]['text'] == '')
check('a captioned picture keeps the caption and the stamped time', got[1]['text'] == 'stock?' and got[1]['when'] == datetime(2026, 10, 30, 15, 38))
check('the shop\'s own pictures and other senders\' are never picked', 'AC13' not in [m['id'] for m in got] and '3EB5' not in [m['id'] for m in got])
check('a text and a picture in one chat both come through, in order',
      [m['kind'] for m in W.classify({'open': True, 'title': TITLE, 'rows': [row('AC01', TITLE, 'hi'), img_row('AC11', TITLE)]}, NOW_P)] == ['text', 'image'])

q = W.photo_question('Amoxyclav 625 - Abbott - 5x3x10 tablets', '')
check('with no caption Ask is told to say what it is and give quick facts', 'Amoxyclav 625' in q and 'without any words' in q and 'MRP' in q, q)
q = W.photo_question('Amoxyclav 625', 'do we have stock?')
check('with a caption Ask is told to answer the caption', 'do we have stock?' in q and 'without any words' not in q)
check('a long description is cut to one short line', len(W.one_line('word ' * 100)) <= 162 and W.one_line('a  b\nc') == 'a b c')

JPEG = b'\xff\xd8\xff\xe0fake-jpeg-bytes'
vision_calls = []


def fake_vision(data, mime):
    vision_calls.append((data, mime))
    return 'Amoxyclav 625 (Abbott), amoxicillin + clavulanate, 5x3x10 tablets', 0.0


def img_listener(infos, **kw):
    st = W.State(Path(tempfile.mkdtemp()) / 's.json')
    ln = W.Listener('919000000001', answer_fn=kw.pop('answer_fn', fake_answer), state=st, now=lambda: NOW_P,
                    vision_fn=kw.pop('vision_fn', fake_vision), fetch_image_fn=kw.pop('fetch_image_fn', lambda page, i: (JPEG, 'image/jpeg')), **kw)
    ln.page = FakePage(infos)
    return ln


sent.clear(); asked.clear(); vision_calls.clear()
ln = img_listener([{'open': True, 'title': TITLE, 'rows': [img_row('AC11', TITLE)]}])
ln.step(ln.page)
check('a picture is read, handed to Ask as a question, and the answer goes back',
      len(vision_calls) == 1 and vision_calls[0] == (JPEG, 'image/jpeg') and len(asked) == 1 and 'Amoxyclav 625' in asked[0][0], (vision_calls, asked))
check('the reply says first what the photo was read as, then answers',
      len(sent) == 1 and sent[0][0].startswith('*Subbii:* Photo read as: Amoxyclav 625') and 'answer to' in sent[0][0], sent)
check('a follow-up after a picture is given the picture\'s reading as history', ln.history and 'Photo read as: Amoxyclav' in ln.history[0]['question'])

sent.clear(); asked.clear(); vision_calls.clear()
ln = img_listener([{'open': True, 'title': TITLE, 'rows': [img_row('AC12', TITLE, caption='is it in stock?')]}])
ln.step(ln.page)
check('the caption travels with the picture', len(asked) == 1 and 'is it in stock?' in asked[0][0] and 'Amoxyclav 625' in asked[0][0], asked)

sent.clear(); asked.clear()
ln = img_listener([{'open': True, 'title': TITLE, 'rows': [img_row('AC11', TITLE)]}], fetch_image_fn=lambda page, i: None)
ln.step(ln.page)
check('a picture that cannot be opened gets a plain apology and no question is asked',
      asked == [] and len(sent) == 1 and 'could not open that picture' in sent[0][0], sent)


def bad_vision(data, mime):
    raise RuntimeError('vision down')


sent.clear(); asked.clear()
ln = img_listener([{'open': True, 'title': TITLE, 'rows': [img_row('AC11', TITLE)]}], vision_fn=bad_vision)
ln.step(ln.page)
check('a picture the reader fails on gets an apology naming the kind of failure, and no question is asked',
      asked == [] and len(sent) == 1 and 'could not read that picture' in sent[0][0] and 'RuntimeError' in sent[0][0], sent)

sent.clear(); asked.clear(); vision_calls.clear()
ln = img_listener([{'open': True, 'title': TITLE, 'rows': [img_row('AC11', TITLE, clock='9:00 am')]}])
ln.step(ln.page)
check('a picture older than the age limit is skipped without reading it (no cost)', sent == [] and vision_calls == [] and 'AC11' in ln.state.seen)


class FakeImagePage:
    def __init__(self, outs):
        self.outs = list(outs)
        self.waits = 0

    def evaluate(self, js, arg=None):
        return self.outs.pop(0) if len(self.outs) > 1 else self.outs[0]

    def wait_for_timeout(self, ms):
        self.waits += 1


import base64
b64 = base64.b64encode(JPEG).decode()
pg = FakeImagePage([{'ready': False}, {'ready': False}, {'ready': True, 'type': 'image/jpeg', 'size': len(JPEG), 'b64': b64}])
check('the picture is fetched once the full-size version has loaded', W.fetch_image(pg, 'x', wait_s=30) == (JPEG, 'image/jpeg') and pg.waits == 2)
check('a picture that never loads gives up', W.fetch_image(FakeImagePage([{'ready': False}]), 'x', wait_s=0) is None)
check('a picture that is gone from the page gives up at once', W.fetch_image(FakeImagePage([{'gone': True}]), 'x', wait_s=30) is None)
check('a huge picture is refused', W.fetch_image(FakeImagePage([{'ready': True, 'type': 'image/jpeg', 'size': W.MAX_IMAGE_BYTES + 1, 'b64': b64}]), 'x') is None)


class FakeClient:
    def __init__(self):
        self.kwargs = None
        outer = self

        class _Completions:
            def create(self, **kw):
                outer.kwargs = kw
                msg = types.SimpleNamespace(content=' Amoxyclav 625 ')
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage={'cost': 0.0004})

        self.chat = types.SimpleNamespace(completions=_Completions())


import types
fc = FakeClient()
orig_client = W._openrouter_client
W._openrouter_client = lambda: fc
os.environ.pop('WHATSAPP_ASK_VISION_MODEL', None)
text, cost = W.read_photo(JPEG, 'image/jpeg')
content = fc.kwargs['messages'][0]['content']
check('the picture is sent to the model as an image with the reading instructions',
      fc.kwargs['model'] == W.VISION_MODEL_DEFAULT and content[0]['text'] == W.VISION_PROMPT
      and content[1]['image_url']['url'] == 'data:image/jpeg;base64,' + b64, fc.kwargs)
check('the reading and its cost come back', text == 'Amoxyclav 625' and abs(cost - 0.0004) < 1e-9)
check('the reading instructions keep patient details out', 'Do NOT copy the patient' in W.VISION_PROMPT)
os.environ['WHATSAPP_ASK_VISION_MODEL'] = 'some/other-model'
W.read_photo(JPEG)
check('the vision model can be changed in .env', fc.kwargs['model'] == 'some/other-model')
os.environ.pop('WHATSAPP_ASK_VISION_MODEL', None)
W._openrouter_client = orig_client

# --- voice notes ------------------------------------------------------------------------------------
def voice_row(i, who, clock='3:38 pm', ticks=False):
    labels = [f'{who}:', 'Play voice message', 'Voice message', 'Voice note progress slider',
              'Change playback speed, currently 1×', 'This is a disappearing message'] + ([' Read '] if ticks else [])
    return {'id': i, 'pre': '', 'text': '', 'image': False, 'voice': True, 'clock': clock, 'labels': labels}


vinfo = {'open': True, 'title': TITLE, 'rows': [voice_row('AC21', TITLE), voice_row('3EB6', 'You', ticks=True), voice_row('AC22', 'Somebody Else')]}
got = W.classify(vinfo, NOW_P)
check('the owner\'s voice note is picked, with its time from the clock; the shop\'s and others\' are not',
      [(m['id'], m['kind']) for m in got] == [('AC21', 'voice')] and got[0]['when'] == datetime(2026, 10, 30, 15, 38), got)

WAV = b'RIFF\x00\x00\x00\x00WAVEfake'
heard_calls = []


def voice_listener(infos, **kw):
    st = W.State(Path(tempfile.mkdtemp()) / 's.json')
    ln = W.Listener('919000000001', answer_fn=kw.pop('answer_fn', fake_answer), state=st, now=lambda: NOW_P,
                    transcribe_fn=kw.pop('transcribe_fn', lambda wav: heard_calls.append(wav) or ' ಇವತ್ತು ಎಷ್ಟು ಮಾರಾಟ? '),
                    fetch_voice_fn=kw.pop('fetch_voice_fn', lambda page, i: (WAV, '')), **kw)
    ln.page = FakePage(infos)
    return ln


sent.clear(); asked.clear()
ln = voice_listener([{'open': True, 'title': TITLE, 'rows': [voice_row('AC21', TITLE)]}])
ln.step(ln.page)
check('a voice note is transcribed and the words go to Ask as the question',
      heard_calls and heard_calls[-1] == WAV and [a[0] for a in asked] == ['ಇವತ್ತು ಎಷ್ಟು ಮಾರಾಟ?'], asked)
check('the reply says first what was heard, then answers',
      len(sent) == 1 and sent[0][0].startswith('*Subbii:* I heard: “ಇವತ್ತು') and 'answer to' in sent[0][0], sent)
check('a follow-up after a voice note gets its words as history', ln.history and ln.history[0]['question'] == 'ಇವತ್ತು ಎಷ್ಟು ಮಾರಾಟ?')

sent.clear(); asked.clear()
ln = voice_listener([{'open': True, 'title': TITLE, 'rows': [voice_row('AC21', TITLE)]}], fetch_voice_fn=lambda page, i: (None, 'too_long'))
ln.step(ln.page)
check('a voice note that is too long is declined politely, nothing is asked',
      asked == [] and len(sent) == 1 and 'too long' in sent[0][0] and '2 minutes' in sent[0][0], sent)

sent.clear(); asked.clear()
ln = voice_listener([{'open': True, 'title': TITLE, 'rows': [voice_row('AC21', TITLE)]}], fetch_voice_fn=lambda page, i: (None, 'no audio started'))
ln.step(ln.page)
check('a voice note that cannot be played asks to send it again', asked == [] and len(sent) == 1 and 'could not play that voice note' in sent[0][0], sent)


def bad_stt(wav):
    raise RuntimeError('stt down')


sent.clear(); asked.clear()
ln = voice_listener([{'open': True, 'title': TITLE, 'rows': [voice_row('AC21', TITLE)]}], transcribe_fn=bad_stt)
ln.step(ln.page)
check('a failed transcription says so and asks nothing', asked == [] and 'could not make out that voice note' in sent[0][0] and 'RuntimeError' in sent[0][0], sent)

sent.clear(); asked.clear()
ln = voice_listener([{'open': True, 'title': TITLE, 'rows': [voice_row('AC21', TITLE)]}], transcribe_fn=lambda wav: '   ')
ln.step(ln.page)
check('silence or noise (no words) is not sent to Ask', asked == [] and 'could not make out any words' in sent[0][0], sent)

sent.clear(); asked.clear()
ln = voice_listener([{'open': True, 'title': TITLE, 'rows': [voice_row('AC21', TITLE)]}])
ln.transcribe_fn = None
ln.step(ln.page)
check('with no speech-to-text handed in, a voice note gets a clear "type it" reply', asked == [] and 'not switched on' in sent[0][0], sent)

sent.clear(); asked.clear()
ln = voice_listener([{'open': True, 'title': TITLE, 'rows': [voice_row('AC21', TITLE, clock='9:00 am')]}], fetch_voice_fn=lambda page, i: (_ for _ in ()).throw(AssertionError('must not play an old note')))
ln.step(ln.page)
check('a voice note older than the age limit is skipped without being played', sent == [] and 'AC21' in ln.state.seen)


class VoicePage:
    """Just enough of a Playwright page for fetch_voice: scripted evaluate() answers and locator() clicks."""

    def __init__(self, grab, playing=False):
        self.grab = grab
        self.playing = playing
        self.clicks = []
        self.js_args = []
        outer = self

        class Loc:
            def __init__(self, sel):
                self.sel = sel

            def locator(self, sel, **kw):
                return Loc(sel)

            def count(self):
                if 'Pause voice message' in self.sel:
                    return 1 if outer.playing else 0
                return 1

            @property
            def first(self):
                return self

            def click(self, timeout=0):
                outer.clicks.append(self.sel)
                if 'Pause voice message' in self.sel:
                    outer.playing = False

        self._loc = Loc

    def evaluate(self, js, arg=None):
        self.js_args.append(arg)
        if js == W._VOICE_COUNT_JS:
            return 3
        if js == W._VOICE_HOOK_JS:
            return True
        return self.grab

    def locator(self, sel, **kw):
        return self._loc(sel)

    def wait_for_timeout(self, ms):
        pass


wav_b64 = base64.b64encode(WAV).decode()
vp = VoicePage({'seconds': 8.1, 'b64': wav_b64})
check('a voice note is played once and its WAV comes back', W.fetch_voice(vp, 'AC21') == (WAV, '') and sum('Play voice message' in c for c in vp.clicks) == 1)
check('the page is told how many plays came before, and the length limit', [3, W.MAX_VOICE_SECONDS] in vp.js_args)
vp = VoicePage({'seconds': 8.1, 'b64': wav_b64}, playing=True)
W.fetch_voice(vp, 'AC21')
check('a note that is already playing is stopped first, then played', [('Pause' in c) for c in vp.clicks] == [True, False], vp.clicks)
check('a note over the length limit comes back as too long', W.fetch_voice(VoicePage({'tooLong': True, 'seconds': 200}), 'AC21') == (None, 'too_long'))
check('a page error comes back as a reason', W.fetch_voice(VoicePage({'error': 'the audio could not be decoded'}), 'AC21') == (None, 'the audio could not be decoded'))
check('an odd message id never reaches the page', W.fetch_voice(VoicePage({}), 'x"] , bad') == (None, 'odd message id'))
check('the page code reads the audio the moment play() is called and mutes nothing it needs',
      'fetch(src)' in W._VOICE_HOOK_JS and 'decodeAudioData' in W._VOICE_GRAB_JS and '16000' in W._VOICE_GRAB_JS)

# --- the instant "got it" ------------------------------------------------------------------------------
check('Kannada script gets a Kannada "got it", anything else English',
      W.message_lang('ಇವತ್ತು ಎಷ್ಟು ಮಾರಾಟ?') == 'kn' and W.message_lang('today sales?') == 'en' and W.message_lang('') == 'en')
check('every kind has two gentle wordings in both languages, and none uses an emoji',
      all(len(v) == 2 for lang in W.ACKS.values() for v in lang.values()) and set(W.ACKS) == {'en', 'kn'}
      and all(set(W.ACKS[l]) == {'text', 'voice', 'image'} for l in W.ACKS)
      and not any(ord(ch) > 0x2000 and not (0x0c80 <= ord(ch) <= 0x0cff) for t in W.ALL_ACKS for ch in t))
check('the wording follows the kind of message', 'voice note' in W.ack_text('voice', 'en') and 'photo' in W.ack_text('image', 'en')
      and 'message' in W.ack_text('text', 'en') and W.ack_text('voice', 'kn') != W.ack_text('text', 'kn'))
check('the two wordings alternate', W.ack_text('text', 'en', 0) != W.ack_text('text', 'en', 1) and W.ack_text('text', 'en', 2) == W.ack_text('text', 'en', 0))
check('an unknown language falls back to English', W.ack_text('text', 'xx') in W.ACKS['en']['text'])
check('a question needs a "got it"; a call by name and a basket line do not (Ask answers those instantly)',
      W.needs_ack('how much did Sirsi sell yesterday?', 'text') and not W.needs_ack('subbi', 'text')
      and not W.needs_ack('eco 65.60, 30', 'text'))
check('a voice note and a picture always get one', W.needs_ack('', 'voice') and W.needs_ack('', 'image'))


def new_listener(infos, **kw):
    ln = listener(infos, **kw)
    sent.clear(); acks.clear(); events.clear()
    send_ok[0] = True
    return ln


ln = new_listener([msgs(('AC01', 'what are sales today'))])
ln.step(ln.page)
check('a new question is acknowledged first, then answered',
      len(acks) == 1 and len(sent) == 1 and events[0] == 'ack' and acks[0][0].startswith('*Subbii:* ') and acks[0][0][10:] in W.ACKS['en']['text'], (acks, sent, events))
check('the "got it" does not count as an answer', ln.status()['answered'] == 1)
check('the "got it" is never kept as conversation history', len(ln.history) == 1 and 'Namaste' not in ln.history[0]['answer'])

ln = new_listener([msgs(('AC01', 'ಇವತ್ತು ಎಷ್ಟು ಮಾರಾಟ?'))])
ln.step(ln.page)
check('a Kannada question gets the Kannada "got it"', len(acks) == 1 and acks[0][0][10:] in W.ACKS['kn']['text'], acks)

ln = new_listener([msgs(('AC01', 'subbi'))])
ln.step(ln.page)
check('a call by name gets its instant greeting and no extra "got it"', acks == [] and len(sent) == 1)

ln = new_listener([msgs(('AC01', 'eco 65.60, 30'))])
ln.step(ln.page)
check('a basket line gets no "got it"', acks == [])

ln = voice_listener([{'open': True, 'title': TITLE, 'rows': [voice_row('AC21', TITLE)]}], transcribe_fn=lambda wav: 'ok')
sent.clear(); acks.clear(); events.clear()
ln.last_lang = 'kn'
ln.step(ln.page)
check('a voice note is acknowledged in the language last heard, before it is even played',
      len(acks) == 1 and acks[0][0][10:] in W.ACKS['kn']['voice'] and events[0] == 'ack', (acks, events))

ln = img_listener([{'open': True, 'title': TITLE, 'rows': [img_row('AC11', TITLE)]}])
sent.clear(); acks.clear(); events.clear()
ln.step(ln.page)
check('a picture is acknowledged before it is read', len(acks) == 1 and acks[0][0][10:] in W.ACKS['en']['image'] and events[0] == 'ack', (acks, events))

ln = new_listener([msgs(('AC01', 'first question'), ('AC02', 'second question'))])
ln.step(ln.page)
check('two messages at once: one "got it" up front, the second one\'s when its turn starts',
      [e if e == 'ack' else e[:15] for e in events] == ['ack', '*answer to* fir', 'ack', '*answer to* sec'], events)

send_ok[0] = False
ln = new_listener([msgs(('AC01', 'a question'))])
send_ok[0] = False
ln.step(ln.page)
send_ok[0] = True
check('if the "got it" cannot be sent the answer is still attempted, and nothing is left half-open',
      len(sent) >= 1 and ln._ack_open is False, (sent, ln._ack_open))

# a slow answer: a message sent while it is being worked out is noticed at once and answered after it
import threading as _th
gate = _th.Event()


def slow_answer(q, history):
    if q == 'slow one':
        gate.wait(5)
    return f'**answer to** {q}'


class LivePage(FakePage):
    """The chat as it changes: the second message appears while the first answer is still being worked out."""

    def __init__(self, first, both):
        super().__init__([first])
        self.first, self.both, self.calls = first, both, 0

    def evaluate(self, js):
        self.calls += 1
        if self.calls >= 3:                 # the scan during the slow answer
            gate.set()                      # let the slow answer finish only after the scan has run
            return self.both
        return self.first


gate.clear()
ln = new_listener([msgs(('AC01', 'slow one'))], answer_fn=slow_answer)
ln.poll_s = 0.05
ln.page = LivePage(msgs(('AC01', 'slow one')), msgs(('AC01', 'slow one'), ('AC02', 'next one')))
ln.step(ln.page)
check('a message sent while an answer is being worked out is noticed meanwhile, not lost or answered twice',
      'AC02' in ln.state.seen and len([s for s in sent if 'slow one' in s[0]]) == 1 and len([s for s in sent if 'next one' in s[0]]) == 1, sent)
check('...and answered right after the first, with its own "got it" at that point',
      [e if e == 'ack' else e[:18] for e in events] == ['ack', '*answer to* slow o', 'ack', '*answer to* next o'] and not ln.pending, events)


class BrokenScanPage(FakePage):
    def __init__(self, info):
        super().__init__([info])
        self.n = 0

    def evaluate(self, js):
        self.n += 1
        if self.n >= 3:
            gate2.set()
            raise RuntimeError('page went away')
        return self.last


gate2 = _th.Event()


def slow_answer2(q, history):
    gate2.wait(5)
    return 'the answer'


ln = new_listener([msgs(('AC01', 'ask this'))], answer_fn=slow_answer2)
ln.poll_s = 0.05
ln.page = BrokenScanPage(msgs(('AC01', 'ask this')))
ln.step(ln.page)
check('a failed look at the chat while an answer is being worked out does not lose the answer',
      len(sent) == 1 and 'the answer' in sent[0][0], sent)

# --- more than one person ---------------------------------------------------------------------------------
check('numbers are read from a comma-separated list, bad ones and repeats dropped',
      W.parse_numbers('9000000001, 9000000002;9000000001 12') == ['919000000001', '919000000002'] and W.parse_numbers('') == [])
os.environ['WHATSAPP_ASK_NUMBER'] = '9000000001,9000000002'
cfg = W.settings()
check('settings carry every number, the first is the owner', cfg['numbers'] == ['919000000001', '919000000002'] and cfg['number'] == '919000000001')
check('the first person keeps the first version\'s profile and state file; the next ones get their own',
      W.profile_dir_for(0) == W.PROFILE_DIR and W.profile_dir_for(1).name == '.whatsapp_ask_profile_2' and W.profile_dir_for(2).name == '.whatsapp_ask_profile_3'
      and W.state_path_for('919000000001', 0) == W.STATE_PATH
      and W.state_path_for('919000000002', 1) != W.STATE_PATH and W.state_path_for('919000000002', 1) == W.state_path_for('919000000002', 1)
      and '9000000002' not in W.state_path_for('919000000002', 1).name)
cfg['enabled'] = True
built = W.build_listeners(cfg, transcribe_fn=lambda w: 'x')
check('one listener per number, each with its own profile, state, basket key and label',
      len(built) == 2 and [b.label for b in built] == ['owner', 'person 2']
      and built[0].profile_dir != built[1].profile_dir and built[0].state.path != built[1].state.path
      and built[0].owner_key != built[1].owner_key and built[1].owner_key.startswith(W.WA_OWNER)
      and all(b.transcribe_fn is not None for b in built))
check('each person has a conversation and a queue of their own', built[0].history is not built[1].history and built[0].pending is not built[1].pending)
check('the second person\'s Ask calls use their own basket key',
      built[1].answer_fn.__closure__ is not None)

started = []
orig_start, W.Listener.start = W.Listener.start, lambda self: started.append(self.label)
orig_stagger, W.START_STAGGER_S = W.START_STAGGER_S, 0.05
os.environ['WHATSAPP_ASK_ENABLED'] = '1'
got_list = W.start_from_env(transcribe_fn=lambda w: 'x')
time.sleep(0.4)
check('both listeners are started, the second a moment after the first', got_list and len(got_list) == 2 and started == ['owner', 'person 2'], started)
st = W.status()
check('the status shows each person by label only, never a number or a name',
      st['state'] in ('stopped',) and [e['label'] for e in st['listeners']] == ['owner', 'person 2'] and '9000000001' not in str(st))
W.Listener.start, W.START_STAGGER_S = orig_start, orig_stagger
W._listeners = []
os.environ.pop('WHATSAPP_ASK_ENABLED', None); os.environ.pop('WHATSAPP_ASK_NUMBER', None)

import re as _re
cmd0 = W.stale_chrome_command(W.profile_dir_for(0))
pat0 = _re.search(r"-match '(.*)' \}", cmd0).group(1).replace("''", "'")
line0 = 'chrome.exe --user-data-dir=' + str(W.profile_dir_for(0)) + ' --flag'
line0q = 'chrome.exe "--user-data-dir=' + str(W.profile_dir_for(0)) + '"'
line1 = 'chrome.exe --user-data-dir=' + str(W.profile_dir_for(1)) + ' --flag'
check('cleaning up the first window\'s leftover Chrome never touches the second window\'s',
      _re.search(pat0, line0) and _re.search(pat0, line0q) and not _re.search(pat0, line1), pat0)

# --- sending a file ------------------------------------------------------------------------------------------
class AttachPage:
    """Just enough of WhatsApp Web for send_file: the Attach button, the Document item, the file chooser,
    the preview's Send button and the conversation rows."""

    def __init__(self, no_attach=False, no_preview=False, sees_row=True, preview_closes=True, caption_fails=False, no_caption_box=False):
        self.log = []
        self.no_attach, self.no_preview, self.sees_row, self.preview_closes, self.caption_fails = no_attach, no_preview, sees_row, preview_closes, caption_fails
        self.no_caption_box = no_caption_box
        self.preview_open = False
        self.confirm_open = False
        self.file = None
        self.rows = ['an older message']
        outer = self

        class Keyboard:
            def insert_text(self, t):
                if outer.caption_fails:
                    raise RuntimeError('no caption box')
                outer.log.append('caption:' + t[:20])

            def press(self, k):
                outer.log.append('key:' + k)        # Escape does NOT close the preview on the real page

        self.keyboard = Keyboard()

        class Loc:
            def __init__(self, kind):
                self.kind = kind

            @property
            def first(self_):
                return self_

            @property
            def last(self_):
                return self_

            def count(self_):
                if self_.kind == 'attach':
                    return 0 if outer.no_attach else 1
                if self_.kind == 'send':
                    return 1 if outer.preview_open else 0
                if self_.kind == 'rows':
                    return len(outer.rows)
                if self_.kind == 'close':
                    return 1 if outer.preview_open else 0
                if self_.kind == 'discard':
                    return 1 if outer.confirm_open else 0
                return 0

            def is_visible(self_):
                return outer.preview_open

            def click(self_, timeout=0):
                if self_.kind == 'attach':
                    if outer.no_attach:
                        raise TimeoutError('no attach button')
                    outer.log.append('attach')
                elif self_.kind == 'close':
                    outer.log.append('close')
                    outer.confirm_open = True        # "Discard selection?" appears
                elif self_.kind == 'discard':
                    outer.log.append('discard')
                    outer.confirm_open = False
                    outer.preview_open = False
                elif self_.kind == 'send':
                    outer.log.append('send')
                    if outer.preview_closes:
                        outer.preview_open = False
                    if outer.sees_row:
                        outer.rows.append('%s 31 KB 3:50 pm' % Path(outer.file).name)

            def nth(self_, i):
                class R:
                    def inner_text(s):
                        return outer.rows[i]
                return R()

        self._Loc = Loc

    def locator(self, sel):
        if sel == W._ATTACH_BUTTON:
            return self._Loc('attach')
        if sel == W._PREVIEW_SEND:
            return self._Loc('send')
        if sel == W.wa._MESSAGE_ROW:
            return self._Loc('rows')
        if sel == W._PREVIEW_CLOSE:
            return self._Loc('close')
        return self._Loc('other')

    def get_by_role(self, role, name='', exact=False):
        return self._Loc('discard') if (role, name) == ('button', 'Discard') else self._Loc('other')

    def get_by_text(self, text, exact=False):
        outer = self

        class Item:
            @property
            def first(s):
                return s

            def click(s, timeout=0):
                outer.log.append('item:' + text)
        return Item()

    def expect_file_chooser(self, timeout=0):
        outer = self

        class CM:
            def __enter__(s):
                class V:
                    def set_files(v, path):
                        outer.file = path
                        outer.log.append('files')
                        if not outer.no_preview:
                            outer.preview_open = True
                s.value = V()
                return s

            def __exit__(s, *a):
                return False
        return CM()

    def evaluate(self, js):
        if js == W._FOCUS_CAPTION_JS:
            self.log.append('focus')
            return not self.no_caption_box
        return ['footer:button:Attach', 'footer:button:Emoji']

    def wait_for_timeout(self, ms):
        pass


rep = Path(tempfile.mkdtemp()) / 'GST Report From 1-10-2026 To 31-10-2026.xlsx'
rep.write_bytes(b'x' * 2048)
msgs_log = []
ap = AttachPage()
out = W.send_file(ap, rep, 'GST report 2026-10', on_progress=msgs_log.append)
check('a file is attached as a document, captioned, sent, and seen in the conversation',
      out == {'sent': True, 'reason': 'seen in the conversation'} and ap.log == ['attach', 'item:Document', 'files', 'focus', 'caption:GST report 2026-10', 'send'], (out, ap.log))
check('the file that went into the chooser is the one asked for', ap.file == str(rep))
check('a missing file is refused before any click', W.send_file(AttachPage(), rep.with_name('nothing.xlsx'))['reason'] == 'file_missing')
orig_max, W.MAX_FILE_BYTES = W.MAX_FILE_BYTES, 100
check('a file over the size limit is refused', W.send_file(AttachPage(), rep)['reason'] == 'file_too_big')
W.MAX_FILE_BYTES = orig_max
ap = AttachPage()
out = W.send_file(ap, rep, 'x', dry_run=True)
check('a dry run attaches, then closes the preview through Close and Discard, without sending',
      out['dry_run'] and 'send' not in ap.log and 'close' in ap.log and 'discard' in ap.log and not ap.preview_open, ap.log)
ap, lg = AttachPage(no_attach=True), []
out = W.send_file(ap, rep, on_progress=lg.append)
check('no Attach button: nothing is sent, the screen labels are logged (never message text), and the Attach menu is dismissed',
      out['reason'] == 'attach_failed' and any('footer:button:Attach' in m for m in lg) and ap.log[-1] == 'key:Escape', (out, lg))
ap = AttachPage(no_preview=True)
orig_time = W.time.time
t = [1000.0]
W.time.time = lambda: (t.__setitem__(0, t[0] + 3) or t[0])      # the clock runs fast: the 20-second wait ends at once
out = W.send_file(ap, rep, on_progress=lambda m: None)
W.time.time = orig_time
check('a preview that never appears fails cleanly instead of pressing Send into the void', out['reason'] == 'attach_failed' and 'send' not in ap.log, (out, ap.log))
ap = AttachPage()
ap.preview_open = True
W._close_preview(ap)
check('closing a preview that is open takes Close then Discard and leaves nothing open over the chat', not ap.preview_open and ap.log[:2] == ['close', 'discard'], ap.log)
ap = AttachPage()
W._close_preview(ap)
check('closing when nothing is open does nothing but dismiss a menu', ap.log == ['key:Escape'], ap.log)
ap = AttachPage(caption_fails=True)
check('a caption that cannot be typed does not stop the file', W.send_file(ap, rep, 'caption')['sent'] is True and 'send' in ap.log)
ap = AttachPage(no_caption_box=True)
check('no caption box in the preview: nothing is typed anywhere (never into the message box underneath), and the file still goes',
      W.send_file(ap, rep, 'caption')['sent'] is True and not any(l.startswith('caption:') for l in ap.log) and 'send' in ap.log, ap.log)
check('the preview Send is found by its own "Send N selected" label, never the plain "Send" of the message box',
      W._PREVIEW_SEND == '[role="button"][aria-label^="Send"][aria-label$="selected"]')
ap = AttachPage(sees_row=False, preview_closes=True)
out = W.send_file(ap, rep)
check('sent but not seen, with the preview closed: reported as sent, with the weaker reason', out['sent'] is True and 'not seen' in out['reason'], out)
ap = AttachPage(sees_row=False, preview_closes=False)
orig_time = W.time.time
t = [1000.0]
W.time.time = lambda: (t.__setitem__(0, t[0] + 3) or t[0])      # the clock runs fast: the 25-second wait ends at once
out = W.send_file(ap, rep)
W.time.time = orig_time
check('sent, not seen and the preview still open: NOT reported as sent (the owner is told to check, never a blind retry)',
      out == {'sent': False, 'reason': 'unconfirmed'}, out)

# the listener sends queued files right after the text answer
holder = {}
file_calls = []


def fake_send_file(page, path, caption='', dry_run=False, on_progress=None):
    file_calls.append((path, caption, dry_run))
    events.append('FILE')
    return {'sent': True, 'reason': 'seen'}


def asks_for_a_report(q, history):
    holder['ln'].queue_file(rep, 'GST report 2026-10')        # what the CRM tool does, from the worker thread
    return 'Here is the report.'


ln = new_listener([msgs(('AC01', 'october gst report'))], answer_fn=asks_for_a_report)
holder['ln'] = ln
ln.send_file_fn = fake_send_file
ln.step(ln.page)
check('the report goes out after the text answer, not before',
      [e if e in ('ack', 'FILE') else 'answer' for e in events] == ['ack', 'answer', 'FILE'] and file_calls == [(str(rep), 'GST report 2026-10', False)], events)
check('files sent are counted apart from answers', ln.status().get('files_sent') == 1 and ln.status()['answered'] == 1)
ln.step(ln.page)
check('a sent file is not sent again', len(file_calls) == 1)

ln = new_listener([msgs()])
ln.send_file_fn = lambda page, path, caption='', dry_run=False, on_progress=None: {'sent': False, 'reason': 'attach_failed'}
ln.queue_file(rep, 'x')
ln.step(ln.page)
check('a file that could not be sent is said so in the chat (never silence), and the answer count is unchanged',
      len(sent) == 1 and 'could not send the file' in sent[0][0] and 'attach_failed' in sent[0][0] and 'saved on the shop PC' in sent[0][0] and ln.status()['answered'] == 0, sent)


def raising(page, path, caption='', dry_run=False, on_progress=None):
    raise RuntimeError('chrome died')


ln = new_listener([msgs()])
ln.send_file_fn = raising
ln.queue_file(rep, 'x')
ln.step(ln.page)
check('a crash while sending is caught, reported in the chat, and the listener carries on', len(sent) == 1 and 'RuntimeError' in sent[0][0])
ln = new_listener([msgs()], dry_run=True)
ln.send_file_fn = lambda page, path, caption='', dry_run=False, on_progress=None: {'sent': False, 'dry_run': True, 'reason': 'dry_run'}
ln.queue_file(rep, 'x')
ln.step(ln.page)
check('a dry run says nothing in the chat', sent == [])

# delivery: only the CRM download folder, and to the right chat
import crm_reader
orig_dir = crm_reader.DOWNLOAD_DIR
crm_reader.DOWNLOAD_DIR = rep.parent
os.environ['WHATSAPP_ASK_NUMBER'] = '9000000001,9000000002'
cfg2 = dict(W.settings(), enabled=True)
two = W.build_listeners(cfg2)
W._listeners = two
check('a report in the download folder is queued for the person who asked',
      W.deliver_file('whatsapp-owner-2', rep, 'cap') is True and len(two[1].file_queue) == 1 and len(two[0].file_queue) == 0)
check('...the owner\'s own question to the owner\'s chat', W.deliver_file(W.WA_OWNER, rep, 'cap') is True and len(two[0].file_queue) == 1)
check('...and a question from the Ask web page (a session token) to the owner\'s chat', W.deliver_file('some-session-token', rep, 'cap') is True and len(two[0].file_queue) == 2)
other = Path(tempfile.mkdtemp()) / 'secret.txt'
other.write_text('private', encoding='utf-8')
check('a file anywhere else on the PC is refused, so the hook cannot be used to send it', W.deliver_file(W.WA_OWNER, other, 'x') is False and len(two[0].file_queue) == 2)
sneaky = rep.parent / '..' / other.parent.name / 'secret.txt'
check('a path that climbs out of the folder is refused too', W.deliver_file(W.WA_OWNER, sneaky, 'x') is False)
check('a file that does not exist is refused', W.deliver_file(W.WA_OWNER, rep.with_name('gone.xlsx'), 'x') is False)
W._listeners = []
check('with no WhatsApp listeners running nothing is queued', W.deliver_file(W.WA_OWNER, rep, 'x') is False)
crm_reader.DOWNLOAD_DIR = orig_dir
os.environ.pop('WHATSAPP_ASK_NUMBER', None)

# --- Ask answers like the Ask page ------------------------------------------------------------
import ask as ask_mod
import ask_limits
calls = []
orig = (ask_mod.ask, ask_limits.check, ask_mod.basket_reply, ask_mod.name_call_greeting)
ask_mod.ask = lambda q, history=None, owner=None, **kw: calls.append((q, history, owner)) or 'MODEL'
ask_limits.check = lambda *a, **k: calls.append('limit-checked')
ask_mod.basket_reply = lambda q, owner: {'say': 'BASKET'} if q == 'calculate' else None
ask_mod.name_call_greeting = lambda q: 'GREET' if q == 'subbi' else None
check('a call by name is answered instantly, free', W.ask_answer('subbi', []) == 'GREET' and calls == [])
check('a basket command is answered by rules, not counted as a question', W.ask_answer('calculate', []) == 'BASKET' and calls == [])
check('a question is counted against the cap, then asked with this chat\'s basket key',
      W.ask_answer('sales?', [{'question': 'a', 'answer': 'b'}]) == 'MODEL'
      and calls == ['limit-checked', ('sales?', [{'question': 'a', 'answer': 'b'}], W.WA_OWNER)], calls)


def over(*a, **k):
    raise ask_limits.LimitReached('Today\'s limit has been reached')


ask_limits.check = over
check('the daily cap reaches the owner as a plain message', W.ask_answer('sales?', []) == "Today's limit has been reached")
ask_mod.ask, ask_limits.check, ask_mod.basket_reply, ask_mod.name_call_greeting = orig

print()
print('ALL PASSED' if not fails else f'{len(fails)} FAILED: ' + '; '.join(fails))
sys.exit(1 if fails else 0)

"""Offline tests for the 2026-10-12 voice fixes (server.py transcribe fallback + echo
guard, ask.py spoken-answer model, ask_dashboard.html interrupt threshold).

No network, no model call, no real data: the OpenRouter client is a scripted fake, the
page is driven through Flask's test client with a throwaway in-process session.

Run with the reckoner's venv:
    venv/Scripts/python.exe test_voice_fixes.py
"""
import copy
import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('OPENROUTER_API_KEY', 'sk-test-not-real')
os.environ['OPENROUTER_MODEL_FAST'] = 'fake/fast-model'
os.environ['OPENROUTER_MODEL_REASONING'] = 'fake/reasoning-model'
for v in ('ORDER_ASK_URL', 'ORDER_ASK_KEY'):
    os.environ.pop(v, None)

import openai  # noqa: E402
import ask  # noqa: E402
import server  # noqa: E402

print('testing:', ROOT)
print('  server from', server.__file__)
FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'   <-- {detail}'))
    if not cond:
        FAILS.append(name)


# --- 1. the echo guard ------------------------------------------------------------------
LAST = ('The total sales revenue for last month was about one crore thirty lakh rupees. '
        'This represents our combined performance across all branches.')
echo = server._echo_of_last_answer
check('verbatim echo is caught', echo('the total sales revenue for last month was about one crore thirty lakh rupees', LAST))
check('garbled echo (words dropped by the mic) is caught', echo('total sales revenue last month about one crore thirty lakh rupees combined performance', LAST))
check('echo with trailing punctuation / capitals is caught', echo('The total sales revenue for last month was about one crore, thirty lakh rupees.', LAST))
check('a short follow-up is NOT an echo ("and last month?")', not echo('and last month?', LAST))
check('a follow-up that shares a word is NOT an echo', not echo('is that revenue before or after tax?', LAST))
check('a branch follow-up is NOT an echo', not echo('what about Hospet Road branch?', LAST))
check('a new question is NOT an echo', not echo('where is my dolo order', LAST))
check('no last answer -> never an echo', not echo('the total sales revenue for last month was about one crore', ''))
KN = 'ಕಳೆದ ತಿಂಗಳು ನಮ್ಮ ಒಟ್ಟು ಮಾರಾಟ ಸುಮಾರು ಒಂದು ಕೋಟಿ ಮೂವತ್ತು ಲಕ್ಷ ರೂಪಾಯಿ ಆಗಿತ್ತು.'
check('Kannada echo is caught (words kept whole)', echo('ಕಳೆದ ತಿಂಗಳು ನಮ್ಮ ಒಟ್ಟು ಮಾರಾಟ ಸುಮಾರು ಒಂದು ಕೋಟಿ', KN))
check('Kannada new question is NOT an echo', not echo('ಡೊಲೊ ಆರ್ಡರ್ ಎಲ್ಲಿದೆ ಈಗ ಹೇಳಿ ನನಗೆ', KN))
check('the gate prompt now names the echo case too', 'repeats the assistant\'s own last answer' in server.SPEECH_GATE_PROMPT)


# --- 2. transcription: time limit + fallback --------------------------------------------
class FakeTranscriptions:
    def __init__(self, log, behaviour):
        self.log, self.behaviour = log, behaviour

    def create(self, model, file):
        self.log.append(model)
        b = self.behaviour.get(model, 'ok')
        if b == 'timeout':
            raise openai.APITimeoutError(request=types.SimpleNamespace(url='x'))
        if b == 'error':
            raise RuntimeError('provider down')
        return types.SimpleNamespace(text=f'  heard by {model}  ', usage=None)


class FakeClient:
    def __init__(self, behaviour):
        self.log, self.behaviour, self.options = [], behaviour, []

    def with_options(self, **kw):
        self.options.append(kw)
        return self

    @property
    def audio(self):
        return types.SimpleNamespace(transcriptions=FakeTranscriptions(self.log, self.behaviour))


real_client = server._openrouter_client
PRIMARY, FALLBACK = server.SPEECH_STT_MODEL, server.SPEECH_STT_FALLBACK_MODEL
check('a fallback model is configured by default', bool(FALLBACK) and FALLBACK != PRIMARY, FALLBACK)

fc = FakeClient({PRIMARY: 'timeout'})
server._openrouter_client = lambda: fc
text, cost, used = server._transcribe(b'RIFF....')
check('primary times out -> fallback transcribes the same clip', used == FALLBACK and text == f'heard by {FALLBACK}' and fc.log == [PRIMARY, FALLBACK], (used, text, fc.log))
check('the primary call carries the time limit and no SDK retries', fc.options[0] == {'timeout': server.SPEECH_STT_TIMEOUT_S, 'max_retries': 0}, fc.options)
check('the fallback call is bounded too', fc.options[1].get('max_retries') == 0 and 0 < fc.options[1].get('timeout', 0) <= 30, fc.options)

fc = FakeClient({})
server._openrouter_client = lambda: fc
text, cost, used = server._transcribe(b'RIFF....')
check('primary answers in time -> fallback is not called', used == PRIMARY and fc.log == [PRIMARY], fc.log)

fc = FakeClient({PRIMARY: 'error', FALLBACK: 'error'})
server._openrouter_client = lambda: fc
try:
    server._transcribe(b'RIFF....')
    check('both fail -> the error surfaces (route turns it into a 502)', False, 'no exception')
except Exception:
    check('both fail -> the error surfaces (route turns it into a 502)', True)

server.SPEECH_STT_FALLBACK_MODEL = ''
fc = FakeClient({PRIMARY: 'timeout'})
server._openrouter_client = lambda: fc
try:
    server._transcribe(b'RIFF....')
    check('with no fallback configured a timeout surfaces', False, 'no exception')
except Exception:
    check('with no fallback configured a timeout surfaces', True)
server.SPEECH_STT_FALLBACK_MODEL = FALLBACK
server._openrouter_client = real_client


# --- 3. the route: echo is refused without a paid gate call; questions still gated ---------
tok = server._create_session('tester')
H = {'X-Session-Token': tok, 'Content-Type': 'audio/wav'}
cl = server.app.test_client()
server.warm_ask_cache_in_background = lambda: None
gate_calls = []
real_gate, real_transcribe = server._speech_gate, server._transcribe


def post(text, last_answer, gate_result=('YES', 0.0)):
    server._transcribe = lambda audio: (text, 0.0001, PRIMARY)
    server._speech_gate = lambda t, la: (gate_calls.append((t, la)), gate_result)[1]
    import urllib.parse
    h = dict(H, **{'X-Voice-Context': urllib.parse.quote(last_answer)})
    r = cl.post('/api/speech/transcribe?gate=1', data=b'RIFF' + b'\0' * 64, headers=h)
    return r.status_code, r.get_json()


st, body = post('the total sales revenue for last month was about one crore thirty lakh rupees', LAST)
check('route: echoed answer -> addressed=false, echo=true', st == 200 and body['addressed'] is False and body.get('echo') is True, (st, body))
check('route: ...and the paid gate was NOT called for it', gate_calls == [], gate_calls)
st, body = post('where is my dolo order', LAST)
check('route: a real question still goes through the gate', st == 200 and body['addressed'] is True and len(gate_calls) == 1, (st, body, gate_calls))
check('route: the gate receives the last answer for follow-up context', gate_calls and gate_calls[0][1] == LAST)
st, body = post('two strips of dolo please', LAST, gate_result=('NO', 0.0))
check('route: counter chatter the gate rejects -> addressed=false, echo=false', body['addressed'] is False and body.get('echo') is False, body)
# Owner's rule (16 Oct 2026): one or two stray words are ignored with a "hmm" - and never cost a gate call
gate_calls.clear()
st, body = post('haan haan', LAST)
check('route: a one/two-word interjection -> short=true, addressed=false, no gate call',
      body.get('short') is True and body['addressed'] is False and gate_calls == [], (body, gate_calls))
st, body = post('subbi the thing about', LAST, gate_result=('UNCLEAR', 0.0))
check('route: a fragment the gate calls UNCLEAR -> unclear=true, addressed=false (page says "repeat your question")',
      body.get('unclear') is True and body['addressed'] is False and body.get('short') is False, body)
r = cl.post('/api/speech/transcribe', data=b'RIFF' + b'\0' * 64, headers=H)
check('route: without gate=1 (push-to-talk) nothing is screened', r.get_json()['addressed'] is True)
server._transcribe = lambda audio: (_ for _ in ()).throw(RuntimeError('both failed'))
r = cl.post('/api/speech/transcribe', data=b'RIFF' + b'\0' * 64, headers=H)
check('route: transcription failure -> 502 with a readable error', r.status_code == 502 and 'Could not transcribe' in r.get_json()['error'], (r.status_code, r.get_json()))
server._speech_gate, server._transcribe = real_gate, real_transcribe


# --- 4. ask.py: who writes the final answer ----------------------------------------------
class FakeCompletions:
    def __init__(self, script):
        self.script, self.requests = list(script), []

    def create(self, **kw):
        self.requests.append(copy.deepcopy(kw))
        return self.script.pop(0)


def reply(content=None, calls=None):
    tcs = None
    if calls:
        tcs = [types.SimpleNamespace(id=f'c{i}', function=types.SimpleNamespace(name=n, arguments=json.dumps(a)))
               for i, (n, a) in enumerate(calls)]
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content, tool_calls=tcs))], usage=None)


real_run_tool, real_notes = ask.T.run_tool, ask.T.get_notes
ask.T.run_tool = lambda name, args: {'ok': True}
ask.T.get_notes = lambda n=20: {'notes': []}


def run(script, **kw):
    fake = FakeCompletions(script)
    openai.OpenAI = lambda **k: types.SimpleNamespace(chat=types.SimpleNamespace(completions=fake))
    return ask.ask('How much did we sell last month?', **kw), fake


try:
    answer, fake = run([reply(calls=[('get_overview', {})]), reply('(ready)'), reply('Spoken answer.')], speak=True)
    models = [r['model'] for r in fake.requests]
    check('spoken: the FAST model writes the final answer', answer == 'Spoken answer.' and models[-1] == 'fake/fast-model', models)
    check('spoken: only three model calls (tool round, ready, answer) - no reasoning detour', len(models) == 3, models)
    answer, fake = run([reply(calls=[('get_overview', {})]), reply('(ready)'), reply('Typed answer.')])
    models = [r['model'] for r in fake.requests]
    check('typed: the REASONING model still writes the final answer', answer == 'Typed answer.' and models[-1] == 'fake/reasoning-model', models)
    answer, fake = run([reply(calls=[('get_overview', {})]), reply('(ready)'), reply(''), reply('Retried answer.')])
    models = [r['model'] for r in fake.requests]
    check('typed: an empty reasoning answer still falls back to the fast model', answer == 'Retried answer.' and models[-2:] == ['fake/reasoning-model', 'fake/fast-model'], models)
    answer, fake = run([reply(calls=[('get_overview', {})]), reply('(ready)'), reply('Live answer.')], voice=True)
    models = [r['model'] for r in fake.requests]
    check('live voice (delegated): the FAST model writes the final answer', answer == 'Live answer.' and models[-1] == 'fake/fast-model' and len(models) == 3, models)
    sysmsg = fake.requests[0]['messages'][0]['content']
    check('live voice: the delegated question still carries the identity and the live-voice rules', 'suubbiii' in sysmsg and 'LIVE VOICE conversation' in sysmsg)
finally:
    ask.T.run_tool, ask.T.get_notes = real_run_tool, real_notes

# --- 4c. the live-voice persona (GPT-Live's own instructions) ------------------------------------
VI = ' '.join(server.VOICE_INSTRUCTIONS.split())
check('live persona: named suubbiii', 'Your name is suubbiii' in VI)
# The family facts are private (public repo): they come from the gitignored owner_facts.txt
# at run time, so the test uses a stand-in file with made-up names.
_real_facts = ask.OWNER_FACTS_PATH
ask.OWNER_FACTS_PATH = Path(tempfile.mkdtemp(prefix='owner_facts_')) / 'owner_facts.txt'
ask.OWNER_FACTS_PATH.write_text("Your boss is Test Owner. Test Owner's wife is Test Spouse, and "
                                "Test Owner's son is Test Son.\n", encoding='utf-8')
VF = ' '.join(server.voice_instructions().split())
check('live persona: the family facts come from the private owner_facts.txt, not the code',
      'Your boss is Test Owner' in VF and 'Test Spouse' in VF and 'Test Son' in VF and 'Test Owner' not in VI)
ask.OWNER_FACTS_PATH.unlink()
check('live persona: without the private file there are no family facts', server.voice_instructions() == server.VOICE_INSTRUCTIONS)
ask.OWNER_FACTS_PATH = _real_facts
check('live persona: does not assume who is speaking', 'never assume' in VI)
check('live persona: knows order questions are delegated too', 'order-tracking' in VI and 'overdue' in VI)
check('live persona: still delegates every figure', 'Delegate every question that needs a figure' in VI)
check('live voice is gated by VOICE_LIVE_ENABLED', hasattr(server, 'VOICE_LIVE_ENABLED') and server.VOICE_MAX_MINUTES == 10)


# --- 4b. text-to-speech: time limit + fallback ----------------------------------------------
class FakeSpeech:
    def __init__(self, log, behaviour):
        self.log, self.behaviour = log, behaviour

    def create(self, model, voice, input, response_format):
        self.log.append((model, response_format))
        b = self.behaviour.get(model, 'ok')
        if b == 'timeout':
            raise openai.APITimeoutError(request=types.SimpleNamespace(url='x'))
        if b == 'error':
            raise RuntimeError('provider down')
        hdrs = {'content-type': 'audio/pcm;rate=24000;channels=1', 'x-generation-id': 'gen-' + model[-6:]}
        return types.SimpleNamespace(content=b'\0\0' * 2400, response=types.SimpleNamespace(headers=hdrs))


class FakeTtsClient:
    def __init__(self, behaviour):
        self.log, self.behaviour, self.options = [], behaviour, []

    def with_options(self, **kw):
        self.options.append(kw)
        return self

    @property
    def audio(self):
        return types.SimpleNamespace(speech=FakeSpeech(self.log, self.behaviour))


TTS_PRIMARY, TTS_FALLBACK = server.SPEECH_TTS_MODEL, server.SPEECH_TTS_FALLBACK_MODEL
check('tts: a fallback model is configured by default and differs from the primary', bool(TTS_FALLBACK) and TTS_FALLBACK != TTS_PRIMARY, TTS_FALLBACK)
check('tts: the fallback is a Gemini voice (same Kore voice, so Kannada keeps working)', 'gemini' in TTS_FALLBACK)

fc = FakeTtsClient({TTS_PRIMARY: 'timeout'})
server._openrouter_client = lambda: fc
audio, fmt, ctype, gen, used = server._synthesize('hello')
check('tts: primary times out -> fallback speaks the same text', used == TTS_FALLBACK and fc.log == [(TTS_PRIMARY, 'pcm'), (TTS_FALLBACK, 'pcm')] and gen.endswith(TTS_FALLBACK[-6:]), (used, fc.log, gen))
check('tts: the primary call carries the time limit and no SDK retries', fc.options[0] == {'timeout': server.SPEECH_TTS_TIMEOUT_S, 'max_retries': 0}, fc.options)
check('tts: the fallback call is bounded too', fc.options[1].get('max_retries') == 0 and 0 < fc.options[1].get('timeout', 0) <= 30, fc.options)

fc = FakeTtsClient({})
server._openrouter_client = lambda: fc
audio, fmt, ctype, gen, used = server._synthesize('hello')
check('tts: primary answers in time -> fallback is not called', used == TTS_PRIMARY and fc.log == [(TTS_PRIMARY, 'pcm')], fc.log)

fc = FakeTtsClient({TTS_PRIMARY: 'error', TTS_FALLBACK: 'error'})
server._openrouter_client = lambda: fc
r = cl.post('/api/speech/tts', data=json.dumps({'text': 'hello'}), headers={'X-Session-Token': tok, 'Content-Type': 'application/json'})
check('tts route: both voices fail -> 502 (the page then uses the phone voice)', r.status_code == 502 and 'Could not create speech' in r.get_json()['error'], (r.status_code, r.get_json()))

fc = FakeTtsClient({TTS_PRIMARY: 'timeout'})
server._openrouter_client = lambda: fc
r = cl.post('/api/speech/tts', data=json.dumps({'text': 'hello'}), headers={'X-Session-Token': tok, 'Content-Type': 'application/json'})
check('tts route: fallback result is wrapped as a WAV with the generation id header', r.status_code == 200 and r.data[:4] == b'RIFF' and r.mimetype == 'audio/wav' and r.headers.get('X-Generation-Id', '').endswith(TTS_FALLBACK[-6:]), (r.status_code, r.mimetype, dict(r.headers)))
server._openrouter_client = real_client


# --- 5. the page ----------------------------------------------------------------------------
html = (ROOT / 'ask_dashboard.html').read_text(encoding='utf-8')
check('page: interrupt threshold while an answer plays is now 800 ms', 'const TALK_START_MS_OVER_ANSWER = 800;' in html)
check('page: normal start threshold unchanged (200 ms)', 'const TALK_START_MS = 200;' in html)
check('page: the 3-quiet-minute end is unchanged (owner: keep it)', 'const TALK_IDLE_END_MS = 3 * 60000;' in html)

print()
print(f'{len(FAILS)} FAILED: {FAILS}' if FAILS else 'ALL PASSED')
sys.exit(1 if FAILS else 0)

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


def post(text, last_answer, gate_result=(True, 0.0)):
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
st, body = post('two strips of dolo please', LAST, gate_result=(False, 0.0))
check('route: counter chatter the gate rejects -> addressed=false, echo=false', body['addressed'] is False and body.get('echo') is False, body)
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
finally:
    ask.T.run_tool, ask.T.get_notes = real_run_tool, real_notes


# --- 5. the page ----------------------------------------------------------------------------
html = (ROOT / 'ask_dashboard.html').read_text(encoding='utf-8')
check('page: interrupt threshold while an answer plays is now 800 ms', 'const TALK_START_MS_OVER_ANSWER = 800;' in html)
check('page: normal start threshold unchanged (200 ms)', 'const TALK_START_MS = 200;' in html)
check('page: the 3-quiet-minute end is unchanged (owner: keep it)', 'const TALK_IDLE_END_MS = 3 * 60000;' in html)

print()
print(f'{len(FAILS)} FAILED: {FAILS}' if FAILS else 'ALL PASSED')
sys.exit(1 if FAILS else 0)

r"""
Ask the Reckoner - a conversational BI over your pharmacy data
==============================================================
Type a question in plain English; a model answers by calling the local report
tools in ask_tools.py - it never touches the raw tables directly. Product,
supplier, and employee names/figures go out in full (business info); customer
identity is pseudonymous by default (get_top_customers/get_customer_trends
only ever hand out a one-way 'Cust_xxxxxx' code) - EXCEPT get_patient_history
and get_product_patient_history, two tools the owner explicitly asked for,
which send a real patient's name, mobile number, and purchase history to the
external AI provider on purpose. See ask_tools.py's docstring for the full
privacy boundary.

    python ask.py "is my wholesale channel profitable?"
    python ask.py --dry-run "..."   # show exactly what would be sent out,
                                     # and a sample tool result - NO model call
    python ask.py --selftest        # run every tool, confirm the PII boundary
                                     # holds except the two deliberate exceptions
    python ask.py --tools           # list the tools the model can call

Two backends, chosen by env ASK_BACKEND (default "openrouter"):

  openrouter (default) - https://openrouter.ai, any hosted model, via the
  OpenAI SDK pointed at OpenRouter's OpenAI-compatible endpoint. Two-tier
  routing: a fast/cheap model drives the tool-calling loop, a stronger model
  writes the final answer:
    - env: OPENROUTER_API_KEY=<your key>        (get one at openrouter.ai/keys)
           OPENROUTER_MODEL_FAST=google/gemini-3.1-flash-lite      (default)
           OPENROUTER_MODEL_REASONING=google/gemini-3.1-pro-preview (default)
    Then:  pip install openai

  vertex - Gemini directly on Google Vertex AI:
    - a Google Cloud project with the Vertex AI API enabled + billing on
    - credentials on this PC: run `gcloud auth application-default login`
      (or set GOOGLE_APPLICATION_CREDENTIALS to a service-account key file)
    - env: ASK_BACKEND=vertex   VERTEX_PROJECT=<your-project-id>
           VERTEX_LOCATION=asia-south1   GEMINI_MODEL=gemini-2.5-pro
    Then:  pip install google-genai

Env vars can also go in a `.env` file next to this script (KEY=value, one per
line, '#' comments allowed) instead of being set system-wide - handy on
Windows where a just-set system env var needs a fresh terminal/app restart to
be seen. `.env` is gitignored; a real environment variable always wins over it.
"""
import json
import os
import sys
import textwrap
from pathlib import Path

import ask_tools as T

ROOT = Path(__file__).parent


def _load_dotenv():
    """Populate os.environ from a local .env file, without overriding any
    variable that's already set for real (system/session env wins)."""
    env_path = ROOT / '.env'
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()

# Answers include a Kannada summary and Rupee figures (see SYSTEM_PROMPT) -
# on Windows, stdout/stderr often default to the legacy system codepage
# (e.g. cp1252) rather than UTF-8 when not attached to a real console (piped,
# redirected, or some terminal setups), which crashes on the first non-ASCII
# character printed. Force UTF-8 so the answer always prints instead.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

SYSTEM_PROMPT = """\
You are the analyst for a three-branch pharmacy in Karnataka, India. You help
the owner understand their business and decide what to do.

Rules:
- ALWAYS get numbers by calling a tool. NEVER invent, estimate or recall a
  figure yourself - if a tool didn't give it to you, say you don't have it.
- Money is in Indian rupees (₹); quantities are in strips (a strip is one pack).
- The pharmacy sells through two RETAIL branches (Shivaji Chowk, Hospet Road)
  and a B2B / WHOLESALE channel. Wholesale runs on thin margins by nature - a
  low B2B margin is normal, not a mistake.
- Lead with the answer, then one or two concrete, money-aware actions
  ("this is costing ~₹X, do Y"). Prioritise by rupee impact, not by percentage
  alone. Be brief and specific.
- Answer in English first, then a one- or two-line Kannada (ಕನ್ನಡ) summary.
- If a question needs a product's exact name, use search_products first.
- If the question gives a phone/mobile number, call get_patient_history with
  it and list what that patient bought (with dates and amounts).
- If a question names a PERSON by name (not a phone number) - e.g. "tell me
  about Abhishek" - call identify_person FIRST, before assuming who they are:
    - kind='employee': follow up with get_employee_performance() for their
      detail. If the question asks for "minute detail" (or targets,
      performance chart, earned amount, leave, attendance, absences), ALSO
      call BOTH get_employee_targets AND get_employee_attendance with their
      name and integrate all three into one answer - targets/incentive data
      and leave/absence patterns are not in get_employee_performance at all.
      Each of those two tools may have a different data source and a
      different (explicitly stated) date range - state the source's own
      date range when citing its numbers, never assume it matches the
      reckoner's own history or "the current month". If either returns
      available=False, tell the owner exactly what to do (export from
      shubhadahealth.com and drop the file / pull up that employee's
      Attendance Transaction report live), then ask again - do not fall
      back to reckoner figures as a substitute for either.
    - kind='customer_candidates' with exactly one candidate: follow up with
      get_patient_history using that candidate's mobile number.
    - kind='customer_candidates' with several candidates: do NOT guess which
      one - list the candidates (name, approx spend) in your answer and ask
      the owner to confirm which one before pulling full history.
    - kind='not_found': say so plainly, don't invent a match.
- If a question asks about a product's sale HISTORY/transactions (not just
  totals) - e.g. "who bought X", "sales history of X" - call
  get_product_patient_history and show the buyers (name, mobile, date, qty,
  amount) alongside any aggregate figures from get_product.
"""

MAX_STEPS = 6  # tool-call rounds before we force a final answer


# ---------------------------------------------------------------------------
# Vertex / Gemini integration (the only part that talks to Google).
# Isolated on purpose: swapping providers or going local = edit this function.
# ---------------------------------------------------------------------------
def _gemini_answer(question, verbose=False):
    from google import genai
    from google.genai import types

    project = os.environ.get('VERTEX_PROJECT')
    location = os.environ.get('VERTEX_LOCATION', 'asia-south1')
    model = os.environ.get('GEMINI_MODEL', 'gemini-2.5-pro')
    if not project:
        raise RuntimeError('Set VERTEX_PROJECT (your Google Cloud project id).')

    client = genai.Client(vertexai=True, project=project, location=location)
    tools = [types.Tool(function_declarations=[
        types.FunctionDeclaration(name=t['name'], description=t['description'],
                                  parameters=t['parameters'])
        for t in T.TOOLS])]
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT, tools=tools, temperature=0.2)

    contents = [types.Content(role='user',
                              parts=[types.Part.from_text(T.scrub_question(question))])]
    for _ in range(MAX_STEPS):
        resp = client.models.generate_content(model=model, contents=contents, config=config)
        parts = resp.candidates[0].content.parts
        calls = [p.function_call for p in parts if getattr(p, 'function_call', None)]
        if not calls:
            return resp.text
        contents.append(resp.candidates[0].content)  # record the model's turn
        for fc in calls:
            args = dict(fc.args) if fc.args else {}
            if verbose:
                print(f'  → tool: {fc.name}({json.dumps(args, default=str)})')
            result = T.run_tool(fc.name, args)
            contents.append(types.Content(role='user', parts=[
                types.Part.from_function_response(name=fc.name, response={'result': result})]))
    # ran out of steps - ask for a final answer with no more tools
    resp = client.models.generate_content(
        model=model, contents=contents,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, temperature=0.2))
    return resp.text


# ---------------------------------------------------------------------------
# OpenRouter integration - OpenRouter exposes an OpenAI-compatible API, so the
# official `openai` SDK is pointed at OpenRouter's base_url instead of
# OpenAI's. Default backend.
# ---------------------------------------------------------------------------
def _openrouter_answer(question, verbose=False):
    from openai import APIStatusError, OpenAI

    api_key = os.environ.get('OPENROUTER_API_KEY')
    if not api_key:
        raise RuntimeError('Set OPENROUTER_API_KEY (get one at https://openrouter.ai/keys).')
    # Two-tier routing: a fast/cheap model drives the tool-calling loop
    # ("which tool, what args" is mechanical, doesn't need a strong model),
    # and a stronger reasoning model writes the actual final answer (the
    # money-aware recommendation + Kannada summary) once the data is in hand.
    fast_model = os.environ.get('OPENROUTER_MODEL_FAST', 'google/gemini-3.1-flash-lite')
    # google/gemini-3.1-pro-preview (the only "3.1 pro" tier OpenRouter has -
    # there's no non-preview release yet) was found to silently return empty
    # content for some tool-result payloads (confirmed reproducible: certain
    # combinations of get_employee_performance's sections, finish_reason
    # "stop", no refusal, just nothing - a provider/model-side issue, not a
    # bug here). google/gemini-2.5-pro (GA, stable) handles the identical
    # payload correctly, so it's the default until 3.1 pro leaves preview.
    reasoning_model = os.environ.get('OPENROUTER_MODEL_REASONING', 'google/gemini-2.5-pro')

    client = OpenAI(
        base_url='https://openrouter.ai/api/v1', api_key=api_key,
        default_headers={'HTTP-Referer': 'https://github.com/subbu-h21/shubhada_forecasting',
                         'X-Title': 'Pharmacy Reckoner - Ask'})
    tools = [{'type': 'function', 'function': {
        'name': t['name'], 'description': t['description'], 'parameters': t['parameters']}}
        for t in T.TOOLS]
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': T.scrub_question(question)},
    ]

    def complete(model, with_tools):
        kwargs = {'model': model, 'messages': messages}
        if with_tools:
            kwargs['tools'] = tools
        try:
            return client.chat.completions.create(**kwargs)
        except APIStatusError as e:
            detail = e.body if getattr(e, 'body', None) is not None else str(e)
            raise RuntimeError(f'OpenRouter API error {e.status_code}: {detail}') from None

    for _ in range(MAX_STEPS):
        resp = complete(fast_model, with_tools=True)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            break  # fast model has everything it needs; hand off for the actual answer
        # Record the model's turn (incl. its tool_calls) in the wire format,
        # not a raw SDK object dump - keeps only the fields the API expects
        # back on the next call.
        assistant_msg = {'role': 'assistant', 'content': msg.content,
                         'tool_calls': [{'id': tc.id, 'type': 'function',
                                        'function': {'name': tc.function.name,
                                                    'arguments': tc.function.arguments}}
                                       for tc in msg.tool_calls]}
        messages.append(assistant_msg)
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or '{}')
            except json.JSONDecodeError:
                args = {}
            if verbose:
                print(f'  → tool [{fast_model}]: {name}({json.dumps(args, default=str)})')
            result = T.run_tool(name, args)
            messages.append({'role': 'tool', 'tool_call_id': tc.id,
                             'content': json.dumps(result, default=str)})
    # Final answer: the reasoning model synthesizes the actual response from
    # whatever the fast model already gathered above - no tools needed here,
    # the data's already in hand. Also the fallback if MAX_STEPS ran out.
    if verbose:
        print(f'  → answering with [{reasoning_model}]')
    resp = complete(reasoning_model, with_tools=False)
    answer = resp.choices[0].message.content
    if answer:
        return answer
    # Some models occasionally return empty content with finish_reason=stop
    # and no refusal (a provider-side quirk, seen and confirmed reproducible
    # with a "-preview" model) - rather than show the user a blank/"(no
    # answer)" response, fall back to the fast model for one attempt, which
    # has already proven able to answer directly when it skips tool calls.
    if verbose:
        print(f'  → [{reasoning_model}] returned empty, retrying with [{fast_model}]')
    resp = complete(fast_model, with_tools=False)
    answer = resp.choices[0].message.content
    return answer or "Sorry, I couldn't generate an answer for that - try rephrasing the question."


def ask(question, verbose=False):
    backend = os.environ.get('ASK_BACKEND', 'openrouter')
    if backend == 'openrouter':
        return _openrouter_answer(question, verbose=verbose)
    if backend == 'vertex':
        return _gemini_answer(question, verbose=verbose)
    raise RuntimeError(f"Unknown ASK_BACKEND '{backend}' - use 'openrouter' or 'vertex'.")


# ---------------------------------------------------------------------------
# Offline modes - work with no Vertex account, so you can check privacy first
# ---------------------------------------------------------------------------
def dry_run(question):
    print('=' * 68)
    print('DRY RUN - nothing is sent to any model provider. This shows what a')
    print('live call WOULD expose, so you can see the privacy boundary yourself.')
    print('=' * 68)
    print('\n1) Your question, exactly as it would be sent (no scrubbing applied -')
    print('   see scrub_question\'s docstring for why):')
    print('   ', T.scrub_question(question))
    print('\n2) Tools the model may call (names + what they return):')
    for t in T.TOOLS:
        one_line = ' '.join(t['description'].split())[:90]
        flag = '  [SENDS REAL PATIENT IDENTITY]' if t['name'] in T.PII_ALLOWED_TOOLS else ''
        print(f'   - {t["name"]}: {one_line}{flag}')
    print('\n3) Example of what actually leaves the machine (get_overview output):')
    sample = T.get_overview()
    print(textwrap.indent(json.dumps(sample, indent=2, ensure_ascii=False, default=str), '   '))
    print('\n4) Patient-data check on that output:', _pii_verdict(sample))
    print('\n5) Customer identity example (get_top_customers output) - each')
    print('   customer is only ever a one-way pseudonymous code, never their real mobile number:')
    cust_sample = T.get_top_customers(n=3, by='spend')
    print(textwrap.indent(json.dumps(cust_sample, indent=2, ensure_ascii=False, default=str), '   '))
    print('\n6) Patient-data check on that output:', _pii_verdict(cust_sample))
    print('\nEvery tool above stays inside that boundary EXCEPT the two marked')
    print('[SENDS REAL PATIENT IDENTITY] in section 2 (get_patient_history,')
    print('get_product_patient_history) - by explicit owner request, those send a')
    print('real name, mobile number, and purchase history to the model. Not run')
    print('here to avoid printing a real patient\'s data in a generic demo.')


def _pii_verdict(payload):
    try:
        T._guard_no_pii(payload)
        return 'PASS - no patient fields present'
    except ValueError as e:
        return f'FAIL - {e}'


def selftest():
    print('Running every tool and checking none returns patient data...\n')
    checks = [
        ('get_overview', {}), ('get_channel_profit', {}),
        ('search_products', {'query': 'tab'}), ('get_top', {'kind': 'dead_stock', 'n': 5}),
        ('get_top', {'kind': 'top_distributors', 'n': 5}), ('get_forecast', {}),
        ('get_purchase_issues', {'n': 5}),
        ('get_employee_performance', {}),
        ('get_employee_targets', {}),
        ('get_employee_attendance', {}),
        ('get_top_customers', {'n': 5, 'by': 'spend'}),
        ('get_customer_trends', {'churn_limit': 5}),
        ('query_sales', {'group_by': ['branch'], 'metric': 'revenue'}),
    ]
    ok = True
    for name, args in checks:
        out = T.run_tool(name, args)
        verdict = _pii_verdict(out)
        size = len(json.dumps(out, default=str))
        ok = ok and verdict.startswith('PASS')
        print(f'  {name:20} {verdict:34} ({size:,} bytes)')
    # spot-check a real product end to end
    first = T.search_products('tab', 1)['matches']
    if first:
        p = T.run_tool('get_product', {'name': first[0]})
        print(f'\n  get_product("{first[0]}") -> margin {p.get("gross_margin_pct")}%, '
              f'sold {p.get("total_sold_strips")} strips  [{_pii_verdict(p)}]')
    print('\nAll tools passed the patient-data check.' if ok else '\nSOME TOOLS LEAKED - fix before going live.')

    print('\nDeliberately EXEMPTED from the check above (real patient identity by')
    print('explicit owner request - see ask_tools.py docstring):')
    for name in sorted(T.PII_ALLOWED_TOOLS):
        print(f'  - {name}')
    smoke = T.run_tool('get_patient_history', {'mobile': '0000000000'})
    print(f'  structural smoke test: get_patient_history("0000000000") -> found={smoke.get("found")}')


def main(argv):
    args = argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        return
    if args[0] == '--tools':
        for t in T.TOOLS:
            print(f'{t["name"]}\n    {" ".join(t["description"].split())}\n')
        return
    if args[0] == '--selftest':
        selftest()
        return
    verbose = '--verbose' in args or '-v' in args
    args = [a for a in args if a not in ('--verbose', '-v')]
    if args[0] == '--dry-run':
        dry_run(' '.join(args[1:]))
        return
    question = ' '.join(args)
    backend = os.environ.get('ASK_BACKEND', 'openrouter')
    try:
        print(ask(question, verbose=verbose))
    except ImportError:
        pkg = 'openai' if backend == 'openrouter' else 'google-genai'
        print(f'The {pkg} package is not installed yet (needed for ASK_BACKEND={backend}).\n'
              f'Run:  pip install {pkg}\n'
              'Meanwhile try:  python ask.py --dry-run "%s"' % question)
    except Exception as e:
        hint = 'Check OPENROUTER_API_KEY.' if backend == 'openrouter' else 'Check VERTEX_PROJECT / credentials.'
        print(f'Could not reach the model ({type(e).__name__}: {e}).\n'
              f'{hint} Or use --dry-run to test locally.')


if __name__ == '__main__':
    main(sys.argv)

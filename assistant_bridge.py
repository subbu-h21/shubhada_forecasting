"""The Shop Assistant's door into the reckoner (project-15 shop assistant, 31 Oct 2026).

The assistant - the WhatsApp "Jarvis" for the business - runs as its own server and treats the reckoner as one of
its connectors: sales, purchases, stock, forecasts, employees, customers, MRP totals and the MRP basket all stay
here, and the assistant reaches them over a small HTTP API instead of importing this code:

    GET  /api/assistant/ping                         is the reckoner up, and does the key match
    GET  /api/assistant/tools                        the reckoner's tools (OpenAI function format) + which carry patient data
    GET  /api/assistant/context                      the reckoner's rules for its tools, today's date line and saved notes
    POST /api/assistant/run     {name, args, owner}  run one tool; `owner` keys the MRP basket
    POST /api/assistant/basket  {action, owner, text}
                                 action 'classify': is this a basket item/command? (changes nothing)
                                 action 'interpret': act on it -> the basket's reply, or null

Who may call it: only a program on THIS PC (127.0.0.1 / ::1) that sends the shared key in X-Assistant-Key -
ASSISTANT_BRIDGE_KEY in both projects' .env files (at least 32 characters). Unset, every route answers 503, so
the reckoner behaves exactly as before. The tools are the same read-only ones the Ask page uses (plus save_note,
which only adds to the reckoner's own notes).
"""
import os
import secrets

import ask
import ask_tools as T
import mrp_basket

KEY_NAME = 'ASSISTANT_BRIDGE_KEY'
MIN_KEY_CHARS = 32
LOCAL_ADDRS = ('127.0.0.1', '::1', '::ffff:127.0.0.1')


def configured():
    return len((os.environ.get(KEY_NAME) or '').strip()) >= MIN_KEY_CHARS


def authorised(remote_addr, given_key):
    """-> (ok, http_status, message)."""
    if not configured():
        return False, 503, 'the assistant bridge is off (ASSISTANT_BRIDGE_KEY is not set in the reckoner .env)'
    if remote_addr not in LOCAL_ADDRS:
        return False, 403, 'the assistant bridge only answers programs on this PC'
    if not given_key or not secrets.compare_digest(str(given_key), os.environ[KEY_NAME].strip()):
        return False, 403, 'wrong assistant key'
    return True, 200, ''


def tool_specs():
    return list(T.TOOLS) + list(mrp_basket.tool_specs())


def describe():
    # ask_tools.TOOLS entries also carry the Python function itself - only the three fields a model needs go out
    return {'tools': [{k: t[k] for k in ('name', 'description', 'parameters')} for t in tool_specs()],
            'pii_tools': sorted(T.PII_ALLOWED_TOOLS),
            'basket_tools': sorted(mrp_basket.TOOL_NAMES)}


def context():
    """The reckoner's half of the system prompt: 'prompt' = its rules for its own tools plus today's date and data
    coverage; 'notes' = the notes saved from past conversations, as one block. The assistant adds who it is and the
    other connectors between the two, so the notes stay last - exactly where the Ask brain always put them."""
    notes = T.get_notes(n=30).get('notes') or []
    block = ''
    if notes:
        block = ('\n\nNotes saved from previous conversations (may be outdated - verify against a tool before '
                 'relying on one):\n' + '\n'.join(f"- [{n['timestamp']}] {n['note']}" for n in notes))
    return {'prompt': ask.SYSTEM_PROMPT + ask._date_context(), 'notes': block}


def run(name, args, owner=None):
    names = {t['name'] for t in tool_specs()}
    if name not in names:
        return {'error': f'the reckoner has no tool {name}'}
    if not isinstance(args, dict):
        args = {}
    if mrp_basket.is_basket_tool(name):
        if not owner:
            return {'error': 'the basket needs an owner key'}
        token = mrp_basket.OWNER.set(str(owner))
        try:
            return mrp_basket.run(name, args)
        finally:
            mrp_basket.OWNER.reset(token)
    return T.run_tool(name, args)


def basket(action, owner, text):
    if not owner:
        return {'error': 'the basket needs an owner key'}
    if action == 'classify':
        return {'kind': mrp_basket.classify(str(owner), str(text or ''))}
    if action == 'interpret':
        return {'reply': mrp_basket.interpret(str(owner), str(text or ''))}
    return {'error': f'unknown basket action {action}'}

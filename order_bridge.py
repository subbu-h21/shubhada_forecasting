"""Dual mode: lets the Ask page answer ORDER questions as well as sales / forecast ones.

The order-automation dashboard (a separate project, separate process, separate data)
exposes a small read-only HTTP bridge: GET /ask-bridge/tools and POST /ask-bridge/call.
This module fetches its tool list, renames each tool `orders_<name>` so the model can
tell the two systems apart, and forwards calls to it. ask.py merges these tools into the
normal tool loop and adds a short DUAL MODE section to the system prompt - the model
itself decides, per question, which system (or both) to use.

Switched on by two settings (environment or the local .env):
    ORDER_ASK_URL   e.g. http://127.0.0.1:8000   (the order dashboard, no trailing path)
    ORDER_ASK_KEY   the same long secret as ASK_BRIDGE_KEY in the order project's .env

Failure is soft by design. Not configured, the order dashboard being off, a wrong key, a
slow reply - every one of these just means "no orders_* tools right now": the page keeps
answering sales and forecast questions exactly as before, and a call that fails mid-answer
comes back to the model as an error it can explain. Nothing here raises into ask.py.

Nothing is written anywhere and the key is never logged or placed in a tool result.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

PREFIX = 'orders_'
MIN_KEY_CHARS = 32
TOOLS_TTL_S = 300          # how long a fetched tool list is trusted
FAILED_TTL_S = 30          # after a failed fetch, wait this long before trying again
LIST_TIMEOUT_S = 5
CALL_TIMEOUT_S = 25
MAX_REPLY_BYTES = 2_000_000

_lock = threading.Lock()
_cache = {'at': 0.0, 'ok': False, 'tools': [], 'context': ''}


def _settings():
    """(base_url, key), or ('', '') when dual mode is not configured."""
    url = (os.environ.get('ORDER_ASK_URL') or '').strip().rstrip('/')
    key = (os.environ.get('ORDER_ASK_KEY') or '').strip()
    if not url.startswith(('http://', 'https://')) or len(key) < MIN_KEY_CHARS:
        return '', ''
    return url, key


def _http(method, path, payload=None, timeout=LIST_TIMEOUT_S):
    url, key = _settings()
    if not url:
        raise RuntimeError('not configured')
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url + path, data=data, method=method,
                                 headers={'X-Ask-Bridge-Key': key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_REPLY_BYTES + 1)
    if len(raw) > MAX_REPLY_BYTES:
        raise RuntimeError('reply too large')
    return json.loads(raw.decode('utf-8'))


def _refresh(force=False):
    with _lock:
        age = time.monotonic() - _cache['at']
        if not force and _cache['at'] and age < (TOOLS_TTL_S if _cache['ok'] else FAILED_TTL_S):
            return
        _cache['at'] = time.monotonic()
        try:
            data = _http('GET', '/ask-bridge/tools')
            tools = []
            for t in data.get('tools') or []:
                if not (isinstance(t, dict) and t.get('name') and isinstance(t.get('parameters'), dict)):
                    continue
                tools.append({
                    'name': PREFIX + str(t['name']),
                    'description': '[ORDER TRACKING] ' + str(t.get('description') or ''),
                    'parameters': t['parameters'],
                })
            _cache.update(ok=bool(tools), tools=tools, context=str(data.get('context') or ''))
        except Exception:  # noqa: BLE001 - soft failure: just no order tools for a while
            _cache.update(ok=False, tools=[], context='')


def tool_specs():
    """Order tools in the same shape as ask_tools.TOOLS entries (name, description, parameters).
    Empty when dual mode is off or the order dashboard cannot be reached."""
    if not _settings()[0]:
        return []
    _refresh()
    return list(_cache['tools'])


def context():
    """Facts about the order system (stages, what OVERDUE means ...) so the model can read the results."""
    return _cache['context'] if _cache['ok'] else ''


def is_order_tool(name):
    return isinstance(name, str) and name.startswith(PREFIX)


def run(name, args):
    """Forward one orders_* call. Always returns a dict; failures are errors the model can explain."""
    if not is_order_tool(name):
        return {'error': f'tool {name} is not an order-tracking tool'}
    if not _settings()[0]:
        return {'error': 'Order tracking is not connected to this page.'}
    payload = {'name': name[len(PREFIX):], 'args': args if isinstance(args, dict) else {}}
    try:
        reply = _http('POST', '/ask-bridge/call', payload, timeout=CALL_TIMEOUT_S)
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            return {'error': 'Order tracking refused this page (the shared key is wrong or the bridge is switched off).'}
        if e.code == 429:
            return {'error': 'Order tracking is rate-limiting this page right now - try again in a few minutes.'}
        return {'error': f'Order tracking returned an error (HTTP {e.code}).'}
    except Exception as e:  # noqa: BLE001
        return {'error': f'Order tracking is not reachable right now ({type(e).__name__}).'}
    result = reply.get('result') if isinstance(reply, dict) else None
    return result if isinstance(result, dict) else {'error': 'Order tracking sent a reply this page could not read.'}


def reset_cache():
    """For tests."""
    with _lock:
        _cache.update(at=0.0, ok=False, tools=[], context='')

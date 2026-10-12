"""Encrypted vault for website logins the reckoner uses on its own - first,
the shubhadahealth.com login, so attendance punches and Performance Charts can
be fetched instead of downloaded and uploaded by hand (owner, 2026-10-12:
"make a vault like in order automation, where I will keep login credentials").

Modelled on the order automation project's credential_store.py: Fernet
encryption under a key from the environment, write-then-replace saves, a
masked summary for the dashboard, and an unreadable file treated as "nothing
stored" rather than a crash.

What protects what, stated plainly: the encrypted file (.credentials.enc) and
its key (RECKONER_VAULT_KEY in .env) both live on this PC and are both kept
out of git. The encryption stops the password leaking through a copied file
or a backup; it does not stop someone who can use this PC's files. Never send
a password to the browser - only `summary()` is safe for that.

This reverses an earlier choice recorded in ask_tools.py ("no credential for
that site is stored anywhere in this codebase"), at the owner's request.
"""

import base64
import hashlib
import json
import os
import secrets
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).parent
STORE_PATH = ROOT / '.credentials.enc'
ENV_PATH = ROOT / '.env'
KEY_NAME = 'RECKONER_VAULT_KEY'

# The logins the vault holds, with how the dashboard names them.
PLATFORMS = {'shubhadahealth': 'shubhadahealth.com'}

_lock = threading.Lock()


def _key():
    """The vault key from the environment (.env). Made once, on the first
    save, and appended to .env - so it exists only on this PC."""
    key = os.environ.get(KEY_NAME)
    if not key and ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
            if line.strip().startswith(KEY_NAME + '='):
                key = line.split('=', 1)[1].strip()
    if not key:
        key = secrets.token_urlsafe(32)
        with open(ENV_PATH, 'a', encoding='utf-8') as f:
            f.write(f'\n# Key for the reckoner\'s login vault (.credentials.enc). Losing it means re-entering logins.\n'
                    f'{KEY_NAME}={key}\n')
    os.environ[KEY_NAME] = key
    return key


def _fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(_key().encode('utf-8')).digest()))


def _load_all():
    if not STORE_PATH.exists():
        return {}
    try:
        return json.loads(_fernet().decrypt(STORE_PATH.read_bytes()).decode('utf-8'))
    except (InvalidToken, json.JSONDecodeError, OSError, ValueError):
        return {}


def _save_all(data):
    token = _fernet().encrypt(json.dumps(data).encode('utf-8'))
    tmp = STORE_PATH.with_suffix('.tmp')
    tmp.write_bytes(token)
    tmp.replace(STORE_PATH)


def get_credentials(platform):
    """{'username', 'password'} or None. Backend only - never to a client."""
    entry = _load_all().get(platform) or {}
    if not entry.get('username'):
        return None
    return {'username': entry['username'], 'password': entry.get('password', '')}


def set_credentials(platform, username, password):
    if platform not in PLATFORMS:
        return f'Unknown login "{platform}".'
    username, password = str(username or '').strip(), str(password or '')
    if not username or not password:
        return 'Enter both the username and the password.'
    if len(username) > 200 or len(password) > 200:
        return 'That username or password is too long.'
    with _lock:
        data = _load_all()
        data[platform] = {'username': username, 'password': password}
        _save_all(data)
    return None


def clear_credentials(platform):
    with _lock:
        data = _load_all()
        if data.pop(platform, None) is not None:
            _save_all(data)


def _mask(username):
    if len(username) <= 3:
        return '*' * len(username)
    return f'{username[:2]}{"*" * (len(username) - 3)}{username[-1]}'


def summary():
    """Safe for the dashboard: which logins are stored and a masked username."""
    data = _load_all()
    out = {}
    for platform, label in PLATFORMS.items():
        user = (data.get(platform) or {}).get('username', '')
        out[platform] = {'label': label, 'configured': bool(user), 'username_masked': _mask(user) if user else ''}
    return out

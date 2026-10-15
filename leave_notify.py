"""WhatsApp messages for the shop's leave app (project 13), sent through the
reckoner's own linked WhatsApp Web profile (.whatsapp_profile).

The leave app (Node, same PC) posts short texts here - login codes, approval
codes, "new leave request" alerts, decisions - and this module queues them
and sends them one by one with salary_whatsapp's proven driver.

Guards (anyone on the shop Wi-Fi can reach the reckoner):
  - only from this PC (127.0.0.1 / ::1),
  - only with the shared secret LEAVE_NOTIFY_TOKEN (made once, kept in .env;
    the leave app's own .env holds the same value),
  - plain text up to MAX_CHARS, Indian mobile numbers only, at most
    MAX_PER_HOUR messages an hour.

One Chrome profile can be open in only one process at a time, so PROFILE_LOCK
is shared with the salary-slip sender: while leave messages are going out the
salary sender says "busy", and the other way round leave messages wait.
The window stays open IDLE_SECONDS after the last message so a burst (a code,
then an approval) doesn't pay WhatsApp's ~45 s start-up each time.

LEAVE_NOTIFY_DRY_RUN=1 in .env logs instead of sending (for testing).
"""
import hmac
import itertools
import logging
import os
import queue
import re
import secrets
import threading
import time
import traceback
from pathlib import Path

from flask import Blueprint, jsonify, request

ROOT = Path(__file__).parent
ENV_PATH = ROOT / '.env'
TOKEN_NAME = 'LEAVE_NOTIFY_TOKEN'
MAX_CHARS = 1000
MAX_PER_HOUR = 60
IDLE_SECONDS = 300
LINK_WAIT_SECONDS = 120

logger = logging.getLogger('reckoner_server')
bp = Blueprint('leave_notify', __name__)

PROFILE_LOCK = threading.Lock()
_queue = queue.Queue()
_ids = itertools.count(1)
_jobs = {}            # id -> {'status': queued|sending|sent|failed, 'reason': str, 'at': float}
_jobs_lock = threading.Lock()
_sent_times = []      # for the hourly limit
_worker = None
_state = {'open': False, 'stage': 'idle'}
_release = threading.Event()  # the salary sender wants the profile: close when idle
_settings = {}        # set by init(): profile_dir, busy (callable), log (callable)


def _env(name):
    value = os.environ.get(name)
    if value is None and ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
            if line.strip().startswith(name + '='):
                value = line.split('=', 1)[1].strip()
    return value


def token(create=True):
    """The shared secret; made once and appended to .env."""
    t = _env(TOKEN_NAME)
    if not t and create:
        t = secrets.token_urlsafe(32)
        with open(ENV_PATH, 'a', encoding='utf-8') as f:
            f.write('\n# Shared secret for the leave app (project 13) to send WhatsApp messages through the reckoner.\n'
                    f'{TOKEN_NAME}={t}\n')
    if t:
        os.environ[TOKEN_NAME] = t
    return t


def dry_run():
    return (_env('LEAVE_NOTIFY_DRY_RUN') or '').strip() in ('1', 'true', 'yes')


def normalise_phone(number):
    """Digits only; India's 91 added to a 10-digit number (same rule as the salary slips)."""
    digits = re.sub(r'[^0-9]', '', str(number or ''))
    if len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    if len(digits) == 10:
        digits = '91' + digits
    return digits


def _mask(number):
    return number[:4] + '*' * max(0, len(number) - 6) + number[-2:]


def init(profile_dir, busy, log):
    """profile_dir: the reckoner's WhatsApp profile; busy(): True while the
    salary sender runs; log(msg): the server's logger."""
    _settings.update(profile_dir=Path(profile_dir), busy=busy, log=log)
    token()


def _set(job_id, **kw):
    with _jobs_lock:
        _jobs[job_id].update(kw)


def _ensure_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_run, name='leave-notify', daemon=True)
        _worker.start()


def _run():
    log = _settings['log']
    while True:
        job = _queue.get()
        # The salary sender holds the profile: wait for it (messages stay queued).
        while _settings['busy']() or not PROFILE_LOCK.acquire(timeout=5):
            _state['stage'] = 'waiting for the salary sender'
            time.sleep(3)
        try:
            if dry_run():
                _send_dry(job, log)
                continue
            _send_batch(job, log)
        except Exception:
            logger.error('Leave WhatsApp worker failed:\n%s', traceback.format_exc())
        finally:
            _state.update(open=False, stage='idle')
            PROFILE_LOCK.release()


def _send_dry(first, log):
    job = first
    while job:
        log(f"[leave-whatsapp] DRY RUN to {_mask(job['to'])} ({job['kind']}, {len(job['text'])} chars)")
        _set(job['id'], status='sent', reason='dry run - nothing sent', at=time.time())
        try:
            job = _queue.get_nowait()
        except queue.Empty:
            job = None


def _send_batch(first, log):
    import salary_whatsapp as wa
    from playwright.sync_api import sync_playwright
    job = first
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(_settings['profile_dir']), channel='chrome', headless=False,
            args=['--start-minimized'], no_viewport=True)
        _state.update(open=True, stage='opening WhatsApp')
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(wa.WEB_URL, wait_until='domcontentloaded', timeout=90000)
            # Same caution as the salary sender: the QR screen appears late, so
            # watch for it before trusting the link. Never waits for a scan here -
            # linking is done from Salary > Slips.
            deadline = time.time() + 40
            while time.time() < deadline:
                page.wait_for_timeout(2000)
                if not wa.is_linked(page):
                    raise RuntimeError('WhatsApp is not linked - link it from People > Salary > Slips first.')
            log('[leave-whatsapp] WhatsApp open')
            while job:
                _state['stage'] = f"sending a {job['kind']} message"
                _set(job['id'], status='sending')
                try:
                    if not wa.open_chat(page, job['to'], ''):
                        _set(job['id'], status='failed', reason='chat did not open (number not on WhatsApp?)', at=time.time())
                    else:
                        out = wa.send_message(page, job['text'], dry_run=False)
                        ok = bool(out.get('sent'))
                        _set(job['id'], status='sent' if ok else 'failed', reason=out.get('reason') or '', at=time.time())
                    log(f"[leave-whatsapp] {job['kind']} to {_mask(job['to'])}: {_jobs[job['id']]['status']}")
                except Exception as exc:
                    logger.error('Leave WhatsApp send failed:\n%s', traceback.format_exc())
                    _set(job['id'], status='failed', reason=str(exc)[:160], at=time.time())
                # Stay open a while for the next message; leave early if the
                # salary sender wants the profile.
                job = None
                idle_until = time.time() + IDLE_SECONDS
                _release.clear()
                while time.time() < idle_until and not _release.is_set():
                    try:
                        job = _queue.get(timeout=2)
                        break
                    except queue.Empty:
                        _state['stage'] = 'open, waiting for messages'
        except Exception as exc:
            # Everything still queued fails fast so the leave app can fall back.
            reason = str(exc)[:160]
            for j in [job] + _drain():
                if j:
                    _set(j['id'], status='failed', reason=reason, at=time.time())
            log(f'[leave-whatsapp] stopped: {reason}')
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def _drain():
    out = []
    while True:
        try:
            out.append(_queue.get_nowait())
        except queue.Empty:
            return out


def _authorised():
    if request.remote_addr not in ('127.0.0.1', '::1'):
        return False
    t = token(create=False) or ''
    given = request.headers.get('X-Notify-Token') or ''
    return bool(t) and hmac.compare_digest(t, given)


@bp.route('/api/notify/whatsapp', methods=['POST'])
def api_notify_send():
    if not _authorised():
        return jsonify({'error': 'not allowed'}), 403
    b = request.get_json(silent=True) or {}
    to = normalise_phone(b.get('to'))
    text = str(b.get('text') or '').strip()
    kind = re.sub(r'[^a-z_]', '', str(b.get('kind') or 'message'))[:30] or 'message'
    if not (len(to) == 12 and to.startswith('91')):
        return jsonify({'error': 'to must be a 10-digit Indian mobile number'}), 400
    if not text or len(text) > MAX_CHARS:
        return jsonify({'error': f'text must be 1-{MAX_CHARS} characters'}), 400
    now = time.time()
    with _jobs_lock:
        _sent_times[:] = [t for t in _sent_times if now - t < 3600]
        if len(_sent_times) >= MAX_PER_HOUR:
            return jsonify({'error': 'too many messages this hour'}), 429
        _sent_times.append(now)
        job_id = next(_ids)
        _jobs[job_id] = {'status': 'queued', 'reason': '', 'at': now}
        for old in [k for k, v in _jobs.items() if now - v['at'] > 86400]:
            del _jobs[old]
    _queue.put({'id': job_id, 'to': to, 'text': text, 'kind': kind})
    _ensure_worker()
    _settings['log'](f'[leave-whatsapp] queued {kind} for {_mask(to)}')
    return jsonify({'queued': True, 'id': job_id, 'dryRun': dry_run()})


@bp.route('/api/notify/whatsapp/<int:job_id>')
def api_notify_status(job_id):
    if not _authorised():
        return jsonify({'error': 'not allowed'}), 403
    with _jobs_lock:
        job = dict(_jobs.get(job_id) or {})
    if not job:
        return jsonify({'error': 'unknown message'}), 404
    return jsonify({'id': job_id, 'status': job['status'], 'reason': job['reason'],
                    'stage': _state['stage'], 'queued': _queue.qsize()})


def request_release():
    """The salary sender wants the profile: close WhatsApp after the message
    being sent now (if any) instead of waiting out the idle time."""
    _release.set()

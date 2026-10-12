r"""WhatsApp Web driver for sending salary slips - COPIED from the order
automation project (project-7-order-automation, file
order_automation_v2/whatsapp.py, copied 2026-10-12), whose selectors were
captured from the live WhatsApp Web DOM and whose safety checks were proven
live there.
Keep the two in step: if WhatsApp changes its page and that file is fixed,
copy the fix here.

The reckoner uses its OWN Chrome profile (.whatsapp_profile), linked by its
own QR scan. It must not share order automation's profile: only one process
can hold a Chrome profile at a time, and that project's seven browser flows
guard their shared profile against exactly that collision.

Everything below is that module unchanged, apart from this header.
"""
import time
import urllib.parse

from playwright.sync_api import Page


WEB_URL = "https://web.whatsapp.com"

# Stable handles, captured live. See the module docstring for why the
# composer's aria-label is deliberately NOT used.
_COMPOSE_BOX = '[data-testid="conversation-compose-box-input"]'
_COMPOSER = 'div[contenteditable="true"][aria-placeholder="Type a message"]'
_SEND_BUTTON = 'button[aria-label="Send"]'
# Conversation rows, for verifying a send actually happened.
#
# This was `div.message-out` and it matched NOTHING - written from memory,
# which is the one thing this project's selector rule forbids, and it cost the
# first real send its primary verification (it fell through to "the box
# emptied", a much weaker signal). Checked live 2026-09-20: WhatsApp's bubble
# classes are obfuscated and rotate (`x1n2onr6 xscbp6u`), and there is no
# usable outgoing marker - `[data-id^="true_"]` matched nothing either, and in
# a message-yourself chat nothing could distinguish in from out anyway.
#
# `[role="row"]` is what the conversation really exposes, so verification reads
# the last few rows' text and looks for the message. Roles are part of the
# accessibility contract rather than a build artefact, so they outlive a class
# rename.
_MESSAGE_ROW = '[role="row"]'
_ROWS_TO_CHECK = 6

_NOT_LINKED_MARKERS = ("scan the qr", "log into whatsapp", "link with phone number")


def is_linked(page: Page) -> bool:
    """False when WhatsApp Web is showing its QR screen instead of the app.

    Read off the page's own text rather than the URL: the QR screen lives at
    the same address as the app."""
    try:
        body = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    return not any(marker in body for marker in _NOT_LINKED_MARKERS)


def chat_url(number: str, text: str = "") -> str:
    url = f"{WEB_URL}/send?phone={number}"
    if text:
        url += "&text=" + urllib.parse.quote(text)
    return url


def open_chat(page: Page, number: str, text: str = "", timeout_ms: int = 90000,
              on_progress=None) -> bool:
    """Opens one contact's chat, optionally with `text` pre-filled.

    Returns False when WhatsApp Web is not linked - the caller should stop and
    ask for a fresh QR scan rather than driving a dead page.

    The long settle is not politeness. WhatsApp Web renders its shell well
    before the conversation exists, so anything that reads the composer too
    early sees nothing and concludes the text did not land - which is exactly
    the wrong conclusion this module was built to correct."""
    def log(message):
        if on_progress:
            on_progress(message)

    page.goto(chat_url(number, text), wait_until="domcontentloaded",
              timeout=timeout_ms)

    # Wait for the composer itself, not a fixed sleep - that is the only thing
    # that proves the chat is really open and ready to be typed into.
    deadline = time.time() + 45
    while time.time() < deadline:
        if not is_linked(page):
            log("WhatsApp Web is not linked any more - the QR needs scanning again.")
            return False
        try:
            if page.locator(_COMPOSER).count() > 0:
                page.wait_for_timeout(800)
                return True
        except Exception:
            pass
        page.wait_for_timeout(1000)

    if not is_linked(page):
        log("WhatsApp Web is not linked any more - the QR needs scanning again.")
        return False
    log(f"The chat with {number} did not finish opening in time.")
    return False


def read_composer(page: Page) -> str:
    """Whatever is actually sitting in the message box right now."""
    try:
        box = page.locator(_COMPOSER).first
        if box.count() == 0:
            return ""
        return (box.inner_text() or "").strip()
    except Exception:
        return ""


def clear_composer(page: Page) -> None:
    """Empties the message box through real key events.

    Lexical ignores DOM writes, so this selects all and deletes rather than
    assigning an empty value."""
    box = page.locator(_COMPOSER).first
    box.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.wait_for_timeout(300)


def type_message(page: Page, text: str, on_progress=None) -> bool:
    """Puts `text` in the composer and confirms it is really there.

    Multi-line matters: a bare Enter in this box SENDS. So newlines are typed
    as Shift+Enter, line by line. Getting this wrong would send the first line
    of the summary on its own and leave the rest in the box - one of the more
    obvious ways to make a mess of somebody's chat."""
    def log(message):
        if on_progress:
            on_progress(message)

    box = page.locator(_COMPOSER).first
    if box.count() == 0:
        log("The message box isn't there - the chat may not be open.")
        return False

    clear_composer(page)
    box.click()
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if index:
            # Shift+Enter is a newline; a plain Enter would SEND.
            page.keyboard.press("Shift+Enter")
        if line:
            page.keyboard.insert_text(line)
    page.wait_for_timeout(600)

    landed = read_composer(page)
    got, want = _squash(landed), _squash(text)
    if got != want:
        # Say WHERE it diverged, not just that the lengths differ. When this
        # first fired live, the log gave only "824 vs 742" and the real cause -
        # WhatsApp auto-numbering a list and doubling every "2." - took a
        # separate diagnostic run to find.
        where = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b),
                     min(len(got), len(want)))
        log(f"The message box does not match what was typed - not sending. "
            f"First difference at character {where}: box has "
            f"{got[max(0, where - 30):where + 40]!r}, should be "
            f"{want[max(0, where - 30):where + 40]!r}")
        return False
    return True


def _plain(text: str) -> str:
    """Whitespace-collapsed AND with WhatsApp's own markup characters removed.

    Use this when comparing against a SENT message. WhatsApp renders "*bold*"
    and "_italic_", so the delivered bubble's text carries no asterisks or
    underscores - checking for them found nothing, and the send verification
    silently fell back to the weaker "the composer emptied" signal every time
    (seen live 2026-09-20 on a report whose every heading was bold).

    NOT for the composer check: the composer holds the literal markup
    characters as typed, so comparing there must stay exact."""
    return (_squash(text).replace("*", "").replace("_", "").replace("~", ""))


def _squash(text: str) -> str:
    """Whitespace-insensitive comparison. The composer re-flows what is typed
    into it, so an exact string match would fail on formatting alone - but the
    words themselves must match."""
    return " ".join((text or "").split())


def send_message(page: Page, text: str, dry_run: bool = True,
                 on_progress=None) -> dict:
    """Sends `text` in the currently open chat. THE IRREVERSIBLE STEP.

    dry_run defaults to TRUE: it types the message, verifies the box holds
    exactly what was asked for, and STOPS - leaving the window open with the
    draft ready for a human to send. Sending for real has to be asked for.

    Guards, in order:
      1. The composer must contain exactly the reviewed text (whitespace
         aside). What gets sent is what was checked, or nothing is sent.
      2. The Send control must be VISIBLE. It only appears for a non-empty
         composer, so its absence means the text did not land.
      3. Success is read from the conversation - the text must show up as an
         outgoing message. The composer emptying is a second signal.

    Nothing after the click raises: failing to read the outcome must never be
    reported as failing to send, because a retry would message someone twice.
    """
    def log(message):
        if on_progress:
            on_progress(message)

    if not type_message(page, text, on_progress=on_progress):
        return {"sent": False, "reason": "message_did_not_land"}

    if dry_run:
        log(f"Draft ready - {len(text)} characters are in the box. "
            f"NOTHING HAS BEEN SENT; press Send in that window.")
        return {"sent": False, "dry_run": True, "chars": len(text),
                "drafted": True}

    button = page.locator(_SEND_BUTTON).first
    visible = False
    try:
        visible = button.count() > 0 and button.is_visible(timeout=5000)
    except Exception:
        visible = False
    if not visible:
        log("The Send button isn't showing, so nothing was sent. It only "
            "appears once the message box has text in it.")
        return {"sent": False, "reason": "no_send_button"}

    log(f"Sending {len(text)} characters - a WhatsApp message cannot be recalled.")
    button.click()

    # ---- nothing below may raise ----
    sent, reason = False, None
    try:
        # Match on a PREFIX of the message, not the whole thing: the row also
        # carries a timestamp and delivery ticks, and WhatsApp re-flows long
        # text, so an equality check would never pass.
        wanted = _plain(text)[:80]
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                rows = page.locator(_MESSAGE_ROW)
                count = rows.count()
                for i in range(max(0, count - _ROWS_TO_CHECK), count):
                    body = _plain(rows.nth(i).inner_text() or "")
                    if wanted and wanted in body:
                        sent, reason = True, "seen in the conversation"
                        break
                if sent:
                    break
            except Exception:
                pass
            page.wait_for_timeout(700)
        if not sent and not read_composer(page):
            sent = True
            reason = ("the message box emptied, but the sent message was "
                      "never seen in the conversation")
    except Exception:
        pass

    if not sent:
        log("Pressed Send but could not confirm the message went - CHECK "
            "WHATSAPP BEFORE RETRYING. It may well have gone through.")
    else:
        log(f"Sent ({reason}).")
    return {"sent": sent, "reason": reason, "chars": len(text)}

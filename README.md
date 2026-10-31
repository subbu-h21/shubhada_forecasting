# Pharmacy Ready Reckoner

Monthly reckoner for a pharmacy's Sale/Purchase XLS exports: demand forecast,
reorder signals, over/under-purchased tracking, gross profit & margin,
purchase entry error checks (PTR vs MRP, MRP consistency), supplier scheme
and discount consistency, and a branch-wise breakdown. Includes a mobile
dashboard so reports can be checked from a phone.

## Setup

1. Install dependencies: `pip install pandas openpyxl flask waitress numpy playwright==1.61.0 cryptography==50.0.1` (Playwright is only for sending salary slips by WhatsApp; it uses the installed Google Chrome)
2. Drop your first month's Sale and Purchase `.xlsx` files into `data/raw/`
3. Run `python run_reckoner.py` — this ingests the files, builds
   `data/processed/*.csv` (permanent history) and writes
   `reports/<month>/Monthly Reckoner Report.xlsx`

## Every following month

Drop the new month's Sale + Purchase files into `data/raw/` and run
`run_reckoner.py` again (or upload them from the mobile dashboard, below).
It only ingests files it hasn't seen, and always recomputes every report
from the *full* history to date.

## Setting up on a new PC (keeping your existing history)

1. **Install prerequisites** — Python 3 from [python.org](https://python.org)
   (check "Add python.exe to PATH" during install) and Git for Windows from
   [git-scm.com](https://git-scm.com).

2. **Clone the repo:**
   ```bash
   git clone https://github.com/subbu-h21/shubhada_forecasting.git
   cd shubhada_forecasting
   ```

3. **Install the Python packages:**
   ```bash
   pip install pandas openpyxl flask waitress numpy playwright==1.61.0 cryptography==50.0.1
   ```

4. **Bring your data over.** `data/raw/` and `data/processed/` are
   deliberately not in GitHub (see Security notes below), so they don't come
   with `git clone`. Copy these two folders from the old PC to the new one
   — a USB drive or a cloud folder (OneDrive/Google Drive) works, they
   total under 50MB — and drop them into the same spot inside the cloned
   folder, replacing the empty placeholders:
   ```
   data/raw/
   data/processed/
   ```

5. **Verify it worked:**
   ```bash
   python run_reckoner.py
   ```
   It should print all your existing history months and save the report —
   that confirms the data copied over correctly.

6. **Set up the mobile dashboard on the new PC:**
   ```bash
   python server.py
   ```
   (or double-click `Start Reckoner Server.bat`). This machine generates
   its **own** random login the first time it runs — it will *not* match
   the password on the old PC, since `server_config.json` isn't shared via
   GitHub either. Note the new username/password it prints.

7. **For phone access to this PC**, install
   [Tailscale](https://tailscale.com/) and sign in with the same account
   used before. It gets its own Tailscale address (different from any
   other PC already in the tailnet) — bookmark that new address on your
   phone.

If you *don't* need the existing history — e.g. setting up a second,
independent installation — skip step 4 and just start dropping fresh
monthly files into `data/raw/` per the Setup section above.

## Mobile dashboard

Run `python server.py` (or double-click `Start Reckoner Server.bat`). It
prints a URL and a login (auto-generated on first run, saved to
`server_config.json`) — open that URL from your phone on the same network.
You can also upload next month's files directly from the phone.

For access away from your home network, this is designed to be used over
[Tailscale](https://tailscale.com/) (a private device-to-device tunnel) —
**not** by exposing the port to the public internet.

## Salary calculator (People → Salary)

Monthly slips built from the rules agreed in the two Salary Review rounds
(Oct 2026) - the rules and their reasons are at the top of `salary.py`.
Salary = Fixed 35% + Performance 45% + Discipline 20% of each person's base;
performance is against their own best month, discipline loses 5% per late
arrival / long break after 3 free ones (early exit counts half). Until the
trial month in Setup → Rules, nobody is paid below their base.

Each month:
1. **Setup** (once, then when pay changes): base salary and role per person.
   Enter each person's agreed timings (start, break out, break back, end) -
   late / long break / early are judged against them. Tick **Half-day** for
   half-day / contract staff: only start and end, no break counted, and
   nothing is counted until their shift is entered.
2. **Log**, on the day it happens: wrong item, cash short, near-expiry left
   on a rack, complaints, compliments, covering for a colleague, unplanned
   leave. Late arrival, lunch and exit are read from the bills automatically.
3. **Charts**, after the month ends: upload each back-office person's
   Performance Chart from shubhadahealth.com.
4. **Ratings**: 1-5 on the four O1-O4 points.
5. **Commit** (once, then when someone's timings change): staff commit to
   their own timings and targets on a Google Form. Run
   `google/commitment_form.gs` once at script.google.com (steps at the top of
   that file) and paste the TEMPLATE LINK it prints. Each person then gets a
   WhatsApp link pre-filled with their usual times and best month. Download
   the answers sheet as .xlsx, import it, and approve each answer - only
   approved answers count. Reaching committed targets earns up to 5% of base,
   shared per target reached.
6. **Slips**: tap a person to see how every rupee was worked out.
   **Share on WhatsApp** on each slip opens WhatsApp with that person's full
   slip already typed (every parameter, their marks, discipline, targets and
   pay) - check it and press Send yourself. Save each person's number on the
   Edit screen so it opens their chat directly; without one you pick the chat.
   The shop's margin and branch sales figures are left out of the message.
   **Send automatically** (top of Slips) sends every slip not yet sent this
   month from WhatsApp on this PC, with no Send button to press. It uses its
   own Chrome profile (`.whatsapp_profile/`, gitignored - it holds the linked
   WhatsApp session); the first run shows a QR code to scan with your phone
   (WhatsApp > Linked devices > Link a device). Each message is read back
   before sending and confirmed in the chat afterwards, using the order
   automation project's WhatsApp driver (copied as `salary_whatsapp.py`).
   Anyone already sent is skipped. Try **Send a test to my own number** first.

What you enter is kept in `data/processed/salary/`, so it's included in the
normal backups.

**Logins vault** (bottom of Salary > Edit): website logins the reckoner uses
on its own - first shubhadahealth.com, for fetching attendance and
Performance Charts. Stored encrypted in `.credentials.enc` with the key
`RECKONER_VAULT_KEY` in `.env` (both on this PC only, both gitignored). The
dashboard only ever shows a masked username. Losing `.env` means typing the
logins in again.

**Fetch from shubhadahealth** (top of Salary > Charts): logs in with the
vault's login in its own Chrome window (`.shubhadahealth_profile/`) and, for
the month, reads the Attendance Report (everyone's punches) and every Emp
Performance Chart - read-only; it never clicks a "Change", "Add Target" or
leave/shift page. The punch-in becomes each person's arrival time for
discipline (bills still give breaks and exits; a punch-in outside 05:00-23:00
is ignored). Names spelt differently on the site are matched automatically;
any it can't match are listed there for you to link once.

## Voice (trial)

The Ask page has two voice buttons. Both answer from the same Ask
Subbii Shubhada assistant as typed questions, with patient lookups switched off.

### 🗣 Hands-free conversation (the cheaper alternative to 🎤)

Tap 🗣 once and just talk, like the live conversation: the phone itself
notices when you start and stop speaking, and only that stretch of speech is
sent. Each one goes to speech-to-text, then a tiny check that it was said TO
the assistant (counter talk is ignored, and doesn't count against the
question limit), then the assistant answers in the language you spoke (Kannada
or English) and a text-to-speech voice reads it out. Start talking over an
answer to interrupt it; tap End (or ■) to stop. The conversation ends by itself
after 3 quiet minutes. The bar shows the running cost from OpenRouter's actual
charges (the voice's charge is looked up a few seconds later, shown as "+").

Cost: nothing while idle - only speech sent and answers read out. Measured
2026-10-10: a 3-minute conversation with three answered questions and one
ignored bit of chatter cost $0.068 in all (~2 US cents per question) vs $0.15
for 3 minutes of GPT-Live. Reading an answer aloud is most of it ($0.008-0.022
per answer); speech-to-text + the "said to me?" check is ~$0.0002 per
utterance. Uses the existing `OPENROUTER_API_KEY`:

- Speech-to-text: `microsoft/mai-transcribe-2` (fastest and best on mixed
  Kannada-English in a 2026-10-04 test).
- Text-to-speech: `google/gemini-3.1-flash-tts-preview`, voice `Kore` (the
  only voice whose Kannada came back word-perfect). If it fails, the phone's
  own built-in voice reads the answer instead.
- Override with `SPEECH_STT_MODEL`, `SPEECH_TTS_MODEL`, `SPEECH_TTS_VOICE` in
  `.env`. This OpenRouter account has zero-data-retention on, which blocks
  some models (e.g. Gemini 3.8 TTS) - pick ones it allows.

**Free reading-out.** `SPEECH_TTS_MODEL=phone` (also `device`, `browser`, `free`,
`off`, `none`) calls no cloud voice at all: the page reads answers with the
device's own voice (Kannada text in `kn-IN`, otherwise `en-IN`). Android's
Google voice reads Kannada; this PC's Windows voices do not. Restart the server
after changing it.

**Free listening (optional, per device).** The "Listening" menu above the
question box chooses how 🗣 listens: *Best quality* (the paid speech-to-text
above - the default) or *Free* in English, Kannada (ಕನ್ನಡ) or Hindi. Free uses
the browser's own speech recognition (`SpeechRecognition`: Chrome or Edge, also
Chrome on Android; https or localhost; needs the internet - the browser sends
the audio to its own speech service, Google's for Chrome, not to OpenRouter), so
there is no speech-to-text charge. It listens in one language at a time, so
Kannada mixed with English is better left to *Best quality*. The words go to
`/api/speech/heard`, which does the same "said to me?" screening as
`/api/speech/transcribe` (echo guard, stray words, name call, basket commands),
then `/api/ask` as usual. Same rules as the paid listening: a word or two over a
playing answer is ignored; a real sentence (4+ words) interrupts it. The menu
choice is remembered in the browser (`ask_listen_mode`); it is hidden where the
browser has no speech recognition (Firefox) and the paid listening is used.

### 🎤 Live conversation (GPT-Live-1) - optional, off by default

Controlled by `VOICE_LIVE_ENABLED` in `.env` (`1` = show the 🎤 button,
`0`/absent = hidden and `/api/voice/session` refuses); restart the server
after changing it.

Ask your question out loud and hear the answer.
OpenAI's GPT-Live-1 does the talking; every business question it hears is
handed to the same Ask Subbii Shubhada assistant as a typed question, so the
numbers are identical. Patient lookups are switched off in voice mode.

- Needs `OPENAI_API_KEY=...` in `.env` (the key stays on the PC).
- Costs $0.05 per minute of voice, billed per second, plus the normal
  per-question cost. A voice session ends itself after 10 minutes.
- Browsers only allow the microphone on **https** pages or on the PC itself
  (this applies to both voice buttons).
  Over the plain `http://` Tailscale/LAN address the phone shows a message
  instead. To use it from a phone, either serve the dashboard over https
  (e.g. `tailscale serve`, run from the Windows account that owns Tailscale)
  or, on Android Chrome only, add the dashboard address under
  `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.
- What is sent to OpenAI: your voice, the transcript, and each answer it
  speaks.

## Dual mode: orders + sales on one Ask page

The Ask page can answer **order** questions ("where is my Dolo order?", "what is
overdue at DRK?") as well as **sales / forecast / history** ones. The model decides
per question which system to use - or uses both - and says where each figure came from.
Order questions are answered from the order-automation dashboard (a separate project);
sales, forecasts, margins and anything older than ~90 days come from this project's
history as before.

To switch it on, put two lines in this project's `.env`:

    ORDER_ASK_URL=http://127.0.0.1:8000
    ORDER_ASK_KEY=<the same long secret as ASK_BRIDGE_KEY in the order project's .env>

then restart the reckoner server. Without them nothing changes - the page behaves exactly
as before. If the order dashboard is off or unreachable, the page quietly falls back to
sales questions only and tells you order tracking is offline if you ask about orders.

- **Read-only.** The order tools only read; nothing can be placed or changed from here.
  Profit-by-day on the order side is *not* exposed through this (use the Order dashboard's
  Ask page and its admin password for that). The order project's key check, lockout and
  rate limit live in its `dashboard/backend/ask_bridge_routes.py`.
- **Limits.** `/api/ask` now has a brake (`ask_limits.py`): `ASK_DAILY_LIMIT_QUESTIONS`
  (default 200 a day) and `ASK_PER_10_MIN` (default 20); `0` turns a limit off. It exists
  because every question can trigger several paid model calls, and a question asked here
  never passes the order dashboard's own spending cap.
- **Scheme list.** "What is the scheme of Dolo 650?", "who gives the best scheme on X?",
  "what does DRK give on X?" are answered from the order project's **product scheme list**
  (what each distributor is listing with a scheme right now, e.g. `5+1`), through the same
  bridge: the tool is `orders_find_product_scheme`. It is not the scheme on past purchase
  bills (those stay with the purchase tools). A product missing from the list simply has no
  scheme listed by any distributor. The list is refreshed on the order side, and the order
  backend has to be restarted once to load the tool (it restarts itself every morning).
- **Tests.** `venv/Scripts/python.exe test_dual_ask.py` (offline - a stub order dashboard
  and a scripted model).

## Rack & bin questions on the Ask page

The Ask page can also answer from **Rack Management** (the separate rack app on port
8765): "which rack or bin is Ecosprin AV 75 in?", "how many plastic bins do we have?",
"what is in bin A-3?", "what is on the H1 rack?". Three tools in `rack_bridge.py`
(`rack_find_product`, `rack_counts`, `rack_contents`) read that app's own Find, branch
and Rack & Bin plan screens, so an answer is exactly what staff see there - none of its
rules are copied here.

To switch it on:

1. In Rack Management: **Staff** > add a staff member (for example "Reckoner Ask") >
   **Set PIN**.
2. Put that PIN in this project's `.env`:

        RACK_ASK_PIN=<that PIN>

   (`RACK_ASK_URL` is only needed if the rack app is not at `http://127.0.0.1:8765`.)
3. Restart the reckoner server.

Without `RACK_ASK_PIN` nothing changes. If the rack app is off the page tells you it is
not reachable and keeps answering everything else.

- **Read-only, in code.** The only requests ever sent are the rack app's read screens
  plus the login and the branch switch; assigning, moving, renaming, the staff list and
  the audit log are refused before anything is sent. The PIN is never logged or shown.
  It is a normal staff PIN, so keep it in `.env` (git-ignored) and nowhere else.
- **Plan vs placed.** The rack app holds a plan (where each product should live) and what
  staff have actually placed so far. Answers say "planned for ..." unless staff have
  recorded placing it, and a `PARK ...` code is only a virtual holding list.
- **Branches.** Shivaji Chowk and Hospet Road have a plan; Herur and Sidhapur do not yet.
- **Tests.** `venv/Scripts/python.exe test_rack_bridge.py` (offline - a fake rack app
  inside the test, no live data).

## Payments received on the Ask page

Ask can answer "how much UPI came in today / this month", "daily payments this week", "which hour
do most payments arrive", "biggest payments", "payments from <name>" and "what is the bank balance"
from the payment dashboard's ledger (`payment_ledger.py`, tools `payments_*`).

- **Read-only and separate.** It reads that app's `backend/.gmail_transactions.json` directly (set
  `PAYMENT_LEDGER_PATH` in `.env` if it ever moves). It never writes to the file, never calls that
  server, and needs no change in it. With no file there are no tools and the Ask prompt is unchanged.
- **What the numbers are.** Money RECEIVED into the shop's bank accounts (credits only - not sales),
  since the dashboard started reading on 22 Sep 2026. A KBL alert carries no transaction date, so
  its time is when the alert reached the mailbox; an Axis alert has its own. Alerts the dashboard
  could not read are not in the totals - every answer's data says how many (683 at the time of writing).
- **Privacy.** Totals, days, hours, accounts and "largest payments" never carry a payer's name or UPI
  id. Only `payments_find_payer` returns who paid (the UPI payer name), like the patient lookups it
  is withheld from voice conversations - and even it never returns the UPI id, reference or narration.
  Everything Ask returns goes to the AI provider, as with every other Ask answer.
- **Balance.** `payments_ledger_info` gives the balance printed in an account's latest alert, with its
  time - not a live bank balance.
- **Tests.** `venv/Scripts/python.exe test_payment_ledger.py` (offline - a made-up ledger).

## CRM reports on the Ask page (and the GST file on WhatsApp)

Ask can read the shop's CRM (shubhadahealth.com) for you - `crm_reader.py`, tools `crm_*`:
"October GST report" (net GST payable, tax totals, **and the Excel file sent to your WhatsApp**),
"outstanding payable" (what is owed, party by party) and "when did we last pay <supplier>".

- **One saved login.** It uses the shubhadahealth login already in the vault (Salary > Edit > Vault).
  The program signs in by itself whenever a question needs it, in its own hidden Chrome profile
  (`.crm_profile/`, git-ignored). The password is read only by that code - never printed, logged or
  sent to the AI - and Claude never types it. Change it in the vault box; nothing else to update.
- **Read-only, by construction.** Every click goes through an allow-list (menus, the report's
  Generate / Excel buttons, View Ledger, Next page); anything that looks like a change - Set Already
  Paid, voucher, create, alter, delete, save - is refused in code. Pages are reached by clicking
  through the menus (the site refuses direct addresses).
- **The GST route.** Dashboard > Reports > Procurement > Consolidated GST Report > calendar (year, then
  month) > Generate > wait until every section has stopped saying "Please wait fetching data..." > Excel.
  The download goes to `data/crm_downloads/` (git-ignored, kept 30 days) and is queued for the chat of
  whoever asked (a question from the Ask web page goes to the owner's chat). Only files in that folder
  can ever be queued, so the hook cannot send anything else from the PC.
- **Speed.** A report takes 20 to 90 seconds (the GST page loads section by section); the WhatsApp
  "got it" goes out first. Results are kept 10-15 minutes. One browser job at a time; a second request
  waits for the first. Not offered in voice conversations.
- **Switches.** `CRM_ENABLED=0` turns it off; `CRM_HEADLESS=0` shows the Chrome window (the default
  is hidden). A saved login the site rejects gives a plain "check the vault" message.
- **WhatsApp.** Sending the GST file on WhatsApp is done by the Shop Assistant (project-15) now; a report
  asked for on this Ask page is saved in `data/crm_downloads/` only.
- **Tests.** `venv/Scripts/python.exe test_crm_reader.py` (offline - made-up pages and a scripted
  fake browser).

## WhatsApp, sales bills and the Shop Assistant

Ask by WhatsApp and the sales bill by WhatsApp moved out of the reckoner on 31 Oct 2026, into their own project:
**`D:\claude projects\project-15 shop assistant`** (its own server on port 8030, registered in Server Manager). Its
README describes them. `WHATSAPP_ASK_ENABLED` in this `.env` is 0: two bots must never answer the same chat.

The assistant reaches the reckoner's data through a small keyed API (`assistant_bridge.py`):
`/api/assistant/ping`, `/tools`, `/context`, `/run`, `/basket`. It answers only a program on this PC (127.0.0.1)
that sends `ASSISTANT_BRIDGE_KEY` (the same 32+ character secret in both projects' `.env`); without the key every
route answers 503. The tools are the Ask page's own (read-only, plus `save_note`). Tests:
`venv/Scripts/python.exe test_assistant_bridge.py`.

## Backups & recovery

Every time `run_reckoner.py` (directly, via a `.bat` file, or through a mobile
upload) actually ingests something new, it copies `data/processed/`,
`holidays.json`, and `server_config.json` into a timestamped folder under
`backups/`, keeping the most recent 8. This is a **local** safety net against
a bad ingest, an accidental delete, or a corrupted file — it lives on the
same PC, so it does **not** protect against a failed hard drive. For real
disaster recovery, periodically copy the `backups/` folder (or `data/processed/`
directly) to a second location — a synced OneDrive/Google Drive folder is
enough.

**If `data/processed/` is ever lost or corrupted anyway:** the original
`.xlsx` files in `data/raw/` are kept indefinitely, so the full history can be
rebuilt from scratch:
1. Stop the mobile server if it's running.
2. Move (don't delete) the broken `data/processed/` folder aside, in case
   anything in it is still useful.
3. Run `python run_reckoner.py` — it re-ingests every file in `data/raw/`
   from zero and rebuilds `sales_master.csv`, `purchase_master.csv`, and
   `manifest.json` from them.
4. Compare the new report's `History months` against what you expect. If a
   month is missing, its file may have been removed from `data/raw/` at some
   point — check the `backups/` folders for an older `data/raw/`-equivalent,
   or the original export if it's saved elsewhere.

## Security notes for anyone cloning this repo

This repo intentionally does **not** include:
- `server_config.json` — the mobile dashboard's login. Regenerated fresh
  (random password) the first time `server.py` runs on a new machine.
- `data/raw/` and `data/processed/` — the actual Sale/Purchase data. This is
  real patient and business financial data and must never be committed.
- `reports/**/*.xlsx` — generated reports contain real revenue/profit figures.
- `backups/` — copies of the same real data (see Backups & recovery, above).

If you fork or clone this to reuse the *tool* (not the data), you start with
empty `data/raw/` and `data/processed/` folders and feed it your own files.

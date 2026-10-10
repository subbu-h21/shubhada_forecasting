# Pharmacy Ready Reckoner

Monthly reckoner for a pharmacy's Sale/Purchase XLS exports: demand forecast,
reorder signals, over/under-purchased tracking, gross profit & margin,
purchase entry error checks (PTR vs MRP, MRP consistency), supplier scheme
and discount consistency, and a branch-wise breakdown. Includes a mobile
dashboard so reports can be checked from a phone.

## Setup

1. Install dependencies: `pip install pandas openpyxl flask waitress numpy`
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
   pip install pandas openpyxl flask waitress numpy
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
performance is against their own best month, discipline loses ₹50 per late
arrival / long lunch after 3 free ones (early exit counts half). Until the
trial month in Setup → Rules, nobody is paid below their base.

Each month:
1. **Setup** (once, then when pay changes): base salary and role per person.
   Tick **Half-day** for half-day / contract staff and enter their agreed
   shift - they're judged against it (no lunch counted); until it's entered
   no late/early is counted for them.
2. **Log**, on the day it happens: wrong item, cash short, near-expiry left
   on a rack, complaints, compliments, covering for a colleague, unplanned
   leave. Late arrival, lunch and exit are read from the bills automatically.
3. **Charts**, after the month ends: upload each back-office person's
   Performance Chart from shubhadahealth.com.
4. **Ratings**: 1-5 on the four O1-O4 points.
5. **Slips**: tap a person to see how every rupee was worked out.

What you enter is kept in `data/processed/salary/`, so it's included in the
normal backups.

## Voice (trial)

The Ask page has two voice buttons. Both answer from the same Ask the
Reckoner assistant as typed questions, with patient lookups switched off.

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

### 🎤 Live conversation (GPT-Live-1) - optional, off by default

Controlled by `VOICE_LIVE_ENABLED` in `.env` (`1` = show the 🎤 button,
`0`/absent = hidden and `/api/voice/session` refuses); restart the server
after changing it.

Ask your question out loud and hear the answer.
OpenAI's GPT-Live-1 does the talking; every business question it hears is
handed to the same Ask the Reckoner assistant as a typed question, so the
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
- **Tests.** `venv/Scripts/python.exe test_dual_ask.py` (offline - a stub order dashboard
  and a scripted model).

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

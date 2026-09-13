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

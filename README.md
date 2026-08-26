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

## Mobile dashboard

Run `python server.py` (or double-click `Start Reckoner Server.bat`). It
prints a URL and a login (auto-generated on first run, saved to
`server_config.json`) — open that URL from your phone on the same network.
You can also upload next month's files directly from the phone.

For access away from your home network, this is designed to be used over
[Tailscale](https://tailscale.com/) (a private device-to-device tunnel) —
**not** by exposing the port to the public internet.

## Security notes for anyone cloning this repo

This repo intentionally does **not** include:
- `server_config.json` — the mobile dashboard's login. Regenerated fresh
  (random password) the first time `server.py` runs on a new machine.
- `data/raw/` and `data/processed/` — the actual Sale/Purchase data. This is
  real patient and business financial data and must never be committed.
- `reports/**/*.xlsx` — generated reports contain real revenue/profit figures.

If you fork or clone this to reuse the *tool* (not the data), you start with
empty `data/raw/` and `data/processed/` folders and feed it your own files.

# icici-statement-fetcher

Reads ICICI retail net banking and saves the result into the same
transactions database the HDFC, Axis and SBI fetchers write to (the
`txdb` package inside `omarchy-transactions`), which is what the
Transactions bar widget reads.

**Status: step one.** Only the probe exists so far. It opens the login
page, waits while you scan the QR, and saves the real markup of whatever
comes back. The scraper proper is written from those captures — the same
way the other three fetchers were — rather than from guesses about a page
nobody has seen yet.

## Setup

Uses [`uv`](https://docs.astral.sh/uv/) for the virtualenv and packages.

```bash
uv venv .venv
uv pip install -r requirements.txt --python .venv/bin/python
.venv/bin/playwright install chromium   # optional: a run downloads it anyway
```

## Run the probe

```bash
cd ~/Work/icici-statement-fetcher
.venv/bin/python -m src.probe
```

A Chromium window opens on the login page — it has to be visible, since
you scan the QR off the screen with the iMobile app. The probe waits up to
five minutes (`ICICI_LOGIN_TIMEOUT`), notices when the page stops being the
login page, then saves:

| file | what it is |
|---|---|
| `output/landing.html` / `.png` / `.txt` | the page right after login, as markup, picture and plain text |
| `output/page-<name>.*` | the same for up to six pages it then opens (statements, transactions, account detail) |
| `output/probe-summary.json` | headings, every amount and account-looking number it saw, and each link it found with a selector that reaches it |

Those files contain your account data. `output/` is git-ignored; delete it
once the selectors are written.

## Login

QR only. You scan, the browser session is the session — nothing is stored,
no password is handled, and there is no `.env` to fill in. `ICICI_HEADED=0`
exists for later, when a run no longer needs a scan.

## Files

- `src/probe.py` — the capture run described above
- `src/browser.py` — Chromium, headed by default, desktop user-agent
- `src/config.py` — login URL, output directory, timeouts
- `src/guard.py`, `src/store.py` — byte-identical copies of the other
  fetchers': error types with one-line messages for the widget, atomic
  writes, the single-run lock, the run wrapper that always ends the status
  file as done/error/cancelled, on-demand Playwright browser install, and
  the bridge to the shared database

## Next, once the probe has run

1. Read `probe-summary.json` and the saved markup; write `login.py`
   (detect the QR, detect the signed-in state) and the statement scraper
   against it.
2. Feed rows through `store.ingest` with `bank="ICICI"`, including each
   row's running balance, so the widget's balance check covers this bank
   too.
3. Add the fetch control to the widget beside HDFC, Axis and SBI.

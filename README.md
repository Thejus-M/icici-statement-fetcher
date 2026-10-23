# icici-statement-fetcher

Reads ICICI retail net banking and saves the result into the same
transactions database the HDFC, Axis and SBI fetchers write to (the
`txdb` package inside `omarchy-transactions`), which is what the
Transactions bar widget reads.

**Status: the card is live.** `icici-fetch` saves it; `icici-probe`
remains for capturing markup when something changes.

```bash
icici-fetch     # scan the QR, and the card lands in the widget's BILLS
icici-probe     # capture markup instead of saving anything
```

The fetcher reads the credit card's summary — name, last four, amount
due, spend this cycle, total and available limit — and writes it as a
`card_cycles` row, the same shape the HDFC cards use, so the widget shows
it with no special case. It writes no transactions: this profile has no
linked bank account and the card reports none this cycle.

Two rules learnt the hard way, both encoded in the code and worth keeping
if you extend it:

- **Never `page.goto()` an in-app route once logged in.** ICICI ends the
  session (`/bank-account` bounced to `/login-page`, then everything to
  `/session-expire`), exactly as HDFC does. Click the sidenav instead.
- **Never click the card page's eye toggle.** It unmasks the card number
  and expiry, and ICICI demands an OTP for it — an OTP for data this
  fetcher neither needs nor should store. Every "mysterious" OTP prompt
  during development was self-inflicted that way.

**Older note — step one.** Only the probe existed at first. It opens the login
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

A Chromium window opens on the login page, so there is always a code on
screen to scan with the iMobile app (the widget's popup shows the same
code — see Login). The probe waits up to five minutes
(`ICICI_LOGIN_TIMEOUT`), notices when the page stops being the login page,
then saves:

| file | what it is |
|---|---|
| `output/landing.html` / `.png` / `.txt` | the page right after login, as markup, picture and plain text |
| `output/page-<name>.*` | the same for up to six pages it then opens (statements, transactions, account detail) |
| `output/probe-summary.json` | headings, every amount and account-looking number it saw, and each link it found with a selector that reaches it |
| `output/qr.png` | the login code, redrawn for the widget and deleted when the run ends |

Those files contain your account data. `output/` is git-ignored; delete it
once the selectors are written.

## Login

QR only. You scan, the browser session is the session — nothing is stored,
no password is handled, and there is no `.env` to fill in.

The code does not have to be scanned off the browser window. ICICI draws
it into a `<canvas>`, which `src/qr.py` screenshots, decodes with
`zbarimg` and redraws on the widget's dark tile as `output/qr.png` — the
same hand-off the HDFC fetcher makes, so the Transactions widget shows the
code in its own popup. ICICI replaces the code about every 30 seconds; each
new one is republished and the status file re-written, which is what makes
the popup reload the image.

After about four codes (90-120 seconds) ICICI stops: it removes the canvas
and leaves the QR tab empty, with no refresh button and no message.
Re-clicking that tab brought a fresh code back in one live run out of four
— in the others no canvas existed at all eight seconds later — so the wait
tries it three times and then clears `output/qr.png` and says "The login
code expired. Fetch again." in the widget. A popup with no code beats a
popup with a code the phone refuses. Scanning inside the first two minutes
avoids the whole question.

`output/qr.png` is a login credential: it is written through a temp file
(never half-drawn) and deleted as soon as login succeeds, the run fails or
Cancel stops it.

The finder rings use ICICI's own two theme colours by default — red outer
ring, orange inner dot, sampled from ICICI's real header logo and the same
pair `IciciLogo.qml`'s mark fades between — rather than one flat accent.
`QR_FG`, `QR_BG`, `QR_ACCENT` and `QR_ACCENT2` (`#rrggbb` only) override
the colours. Swapping the first two renders the usual dark-on-light code,
if a phone ever refuses the light-on-dark one. `ICICI_HEADED=0` hides the
browser window; the window still shows by default, so there is always
something to scan if the popup's code does not read.

## Files

- `src/probe.py` — the capture run described above
- `src/qr.py` — the login code for the widget: decode, redraw, publish,
  and delete again. Needs `zbarimg`, `qrencode` and Pillow; without any of
  them it publishes ICICI's own screenshot, which still scans
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

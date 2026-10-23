"""Entry point: icici-fetch (or .venv/bin/python -m src.main)

Opens ICICI net banking, waits while you scan the QR with the iMobile
app, reads the credit card's summary and saves it into the shared
transactions database the Transactions widget reads.

What it saves, and what it does not:

  * The card — name, last four, amount due, spend this cycle, limit and
    available limit — as a `card_cycles` row, the same shape the HDFC
    cards use, so the widget shows it in BILLS with no special case.
  * No transactions. This profile has no linked bank account, and the
    card's page says "no transactions in this billing cycle". When there
    are some, they go through store.ingest.upsert_rows like every other
    bank's rows.

Two things learnt the hard way and encoded here: never page.goto() an
in-app route once logged in (ICICI ends the session, as HDFC does), and
never click the card page's eye toggle (it unmasks the card number and
asks for an OTP for the privilege).
"""
from __future__ import annotations

import re
import sys
from datetime import datetime

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from . import browser as browser_mod
from . import config, qr, store_card
from .guard import ActionNeeded, FetchError, atomic_write_json, run_main
from .probe import _click, _text, card_details, open_menu, open_qr_tab, settle, wait_for_login

CARD_MENU = "CARDS"
CARD_ITEM = re.compile(r"credit card", re.I)
# One wording for the one step that needs you, reused every time the code
# is republished so the popup's line does not change under you.
QR_MESSAGE = "Scan the QR with your iMobile app."


STATUS_PATH = config.OUTPUT_DIR / "icici-fetch-status.json"


def write_status(stage: str, message: str = "", qr: str = "", card: str = "", step: str = "") -> None:
    """The progress file the widget's reload button watches, written
    atomically so it never reads a half-written one. Same keys as the
    other three banks': the widget needs no ICICI special case.

    `qr` carries the path to the login code, which the widget shows while
    the stage is qr-ready — the one step that needs you. (It is one of the
    four banks' shared keys, so it keeps that name and shadows the `qr`
    module inside this function; the module is only used outside it.)
    ICICI retires the code on a timer, so this is written again for each
    redrawn one: the widget keys its image cache on `updated`, and would
    otherwise keep showing the first code it was given.
    """
    config.ensure_output_dir()
    atomic_write_json(STATUS_PATH, {
        "stage": stage, "message": message, "qr": qr, "card": card, "step": step,
        "updated": datetime.now().isoformat(timespec="milliseconds"),
    }, mode=0o644)
    print(f"  [{stage}] {message}", flush=True)


def run() -> str:
    with sync_playwright() as playwright:
        browser = browser_mod.launch(playwright)
        context, page = browser_mod.new_page(browser)
        page.on("dialog", lambda d: d.dismiss())
        page.set_default_timeout(30_000)

        try:
            # A code left behind by a run that was killed outright is not
            # this run's code: clear it before the status can say qr-ready
            # and point the widget at it.
            qr.forget()
            write_status("opening", "Opening ICICI…")
            page.goto(config.LOGIN_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            # Only point the widget at a code that is actually there: with
            # no qr path the popup stays shut and the message line carries
            # the fallback, rather than the popup showing a broken image.
            if open_qr_tab(page):
                write_status("qr-ready", QR_MESSAGE, qr=str(qr.QR_PATH))
            else:
                write_status("qr-ready", "Scan the QR in the browser window.")
            if not wait_for_login(
                    page,
                    on_qr=lambda path: write_status("qr-ready", QR_MESSAGE, qr=str(path)),
                    # No qr path, so the popup closes and this line shows
                    # instead: the code ICICI retired cannot be scanned, and
                    # saying so beats leaving it on screen.
                    on_expired=lambda: write_status(
                        "qr-ready", "The login code expired. Fetch again.")):
                raise ActionNeeded("The QR was not scanned in time.")

            write_status("reading-card", "Reading the credit card…")
            items = open_menu(page, CARD_MENU)
            target = next((i for i in items if CARD_ITEM.search(i)), None)
            if not target:
                raise FetchError("No credit card entry in the CARDS menu.")
            if not _click(page.get_by_text(re.compile(rf"^\s*{re.escape(target)}\s*$", re.I)), target):
                raise FetchError("Could not open the credit card page.")
            settle(page)

            card = card_details(_text(page))
            if not card.get("last4"):
                raise FetchError("The credit card page did not show a card number.")

            account_id = store_card.save_card(card)
            due = card.get("totalDue", "?")
            limit = card.get("totalLimit", "?")
            return (f"ICICI: {card.get('cardName', 'card')} ••{card['last4']} — "
                    f"{due} due, limit {limit} ({account_id}).")
        finally:
            # The login code is a credential: it goes whether the run
            # succeeded, failed or was cancelled.
            qr.forget()
            for closer in (context.close, browser.close):
                try:
                    closer()
                except PlaywrightError:
                    pass


if __name__ == "__main__":
    sys.exit(run_main(run, write_status, "ICICI", config.OUTPUT_DIR))

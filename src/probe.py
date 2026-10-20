"""Step one for ICICI: open the login page, wait while you scan the QR with
the iMobile app, then save everything the landing page shows.

    cd ~/Work/icici-statement-fetcher && .venv/bin/python -m src.probe

Nothing is scraped into the database yet and nothing is guessed. This run
exists to capture real markup — the same way the HDFC, Axis and SBI
fetchers were written — so the selectors that follow are written against
what the site actually serves rather than against an assumption.

What it leaves in output/:

  landing.html / landing.png        the page right after login
  landing.txt                       its visible text, for reading quickly
  <slug>.html / <slug>.png          the same for each page it then opens
  probe-summary.json                headings, amounts, account-looking
                                    numbers and every link it found, with
                                    the selector that reaches each one

Your account data is in those files, so output/ is git-ignored. Delete it
when you are done sharing anything from it.
"""
from __future__ import annotations

import json
import re
import sys
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from . import browser as browser_mod
from . import config

# Login is done when the page stops being the login page. Checked against
# the URL and against the text, because a bank's SPA often keeps the URL
# while swapping the whole view.
_LOGGED_OUT_URL = re.compile(r"login|signin|sign-in", re.I)
_LOGGED_IN_TEXT = re.compile(r"log ?out|sign ?out|dashboard|my accounts|account summary", re.I)

_AMOUNT = re.compile(r"(?:₹|INR|Rs\.?)\s?[\d,]+(?:\.\d{2})?")
# A savings account number or a masked card: long digit runs, or digits
# behind asterisks.
_ACCOUNT_LIKE = re.compile(r"(?:[Xx*•]{2,}\s?\d{3,})|(?:\b\d{9,18}\b)")

_INTERESTING_LINK = re.compile(
    r"statement|transaction|account|balance|detail|history|download|card", re.I)

# The login page opens on the User ID tab; the QR is only drawn once its
# own tab is selected, into a <canvas>. Confirmed live: the tabs are
# <button class="tab-btn" data-tts="User ID" | "Mobile No" | "QR code">.
_QR_TAB = "button.tab-btn[data-tts='QR code']"
_QR_CANVAS = "canvas"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "page"


def _save(page, name: str) -> dict:
    """HTML, a full-page screenshot and the visible text of one page."""
    config.ensure_output_dir()
    base = config.OUTPUT_DIR / name
    saved = {}
    try:
        base.with_suffix(".html").write_text(page.content(), encoding="utf-8")
        saved["html"] = base.with_suffix(".html").name
    except PlaywrightError as exc:
        print(f"  could not save HTML: {exc}", file=sys.stderr)
    try:
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        saved["png"] = base.with_suffix(".png").name
    except PlaywrightError as exc:
        print(f"  could not screenshot: {exc}", file=sys.stderr)
    try:
        text = " ".join(page.locator("body").inner_text().split())
        base.with_suffix(".txt").write_text(text, encoding="utf-8")
        saved["txt"] = base.with_suffix(".txt").name
        saved["amounts"] = sorted(set(_AMOUNT.findall(text)))[:40]
        saved["accountLike"] = sorted(set(_ACCOUNT_LIKE.findall(text)))[:20]
    except PlaywrightError as exc:
        print(f"  could not read text: {exc}", file=sys.stderr)
    return saved


def _headings(page) -> list[str]:
    try:
        return [h for h in page.locator("h1, h2, h3, [role=heading]").all_inner_texts()
                if h.strip()][:30]
    except PlaywrightError:
        return []


def _links(page) -> list[dict]:
    """Every link and button worth clicking next, with how to reach it."""
    try:
        found = page.locator("a, button, [role=button], [role=tab], [role=menuitem]").evaluate_all(
            """els => els.map(e => ({
                 text: (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 60),
                 href: e.getAttribute('href') || '',
                 id: e.id || '',
                 testid: e.getAttribute('data-testid') || e.getAttribute('data-test') || '',
                 tag: e.tagName.toLowerCase()
               })).filter(e => e.text)""")
    except PlaywrightError:
        return []
    seen, out = set(), []
    for item in found:
        key = (item["text"], item["href"])
        if key in seen or not _INTERESTING_LINK.search(item["text"] + " " + item["href"]):
            continue
        seen.add(key)
        item["selector"] = (f"#{item['id']}" if item["id"]
                            else f"[data-testid='{item['testid']}']" if item["testid"]
                            else f"{item['tag']}:has-text(\"{item['text'][:30]}\")")
        out.append(item)
    return out[:60]


def open_qr_tab(page) -> bool:
    """Switches the login form to its QR tab and saves the code as
    output/qr.png. False when the tab or the code never appeared — the run
    can still go on, you just log in whichever way the page offers."""
    tab = page.locator(_QR_TAB)
    if tab.count() == 0:
        tab = page.get_by_text("QR Code", exact=True)
    if tab.count() == 0:
        print("  no QR tab on this page; log in whichever way it offers.", flush=True)
        return False
    try:
        tab.first.click(timeout=10_000)
        page.wait_for_selector(_QR_CANVAS, state="visible", timeout=20_000)
        page.wait_for_timeout(1200)  # let it finish drawing
        config.ensure_output_dir()
        page.locator(_QR_CANVAS).first.screenshot(path=str(config.OUTPUT_DIR / "qr.png"))
        print(f"  QR shown, also saved to {config.OUTPUT_DIR / 'qr.png'}", flush=True)
        return True
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        print(f"  could not open the QR tab: {str(exc).splitlines()[0][:90]}", flush=True)
        return False


def wait_for_login(page) -> bool:
    """Blocks while you scan the QR. Returns False if the wait runs out."""
    print(f"\nScan the QR in the window with your iMobile app. Waiting up to "
          f"{int(config.LOGIN_TIMEOUT / 60)} minutes…", flush=True)
    print("  (the code expires on its own timer — the page draws a fresh one, "
          "just scan that)", flush=True)
    deadline = time.monotonic() + config.LOGIN_TIMEOUT
    last_url = ""
    while time.monotonic() < deadline:
        try:
            url = page.url
            if url != last_url:
                print(f"  at {url[:110]}", flush=True)
                last_url = url
            body = page.locator("body").inner_text()
            if not _LOGGED_OUT_URL.search(url) or _LOGGED_IN_TEXT.search(body):
                print("  looks logged in.", flush=True)
                page.wait_for_timeout(2500)  # let the dashboard settle
                return True
        except PlaywrightError:
            pass  # mid-navigation; try again
        time.sleep(1.0)
    return False


def run() -> int:
    config.ensure_output_dir()
    summary: dict = {"loginUrl": config.LOGIN_URL, "pages": {}}

    with sync_playwright() as playwright:
        browser = browser_mod.launch(playwright)
        context, page = browser_mod.new_page(browser)
        # An ad or a "download the app" interstitial opening a second tab
        # would otherwise steal the scan.
        context.on("page", lambda p: p.close() if p != page else None)
        page.on("dialog", lambda d: d.dismiss())
        page.set_default_timeout(30_000)

        try:
            print(f"Opening {config.LOGIN_URL}", flush=True)
            page.goto(config.LOGIN_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)  # Angular draws the form after load
            summary["qrShown"] = open_qr_tab(page)
            summary["pages"]["login"] = {**_save(page, "login"), "headings": _headings(page)}

            if not wait_for_login(page):
                print("\nNo login detected before the timeout. Nothing was scraped.", file=sys.stderr)
                _save(page, "login-timeout")
                return 1

            print("\nSaving the landing page…", flush=True)
            summary["landingUrl"] = page.url
            summary["pages"]["landing"] = {
                **_save(page, "landing"),
                "headings": _headings(page),
                "links": _links(page),
            }

            # Follow the obvious ones (statements, transactions, account
            # detail) one level deep. Each is saved under its own name and
            # the run returns to the landing page afterwards.
            for link in summary["pages"]["landing"]["links"][:6]:
                name = f"page-{_slug(link['text'])}"
                print(f"  opening {link['text']!r}", flush=True)
                try:
                    page.locator(link["selector"]).first.click(timeout=8000)
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except (PlaywrightError, PlaywrightTimeoutError) as exc:
                    print(f"    skipped: {str(exc).splitlines()[0][:90]}", flush=True)
                    continue
                summary["pages"][name] = {
                    **_save(page, name), "headings": _headings(page), "url": page.url,
                    "openedFrom": link["text"],
                }
                try:
                    page.go_back(wait_until="domcontentloaded", timeout=15_000)
                    page.wait_for_timeout(1500)
                except (PlaywrightError, PlaywrightTimeoutError):
                    page.goto(summary["landingUrl"], wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
        finally:
            (config.OUTPUT_DIR / "probe-summary.json").write_text(
                json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
            try:
                context.close()
                browser.close()
            except PlaywrightError:
                pass

    print(f"\nSaved {len(summary['pages'])} pages to {config.OUTPUT_DIR}")
    print("Read output/probe-summary.json first; the .txt files are the quickest look.")
    return 0


if __name__ == "__main__":
    sys.exit(run())

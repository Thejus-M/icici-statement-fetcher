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
import os
import re
import sys
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from . import browser as browser_mod
from . import config
from .guard import read_line

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


def _text(page) -> str:
    """The page's visible text, or "" if the page has gone (the site closes
    it on us often enough that every caller has to cope)."""
    try:
        return " ".join(page.locator("body").inner_text().split())
    except PlaywrightError:
        return ""


def _save(page, name: str) -> dict:
    """HTML, a full-page screenshot and the visible text of one page."""
    config.ensure_output_dir()
    base = config.OUTPUT_DIR / name
    saved = {"url": page.url if not page.is_closed() else ""}
    for suffix, produce in ((".html", page.content), ("-dom.html", lambda: _deep_html(page))):
        try:
            (config.OUTPUT_DIR / f"{name}{suffix}").write_text(produce(), encoding="utf-8")
            saved[suffix.strip(".-")] = f"{name}{suffix}"
        except PlaywrightError as exc:
            print(f"  could not save {suffix}: {str(exc).splitlines()[0][:80]}", file=sys.stderr)
    try:
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        saved["png"] = base.with_suffix(".png").name
    except PlaywrightError as exc:
        print(f"  could not screenshot: {exc}", file=sys.stderr)
    text = _text(page)
    if text:
        base.with_suffix(".txt").write_text(text, encoding="utf-8")
        saved["txt"] = base.with_suffix(".txt").name
        saved["amounts"] = sorted(set(_AMOUNT.findall(text)))[:40]
        saved["accountLike"] = sorted(set(_ACCOUNT_LIKE.findall(text)))[:20]
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


# --- what the first live run taught us ---------------------------------
#
# * The nav reads ACCOUNTS on screen but the markup says "Accounts": the
#   uppercase is CSS, and innerText returns rendered text. Match nav items
#   case-insensitively, never against the saved HTML's casing.
# * The top nav items are **menus**, not links: clicking ACCOUNTS only
#   opens a submenu, whose items ("Bank Accounts" …) are not in the markup
#   until it is open, and the thing worth opening is inside it.
# * page.content() on the overview is ~10 MB of inline CSS and JS, which is
#   unreadable. Each page is also saved as a tag/attribute/text
#   serialisation about 500x smaller (and that walker descends into shadow
#   roots, should this app ever grow any).
# * **Going back kills the session** — the first run lost the browser to a
#   page.go_back(), exactly as HDFC does. This one never goes back: it
#   returns to the overview by clicking OVERVIEW, and stops cleanly if the
#   page dies.
# * Balances are masked ("PORTFOLIO VALUE Rs •••") until an eye toggle is
#   clicked.
# * The nav is a **sidenav rendered twice** — a desktop copy and a tablet
#   one (#sidenav / #sidenavNew, #menu-logo-tablet) — so a plain text match
#   finds the hidden copy first and every click times out waiting for it to
#   become visible. Each menu item therefore appeared twice in the second
#   run's listing. Always take the visible match.
# * Its items are bare <a> with neither href nor routerlink: Angular routes
#   them in JS, so there is no URL to navigate to directly, and a click is
#   the only way in. When Playwright refuses one (an overlay, a transition)
#   the element's own .click() is dispatched instead, the same trick the
#   HDFC fetcher uses for its sticky header.

_REVEAL = "[class*=eye i], [aria-label*='show' i], [aria-label*='unmask' i]"

# NEVER page.goto() an in-app route once logged in. The fourth live run
# did exactly that and lost the session on the first one: /bank-account
# bounced to /login-page and everything after it to /session-expire. ICICI
# behaves like HDFC — only real in-app clicks are allowed, so the sidenav
# is the way in, hidden twin and all.
#
# This profile holds one credit card and no linked bank account (that run's
# /bank-account page offered "LINK SAVINGS ACCOUNT", and the overview reads
# "WHAT I HAVE Rs 0.00"), so the probe spends its one surviving session on
# the card.
_ROUTES = [
    ("CARDS", re.compile(r"credit card|card detail", re.I)),
]

# On the credit-card page: the statement tabs, and the downloads beside
# them. A statement file is a better transaction source than scraping a
# table, if the site will hand one over.
_CARD_TABS = [re.compile(r"^current$", re.I), re.compile(r"^past$", re.I), re.compile(r"^status$", re.I)]
_DOWNLOADS = [re.compile(r"^xls$", re.I), re.compile(r"^pdf$", re.I)]
_QUICK_LINKS = [re.compile(r"^view statement$", re.I)]

# A small, readable serialisation of the DOM: tags, attributes and text,
# without the megabytes of inline CSS page.content() carries. Descends into
# shadow roots too, which page.content() would drop.
_DEEP_HTML_JS = """() => {
  const walk = (node) => {
    let out = '';
    for (const child of node.children || []) {
      const tag = child.tagName.toLowerCase();
      const attrs = [...child.attributes].map(a => ` ${a.name}="${a.value}"`).join('');
      out += `<${tag}${attrs}>`;
      if (child.shadowRoot) out += `<!--shadow-->${walk(child.shadowRoot)}<!--/shadow-->`;
      out += walk(child) || child.textContent || '';
      out += `</${tag}>`;
    }
    return out;
  };
  return walk(document.body);
}"""


def _deep_html(page) -> str:
    return page.evaluate(_DEEP_HTML_JS)


def reveal_amounts(page) -> int:
    """Clicks every mask toggle, so a capture holds figures rather than
    "Rs •••". Returns how many it clicked."""
    clicked = 0
    try:
        toggles = page.locator(_REVEAL)
        for i in range(min(toggles.count(), 8)):
            try:
                toggles.nth(i).click(timeout=2500)
                clicked += 1
                page.wait_for_timeout(300)
            except (PlaywrightError, PlaywrightTimeoutError):
                continue
    except PlaywrightError:
        pass
    return clicked


def _visible(locator):
    """The visible match, falling back to the first — the sidenav exists
    twice over, and the hidden copy is the one text matching finds."""
    try:
        shown = locator.locator("visible=true")
        if shown.count():
            return shown.first
    except PlaywrightError:
        pass
    return locator.first


def _click(locator, what: str) -> bool:
    """Click, and if Playwright will not (hidden, covered, mid-animation),
    dispatch the element's own click. Returns whether either worked."""
    target = _visible(locator)
    try:
        target.scroll_into_view_if_needed(timeout=3000)
    except (PlaywrightError, PlaywrightTimeoutError):
        pass
    try:
        target.click(timeout=6000)
        return True
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        print(f"    click refused ({str(exc).splitlines()[0][:60]}), dispatching it directly",
              flush=True)
    try:
        target.evaluate("el => el.click()")
        return True
    except PlaywrightError as exc:
        print(f"    could not click {what}: {str(exc).splitlines()[0][:70]}", flush=True)
        return False


def _menu_items(page) -> list:
    try:
        items = page.locator("[role=menuitem], [role=menu] a, .dropdown a, .submenu a, "
                             "[class*=menu i] a, [class*=dropdown i] a").all_inner_texts()
    except PlaywrightError:
        return []
    return [" ".join(i.split()) for i in items if i.strip()][:40]


def open_menu(page, label: str) -> list:
    """Opens a top-nav menu (hover first: several only open that way) and
    returns the items it revealed."""
    try:
        # innerText is the rendered text, so "Accounts" in the markup
        # arrives here as "ACCOUNTS"; match either.
        nav = page.get_by_text(re.compile(rf"^{re.escape(label)}$", re.I))
        if nav.count() == 0:
            return []
        shown = _visible(nav)
        try:
            shown.hover(timeout=5000)
            page.wait_for_timeout(700)
        except (PlaywrightError, PlaywrightTimeoutError):
            pass
        items = _menu_items(page)
        if not items:
            _click(nav, label)
            page.wait_for_timeout(1200)
            items = _menu_items(page)
        return items
    except (PlaywrightError, PlaywrightTimeoutError):
        return []


def back_to_overview(page) -> bool:
    """Never page.go_back(): the first run lost the session to it. Clicking
    OVERVIEW is a real in-app navigation, which the site allows."""
    if page.is_closed():
        return False
    try:
        home = page.get_by_text(re.compile(r"^overview$", re.I))
        if home.count() and _click(home, "OVERVIEW"):
            page.wait_for_timeout(2500)
            return True
    except (PlaywrightError, PlaywrightTimeoutError):
        pass
    return False


def wait_for_login(page) -> bool:
    """Blocks while you scan the QR. Returns False if the wait runs out."""
    budget = (f"{config.LOGIN_TIMEOUT / 60:.0f} minutes" if config.LOGIN_TIMEOUT >= 90
              else f"{config.LOGIN_TIMEOUT:.0f} seconds")
    print(f"\nScan the QR in the window with your iMobile app. Waiting up to {budget}…", flush=True)
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


# What the credit-card page prints, as confirmed across three live runs:
#   "Coral RuPay -2004 ₹ 0 Min amount due ₹0 … 6528 **** **** 2004 …
#    Total spent + ₹40 C.R. Total amount due ₹0
#    Available credit limit ₹1,30,040 … ₹1,30,000 TOTAL CARD LIMIT"
_CARD_FIELDS = (
    # Up to three words before "-2004 ₹". The all-caps nav sits right in
    # front of it ("… CUSTOMER SERVICE Coral RuPay -2004 ₹ 0"), so
    # uppercase words are dropped and the card's own title-case name kept.
    ("card", re.compile(r"((?:[A-Za-z]+ ){0,3}[A-Za-z]+)\s*-\s?(\d{4})\s*₹")),
    ("number", re.compile(r"(\d{4}(?:\s+\*{4}){2}\s+\d{4})")),
    ("holder", re.compile(r"([A-Z][A-Z ]{4,30})\s+\d{4}(?:\s+\*{4})")),
    ("minimumDue", re.compile(r"Min(?:imum)? amount due\s*₹\s?([\d,]+(?:\.\d{2})?)", re.I)),
    ("totalDue", re.compile(r"Total amount due\s*₹\s?([\d,]+(?:\.\d{2})?)", re.I)),
    ("spentThisCycle", re.compile(r"Total spent\s*([+-])?\s*₹\s?([\d,]+(?:\.\d{2})?)\s*(C\.R\.|D\.R\.)?", re.I)),
    ("availableLimit", re.compile(r"Available credit limit\s*₹\s?([\d,]+(?:\.\d{2})?)", re.I)),
    ("totalLimit", re.compile(r"₹\s?([\d,]+(?:\.\d{2})?)\s*TOTAL CARD LIMIT", re.I)),
)
_NO_TRANSACTIONS = re.compile(r"no transactions in this billing cycle", re.I)


def card_details(text: str) -> dict:
    """The card's figures, read out of the page's visible text. Only what
    is actually there — a field the page did not print is left out rather
    than guessed, so the gap is visible in the printout."""
    found: dict = {}
    for name, pattern in _CARD_FIELDS:
        m = pattern.search(text)
        if not m:
            continue
        if name == "card":
            words = [w for w in m.group(1).split() if not w.isupper()]
            found["cardName"], found["last4"] = " ".join(words) or m.group(1), m.group(2)
        elif name == "spentThisCycle":
            sign, amount, marker = m.group(1) or "", m.group(2), (m.group(3) or "").upper()
            # "+ ₹40 C.R." is money the bank owes back, not spend.
            found["spentThisCycle"] = f"{'-' if marker == 'C.R.' else sign}₹{amount}"
        else:
            found[name] = m.group(1) if name in ("number", "holder") else f"₹{m.group(1)}"
    if _NO_TRANSACTIONS.search(text):
        found["transactionsThisCycle"] = "none"
    return found


# ICICI asks for an OTP to go past the card summary (the statement tabs
# were never "missing" — the page was sitting on an OTP step we never
# answered, and the session died waiting). The field is sometimes one box
# and sometimes six single-character ones.
_OTP_FIELD = ("input[type=tel], input[formcontrolname*=otp i], input[id*=otp i], "
              "input[name*=otp i], input[aria-label*='otp' i], input[maxlength='1']")
_OTP_WANTED = re.compile(r"\botp\b|one[- ]time password|verification code", re.I)
_OTP_SUBMIT = re.compile(r"^(submit|verify|continue|proceed|confirm)$", re.I)
# Long enough to find the phone, read the message and type it in.
_OTP_TIMEOUT = float(os.environ.get("ICICI_OTP_TIMEOUT", "180"))


def answer_otp(page, where: str = "") -> bool:
    """If the page is asking for an OTP, ask you for it and type it in.

    Returns True when one was entered. The wait is yours, not the site's:
    nothing else happens until you type it or the wait runs out, which is
    the whole point — the previous runs walked on and lost the session.
    """
    if page.is_closed():
        return False
    fields = page.locator(_OTP_FIELD)
    try:
        visible = fields.locator("visible=true")
        count = visible.count()
    except PlaywrightError:
        return False
    if count == 0 or not _OTP_WANTED.search(_text(page)):
        return False

    print(f"\n  ICICI is asking for an OTP{' on ' + where if where else ''}.", flush=True)
    code = read_line("  Type it here and press Enter: ", hidden=False, timeout=_OTP_TIMEOUT)
    if not code:
        print("  no OTP typed; carrying on without it.", flush=True)
        return False
    digits = re.sub(r"\D", "", code)

    try:
        if count >= len(digits) > 1:
            # One box per digit: fill each, since typing into the first
            # only advances if the site wires it that way.
            for i, digit in enumerate(digits):
                visible.nth(i).fill(digit)
                page.wait_for_timeout(120)
        else:
            visible.first.fill(digits)
        page.wait_for_timeout(500)
        submit = page.get_by_text(_OTP_SUBMIT)
        if submit.count():
            _click(submit, "the OTP submit button")
        else:
            page.keyboard.press("Enter")
        settle(page, 12)
        print(f"  OTP sent; now at {page.url[:70]}", flush=True)
        return True
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        print(f"  could not enter the OTP: {str(exc).splitlines()[0][:80]}", flush=True)
        return False


def settle(page, seconds: float = 8.0) -> None:
    """Angular fills these pages well after load; the third run captured
    /bank-account before its content arrived."""
    try:
        page.wait_for_load_state("networkidle", timeout=seconds * 1000)
    except (PlaywrightError, PlaywrightTimeoutError):
        pass
    page.wait_for_timeout(2500)


def capture_card_statements(page, summary: dict) -> None:
    """The statement tabs on the credit-card page, and a statement file if
    the site will give one up."""
    for pattern in _CARD_TABS:
        if page.is_closed():
            return
        answer_otp(page, "the statements")
        tab = page.get_by_text(pattern)
        if tab.count() == 0:
            print(f"    no {pattern.pattern.strip('^$')} tab on this page", flush=True)
            continue
        name = f"card-tab-{pattern.pattern.strip('^$')}"
        print(f"    tab {pattern.pattern.strip('^$')}", flush=True)
        if _click(tab, name):
            settle(page, 6)
            answer_otp(page, name)
            reveal_amounts(page)
            summary["pages"][name] = {**_save(page, name), "headings": _headings(page)}

    for pattern in _DOWNLOADS:
        if page.is_closed():
            return
        button = page.get_by_text(pattern)
        if button.count() == 0:
            print(f"    no {pattern.pattern.strip('^$')} download button on this page", flush=True)
            continue
        label = pattern.pattern.strip("^$")
        try:
            with page.expect_download(timeout=20_000) as download:
                _click(button, label)
            saved = config.OUTPUT_DIR / f"statement.{label.lower()}"
            download.value.save_as(str(saved))
            summary.setdefault("downloads", []).append(saved.name)
            print(f"    downloaded {saved.name}", flush=True)
        except (PlaywrightError, PlaywrightTimeoutError):
            print(f"    no {label} download (it may need a date range first)", flush=True)


def run() -> int:
    config.ensure_output_dir()
    summary: dict = {"loginUrl": config.LOGIN_URL, "pages": {}, "menus": {}}
    extra_pages: list = []

    with sync_playwright() as playwright:
        browser = browser_mod.launch(playwright)
        context, page = browser_mod.new_page(browser)
        # Keep tabs the site opens rather than closing them — a statement
        # may well open in one — but never let one replace the main page.
        context.on("page", lambda p: extra_pages.append(p))
        page.on("dialog", lambda d: d.dismiss())
        # Only a surprise close is worth reporting; the one in the cleanup
        # below is ours, and saying so there read like a failure after a
        # run that had in fact finished.
        closing_up = {"done": False}
        page.on("close", lambda: None if closing_up["done"]
                else print("  !! the site closed the main page", flush=True))
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

            summary["landingUrl"] = page.url
            print(f"\nRevealing masked figures… {reveal_amounts(page)} toggles clicked", flush=True)
            print("Saving the overview…", flush=True)
            summary["pages"]["landing"] = {
                **_save(page, "landing"), "headings": _headings(page), "links": _links(page)}

            # Quick links first: "View Statement" is the shortest path to a
            # transaction list.
            for pattern in _QUICK_LINKS:
                if page.is_closed():
                    break
                link = page.get_by_text(pattern)
                if link.count() == 0:
                    continue
                label = " ".join(link.first.inner_text().split())
                name = f"quick-{_slug(label)}"
                print(f"  opening {label!r}", flush=True)
                if _click(link, label):
                    page.wait_for_timeout(4500)
                    if page.is_closed():
                        print("    the site closed the page on that click.", flush=True)
                        break
                    reveal_amounts(page)
                    summary["pages"][name] = {**_save(page, name), "headings": _headings(page),
                                              "links": _links(page)}
                    print(f"    now at {page.url[:80]}", flush=True)
                back_to_overview(page)

            # Then the card, through the sidenav — the only navigation
            # this site tolerates.
            for label, wanted in _ROUTES:
                if page.is_closed():
                    print("  stopping: the session is gone.", flush=True)
                    break
                items = open_menu(page, label)
                summary["menus"][label] = items
                print(f"  {label}: {', '.join(items[:6]) or '(no items found)'}", flush=True)
                target = next((i for i in items if wanted.search(i)), None)
                if not target:
                    continue
                name = f"menu-{_slug(label)}-{_slug(target)}"
                print(f"    opening {target!r}", flush=True)
                # The open submenu's markup is worth keeping whether or not
                # the click lands: it is where the real selectors live.
                _save(page, f"{name}-menu-open")
                if not _click(page.get_by_text(re.compile(rf"^{re.escape(target)}$", re.I)), target):
                    continue
                settle(page)
                if page.is_closed():
                    print("    the site closed the page on that click.", flush=True)
                    break
                if answer_otp(page, name):
                    summary.setdefault("otpSteps", []).append(name)
                reveal_amounts(page)
                summary["pages"][name] = {**_save(page, name), "headings": _headings(page),
                                          "links": _links(page)}
                print(f"    now at {page.url[:80]}", flush=True)
                if "credit-card" in page.url:
                    captured = _text(page) or (config.OUTPUT_DIR / f"{name}.txt").read_text(
                        encoding="utf-8", errors="replace")
                    summary["card"] = card_details(captured)
                    capture_card_statements(page, summary)

            # Anything the site opened in a tab of its own.
            for i, other in enumerate(p for p in extra_pages if not p.is_closed()):
                print(f"  saving extra tab {other.url[:70]}", flush=True)
                reveal_amounts(other)
                summary["pages"][f"tab-{i + 1}"] = {**_save(other, f"tab-{i + 1}"),
                                                    "headings": _headings(other)}
        finally:
            (config.OUTPUT_DIR / "probe-summary.json").write_text(
                json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
            # Printed here, not at the end of run(): the site closing the
            # page mid-walk must not cost you the figures already read.
            if summary.get("pages"):
                print(f"\nSaved {len(summary['pages'])} pages to {config.OUTPUT_DIR}")
                print_findings(summary)
            closing_up["done"] = True
            for target in (*extra_pages, page):
                try:
                    if not target.is_closed():
                        target.close()
                except PlaywrightError:
                    pass
            try:
                context.close()
                browser.close()
            except PlaywrightError:
                pass

    return 0


def print_findings(summary: dict) -> None:
    """The point of the run, in the terminal: what the card says, where the
    files are, and what is still missing."""
    card = summary.get("card") or {}
    print("\n" + "─" * 58)
    if card:
        title = f"{card.get('cardName', 'Credit card')} ••{card.get('last4', '????')}"
        print(f"  {title}")
        if card.get("holder"):
            print(f"  {card['holder'].title()}   {card.get('number', '')}")
        print()
        for label, key in (("Total amount due", "totalDue"),
                           ("Minimum due", "minimumDue"),
                           ("Spent this cycle", "spentThisCycle"),
                           ("Available limit", "availableLimit"),
                           ("Total card limit", "totalLimit"),
                           ("Transactions this cycle", "transactionsThisCycle")):
            if key in card:
                print(f"  {label:<24} {card[key]}")
    else:
        print("  No card figures were read — the run did not reach the card page,")
        print("  or the page's wording has changed. The captures below say which.")

    if summary.get("downloads"):
        print(f"\n  Statements downloaded: {', '.join(summary['downloads'])}")

    print("\n  Captured pages:")
    for name, page in summary.get("pages", {}).items():
        url = (page.get("url") or "").replace("https://retailnetbanking.icici.bank.in", "")
        print(f"    {name:<34} {url[:46]}")
    print(f"\n  Files in {config.OUTPUT_DIR}")
    print("  probe-summary.json has the lot; the .txt files read fastest.")
    print("─" * 58)


if __name__ == "__main__":
    sys.exit(run())

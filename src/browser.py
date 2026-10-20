"""Chromium for the ICICI fetcher, set up like the other three banks':
Chromium's full headless mode, the automation flag off, a regular desktop
Chrome user-agent and a desktop viewport.

Unlike the others this one runs **headed by default** — login is a QR code
you scan off the screen with the iMobile app, so the window has to be
visible. ICICI_HEADED=0 turns that off once a run no longer needs a scan.
"""
from __future__ import annotations

from playwright.sync_api import Browser, Playwright

from . import config
from .guard import launch_with_install

# Sites refuse Playwright's default headless user-agent; this is the one the
# other three fetchers use.
_DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def launch(playwright: Playwright, on_status=None) -> Browser:
    # channel="chromium" is Chromium's full build, not the headless shell.
    # launch_with_install downloads it first when a rebuilt .venv has a
    # newer playwright than the cached browser belongs to.
    return launch_with_install(lambda: playwright.chromium.launch(
        headless=not config.HEADED,
        channel="chromium",
        args=["--disable-blink-features=AutomationControlled"],
    ), on_status)


def new_page(browser: Browser):
    context = browser.new_context(
        user_agent=_DESKTOP_USER_AGENT,
        viewport={"width": 1366, "height": 900},
        device_scale_factor=2,
    )
    return context, context.new_page()

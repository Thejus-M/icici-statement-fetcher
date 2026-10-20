"""Paths and settings for the ICICI fetcher.

Login is QR based — you scan the code on screen with the iMobile app — so
nothing here is a credential. ICICI_ACCOUNT_LAST4 only matters once more
than one account turns up.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

LOGIN_URL = os.environ.get(
    "ICICI_LOGIN_URL",
    "https://retailnetbanking.icici.bank.in/login-page"
    "?utm_source=website&utm_medium=onelink&utm_campaign=home_home_na",
)

OUTPUT_DIR = PROJECT_ROOT / "output"

# The window is shown by default: the QR has to be on screen for you to
# scan it. Set ICICI_HEADED=0 once a session can be reused without one.
HEADED = os.environ.get("ICICI_HEADED", "1") == "1"

# How long the probe waits for the scan before giving up, in seconds.
LOGIN_TIMEOUT = float(os.environ.get("ICICI_LOGIN_TIMEOUT", "300"))

ICICI_ACCOUNT_LAST4 = os.environ.get("ICICI_ACCOUNT_LAST4", "").strip()


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

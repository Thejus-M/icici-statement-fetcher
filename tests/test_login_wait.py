"""The wait that keeps the widget's QR current.

ICICI replaces the code every 30 seconds and then, after about four of
them, removes the canvas and leaves the QR tab empty — measured live, and
the reason this loop both republishes and re-clicks the tab. The page here
is a stand-in: a scripted series of canvas screenshots, so the loop can be
checked without a session or a phone.
"""
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import probe, qr  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("zbarimg") is None or shutil.which("qrencode") is None,
    reason="zbarimg and qrencode are required",
)

LOGGED_OUT = "Login to Net Banking User ID Mobile No QR Code"
LOGGED_IN = "Overview My Accounts Log out"


def _code(token: str) -> bytes:
    """A stand-in for ICICI's canvas: a plain dark-on-light code."""
    return subprocess.run(
        ["qrencode", "-t", "PNG", "-s", "6", "-o", "-", f"RIB://Login?refNum={token}"],
        capture_output=True, check=True,
    ).stdout


def _published_payload() -> str:
    """What a phone would read off qr.png as it stands (it is drawn
    light-on-dark, so it is inverted first — see test_qr.py). Read while
    the wait is running: the code is deleted the moment login succeeds."""
    inverted = qr.QR_PATH.with_name("inverted.png")
    ImageOps.invert(Image.open(qr.QR_PATH).convert("RGB")).save(inverted)
    result = subprocess.run(["zbarimg", "--quiet", "--raw", str(inverted)],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


class FakeLocator:
    def __init__(self, page, kind: str):
        self.page = page
        self.kind = kind

    # .first, and the visible/hidden twin trick _click uses, both land here.
    def locator(self, _selector):
        return self

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def inner_text(self):
        return self.page.body

    def screenshot(self, timeout=None):
        return self.page.take()

    def scroll_into_view_if_needed(self, timeout=None):
        pass

    def click(self, timeout=None):
        self.page.clicked(self.kind)

    def evaluate(self, _js):
        self.page.clicked(self.kind)


class FakePage:
    """A login page whose canvas hands out `canvases` one screenshot at a
    time, and b"" once they run out — which is what ICICI's emptied QR tab
    looks like from here. `revival` is the code a click on the QR tab brings
    back. The page reports itself logged in once `finish_after_polls`
    screenshots have been taken, and that is what ends the wait.
    """

    def __init__(self, canvases, revival=b"", finish_after_polls=99):
        self.queue = deque(canvases)
        self.revival = revival
        self.finish_after_polls = finish_after_polls
        self.polls = 0
        self.clicks: list[str] = []
        self.url = "https://retailnetbanking.icici.bank.in/login-page"
        self.body = LOGGED_OUT

    def locator(self, selector):
        kind = ("canvas" if selector == probe._QR_CANVAS
                else "body" if selector == "body" else "tab")
        return FakeLocator(self, kind)

    def take(self) -> bytes:
        self.polls += 1
        if self.polls >= self.finish_after_polls:
            self.body = LOGGED_IN
        return self.queue.popleft() if self.queue else b""

    def clicked(self, kind: str) -> None:
        self.clicks.append(kind)
        if kind == "tab" and self.revival:
            self.queue.append(self.revival)

    def wait_for_timeout(self, _ms):
        pass

    def wait_for_selector(self, _selector, state=None, timeout=None):
        """The real page is waited on after a revival click; here the next
        screenshot already says whether a code came back."""
        return None


@pytest.fixture(autouse=True)
def fast_and_isolated(tmp_path, monkeypatch):
    """No real sleeping, no real output/, and a poll on every pass."""
    monkeypatch.setattr(probe.time, "sleep", lambda _s: None)
    monkeypatch.setattr(probe, "_QR_POLL_S", 0.0)
    monkeypatch.setattr(probe.config, "LOGIN_TIMEOUT", 60.0)
    monkeypatch.setattr(qr, "QR_PATH", tmp_path / "out" / "qr.png")
    monkeypatch.setattr(qr, "_published", "")


def _run(page) -> list[tuple[str, str]]:
    """Runs the wait and returns, for each time the status file would have
    been written, the path it was given and what the code said at that
    moment. Asserts the wait reported a login, so a loop that fails to end
    shows up here rather than as a hang."""
    published: list[tuple[str, str]] = []
    assert probe.wait_for_login(
        page, on_qr=lambda path: published.append((str(path), _published_payload())))
    return published


def test_each_new_code_is_republished_and_the_status_rewritten():
    page = FakePage([_code("A"), _code("A"), _code("B"), _code("B"), _code("C")],
                    finish_after_polls=5)
    published = _run(page)
    # Three codes, not five polls: the same canvas twice is not republished.
    assert [payload for _, payload in published] == [
        "RIB://Login?refNum=A", "RIB://Login?refNum=B", "RIB://Login?refNum=C"]
    assert {path for path, _ in published} == {str(qr.QR_PATH)}


def test_an_empty_tab_asks_for_a_new_code_and_publishes_it():
    page = FakePage([_code("A"), b"", b""], revival=_code("REVIVED"),
                    finish_after_polls=4)
    published = _run(page)
    assert page.clicks.count("tab") == 1
    assert [payload for _, payload in published] == [
        "RIB://Login?refNum=A", "RIB://Login?refNum=REVIVED"]


def test_one_empty_poll_between_codes_does_not_spend_a_revival():
    """The canvas can be caught mid-redraw; that is not an expired code."""
    page = FakePage([_code("A"), b"", _code("B"), b"", _code("C")],
                    revival=_code("UNWANTED"), finish_after_polls=5)
    assert len(_run(page)) == 3
    assert page.clicks == []


def test_it_stops_asking_after_the_cap():
    """An empty tab that stays empty is asked a fixed number of times, not
    once per poll for the rest of the wait."""
    page = FakePage([], finish_after_polls=99)
    assert _run(page) == []
    assert page.clicks.count("tab") == probe._QR_REVIVALS


def test_a_code_that_cannot_be_revived_is_cleared_and_reported():
    """Once ICICI stops drawing codes for good, the popup should go rather
    than keep offering one the phone will refuse."""
    expired: list[bool] = []
    page = FakePage([_code("A")], finish_after_polls=99)
    assert probe.wait_for_login(page, on_expired=lambda: expired.append(True))
    assert expired == [True]              # said once, not once per poll
    assert not qr.QR_PATH.exists()


def test_the_code_is_deleted_the_moment_login_succeeds():
    page = FakePage([_code("A")], finish_after_polls=1)
    assert len(_run(page)) == 1
    assert not qr.QR_PATH.exists()


def test_nothing_is_screenshotted_once_the_page_says_logged_in():
    """_QR_CANVAS matches any <canvas>, and the dashboard behind the login
    has charts."""
    page = FakePage([_code("A")])
    page.body = LOGGED_IN
    assert probe.wait_for_login(page) is True
    assert page.polls == 0
    assert not qr.QR_PATH.exists()


def test_a_wait_that_runs_out_leaves_the_code_for_the_caller(monkeypatch):
    """main.py deletes qr.png in its own finally, so a timeout and a cancel
    clean up by the same path."""
    monkeypatch.setattr(probe.config, "LOGIN_TIMEOUT", 0.0)
    page = FakePage([_code("A")])
    assert probe.wait_for_login(page) is False

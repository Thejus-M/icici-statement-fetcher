"""The login QR the widget shows has to decode, stay on the dark tile, and
be republished only when ICICI has actually drawn a new code.

zbarimg does not read a light-on-dark code directly (verified), so the
tests that check the rendered image invert it first, which is what a phone
camera does for an inverted code. Nothing here needs a live session: the
codes are made with qrencode, the same way ICICI's canvas would hand one
over as a screenshot.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import qr  # noqa: E402

PAYLOAD = "https://retailnetbanking.icici.bank.in/qr-login/TEST-TOKEN-0001"
OTHER_PAYLOAD = "https://retailnetbanking.icici.bank.in/qr-login/TEST-TOKEN-0002"

pytestmark = pytest.mark.skipif(
    shutil.which("zbarimg") is None or shutil.which("qrencode") is None,
    reason="zbarimg and qrencode are required",
)


def _canvas_png(payload: str) -> bytes:
    """A stand-in for ICICI's canvas screenshot: a plain dark-on-light code."""
    return subprocess.run(
        ["qrencode", "-t", "PNG", "-s", "6", "-o", "-", payload],
        capture_output=True, check=True,
    ).stdout


def _zbar(path: Path) -> str:
    result = subprocess.run(
        ["zbarimg", "--quiet", "--raw", str(path)], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _zbar_inverted(path: Path, tmp_path: Path) -> str:
    inverted = tmp_path / "inverted.png"
    ImageOps.invert(Image.open(path).convert("RGB")).save(inverted)
    return _zbar(inverted)


@pytest.fixture(autouse=True)
def published_in_tmp(tmp_path, monkeypatch):
    """Every test publishes into its own tmp dir, never the real output/,
    and starts with nothing published."""
    monkeypatch.setattr(qr, "QR_PATH", tmp_path / "out" / "qr.png")
    monkeypatch.setattr(qr, "_published", "")
    return qr.QR_PATH


# --- decoding ---------------------------------------------------------------

def test_decode_reads_the_canvas_screenshot_from_memory():
    """The undecoded screenshot never touches the disk: zbarimg takes it on
    stdin. If that ever stops working, publish() silently stops redrawing."""
    assert qr.decode(_canvas_png(PAYLOAD)) == PAYLOAD


def test_decode_returns_empty_for_something_that_is_not_a_code():
    assert qr.decode(b"not a png at all") == ""


# --- rendering --------------------------------------------------------------

def test_render_decodes_the_exact_payload(tmp_path):
    out = tmp_path / "qr.png"
    qr.render(PAYLOAD, out)
    assert _zbar_inverted(out, tmp_path) == PAYLOAD


def test_render_decodes_at_the_size_the_popup_shows(tmp_path):
    """QrCard draws it at 200px; the code has to survive that."""
    out = tmp_path / "qr.png"
    qr.render(PAYLOAD, out)
    small = tmp_path / "small.png"
    Image.open(out).convert("RGB").resize((200, 200), Image.NEAREST).save(small)
    assert _zbar_inverted(small, tmp_path) == PAYLOAD


def test_render_sits_on_the_tile_with_no_white(tmp_path):
    out = tmp_path / "qr.png"
    qr.render(PAYLOAD, out)
    image = Image.open(out).convert("RGB")
    assert image.getpixel((2, 2)) == qr._BG       # the margin is the tile colour
    colours = [colour for _, colour in image.getcolors(maxcolors=1 << 24)]
    assert not any(min(colour) > 240 for colour in colours)


def test_render_honours_the_widgets_colours(tmp_path, monkeypatch):
    """QR_FG / QR_BG swap the polarity as well as the colours, which is the
    way out if a phone ever refuses a light-on-dark code."""
    monkeypatch.setenv("QR_FG", "#000000")
    monkeypatch.setenv("QR_BG", "#ffffff")
    monkeypatch.setenv("QR_ACCENT", "#123456")
    out = tmp_path / "qr.png"
    qr.render(PAYLOAD, out)
    image = Image.open(out).convert("RGB")
    assert image.getpixel((2, 2)) == (255, 255, 255)
    assert _zbar(out) == PAYLOAD                  # dark-on-light: no inverting


def test_a_bad_colour_value_falls_back_instead_of_failing_a_login(monkeypatch):
    for bad in ("", "red", "#fff", "0b1628", "#0b16288"):
        monkeypatch.setenv("QR_BG", bad)
        assert qr._colour("QR_BG", qr._BG) == qr._BG


# --- publishing -------------------------------------------------------------

def test_publish_redraws_the_code_and_leaves_no_temp_file(published_in_tmp, tmp_path):
    png = _canvas_png(PAYLOAD)
    assert qr.publish(png) == published_in_tmp
    assert published_in_tmp.read_bytes() != png          # redrawn, not the screenshot
    assert _zbar_inverted(published_in_tmp, tmp_path) == PAYLOAD
    assert [p.name for p in published_in_tmp.parent.iterdir()] == ["qr.png"]


def test_publish_keeps_the_code_private():
    qr.publish(_canvas_png(PAYLOAD))
    assert qr.QR_PATH.stat().st_mode & 0o777 == 0o600


def test_the_same_code_screenshotted_again_is_not_republished():
    """The wait loop screenshots every couple of seconds. Rewriting an
    identical code would only make the widget reload the image."""
    png = _canvas_png(PAYLOAD)
    assert qr.publish(png) is not None
    assert qr.publish(png) is None
    assert qr.publish(_canvas_png(PAYLOAD)) is None      # a fresh screenshot of it


def test_a_redrawn_code_is_republished(tmp_path):
    qr.publish(_canvas_png(PAYLOAD))
    assert qr.publish(_canvas_png(OTHER_PAYLOAD)) is not None
    assert _zbar_inverted(qr.QR_PATH, tmp_path) == OTHER_PAYLOAD


def test_an_undecodable_screenshot_is_published_as_icici_drew_it(published_in_tmp, tmp_path):
    """Better the bank's own image, which still scans on the phone, than no
    code at all — a canvas caught mid-redraw decodes as nothing."""
    blank = tmp_path / "blank.png"
    Image.new("RGB", (40, 40), (255, 255, 255)).save(blank)
    assert qr.publish(blank.read_bytes()) == published_in_tmp
    assert published_in_tmp.read_bytes() == blank.read_bytes()


def test_nothing_is_published_for_a_missed_screenshot():
    assert qr.publish(b"") is None
    assert not qr.QR_PATH.exists()


def test_forget_removes_the_code_and_lets_the_next_one_through():
    png = _canvas_png(PAYLOAD)
    qr.publish(png)
    qr.forget()
    assert not qr.QR_PATH.exists()
    assert qr.publish(png) is not None               # not "unchanged": it is gone
    qr.forget()
    qr.forget()                                      # idempotent: main calls it twice

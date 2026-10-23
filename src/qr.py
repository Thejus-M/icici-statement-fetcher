"""The login QR the Transactions widget shows (output/qr.png).

ICICI draws its code into a <canvas> on the login form, so there is no
image URL to read: probe.capture_qr screenshots the canvas and hands the
PNG bytes here. The bytes are decoded with zbarimg and the code is redrawn
by `render` in the widget's colours, which is what the HDFC fetcher does
with the data: URL its page carries (see its src/login.py). The two
renderers are deliberate copies of one design rather than a shared import:
these repos stay independent of each other, the same way guard.py and
store.py are byte-identical copies.

Written through a temp file and renamed, so the widget never loads a
half-drawn image, and removed as soon as login ends — the code is a login
credential, not a capture worth keeping.

Colours come from QR_FG / QR_BG / QR_ACCENT / QR_ACCENT2 (#rrggbb only),
and default to the dark tile the widget's QrCard draws the code on, with
the finder rings in ICICI's own two theme colours (red outer ring, orange
inner dot — sampled from ICICI's own header logo, the same pair
IciciLogo.qml uses, rather than a single flat accent). Setting QR_FG to
the dark value and QR_BG to a light one renders the usual dark-on-light
code, should a phone ever refuse an inverted one.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
from pathlib import Path

from . import config

try:  # Pillow only redraws the code: without it ICICI's own PNG is shown.
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - requirements.txt installs it
    Image = ImageDraw = None

QR_PATH = config.OUTPUT_DIR / "qr.png"

# The widget's QrCard tile is #0B1628 and the code has to sit on it without
# a seam, so these are its colours. The rings are ICICI's own two theme
# colours — sampled from ICICI's real header logo (#BE2A2A, #EF7F1A), the
# same pair IciciLogo.qml's ellipse uses — not a single flat accent.
_FG = (214, 230, 247)
_BG = (11, 22, 40)
_ACCENT = (190, 42, 42)     # red, outer ring
_ACCENT2 = (239, 127, 26)   # orange, inner dot

_OUTPUT_PX = 680        # approximate final side of qr.png
_SUPERSAMPLE = 4        # draw at 4x, then downsample with a box filter (no overshoot)
_QUIET_MODULES = 3      # margin around the code, in modules
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# Fingerprint of the code currently in qr.png: the payload when it decoded,
# otherwise a hash of the bytes. ICICI redraws the canvas on its own timer,
# and this is how `publish` tells a new code from the same one screenshotted
# again.
_published = ""


def _colour(name: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """An #rrggbb environment value as RGB. Anything else is ignored rather
    than guessed at, so a bad value shows the default instead of failing a
    login."""
    value = (os.environ.get(name) or "").strip()
    if not _HEX.match(value):
        return default
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def decode(png: bytes) -> str:
    """The code's payload, or "" when zbarimg cannot read it (which is also
    what happens when zbarimg is not installed). zbarimg reads the PNG from
    stdin, so the undecoded screenshot never touches the disk."""
    try:
        result = subprocess.run(
            ["zbarimg", "--quiet", "--raw", "-"],
            input=png, capture_output=True, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.decode("utf-8", "replace").strip() if result.returncode == 0 else ""


def qr_matrix(payload: str) -> list[list[bool]]:
    """Module matrix (True = dark) at error correction H. qrencode renders a
    1px-per-module PNG with no margin; the matrix is read back from its
    pixels, which keeps the whole renderer to two command-line tools."""
    if Image is None:
        raise RuntimeError("Pillow is not installed")
    result = subprocess.run(
        ["qrencode", "-t", "PNG", "-s", "1", "-m", "0", "-l", "H", "-o", "-", payload],
        capture_output=True, check=True,
    )
    img = Image.open(io.BytesIO(result.stdout)).convert("RGBA").convert("L")
    if img.width != img.height:
        raise RuntimeError("qrencode returned a non-square matrix")
    px = img.load()
    return [[px[col, row] < 128 for col in range(img.width)] for row in range(img.height)]


def render(payload: str, out_path: Path) -> None:
    """Writes the widget's QR PNG for `payload` to `out_path`: rounded light
    modules on the dark tile, with the three finder rings in ICICI's own
    red-and-orange, not one flat accent. Nothing is drawn in the middle —
    no logo plate — so every module of the code stays on the tile."""
    if Image is None:
        raise RuntimeError("Pillow is not installed")
    matrix = qr_matrix(payload)
    n = len(matrix)
    total = n + 2 * _QUIET_MODULES
    m_final = max(1, round(_OUTPUT_PX / total))
    m = m_final * _SUPERSAMPLE
    side = total * m
    off = _QUIET_MODULES * m
    fg, bg = _colour("QR_FG", _FG), _colour("QR_BG", _BG)
    accent, accent2 = _colour("QR_ACCENT", _ACCENT), _colour("QR_ACCENT2", _ACCENT2)

    canvas = Image.new("RGB", (side, side), bg)
    draw = ImageDraw.Draw(canvas)
    finders = [(0, 0), (0, n - 7), (n - 7, 0)]

    def in_finder(row: int, col: int) -> bool:
        return any(fr <= row < fr + 7 and fc <= col < fc + 7 for fr, fc in finders)

    radius = int(0.3 * m)
    for row in range(n):
        for col in range(n):
            if not matrix[row][col] or in_finder(row, col):
                continue
            x0 = off + col * m
            y0 = off + row * m
            draw.rounded_rectangle((x0, y0, x0 + m - 1, y0 + m - 1), radius=radius, fill=fg)

    # Each finder is drawn as three nested rounded squares, not as its own
    # modules: the ring reads as one mark at the size the popup shows. Red
    # outer ring, orange inner dot — ICICI's two theme colours, the same
    # pair the mark's ellipse fades between, rather than one flat accent.
    for fr, fc in finders:
        x = off + fc * m
        y = off + fr * m
        draw.rounded_rectangle((x, y, x + 7 * m - 1, y + 7 * m - 1), radius=int(1.6 * m), fill=accent)
        draw.rounded_rectangle((x + m, y + m, x + 6 * m - 1, y + 6 * m - 1), radius=int(1.0 * m), fill=bg)
        draw.rounded_rectangle((x + 2 * m, y + 2 * m, x + 5 * m - 1, y + 5 * m - 1), radius=int(0.7 * m), fill=accent2)

    canvas.resize((total * m_final, total * m_final), Image.BOX).save(out_path, format="PNG")


def publish(png: bytes) -> Path | None:
    """Puts `png`'s code in qr.png for the widget, redrawn when it decodes
    and saved as ICICI drew it when it does not.

    Returns the path, or None when this is the same code qr.png already
    holds — ICICI's canvas is screenshotted every couple of seconds while
    you scan, and republishing an identical code would only make the widget
    reload the image for nothing.
    """
    global _published
    if not png:
        return None
    payload = decode(png)
    key = payload or f"sha256:{hashlib.sha256(png).hexdigest()}"
    if key == _published:
        return None

    QR_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = QR_PATH.with_name(f".{QR_PATH.name}.tmp")
    try:
        if payload:
            render(payload, tmp)
        else:
            tmp.write_bytes(png)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        # A missing qrencode, a missing Pillow, a payload qrencode will not
        # take: the screenshot still scans, so show it rather than nothing.
        print(f"  could not redraw the QR ({type(exc).__name__}); showing ICICI's own image.",
              flush=True)
        try:
            tmp.write_bytes(png)
        except OSError:
            tmp.unlink(missing_ok=True)
            return None
    os.chmod(tmp, 0o600)
    os.replace(tmp, QR_PATH)
    _published = key
    return QR_PATH


def forget() -> None:
    """Removes qr.png. Called the moment login succeeds and again when the
    run ends: a login code left on disk is a credential left on disk."""
    global _published
    _published = ""
    QR_PATH.unlink(missing_ok=True)

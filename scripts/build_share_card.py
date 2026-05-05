#!/usr/bin/env python3
"""Generate docs/post/assets/share-final.png — the og:image / twitter:image
shown when the site is shared. Type-first poster: "ALL THOCK / NO CLACK"
with the keycap icon as a small accent.

Run after editing copy or the keycap icon:
    python3 scripts/build_share_card.py

Downloads Fraunces (the site's display serif) on first run and caches it
under /tmp/keyboard-wire-fonts/. Output is a 1200x630 PNG suitable for
Twitter/X summary_large_image.
"""
from __future__ import annotations

import pathlib
import urllib.request

from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parent.parent
ICON = ROOT / "docs" / "post" / "assets" / "icon-final.png"
OUT = ROOT / "docs" / "post" / "assets" / "share-final.png"

FONT_CACHE = pathlib.Path("/tmp/keyboard-wire-fonts")
FRAUNCES_URL = (
    "https://fonts.gstatic.com/s/fraunces/v38/"
    "6NUh8FyLNQOQZAnv9bYEvDiIdE9Ea92uemAk_WBq8U_9v0c2Wa0K7iN7hzFUPJH58nib"
    "1603gg7S2nfgRYIcHhyjDg.ttf"
)

W, H = 1200, 630
BG = (20, 17, 13)
INK = (240, 232, 215)
INK_SOFT = (200, 190, 170)
ACCENT = (232, 93, 74)


def fetch_font() -> str:
    FONT_CACHE.mkdir(parents=True, exist_ok=True)
    p = FONT_CACHE / "Fraunces-Black.ttf"
    if not p.exists():
        urllib.request.urlretrieve(FRAUNCES_URL, p)
    return str(p)


def fit_size(font_path: str, text: str, max_width: int,
             start: int = 240, min_size: int = 120) -> int:
    size = start
    while size > min_size:
        f = ImageFont.truetype(font_path, size=size)
        bbox = f.getbbox(text)
        if (bbox[2] - bbox[0]) <= max_width:
            return size
        size -= 4
    return min_size


def main() -> None:
    fraunces = fetch_font()
    img = Image.new("RGB", (W, H), BG)

    # Soft radial vignette so the corners darken naturally.
    vignette = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vignette)
    for r in range(0, 800, 8):
        alpha = int(80 * (1 - r / 800))
        vd.ellipse(
            (W / 2 - r, H / 2 - r * 0.6, W / 2 + r, H / 2 + r * 0.6),
            fill=255 - alpha,
        )
    overlay = Image.new("RGB", (W, H), (0, 0, 0))
    img = Image.composite(img, overlay, vignette.filter(ImageFilter.GaussianBlur(80)))
    draw = ImageDraw.Draw(img)

    side_pad = 80
    sz_top = fit_size(fraunces, "ALL THOCK", W - 2 * side_pad, start=240)
    sz_bot = fit_size(fraunces, "NO CLACK", W - 2 * side_pad, start=240)
    size = min(sz_top, sz_bot)
    main_font = ImageFont.truetype(fraunces, size=size)

    b_top = main_font.getbbox("ALL THOCK")
    b_bot_no = main_font.getbbox("NO ")
    b_bot_clack = main_font.getbbox("CLACK")
    line_h = b_top[3] - b_top[1]
    gap = int(size * 0.08)
    total_h = line_h * 2 + gap

    y_top = int((H - total_h) / 2 - 40) - b_top[1]
    y_bot = y_top + line_h + gap + (b_top[1] - b_bot_no[1])

    x_top = (W - (b_top[2] - b_top[0])) // 2 - b_top[0]
    draw.text((x_top, y_top), "ALL THOCK", font=main_font, fill=INK)

    combined_w = (b_bot_no[2] - b_bot_no[0]) + (b_bot_clack[2] - b_bot_clack[0])
    x_no = (W - combined_w) // 2 - b_bot_no[0]
    draw.text((x_no, y_bot), "NO ", font=main_font, fill=INK)
    draw.text(
        (x_no + (b_bot_no[2] - 0), y_bot),
        "CLACK",
        font=main_font,
        fill=ACCENT,
    )

    eyebrow_font = ImageFont.truetype(fraunces, size=28)
    eyebrow = "K E Y B O A R D   N E W S W I R E"
    b_eye = eyebrow_font.getbbox(eyebrow)
    draw.text(
        ((W - (b_eye[2] - b_eye[0])) // 2 - b_eye[0], 80),
        eyebrow,
        font=eyebrow_font,
        fill=INK_SOFT,
    )

    url_font = ImageFont.truetype(fraunces, size=34)
    url = "keyboard-newswire.com"
    b_url = url_font.getbbox(url)
    url_y = H - 80 - (b_url[3] - b_url[1])
    draw.text(
        ((W - (b_url[2] - b_url[0])) // 2 - b_url[0], url_y),
        url,
        font=url_font,
        fill=INK_SOFT,
    )

    rule_w = 80
    rule_y = url_y - 22
    draw.line(
        [((W - rule_w) // 2, rule_y), ((W + rule_w) // 2, rule_y)],
        fill=ACCENT,
        width=3,
    )

    icon = Image.open(ICON).convert("RGBA")
    icon_size = 110
    icon = icon.resize((icon_size, icon_size), Image.LANCZOS)
    img.paste(icon, (60, H - icon_size - 60), icon)

    img.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()

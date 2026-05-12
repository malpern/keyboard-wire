#!/usr/bin/env python3
"""Fetch a representative thumbnail image for each item, save to docs/img/.

Heuristics:
  HN items     → fetch item.url, parse og:image / twitter:image
  Reddit items → fetch <reddit-url>.json, prefer preview.images[0].source.url;
                 fall back to fetching the post's external link (url_overridden_by_dest)
                 and parsing its og:image

Validates by downloading and inspecting size with Pillow. Skips images < 200×200.
Crops/resizes to 320×320 and saves as JPEG (quality 85) for compatibility.

Reads items JSON on stdin or argv[1]. Writes items JSON on stdout with
`image` field added (relative path under docs/, e.g. "img/<id>.jpg") when
fetch succeeded. Original item is unchanged when no image found.
"""
import io
import json
import pathlib
import re
import subprocess
import sys
from urllib.parse import urljoin, urlparse

from PIL import Image, ImageOps

ROOT = pathlib.Path(__file__).resolve().parent.parent
IMG_DIR = ROOT / "docs" / "img"
UA = "keyboard-wire/1.0 (+https://malpern.github.io/keyboard-wire)"

OG_IMAGE_RE = re.compile(
    r'<meta\s+(?:property|name)=["\'](og:image|twitter:image|twitter:image:src)["\']\s+'
    r'content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
OG_IMAGE_RE2 = re.compile(
    r'<meta\s+content=["\']([^"\']+)["\']\s+'
    r'(?:property|name)=["\'](og:image|twitter:image|twitter:image:src)["\']',
    re.IGNORECASE,
)


def http_get(url: str, timeout: int = 12, max_bytes: int = 800_000) -> bytes:
    """GET via curl, returning at most max_bytes."""
    try:
        r = subprocess.run(
            ["curl", "-sSL", "-A", UA, "--max-time", str(timeout),
             "--max-filesize", str(max_bytes * 4), "-o", "-", url],
            capture_output=True, timeout=timeout + 4,
        )
        if r.returncode != 0:
            return b""
        return r.stdout[:max_bytes]
    except Exception:
        return b""


def http_get_full(url: str, timeout: int = 20) -> bytes:
    """GET full body — used for image downloads."""
    try:
        r = subprocess.run(
            ["curl", "-sSL", "-A", UA, "--max-time", str(timeout), "-o", "-", url],
            capture_output=True, timeout=timeout + 4,
        )
        if r.returncode != 0:
            return b""
        return r.stdout
    except Exception:
        return b""


def extract_og_image(html: str, base_url: str) -> str | None:
    """Pull og:image / twitter:image from raw HTML."""
    if not html:
        return None
    m = OG_IMAGE_RE.search(html)
    if not m:
        m = OG_IMAGE_RE2.search(html)
        if m:
            url = m.group(1)
        else:
            return None
    else:
        url = m.group(2)
    url = url.strip()
    if not url:
        return None
    # Resolve relative URLs
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        p = urlparse(base_url)
        url = f"{p.scheme}://{p.netloc}{url}"
    elif not url.startswith(("http://", "https://")):
        url = urljoin(base_url, url)
    return url


def reddit_thumbnail(reddit_url: str) -> tuple[str | None, str | None]:
    """Return (image_url, fallback_article_url)."""
    json_url = reddit_url.rstrip("/") + ".json?limit=1"
    raw = http_get(json_url, max_bytes=600_000)
    if not raw:
        return None, None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None, None
    try:
        post = data[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        return None, None

    # 1) Modern gallery / image-post format: media_metadata
    md = post.get("media_metadata") or {}
    for k, v in md.items():
        if not isinstance(v, dict) or v.get("status") != "valid":
            continue
        # 's' is the largest source variant
        s = v.get("s") or {}
        u = s.get("u") or s.get("gif")
        if u:
            return u.replace("&amp;", "&"), None

    # 2) Standard preview.images (link posts that Reddit pre-rendered)
    preview = post.get("preview", {})
    images = preview.get("images") or []
    if images:
        src = images[0].get("source", {}).get("url")
        if src:
            return src.replace("&amp;", "&"), None

    # 3) External-link post — defer to og:image of the linked article
    ext = post.get("url_overridden_by_dest") or post.get("url")
    if ext and "reddit.com" not in ext and ext.startswith(("http://", "https://")):
        return None, ext

    # 4) Last-ditch: post thumbnail (often small, but better than nothing)
    thumb = post.get("thumbnail")
    if thumb and thumb.startswith(("http://", "https://")):
        return thumb, None

    return None, None


# Geekhack chrome — board theme images, smileys, avatars — that we
# must skip when scanning a thread page for OP product photos.
_GEEKHACK_CHROME = re.compile(
    r"(?:/Themes/|/Smileys/|/avatar|sigpic|useroff|useron|"
    r"normal_post|sticky|new_some|toggle|upshrink|banner|"
    r"thumbsup|thumbsdown)",
    re.IGNORECASE,
)


def geekhack_first_op_image(thread_url: str) -> str | None:
    """Fetch a Geekhack thread page and return the first content image
    URL — designer's OP photos, hosted on imgur / postimg / etc., not
    Geekhack chrome. Returns None on failure.

    v2.0: returns one image. Step 1b will return a list of N images for
    the carousel; we crop to one for now to keep the change small.
    """
    try:
        html_text = http_get(thread_url).decode("utf-8", errors="replace")
    except Exception:
        return None
    # Match every src= URL pointing to a real image extension. We do
    # the chrome filter and host check ourselves below.
    pattern = re.compile(
        r'<img[^>]*\bsrc=["\'](https?://[^"\']+\.(?:jpe?g|png|webp|gif)'
        r'(?:\?[^"\']*)?)["\']',
        re.IGNORECASE,
    )
    for m in pattern.finditer(html_text):
        url = m.group(1)
        host = (urlparse(url).hostname or "").lower()
        # Skip Geekhack-hosted chrome, even if it matches the extension regex.
        if host.endswith("geekhack.org"):
            continue
        if _GEEKHACK_CHROME.search(url):
            continue
        return url
    return None


def discover_image_url(item: dict) -> str | None:
    source = item.get("source")
    if source == "hn" or source == "email" or source == "kbdnews":
        url = item.get("url")
        if not url or url.startswith("https://mail.google.com/"):
            return None
        html = http_get(url).decode("utf-8", errors="replace")
        return extract_og_image(html, url)
    if source == "reddit":
        ru = item.get("url")
        if not ru:
            return None
        img, fallback = reddit_thumbnail(ru)
        if img:
            return img
        if fallback:
            html = http_get(fallback).decode("utf-8", errors="replace")
            return extract_og_image(html, fallback)
        return None
    if source == "geekhack":
        url = item.get("url")
        if not url:
            return None
        return geekhack_first_op_image(url)
    return None


def download_and_save(image_url: str, dest: pathlib.Path, *,
                      target_size: tuple[int, int] = (320, 320),
                      square_crop: bool = True,
                      quality: int = 82) -> bool:
    """Download, validate, optionally crop, save JPEG.

    News items keep the default 320×320 square crop (thumb in the
    item card's right margin — CSS sizes it explicitly). GB carousel
    items pass `square_crop=False` with a larger `target_size` so
    Pillow `thumbnail`-shrinks to fit while preserving aspect ratio.
    CSS does the final 4:3 framing via `object-fit: cover`, so the
    source image stays sharp on retina displays.
    """
    raw = http_get_full(image_url)
    if not raw or len(raw) < 1000:
        return False
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return False
    # Reject too-small or non-image content.
    w, h = img.size
    if min(w, h) < 200:
        return False
    # Convert to RGB (handle RGBA / palette).
    if img.mode != "RGB":
        bg = Image.new("RGB", img.size, (253, 252, 249))  # site bg
        if img.mode in ("RGBA", "LA"):
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
    if square_crop:
        # Center-crop to a square thumb (news cards).
        img = ImageOps.fit(img, target_size, method=Image.LANCZOS)
    else:
        # Preserve aspect ratio; shrink to fit within target_size.
        img.thumbnail(target_size, Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="JPEG", quality=quality,
             optimize=True, progressive=True)
    return True


def slug_for_item(item: dict) -> str:
    s = item.get("id") or ""
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-")
    return s or "item"


MAX_GB_IMAGES = 6  # cap per-item to keep carousel tidy + bandwidth bounded

# GB carousel images: keep aspect ratio, cap longest side at 1280px,
# higher JPEG quality. Display size on /groupbuys/ is at most ~720px
# wide, but on retina screens (devicePixelRatio 2-3) the browser wants
# 1440-2160px of source. 1280 is the pragmatic cap — sharp on most
# devices, ~100KB per image, 6 images per item = ~600KB total.
GB_TARGET_SIZE = (1280, 1280)
GB_QUALITY = 88


def fetch_for(item: dict) -> dict:
    # Multi-image path: pilot pre-discovered a list of remote URLs
    # (currently only geekhack_pilot step 1b). Download each into a
    # numbered local crop.
    #
    # Idempotency: re-download only when the existing `images` list
    # doesn't match the numbered naming convention this branch uses
    # (`<slug>-<N>.jpg`). That way a fresh re-emit of `images_remote`
    # over a stale single-image entry (legacy single-fetch path that
    # produced `<slug>.jpg`) correctly upgrades to the carousel,
    # while normal runs that already have `<slug>-0.jpg` skip.
    remotes = item.get("images_remote") or []
    if remotes:
        slug = slug_for_item(item)
        existing = item.get("images") or []
        expected_prefix = f"img/{slug}-"
        # "Done" only if every existing path matches the numbered
        # naming convention AND the count matches what we'd download
        # this pass (i.e. min(remotes, MAX_GB_IMAGES)). This handles
        # the case where a prior run downloaded only 1 image but the
        # newly-extracted images_remote now has many.
        expected_count = min(len(remotes), MAX_GB_IMAGES)
        already_numbered = (
            existing
            and len(existing) == expected_count
            and all(p.startswith(expected_prefix) for p in existing)
        )
        if not already_numbered:
            local_paths: list[str] = []
            for idx, url in enumerate(remotes[:MAX_GB_IMAGES]):
                dest = IMG_DIR / f"{slug}-{idx}.jpg"
                if download_and_save(url, dest,
                                     target_size=GB_TARGET_SIZE,
                                     square_crop=False,
                                     quality=GB_QUALITY):
                    local_paths.append(f"img/{dest.name}")
            if local_paths:
                item = dict(item)
                item["images"] = local_paths
                # Back-compat: callers that still read `item.image`
                # (e.g. share-card tile views, future single-image
                # consumers) get the first frame.
                item["image"] = local_paths[0]
            return item
        # Already have the numbered set — pass through unchanged.
        return item

    # Single-image path (legacy / non-GB sources).
    if item.get("image"):
        return item  # already have one (idempotent)
    img_url = discover_image_url(item)
    if not img_url:
        return item
    slug = slug_for_item(item)
    dest = IMG_DIR / f"{slug}.jpg"
    if download_and_save(img_url, dest):
        item = dict(item)
        item["image"] = f"img/{dest.name}"
    return item


def main():
    if len(sys.argv) > 1 and sys.argv[1] != "-":
        items = json.loads(open(sys.argv[1]).read())
    else:
        items = json.loads(sys.stdin.read())
    if not isinstance(items, list):
        sys.stderr.write("expected JSON array\n")
        sys.exit(1)
    out = []
    for i, item in enumerate(items):
        sys.stderr.write(f"  image {i+1}/{len(items)}: {item.get('title','')[:50]}\n")
        out.append(fetch_for(item))
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

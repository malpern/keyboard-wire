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


def discover_image_url(item: dict) -> str | None:
    source = item.get("source")
    if source == "hn":
        url = item.get("url")
        if not url:
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
    return None


def download_and_save(image_url: str, dest: pathlib.Path) -> bool:
    """Download, validate, crop to 320×320, save JPEG. Returns True on success."""
    raw = http_get_full(image_url)
    if not raw or len(raw) < 1000:
        return False
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return False
    # Reject too-small or non-image content
    w, h = img.size
    if min(w, h) < 200:
        return False
    # Convert to RGB (handle RGBA / palette)
    if img.mode != "RGB":
        bg = Image.new("RGB", img.size, (253, 252, 249))  # site bg
        if img.mode in ("RGBA", "LA"):
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
    # ImageOps.fit gives us a center-cropped square
    img = ImageOps.fit(img, (320, 320), method=Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="JPEG", quality=82, optimize=True, progressive=True)
    return True


def slug_for_item(item: dict) -> str:
    s = item.get("id") or ""
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-")
    return s or "item"


def fetch_for(item: dict) -> dict:
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

#!/usr/bin/env python3
"""KBD.news ingest — parse https://kbd.news/rss.xml into the standard item
schema used by the rest of the keyboard-wire pipeline.

KBD.news is a hand-curated weekly e-zine by Tamás Dövényi-Nagy (u/dovenyi)
covering ergo/DIY mechanical keyboards. Each <item> in the RSS is one of
his curated posts; we treat his post URL as the canonical item URL (his
writeup IS the source — the underlying project link lives inside the post
body, not in the RSS).

Output: JSON array on stdout, suitable for tag_items.py → rewrite_titles.py
→ fetch_images.py → append_day.py.

Window: last 24h (matches firmware-watch + email pipelines). Skips "Behind
the scenes" weekly meta-posts since those summarize items we'd already have
ingested individually.

Usage:
  kbdnews_pilot.py                       # default 24h window
  kbdnews_pilot.py --hours 48            # custom window
  kbdnews_pilot.py --dry-run             # print summary instead of JSON
"""
import argparse
import datetime
import email.utils
import html as html_lib
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

FEED_URL = "https://kbd.news/rss.xml"
USER_AGENT = "keyboard-wire/1.0 (+https://keyboard-newswire.com)"

# Skip filter: weekly meta-posts that summarize content we already have
SKIP_TITLE_PATTERNS = [
    re.compile(r"^Behind the scenes", re.IGNORECASE),
]


def parse_pubdate(s: str) -> datetime.datetime | None:
    """RSS pubDate → UTC datetime (or None on failure)."""
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def post_id_from_url(url: str) -> str | None:
    """Extract trailing post number from a kbd.news URL.

    e.g. https://kbd.news/Levels54-2851.html → "2851"
    """
    m = re.search(r"-(\d+)\.html?$", url or "")
    return m.group(1) if m else None


def strip_html(s: str) -> str:
    """Strip HTML tags, decode entities, collapse whitespace.

    Also drops a leading <img> tag (the RSS description starts with one and
    we already capture the image URL separately via <enclosure>).
    """
    if not s:
        return ""
    # Drop a single leading <img ...> if present
    s = re.sub(r"^\s*<img\b[^>]*>\s*", "", s, count=1)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_lib.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fetch_feed() -> str:
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_feed(xml_text: str, since: datetime.datetime) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = parse_pubdate(it.findtext("pubDate") or "")
        desc = it.findtext("description") or ""

        if not title or not link:
            continue
        if pub and pub < since:
            continue
        if any(p.search(title) for p in SKIP_TITLE_PATTERNS):
            continue

        pid = post_id_from_url(link)
        if not pid:
            # Without a stable id, dedup would break; skip rather than guess.
            sys.stderr.write(f"  skip (no id in url): {link}\n")
            continue

        # Enclosure (image URL) — we don't download here; fetch_images.py
        # follows the kbd.news post's og:image which equals this anyway.
        enclosure = it.find("enclosure")
        image_url = (enclosure.get("url") if enclosure is not None else None) or None

        takeaway = strip_html(desc)

        item = {
            "id": f"kbdnews-{pid}",
            "title": title,
            "url": link,
            "discussion_url": link,
            "source": "kbdnews",
            "subreddit": None,
            "via": "KBD.news",
            "score": None,
            "comments": None,
            "category": "breaking",
            "takeaway": takeaway,
        }
        if image_url:
            # Pass the enclosure URL through as a hint; fetch_images.py will
            # re-derive from og:image (which is the same) and download/crop.
            item["image_hint"] = image_url

        out.append(item)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24,
                    help="lookback window in hours (default: 24)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print human-readable summary instead of JSON")
    args = ap.parse_args()

    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=args.hours)

    try:
        xml_text = fetch_feed()
    except Exception as e:
        sys.stderr.write(f"feed fetch failed: {e}\n")
        sys.exit(1)

    items = parse_feed(xml_text, since)

    if args.dry_run:
        print(f"kbd.news: {len(items)} items in last {args.hours}h")
        for it in items:
            print(f"  - [{it['id']}] {it['title']}")
            print(f"    {it['url']}")
            if it.get("takeaway"):
                print(f"    {it['takeaway'][:120]}")
        return

    json.dump(items, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stderr.write(f"kbd.news: emitted {len(items)} items in last {args.hours}h\n")


if __name__ == "__main__":
    main()

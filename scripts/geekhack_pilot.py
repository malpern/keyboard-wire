#!/usr/bin/env python3
"""Geekhack ingest — pull boards 70 (Group Buys) + 132 (Interest Checks)
into the standard item schema.

Geekhack's RSS emits *posts*, not threads. Every reply produces a new
<item>. We want one item per thread (per the first time it's seen),
keyed by the `topic=<NNN>` segment in each post URL. State lives in
`data/geekhack_seen.json` as a sorted array of thread IDs.

Pipeline note: the driver should SKIP `rewrite_titles.py` for these
items so `[GB] GMK Gregory 2` style headlines stay canonical.

Output: JSON array on stdout, ready for tag_items.py → fetch_images.py
→ append_day.py.

Usage:
  geekhack_pilot.py                  # fetch both boards, update state file
  geekhack_pilot.py --dry-run        # print summary, no state write
  geekhack_pilot.py --feed-file X.xml --board 70 --no-state
                                     # offline test against a local feed
"""
import argparse
import datetime
import email.utils
import html as html_lib
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "geekhack_seen.json"
# Per-URL ETag / Last-Modified cache; persists between cron runs so
# quiet days return 304 Not Modified instead of re-downloading the
# full RSS XML. See docs/GB_IC_FEED.md "Step 1c".
HTTP_CACHE_FILE = ROOT / "data" / "geekhack_http_cache.json"

USER_AGENT = "keyboard-wire/1.0 (+https://keyboard-newswire.com)"

BOARDS = {
    70: "Group Buys",
    132: "Interest Checks",
}


def feed_url(board: int) -> str:
    return f"https://geekhack.org/index.php?action=.xml;type=rss;board={board}"


def fetch_feed(url: str) -> bytes | None:
    """Return raw bytes from the RSS feed using conditional GET. On
    `304 Not Modified` returns None — caller should treat as "no new
    items, exit quietly" (since RSS unchanged ⇒ no new threads).

    Geekhack serves ISO-8859-1; the caller passes the bytes straight
    to ET.fromstring which honors the XML encoding decl.

    Note (2026-05-12): Geekhack currently sends a constant
    Last-Modified of 2018-09-06 for every RSS response and no ETag.
    Their server doesn't actually do conditional-response logic, so
    the 304 path won't trigger in practice — but we still send the
    correct headers (signals politeness, costs nothing) and the cache
    plumbing is ready for any upstream that does honor it (kbd.news
    being the obvious next candidate)."""
    import http_polite
    status, body = http_polite.conditional_get(
        url, HTTP_CACHE_FILE, timeout=20, user_agent=USER_AGENT,
    )
    if status == 304:
        return None
    if status != 200 or body is None:
        # Raise so the existing fail path (sys.exit(1) in main) triggers.
        raise RuntimeError(f"feed HTTP {status} for {url}")
    return body


def thread_id_from_url(url: str) -> str | None:
    """Extract NNN from geekhack URLs like
    https://geekhack.org/index.php?topic=126649.msg3215977#msg3215977
    """
    m = re.search(r"[?&;]topic=(\d+)", url or "")
    return m.group(1) if m else None


def thread_root_url(thread_id: str) -> str:
    assert thread_id and str(thread_id).isdigit(), \
        f"thread_id must be a numeric string, got {thread_id!r}"
    return f"https://geekhack.org/index.php?topic={thread_id}.0"


def clean_title(raw: str) -> str:
    """Strip leading 'Re: ' chains. Preserve [GB] / [IC] prefix."""
    t = (raw or "").strip()
    while True:
        m = re.match(r"^Re:\s*", t, re.IGNORECASE)
        if not m:
            break
        t = t[m.end():]
    return t.strip()


def parse_type(title: str) -> str | None:
    """Return 'GB' or 'IC' if the title starts with that bracketed prefix."""
    m = re.match(r"^\s*\[(GB|IC)\]", title, re.IGNORECASE)
    return m.group(1).upper() if m else None


def strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_lib.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_pubdate(s: str) -> datetime.datetime | None:
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def parse_feed(xml_text, board: int) -> list[dict]:
    """xml_text may be bytes (preferred — ET decodes via XML decl) or str."""
    """Parse one board's RSS into a list of {thread_id, title, url,
    takeaway, pubdate} dicts — one per *post*. Caller dedupes by
    thread_id, keeping the earliest pubdate.
    """
    root = ET.fromstring(xml_text)
    out = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = it.findtext("description") or ""
        pub = parse_pubdate(it.findtext("pubDate") or "")

        if not title or not link:
            continue
        tid = thread_id_from_url(link)
        if not tid:
            sys.stderr.write(f"  skip (no topic id in url): {link}\n")
            continue

        out.append({
            "thread_id": tid,
            "title": clean_title(title),
            "raw_url": link,
            "takeaway": strip_html(desc),
            "pubdate": pub,
            "board": board,
        })
    return out


def load_state() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text())
        return set(str(x) for x in data)
    except Exception as e:
        sys.stderr.write(f"warn: could not read state file: {e}\n")
        return set()


def save_state(seen: set[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(sorted(seen, key=lambda s: int(s) if s.isdigit() else s),
                   indent=2) + "\n"
    )


# ─── Step 1b: per-thread enrichment ─────────────────────────────────
#
# RSS gives us title + per-post link + reply body. The thread page
# itself has the OP body, the views count, and all OP images. We
# fetch each newly-seen thread page exactly once (state file keys
# off thread_id, so already-emitted threads are never re-scraped)
# with a 1s throttle between fetches. See docs/GB_IC_FEED.md
# "Step 1b" for the rationale.

# Geekhack chrome (theme images, smileys, avatars, post-status icons)
# we must skip when collecting OP body images.
_GEEKHACK_CHROME_RE = re.compile(
    r"(?:/Themes/|/Smileys/|/avatar|sigpic|useroff|useron|"
    r"normal_post|sticky|new_some|toggle|upshrink|banner|"
    r"thumbsup|thumbsdown)",
    re.IGNORECASE,
)

# OP body image extensions we trust enough to download.
_IMG_EXT_RE = re.compile(
    r"\.(jpe?g|png|webp|gif)(?:\?[^\"']*)?$", re.IGNORECASE,
)

# Geekhack-native attachment URLs. Designers using the forum's own
# uploader (instead of imgur / postimg) generate URLs of this shape;
# they're legitimate OP photos even though the host is geekhack.org.
# Real attachment URLs end with `;image`. Avatars use the same
# `action=dlattach` prefix but end with `;type=avatar` — those are
# chrome, not OP content.
_GEEKHACK_DLATTACH_RE = re.compile(
    r"action=dlattach[^\"']*;image(?:[^a-z]|$)", re.IGNORECASE,
)


def _is_op_image(url: str) -> bool:
    """True if `url` looks like an OP-body content image (not chrome)."""
    if not url:
        return False
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if _GEEKHACK_CHROME_RE.search(url):
        return False
    # Geekhack-hosted native attachments are OP content; the URL has
    # no file extension (it's a PHP endpoint) so the extension check
    # is skipped for these.
    if host.endswith("geekhack.org"):
        return bool(_GEEKHACK_DLATTACH_RE.search(url))
    # Off-site host — require a real image extension.
    if not _IMG_EXT_RE.search(url):
        return False
    return True


def parse_thread_html(html_text: str) -> dict:
    """Pure parser for a Geekhack thread page. Returns a dict with:

    - `views`: int | None              — "(Read N times)" header line
    - `replies`: int | None            — highest 'Reply #N' seen on page
                                         (lower-bound for multi-page
                                         threads; rare for new GBs)
    - `images`: [str]                  — OP-body image URLs, in order,
                                         deduplicated, chrome filtered
    - `op_body`: str | None            — text content of the first post
                                         (OP), HTML-stripped, collapsed

    Pure — no I/O, monkeypatch-free testing.
    """
    out = {
        "views": None,
        "replies": None,
        "images": [],
        "op_body": None,
    }

    # Views — "Topic: ... (Read 9708 times)" appears in <title> and
    # also in a content header. Either form works.
    m = re.search(r"\(Read\s+([0-9,]+)\s+times?\)", html_text)
    if m:
        try:
            out["views"] = int(m.group(1).replace(",", ""))
        except ValueError:
            pass

    # Replies — SMF labels each reply post "Reply #N" (N is 1-indexed).
    # The largest N visible on the page is a lower bound. Long threads
    # paginate, so the count saturates at "page size minus 1" — for
    # newly-emitted GB threads this is rarely a problem (most have <50
    # posts at first emit), and the user-visible engagement signal is
    # still directionally correct.
    reply_nums = [int(x) for x in re.findall(r"Reply\s*#(\d+)", html_text)]
    if reply_nums:
        out["replies"] = max(reply_nums)

    # OP body — the first post on the page. SMF wraps each post in a
    # `<div class="post">…</div>` block. We grab the first one's text.
    first_post = re.search(
        r'<div class="post">(.*?)</div>\s*<div class="moderatorbar',
        html_text, re.DOTALL,
    )
    if not first_post:
        first_post = re.search(
            r'<div class="post">(.*?)</div>', html_text, re.DOTALL,
        )
    op_html = first_post.group(1) if first_post else None

    if op_html:
        # Collect OP-body images, in order, deduplicated.
        seen = set()
        for m in re.finditer(
            r'<img[^>]*\bsrc=["\'](https?://[^"\']+)["\']',
            op_html, re.IGNORECASE,
        ):
            # `&amp;` in href/src attrs must be decoded for the real
            # request URL — Geekhack's dlattach URLs are full of them.
            u = html_lib.unescape(m.group(1))
            if _is_op_image(u) and u not in seen:
                seen.add(u)
                out["images"].append(u)

        # Strip HTML for the OP body text. Geekhack quotes are wrapped
        # in <div class="quoteheader"> + <blockquote>; we drop those
        # blocks entirely (they're someone else's content quoted into
        # the OP, not the OP's own description).
        body = re.sub(
            r'<div class="quoteheader[^"]*">.*?</blockquote>',
            ' ', op_html, flags=re.DOTALL,
        )
        body = re.sub(r"<[^>]+>", " ", body)
        body = html_lib.unescape(body)
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            out["op_body"] = body

    return out


def fetch_thread_metadata(thread_id: str) -> dict | None:
    """Fetch a Geekhack thread root page and return parse_thread_html's
    output. None on HTTP failure (caller emits the item anyway with
    whatever data the RSS gave us — partial enrichment beats silent
    loss)."""
    url = thread_root_url(thread_id)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
    except Exception as e:
        sys.stderr.write(f"  thread {thread_id} scrape failed: {e}\n")
        return None
    # Geekhack declares ISO-8859-1 but actually serves Windows-1252
    # (Microsoft's superset that includes em-dashes, smart quotes,
    # etc.). Decoding as strict ISO-8859-1 turns \x97 — the real em-
    # dash byte — into an invisible control char, breaking text like
    # "reinventing—shaping". cp1252 handles both correctly.
    try:
        text = raw.decode("cp1252")
    except Exception:
        text = raw.decode("utf-8", errors="replace")
    return parse_thread_html(text)


def enrich_items(items: list[dict], throttle: float = 1.0) -> None:
    """Mutate items in place: add views/replies/images_remote/takeaway
    from per-thread scrapes. 1s throttle between fetches by default.
    Politeness: bounded by `len(items)` which equals the number of
    newly-seen threads (state file keeps re-scrapes out).
    """
    for i, it in enumerate(items):
        tid = it["id"].split("-", 1)[1] if "-" in it["id"] else None
        if not tid:
            continue
        sys.stderr.write(
            f"  scraping {i + 1}/{len(items)}: thread {tid}\n"
        )
        meta = fetch_thread_metadata(tid)
        if meta is None:
            continue
        if meta["views"] is not None:
            it["score"] = meta["views"]
        if meta["replies"] is not None:
            it["comments"] = meta["replies"]
        if meta["images"]:
            it["images_remote"] = meta["images"]
        if meta["op_body"]:
            # OP body is the real description — much better signal than
            # the latest-reply text the RSS gave us. Truncate generously.
            it["takeaway"] = meta["op_body"][:600].rstrip()
        if i < len(items) - 1 and throttle > 0:
            time.sleep(throttle)


def to_item(rec: dict) -> dict:
    """Convert a per-post record into the standard pipeline item."""
    tid = rec["thread_id"]
    assert tid and str(tid).isdigit(), f"bad thread_id: {tid!r}"
    assert rec.get("board") in BOARDS, f"unknown board: {rec.get('board')!r}"
    gbic = parse_type(rec["title"])
    item = {
        "id": f"geekhack-{tid}",
        "title": rec["title"],
        "url": thread_root_url(tid),
        "discussion_url": thread_root_url(tid),
        "source": "geekhack",
        "subreddit": None,
        "via": f"Geekhack · {BOARDS.get(rec['board']) or 'board ' + str(rec['board'])}",
        "score": None,
        "comments": None,
        "category": "breaking",
        "takeaway": rec["takeaway"],
        # Pre-seed topic so tag_items.py doesn't have to discover it.
        # (tag_items.py merges these with any LLM-discovered topics.)
        "topics": ["group-buys-vendors"],
    }
    if gbic:
        item["type"] = gbic
    return item


def collect(feeds: list, seen: set[str]) -> list[dict]:
    """Given a list of (board, xml_text) tuples and the seen-thread set,
    return one item per *newly seen* thread. Earliest-pubdate post wins
    (its title is most likely the OP title, not a reply).
    """
    by_thread: dict[str, dict] = {}
    for board, xml_text in feeds:
        for rec in parse_feed(xml_text, board):
            tid = rec["thread_id"]
            if tid in seen:
                continue
            prev = by_thread.get(tid)
            if prev is None:
                by_thread[tid] = rec
                continue
            # Keep the earliest known post for this thread (likely the OP).
            prev_pub = prev["pubdate"] or datetime.datetime.max.replace(
                tzinfo=datetime.timezone.utc
            )
            cur_pub = rec["pubdate"] or datetime.datetime.max.replace(
                tzinfo=datetime.timezone.utc
            )
            if cur_pub < prev_pub:
                by_thread[tid] = rec

    items = [to_item(rec) for rec in by_thread.values()]
    items.sort(key=lambda it: int(it["id"].split("-", 1)[1]))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print summary, do not write state")
    ap.add_argument("--no-state", action="store_true",
                    help="ignore + do not write state file (testing)")
    ap.add_argument("--feed-file", action="append", default=[],
                    metavar="PATH",
                    help="local XML file to use instead of fetching "
                         "(repeat for multiple boards; pair with --board)")
    ap.add_argument("--board", action="append", default=[], type=int,
                    help="board id matching each --feed-file (repeatable)")
    ap.add_argument("--no-enrich", action="store_true",
                    help="skip per-thread page scrapes (RSS-only output)")
    ap.add_argument("--throttle", type=float, default=1.0,
                    help="seconds to wait between thread-page fetches "
                         "(default: 1.0)")
    args = ap.parse_args()

    seen = set() if args.no_state else load_state()

    feeds: list[tuple[int, bytes]] = []
    if args.feed_file:
        if len(args.feed_file) != len(args.board):
            sys.stderr.write("--feed-file and --board must be paired 1:1\n")
            sys.exit(2)
        for path, board in zip(args.feed_file, args.board):
            feeds.append((board, pathlib.Path(path).read_bytes()))
    else:
        all_unchanged = True
        for board in BOARDS:
            try:
                body = fetch_feed(feed_url(board))
            except Exception as e:
                sys.stderr.write(f"feed fetch failed (board {board}): {e}\n")
                sys.exit(1)
            if body is None:
                sys.stderr.write(
                    f"  board {board}: 304 Not Modified — skipping\n"
                )
                continue
            all_unchanged = False
            feeds.append((board, body))
        # If every board returned 304, there's nothing to do. Emit an
        # empty array on stdout so the driver's "0 items, exit silently"
        # path triggers without alerting.
        if all_unchanged and not feeds:
            if args.dry_run:
                print("geekhack: all boards unchanged (304) — no work")
            else:
                json.dump([], sys.stdout)
                sys.stdout.write("\n")
                sys.stderr.write("geekhack: all boards unchanged (304)\n")
            return

    items = collect(feeds, seen)

    # Per-thread enrichment: views, replies, OP body, multi-image
    # carousel data. Skipped when running offline tests (`--feed-file`
    # implies network-free) or when `--no-enrich` is passed.
    if items and not args.no_enrich and not args.feed_file:
        enrich_items(items, throttle=args.throttle)

    # Step 2.3: title+body extractors fill the gb chip row.
    # Pure / offline, so always safe to run.
    import gb_extract  # local import to keep the test-only path light
    for it in items:
        facets = gb_extract.extract_gb_facets(it)
        if facets:
            it.setdefault("gb", {}).update(facets)

    if args.dry_run:
        print(f"geekhack: {len(items)} new threads "
              f"(state has {len(seen)} known)")
        for it in items:
            t = it.get("type") or "?"
            print(f"  - [{t}] {it['id']} {it['title']}")
            print(f"    {it['url']}")
            if it.get("takeaway"):
                print(f"    {it['takeaway'][:120]}")
        return

    json.dump(items, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stderr.write(f"geekhack: emitted {len(items)} new threads "
                     f"({len(seen)} previously seen)\n")

    if not args.no_state:
        for it in items:
            seen.add(it["id"].split("-", 1)[1])
        save_state(seen)


if __name__ == "__main__":
    main()

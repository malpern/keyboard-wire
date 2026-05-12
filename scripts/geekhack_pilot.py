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
import urllib.request
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "geekhack_seen.json"

USER_AGENT = "keyboard-wire/1.0 (+https://keyboard-newswire.com)"

BOARDS = {
    70: "Group Buys",
    132: "Interest Checks",
}


def feed_url(board: int) -> str:
    return f"https://geekhack.org/index.php?action=.xml;type=rss;board={board}"


def fetch_feed(url: str) -> bytes:
    """Return raw bytes so ET.fromstring can honor the XML encoding decl
    (Geekhack serves ISO-8859-1, not UTF-8)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


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
        for board in BOARDS:
            try:
                feeds.append((board, fetch_feed(feed_url(board))))
            except Exception as e:
                sys.stderr.write(f"feed fetch failed (board {board}): {e}\n")
                sys.exit(1)

    items = collect(feeds, seen)

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

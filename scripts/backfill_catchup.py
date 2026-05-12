#!/usr/bin/env python3
"""One-shot catch-up poster for the 5/06-5/10 backfill.

Posts 3 day-digest tweets (5/06, 5/08, 5/10) + 5 cherry-picked individual items
from 5/10. 8 tweets total, oldest day first, 30s spacing, halts on 429.

Reuses post_twitter.py's OAuth flow. Tracks digest posts in
data/twitter_posted_digests.json so this is idempotent.

Usage:
  backfill_catchup.py --dry-run   # show all 8 composed tweets, don't post
  backfill_catchup.py             # post for real
"""
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import post_twitter as pt  # noqa: E402

DAYS_DIR = ROOT / "data" / "days"
DIGEST_POSTED = ROOT / "data" / "twitter_posted_digests.json"
SITE_URL = "https://keyboard-newswire.com"
POST_URL = pt.POST_URL
SPACING_SECONDS = 30

DIGEST_DAYS = ["2026-05-06", "2026-05-08", "2026-05-10"]

# Cherry-picked items (date, index into data/days/<date>.json items list).
# Selected for news value / unique content over generic showcases or troubleshooting.
CHERRY_PICKS = [
    ("2026-05-11", 0),   # ScottoAcademy Workshop Logistics (re-post; deleted earlier)
    ("2026-05-10", 0),   # Updated TOTEM dongle zmk module
    ("2026-05-10", 3),   # Modified Scylla with encoders/joysticks/TFT
    ("2026-05-10", 21),  # Portable Bluetooth keyboard with ZMK
    ("2026-05-10", 30),  # ZSA Voyager Neovim combos layout
    ("2026-05-10", 40),  # BBC Sounds split keyboard episode
]


def load_digest_posted() -> set:
    if DIGEST_POSTED.exists():
        return set(json.loads(DIGEST_POSTED.read_text()))
    return set()


def save_digest_posted(posted: set) -> None:
    DIGEST_POSTED.write_text(json.dumps(sorted(posted), indent=2) + "\n")


def top_hashtags(items, n=4) -> list:
    """Pick the N most common tags across the day's items, formatted as hashtags."""
    from collections import Counter
    counts = Counter()
    for it in items:
        for t in it.get("tags", []):
            counts[t] += 1
    out = []
    for tag, _ in counts.most_common(n * 2):  # over-fetch, dedupe-by-case after
        parts = tag.split("-")
        h = "#" + "".join(p.capitalize() for p in parts)
        if h not in out:
            out.append(h)
        if len(out) == n:
            break
    return out


def format_digest(date_str: str) -> str:
    day = json.loads((DAYS_DIR / f"{date_str}.json").read_text())
    items = day.get("items", [])
    n = len(items)
    hashtags = top_hashtags(items, 4)
    hashtag_str = " ".join(hashtags)
    url = f"{SITE_URL}/#{date_str}"

    # Hand-tuned summary line per date (keeps tweets evocative, not just "44 items")
    summaries = {
        "2026-05-06": "Firmware updates, group buys, builds, switch reviews, ergo discussions.",
        "2026-05-08": "Omnitype offers 50% off the Symbiote keycap set (Firsty Thursdays).",
        "2026-05-10": "Big day: ZMK modules, custom builds, group buys, ergo deep dives, switch comparisons.",
    }
    summary = summaries.get(date_str, f"{n} items archived.")

    body = f"📚 Keyboard Newswire — {date_str}\n{n} item{'s' if n != 1 else ''}: {summary}\n{hashtag_str}\n{url}"
    return body


def format_item(date_str: str, idx: int) -> str:
    day = json.loads((DAYS_DIR / f"{date_str}.json").read_text())
    item = day["items"][idx]
    # Reuse the same formatter the daily cron uses
    return pt.format_tweet(item)


def post(text: str) -> dict:
    return pt.post_tweet(text)


def main():
    dry_run = "--dry-run" in sys.argv

    if not all([pt.API_KEY, pt.API_SECRET, pt.ACCESS_TOKEN, pt.ACCESS_TOKEN_SECRET]):
        print("missing X API credentials in environment", file=sys.stderr)
        sys.exit(1)

    digest_posted = load_digest_posted()
    item_posted = pt.load_posted()

    plan = []
    # Order: oldest digest → newest digest → cherry-picked items (newest-day batch last)
    for d in DIGEST_DAYS:
        key = f"digest:{d}"
        if key in digest_posted:
            print(f"skip (already posted): {key}", file=sys.stderr)
            continue
        plan.append(("digest", d, key, format_digest(d)))

    for date_str, idx in CHERRY_PICKS:
        day = json.loads((DAYS_DIR / f"{date_str}.json").read_text())
        item = day["items"][idx]
        if item["id"] in item_posted:
            print(f"skip (already posted): {item['id']}", file=sys.stderr)
            continue
        plan.append(("item", item["id"], item["id"], format_item(date_str, idx)))

    print(f"\n=== plan: {len(plan)} tweets ===\n", file=sys.stderr)
    for kind, label, _key, body in plan:
        print(f"--- {kind}: {label} ({len(body)} chars) ---")
        print(body)
        print()

    if dry_run:
        print(f"DRY RUN — would post {len(plan)} tweets with {SPACING_SECONDS}s spacing.", file=sys.stderr)
        return

    print(f"\nposting {len(plan)} tweets, {SPACING_SECONDS}s spacing...\n", file=sys.stderr)
    for i, (kind, label, key, body) in enumerate(plan):
        try:
            result = post(body)
            tweet_id = result.get("data", {}).get("id", "?")
            print(f"[{i+1}/{len(plan)}] posted {kind} {label} -> tweet {tweet_id}", file=sys.stderr)
            if kind == "digest":
                digest_posted.add(key)
                save_digest_posted(digest_posted)
            else:
                item_posted.add(key)
                pt.save_posted(item_posted)
        except urllib.error.HTTPError as e:
            body_err = e.read().decode()
            print(f"[{i+1}/{len(plan)}] ERROR {kind} {label}: {e.code} {body_err}", file=sys.stderr)
            if e.code == 429:
                print("rate limited — stopping. resume by re-running.", file=sys.stderr)
                sys.exit(2)
            if e.code in (401, 403):
                print("auth/quota error — stopping.", file=sys.stderr)
                sys.exit(3)
            # On other errors, keep going (single bad item shouldn't block the batch)
            continue

        if i < len(plan) - 1:
            time.sleep(SPACING_SECONDS)

    print(f"\ndone: {len(plan)} tweets posted.", file=sys.stderr)


if __name__ == "__main__":
    main()

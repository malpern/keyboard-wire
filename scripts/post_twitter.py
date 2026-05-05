#!/usr/bin/env python3
"""Post today's keyboard-wire items to X (Twitter).

Reads items from data/days/<date>.json and posts each one that hasn't
already been posted. Tracks posted item IDs in data/twitter_posted.json
to avoid duplicates across runs.

Requires four environment variables (OAuth 1.0a User Context):
  X_API_KEY            (Consumer Key)
  X_API_SECRET         (Consumer Secret)
  X_ACCESS_TOKEN       (Access Token)
  X_ACCESS_TOKEN_SECRET (Access Token Secret)

Usage:
  post_twitter.py                  # posts today's items
  post_twitter.py 2026-05-04      # posts a specific day
  post_twitter.py --dry-run       # preview without posting
"""
import base64
import datetime
import hashlib
import hmac
import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
DAYS_DIR = ROOT / "data" / "days"
POSTED_FILE = ROOT / "data" / "twitter_posted.json"
SITE_URL = "https://keyboard-newswire.com"

API_KEY = os.environ.get("X_API_KEY", "")
API_SECRET = os.environ.get("X_API_SECRET", "")
ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN", "")
ACCESS_TOKEN_SECRET = os.environ.get("X_ACCESS_TOKEN_SECRET", "")

POST_URL = "https://api.x.com/2/tweets"


def load_posted() -> set:
    if POSTED_FILE.exists():
        return set(json.loads(POSTED_FILE.read_text()))
    return set()


def save_posted(posted: set) -> None:
    POSTED_FILE.write_text(json.dumps(sorted(posted), indent=2) + "\n")


def percent_encode(s: str) -> str:
    return urllib.parse.quote(s, safe="")


def oauth_signature(method: str, url: str, params: dict) -> str:
    param_str = "&".join(
        f"{percent_encode(k)}={percent_encode(v)}"
        for k, v in sorted(params.items())
    )
    base_string = f"{method}&{percent_encode(url)}&{percent_encode(param_str)}"
    signing_key = f"{percent_encode(API_SECRET)}&{percent_encode(ACCESS_TOKEN_SECRET)}"
    sig = hmac.new(
        signing_key.encode(), base_string.encode(), hashlib.sha1
    ).digest()
    return base64.b64encode(sig).decode()


def build_oauth_header(method: str, url: str, extra_params: dict | None = None) -> str:
    oauth_params = {
        "oauth_consumer_key": API_KEY,
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": ACCESS_TOKEN,
        "oauth_version": "1.0",
    }
    sig_params = {**oauth_params, **(extra_params or {})}
    sig = oauth_signature(method, url, sig_params)
    oauth_params["oauth_signature"] = sig
    header_parts = ", ".join(
        f'{percent_encode(k)}="{percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )
    return f"OAuth {header_parts}"


def post_tweet(text: str) -> dict:
    payload = {"text": text}
    body = json.dumps(payload).encode()
    auth = build_oauth_header("POST", POST_URL)
    req = urllib.request.Request(
        POST_URL,
        data=body,
        headers={
            "Authorization": auth,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())
    return None


def format_tweet(item: dict) -> str:
    title = item["title"]
    url = item["url"]
    topics = item.get("topics", [])
    tags = item.get("tags", [])

    hashtags = []
    for t in tags[:3]:
        parts = t.split("-")
        tag = "".join(p.capitalize() for p in parts)
        if tag:
            hashtags.append(f"#{tag}")
    hashtags = list(dict.fromkeys(hashtags))[:3]

    parts = [title, "", url]
    if hashtags:
        parts.insert(2, " ".join(hashtags))

    tweet = "\n".join(parts)
    if len(tweet) > 280:
        tweet = f"{title}\n\n{url}"
    if len(tweet) > 280:
        tweet = f"{title[:240]}...\n\n{url}"
    return tweet


def main():
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]

    if args:
        date_str = args[0]
    else:
        date_str = datetime.date.today().isoformat()

    day_file = DAYS_DIR / f"{date_str}.json"
    if not day_file.exists():
        print(f"no items for {date_str}", file=sys.stderr)
        return

    if not dry_run and not all([API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET]):
        print("missing X API credentials in environment", file=sys.stderr)
        sys.exit(1)

    day = json.loads(day_file.read_text())
    items = day.get("items", [])
    posted = load_posted()

    new_items = [i for i in items if i["id"] not in posted]
    if not new_items:
        print(f"{date_str}: nothing new to post", file=sys.stderr)
        return

    print(f"{date_str}: {len(new_items)} items to post", file=sys.stderr)

    for item in new_items:
        tweet = format_tweet(item)

        if dry_run:
            print(f"[DRY RUN] {item['id']}:")
            print(tweet)
            print("---")
            continue

        try:
            result = post_tweet(tweet)
            tweet_id = result.get("data", {}).get("id", "?")
            print(f"posted {item['id']} -> tweet {tweet_id}", file=sys.stderr)
            posted.add(item["id"])
            save_posted(posted)
            time.sleep(2)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"error posting {item['id']}: {e.code} {body}", file=sys.stderr)
            if e.code == 429:
                print("rate limited, stopping", file=sys.stderr)
                break

    print(f"done: {len(posted)} total posted", file=sys.stderr)


if __name__ == "__main__":
    main()

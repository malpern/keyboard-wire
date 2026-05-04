#!/usr/bin/env python3
"""Parse a Slack-formatted Keyboard News or Firmware Watch markdown digest into
structured items suitable for the keyboard-wire corpus.

Reads from stdin or a file path argument; emits JSON array on stdout.

Recognized item formats (post-Slack mrkdwn):

  HN:
    *<ARTICLE_URL|Title>* · POINTS pts · COMMENTS comments · <https://news.ycombinator.com/item?id=ID|discuss>

  Reddit:
    EMOJI *<URL|Title>* · ⬆️SCORE · 💬COMMENTS · r/SUBREDDIT
    Optional one-line takeaway on the next non-empty line.
"""
import json
import re
import sys
from urllib.parse import urlparse, parse_qs

LINK_RE = re.compile(r"\*<?([^|>*]+)\|([^>*]+)>?\*")
HN_LINE_RE = re.compile(
    r"\*<?(?P<url>https?[^|>*]+)\|(?P<title>[^>*<]+)>?\*\s*[·•]\s*"
    r"(?P<score>\d+)\s*pts?\s*[·•]\s*(?P<comments>\d+)\s*comments?\s*"
    r"[·•]\s*<?(?P<discuss>https?://news\.ycombinator\.com/item\?id=(?P<id>\d+))\|[^>*]+>?"
)
REDDIT_LINE_RE = re.compile(
    r"^(?P<emoji>[\U0001F300-\U0001FAFF☀-➿⬀-⯿\U0001F900-\U0001F9FF]+|⭐|🔥|📢|🔧)?\s*"
    r"\*<?(?P<url>https?://www\.reddit\.com/r/(?P<subreddit>[^/]+)/comments/(?P<id>[^/]+)/[^|>*]*)\|"
    r"(?P<title>[^>*<]+)>?\*\s*[·•]\s*"
    r"⬆️?(?P<score>\d+)\s*[·•]\s*"
    r"💬(?P<comments>\d+)\s*[·•]\s*"
    r"r/(?P<sub2>[^\s]+)",
    re.UNICODE,
)

BREAKING_EMOJI = {"📢", "🔥", "🚨"}
EVERGREEN_EMOJI = {"🔧", "🛠", "⚙️", "📚"}

BREAKING_KEYWORDS = re.compile(
    r"\b(release[ds]?|launches?|launching|launched|now\s+available|"
    r"announces?|announced|announcement|just\s+(?:dropped|launched|released)|"
    r"available\s+now|version\s+\d|v\d+\.\d|"
    r"is\s+now\s+(?:open\s+source|fully\s+open|live|available)|"
    r"open[\s-]?sourced?|new\s+release)\b",
    re.IGNORECASE,
)


def classify(emoji: str, title: str) -> str:
    if emoji in BREAKING_EMOJI:
        return "breaking"
    if emoji in EVERGREEN_EMOJI:
        return "evergreen"
    if BREAKING_KEYWORDS.search(title):
        return "breaking"
    return "evergreen"


def parse_hn_id(url: str) -> str | None:
    if "news.ycombinator.com" not in url:
        return None
    qs = parse_qs(urlparse(url).query)
    ids = qs.get("id")
    return ids[0] if ids else None


def parse(text: str) -> list[dict]:
    items: list[dict] = []
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i].rstrip()

        # Reddit-format line (looser — emoji optional, may be ⭐)
        m = REDDIT_LINE_RE.search(line)
        if m and "reddit.com" in m.group("url"):
            emoji = (m.group("emoji") or "").strip()
            takeaway = ""
            # Next non-empty line that isn't another item is the takeaway
            j = i + 1
            while j < n:
                nxt = lines[j].strip()
                if not nxt:
                    j += 1
                    continue
                # Stop if it's clearly another item (starts with * or with an emoji+*)
                if "*<" in nxt and "|http" in nxt[:200]:
                    break
                if nxt.startswith(("*", "—", "-")) and "*<" in nxt:
                    break
                takeaway = nxt
                break
            items.append({
                "id": f"reddit-{m.group('id')}",
                "title": m.group("title").strip(),
                "url": m.group("url"),
                "discussion_url": m.group("url"),
                "source": "reddit",
                "subreddit": m.group("subreddit"),
                "score": int(m.group("score")),
                "comments": int(m.group("comments")),
                "category": classify(emoji, m.group("title")),
                "takeaway": takeaway,
            })
            i = max(j, i + 1)
            continue

        # HN-format line
        m = HN_LINE_RE.search(line)
        if m:
            takeaway = ""
            # HN format usually doesn't have a takeaway line, but check next line
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n:
                nxt = lines[j].strip()
                if nxt and not nxt.startswith(("*", "—", "-", "_")) and "*<" not in nxt[:50]:
                    takeaway = nxt
                    j += 1
            items.append({
                "id": f"hn-{m.group('id')}",
                "title": m.group("title").strip(),
                "url": m.group("url"),
                "discussion_url": m.group("discuss"),
                "source": "hn",
                "subreddit": None,
                "score": int(m.group("score")),
                "comments": int(m.group("comments")),
                "category": classify("", m.group("title")),
                "takeaway": takeaway,
            })
            i = j if j > i + 1 else i + 1
            continue

        i += 1
    return items


def main():
    if len(sys.argv) > 1 and sys.argv[1] != "-":
        text = open(sys.argv[1]).read()
    else:
        text = sys.stdin.read()
    items = parse(text)
    json.dump(items, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

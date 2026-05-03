#!/usr/bin/env python3
"""Generate docs/index.html and docs/feed.xml from data/corpus.json."""
import datetime
import html
import json
import pathlib
import sys
from xml.sax.saxutils import escape as xml_escape

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "corpus.json"
DOCS = ROOT / "docs"
SITE_URL = "https://malpern.github.io/keyboard-wire"

MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_score_comments(item):
    parts = []
    if item.get("score") is not None:
        parts.append(f"⬆ {item['score']}")
    if item.get("comments") is not None:
        parts.append(f"💬 {item['comments']}")
    return parts


def render_item(item):
    title = html.escape(item["title"])
    url = html.escape(item["url"])
    takeaway = html.escape(item.get("takeaway") or "")
    source = item.get("source", "")
    subreddit = item.get("subreddit") or ""

    pill_class = source
    pill_label = "HN" if source == "hn" else (f"r/{subreddit}" if subreddit else "reddit")

    meta_parts = [f'<span class="source-pill {html.escape(pill_class)}">{html.escape(pill_label)}</span>']
    for p in fmt_score_comments(item):
        meta_parts.append(f"<span>{html.escape(p)}</span>")
    if item.get("discussion_url") and item["discussion_url"] != item["url"]:
        meta_parts.append(f'<a href="{html.escape(item["discussion_url"])}" rel="noopener">discuss</a>')

    meta_html = '<span class="dot"></span>'.join(meta_parts)
    takeaway_html = f'<p class="item-takeaway">{takeaway}</p>' if takeaway else ""

    return f'''<a class="item" href="{url}" rel="noopener" target="_blank">
  <h3 class="item-title">{title}</h3>
  {takeaway_html}
  <div class="item-meta">{meta_html}</div>
</a>'''


def render_section(label, items, breaking=False):
    if not items:
        return ""
    cls = "section-label breaking" if breaking else "section-label"
    items_html = "\n".join(render_item(i) for i in items)
    return f'''<section class="section">
  <h2 class="{cls}">{html.escape(label)}</h2>
  {items_html}
</section>'''


def render_day(day):
    """day = {date: 'YYYY-MM-DD', items: [...]}"""
    date = day["date"]
    y, m, d = date.split("-")
    day_num = int(d)
    month_year = f"{MONTHS[int(m)]} {y}"
    day_id = date

    items = day.get("items", [])
    breaking = sorted(
        [i for i in items if i.get("category") == "breaking"],
        key=lambda i: -(i.get("score") or 0),
    )
    evergreen = sorted(
        [i for i in items if i.get("category") == "evergreen"],
        key=lambda i: -(i.get("score") or 0),
    )

    body_html = ""
    body_html += render_section("Breaking", breaking, breaking=True)
    body_html += "\n" + render_section("Evergreen", evergreen)

    if not body_html.strip():
        body_html = '<p class="empty">Quiet day — nothing notable.</p>'

    return f'''<section class="day" id="{day_id}">
  <header class="day-header">
    <span class="day-number">{day_num:02d}</span>
    <span class="day-month-year">{month_year}</span>
  </header>
  {body_html}
</section>'''


def render_html(corpus):
    title = html.escape(corpus["title"])
    tagline = html.escape(corpus["tagline"])
    days = sorted(corpus["days"], key=lambda d: d["date"], reverse=True)
    days_html = "\n".join(render_day(d) for d in days)

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{tagline}">
<link rel="stylesheet" href="style.css">
<link rel="alternate" type="application/rss+xml" title="{title}" href="feed.xml">
<link rel="canonical" href="{SITE_URL}/">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{tagline}">
<meta property="og:type" content="website">
<meta property="og:url" content="{SITE_URL}/">
</head>
<body>
<main>
  <header>
    <h1 class="site-title">{title}</h1>
    <p class="tagline">{tagline}</p>
    <p class="subscribe">
      <a href="feed.xml">RSS</a>
      <span aria-hidden="true">·</span>
      <a href="https://github.com/malpern/keyboard-wire">source</a>
      <span class="font-controls" role="group" aria-label="Text size">
        <button type="button" id="font-down" aria-label="smaller" title="Smaller text">A−</button>
        <button type="button" id="font-up" aria-label="larger" title="Larger text">A+</button>
      </span>
    </p>
  </header>
  {days_html if days_html else '<p class="empty">No entries yet — check back tomorrow.</p>'}
  <footer>
    malpern · keyboard wire · auto-curated
    <a href="feed.xml">RSS</a>
    <a href="https://github.com/malpern/keyboard-wire">source</a>
  </footer>
</main>
<script>
(function() {{
  var KEY = 'kw-font-scale';
  var MIN = 0.85, MAX = 1.6, STEP = 0.1;
  var root = document.documentElement;

  function clamp(v) {{ return Math.max(MIN, Math.min(MAX, Math.round(v * 100) / 100)); }}
  function read() {{
    try {{
      var v = parseFloat(localStorage.getItem(KEY));
      return isNaN(v) ? 1 : clamp(v);
    }} catch (e) {{ return 1; }}
  }}
  function write(v) {{
    try {{ localStorage.setItem(KEY, String(v)); }} catch (e) {{}}
  }}
  function apply(v) {{
    root.style.setProperty('--font-scale', String(v));
    var down = document.getElementById('font-down');
    var up = document.getElementById('font-up');
    if (down) down.disabled = v <= MIN + 0.001;
    if (up) up.disabled = v >= MAX - 0.001;
  }}

  var current = read();
  apply(current);

  document.addEventListener('DOMContentLoaded', function() {{
    apply(current); // re-apply after buttons exist (for disabled state)
    var down = document.getElementById('font-down');
    var up = document.getElementById('font-up');
    if (down) down.addEventListener('click', function() {{
      current = clamp(current - STEP); apply(current); write(current);
    }});
    if (up) up.addEventListener('click', function() {{
      current = clamp(current + STEP); apply(current); write(current);
    }});
  }});
}})();
</script>
</body>
</html>
'''


def render_rss(corpus):
    """RSS 2.0 feed. One <item> per content item, newest first, capped at 200."""
    title = xml_escape(corpus["title"])
    tagline = xml_escape(corpus["tagline"])
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

    flat = []
    for day in corpus["days"]:
        date = day["date"]
        for it in day.get("items", []):
            flat.append((date, it))
    flat.sort(key=lambda r: (r[0], r[1].get("score") or 0), reverse=True)
    flat = flat[:200]

    items_xml = []
    for date, it in flat:
        # Use the day at noon UTC as pubDate to avoid clock-stamp confusion
        try:
            dt = datetime.datetime.strptime(date, "%Y-%m-%d").replace(
                hour=12, tzinfo=datetime.timezone.utc
            )
        except ValueError:
            dt = datetime.datetime.now(datetime.timezone.utc)
        pubdate = dt.strftime("%a, %d %b %Y %H:%M:%S +0000")

        title_x = xml_escape(it["title"])
        url_x = xml_escape(it["url"])
        takeaway = it.get("takeaway") or ""
        source = it.get("source", "")
        subreddit = it.get("subreddit") or ""
        source_label = "Hacker News" if source == "hn" else (
            f"r/{subreddit}" if subreddit else "Reddit"
        )

        # Description: takeaway + score/comment metadata
        desc_parts = []
        if takeaway:
            desc_parts.append(takeaway)
        meta = []
        if it.get("score") is not None:
            meta.append(f"{it['score']} pts")
        if it.get("comments") is not None:
            meta.append(f"{it['comments']} comments")
        if meta:
            desc_parts.append(" · ".join(meta) + f" · {source_label}")
        else:
            desc_parts.append(source_label)
        desc = xml_escape(" — ".join(desc_parts))

        # Stable GUID: prefer item.id else url
        guid = xml_escape(it.get("id") or it["url"])

        cat_x = xml_escape(it.get("category", ""))

        items_xml.append(f'''  <item>
    <title>{title_x}</title>
    <link>{url_x}</link>
    <guid isPermaLink="false">{guid}</guid>
    <pubDate>{pubdate}</pubDate>
    <category>{cat_x}</category>
    <source url="{xml_escape(SITE_URL)}/feed.xml">{xml_escape(source_label)}</source>
    <description>{desc}</description>
  </item>''')

    items_block = "\n".join(items_xml)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>{title}</title>
  <link>{SITE_URL}/</link>
  <atom:link href="{SITE_URL}/feed.xml" rel="self" type="application/rss+xml"/>
  <description>{tagline}</description>
  <language>en-us</language>
  <lastBuildDate>{now}</lastBuildDate>
{items_block}
</channel>
</rss>
'''


def main():
    corpus = json.loads(CORPUS.read_text())
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "index.html").write_text(render_html(corpus))
    (DOCS / "feed.xml").write_text(render_rss(corpus))
    n_days = len(corpus["days"])
    n_items = sum(len(d.get("items", [])) for d in corpus["days"])
    print(f"generated: {n_days} days, {n_items} items → docs/index.html, docs/feed.xml")


if __name__ == "__main__":
    main()

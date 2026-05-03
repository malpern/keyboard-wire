#!/usr/bin/env python3
"""Generate the keyboard-wire site:

- /index.html              daily chronologic feed (Breaking + Evergreen sections)
- /feed.xml                main RSS feed (200 most recent items)
- /topics/<slug>/index.html  per-topic browse page (all-time, dated)
- /topics/<slug>/feed.xml    per-topic RSS feed
- /tags/<slug>/index.html    per-tag browse page
- /tags/<slug>/feed.xml      per-tag RSS feed
- /topics/index.html         topic directory
- /tags/index.html           tag directory

Reads:
  data/corpus.json            day-grouped items
  data/topics.json            broad topic registry (~8 entries)
  data/tags.json              fine-grained tag registry (grows organically)
"""
import datetime
import html
import json
import pathlib
import re
import shutil
import sys
from xml.sax.saxutils import escape as xml_escape

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "corpus.json"
TOPICS_FILE = ROOT / "data" / "topics.json"
TAGS_FILE = ROOT / "data" / "tags.json"
DOCS = ROOT / "docs"
SITE_URL = "https://malpern.github.io/keyboard-wire"

MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ── helpers ──────────────────────────────────────────────────────


def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def fmt_date_long(date: str) -> str:
    """2026-05-03 → 'May 3, 2026'"""
    y, m, d = date.split("-")
    return f"{MONTHS[int(m)]} {int(d)}, {y}"


def fmt_date_short(date: str) -> str:
    """2026-05-03 → 'May 3'"""
    y, m, d = date.split("-")
    return f"{MONTHS[int(m)]} {int(d)}"


def source_label(item: dict) -> str:
    if item.get("source") == "hn":
        return "Hacker News"
    sub = item.get("subreddit")
    return f"r/{sub}" if sub else "Reddit"


def topic_url(slug: str) -> str:
    return f"{SITE_URL}/topics/{slug}/"


def tag_url(slug: str) -> str:
    return f"{SITE_URL}/tags/{slug}/"


# ── HTML rendering ───────────────────────────────────────────────


def head(title: str, description: str, canonical: str, feed: str | None = None) -> str:
    feed_link = (
        f'<link rel="alternate" type="application/rss+xml" title="{html.escape(title)}" href="{html.escape(feed)}">'
        if feed else ""
    )
    # Cache-bust style.css with the file's mtime so deploys force a fresh CSS pull.
    css_path = DOCS / "style.css"
    css_v = int(css_path.stat().st_mtime) if css_path.exists() else 1
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<link rel="stylesheet" href="{relative_to_docs(canonical, "style.css")}?v={css_v}">
{feed_link}
<link rel="canonical" href="{html.escape(canonical)}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(description)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{html.escape(canonical)}">
</head>
<body>
<main>'''


def relative_to_docs(canonical: str, asset: str) -> str:
    """Return the path to /docs/<asset> relative to the page being rendered."""
    if canonical == f"{SITE_URL}/":
        return asset
    rel = canonical[len(SITE_URL):].strip("/")
    depth = rel.count("/")
    return "../" * (depth + 1) + asset


def font_controls() -> str:
    return '''<span class="font-controls" role="group" aria-label="Text size">
        <button type="button" id="font-down" aria-label="smaller" title="Smaller text">A−</button>
        <button type="button" id="font-up" aria-label="larger" title="Larger text">A+</button>
      </span>'''


def site_header(canonical: str) -> str:
    is_root = canonical == f"{SITE_URL}/"
    home = "" if is_root else f'<a href="{relative_to_docs(canonical, "")}">home</a><span aria-hidden="true">·</span>'
    feed_path = relative_to_docs(canonical, "feed.xml")
    source_path = "https://github.com/malpern/keyboard-wire"
    return f'''<header>
    <h1 class="site-title"><a href="{relative_to_docs(canonical, "")}">malpern's keyboard wire</a></h1>
    <p class="tagline">daily mechanical keyboards, firmware &amp; tools</p>
    <p class="subscribe">
      {home}
      <a href="{feed_path}">RSS</a>
      <span aria-hidden="true">·</span>
      <a href="{source_path}">source</a>
      {font_controls()}
    </p>
  </header>'''


def site_footer() -> str:
    return '''<footer>
    malpern · keyboard wire · auto-curated
    <a href="feed.xml">RSS</a>
    <a href="https://github.com/malpern/keyboard-wire">source</a>
    <a href="topics/">topics</a>
    <a href="tags/">tags</a>
  </footer>'''


def font_script() -> str:
    return '''<script>
(function() {
  var KEY = 'kw-font-scale';
  var MIN = 0.85, MAX = 1.6, STEP = 0.1;
  var root = document.documentElement;
  function clamp(v) { return Math.max(MIN, Math.min(MAX, Math.round(v * 100) / 100)); }
  function read() {
    try {
      var v = parseFloat(localStorage.getItem(KEY));
      return isNaN(v) ? 1 : clamp(v);
    } catch (e) { return 1; }
  }
  function write(v) { try { localStorage.setItem(KEY, String(v)); } catch (e) {} }
  function apply(v) {
    root.style.setProperty('--font-scale', String(v));
    var down = document.getElementById('font-down');
    var up = document.getElementById('font-up');
    if (down) down.disabled = v <= MIN + 0.001;
    if (up) up.disabled = v >= MAX - 0.001;
  }
  var current = read();
  apply(current);
  document.addEventListener('DOMContentLoaded', function() {
    apply(current);
    var down = document.getElementById('font-down');
    var up = document.getElementById('font-up');
    if (down) down.addEventListener('click', function() {
      current = clamp(current - STEP); apply(current); write(current);
    });
    if (up) up.addEventListener('click', function() {
      current = clamp(current + STEP); apply(current); write(current);
    });
  });
})();
</script>'''


def render_item(item: dict, topics_reg: dict, tags_reg: dict, *,
                date: str | None = None, page: str = "day",
                rel_prefix: str = "") -> str:
    """Render one item.

    page = 'day' | 'topic' | 'tag'  — affects which date/meta is shown.
    rel_prefix = path prefix from current page to docs root, e.g., '../../' for /topics/<slug>/.
    """
    title = html.escape(item["title"])
    url = html.escape(item["url"])
    takeaway = html.escape(item.get("takeaway") or "")

    # Top meta line: [DATE ·] SOURCE · TOPIC1 · TOPIC2
    top_parts = []
    if page in ("topic", "tag") and date:
        top_parts.append(f'<span class="date-prefix">{html.escape(fmt_date_short(date))}</span>')
    top_parts.append(f'<span class="source">{html.escape(source_label(item))}</span>')

    # Topics (above title, primary nav)
    item_topics = item.get("topics") or []
    for t_slug in item_topics:
        t = topics_reg.get(t_slug)
        if not t:
            continue
        href = f"{rel_prefix}topics/{t_slug}/"
        top_parts.append(f'<a href="{html.escape(href)}">{html.escape(t["name"])}</a>')

    top_meta = '<span class="sep">·</span>'.join(top_parts)

    # Bottom utility row: score · comments · discuss · #tags
    bottom_parts = []
    if item.get("score") is not None:
        bottom_parts.append(f'<span class="stat">⬆ {item["score"]}</span>')
    if item.get("comments") is not None:
        bottom_parts.append(f'<span class="stat">💬 {item["comments"]}</span>')
    if item.get("discussion_url") and item["discussion_url"] != item["url"]:
        bottom_parts.append(f'<a href="{html.escape(item["discussion_url"])}" rel="noopener" target="_blank">discuss</a>')

    # Tags (utility row, secondary nav)
    item_tags = item.get("tags") or []
    if item_tags:
        tags_html = "".join(
            f'<a class="tag" href="{html.escape(rel_prefix)}tags/{html.escape(slug)}/">{html.escape(tags_reg.get(slug, {}).get("name", slug))}</a>'
            for slug in item_tags
        )
        bottom_parts.append(f'<span class="tag-list">{tags_html}</span>')

    bottom_meta = "".join(bottom_parts) if bottom_parts else ""
    takeaway_html = f'<p class="item-takeaway">{takeaway}</p>' if takeaway else ""

    image = item.get("image")
    if image:
        # rel_prefix handles topic/tag pages where /img/ is at root
        img_src = f"{rel_prefix}{image}" if rel_prefix else image
        thumb_html = (
            f'<div class="item-thumb" '
            f'style="background-image:url({html.escape(img_src)})" '
            f'role="img" aria-hidden="true"></div>'
        )
        item_classes = "item has-thumb"
    else:
        thumb_html = ""
        item_classes = "item"

    return f'''<a class="{item_classes}" href="{url}" rel="noopener" target="_blank">
  <div class="item-body">
    <div class="item-topmeta">{top_meta}</div>
    <h3 class="item-title">{title}</h3>
    {takeaway_html}
    <div class="item-meta">{bottom_meta}</div>
  </div>
  {thumb_html}
</a>'''


def render_section(label: str, items: list[dict], topics_reg: dict, tags_reg: dict, *,
                   breaking: bool = False) -> str:
    if not items:
        return ""
    cls = "section-label breaking" if breaking else "section-label"
    items_html = "\n".join(render_item(i, topics_reg, tags_reg, page="day") for i in items)
    return f'''<section class="section">
  <h2 class="{cls}">{html.escape(label)}</h2>
  {items_html}
</section>'''


def render_day_block(day: dict, topics_reg: dict, tags_reg: dict) -> str:
    date = day["date"]
    y, m, d = date.split("-")
    day_num = int(d)
    month_year = f"{MONTHS[int(m)]} {y}"

    items = day.get("items", [])
    breaking = sorted(
        [i for i in items if i.get("category") == "breaking"],
        key=lambda i: -(i.get("score") or 0),
    )
    evergreen = sorted(
        [i for i in items if i.get("category") == "evergreen"],
        key=lambda i: -(i.get("score") or 0),
    )

    body = ""
    body += render_section("Breaking", breaking, topics_reg, tags_reg, breaking=True)
    body += "\n" + render_section("Evergreen", evergreen, topics_reg, tags_reg)
    if not body.strip():
        body = '<p class="empty">Quiet day — nothing notable.</p>'

    return f'''<section class="day" id="{date}">
  <header class="day-header">
    <span class="day-number">{day_num:02d}</span>
    <span class="day-month-year">{month_year}</span>
  </header>
  {body}
</section>'''


def render_index(corpus: dict, topics_reg: dict, tags_reg: dict) -> str:
    title = corpus["title"]
    tagline = corpus["tagline"]
    days = sorted(corpus["days"], key=lambda d: d["date"], reverse=True)
    days_html = "\n".join(render_day_block(d, topics_reg, tags_reg) for d in days)
    canonical = f"{SITE_URL}/"
    return f'''{head(title, tagline, canonical, feed="feed.xml")}
  {site_header(canonical)}
  {days_html if days_html else '<p class="empty">No entries yet — check back tomorrow.</p>'}
  {site_footer()}
</main>
{font_script()}
</body>
</html>'''


def render_browse_page(label: str, slug: str, items_with_dates: list[tuple],
                       topics_reg: dict, tags_reg: dict, *, kind: str) -> str:
    """Render a topic or tag browse page.

    items_with_dates = [(date, item), ...] sorted by date desc.
    kind = 'topic' or 'tag'
    """
    title = f"{label} · malpern's keyboard wire"
    desc = f"All keyboard wire entries tagged {label}."
    canonical = f"{SITE_URL}/{kind}s/{slug}/"

    if items_with_dates:
        items_html = "\n".join(
            render_item(it, topics_reg, tags_reg, date=date, page=kind, rel_prefix="../../")
            for date, it in items_with_dates
        )
    else:
        items_html = '<p class="empty">No entries yet.</p>'

    return f'''{head(title, desc, canonical, feed="feed.xml")}
  {site_header(canonical)}
  <section class="topic-page">
    <header class="day-header">
      <span class="day-number">{len(items_with_dates):02d}</span>
      <span class="day-month-year">{kind} · {html.escape(label.lower())}</span>
    </header>
    {items_html}
  </section>
  <footer>
    <a href="../../">home</a>
    <a href="../../topics/">topics</a>
    <a href="../../tags/">tags</a>
    <a href="feed.xml">RSS</a>
  </footer>
</main>
{font_script()}
</body>
</html>'''


def render_directory(label: str, kind: str, entries: list[tuple]) -> str:
    """entries = [(slug, name, count), ...] sorted by count desc."""
    title = f"{label} · malpern's keyboard wire"
    canonical = f"{SITE_URL}/{kind}s/"
    rows = "\n".join(
        f'<a class="item" href="{html.escape(slug)}/"><div class="item-topmeta"><span class="date-prefix">{count}</span><span class="sep">·</span><span class="source">{html.escape(kind)}</span></div><h3 class="item-title">{html.escape(name)}</h3></a>'
        for slug, name, count in entries
    )
    return f'''{head(title, label, canonical)}
  {site_header(canonical)}
  <section class="topic-page">
    <header class="day-header">
      <span class="day-number">{len(entries):02d}</span>
      <span class="day-month-year">{html.escape(label.lower())}</span>
    </header>
    {rows if rows else '<p class="empty">None yet.</p>'}
  </section>
  <footer><a href="../">home</a></footer>
</main>
{font_script()}
</body>
</html>'''


# ── RSS ──────────────────────────────────────────────────────────


def render_rss(corpus_title: str, corpus_tagline: str, link: str, self_link: str,
               flat_items: list[tuple], topics_reg: dict) -> str:
    """flat_items = [(date, item), ...] sorted desc. Capped at 200."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    flat_items = flat_items[:200]
    items_xml = []
    for date, it in flat_items:
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
        sl = source_label(it)

        meta = []
        if it.get("score") is not None:
            meta.append(f"{it['score']} pts")
        if it.get("comments") is not None:
            meta.append(f"{it['comments']} comments")

        desc_parts = []
        if takeaway:
            desc_parts.append(takeaway)
        if meta:
            desc_parts.append(" · ".join(meta) + f" · {sl}")
        else:
            desc_parts.append(sl)
        desc = xml_escape(" — ".join(desc_parts))

        guid = xml_escape(it.get("id") or it["url"])
        cats = []
        cats.append(it.get("category", ""))
        for ts in (it.get("topics") or []):
            t = topics_reg.get(ts)
            if t:
                cats.append(t["name"])
        cat_xml = "\n    ".join(f"<category>{xml_escape(c)}</category>" for c in cats if c)

        items_xml.append(f'''  <item>
    <title>{title_x}</title>
    <link>{url_x}</link>
    <guid isPermaLink="false">{guid}</guid>
    <pubDate>{pubdate}</pubDate>
    {cat_xml}
    <source url="{xml_escape(SITE_URL)}/feed.xml">{xml_escape(sl)}</source>
    <description>{desc}</description>
  </item>''')

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>{xml_escape(corpus_title)}</title>
  <link>{xml_escape(link)}</link>
  <atom:link href="{xml_escape(self_link)}" rel="self" type="application/rss+xml"/>
  <description>{xml_escape(corpus_tagline)}</description>
  <language>en-us</language>
  <lastBuildDate>{now}</lastBuildDate>
{chr(10).join(items_xml)}
</channel>
</rss>
'''


# ── main ─────────────────────────────────────────────────────────


def main():
    corpus = json.loads(CORPUS.read_text())
    topics_reg = json.loads(TOPICS_FILE.read_text())["topics"]
    tags_reg = json.loads(TAGS_FILE.read_text()).get("tags", {})

    DOCS.mkdir(parents=True, exist_ok=True)
    # Wipe and rebuild topic/tag dirs to drop stale slugs cleanly
    for sub in ("topics", "tags"):
        d = DOCS / sub
        if d.exists():
            shutil.rmtree(d)

    # --- index + main feed ---
    (DOCS / "index.html").write_text(render_index(corpus, topics_reg, tags_reg))

    flat = []
    for day in corpus["days"]:
        for it in day.get("items", []):
            flat.append((day["date"], it))
    flat.sort(key=lambda r: (r[0], r[1].get("score") or 0), reverse=True)
    (DOCS / "feed.xml").write_text(render_rss(
        corpus["title"], corpus["tagline"],
        f"{SITE_URL}/", f"{SITE_URL}/feed.xml", flat, topics_reg
    ))

    # --- topic pages ---
    by_topic: dict[str, list[tuple]] = {slug: [] for slug in topics_reg}
    for day in corpus["days"]:
        for it in day.get("items", []):
            for ts in it.get("topics") or []:
                if ts in by_topic:
                    by_topic[ts].append((day["date"], it))
    for slug, items in by_topic.items():
        items.sort(key=lambda r: r[0], reverse=True)
        outdir = DOCS / "topics" / slug
        outdir.mkdir(parents=True, exist_ok=True)
        topic_meta = topics_reg[slug]
        (outdir / "index.html").write_text(
            render_browse_page(topic_meta["name"], slug, items, topics_reg, tags_reg, kind="topic")
        )
        (outdir / "feed.xml").write_text(render_rss(
            f"{topic_meta['name']} · keyboard wire",
            topic_meta.get("description", ""),
            topic_url(slug), f"{topic_url(slug)}feed.xml", items, topics_reg
        ))

    topic_dir_entries = sorted(
        [(slug, topics_reg[slug]["name"], len(by_topic[slug])) for slug in topics_reg],
        key=lambda r: -r[2]
    )
    (DOCS / "topics").mkdir(parents=True, exist_ok=True)
    (DOCS / "topics" / "index.html").write_text(render_directory("Topics", "topic", topic_dir_entries))

    # --- tag pages ---
    by_tag: dict[str, list[tuple]] = {}
    seen_tag_slugs = set()
    for day in corpus["days"]:
        for it in day.get("items", []):
            for tg in it.get("tags") or []:
                seen_tag_slugs.add(tg)
                by_tag.setdefault(tg, []).append((day["date"], it))
    for slug, items in by_tag.items():
        items.sort(key=lambda r: r[0], reverse=True)
        outdir = DOCS / "tags" / slug
        outdir.mkdir(parents=True, exist_ok=True)
        name = tags_reg.get(slug, {}).get("name", slug)
        (outdir / "index.html").write_text(
            render_browse_page(name, slug, items, topics_reg, tags_reg, kind="tag")
        )
        (outdir / "feed.xml").write_text(render_rss(
            f"#{name} · keyboard wire",
            f"All keyboard wire entries tagged {name}.",
            tag_url(slug), f"{tag_url(slug)}feed.xml", items, topics_reg
        ))

    tag_dir_entries = sorted(
        [(slug, tags_reg.get(slug, {}).get("name", slug), len(items))
         for slug, items in by_tag.items()],
        key=lambda r: -r[2]
    )
    (DOCS / "tags").mkdir(parents=True, exist_ok=True)
    (DOCS / "tags" / "index.html").write_text(render_directory("Tags", "tag", tag_dir_entries))

    n_days = len(corpus["days"])
    n_items = sum(len(d.get("items", [])) for d in corpus["days"])
    n_topics_used = sum(1 for v in by_topic.values() if v)
    n_tags = len(by_tag)
    print(f"generated: {n_days} days, {n_items} items, "
          f"{n_topics_used}/{len(topics_reg)} topics, {n_tags} tags")


if __name__ == "__main__":
    main()

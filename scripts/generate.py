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
    if item.get("source") == "email":
        return f"✉ {item.get('via') or 'Inbox'}"
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
    home_path = relative_to_docs(canonical, "")
    home = "" if is_root else f'<a href="{home_path}">home</a><span aria-hidden="true">·</span>'
    feed_path = relative_to_docs(canonical, "feed.xml")
    settings_path = relative_to_docs(canonical, "settings/")
    archive_path = relative_to_docs(canonical, "archive/")
    source_path = "https://github.com/malpern/keyboard-wire"
    return f'''<header>
    <h1 class="site-title"><a href="{home_path}">malpern's keyboard wire</a></h1>
    <p class="tagline">daily mechanical keyboards, firmware &amp; tools</p>
    <p class="subscribe">
      {home}
      <a href="{archive_path}">archive</a>
      <span aria-hidden="true">·</span>
      <a href="{feed_path}">RSS</a>
      <span aria-hidden="true">·</span>
      <a href="{settings_path}">settings</a>
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
  // Font scale (persisted)
  var FK = 'kw-font-scale', MIN = 0.85, MAX = 1.6, STEP = 0.1;
  var root = document.documentElement;
  function clampScale(v) { return Math.max(MIN, Math.min(MAX, Math.round(v * 100) / 100)); }
  function readScale() {
    try { var v = parseFloat(localStorage.getItem(FK)); return isNaN(v) ? 1 : clampScale(v); }
    catch (e) { return 1; }
  }
  function writeScale(v) { try { localStorage.setItem(FK, String(v)); } catch (e) {} }
  function applyScale(v) {
    root.style.setProperty('--font-scale', String(v));
    var down = document.getElementById('font-down');
    var up = document.getElementById('font-up');
    if (down) down.disabled = v <= MIN + 0.001;
    if (up) up.disabled = v >= MAX - 0.001;
  }
  var scale = readScale();
  applyScale(scale);

  // Title rewriting toggle (persisted, default off — show originals)
  var RK = 'kw-rewrite-titles';
  function readRewrite() {
    try { return localStorage.getItem(RK) === 'on'; } catch (e) { return false; }
  }
  function writeRewrite(on) {
    try { localStorage.setItem(RK, on ? 'on' : 'off'); } catch (e) {}
  }
  function applyRewrite(on) {
    document.querySelectorAll('h3.item-title[data-rewritten]').forEach(function(el) {
      if (!el.dataset.original) el.dataset.original = el.textContent;
      el.textContent = on ? el.dataset.rewritten : el.dataset.original;
    });
  }

  document.addEventListener('DOMContentLoaded', function() {
    applyScale(scale);
    var down = document.getElementById('font-down');
    var up = document.getElementById('font-up');
    if (down) down.addEventListener('click', function() {
      scale = clampScale(scale - STEP); applyScale(scale); writeScale(scale);
    });
    if (up) up.addEventListener('click', function() {
      scale = clampScale(scale + STEP); applyScale(scale); writeScale(scale);
    });

    applyRewrite(readRewrite());
    var rewrite = document.getElementById('rewrite-toggle');
    if (rewrite) {
      rewrite.checked = readRewrite();
      rewrite.addEventListener('change', function() {
        writeRewrite(rewrite.checked);
        applyRewrite(rewrite.checked);
      });
    }
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
    # Default to ORIGINAL title; rewritten value rides in a data attribute,
    # JS swaps based on the user's settings toggle (default off).
    if item.get("title_rewritten") and item.get("original_title"):
        display_title = html.escape(item["original_title"])
        rewritten_attr = f' data-rewritten="{html.escape(item["title"], quote=True)}"'
    else:
        display_title = html.escape(item["title"])
        rewritten_attr = ""
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

    # Tags hidden from item cards for now (still drive /tags/<slug>/ pages
    # and feed the archive search index). Re-enable here later if desired.
    bottom_meta = "".join(bottom_parts) if bottom_parts else ""
    takeaway_html = f'<p class="item-takeaway">{takeaway}</p>' if takeaway else ""

    image = item.get("image")
    if image:
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

    # Item is a <div> (not <a>) since topic/tag links inside would create
    # invalid nested anchors. Title is the click target for the story.
    return f'''<div class="{item_classes}">
  <div class="item-body">
    <div class="item-topmeta">{top_meta}</div>
    <h3 class="item-title"{rewritten_attr}><a class="item-link" href="{url}" rel="noopener" target="_blank">{display_title}</a></h3>
    {takeaway_html}
    <div class="item-meta">{bottom_meta}</div>
  </div>
  {thumb_html}
</div>'''


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


def render_archive_page(corpus: dict, topics_reg: dict, tags_reg: dict) -> str:
    canonical = f"{SITE_URL}/archive/"
    title = "Archive · malpern's keyboard wire"
    desc = "Browse, filter, sort, and search the keyboard wire corpus."

    # Flatten items + counts
    flat = []
    topic_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    for day in corpus["days"]:
        for it in day.get("items", []):
            flat.append((day["date"], it))
            for t in it.get("topics") or []:
                topic_counts[t] = topic_counts.get(t, 0) + 1
            for t in it.get("tags") or []:
                tag_counts[t] = tag_counts.get(t, 0) + 1
    flat.sort(key=lambda r: (r[0], r[1].get("score") or 0), reverse=True)

    # Filter dropdown options (ordered by count desc)
    topic_options = sorted(
        [(slug, topics_reg[slug]["name"], topic_counts.get(slug, 0))
         for slug in topics_reg if topic_counts.get(slug, 0) > 0],
        key=lambda r: -r[2],
    )
    tag_options = sorted(
        [(slug, tags_reg.get(slug, {}).get("name", slug), c)
         for slug, c in tag_counts.items() if c > 0],
        key=lambda r: -r[2],
    )

    topic_opts_html = "".join(
        f'<option value="{html.escape(s)}">{html.escape(n)} ({c})</option>'
        for s, n, c in topic_options
    )
    tag_opts_html = "".join(
        f'<option value="{html.escape(s)}">{html.escape(n)} ({c})</option>'
        for s, n, c in tag_options
    )

    # Suggestions for search autocomplete
    suggestions = []
    for slug, name, c in topic_options:
        suggestions.append({"kind": "topic", "slug": slug, "label": name, "count": c})
    for slug, name, c in tag_options:
        suggestions.append({"kind": "tag", "slug": slug, "label": name, "count": c})

    # Render items as compact rows (no thumbnails — archive is for scanning)
    items_html_parts = []
    for date, it in flat:
        item_topics = " ".join(it.get("topics") or [])
        item_tags = " ".join(it.get("tags") or [])
        score = it.get("score") or 0
        # title to display: original by default; rewritten swap is a runtime concern
        if it.get("title_rewritten") and it.get("original_title"):
            display_title = html.escape(it["original_title"])
            rewritten_attr = f' data-rewritten="{html.escape(it["title"], quote=True)}"'
        else:
            display_title = html.escape(it["title"])
            rewritten_attr = ""

        meta_bits = [f'<time>{html.escape(fmt_date_short(date))}</time>',
                     f'<span class="source">{html.escape(source_label(it))}</span>']
        for ts in (it.get("topics") or [])[:2]:
            t = topics_reg.get(ts)
            if t:
                meta_bits.append(f'<a href="../topics/{html.escape(ts)}/">{html.escape(t["name"])}</a>')
        meta_html = '<span class="sep">·</span>'.join(meta_bits)

        score_str = str(score) if score else ""
        searchable = (it["title"] + " " + (it.get("takeaway") or "")).lower()
        searchable = re.sub(r'[^a-z0-9 ]+', ' ', searchable)

        url = html.escape(it["url"])
        takeaway = html.escape(it.get("takeaway") or "")
        takeaway_html = f'<p class="archive-takeaway">{takeaway}</p>' if takeaway else ""
        items_html_parts.append(f'''<article class="archive-item"
  data-date="{html.escape(date)}"
  data-score="{html.escape(score_str)}"
  data-topics=" {html.escape(item_topics)} "
  data-tags=" {html.escape(item_tags)} "
  data-search="{html.escape(searchable)}">
  <div class="archive-meta">{meta_html}</div>
  <h3 class="archive-title"{rewritten_attr}><a href="{url}" rel="noopener" target="_blank">{display_title}</a></h3>
  {takeaway_html}
</article>''')

    items_html = "\n".join(items_html_parts)

    suggestions_json = json.dumps(suggestions, ensure_ascii=False).replace("</", "<\\/")

    return f'''{head(title, desc, canonical)}
  {site_header(canonical)}
  <section class="archive-page">
    <header class="archive-header">
      <h2 class="archive-title-page">Archive</h2>
      <p class="archive-stats">{len(flat)} items · {len(topic_options)} topics · {len(tag_options)} tags</p>
    </header>

    <div class="archive-controls">
      <div class="search-wrap">
        <input type="search" id="archive-search" placeholder="Search titles, topics, tags…" autocomplete="off">
        <ul id="search-suggest" class="search-suggest" hidden></ul>
      </div>
      <div class="filter-row">
        <label class="filter-field">
          <span>Topic</span>
          <select id="filter-topic"><option value="">all</option>{topic_opts_html}</select>
        </label>
        <label class="filter-field">
          <span>Tag</span>
          <select id="filter-tag"><option value="">all</option>{tag_opts_html}</select>
        </label>
        <label class="filter-field">
          <span>Sort</span>
          <select id="filter-sort"><option value="date">newest</option><option value="score">top score</option></select>
        </label>
        <button type="button" id="filter-clear" class="filter-clear">clear</button>
      </div>
    </div>

    <div id="archive-results" class="archive-results">
      {items_html}
    </div>
    <p id="archive-empty" class="empty" hidden>No items match those filters.</p>
  </section>
  <footer><a href="../">home</a></footer>
</main>
<script>window.KW_SUGGESTIONS = {suggestions_json};</script>
{font_script()}
{archive_script()}
</body>
</html>'''


def archive_script() -> str:
    return '''<script>
(function() {
  var $ = function(id) { return document.getElementById(id); };
  var resultsEl = $('archive-results');
  var items = Array.from(resultsEl.querySelectorAll('.archive-item'));
  var search = $('archive-search');
  var topicSel = $('filter-topic');
  var tagSel = $('filter-tag');
  var sortSel = $('filter-sort');
  var empty = $('archive-empty');
  var suggest = $('search-suggest');
  var clearBtn = $('filter-clear');

  function renderSuggest(q) {
    var ql = q.trim().toLowerCase();
    var src = window.KW_SUGGESTIONS || [];
    var matches = ql
      ? src.filter(function(s) { return s.label.toLowerCase().indexOf(ql) >= 0; })
      : src.slice(0, 12);
    matches = matches.slice(0, 10);
    if (!matches.length) { suggest.hidden = true; suggest.innerHTML = ''; return; }
    suggest.innerHTML = matches.map(function(m) {
      return '<li data-kind="' + m.kind + '" data-slug="' + m.slug + '" tabindex="-1">' +
        '<span class="suggest-kind">' + m.kind + '</span>' +
        '<span class="suggest-label">' + m.label + '</span>' +
        '<span class="suggest-count">' + m.count + '</span></li>';
    }).join('');
    suggest.hidden = false;
  }

  function apply() {
    var q = search.value.trim().toLowerCase();
    var qWords = q.split(/\\s+/).filter(Boolean);
    var topic = topicSel.value;
    var tag = tagSel.value;
    var visible = 0;
    items.forEach(function(el) {
      var hay = el.dataset.search;
      var titleOk = qWords.every(function(w) { return hay.indexOf(w) >= 0; });
      var topicOk = !topic || el.dataset.topics.indexOf(' ' + topic + ' ') >= 0;
      var tagOk = !tag || el.dataset.tags.indexOf(' ' + tag + ' ') >= 0;
      var ok = titleOk && topicOk && tagOk;
      el.hidden = !ok;
      if (ok) visible++;
    });
    empty.hidden = visible > 0;

    var sortBy = sortSel.value;
    var sorted = items.slice().sort(function(a, b) {
      if (sortBy === 'score') {
        return (parseInt(b.dataset.score) || 0) - (parseInt(a.dataset.score) || 0);
      }
      return b.dataset.date.localeCompare(a.dataset.date);
    });
    sorted.forEach(function(el) { resultsEl.appendChild(el); });
  }

  search.addEventListener('focus', function() { renderSuggest(search.value); });
  search.addEventListener('input', function() { renderSuggest(search.value); apply(); });
  search.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') { search.blur(); suggest.hidden = true; }
  });
  document.addEventListener('click', function(e) {
    if (!suggest.contains(e.target) && e.target !== search) {
      suggest.hidden = true;
    }
  });

  suggest.addEventListener('mousedown', function(e) {
    var li = e.target.closest('li');
    if (!li) return;
    var kind = li.dataset.kind;
    var slug = li.dataset.slug;
    if (kind === 'topic') { topicSel.value = slug; }
    else if (kind === 'tag') { tagSel.value = slug; }
    search.value = '';
    suggest.hidden = true;
    apply();
  });

  topicSel.addEventListener('change', apply);
  tagSel.addEventListener('change', apply);
  sortSel.addEventListener('change', apply);
  clearBtn.addEventListener('click', function() {
    search.value = '';
    topicSel.value = '';
    tagSel.value = '';
    sortSel.value = 'date';
    suggest.hidden = true;
    apply();
  });

  // Reuse the title-rewrite toggle on archive titles too
  var rewriteOn;
  try { rewriteOn = localStorage.getItem('kw-rewrite-titles') === 'on'; } catch (e) { rewriteOn = false; }
  if (rewriteOn) {
    document.querySelectorAll('h3.archive-title[data-rewritten]').forEach(function(el) {
      var a = el.querySelector('a');
      if (a) a.textContent = el.dataset.rewritten;
    });
  }

  apply();
})();
</script>'''


def render_settings_page() -> str:
    canonical = f"{SITE_URL}/settings/"
    title = "Settings · malpern's keyboard wire"
    desc = "Display options and source pipelines for malpern's keyboard wire."

    sources = [
        {
            "name": "Hacker News",
            "schedule": "Daily, 5:02 PT",
            "model": "Local Qwen3.6 (35B-a3b) on Ollama",
            "description": (
                "Searches the Hacker News Algolia API for stories matching a curated "
                "list of mechanical-keyboard / firmware / tooling terms (mechanical keyboard, "
                "keyboard, keycap, cherry mx, QMK, ZMK, KMK, split keyboard, Karabiner, "
                "Raycast, kanata, key remapping, keyboard firmware) over the last 24h. "
                "Local Qwen3.6 filters out false positives (piano keyboards, generic 'shortcut' "
                "noise) using a strict prompt with worked examples."
            ),
            "endpoints": [
                "https://hn.algolia.com/api/v1/search",
            ],
            "label": "hn",
        },
        {
            "name": "Reddit",
            "schedule": "Daily, 5:03 PT",
            "model": "Local Qwen3.6 (35B-a3b) on Ollama",
            "description": (
                "Scans r/olkb, r/zmk, r/MechanicalKeyboards, r/ErgoMechKeyboards, r/KeyboardLayouts "
                "for new posts in the last 24h. Also runs Reddit search for kanata-related queries "
                "(jtroo kanata, key remapping software). Filters by upvotes and "
                "comment count to skip low-engagement posts; always includes kanata-tagged items "
                "regardless of engagement."
            ),
            "endpoints": [
                "https://www.reddit.com/r/<sub>/new/.json",
                "https://www.reddit.com/search.json?q=<query>",
            ],
            "label": "reddit",
        },
        {
            "name": "Gmail (Keyboard label)",
            "schedule": "Daily, 5:04 PT",
            "model": "Local Qwen3.6 (35B-a3b) on Ollama",
            "description": (
                "Pulls messages from the personal Gmail \"Keyboard\" label (and sublabels like "
                "Keyboard/Tech) from the last 24h. Reddit notification emails are filtered out "
                "to avoid duplicating the Reddit pipeline. For each remaining email, Qwen "
                "extracts the primary article link, a one-line takeaway, and a clean title from "
                "the body — turning vendor newsletters and indie keyboard blogs into wire items."
            ),
            "endpoints": [
                "gog gmail search -a malpern@gmail.com -j 'label:Keyboard newer_than:1d'",
            ],
            "label": "email",
        },
    ]

    sources_html = ""
    for s in sources:
        endpoints_html = "".join(
            f'<li><code>{html.escape(e)}</code></li>' for e in s.get("endpoints", [])
        )
        sources_html += f'''
    <article class="source-card">
      <header class="source-card-header">
        <span class="source-card-label">{html.escape(s["label"])}</span>
        <h3 class="source-card-name">{html.escape(s["name"])}</h3>
      </header>
      <dl class="source-card-meta">
        <dt>Schedule</dt><dd>{html.escape(s["schedule"])}</dd>
        <dt>Model</dt><dd>{html.escape(s["model"])}</dd>
      </dl>
      <p class="source-card-desc">{html.escape(s["description"])}</p>
      <ul class="source-card-endpoints">{endpoints_html}</ul>
    </article>'''

    return f'''{head(title, desc, canonical)}
  {site_header(canonical)}
  <section class="settings">
    <h2 class="settings-section-label">Display</h2>
    <div class="setting-row">
      <label class="toggle">
        <input type="checkbox" id="rewrite-toggle">
        <span class="toggle-track"><span class="toggle-thumb"></span></span>
        <span class="toggle-label">
          <strong>Use rewritten titles</strong>
          <small>When off (default), titles appear as originally posted on HN, Reddit, or in your inbox.
          When on, sensational or vague titles are rewritten in a Techmeme-style factual form by a local model.
          Original titles are always preserved and recoverable.</small>
        </span>
      </label>
    </div>

    <h2 class="settings-section-label">Sources</h2>
    <div class="source-cards">{sources_html}
    </div>
  </section>
  <footer><a href="../">home</a></footer>
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

    # --- settings page ---
    (DOCS / "settings").mkdir(parents=True, exist_ok=True)
    (DOCS / "settings" / "index.html").write_text(render_settings_page())

    # --- archive page ---
    (DOCS / "archive").mkdir(parents=True, exist_ok=True)
    (DOCS / "archive" / "index.html").write_text(
        render_archive_page(corpus, topics_reg, tags_reg)
    )

    n_days = len(corpus["days"])
    n_items = sum(len(d.get("items", [])) for d in corpus["days"])
    n_topics_used = sum(1 for v in by_topic.values() if v)
    n_tags = len(by_tag)
    print(f"generated: {n_days} days, {n_items} items, "
          f"{n_topics_used}/{len(topics_reg)} topics, {n_tags} tags")


if __name__ == "__main__":
    main()

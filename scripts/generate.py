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
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

from cluster import cluster_items

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "corpus.json"
TOPICS_FILE = ROOT / "data" / "topics.json"
TAGS_FILE = ROOT / "data" / "tags.json"
DOCS = ROOT / "docs"
SITE_URL = "https://keyboard-newswire.com"

# Sources quarantined from the main news surfaces (index, RSS, archive,
# Slack, X, email, non-GB topic/tag pages). They render only on the
# dedicated /groupbuys/ page and the auto-generated
# /topics/group-buys-vendors/ page. See docs/GB_IC_FEED.md.
GB_SOURCES = {"geekhack", "shopify"}
GB_TOPIC_SLUG = "group-buys-vendors"


def is_gb(item: dict) -> bool:
    return (item.get("source") or "") in GB_SOURCES


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
    src = (item.get("source") or "").lower()
    if src in ("hn", "hackernews", "hacker-news", "hacker news"):
        return "Hacker News"
    if src == "email":
        return f"✉ {item.get('via') or 'Inbox'}"
    sub = item.get("subreddit")
    if sub:
        return f"r/{sub}"
    if src == "reddit":
        return "Reddit"
    # Every non-Reddit pilot sets `via` to a human-readable attribution
    # (kbdnews → "KBD.news", geekhack → "Geekhack · Group Buys", etc.).
    # Fall back to a capitalized source slug if `via` is missing.
    via = item.get("via")
    if via:
        return via
    return src.capitalize() if src else "Source"


def source_domain(item: dict) -> str:
    """Best-effort favicon hostname for an item."""
    src = item.get("source")
    if src == "reddit":
        return "reddit.com"
    if src == "email":
        sender = item.get("sender") or ""
        m = re.search(r"<[^>]*@([^>\s]+)>", sender)
        if m:
            return m.group(1).lower()
        # fall through to url-based
    if src in ("hn", "email"):
        try:
            host = (urlparse(item.get("url") or "").hostname or "").lower()
        except Exception:
            host = ""
        if host.startswith("www."):
            host = host[4:]
        # Skip Gmail thread URLs as favicon source
        if host.endswith("mail.google.com"):
            return ""
        return host
    return ""


def favicon_url(domain: str) -> str:
    if not domain:
        return ""
    return f"https://icons.duckduckgo.com/ip3/{domain}.ico"


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
    share_path = DOCS / "post" / "assets" / "share-final.png"
    share_v = int(share_path.stat().st_mtime) if share_path.exists() else 1
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<link rel="stylesheet" href="{relative_to_docs(canonical, "style.css")}?v={css_v}">
<link rel="icon" type="image/svg+xml" href="{relative_to_docs(canonical, "favicon.svg")}">
{feed_link}
<link rel="canonical" href="{html.escape(canonical)}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(description)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{html.escape(canonical)}">
<meta property="og:image" content="{SITE_URL}/post/assets/share-final.png?v={share_v}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{SITE_URL}/post/assets/share-final.png?v={share_v}">
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
    # Controls are now revealed via long-press on a title; nothing in the header.
    return ""


def subscribe_dialog(canonical: str) -> str:
    icon_path = relative_to_docs(canonical, "post/assets/icon-final.png")
    return f'''<dialog id="subscribe-dialog" class="subscribe-dialog" aria-labelledby="subscribe-title">
      <form method="dialog" class="subscribe-close-form">
        <button class="subscribe-close" type="submit" value="cancel" aria-label="Close">×</button>
      </form>
      <div class="subscribe-content">
        <img src="{icon_path}" alt="" class="subscribe-icon" width="40" height="40">
        <h2 id="subscribe-title" class="subscribe-title">One short email a day.</h2>
        <p class="subscribe-pitch">All thock, no clack.</p>
        <form id="subscribe-form" class="subscribe-form"
              action="https://buttondown.com/api/emails/embed-subscribe/keyboard-newswire"
              method="post">
          <label for="subscribe-email" class="visually-hidden">Email address</label>
          <input type="email" name="email" id="subscribe-email" placeholder="you@example.com"
                 autocomplete="email" required>
          <button type="submit" class="subscribe-submit">
            <span class="subscribe-submit-label">Subscribe</span>
            <span class="subscribe-submit-spinner" aria-hidden="true"></span>
          </button>
        </form>
        <div class="subscribe-success" hidden>
          <svg class="subscribe-check" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
            <path fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"
                  d="M5 12.5l4.5 4.5L19 7"/>
          </svg>
          <span>Subscribed — check your inbox to confirm.</span>
        </div>
        <p class="subscribe-error" hidden role="alert"></p>
      </div>
    </dialog>'''


def subscribe_card(canonical: str) -> str:
    icon_path = relative_to_docs(canonical, "post/assets/icon-final.png")
    return f'''<aside id="subscribe-card" class="subscribe-card" hidden aria-label="Subscribe to keyboard wire">
      <button type="button" class="subscribe-card-close" aria-label="Dismiss">×</button>
      <div class="subscribe-card-body">
        <img src="{icon_path}" alt="" class="subscribe-card-icon" width="32" height="32">
        <div class="subscribe-card-text">
          <p class="subscribe-card-pitch"><strong>One short email a day.</strong> All thock, no clack.</p>
        <form class="subscribe-form subscribe-card-form"
              action="https://buttondown.com/api/emails/embed-subscribe/keyboard-newswire"
              method="post">
          <label for="subscribe-card-email" class="visually-hidden">Email address</label>
          <input type="email" name="email" id="subscribe-card-email" placeholder="you@example.com"
                 autocomplete="email" required>
          <button type="submit" class="subscribe-submit">
            <span class="subscribe-submit-label">Subscribe</span>
            <span class="subscribe-submit-spinner" aria-hidden="true"></span>
          </button>
        </form>
        <div class="subscribe-success subscribe-card-success" hidden>
          <svg class="subscribe-check" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"
                  d="M5 12.5l4.5 4.5L19 7"/>
          </svg>
          <span>Subscribed — check your inbox.</span>
        </div>
          <p class="subscribe-error subscribe-card-error" hidden role="alert"></p>
        </div>
      </div>
    </aside>'''


def site_header(canonical: str) -> str:
    is_root = canonical == f"{SITE_URL}/"
    home_path = relative_to_docs(canonical, "")
    home = "" if is_root else f'<a href="{home_path}">home</a><span aria-hidden="true">·</span>'
    feed_path = relative_to_docs(canonical, "feed.xml")
    settings_path = relative_to_docs(canonical, "settings/")
    archive_path = relative_to_docs(canonical, "archive/")
    buylist_path = relative_to_docs(canonical, "buylist/")
    groupbuys_path = relative_to_docs(canonical, "groupbuys/")

    def aria_current(page_url: str) -> str:
        return ' aria-current="page"' if canonical == page_url else ""

    archive_attr = aria_current(f"{SITE_URL}/archive/")
    groupbuys_attr = aria_current(f"{SITE_URL}/groupbuys/")
    settings_attr = aria_current(f"{SITE_URL}/settings/")
    about_attr = aria_current(f"{SITE_URL}/post/")
    source_path = "https://github.com/malpern/keyboard-newswire"
    icon_path = relative_to_docs(canonical, "post/assets/icon-final.png")
    return f'''<header>
    <div class="site-masthead">
      <a href="{home_path}" class="site-icon-link"><img src="{icon_path}" alt="" class="site-icon"></a>
      <div class="site-masthead-text">
        <h1 class="site-title"><a href="{home_path}">mechanical keyboard newswire</a></h1>
        <p class="tagline">daily mechanical keyboards, firmware &amp; tools</p>
      </div>
    </div>
    <p class="subscribe">
      {home}
      <a href="#" id="subscribe-trigger" data-subscribe>subscribe</a>
      <span aria-hidden="true">·</span>
      <a href="{archive_path}"{archive_attr}>archive</a>
      <span aria-hidden="true">·</span>
      <a href="{groupbuys_path}"{groupbuys_attr}>group buys</a>
      <span aria-hidden="true">·</span>
      <a href="{feed_path}">RSS</a>
      <span aria-hidden="true">·</span>
      <a href="{settings_path}"{settings_attr}>settings</a>
      <span aria-hidden="true">·</span>
      <a href="{relative_to_docs(canonical, 'post/')}"{about_attr}>about</a>
      {font_controls()}
    </p>
    {subscribe_dialog(canonical)}
    {subscribe_card(canonical)}
  </header>'''


def site_footer() -> str:
    return '''<footer>
    mechanical keyboard newswire · auto-curated
    <a href="post/">about</a>
    <a href="feed.xml">RSS</a>
    <a href="https://github.com/malpern/keyboard-newswire">source</a>
    <a href="topics/">topics</a>
    <a href="tags/">tags</a>
  </footer>'''


def font_script() -> str:
    # Wraps font scale, rewrite toggle, buylist toggle, long-press popover,
    # and Web Speech audio. Single IIFE so all features share state cleanly.
    return '''<script>
(function() {
  // ── Font scale (persisted) ──────────────────────────────
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
  }
  var scale = readScale();
  applyScale(scale);

  // ── Title rewriting toggle (persisted, default off) ─────
  var RK = 'kw-rewrite-titles';
  function readRewrite() {
    try { return localStorage.getItem(RK) === 'on'; } catch (e) { return false; }
  }
  function writeRewrite(on) {
    try { localStorage.setItem(RK, on ? 'on' : 'off'); } catch (e) {}
  }
  function applyRewrite(on) {
    document.querySelectorAll('h3.item-title[data-rewritten]').forEach(function(el) {
      var a = el.querySelector('a');
      if (a) {
        if (!a.dataset.original) a.dataset.original = a.textContent;
        a.textContent = on ? el.dataset.rewritten : a.dataset.original;
      }
    });
    document.querySelectorAll('h3.archive-title[data-rewritten]').forEach(function(el) {
      var a = el.querySelector('a');
      if (a) {
        if (!a.dataset.original) a.dataset.original = a.textContent;
        a.textContent = on ? el.dataset.rewritten : a.dataset.original;
      }
    });
  }

  // ── Buylist (localStorage-backed) ───────────────────────
  var BL = 'kw-buylist';
  function readBL() {
    try { return JSON.parse(localStorage.getItem(BL)) || []; } catch (e) { return []; }
  }
  function writeBL(list) {
    try { localStorage.setItem(BL, JSON.stringify(list)); } catch (e) {}
  }
  function inBL(id) { return readBL().some(function(i){ return i.id === id; }); }
  function refreshNavCount() {
    var n = readBL().length;
    var el = document.querySelector('#nav-buylist .buylist-count');
    if (!el) return;
    if (n > 0) { el.textContent = ' (' + n + ')'; el.hidden = false; }
    else { el.textContent = ''; el.hidden = true; }
  }
  function buylistAdd(itemEl) {
    var list = readBL();
    var id = itemEl.dataset.id;
    if (!id || list.some(function(i){ return i.id === id; })) return false;
    list.unshift({
      id: id,
      title: itemEl.dataset.title,
      url: itemEl.dataset.url,
      source: itemEl.dataset.source,
      favicon: itemEl.dataset.favicon,
      date: itemEl.dataset.date,
      addedAt: new Date().toISOString().slice(0, 10),
    });
    writeBL(list);
    refreshNavCount();
    return true;
  }
  function buylistRemove(id) {
    var list = readBL();
    var idx = list.findIndex(function(i){ return i.id === id; });
    if (idx < 0) return false;
    list.splice(idx, 1);
    writeBL(list);
    refreshNavCount();
    return true;
  }

  // ── Long-press detection (Pointer Events) ───────────────
  function attachLongPress(rootEl, selector, onTrigger, opts) {
    opts = opts || {};
    var ms = opts.ms || 500;
    var timer = null;
    var startX = 0, startY = 0;
    var anchor = null;
    function down(e) {
      var t = e.target.closest(selector);
      if (!t) return;
      // Skip if pressing a real link/button inside (for items, the title
      // anchor and the play button shouldn't trigger card long-press)
      if (opts.exclude && e.target.closest(opts.exclude)) return;
      anchor = t;
      startX = e.clientX; startY = e.clientY;
      clearTimeout(timer);
      timer = setTimeout(function() {
        if (!anchor) return;
        try { navigator.vibrate && navigator.vibrate(15); } catch (err) {}
        onTrigger(anchor, startX, startY);
        anchor = null;
      }, ms);
    }
    function move(e) {
      if (!anchor) return;
      if (Math.abs(e.clientX - startX) > 8 || Math.abs(e.clientY - startY) > 8) {
        clearTimeout(timer); anchor = null;
      }
    }
    function cancel() { clearTimeout(timer); anchor = null; }
    rootEl.addEventListener('pointerdown', down);
    rootEl.addEventListener('pointermove', move);
    rootEl.addEventListener('pointerup', cancel);
    rootEl.addEventListener('pointercancel', cancel);
    rootEl.addEventListener('pointerleave', cancel);
  }

  // ── Popover ─────────────────────────────────────────────
  var popover = null;
  function ensurePopover() {
    if (popover) return popover;
    popover = document.createElement('div');
    popover.id = 'kw-popover';
    popover.className = 'popover';
    popover.setAttribute('role', 'menu');
    popover.hidden = true;
    document.body.appendChild(popover);
    document.addEventListener('click', function(e) {
      if (!popover.hidden && !popover.contains(e.target)) hidePopover();
    });
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') hidePopover();
    });
    window.addEventListener('resize', hidePopover);
    window.addEventListener('scroll', hidePopover, { passive: true });
    return popover;
  }
  function showPopover(items, x, y) {
    var p = ensurePopover();
    p.innerHTML = items.map(function(it) {
      if (it.kind === 'row') {
        return '<div class="popover-row">' + it.children.map(function(c) {
          return '<button type="button" data-act="' + c.act + '" data-arg="' + (c.arg || '') + '">' + c.label + '</button>';
        }).join('') + '</div>';
      }
      if (it.kind === 'sep') return '<div class="popover-sep"></div>';
      if (it.kind === 'note') return '<div class="popover-note">' + it.text + '</div>';
      return '<button type="button" data-act="' + it.act + '" data-arg="' + (it.arg || '') + '"' +
        (it.muted ? ' class="muted"' : '') + '>' + it.label + '</button>';
    }).join('');
    p.hidden = false;
    var pw = p.offsetWidth, ph = p.offsetHeight;
    var pad = 10;
    var px = Math.min(window.innerWidth - pw - pad, Math.max(pad, x - pw / 2));
    var py = y + 8;
    if (py + ph > window.innerHeight - pad) py = Math.max(pad, y - ph - 8);
    p.style.left = px + 'px';
    p.style.top = py + 'px';
  }
  function hidePopover() {
    if (popover) { popover.hidden = true; popover.innerHTML = ''; }
  }

  function handlePopoverClick(e) {
    var btn = e.target.closest('button[data-act]');
    if (!btn) return;
    var act = btn.dataset.act;
    var arg = btn.dataset.arg || '';
    if (act === 'font-down') {
      scale = clampScale(scale - STEP); applyScale(scale); writeScale(scale);
      // Refresh popover labels
      buildTitlePopover();
    } else if (act === 'font-up') {
      scale = clampScale(scale + STEP); applyScale(scale); writeScale(scale);
      buildTitlePopover();
    } else if (act === 'rewrite-toggle') {
      var on = !readRewrite();
      writeRewrite(on); applyRewrite(on);
      buildTitlePopover();
    } else if (act === 'buylist-add') {
      var el = document.querySelector('.item[data-id="' + arg.replace(/"/g, '\\\\"') + '"]');
      if (el) buylistAdd(el);
      hidePopover();
    } else if (act === 'buylist-remove') {
      buylistRemove(arg);
      hidePopover();
      // Trigger buylist page re-render if present
      if (window.kwBuylistRender) window.kwBuylistRender();
    } else if (act === 'buylist-move') {
      var parts = arg.split('|');
      kwBuylistMove(parts[0], parts[1]);
    }
  }

  // Re-build the title popover (font controls + rewrite toggle) keeping
  // its position since user is still holding it.
  var lastTitlePopX = 0, lastTitlePopY = 0;
  function buildTitlePopover() {
    var rewriteOn = readRewrite();
    var atMin = scale <= MIN + 0.001;
    var atMax = scale >= MAX - 0.001;
    showPopover([
      { kind: 'note', text: 'Text size · ' + Math.round(scale * 100) + '%' },
      { kind: 'row', children: [
        { act: 'font-down', label: 'A−', disabled: atMin },
        { act: 'font-up', label: 'A+', disabled: atMax },
      ]},
      { kind: 'sep' },
      { act: 'rewrite-toggle', label: (rewriteOn ? '✓ ' : '') + 'Use rewritten titles' },
    ], lastTitlePopX, lastTitlePopY);
    // Apply disabled state
    if (popover) {
      if (atMin) { var b = popover.querySelector('[data-act="font-down"]'); if (b) b.disabled = true; }
      if (atMax) { var b2 = popover.querySelector('[data-act="font-up"]'); if (b2) b2.disabled = true; }
    }
  }

  function buildItemPopover(itemEl, x, y) {
    var id = itemEl.dataset.id;
    var saved = inBL(id);
    showPopover([
      saved
        ? { act: 'buylist-remove', arg: id, label: '♥ Want to buy' }
        : { act: 'buylist-add', arg: id, label: '♡ Want to buy' },
    ], x, y);
  }

  // Buylist row move (used by buylist page)
  function kwBuylistMove(id, dir) {
    var list = readBL();
    var idx = list.findIndex(function(i){ return i.id === id; });
    if (idx < 0) return;
    var to;
    if (dir === 'top') to = 0;
    else if (dir === 'bottom') to = list.length - 1;
    else if (dir === 'up') to = Math.max(0, idx - 1);
    else if (dir === 'down') to = Math.min(list.length - 1, idx + 1);
    else return;
    var moved = list.splice(idx, 1)[0];
    list.splice(to, 0, moved);
    writeBL(list);
    hidePopover();
    if (window.kwBuylistRender) window.kwBuylistRender();
  }
  window.kwBuylistMove = kwBuylistMove;

  // Hooks for the buylist page to ask us to render its row popover
  window.kwShowBuylistPopover = function(li, x, y) {
    var id = li.dataset.id;
    showPopover([
      { kind: 'row', children: [
        { act: 'buylist-move', arg: id + '|up', label: '↑ Up' },
        { act: 'buylist-move', arg: id + '|down', label: '↓ Down' },
      ]},
      { kind: 'row', children: [
        { act: 'buylist-move', arg: id + '|top', label: 'Top' },
        { act: 'buylist-move', arg: id + '|bottom', label: 'Bottom' },
      ]},
      { kind: 'sep' },
      { act: 'buylist-remove', arg: id, label: 'Remove from list' },
    ], x, y);
  };

  document.addEventListener('DOMContentLoaded', function() {
    ensurePopover();
    popover.addEventListener('click', handlePopoverClick);

    applyScale(scale);
    applyRewrite(readRewrite());
    refreshNavCount();

    // Long-press on title → font/rewrite controls
    attachLongPress(document, '.item-title, .archive-title, .bl-title', function(el, x, y) {
      lastTitlePopX = x; lastTitlePopY = y;
      buildTitlePopover();
    });

    // Long-press on item card → buylist add/remove (excluding clicks on the
    // title link, the play button, or any nested anchor)
    attachLongPress(document, '.item', function(el, x, y) {
      buildItemPopover(el, x, y);
    }, { exclude: '.item-title a, .item-meta a, .item-topmeta a, .play-day' });

    initSpeech();
  });

  function initSpeech() {
    if (!('speechSynthesis' in window)) {
      // No support → hide play buttons
      document.querySelectorAll('.play-day').forEach(function(b){ b.hidden = true; });
      return;
    }
    var synth = window.speechSynthesis;
    var queue = [];          // remaining utterances for current playback
    var currentBtn = null;
    var voicesReady = false;

    function preferredVoice() {
      var voices = synth.getVoices();
      if (!voices.length) return null;
      // Prefer Apple/Google premium English voices, then any en-US, then any English
      var pickers = [
        function(v) { return /en[-_]US/i.test(v.lang) && /(premium|enhanced|neural|samantha|nova)/i.test(v.name); },
        function(v) { return /en[-_]US/i.test(v.lang); },
        function(v) { return /^en/i.test(v.lang); },
      ];
      for (var i = 0; i < pickers.length; i++) {
        var v = voices.find(pickers[i]);
        if (v) return v;
      }
      return voices[0];
    }
    if (synth.onvoiceschanged !== undefined) {
      synth.onvoiceschanged = function() { voicesReady = true; };
    }

    function clean(s) {
      return (s || '').replace(/\\s+/g, ' ').trim();
    }
    function buildChunks(dayEl) {
      // Content-focused: title + takeaway only, one chunk per item. No preamble,
      // no source attribution, no "N stories". The item element comes back so
      // we can highlight + scroll to it as it speaks.
      var items = Array.from(dayEl.querySelectorAll('.item'));
      var chunks = [];
      items.forEach(function(it) {
        var titleEl = it.querySelector('.item-title');
        var title = clean(titleEl ? titleEl.textContent : '');
        var take = clean((it.querySelector('.item-takeaway') || {}).textContent);
        if (!title) return;
        var line = title + (/[.!?]$/.test(title) ? '' : '.');
        if (take) line += ' ' + take + (/[.!?]$/.test(take) ? '' : '.');
        chunks.push({ text: line, el: it });
      });
      return chunks;
    }

    function setBtnState(btn, state) {
      if (!btn) return;
      btn.classList.toggle('playing', state === 'playing');
      btn.classList.toggle('paused', state === 'paused');
      btn.setAttribute('aria-label',
        state === 'playing' ? 'Pause' : (state === 'paused' ? 'Resume' : "Play this day's stories")
      );
    }

    function clearActiveItem() {
      document.querySelectorAll('.item.now-playing').forEach(function(el) {
        el.classList.remove('now-playing');
      });
    }
    function setActiveItem(el) {
      clearActiveItem();
      if (!el) return;
      el.classList.add('now-playing');
      // Scroll into a comfortable position: roughly 1/3 from the top
      var r = el.getBoundingClientRect();
      var target = window.scrollY + r.top - window.innerHeight * 0.25;
      window.scrollTo({ top: target, behavior: 'smooth' });
    }

    function stopAll() {
      queue = [];
      synth.cancel();
      setBtnState(currentBtn, 'idle');
      clearActiveItem();
      currentBtn = null;
    }

    function speakNext(btn) {
      if (queue.length === 0) {
        setBtnState(btn, 'idle');
        clearActiveItem();
        currentBtn = null;
        return;
      }
      var chunk = queue.shift();
      setActiveItem(chunk.el);
      var u = new SpeechSynthesisUtterance(chunk.text);
      var v = preferredVoice();
      if (v) u.voice = v;
      u.rate = 1.0;
      u.pitch = 1.0;
      u.onend = function() { speakNext(btn); };
      u.onerror = function() { speakNext(btn); };
      synth.speak(u);
    }

    document.addEventListener('click', function(e) {
      var btn = e.target.closest('.play-day');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();

      // Same button → toggle pause/resume or stop
      if (btn === currentBtn) {
        if (synth.paused) {
          synth.resume();
          setBtnState(btn, 'playing');
        } else if (synth.speaking) {
          synth.pause();
          setBtnState(btn, 'paused');
        } else {
          stopAll();
        }
        return;
      }

      // Different day → stop current, start new
      stopAll();
      var dayEl = btn.closest('.day');
      if (!dayEl) return;
      queue = buildChunks(dayEl);
      currentBtn = btn;
      setBtnState(btn, 'playing');
      speakNext(btn);
    });

    // Best-effort warm-up: trigger getVoices() on first user interaction so iOS
    // populates the voice list before play.
    document.addEventListener('touchstart', function once() {
      synth.getVoices();
      document.removeEventListener('touchstart', once);
    }, { once: true, passive: true });
  }

  // ── Subscribe (Buttondown) ──────────────────────────────
  var SUB_DISMISS_KEY = 'kw-sub-card-dismissed';
  var SUB_DONE_KEY = 'kw-sub-done';
  function subDismissed() {
    try { return localStorage.getItem(SUB_DISMISS_KEY) === '1'; } catch (e) { return false; }
  }
  function subMarkDismissed() {
    try { localStorage.setItem(SUB_DISMISS_KEY, '1'); } catch (e) {}
  }
  function subDone() {
    try { return localStorage.getItem(SUB_DONE_KEY) === '1'; } catch (e) { return false; }
  }
  function subMarkDone() {
    try { localStorage.setItem(SUB_DONE_KEY, '1'); } catch (e) {}
  }

  function wireSubscribeForm(form, onSuccess) {
    if (!form) return;
    var emailEl = form.querySelector('input[type="email"]');
    var submitBtn = form.querySelector('.subscribe-submit');
    var successEl = form.parentElement.querySelector('.subscribe-success');
    var errorEl = form.parentElement.querySelector('.subscribe-error');
    form.addEventListener('submit', function(e) {
      e.preventDefault();
      var email = (emailEl.value || '').trim();
      if (!email) return;
      if (errorEl) { errorEl.hidden = true; errorEl.textContent = ''; }
      submitBtn.classList.add('is-loading');
      submitBtn.disabled = true;
      var data = new FormData();
      data.append('email', email);
      fetch(form.action, { method: 'POST', body: data, mode: 'no-cors' })
        .then(function() {
          form.style.display = 'none';
          if (successEl) {
            successEl.hidden = false;
            requestAnimationFrame(function(){ successEl.classList.add('is-shown'); });
          }
          subMarkDone();
          if (onSuccess) onSuccess();
        })
        .catch(function() {
          if (errorEl) {
            errorEl.textContent = 'Something went wrong — try again?';
            errorEl.hidden = false;
          }
        })
        .finally(function() {
          submitBtn.classList.remove('is-loading');
          submitBtn.disabled = false;
        });
    });
  }

  // Header-nav dialog
  var subTrigger = document.getElementById('subscribe-trigger');
  var subDialog = document.getElementById('subscribe-dialog');
  if (subTrigger && subDialog) {
    var subDialogForm = subDialog.querySelector('#subscribe-form');
    var subDialogEmail = subDialog.querySelector('#subscribe-email');
    function openSub() {
      var err = subDialog.querySelector('.subscribe-error');
      if (err) { err.hidden = true; err.textContent = ''; }
      if (typeof subDialog.showModal === 'function') subDialog.showModal();
      else subDialog.setAttribute('open', '');
      requestAnimationFrame(function(){ subDialogEmail && subDialogEmail.focus(); });
    }
    subTrigger.addEventListener('click', function(e) { e.preventDefault(); openSub(); });
    subDialog.addEventListener('click', function(e) {
      if (e.target === subDialog) subDialog.close();
    });
    wireSubscribeForm(subDialogForm, function() {
      // After dialog success, also hide the bottom card if visible
      var card = document.getElementById('subscribe-card');
      if (card) hideSubCard(card);
    });
    // Deep-link: open dialog automatically for ?subscribe=1 or #subscribe
    var search = (window.location.search || '');
    var hash = (window.location.hash || '').toLowerCase();
    if (/[?&](subscribe|sub|s)(=1)?(&|$)/.test(search) || hash === '#subscribe') {
      // Wait a tick so layout settles before showing the modal
      setTimeout(openSub, 50);
    }
  }

  // Bottom slide-up card (scroll-triggered, dismissible)
  var subCard = document.getElementById('subscribe-card');
  function hideSubCard(card) {
    card.classList.remove('is-shown');
    setTimeout(function(){ card.hidden = true; }, 300);
  }
  if (subCard && !subDismissed() && !subDone()) {
    var subCardForm = subCard.querySelector('.subscribe-card-form');
    wireSubscribeForm(subCardForm);
    var closeBtn = subCard.querySelector('.subscribe-card-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', function() {
        subMarkDismissed();
        hideSubCard(subCard);
      });
    }
    var shown = false;
    function maybeShowSubCard() {
      if (shown) return;
      // Trigger after the user has scrolled past one viewport's worth of content
      var threshold = window.innerHeight * 0.9;
      if (window.scrollY > threshold) {
        shown = true;
        subCard.hidden = false;
        // Force reflow before adding the class so the transition runs
        // eslint-disable-next-line no-unused-expressions
        subCard.offsetHeight;
        subCard.classList.add('is-shown');
        window.removeEventListener('scroll', onScroll);
      }
    }
    var scrollTimer = null;
    function onScroll() {
      if (scrollTimer) return;
      scrollTimer = setTimeout(function() {
        scrollTimer = null;
        maybeShowSubCard();
      }, 80);
    }
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  // ── GB/IC image carousel (dot sync, chevrons, keyboard nav) ──
  function initGbCarousels() {
    var carousels = document.querySelectorAll('[data-gb-carousel]');
    if (!carousels.length) return;
    Array.prototype.forEach.call(carousels, function(c) {
      var track = c.querySelector('.gb-track');
      var dots = c.querySelectorAll('.gb-dot');
      var navs = c.querySelectorAll('.gb-nav');
      if (!track || !dots.length) return;

      function currentIndex() {
        var w = c.clientWidth;
        if (w === 0) return 0;
        return Math.round(track.scrollLeft / w);
      }
      function setCurrent(idx) {
        idx = Math.max(0, Math.min(dots.length - 1, idx));
        for (var i = 0; i < dots.length; i++) {
          if (i === idx) dots[i].setAttribute('aria-current', 'true');
          else dots[i].removeAttribute('aria-current');
        }
      }
      function scrollTo(idx) {
        idx = Math.max(0, Math.min(dots.length - 1, idx));
        track.scrollTo({ left: idx * c.clientWidth, behavior: 'smooth' });
      }

      var scrollTick = null;
      track.addEventListener('scroll', function() {
        if (scrollTick) return;
        scrollTick = requestAnimationFrame(function() {
          scrollTick = null;
          setCurrent(currentIndex());
        });
      }, { passive: true });

      Array.prototype.forEach.call(dots, function(dot) {
        dot.addEventListener('click', function() {
          var idx = parseInt(dot.getAttribute('data-slide'), 10) || 0;
          scrollTo(idx);
        });
      });

      Array.prototype.forEach.call(navs, function(btn) {
        btn.addEventListener('click', function() {
          var dir = parseInt(btn.getAttribute('data-dir'), 10) || 0;
          scrollTo(currentIndex() + dir);
        });
      });

      c.addEventListener('keydown', function(e) {
        if (e.key === 'ArrowLeft')  { e.preventDefault(); scrollTo(currentIndex() - 1); }
        else if (e.key === 'ArrowRight') { e.preventDefault(); scrollTo(currentIndex() + 1); }
        else if (e.key === 'Home')  { e.preventDefault(); scrollTo(0); }
        else if (e.key === 'End')   { e.preventDefault(); scrollTo(dots.length - 1); }
      });
    });
  }
  initGbCarousels();

  // ── GB image lightbox (click carousel slide to expand) ──
  // Two display modes per loaded image:
  //   FIT:    object-fit:contain, sized to viewport (default on open)
  //   ACTUAL: shown at natural pixel dimensions, click/drag pans.
  //           If data-full points at a larger remote source, load it
  //           on entry so users see full-quality original.
  // Click image toggles modes; click-without-move during drag stays
  // in ACTUAL (a real click toggles, a drag does not).
  function initGbLightbox() {
    var lb = document.getElementById('gb-lightbox');
    if (!lb) return;
    var lbImg = lb.querySelector('.gb-lightbox-img');
    var lbClose = lb.querySelector('.gb-lightbox-close');
    var lbPrev = lb.querySelector('.gb-lightbox-prev');
    var lbNext = lb.querySelector('.gb-lightbox-next');
    var lbCount = lb.querySelector('.gb-lightbox-counter');

    var currentSrcs = [];    // array of {fit, full} per slide
    var currentIdx = 0;
    var lastTrigger = null;
    var mode = 'fit';        // 'fit' | 'actual'
    var panX = 0, panY = 0;
    var dragging = false, didDrag = false;
    var dragStartX = 0, dragStartY = 0;
    var pointerDownX = 0, pointerDownY = 0;

    function clampPan() {
      // Center the image; bound pan so its edges can't leave viewport
      // entirely (cap at half-image-overflow).
      var iw = lbImg.naturalWidth || lbImg.width;
      var ih = lbImg.naturalHeight || lbImg.height;
      var vw = window.innerWidth;
      var vh = window.innerHeight;
      var maxX = Math.max(0, (iw - vw) / 2);
      var maxY = Math.max(0, (ih - vh) / 2);
      if (panX >  maxX) panX =  maxX;
      if (panX < -maxX) panX = -maxX;
      if (panY >  maxY) panY =  maxY;
      if (panY < -maxY) panY = -maxY;
    }

    function applyPan() {
      lbImg.style.transform = 'translate(' + panX + 'px,' + panY + 'px)';
    }

    function setFit() {
      mode = 'fit';
      panX = 0; panY = 0;
      lb.classList.remove('gb-lightbox-actual');
      lbImg.style.transform = '';
    }

    function setActual() {
      mode = 'actual';
      panX = 0; panY = 0;
      lb.classList.add('gb-lightbox-actual');
      lbImg.style.transform = '';
      // If the slide carries a data-full URL different from the
      // currently-loaded fit-resolution src, swap to it now. We use
      // the local 1280px crop as the loading-state placeholder until
      // the bigger source arrives.
      var slide = currentSrcs[currentIdx];
      if (slide.full && slide.full !== lbImg.src) {
        lbImg.src = slide.full;
      }
    }

    function show(idx) {
      if (!currentSrcs.length) return;
      currentIdx = (idx + currentSrcs.length) % currentSrcs.length;
      setFit();
      lbImg.src = currentSrcs[currentIdx].fit;
      if (currentSrcs.length > 1) {
        lbCount.textContent = (currentIdx + 1) + ' / ' + currentSrcs.length;
        lbPrev.style.display = '';
        lbNext.style.display = '';
      } else {
        lbCount.textContent = '';
        lbPrev.style.display = 'none';
        lbNext.style.display = 'none';
      }
    }

    function open(srcs, startIdx, trigger) {
      currentSrcs = srcs.slice();
      lastTrigger = trigger || null;
      show(startIdx || 0);
      lb.hidden = false;
      document.body.style.overflow = 'hidden';
      lbClose.focus();
    }

    function close() {
      lb.hidden = true;
      lbImg.src = '';
      setFit();
      document.body.style.overflow = '';
      currentSrcs = [];
      if (lastTrigger && typeof lastTrigger.focus === 'function') {
        lastTrigger.focus();
        lastTrigger = null;
      }
    }

    // Delegate: clicking any slide img opens the lightbox with the
    // full carousel's image list (and data-full sources where set).
    document.addEventListener('click', function(e) {
      var img = e.target;
      if (img.tagName !== 'IMG') return;
      var slide = img.closest('.gb-slide, .gb-carousel-single');
      if (!slide) return;
      var carousel = slide.closest('.gb-carousel, .gb-carousel-single');
      if (!carousel) return;
      var imgs = carousel.querySelectorAll('img');
      var srcs = [];
      var startIdx = 0;
      for (var i = 0; i < imgs.length; i++) {
        srcs.push({
          fit: imgs[i].src,
          full: imgs[i].getAttribute('data-full') || null,
        });
        if (imgs[i] === img) startIdx = i;
      }
      open(srcs, startIdx, img);
      e.preventDefault();
    });

    // Click on backdrop (but not image / controls) closes.
    lb.addEventListener('click', function(e) {
      if (e.target === lbImg) return;
      if (e.target === lbPrev || e.target === lbNext) return;
      if (e.target === lbClose) return;
      close();
    });

    lbClose.addEventListener('click', close);
    lbPrev.addEventListener('click', function() { show(currentIdx - 1); });
    lbNext.addEventListener('click', function() { show(currentIdx + 1); });

    // Image click + drag — pointer events handle both mouse + touch.
    lbImg.addEventListener('pointerdown', function(e) {
      // In fit mode, the click handler below toggles to actual.
      if (mode !== 'actual') return;
      dragging = true;
      didDrag = false;
      pointerDownX = e.clientX;
      pointerDownY = e.clientY;
      dragStartX = e.clientX - panX;
      dragStartY = e.clientY - panY;
      try { lbImg.setPointerCapture(e.pointerId); } catch (_) {}
      e.preventDefault();
    });

    lbImg.addEventListener('pointermove', function(e) {
      if (!dragging) return;
      panX = e.clientX - dragStartX;
      panY = e.clientY - dragStartY;
      clampPan();
      applyPan();
      if (Math.abs(e.clientX - pointerDownX) > 4 ||
          Math.abs(e.clientY - pointerDownY) > 4) {
        didDrag = true;
      }
    });

    function endDrag(e) {
      if (!dragging) return;
      dragging = false;
      try { lbImg.releasePointerCapture(e.pointerId); } catch (_) {}
    }
    lbImg.addEventListener('pointerup', endDrag);
    lbImg.addEventListener('pointercancel', endDrag);

    // Click toggles mode — but only if it was a real click (no drag).
    // In actual mode + a drag happened, swallow the click so we don't
    // accidentally exit zoom.
    lbImg.addEventListener('click', function(e) {
      e.stopPropagation();
      if (mode === 'actual' && didDrag) {
        didDrag = false;
        return;
      }
      if (mode === 'fit') setActual(); else setFit();
    });

    document.addEventListener('keydown', function(e) {
      if (lb.hidden) return;
      if (e.key === 'Escape')         { e.preventDefault(); close(); }
      else if (e.key === 'ArrowLeft')  { e.preventDefault(); show(currentIdx - 1); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); show(currentIdx + 1); }
    });
  }
  initGbLightbox();

  // ── GB vendor pills: client-side geo prioritization ──
  // Detect user's region from browser timezone (or manual override
  // saved in localStorage). Pills whose data-region matches stay
  // expanded; the rest collapse behind a "+N more vendors" toggle.
  // SSR keeps showing every pill so crawlers / feed readers see
  // everyone; JS just narrows the visible set post-load.
  function detectGbRegion() {
    try {
      var override = localStorage.getItem('kw-region');
      if (override) return override;  // includes "__all__" sentinel
    } catch (_) {}
    var tz = '';
    try {
      tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    } catch (_) {}
    if (!tz) return null;
    if (tz === 'Europe/London' || tz === 'Europe/Dublin'
        || tz === 'Europe/Belfast') return 'UK';
    if (tz.indexOf('Europe/') === 0) return 'EU';
    if (tz === 'Asia/Tokyo') return 'JP';
    if (tz === 'Asia/Seoul') return 'KR';
    if (/^Asia\/(Shanghai|Hong_Kong|Chongqing|Urumqi|Taipei|Macau)$/.test(tz))
      return 'CN';
    if (tz === 'Asia/Singapore') return 'SG';
    if (tz.indexOf('Australia/') === 0) return 'AU';
    if (tz === 'Pacific/Auckland') return 'NZ';
    if (/^America\/(Toronto|Vancouver|Edmonton|Winnipeg|Halifax|Montreal|Regina|Moncton|St_Johns|Whitehorse)$/.test(tz))
      return 'CA';
    if (tz.indexOf('America/') === 0) return 'US';
    return null;
  }

  // Region equivalences: an "AU" user is at home with "OC" (Oceania)
  // pills; "EU" pills with subregions like "DE" / "FR" / "ES" also
  // count as near for EU users.
  var NEAR_REGION = {
    AU: ['AU', 'OC', 'NZ'],
    NZ: ['NZ', 'AU', 'OC'],
    OC: ['OC', 'AU', 'NZ'],
    EU: ['EU', 'DE', 'FR', 'NL', 'ES', 'IT', 'PL', 'SE', 'FI', 'DK', 'NO'],
    UK: ['UK'],
    US: ['US'],
    CA: ['CA', 'US'],  // Canadians often buy from US vendors too
    JP: ['JP'],
    KR: ['KR'],
    CN: ['CN'],
    SG: ['SG', 'SEA'],
  };

  function isPillNear(pillRegion, userRegion) {
    if (!userRegion || !pillRegion) return false;
    var nearList = NEAR_REGION[userRegion] || [userRegion];
    return nearList.indexOf(pillRegion) !== -1;
  }

  function initGbVendorGeo() {
    var userRegion = detectGbRegion();
    if (!userRegion) return;  // Unknown timezone — show everyone.
    if (userRegion === '__all__') return;  // explicit "show all" override
    var containers = document.querySelectorAll('[data-gb-vendors]');
    Array.prototype.forEach.call(containers, function(container) {
      var pills = container.querySelectorAll('.gb-vendor-pill');
      if (pills.length < 3) return;  // Don't bother collapsing tiny lists.
      var near = [], far = [];
      for (var i = 0; i < pills.length; i++) {
        var r = pills[i].getAttribute('data-region') || '';
        if (isPillNear(r, userRegion)) near.push(pills[i]);
        else far.push(pills[i]);
      }
      // If 0 or 1 near pills, show everyone — users with few-or-no
      // local vendors shouldn't see "+ 7 more" hiding the only
      // options they have.
      if (near.length < 2 || far.length === 0) return;
      // Mark far pills + hide them; build a toggle.
      Array.prototype.forEach.call(far, function(p) {
        p.classList.add('gb-vendor-pill-far');
        p.hidden = true;
      });
      var toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'gb-vendor-toggle';
      toggle.setAttribute('aria-expanded', 'false');
      toggle.textContent = '+ ' + far.length + ' more worldwide';
      toggle.addEventListener('click', function() {
        var expanded = toggle.getAttribute('aria-expanded') === 'true';
        Array.prototype.forEach.call(far, function(p) {
          p.hidden = expanded;
        });
        toggle.setAttribute('aria-expanded', expanded ? 'false' : 'true');
        toggle.textContent = expanded
          ? ('+ ' + far.length + ' more worldwide')
          : 'show local only';
      });
      container.appendChild(toggle);
    });
  }
  initGbVendorGeo();

  // ── Settings: region picker (only present on /settings/) ──
  // Reads current localStorage on load, writes on change. Empty
  // value clears the override and falls back to timezone detection.
  var regionSelect = document.getElementById('region-select');
  if (regionSelect) {
    try {
      regionSelect.value = localStorage.getItem('kw-region') || '';
    } catch (_) {}
    regionSelect.addEventListener('change', function() {
      try {
        if (regionSelect.value) {
          localStorage.setItem('kw-region', regionSelect.value);
        } else {
          localStorage.removeItem('kw-region');
        }
      } catch (_) {}
    });
  }
})();
</script>'''


def clean_remote_image_url(url: str) -> str:
    """Strip session-bound parameters from a remote image URL so the
    cleaned form stays valid past the session that scraped it.

    Geekhack's native `action=dlattach` URLs include a PHPSESSID
    parameter which expires. The bare attach=NNN URL still resolves
    correctly. Other hosts (imgur, postimg, etc.) are returned as-is.
    """
    if not url:
        return ""
    if "PHPSESSID=" not in url:
        return url
    # Drop PHPSESSID=… with its adjacent separator. Geekhack uses both
    # `&` and `;` as query separators, sometimes in the same URL, so
    # match each leading-or-trailing position explicitly.
    cleaned = url
    # First-param forms — keep the leading `?`, drop the param + its
    # trailing separator.
    cleaned = re.sub(r"\?PHPSESSID=[^&;]+&", "?", cleaned)
    cleaned = re.sub(r"\?PHPSESSID=[^&;]+;", "?", cleaned)
    cleaned = re.sub(r"\?PHPSESSID=[^&;]+$", "", cleaned)
    # Mid- or last-param forms — drop the param including the leading
    # separator.
    cleaned = re.sub(r"[&;]PHPSESSID=[^&;]+", "", cleaned)
    return cleaned


def gb_images(item: dict) -> list[str]:
    """Return ordered list of image paths for a GB item.

    v2.0: pilots emit a single-image array (or just `item.image`).
    Step 1b/2 pilots will emit multi-image arrays directly. The
    render path tolerates both — read `images[]` if present, else
    fall back to `[image]` for backwards compat.
    """
    imgs = item.get("images")
    if imgs:
        return [i for i in imgs if i]
    single = item.get("image")
    return [single] if single else []


def fmt_price_chip(gb: dict) -> str | None:
    """gb.price_low/high are in cents. Returns "$145+" / "$145-160" / None."""
    low = gb.get("price_low")
    high = gb.get("price_high")
    if low is None and high is None:
        return None
    sym = "$" if (gb.get("currency") or "USD") == "USD" else ""
    if low is not None and high is not None and high > low:
        return f"{sym}{low // 100}-{high // 100}"
    val = low if low is not None else high
    return f"{sym}{val // 100}+"


# Known-vendor → region map. Used to label vendor_link pills that
# don't appear in the OP's structured "Vendors US: X" list (the
# vendor was hyperlinked inline). Hand-curated as new vendors show
# up in the corpus; default behavior when host is unknown is to
# omit the region badge entirely (rather than guess wrong).
_KNOWN_VENDOR_REGIONS = {
    # US
    "novelkeys.com":       "US",
    "cannonkeys.com":      "US",
    "bowlkeyboards.com":   "US",
    "saberkeebs.com":      "US",
    "mechsandco.com":      "US",
    "minokeys.com":        "US",
    # CA
    "deskhero.ca":         "CA",
    "www.deskhero.ca":     "CA",
    # UK
    "prototypist.net":     "UK",
    "proto-typist.com":    "UK",
    # EU
    "oblotzky.industries": "EU",
    "mykeyboard.eu":       "EU",
    "keeb.supply":         "EU",
    "coffeekeys.de":       "EU",
    "torokeeb.store":      "EU",
    "www.torokeeb.store":  "EU",
    "delta-key.co":        "EU",
    # KR
    "geon.works":          "KR",
    "geonworks.com":       "KR",
    # CN
    "kbdfans.com":         "CN",
    "typist.club":         "CN",
    "zfrontier.com":       "CN",
    # JP
    "shop.yushakobo.jp":   "JP",
    "yushakobo.jp":        "JP",
    # SG / SEA
    "ilumkb.com":          "SG",
    "monokei.co":          "SG",
    "ktechs.store":        "SG",
    # AU / OC
    "keebzncables.com":     "AU",
    "www.keebzncables.com": "AU",
    "dailyclack.com":      "AU",
}


def infer_vendor_region(host: str | None) -> str | None:
    """Best-effort region inference from a vendor's host. Returns
    None when we'd rather omit the region badge than guess. Prefers
    the explicit known-vendor map; falls back to country-code TLDs."""
    if not host:
        return None
    h = host.lower().lstrip(".")
    if h in _KNOWN_VENDOR_REGIONS:
        return _KNOWN_VENDOR_REGIONS[h]
    # Country-code TLD fallback. Lowercase only.
    tld_map = {
        ".jp": "JP", ".kr": "KR", ".cn": "CN", ".sg": "SG",
        ".au": "AU", ".nz": "NZ", ".ca": "CA",
        ".de": "EU", ".fr": "EU", ".nl": "EU", ".es": "EU",
        ".it": "EU", ".eu": "EU", ".pl": "EU", ".se": "EU",
        ".fi": "EU", ".dk": "EU", ".no": "EU", ".at": "EU",
        ".be": "EU", ".pt": "EU", ".ie": "UK",
        ".uk": "UK", ".co.uk": "UK",
    }
    for tld, region in tld_map.items():
        if h.endswith(tld):
            return region
    return None


_CURRENCY_SYMBOL = {
    "USD": "$", "CAD": "$", "AUD": "$", "NZD": "$",
    "GBP": "£", "EUR": "€", "JPY": "¥", "CNY": "¥",
}


def format_vendor_price(low_cents: int,
                        high_cents: int | None = None,
                        currency: str | None = None) -> str:
    """Format a Shopify-derived price into a compact pill chip.
    `$135`, `£135`, `€135-160`. Currency defaults to USD when unknown.

    Heuristic: when high > 2× low, the low end is almost always a
    cheap add-on (sticker, deskmat, novelties), and the user cares
    about the base-kit price (the high end). Display the high price
    alone in that case to avoid misleading "$3-100" ranges where $3
    is meaningless to the buyer.
    """
    cur = (currency or "USD").upper()
    symbol = _CURRENCY_SYMBOL.get(cur, cur + " ")
    lo = low_cents // 100
    if high_cents is None or high_cents == low_cents:
        return f"{symbol}{lo}"
    hi = high_cents // 100
    if high_cents > 2 * low_cents:
        return f"{symbol}{hi}"
    return f"{symbol}{lo}-{hi}"


def fmt_date_chip(iso: str | None, *, prefix: str) -> str | None:
    """ISO date → "ends Jun 14" style chip text. None if missing."""
    if not iso:
        return None
    try:
        y, m, d = iso.split("-")
        return f"{prefix} {MONTHS[int(m)]} {int(d)}"
    except Exception:
        return None


def unified_vendor_pills(gb: dict) -> list[dict]:
    """Combine `gb.vendor_regions` (parsed from the OP's structured
    "Vendors US: X" list) with `gb.vendor_links` (hyperlinks in the
    OP body) into a single ordered list of pills the render layer
    iterates.

    Output entries: `{region, name, url, price_low, price_high,
    currency, available}`. Region comes from vendor_regions when the
    vendor's name matches; otherwise inferred from the link host
    (infer_vendor_region). Both fields are optional.

    Iteration order: vendor_regions first (preserves OP order), then
    any vendor_links that didn't match a region. Dedup by lowercased
    vendor name + host to avoid double-emit when a vendor appears in
    both lists.
    """
    regions = gb.get("vendor_regions") or []
    links = gb.get("vendor_links") or []
    link_by_name = {}
    for vl in links:
        n = str(vl.get("vendor") or "").strip().lower()
        if n and n not in link_by_name:
            link_by_name[n] = vl

    out: list[dict] = []
    seen_keys: set = set()

    def _emit(region, name, vl):
        if not name:
            return
        key = (name.strip().lower(), (vl.get("host") or "") if vl else "")
        if key in seen_keys:
            return
        seen_keys.add(key)
        entry = {"region": region or None, "name": name.strip()}
        if vl:
            for k in ("url", "host", "price_low", "price_high",
                      "currency", "available"):
                if k in vl:
                    entry[k] = vl[k]
        out.append(entry)

    for r in regions:
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        vl = link_by_name.get(name.lower())
        _emit(r.get("region") or "", name, vl)

    # Orphan vendor_links — not in vendor_regions but still real.
    used_names = {e["name"].lower() for e in out}
    for vl in links:
        name = str(vl.get("vendor") or "").strip()
        if not name or name.lower() in used_names:
            continue
        inferred = infer_vendor_region(vl.get("host"))
        _emit(inferred or "", name, vl)

    return out


def render_gb_item(item: dict, topics_reg: dict, tags_reg: dict, *,
                   date: str | None = None, page: str = "day",
                   rel_prefix: str = "") -> str:
    """Visual-first card for GB/IC items. Image carousel dominates;
    metadata chips populate as ingestors learn to extract them.

    Layout (see docs/GB_CARD_DESIGN.md):
        [GB] title
        Vendor · source
        ┌──────── carousel ────────┐
        │  ● ○ ○ ○                 │  (dots overlaid bottom-center)
        └──────────────────────────┘
        status · MOQ · price · ends
        designer · profile · material
        takeaway
        engagement + open link
    """
    title = item.get("title") or ""
    raw_type = (item.get("type") or "").upper()
    # IC → GB auto-graduate: once an Interest Check thread has vendors
    # listed (≥1 link in gb.vendor_links), the designer has committed
    # — render and route it as a GB. The data still says type=IC; we
    # just stop using the speculative-stage chrome for the card.
    is_ic = raw_type == "IC" and not (item.get("gb") or {}).get("vendor_links")
    # Strip "[GB] " / "[IC] " prefix from displayed title — the chip
    # carries that info now, no need to double-encode.
    display_title = title
    if raw_type in ("GB", "IC"):
        prefix = f"[{raw_type}]"
        if display_title.lstrip().upper().startswith(prefix):
            display_title = display_title.lstrip()[len(prefix):].lstrip()
    display_title = html.escape(display_title or title)

    url = html.escape(item["url"])
    item_id = html.escape(item.get("id") or "", quote=True)

    # ── type chip + title ──
    type_chip = ""
    if raw_type in ("GB", "IC"):
        type_chip = (
            f'<span class="gb-type gb-type-{raw_type.lower()}" '
            f'aria-label="{raw_type}">{raw_type}</span>'
        )
    title_block = (
        f'<h3 class="gb-title">{type_chip}'
        f'<a class="item-link gb-title-link" href="{url}" '
        f'rel="noopener" target="_blank">{display_title}</a></h3>'
    )

    # ── vendor / source line ──
    via_label = html.escape(source_label(item))
    vendor_line = f'<p class="gb-vendor">{via_label}</p>'

    # ── IC subtitle: frames the empty chip row as expected, not broken ──
    ic_subtitle = (
        '<p class="gb-ic-subtitle">'
        'Interest check · gauging interest, no vendors yet</p>'
        if is_ic else ""
    )

    # ── image carousel ──
    images = gb_images(item)
    # Map local image index → original remote URL (cleaned of session
    # tokens) so the lightbox can load the full-resolution source on
    # "expand to actual size". May be shorter than `images` if the
    # pilot didn't capture remotes for some slides; missing entries
    # fall through to the local 1280px crop.
    remotes_raw = item.get("images_remote") or []
    remotes = [clean_remote_image_url(u) for u in remotes_raw]
    carousel_html = ""
    if images:
        slides = []
        for idx, img in enumerate(images):
            src = f"{rel_prefix}{img}" if rel_prefix and not img.startswith(("http://", "https://", "/")) else img
            loading = "eager" if idx == 0 else "lazy"
            data_full = ""
            if idx < len(remotes) and remotes[idx]:
                data_full = (
                    f' data-full="{html.escape(remotes[idx], quote=True)}"'
                )
            slides.append(
                f'<div class="gb-slide" role="group" '
                f'aria-label="Image {idx + 1} of {len(images)}" '
                f'aria-roledescription="slide">'
                f'<img src="{html.escape(src)}" alt="" '
                f'loading="{loading}" decoding="async"{data_full}>'
                f'</div>'
            )
        if len(images) == 1:
            # Single image — no carousel chrome, just a static frame.
            carousel_html = (
                f'<div class="gb-carousel gb-carousel-single">'
                f'{slides[0]}'
                f'</div>'
            )
        else:
            dots = []
            for idx in range(len(images)):
                aria_cur = ' aria-current="true"' if idx == 0 else ""
                dots.append(
                    f'<button type="button" class="gb-dot" '
                    f'data-slide="{idx}"{aria_cur} '
                    f'aria-label="Go to image {idx + 1}"></button>'
                )
            carousel_html = (
                f'<div class="gb-carousel" '
                f'role="region" aria-roledescription="carousel" '
                f'aria-label="Images for {html.escape(item.get("title") or "")}" '
                f'tabindex="0" data-gb-carousel>'
                f'<div class="gb-track">{"".join(slides)}</div>'
                f'<button type="button" class="gb-nav gb-nav-prev" '
                f'aria-label="Previous image" data-dir="-1">‹</button>'
                f'<button type="button" class="gb-nav gb-nav-next" '
                f'aria-label="Next image" data-dir="1">›</button>'
                f'<div class="gb-dots" role="tablist" '
                f'aria-label="Image selector">{"".join(dots)}</div>'
                f'</div>'
            )

    # ── status + MOQ + price + end-date chips ──
    gb = item.get("gb") or {}
    chips = []
    status = gb.get("status")
    if status in ("live", "sold-out", "ended", "postponed"):
        chips.append(
            f'<span class="gb-chip gb-status gb-status-{status}">'
            f'{html.escape(status)}</span>'
        )
    if gb.get("moq") is not None:
        chips.append(f'<span class="gb-chip">MOQ {int(gb["moq"])}</span>')
    price = fmt_price_chip(gb)
    if price:
        chips.append(f'<span class="gb-chip">{html.escape(price)}</span>')
    ends = fmt_date_chip(gb.get("ends_at"), prefix="ends")
    if ends:
        chips.append(f'<span class="gb-chip">{html.escape(ends)}</span>')
    starts = fmt_date_chip(gb.get("starts_at"), prefix="starts")
    if starts and not ends:
        chips.append(f'<span class="gb-chip">{html.escape(starts)}</span>')
    chips_row = (
        f'<div class="gb-chips">{"".join(chips)}</div>' if chips else ""
    )

    # ── designer / profile / material line ──
    facet_bits = []
    for k in ("designer", "profile", "material"):
        v = gb.get(k)
        if v:
            facet_bits.append(html.escape(str(v)))
    facets = (
        f'<p class="gb-facets">{" · ".join(facet_bits)}</p>'
        if facet_bits else ""
    )

    # ── vendors by region ──
    vendor_html = ""
    unified = unified_vendor_pills(gb)
    if unified:
        pills = []
        for entry in unified:
            region_raw = entry.get("region") or ""
            region = html.escape(region_raw)
            name = html.escape(entry.get("name") or "")
            link_url = entry.get("url") or ""
            available = entry.get("available")  # True / False / None
            price_low = entry.get("price_low")
            chips_inline = ""
            if available is False:
                chips_inline = (
                    '<span class="gb-vendor-status gb-vendor-status-out">'
                    'sold out</span>'
                )
            elif price_low is not None:
                price = format_vendor_price(
                    price_low,
                    entry.get("price_high"),
                    entry.get("currency"),
                )
                chips_inline = (
                    f'<span class="gb-vendor-price">'
                    f'{html.escape(price)}</span>'
                )
            region_html = (
                f'<span class="gb-vendor-region">{region}</span>'
                if region else ""
            )
            inner = f'{region_html}{name}{chips_inline}'
            pill_classes = "gb-vendor-pill"
            if available is False:
                pill_classes += " gb-vendor-pill-out"
            # data-region powers the client-side geo-filter (see
            # initGbVendorGeo() in font_script). Empty string when
            # we couldn't confidently infer.
            data_region = (
                f' data-region="{html.escape(region_raw, quote=True)}"'
                if region_raw else ' data-region=""'
            )
            if link_url:
                pills.append(
                    f'<a class="{pill_classes} gb-vendor-pill-link" '
                    f'href="{html.escape(link_url)}" '
                    f'rel="noopener" target="_blank"{data_region}>'
                    f'{inner}</a>'
                )
            else:
                pills.append(
                    f'<span class="{pill_classes}"{data_region}>{inner}</span>'
                )
        vendor_html = (
            f'<div class="gb-vendors" data-gb-vendors '
            f'aria-label="Vendors by region">{"".join(pills)}</div>'
        )
    elif is_ic:
        # Empty-state copy on IC cards. Frames the absent vendor list
        # as expected ("designer hasn't decided"), not as data we
        # failed to extract.
        vendor_html = (
            '<p class="gb-no-vendors">'
            'No vendors signed yet — designer is gauging interest.'
            '</p>'
        )

    # ── takeaway ──
    takeaway = html.escape(item.get("takeaway") or "")
    takeaway_html = (
        f'<p class="gb-takeaway">{takeaway}</p>' if takeaway else ""
    )

    # ── engagement + CTA row ──
    engage_bits = []
    if item.get("score") is not None:
        engage_bits.append(
            f'<span class="gb-stat">⬆ {int(item["score"]):,} views</span>'
        )
    if item.get("comments") is not None:
        engage_bits.append(
            f'<span class="gb-stat">💬 {int(item["comments"]):,} replies</span>'
        )
    # Related Geekhack thread (predecessor IC or earlier GB). Picks
    # the most relevant: prefer an IC for a GB card; prefer a GB for
    # an IC card (rare); otherwise pick the first.
    related = ((item.get("gb") or {}).get("related_threads") or [])
    chosen_related = None
    if related:
        priority = "IC" if not is_ic else "GB"
        chosen_related = next(
            (r for r in related if (r.get("type") or "") == priority),
            related[0],
        )
    if chosen_related and chosen_related.get("url"):
        rtype = chosen_related.get("type") or ""
        rlabel = {
            "IC": "Original Interest Check",
            "GB": "Earlier Group Buy",
        }.get(rtype, "Related Geekhack thread")
        engage_bits.append(
            f'<a class="gb-related" '
            f'href="{html.escape(chosen_related["url"])}" '
            f'rel="noopener" target="_blank" '
            f'title="{html.escape(chosen_related.get("title") or rlabel)}">'
            f'🔗 {html.escape(rlabel)}</a>'
        )
    if item.get("source") == "geekhack":
        cta_label = "join the discussion" if is_ic else "open on Geekhack"
    else:
        cta_label = "open"
    engage_bits.append(
        f'<a class="gb-cta" href="{url}" rel="noopener" target="_blank">'
        f'→ {html.escape(cta_label)}</a>'
    )
    engage_row = f'<div class="gb-engage">{"".join(engage_bits)}</div>'

    # ── buylist data-attrs (preserved from the news card contract) ──
    fav_dom = source_domain(item)
    pub_date = date or ""
    item_data = (
        f'data-id="{html.escape(item.get("id") or "", quote=True)}" '
        f'data-title="{html.escape(item.get("title") or "", quote=True)}" '
        f'data-url="{html.escape(item.get("url") or "", quote=True)}" '
        f'data-source="{html.escape(source_label(item), quote=True)}" '
        f'data-favicon="{html.escape(favicon_url(fav_dom), quote=True)}" '
        f'data-date="{html.escape(pub_date, quote=True)}"'
    )
    permalink = (
        f'<a class="item-permalink" href="#{item_id}" '
        f'title="Permalink" aria-label="Permalink">¶</a>'
        if item_id else ""
    )

    item_classes = "item gb-item"
    if is_ic:
        item_classes += " gb-item-ic"

    return f'''<article class="{item_classes}" id="{item_id}" {item_data}>
  {title_block}
  {vendor_line}
  {ic_subtitle}
  {carousel_html}
  {chips_row}
  {facets}
  {vendor_html}
  {takeaway_html}
  {engage_row}
  {permalink}
</article>'''


def render_item(item: dict, topics_reg: dict, tags_reg: dict, *,
                date: str | None = None, page: str = "day",
                rel_prefix: str = "") -> str:
    """Render one item.

    page = 'day' | 'topic' | 'tag'  — affects which date/meta is shown.
    rel_prefix = path prefix from current page to docs root.
    """
    # GB/IC items get the visual-first card with image carousel.
    if is_gb(item):
        return render_gb_item(item, topics_reg, tags_reg,
                              date=date, page=page, rel_prefix=rel_prefix)

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

    # Top meta line: [DATE ·] [favicon] SOURCE · TOPIC1 · TOPIC2
    top_parts = []
    if page in ("topic", "tag") and date:
        top_parts.append(f'<span class="date-prefix">{html.escape(fmt_date_short(date))}</span>')
    fav_dom = source_domain(item)
    fav_html = (
        f'<img class="favicon" src="{html.escape(favicon_url(fav_dom))}" '
        f'alt="" width="14" height="14" loading="lazy">'
        if fav_dom else ""
    )
    top_parts.append(f'<span class="source">{fav_html}{html.escape(source_label(item))}</span>')

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
    # Suppressed entirely on multi-source items: per-source stats already
    # appear in the "Also discussed at" list above, and aggregating them
    # on an email-led cluster would surface phantom stats.
    sources_count = len(item.get("sources") or [])
    bottom_parts = []
    if sources_count <= 1:
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

    # Multi-source discussion list (Techmeme-style). Each secondary source
    # gets its own line because score + comments are per-source.
    sources = item.get("sources") or []
    discussion_html = ""
    is_multi = len(sources) > 1
    if is_multi:
        secondary = sources[1:]
        rows = []
        for s in secondary:
            label = html.escape(s.get("label") or "source")
            href = html.escape(s.get("discussion_url") or "")
            stats = []
            if s.get("score") is not None:
                stats.append(f'<span class="src-stat">⬆ {s["score"]}</span>')
            if s.get("comments") is not None:
                stats.append(f'<span class="src-stat">💬 {s["comments"]}</span>')
            stats_html = (' ' + ' '.join(stats)) if stats else ''
            link = (
                f'<a href="{href}" rel="noopener" target="_blank">{label}</a>'
                if href else f'<span>{label}</span>'
            )
            rows.append(f'<li>{link}{stats_html}</li>')
        discussion_html = (
            '<div class="item-discussion">'
            '<p class="item-discussion-label">Also discussed at</p>'
            '<ul>' + ''.join(rows) + '</ul>'
            '</div>'
        )

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
    if is_multi:
        item_classes += " is-multi"
        if len(sources) >= 3:
            item_classes += " is-lead"

    # Buylist data is stored as data-attrs on the item itself; press-and-hold
    # on the card reveals the Add/Remove action via the shared popover.
    pub_date = date or ""
    item_data = (
        f'data-id="{html.escape(item.get("id") or "", quote=True)}" '
        f'data-title="{html.escape(item.get("title") or "", quote=True)}" '
        f'data-url="{html.escape(item.get("url") or "", quote=True)}" '
        f'data-source="{html.escape(source_label(item), quote=True)}" '
        f'data-favicon="{html.escape(favicon_url(fav_dom), quote=True)}" '
        f'data-date="{html.escape(pub_date, quote=True)}"'
    )

    item_id = html.escape(item.get("id") or "", quote=True)
    permalink = f'<a class="item-permalink" href="#{item_id}" title="Permalink" aria-label="Permalink">¶</a>' if item_id else ""

    return f'''<div class="{item_classes}" id="{item_id}" {item_data}>
  <div class="item-body">
    <div class="item-topmeta">{top_meta}</div>
    <h3 class="item-title"{rewritten_attr}><a class="item-link" href="{url}" rel="noopener" target="_blank">{display_title}</a>{permalink}</h3>
    {takeaway_html}
    {discussion_html}
    <div class="item-meta">{bottom_meta}</div>
  </div>
  {thumb_html}
</div>'''


def render_section(label: str, items: list[dict], topics_reg: dict, tags_reg: dict,
                   day_date: str | None = None, *, breaking: bool = False) -> str:
    if not items:
        return ""
    cls = "section-label breaking" if breaking else "section-label"
    items_html = "\n".join(
        render_item(i, topics_reg, tags_reg, page="day", date=day_date) for i in items
    )
    return f'''<section class="section">
  <h2 class="{cls}">{html.escape(label)}</h2>
  {items_html}
</section>'''


def render_day_block(day: dict, topics_reg: dict, tags_reg: dict) -> str:
    date = day["date"]
    y, m, d = date.split("-")
    day_num = int(d)
    month_year = f"{MONTHS[int(m)]} {y}"

    items = cluster_items(day.get("items", []))
    breaking = sorted(
        [i for i in items if i.get("category") == "breaking"],
        key=lambda i: -(i.get("score") or 0),
    )
    evergreen = sorted(
        [i for i in items if i.get("category") == "evergreen"],
        key=lambda i: -(i.get("score") or 0),
    )

    body = ""
    body += render_section("Breaking", breaking, topics_reg, tags_reg, day_date=date, breaking=True)
    body += "\n" + render_section("Evergreen", evergreen, topics_reg, tags_reg, day_date=date)
    if not body.strip():
        body = '<p class="empty">Quiet day — nothing notable.</p>'

    play_btn = f'''<button class="play-day" type="button" aria-label="Play this day's stories"
      title="Play this day's stories">
      <svg class="icon-play" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <path d="M4 3l9 5-9 5z" fill="currentColor"/>
      </svg>
      <svg class="icon-pause" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <path d="M4 3h3v10H4zm5 0h3v10H9z" fill="currentColor"/>
      </svg>
    </button>'''

    return f'''<section class="day" id="{date}">
  <header class="day-header">
    <span class="day-number">{day_num:02d}</span>
    <span class="day-month-year">{month_year}</span>
    {play_btn}
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
    title = f"{label} · mechanical keyboard newswire"
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


def render_buylist_page() -> str:
    canonical = f"{SITE_URL}/buylist/"
    title = "Want to buy · mechanical keyboard newswire"
    desc = "Saved keyboard wire stories you want to buy or follow up on."

    return f'''{head(title, desc, canonical)}
  {site_header(canonical)}
  <section class="buylist-page">
    <header class="archive-header">
      <h2 class="archive-title-page">Want to buy</h2>
      <p class="archive-stats" id="buylist-stats">0 saved</p>
    </header>

    <p id="buylist-empty" class="empty">
      Tap the ♡ on any story to save it here. Items live in your browser only —
      they aren’t synced or shared.
    </p>

    <ol id="buylist" class="buylist" hidden>
    </ol>

    <p class="buylist-help" hidden id="buylist-help">
      Drag rows to reorder. Press and hold a row to remove or move it to the top.
    </p>
  </section>
  <footer><a href="../">home</a></footer>
</main>
{font_script()}
{buylist_script()}
</body>
</html>'''


def buylist_script() -> str:
    return '''<script>
(function() {
  var BL_KEY = 'kw-buylist';
  function read() { try { return JSON.parse(localStorage.getItem(BL_KEY)) || []; } catch (e) { return []; } }
  function write(list) { try { localStorage.setItem(BL_KEY, JSON.stringify(list)); } catch (e) {} }

  var root = document.getElementById('buylist');
  var stats = document.getElementById('buylist-stats');
  var empty = document.getElementById('buylist-empty');
  var help = document.getElementById('buylist-help');

  function escapeHtml(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : s;
    return d.innerHTML;
  }

  function render() {
    var list = read();
    stats.textContent = list.length + (list.length === 1 ? ' saved' : ' saved');
    empty.hidden = list.length > 0;
    root.hidden = list.length === 0;
    if (help) help.hidden = list.length === 0;
    root.innerHTML = list.map(function(it) {
      var fav = it.favicon ? '<img class="favicon" src="' + escapeHtml(it.favicon) + '" alt="" width="14" height="14" loading="lazy">' : '';
      return '<li class="buylist-item" draggable="true" data-id="' + escapeHtml(it.id) + '">' +
        '<div class="bl-body">' +
          '<div class="bl-meta">' + fav + '<span>' + escapeHtml(it.source) + '</span></div>' +
          '<h3 class="bl-title"><a href="' + escapeHtml(it.url) + '" rel="noopener" target="_blank">' + escapeHtml(it.title) + '</a></h3>' +
          '<p class="bl-dates">' +
            (it.date ? '<span>published <time>' + escapeHtml(it.date) + '</time></span><span class="sep">·</span>' : '') +
            '<span>added <time>' + escapeHtml(it.addedAt || '—') + '</time></span>' +
          '</p>' +
        '</div>' +
      '</li>';
    }).join('');
    wireDrag();
  }

  // Expose so the shared popover handler can re-render after move/remove
  window.kwBuylistRender = render;

  function wireDrag() {
    var dragId = null;
    root.querySelectorAll('li').forEach(function(li) {
      li.addEventListener('dragstart', function(e) {
        dragId = li.dataset.id;
        li.classList.add('dragging');
        if (e.dataTransfer) { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragId); }
      });
      li.addEventListener('dragend', function() { li.classList.remove('dragging'); dragId = null; });
      li.addEventListener('dragover', function(e) {
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
        li.classList.add('drag-over');
      });
      li.addEventListener('dragleave', function() { li.classList.remove('drag-over'); });
      li.addEventListener('drop', function(e) {
        e.preventDefault();
        li.classList.remove('drag-over');
        var targetId = li.dataset.id;
        if (!dragId || dragId === targetId) return;
        var list = read();
        var fromIdx = list.findIndex(function(i){ return i.id === dragId; });
        var toIdx = list.findIndex(function(i){ return i.id === targetId; });
        if (fromIdx < 0 || toIdx < 0) return;
        var moved = list.splice(fromIdx, 1)[0];
        list.splice(toIdx, 0, moved);
        write(list);
        render();
      });
    });
  }

  render();

  // Long-press on a buylist row → row actions (move / remove). The shared
  // popover code lives in the main script; we just expose a hook so the
  // press is consistent across pages.
  function attachRowLongPress() {
    var timer = null, anchor = null, sx = 0, sy = 0;
    function down(e) {
      var li = e.target.closest('.buylist-item');
      if (!li) return;
      // ignore clicks on the title link
      if (e.target.closest('.bl-title a')) return;
      anchor = li; sx = e.clientX; sy = e.clientY;
      clearTimeout(timer);
      timer = setTimeout(function() {
        if (!anchor) return;
        try { navigator.vibrate && navigator.vibrate(15); } catch (err) {}
        if (window.kwShowBuylistPopover) window.kwShowBuylistPopover(anchor, sx, sy);
        anchor = null;
      }, 500);
    }
    function move(e) {
      if (!anchor) return;
      if (Math.abs(e.clientX - sx) > 8 || Math.abs(e.clientY - sy) > 8) {
        clearTimeout(timer); anchor = null;
      }
    }
    function cancel() { clearTimeout(timer); anchor = null; }
    document.addEventListener('pointerdown', down);
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', cancel);
    document.addEventListener('pointercancel', cancel);
  }
  attachRowLongPress();

  // Cross-tab sync
  window.addEventListener('storage', function(e) {
    if (e.key === BL_KEY) render();
  });
})();
</script>'''


def render_archive_page(corpus: dict, topics_reg: dict, tags_reg: dict) -> str:
    canonical = f"{SITE_URL}/archive/"
    title = "Archive · mechanical keyboard newswire"
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
    title = "Settings · mechanical keyboard newswire"
    desc = "Display options and source pipelines for mechanical keyboard newswire."

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

    <h2 class="settings-section-label">Region</h2>
    <div class="setting-row">
      <label class="setting-select">
        <span class="setting-select-label">
          <strong>Show local vendors first</strong>
          <small>When viewing the group-buys page, vendors in your region appear
          expanded by default; others collapse behind a "+ N more worldwide" toggle.
          Auto-detected from your browser timezone — set explicitly here to override.</small>
        </span>
        <select id="region-select" aria-label="Your region">
          <option value="">Auto (use timezone)</option>
          <option value="US">US — United States</option>
          <option value="CA">CA — Canada</option>
          <option value="UK">UK — United Kingdom</option>
          <option value="EU">EU — Europe</option>
          <option value="JP">JP — Japan</option>
          <option value="KR">KR — Korea</option>
          <option value="CN">CN — China</option>
          <option value="SG">SG — Singapore / SEA</option>
          <option value="AU">AU — Australia / Oceania</option>
          <option value="NZ">NZ — New Zealand</option>
          <option value="__all__">All regions (no collapse)</option>
        </select>
      </label>
    </div>

    <h2 class="settings-section-label">Lists</h2>
    <div class="setting-row">
      <a class="settings-list-link" href="../buylist/" id="nav-buylist">
        <span class="settings-list-icon" aria-hidden="true">♥</span>
        <span class="settings-list-text">
          <strong>Want to buy<span class="buylist-count" hidden></span></strong>
          <small>Saved items you press-and-hold to add. Stored locally in your browser.</small>
        </span>
      </a>
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
    title = f"{label} · mechanical keyboard newswire"
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


def filter_corpus(corpus: dict, predicate) -> dict:
    """Return a corpus-shaped dict whose items pass `predicate`.
    Day order preserved; empty days are kept (the index renders an
    empty-day message which is fine and accurate)."""
    assert callable(predicate), "predicate must be callable"
    assert isinstance(corpus, dict) and "days" in corpus, \
        "corpus must be a dict with a 'days' key"
    return {
        "title": corpus.get("title", ""),
        "tagline": corpus.get("tagline", ""),
        "days": [
            {"date": d["date"],
             "items": [it for it in d.get("items", []) if predicate(it)]}
            for d in corpus.get("days", [])
        ],
    }


def render_gb_day_block(day: dict, topics_reg: dict, tags_reg: dict,
                        *, rel_prefix: str = "") -> str:
    """Day block specialized for /groupbuys/. Like render_day_block
    but no Breaking/Evergreen sections (irrelevant to GB items —
    they're not news), items sorted by views descending so the most
    talked-about appear first within each day.

    `rel_prefix` must point from the rendered page back to docs/ root
    (e.g. `"../"` when this is called for `/groupbuys/index.html`) so
    that image src attributes resolve correctly.
    """
    items = sorted(day.get("items", []),
                   key=lambda i: -(i.get("score") or 0))
    body = "\n".join(
        render_item(i, topics_reg, tags_reg, page="day",
                    date=day["date"], rel_prefix=rel_prefix)
        for i in items
    )
    if not body.strip():
        return ""
    date = day["date"]
    y, m, d = date.split("-")
    return f'''<section class="day gb-day" id="{date}">
  <header class="day-header">
    <span class="day-number">{int(d):02d}</span>
    <span class="day-month-year">{MONTHS[int(m)]} {y}</span>
  </header>
  {body}
</section>'''


def render_groupbuys_page(corpus: dict, topics_reg: dict, tags_reg: dict) -> str:
    """Render /groupbuys/index.html — dedicated page for GB/IC sources,
    split into two sections (active group buys, then interest checks)
    so the two distinct stages don't visually compete. See
    docs/IC_DIFFERENTIATION.md."""
    title = "Group buys & ICs · keyboard newswire"
    tagline = "Live group buys and interest checks from Geekhack and partner vendors."
    canonical = f"{SITE_URL}/groupbuys/"

    def _effective_type(it):
        """Mirror render_gb_item's IC→GB auto-graduate rule: an IC
        with vendor_links lands in the GB section."""
        t = (it.get("type") or "").upper()
        if t == "IC" and (it.get("gb") or {}).get("vendor_links"):
            return "GB"
        return t

    gb_only = filter_corpus(corpus, lambda it: _effective_type(it) == "GB")
    ic_only = filter_corpus(corpus, lambda it: _effective_type(it) == "IC")
    # Untyped items (rare — older Shopify items will land here) fall
    # back to the GB section since they're closer in cadence.
    untyped = filter_corpus(corpus, lambda it: _effective_type(it) not in ("GB", "IC"))
    # Merge untyped into gb_only.
    for du, dg in zip(untyped["days"], gb_only["days"]):
        dg["items"].extend(du["items"])

    def _render_section(c, slug, label, blurb):
        days = sorted(
            [d for d in c["days"] if d.get("items")],
            key=lambda d: d["date"], reverse=True,
        )
        if not days:
            return ""
        # /groupbuys/index.html lives one level below docs/, so all
        # `img/<slug>.jpg` references in cards need a "../" prefix.
        blocks = "\n".join(
            render_gb_day_block(d, topics_reg, tags_reg, rel_prefix="../")
            for d in days
        )
        return f'''<section class="gb-section gb-section-{slug}">
  <header class="gb-section-header">
    <h2 class="gb-section-title">{html.escape(label)}</h2>
    <p class="gb-section-blurb">{html.escape(blurb)}</p>
  </header>
  {blocks}
</section>'''

    gb_html = _render_section(
        gb_only, "live",
        "Active group buys",
        "GBs currently live or recently closed. Locked-in vendors, MOQ, prices.",
    )
    ic_html = _render_section(
        ic_only, "interest",
        "Interest checks",
        "Designer-proposed projects gauging interest before launch. "
        "Not yet for sale.",
    )
    body = gb_html + ic_html
    if not body:
        body = (
            '<p class="empty">No group buys tracked yet — '
            'check back as the ingest catches new threads.</p>'
        )

    return f'''{head(title, tagline, canonical, feed="feed.xml")}
  {site_header(canonical)}
  <p class="gb-page-disclaimer">
    Group buys and interest checks are kept off the main news feed
    while the source pipeline is being debugged. Expect rough edges.
  </p>
  {body}
  {site_footer()}
</main>
<div id="gb-lightbox" class="gb-lightbox" hidden role="dialog"
     aria-label="Image viewer" aria-modal="true">
  <button type="button" class="gb-lightbox-close" aria-label="Close">×</button>
  <button type="button" class="gb-lightbox-nav gb-lightbox-prev" aria-label="Previous image">‹</button>
  <img class="gb-lightbox-img" alt="" />
  <button type="button" class="gb-lightbox-nav gb-lightbox-next" aria-label="Next image">›</button>
  <div class="gb-lightbox-counter" aria-live="polite"></div>
</div>
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

    # Quarantine GB/IC sources from news surfaces. See docs/GB_IC_FEED.md.
    news_corpus = filter_corpus(corpus, lambda it: not is_gb(it))
    gb_corpus = filter_corpus(corpus, is_gb)

    DOCS.mkdir(parents=True, exist_ok=True)
    # Wipe and rebuild topic/tag dirs to drop stale slugs cleanly
    for sub in ("topics", "tags"):
        d = DOCS / sub
        if d.exists():
            shutil.rmtree(d)

    # --- index + main feed (news only) ---
    (DOCS / "index.html").write_text(render_index(news_corpus, topics_reg, tags_reg))

    flat = []
    for day in news_corpus["days"]:
        for it in day.get("items", []):
            flat.append((day["date"], it))
    flat.sort(key=lambda r: (r[0], r[1].get("score") or 0), reverse=True)
    (DOCS / "feed.xml").write_text(render_rss(
        corpus["title"], corpus["tagline"],
        f"{SITE_URL}/", f"{SITE_URL}/feed.xml", flat, topics_reg
    ))

    # --- group buys page (GB items only) ---
    (DOCS / "groupbuys").mkdir(parents=True, exist_ok=True)
    (DOCS / "groupbuys" / "index.html").write_text(
        render_groupbuys_page(gb_corpus, topics_reg, tags_reg)
    )
    gb_flat = []
    for day in gb_corpus["days"]:
        for it in day.get("items", []):
            gb_flat.append((day["date"], it))
    gb_flat.sort(key=lambda r: r[0], reverse=True)
    (DOCS / "groupbuys" / "feed.xml").write_text(render_rss(
        "Group buys & ICs · keyboard newswire",
        "Live group buys and interest checks from Geekhack and partner vendors.",
        f"{SITE_URL}/groupbuys/", f"{SITE_URL}/groupbuys/feed.xml",
        gb_flat, topics_reg,
    ))

    # --- topic pages ---
    # GB items appear on the group-buys-vendors topic page (so it
    # mirrors /groupbuys/), but are excluded from every other topic.
    by_topic: dict[str, list[tuple]] = {slug: [] for slug in topics_reg}
    for day in corpus["days"]:
        for it in day.get("items", []):
            allow_gb = is_gb(it)
            for ts in it.get("topics") or []:
                if ts not in by_topic:
                    continue
                if allow_gb and ts != GB_TOPIC_SLUG:
                    continue
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

    # --- tag pages (news only — GB items never appear on tag pages) ---
    by_tag: dict[str, list[tuple]] = {}
    seen_tag_slugs = set()
    for day in corpus["days"]:
        for it in day.get("items", []):
            if is_gb(it):
                continue
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

    # --- archive page (news only) ---
    (DOCS / "archive").mkdir(parents=True, exist_ok=True)
    (DOCS / "archive" / "index.html").write_text(
        render_archive_page(news_corpus, topics_reg, tags_reg)
    )

    # --- buylist page ---
    (DOCS / "buylist").mkdir(parents=True, exist_ok=True)
    (DOCS / "buylist" / "index.html").write_text(render_buylist_page())

    n_days = len(corpus["days"])
    n_items = sum(len(d.get("items", [])) for d in corpus["days"])
    n_topics_used = sum(1 for v in by_topic.values() if v)
    n_tags = len(by_tag)
    print(f"generated: {n_days} days, {n_items} items, "
          f"{n_topics_used}/{len(topics_reg)} topics, {n_tags} tags")


if __name__ == "__main__":
    main()

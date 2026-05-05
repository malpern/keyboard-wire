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
    return f"r/{sub}" if sub else "Reddit"


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
    source_path = "https://github.com/malpern/keyboard-wire"
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
      <a href="{archive_path}">archive</a>
      <span aria-hidden="true">·</span>
      <a href="{feed_path}">RSS</a>
      <span aria-hidden="true">·</span>
      <a href="{settings_path}">settings</a>
      <span aria-hidden="true">·</span>
      <a href="{relative_to_docs(canonical, 'post/')}">about</a>
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
    <a href="https://github.com/malpern/keyboard-wire">source</a>
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

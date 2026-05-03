#!/usr/bin/env python3
"""Generate a sanitized landing page per email item and rewrite URLs for items
whose original URL resolves to a homepage.

Steps for each item with source='email':
  1. Pull the email body via gog (HTML preferred, plain-text fallback).
  2. Sanitize via sanitize_email.sanitize_html (regex layer only for now).
  3. If sanitize_email.should_skip, leave the item unchanged and report.
  4. Write docs/email/<thread_id>/index.html.
  5. Resolve the item's existing URL (HEAD with redirects). If the final URL
     resolves to a bare homepage (no useful path), point item.url at the
     local landing page.

Usage:
  email_archive.py [--dry-run] [--report]
  Without --dry-run, writes pages and updates corpus.json.
"""
import argparse
import datetime
import html
import json
import os
import pathlib
import re
import subprocess
import sys
from urllib.parse import urlparse, urlunparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from sanitize_email import sanitize_html, should_skip  # noqa: E402
from email_pipeline import _walk_parts, html_to_text  # noqa: E402

CORPUS = ROOT / "data" / "corpus.json"
DOCS = ROOT / "docs"
EMAIL_DIR = DOCS / "email"
SITE_URL = "https://malpern.github.io/keyboard-wire"

GOG = os.environ.get("GOG_BIN", "/opt/homebrew/bin/gog")
ACCOUNT = os.environ.get("KW_GMAIL_ACCOUNT", "malpern@gmail.com")
KEYRING = os.environ.get("GOG_KEYRING_PASSWORD", "clawd-gog-2026")


# ─── Data fetch ─────────────────────────────────────────────────────


def gog_thread(thread_id: str) -> dict | None:
    env = dict(os.environ)
    env["GOG_KEYRING_PASSWORD"] = KEYRING
    try:
        r = subprocess.run(
            [GOG, "gmail", "thread", "get", "-a", ACCOUNT, "--full", "-j", thread_id],
            env=env, capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except Exception:
        return None


def fetch_email(thread_id: str) -> dict | None:
    """Returns {subject, sender, date, plain, html, base_url} or None."""
    data = gog_thread(thread_id)
    if not data:
        return None
    msgs = (data.get("thread") or data).get("messages") or []
    if not msgs:
        return None
    m = msgs[0]
    payload = m.get("payload") or {}
    headers = {h.get("name", ""): h.get("value", "") for h in (payload.get("headers") or [])}
    subject = headers.get("Subject", "")
    sender = headers.get("From", "")
    date_str = headers.get("Date", "")
    plain, html_ = _walk_parts(payload)
    if not plain and html_:
        plain = html_to_text(html_)
    if not plain:
        plain = m.get("snippet", "")
    # Best-effort base URL: first URL in body
    base_url = None
    if html_ or plain:
        m_url = re.search(r"https?://[^\s<>\"']+", (html_ or plain))
        if m_url:
            try:
                p = urlparse(m_url.group(0))
                base_url = f"{p.scheme}://{p.netloc}/"
            except Exception:
                pass
    return {
        "subject": subject,
        "sender": sender,
        "date": date_str,
        "plain": plain,
        "html": html_ or "",
        "base_url": base_url,
    }


# ─── URL resolution ─────────────────────────────────────────────────

# Known tracking-only query parameter names + prefixes — stripped from
# resolved URLs so we don't leak recipient identifiers in archived links.
TRACKING_PARAM_PREFIXES = (
    "utm_", "se_", "mc_", "sg_", "ck_", "klaviyo_", "kt_",
)
TRACKING_PARAM_NAMES = {
    "fbclid", "gclid", "msclkid", "yclid", "syclid", "spm", "ttclid",
    "gbraid", "wbraid", "irgwc", "irclickid",
    "e", "eid", "ref", "referrer", "src", "source",
    "subscriber_id", "recipient_id", "email", "_kx",
    "vero_id", "vero_conv", "hsa_acc", "hsa_cam", "hsa_grp",
    "ml_subscriber", "ml_subscriber_hash",
    "ck_subscriber_id",
}


def _is_tracking_param(name: str) -> bool:
    n = name.lower()
    if n in TRACKING_PARAM_NAMES:
        return True
    return any(n.startswith(p) for p in TRACKING_PARAM_PREFIXES)


def clean_query(url: str) -> str:
    """Strip tracking params from a URL's query string."""
    if not url:
        return url
    try:
        p = urlparse(url)
    except Exception:
        return url
    if not p.query:
        return url
    kept = []
    for kv in p.query.split("&"):
        if not kv:
            continue
        k = kv.split("=", 1)[0]
        if not _is_tracking_param(k):
            kept.append(kv)
    new_q = "&".join(kept)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, ""))


UTM_PARAM_RE = re.compile(r"^(utm_|fb|gclid|mc_|sc|sg|trk|sse|src|spm|s|se_|sy|gbraid|wbraid)", re.IGNORECASE)


def resolve_url(url: str, timeout: int = 15) -> str:
    """Follow redirects, return final URL (or original on failure).

    Uses GET (with --range to limit body) rather than HEAD because some
    tracking endpoints (Shopify Email, Klaviyo) only redirect on GET.
    """
    if not url or url.startswith(("mailto:", "tel:")):
        return url
    try:
        r = subprocess.run(
            ["curl", "-sL", "-A", "Mozilla/5.0",
             "--max-time", str(timeout),
             "-o", "/dev/null", "-w", "%{url_effective}",
             url],
            capture_output=True, text=True, timeout=timeout + 4,
        )
        # Even on non-zero exit (network errors, max-time), curl still prints
        # the most recent effective URL. Trust that if we got something useful.
        final = (r.stdout.strip() or url)
        if final.startswith(("http://", "https://")):
            return final
        return url
    except Exception:
        return url


def is_homepage(url: str) -> bool:
    """Heuristic: does this URL resolve to a bare site root?"""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if not p.netloc:
        return False
    path = (p.path or "").rstrip("/")
    if path:
        return False
    # Path is empty or "/" — only acceptable extras are tracking params
    if not p.query:
        return True
    keep_params = []
    for kv in p.query.split("&"):
        k = kv.split("=", 1)[0]
        if not UTM_PARAM_RE.match(k):
            keep_params.append(k)
    return not keep_params


# ─── Landing page render ────────────────────────────────────────────


def parse_pubdate(date_str: str) -> str:
    """RFC2822 → 'YYYY-MM-DD'."""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        if dt:
            return dt.date().isoformat()
    except Exception:
        pass
    return ""


def render_landing(item: dict, fetched: dict, sanitized_html: str, report: dict) -> str:
    title = item.get("title") or fetched["subject"]
    sender = fetched["sender"]
    pub_date = parse_pubdate(fetched["date"]) or item.get("date") or ""
    thread_id = item["id"][len("email-"):]
    gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{thread_id}"

    # Strip "Name <email>" → "Name (domain)" so we don't show user's email
    sender_display = sender
    m = re.search(r"^([^<]+?)\s*<[^>]+@([^>]+)>", sender)
    if m:
        sender_display = f"{m.group(1).strip()} ({m.group(2).strip()})"

    redaction_summary = ""
    redactions = report.get("redactions") or {}
    if redactions or report.get("tracking_pixels") or report.get("unsub_links") or report.get("view_online_links"):
        bits = []
        if report.get("tracking_pixels"):
            bits.append(f"{report['tracking_pixels']} tracking pixel(s)")
        if report.get("view_online_links"):
            bits.append(f"{report['view_online_links']} view-online link(s)")
        if report.get("unsub_links"):
            bits.append(f"{report['unsub_links']} unsubscribe link(s)")
        if report.get("tracking_redirects"):
            bits.append(f"{report['tracking_redirects']} tracking redirect(s) flattened")
        for k, n in redactions.items():
            bits.append(f"{n} {k} redaction(s)")
        if bits:
            redaction_summary = "Sanitized: " + ", ".join(bits) + "."

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(title)} · malpern's keyboard wire</title>
<link rel="stylesheet" href="../../style.css?v={int(datetime.datetime.now().timestamp())}">
<link rel="canonical" href="{SITE_URL}/email/{thread_id}/">
</head>
<body>
<main>
<header>
  <h1 class="site-title"><a href="../../">malpern's keyboard wire</a></h1>
  <p class="tagline">archived email — read-only</p>
  <p class="subscribe">
    <a href="../../">home</a>
    <span aria-hidden="true">·</span>
    <a href="../../archive/">archive</a>
    <span aria-hidden="true">·</span>
    <a href="../../buylist/" id="nav-buylist">want to buy<span class="buylist-count" hidden></span></a>
  </p>
</header>

<article class="email-page">
  <header class="email-header">
    <div class="email-meta">
      <span class="email-from">{html.escape(sender_display)}</span>
      {f'<span class="sep">·</span><time>{html.escape(pub_date)}</time>' if pub_date else ''}
    </div>
    <h2 class="email-subject">{html.escape(title)}</h2>
    {f'<p class="email-rewrite-note">Originally posted as: {html.escape(item["original_title"])}</p>' if item.get("title_rewritten") else ''}
  </header>

  <div class="email-body">
    {sanitized_html}
  </div>

  <footer class="email-footer">
    {f'<p class="email-redaction-note">{html.escape(redaction_summary)}</p>' if redaction_summary else ''}
    <p class="email-original-link">
      <a href="{html.escape(gmail_link)}" rel="noopener" target="_blank">View original in Gmail</a>
      (works only for the inbox owner)
    </p>
  </footer>
</article>
</main>
</body>
</html>
'''


# ─── Main ───────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="don't write files; print diff/report")
    p.add_argument("--report", action="store_true", help="emit a per-item summary")
    p.add_argument("--id", help="process only one item id (e.g. email-19de...)")
    args = p.parse_args()

    corpus = json.loads(CORPUS.read_text())
    items_to_process = []
    for day in corpus["days"]:
        for it in day.get("items", []):
            if it.get("source") != "email":
                continue
            if args.id and it.get("id") != args.id:
                continue
            items_to_process.append(it)

    if not items_to_process:
        print("no email items to process")
        return

    summary = []
    EMAIL_DIR.mkdir(parents=True, exist_ok=True)

    for item in items_to_process:
        tid = item["id"][len("email-"):]
        sys.stderr.write(f"  {item['id']}  {item.get('title','')[:55]}\n")
        fetched = fetch_email(tid)
        if not fetched:
            summary.append({"id": item["id"], "status": "no email body", "title": item.get("title")})
            continue

        skip, reason = should_skip(fetched["subject"], fetched["sender"], fetched["plain"])
        if skip:
            summary.append({"id": item["id"], "status": f"SKIPPED: {reason}", "title": item.get("title")})
            continue

        # Prefer sanitizing HTML; fall back to wrapping plain text in <p>.
        if fetched["html"]:
            clean_html, report = sanitize_html(fetched["html"], base_url=fetched["base_url"])
        else:
            clean_html, report = sanitize_html(
                "".join(f"<p>{p}</p>" for p in re.split(r"\n{2,}", fetched["plain"])),
                base_url=fetched["base_url"],
            )

        # If sanitization left us with essentially nothing, skip
        text_only = re.sub(r"<[^>]+>", "", clean_html).strip()
        if len(text_only) < 80:
            summary.append({"id": item["id"], "status": "SKIPPED: sanitized body too short", "title": item.get("title")})
            continue

        landing_path = EMAIL_DIR / tid / "index.html"
        landing_url_rel = f"email/{tid}/"
        landing_url_abs = f"{SITE_URL}/{landing_url_rel}"

        # Decide whether to point item.url at landing page or the cleaned
        # resolved URL. Always strip tracking params so archived URLs don't
        # carry recipient identifiers (mailchimp e=, klaviyo _kx, syclid…).
        original_url = item.get("url", "")
        old_url = original_url
        new_url = original_url
        if original_url and not original_url.startswith(SITE_URL):
            resolved = resolve_url(original_url)
            cleaned = clean_query(resolved)
            if is_homepage(cleaned):
                new_url = landing_url_abs
            else:
                new_url = cleaned

        if not args.dry_run:
            landing_path.parent.mkdir(parents=True, exist_ok=True)
            landing_path.write_text(render_landing(item, fetched, clean_html, report))
            if new_url != old_url:
                item["url"] = new_url
                item["discussion_url"] = new_url

        summary.append({
            "id": item["id"],
            "status": "ok" + (" + url-rewritten" if new_url != old_url else ""),
            "title": item.get("title"),
            "old_url": old_url,
            "new_url": new_url,
            "report": report,
        })

    if not args.dry_run:
        CORPUS.write_text(json.dumps(corpus, indent=2, ensure_ascii=False) + "\n")

    # Print summary
    print(f"\n=== email_archive: {len(summary)} items ===\n")
    for s in summary:
        line = f"[{s['status']}] {s['id']}"
        if s.get("title"):
            line += "  " + s["title"][:65]
        print(line)
        if args.report:
            if s.get("old_url") and s.get("old_url") != s.get("new_url"):
                print(f"     url: {s['old_url'][:80]}")
                print(f"      →   {s['new_url']}")
            r = s.get("report") or {}
            if r:
                bits = []
                if r.get("tracking_pixels"): bits.append(f"pixels:{r['tracking_pixels']}")
                if r.get("tracking_redirects"): bits.append(f"redirects:{r['tracking_redirects']}")
                if r.get("view_online_links"): bits.append(f"view-online:{r['view_online_links']}")
                if r.get("unsub_links"): bits.append(f"unsub:{r['unsub_links']}")
                if r.get("redactions"):
                    for k, n in r["redactions"].items():
                        bits.append(f"{k}:{n}")
                if bits:
                    print("     " + ", ".join(bits))


if __name__ == "__main__":
    main()

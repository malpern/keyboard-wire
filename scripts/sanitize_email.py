#!/usr/bin/env python3
"""Sanitize an email HTML body for republishing on the public keyboard-wire archive.

Two layers of safety:

  Layer 1 (this module): deterministic regex + DOM rules. Strips scripts,
  styles, tracking pixels, unsubscribe blocks, view-in-browser preheaders,
  personalized greetings, and common PII patterns (card numbers, phones,
  email addresses, SSN-shaped numbers, order numbers).

  Layer 2 (caller, optional): pass the result through local Qwen for a
  contextual review pass that catches things regex can't (e.g. "your order
  ships Tuesday to your home in Mountain View"). Not implemented here yet.

Public API:
  sanitize_html(html, base_url=None) -> (clean_html, report)
  should_skip(subject, sender, body_text) -> (bool, reason)
"""
import html as html_mod
import re
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin

# ─── Configuration ──────────────────────────────────────────────────

USER_FIRST_NAMES = ("Micah", "Malpern", "malpern")
USER_EMAIL_LOCAL_PARTS = ("malpern",)  # username@... matchers

# Tags we keep (everything else is dropped, content kept).
ALLOWED_TAGS = {
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "b", "i", "u", "s",
    "a", "img", "figure", "figcaption",
    "ul", "ol", "li",
    "blockquote", "q", "cite",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th",
    "pre", "code", "kbd",
    "div", "span", "section", "article", "header", "footer", "main",
}

# Attributes to keep per tag (whitelist).
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
}
# Attributes accepted on any tag
GLOBAL_ATTRS = set()  # intentionally empty — strip class/style/id/data-* across the board

# Tags whose content should also be dropped, not just the tags
BLOCK_TAGS = {"script", "style", "iframe", "noscript", "head", "title", "meta", "link", "object", "embed"}

# Redaction sentinel
REDACT = "[redacted]"

# ─── Regex patterns ─────────────────────────────────────────────────

# Card numbers (13–19 digits with optional spaces/dashes)
CARD_NUM_RE = re.compile(r"\b(?:\d[ -]?){13,19}\d?\b")
# SSN-shaped
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# US phone numbers
PHONE_RE = re.compile(
    r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
)
# Email addresses
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# US-style street addresses (heuristic)
ADDR_RE = re.compile(
    r"\b\d{1,5}\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3}\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|Court|Ct|Place|Pl)\b\.?",
)
# ZIP codes — ONLY redact when adjacent to a state abbreviation (heuristic)
ZIP_NEAR_STATE_RE = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")
# Order / invoice / tracking numbers
ORDER_RE = re.compile(
    r"\b(?:order|invoice|tracking|account|customer|case|ticket|confirmation|reference)\s*(?:number|no\.?|#)?:?\s*[A-Z0-9_-]{4,}\b",
    re.IGNORECASE,
)

# Personalized greetings to drop entirely (line / sentence)
GREETING_RE = re.compile(
    r"(?:Hi|Hello|Hey|Dear|Greetings|Welcome)\s+(?:" + "|".join(USER_FIRST_NAMES) + r")\b[^.!?\n]*[.!?\n]?",
    re.IGNORECASE,
)
# Lines that mention "your order", "your subscription", etc — drop the sentence
ACCOUNT_LINE_RE = re.compile(
    r"[^.!?\n]*\byour\s+(?:order|subscription|account|purchase|payment|delivery|shipment|tracking)\b[^.!?\n]*[.!?]",
    re.IGNORECASE,
)
# Lines that say things like "thank you for your purchase / order"
PURCHASE_LINE_RE = re.compile(
    r"[^.!?\n]*\b(?:thank you for your|thanks for your|we'?ve received your|here'?s your)\s+(?:order|purchase|payment)\b[^.!?\n]*[.!?]",
    re.IGNORECASE,
)
# Stripe-style 4242 references
PAYMENT_LAST4_RE = re.compile(r"\b(?:card|visa|mastercard|amex|discover|debit|credit)[^.\n]{0,40}\bending(?:\s+in)?\s+\d{4}\b", re.IGNORECASE)

# View-in-browser / preheader anchors — drop these <a> tags entirely
VIEW_ONLINE_TEXT_RE = re.compile(
    r"^\s*(?:view\s+(?:this\s+)?(?:email|message|newsletter)\s+(?:online|in\s+(?:your\s+)?browser)|having\s+trouble\s+(?:viewing|reading)|can'?t\s+(?:see|view)\s+(?:this|the)|click\s+here\s+to\s+view)",
    re.IGNORECASE,
)
# Unsubscribe anchors — drop entirely
UNSUB_TEXT_RE = re.compile(
    r"^\s*(?:unsubscribe|opt[-_\s]?out|manage\s+(?:your\s+)?(?:preferences|subscriptions?|email\s+preferences)|update\s+(?:your\s+)?(?:preferences|profile)|email\s+preferences|stop\s+receiving)",
    re.IGNORECASE,
)
# "Sent to <email>" / "you received this because" footers
WHY_RECEIVING_RE = re.compile(
    r"(?:you'?re\s+receiving\s+this|you\s+(?:are|were)\s+sent\s+this|sent\s+to\s+(?:you\s+)?because|this\s+email\s+was\s+sent\s+to)",
    re.IGNORECASE,
)

# Hostnames known to be tracking / view-mirror redirectors
TRACKING_HOSTS_RE = re.compile(
    r"(?:^|\.)(?:click|track|email|news|t|ml|sg|sgp|sgmail|mailgun|mailchimp|sendgrid|stripo|mlsend|klaviyo|hubspotemail|hsforms|cmail\d*|list-manage|elink)\.\w",
    re.IGNORECASE,
)
TRACKING_PIXEL_HOSTS = (
    "open.convertkit.com", "image-proxy", "google-analytics.com",
    "doubleclick.net", "facebook.com/tr", "track.mailerlite.com",
    "click.scottokeebs.com", "trk.klaviyomail.com",
)

# Subjects / senders we always skip outright (transactional, never archive)
SKIP_SUBJECT_RE = re.compile(
    r"\b(?:receipt|invoice|order\s*#|order\s+confirmation|shipped|delivery\s+update|tracking\s+number|payment\s+received|thank\s+you\s+for\s+your\s+(?:order|purchase|payment)|your\s+(?:order|payment|subscription)\s+(?:has\s+been|was|is))\b",
    re.IGNORECASE,
)
SKIP_SENDER_RE = re.compile(
    r"@(?:stripe\.com|paypal\.com|square\.com|venmo\.com|wellsfargo\.com|chase\.com|americanexpress\.com|amazon\.(?:com|co\.uk)|notifications?\.amazon|orders?\.|receipts?\.|noreply@(?:apple|google|microsoft)\.com)",
    re.IGNORECASE,
)


# ─── HTML parser ────────────────────────────────────────────────────


class _Sanitizer(HTMLParser):
    def __init__(self, base_url: str | None = None):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.out: list[str] = []
        self.skip_depth = 0          # > 0 means we're inside a BLOCK_TAG
        self.report: dict = {
            "stripped_tags": {},
            "tracking_pixels": 0,
            "view_online_links": 0,
            "unsub_links": 0,
            "tracking_redirects": 0,
            "anchor_chars_dropped": 0,
        }
        self._anchor_buf: list[str] | None = None
        self._anchor_attrs: dict | None = None
        self._anchor_drop = False

    # ── helpers ──

    def _allowed_attrs(self, tag, attrs):
        allowed = ALLOWED_ATTRS.get(tag, set()) | GLOBAL_ATTRS
        out = []
        for k, v in attrs:
            if k.lower() in allowed:
                out.append((k.lower(), v or ""))
        return out

    def _resolve_url(self, url: str) -> str:
        if not url:
            return ""
        if self.base_url and not url.startswith(("http://", "https://", "mailto:", "tel:", "#", "/")):
            return urljoin(self.base_url, url)
        return url

    def _is_tracking_pixel(self, attrs: dict) -> bool:
        try:
            w = int(attrs.get("width", "0"))
            h = int(attrs.get("height", "0"))
            if 0 < w <= 2 or 0 < h <= 2:
                return True
        except ValueError:
            pass
        src = (attrs.get("src") or "").lower()
        if any(t in src for t in TRACKING_PIXEL_HOSTS):
            return True
        if "tracking" in src or "open?" in src or "/p.gif" in src or "/pixel" in src:
            return True
        # 1x1 transparent/clear gif by filename
        if re.search(r"(?:1x1|spacer|clear|pixel|tracker)\.(?:gif|png)\b", src):
            return True
        return False

    def _bump(self, key, val=1):
        if isinstance(self.report[key], dict):
            self.report[key][val] = self.report[key].get(val, 0) + 1
        else:
            self.report[key] += val

    # ── handlers ──

    def handle_starttag(self, tag, attrs):
        if self.skip_depth:
            self.skip_depth += 0  # already inside a skipped block
            if tag in BLOCK_TAGS:
                self.skip_depth += 1
            return
        if tag in BLOCK_TAGS:
            self.skip_depth = 1
            self._bump("stripped_tags", tag)
            return

        attrs_d = {k.lower(): (v or "") for k, v in attrs}

        # Drop tracking pixels entirely
        if tag == "img" and self._is_tracking_pixel(attrs_d):
            self._bump("tracking_pixels")
            return

        # Anchor handling: buffer text, decide on close
        if tag == "a":
            href = self._resolve_url(attrs_d.get("href", ""))
            self._anchor_buf = []
            self._anchor_attrs = {"href": href, "title": attrs_d.get("title", "")}
            self._anchor_drop = False
            # tracking-redirect host? mark to flatten to text only
            try:
                host = urlparse(href).hostname or ""
            except Exception:
                host = ""
            if host and TRACKING_HOSTS_RE.search(host):
                self._anchor_attrs["_tracking_redirect"] = True
                self._bump("tracking_redirects")
            return

        # Resolve relative URLs
        if tag == "img":
            src = self._resolve_url(attrs_d.get("src", ""))
            if src:
                attrs_d["src"] = src

        # Strip everything except whitelisted tags (keep their content via passthrough)
        if tag not in ALLOWED_TAGS:
            self._bump("stripped_tags", tag)
            return

        kept = self._allowed_attrs(tag, list(attrs_d.items()))
        attr_str = "".join(f' {k}="{html_mod.escape(v, quote=True)}"' for k, v in kept)
        self.out.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag in BLOCK_TAGS:
                self.skip_depth -= 1
            return

        if tag == "a" and self._anchor_buf is not None:
            text = "".join(self._anchor_buf).strip()
            attrs = self._anchor_attrs or {}
            href = attrs.get("href", "")
            # Drop "view in browser" / "unsubscribe" links
            if VIEW_ONLINE_TEXT_RE.match(text):
                self._bump("view_online_links")
                self._anchor_buf = None
                self._anchor_attrs = None
                return
            if UNSUB_TEXT_RE.match(text):
                self._bump("unsub_links")
                self._anchor_buf = None
                self._anchor_attrs = None
                return
            # If anchor is a tracking redirect with text "click here" or empty, drop it
            if attrs.get("_tracking_redirect") and (not text or len(text) < 3):
                self._anchor_buf = None
                self._anchor_attrs = None
                return
            # Otherwise emit anchor; if tracking redirect, keep the text but null out href
            if attrs.get("_tracking_redirect"):
                self.out.append(html_mod.escape(text))
            else:
                href_attr = f' href="{html_mod.escape(href, quote=True)}"' if href else ""
                ttl = attrs.get("title", "")
                title_attr = f' title="{html_mod.escape(ttl, quote=True)}"' if ttl else ""
                self.out.append(f'<a{href_attr}{title_attr} rel="noopener" target="_blank">{html_mod.escape(text)}</a>')
            self._anchor_buf = None
            self._anchor_attrs = None
            return

        if tag in BLOCK_TAGS:
            return
        if tag not in ALLOWED_TAGS:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self._anchor_buf is not None:
            self._anchor_buf.append(data)
            return
        self.out.append(html_mod.escape(data))

    def handle_comment(self, data):
        # drop comments entirely
        return


# ─── PII regex pass ─────────────────────────────────────────────────


def _redact_pii(text: str, report: dict) -> str:
    """Apply regex-based redactions to text content. Mutates report counts."""
    counts = report.setdefault("redactions", {})

    def _sub(pattern, replacement, key, s):
        new, n = pattern.subn(replacement, s)
        if n:
            counts[key] = counts.get(key, 0) + n
        return new

    # Pull out greetings entirely
    text = _sub(GREETING_RE, "", "greeting", text)
    text = _sub(PURCHASE_LINE_RE, " ", "purchase_line", text)
    text = _sub(ACCOUNT_LINE_RE, " ", "account_line", text)
    text = _sub(PAYMENT_LAST4_RE, REDACT, "card_last4", text)

    text = _sub(CARD_NUM_RE, "[card redacted]", "card_full", text)
    text = _sub(SSN_RE, REDACT, "ssn", text)
    text = _sub(PHONE_RE, "[phone redacted]", "phone", text)
    text = _sub(ADDR_RE, "[address redacted]", "address", text)
    text = _sub(ZIP_NEAR_STATE_RE, "[zip redacted]", "zip", text)
    text = _sub(ORDER_RE, "[order # redacted]", "order_number", text)

    # Email addresses — redact ALL of them rather than guessing personal vs vendor
    # contact. Vendors usually link in support emails which are fine to remove.
    text = _sub(EMAIL_RE, "[email redacted]", "email", text)

    # Drop the user's own first name in any remaining text
    name_pat = re.compile(r"\b(?:" + "|".join(USER_FIRST_NAMES) + r")\b")
    text2, n = name_pat.subn(REDACT, text)
    if n:
        counts["user_name"] = counts.get("user_name", 0) + n
    text = text2

    return text


def _redact_in_html(html_text: str, report: dict) -> str:
    """Apply regex-based redactions to all visible text inside an HTML string."""
    parts = re.split(r"(<[^>]+>)", html_text)
    for i, part in enumerate(parts):
        if not part.startswith("<"):
            parts[i] = _redact_pii(part, report)
    return "".join(parts)


# ─── Footer / "why receiving" stripping ─────────────────────────────


def _drop_footer(html_text: str, report: dict) -> str:
    """Drop everything from the first 'why receiving' / unsubscribe-cluster line to end."""
    m = WHY_RECEIVING_RE.search(html_text)
    if not m:
        return html_text
    cut = m.start()
    # Walk back to the nearest paragraph/div boundary
    boundary = max(html_text.rfind("<p", 0, cut), html_text.rfind("<div", 0, cut),
                   html_text.rfind("</p>", 0, cut), html_text.rfind("</div>", 0, cut))
    if boundary < 0:
        boundary = cut
    report["footer_dropped"] = True
    report["footer_cut_at"] = boundary
    return html_text[:boundary]


# ─── Pre-cleanup (before HTMLParser) ────────────────────────────────


_DOCTYPE_RE = re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE)
_HEAD_RE = re.compile(r"<head\b[^>]*>.*?</head\s*>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_NOSCRIPT_RE = re.compile(r"<noscript\b[^>]*>.*?</noscript\s*>", re.IGNORECASE | re.DOTALL)
# Microsoft Outlook conditional comments — `<!--[if mso]>...<![endif]-->`
# Python's HTMLParser doesn't always handle these cleanly.
_MSO_COND_RE = re.compile(r"<!--\s*\[if[^]]*\]>.*?<!\[endif\]-->", re.IGNORECASE | re.DOTALL)
# Downlevel-revealed conditionals — `<!--[if !mso]><!-->...<!--<![endif]-->`
_MSO_REVEAL_OPEN_RE = re.compile(r"<!--\s*\[if[^]]*\]><!-->", re.IGNORECASE)
_MSO_REVEAL_CLOSE_RE = re.compile(r"<!--\s*<!\[endif\]-->", re.IGNORECASE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_BODY_OPEN_RE = re.compile(r"<body\b[^>]*>", re.IGNORECASE)
_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)


_ZERO_WIDTH_RE = re.compile(r"[​‌‍͏­⁠﻿]+")


def _pre_clean(html: str) -> str:
    """Strip head/script/style/IE-conditionals BEFORE HTMLParser sees them."""
    s = html
    # Strip zero-width chars used by Outlook/Gmail preheader padding
    s = _ZERO_WIDTH_RE.sub("", s)
    s = _DOCTYPE_RE.sub("", s)
    # Drop the entire <head>...</head> block (titles, metas, styles inside)
    s = _HEAD_RE.sub("", s)
    s = _STYLE_RE.sub("", s)
    s = _SCRIPT_RE.sub("", s)
    s = _NOSCRIPT_RE.sub("", s)
    # MSO conditionals
    s = _MSO_COND_RE.sub("", s)
    s = _MSO_REVEAL_OPEN_RE.sub("", s)
    s = _MSO_REVEAL_CLOSE_RE.sub("", s)
    # Plain HTML comments (be careful: only after MSO ones)
    s = _HTML_COMMENT_RE.sub("", s)
    # Strip <html> wrapper, leaving content
    s = re.sub(r"</?html\b[^>]*>", "", s, flags=re.IGNORECASE)
    # If we have a <body>...</body>, slice to its inside (more robust than
    # walking through arbitrary outer wrappers)
    bm = _BODY_OPEN_RE.search(s)
    if bm:
        end = _BODY_CLOSE_RE.search(s, bm.end())
        s = s[bm.end():(end.start() if end else len(s))]
    return s


# ─── Public API ─────────────────────────────────────────────────────


def sanitize_html(html: str, base_url: str | None = None) -> tuple[str, dict]:
    """Return (clean_html, report)."""
    if not html:
        return "", {"empty": True}
    pre = _pre_clean(html)
    p = _Sanitizer(base_url=base_url)
    p.feed(pre)
    p.close()
    cleaned = "".join(p.out)
    cleaned = _drop_footer(cleaned, p.report)
    cleaned = _redact_in_html(cleaned, p.report)
    # Collapse empty tags + whitespace
    for _ in range(3):  # multiple passes since collapsing one creates more
        cleaned = re.sub(r"<(p|div|span|td|tr|table|h[1-6]|li|ul|ol)[^>]*>\s*</\1>", "", cleaned)
    cleaned = re.sub(r"\s{3,}", " ", cleaned)
    cleaned = re.sub(r"(\s*<br[^>]*>\s*){3,}", "<br><br>", cleaned)
    return cleaned.strip(), p.report


def should_skip(subject: str, sender: str, body_text: str) -> tuple[bool, str]:
    """Return (skip, reason) — true means don't archive this email at all."""
    if subject and SKIP_SUBJECT_RE.search(subject):
        return True, f"subject matches transactional pattern"
    if sender and SKIP_SENDER_RE.search(sender):
        return True, f"sender domain is transactional"
    # If the body has lots of PII signals, skip rather than risk
    pii_signals = 0
    if body_text:
        if CARD_NUM_RE.search(body_text): pii_signals += 1
        if SSN_RE.search(body_text): pii_signals += 1
        if PAYMENT_LAST4_RE.search(body_text): pii_signals += 1
        if ORDER_RE.search(body_text): pii_signals += 1
        if PHONE_RE.search(body_text): pii_signals += 1
    if pii_signals >= 2:
        return True, f"body has {pii_signals} PII signals"
    return False, ""


if __name__ == "__main__":
    import sys, json
    src = sys.stdin.read()
    out, rep = sanitize_html(src)
    sys.stderr.write(json.dumps(rep, indent=2) + "\n")
    sys.stdout.write(out)

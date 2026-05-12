"""Title + body extractors for GB/IC items.

Pure functions. Pulls structured `gb` metadata out of the free-text
shapes designers actually use on Geekhack OP bodies (validated against
the 10-item audit in 2026-05). Patterns are regex + heuristic, not
LLM-based — deterministic, cheap, unit-testable. Edge cases that
the regexes miss simply don't populate the field (every gb chip is
"render if present, omit if absent").

Public entry point:
    extract_gb_facets(item, today=None) -> dict
        Returns a dict to merge into `item["gb"]`. Never raises.

Extracted facets (all optional):
    status:    "live" | "ended" | "postponed" | (None)
    designer:  str — "X by <Designer>" pattern
    starts_at: ISO date YYYY-MM-DD
    ends_at:   ISO date YYYY-MM-DD
    moq:       int — "MOQ of N" / "N MOQ"
    price_low, price_high: int cents
    vendor_regions: [{"region": "US", "name": "NovelKeys"}, ...]
"""
import datetime
import re

# ── Constants ─────────────────────────────────────────────────────

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_MONTH_TO_NUM = {m.lower(): i + 1 for i, m in enumerate(_MONTHS)}
_MONTH_RE = "|".join(_MONTHS)

# ── Status ────────────────────────────────────────────────────────


_STATUS_TITLE_PATTERNS = [
    (re.compile(r"\bpostponed\b", re.IGNORECASE), "postponed"),
    (re.compile(r"\blast\s*day\b", re.IGNORECASE), "live"),
    (re.compile(r"\bsold\s*out\b", re.IGNORECASE), "sold-out"),
    (re.compile(r"\bclosed\b|\bended\b", re.IGNORECASE), "ended"),
    (re.compile(r"\blive\s+till\b|\blive\s+until\b|\bnow\s+live\b",
                re.IGNORECASE), "live"),
]


def extract_status(title: str, body: str, *,
                   ends_at: str | None = None,
                   today: datetime.date | None = None) -> str | None:
    """Status precedence:
       1. Explicit title markers (LAST DAY, Postponed, Sold out, …)
       2. Body markers ("now live", "GB postponed")
       3. Date inference vs `today`: ends_at in the past → "ended"
    """
    text = title or ""
    for rx, label in _STATUS_TITLE_PATTERNS:
        if rx.search(text):
            return label
    for rx, label in _STATUS_TITLE_PATTERNS:
        if rx.search(body or ""):
            return label
    if ends_at:
        try:
            end = datetime.date.fromisoformat(ends_at)
        except ValueError:
            return None
        today = today or datetime.date.today()
        if end < today:
            return "ended"
        return "live"
    return None


# ── Designer ──────────────────────────────────────────────────────

# Designer-attribution patterns, tried in order. First survivor of the
# negative-filter checks wins. Designers credit themselves in several
# distinct shapes — see step-2.3 audit for the catalog. Adding a new
# pattern: keep the capture group narrow ([A-Z...]+) and tail-anchored
# (lookahead at terminator) so over-capture is bounded.
#
# False negatives are visibly fine (no designer rendered); false
# positives ("the aesthetics of early punk rock albums") make the card
# look broken. Hence the conservative bias.

# Project-name-led:  "<Project> by <Designer>"  (handles co-designers).
# Name capture allows internal `.` (so "keyhub.design" / "Moyu.studio"
# survive); lookahead distinguishes name-internal `.` from sentence-
# ending `.` by requiring whitespace/end after a terminating period.
_DESIGNER_BY_RE = re.compile(
    r"\bby\s+([A-Z][A-Za-z0-9 _.\-]{1,40}?"
    r"(?:\s+and\s+[A-Z][A-Za-z0-9 _.\-]{1,40}?)?)"
    r"(?=\s*(?:[,;:!?(]|\.(?:\s|$)|$|\n|\bDescription\b|\bDesigner\b|\bGreetings\b))",
    re.IGNORECASE,
)

# Studio-led:  "<n>th design from <Studio>"  (ORI CLUB, etc.).
# Stops at next punctuation or a clear next-word boundary.
_DESIGNER_FROM_RE = re.compile(
    r"\bdesign(?:ed)?\s+from\s+"
    r"([A-Z][\w.\-]+(?:\s+[A-Z][\w.\-]+)?)"
    r"(?=[\s\.,;!?\n]|$)",
    re.IGNORECASE,
)

# Studio self-intro:  "this is <Studio>"  (Moyu.studio, keyhub.design).
_DESIGNER_THIS_IS_RE = re.compile(
    r"\bthis\s+is\s+([A-Z][\w]+(?:\.[A-Za-z]+)?)"
    r"(?=[\s\.,;!?\n]|$)",
    re.IGNORECASE,
)

# Maker self-intro:  "My name is <handle>"  (often lowercase handles).
_DESIGNER_NAME_IS_RE = re.compile(
    r"\bMy\s+name\s+is\s+([A-Za-z][\w_\-]{2,30})"
    r"(?=[\s\.,;!?\n]|$)",
    re.IGNORECASE,
)

_DESIGNER_PATTERNS = (
    _DESIGNER_BY_RE,
    _DESIGNER_FROM_RE,
    _DESIGNER_THIS_IS_RE,
    _DESIGNER_NAME_IS_RE,
)

# Negative preludes — "by" preceded by one of these is almost always
# false (inspired by / based on / courtesy of / with credit / and so on).
_DESIGNER_NEG_PRELUDE = re.compile(
    r"\b(?:inspired|based|courtesy|made\s+possible|sponsored|powered|"
    r"manufactured|published|distributed)\s*$",
    re.IGNORECASE,
)

# Phrases that mean the captured "name" is actually prose/role text.
_DESIGNER_NEG_CONTENT = re.compile(
    r"\b(?:myself|us|our|the\s+team|with|collaboration|aesthetic|"
    r"design\s+language|legends?\s+from|stock|behalf)\b",
    re.IGNORECASE,
)

# Common articles that start prose rather than names.
_DESIGNER_NEG_PREFIX_RE = re.compile(
    r"^(?:the|a|an|our|some|several|many|all)\s+", re.IGNORECASE,
)


def _designer_match_ok(name: str, head: str, match_start: int) -> bool:
    """Shared post-match filter for every designer pattern. Returns
    True if the captured name passes; False if it looks like prose,
    month text, or follows a negative prelude (e.g. 'inspired by')."""
    # Reject month-words (false positives like "Available by May").
    if re.search(r"\b(?:" + _MONTH_RE + r")\b", name, re.IGNORECASE):
        return False
    # Reject if the match was preceded by a negative prelude.
    prelude_window = head[max(0, match_start - 24): match_start]
    if _DESIGNER_NEG_PRELUDE.search(prelude_window):
        return False
    # Reject prose-shaped captures.
    if _DESIGNER_NEG_PREFIX_RE.match(name):
        return False
    if _DESIGNER_NEG_CONTENT.search(name):
        return False
    return True


def extract_designer(body: str) -> str | None:
    """Return designer string ("Designer X" or "X and Y") if the OP
    body has an attribution idiom in roughly the first OP paragraph.
    Tries multiple shapes (see _DESIGNER_PATTERNS); first survivor of
    the negative-filter check wins. Returns None when no pattern
    matches cleanly — better to render no designer than a wrong one.
    """
    if not body:
        return None
    # Widened from 240 → 600 chars: long ICs sometimes preamble before
    # the attribution line (e.g. RF8X buries "by keyhub.design" after
    # a paragraph of context).
    # Widened from 240 → 400 chars: long ICs sometimes preamble before
    # the attribution line (e.g. RF8X buries "by keyhub.design" at
    # ~260 chars). Beyond 400 chars, "by X" is almost always prose.
    head = body[:400]
    for rx in _DESIGNER_PATTERNS:
        m = rx.search(head)
        if not m:
            continue
        # Strip trailing sentence punctuation that the lazy capture
        # may have included (esp. for the "from <Studio>." pattern).
        name = m.group(1).strip().rstrip(".,;:!?")
        if not name:
            continue
        if _designer_match_ok(name, head, m.start()):
            return name
    return None


# ── Dates ─────────────────────────────────────────────────────────


_DATE_TOKEN_RE = re.compile(
    rf"\b({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)

# "May 1st - June 1st" / "May 1st - 29th" / "May 1st to 29th".
# Also tolerates "and run until" / "and runs until" between the two
# dates — common phrasing in GB OPs ("April 13th and run until May 15").
_DATE_RANGE_RE = re.compile(
    rf"\b({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?"
    r"\s*(?:to|until|till|[-–—]|and\s+runs?\s+until)\s*"
    rf"(?:({_MONTH_RE})\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)

_END_PREFIX_RE = re.compile(
    rf"\b(?:ends?|until|till|run\s+until)\s+"
    rf"({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)

_START_PREFIX_RE = re.compile(
    rf"\b(?:starts?|from|available\s+from|begins?)\s+"
    rf"({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)


def _pick_year(month: int, today: datetime.date) -> int:
    """Choose the year for a bare month/day. If the resulting date is
    more than 60 days in the past, advance a year — GB-cycle dates
    are always near-future or recent-past."""
    cand = datetime.date(today.year, month, 1)
    if (today - cand).days > 60:
        return today.year + 1
    return today.year


def _to_iso(month_name: str, day: str, today: datetime.date) -> str | None:
    m = _MONTH_TO_NUM.get(month_name.lower())
    if not m:
        return None
    try:
        d = int(day)
    except ValueError:
        return None
    if not (1 <= d <= 31):
        return None
    year = _pick_year(m, today)
    try:
        return datetime.date(year, m, d).isoformat()
    except ValueError:
        return None


def extract_dates(body: str, today: datetime.date | None = None
                  ) -> tuple[str | None, str | None]:
    """Return (starts_at, ends_at) ISO strings or (None, None).

    Order of preference:
      1. Explicit ranges ("May 1st - June 1st", "May 1st to 29th")
      2. End-prefixed token ("ends June 14")
      3. Start-prefixed token ("from May 1st") combined with any other
         bare date as the end.
    """
    today = today or datetime.date.today()
    if not body:
        return None, None
    body = body[:1200]  # cap; dates are always near OP top

    m = _DATE_RANGE_RE.search(body)
    if m:
        m1, d1, m2, d2 = m.group(1), m.group(2), m.group(3), m.group(4)
        m2 = m2 or m1  # range with implicit second month
        s = _to_iso(m1, d1, today)
        e = _to_iso(m2, d2, today)
        if s and e:
            return s, e

    end_m = _END_PREFIX_RE.search(body)
    if end_m:
        e = _to_iso(end_m.group(1), end_m.group(2), today)
        if e:
            return None, e

    start_m = _START_PREFIX_RE.search(body)
    if start_m:
        s = _to_iso(start_m.group(1), start_m.group(2), today)
        return s, None

    return None, None


# ── Photo / render credits ───────────────────────────────────────


# Common shapes (validated against real audit data):
#   "Photo by: keima"   "Photos by X"   "Render by X"
#   "Photography: X"    "Shot by X"     "Photos: X"
# Captures the first name. Multi-render OPs (Distortion 40s lists
# "Renders F1-40 by Geon, MB-44 by MelonBred…") collapse to the
# first credit found.
_PHOTO_CREDIT_RE = re.compile(
    r"\b(?:Photos?|Photography|Rendered?|Renders|Shot)"
    # Optional short bridging — kit codes like "F1-40" in
    # "Renders F1-40 by Geon" — but no punctuation (keeps "by"
    # adjacent to the keyword phrase).
    r"[^.\n,;:!?]{0,20}?"
    r"\bby[:\s]+"
    # Single-word handle. Both "keima" (lowercase) and "Geon" /
    # "MelonBred" (camelCase) survive. Two-word credits ("Trash
    # Man") lose the second word — accepted tradeoff to avoid
    # capturing "Geon for the kit".
    r"([A-Za-z][A-Za-z0-9_\-]{0,30})"
    r"(?=\s|$|[\.,;:!?])",
    re.IGNORECASE,
)


def extract_photo_credit(body: str) -> str | None:
    """Return the first photo / render credit name found in the OP
    body, or None when no clean credit line exists.

    Conservative — designed to avoid false positives. Won't pick up
    "Renders may not picture actual colors" (no name after) or
    "Renders F1-40 by Geon" (the prefix is the kit code, the name
    after is the credit — handled by the lazy name capture)."""
    if not body:
        return None
    m = _PHOTO_CREDIT_RE.search(body)
    if not m:
        return None
    name = m.group(1).strip().rstrip(".,;:!?")
    # Reject prose-shaped captures.
    if not name or _DESIGNER_NEG_PREFIX_RE.match(name):
        return None
    if re.search(r"\b(?:" + _MONTH_RE + r")\b", name, re.IGNORECASE):
        return None
    # Reject very-long captures (likely caught surrounding prose).
    if len(name) > 35:
        return None
    return name


# ── MOQ ──────────────────────────────────────────────────────────


_MOQ_RE = re.compile(
    r"\b(?:MOQ\s*(?:of\s+)?(\d{2,4})|(\d{2,4})\s*MOQ)\b", re.IGNORECASE,
)


def extract_moq(body: str) -> int | None:
    if not body:
        return None
    m = _MOQ_RE.search(body)
    if not m:
        return None
    val = m.group(1) or m.group(2)
    try:
        n = int(val)
    except ValueError:
        return None
    # Sanity cap — keycap GBs typically 25–500. A 4-digit "MOQ 1080"
    # is almost certainly a typo or unrelated number.
    if not (10 <= n <= 1500):
        return None
    return n


# ── Price range ──────────────────────────────────────────────────


# "$135", "$149.00", "$135 USD"
_PRICE_RE = re.compile(
    r"\$(\d{2,4})(?:\.(\d{2}))?", re.IGNORECASE,
)

# Anchor: look for prices in the vicinity of "Base", "Price", "Kit:"
# rather than blindly grabbing every dollar sign (deskpad/numpad
# prices distort the range otherwise).
_PRICE_ANCHOR_RE = re.compile(
    r"\b(?:Base|Pricing|Price|Kit\s*:|@)\b", re.IGNORECASE,
)


def extract_price_range(body: str) -> tuple[int | None, int | None]:
    """Return (price_low, price_high) in cents. Both None if no
    confident extraction. The strategy:

    1. If body has a "Base" / "Pricing" anchor, scan a window around
       it. "Base $135" or "Base: $149 / $130 / $113" (MOQ-tier ladder)
       give us either a single base price or a low-high range. Window
       extends 250 chars after the anchor — far enough to catch the
       ladder, not so far it picks up "Novelties $45".
    2. Otherwise scan the first 600 chars and accept in-range prices.
    """
    if not body:
        return None, None

    anchor = _PRICE_ANCHOR_RE.search(body)
    if anchor:
        # Anchor-led: window straddles the anchor (some OPs write
        # "$80 base", others write "Base $135"). Cutoff at the first
        # add-on category keyword so deskpad/numpad/novelty prices
        # don't contaminate the base range.
        win_start = max(0, anchor.start() - 100)
        win_end = anchor.start() + 250
        window = body[win_start:win_end]
        cutoff = re.search(
            r"\b(?:Novelt|Deskpad|Numpad|Spacebar|Add[\-\s]?on)",
            window, re.IGNORECASE,
        )
        if cutoff:
            window = window[:cutoff.start()]
    else:
        # Unanchored fallback: scan the first 600 chars of body.
        window = body[:600]

    prices = []
    for m in _PRICE_RE.finditer(window):
        dollars = int(m.group(1))
        cents = int(m.group(2) or 0)
        # Sanity: base keycap kits run roughly $40–$300.
        if 30 <= dollars <= 400:
            prices.append(dollars * 100 + cents)
        if len(prices) >= 6:
            break

    if not prices:
        return None, None
    if len(prices) == 1:
        return prices[0], None
    return min(prices), max(prices)


# ── Vendors per region ───────────────────────────────────────────


# Region tokens common on Geekhack: US, UK, EU, CA, KR, JP, CN, SG,
# AU, OCO/OCN (Oceania), CIS, MX, SEA, LATAM. We capture the vendor
# name following the region token until the next region or end-of-line.
_REGIONS = (
    "US", "USA", "UK", "EU", "CA", "KR", "JP", "CN", "SG", "AU", "OC",
    "OCO", "OCN", "CIS", "MX", "SEA", "LATAM", "TR", "PH", "MY", "TH",
    "IN", "BR", "RU", "ID", "VN", "HK", "TW", "NZ", "AUS",
)
_REGION_RE = "|".join(_REGIONS)

_VENDOR_LINE_RE = re.compile(
    rf"\b({_REGION_RE})\b\s*[:\-]\s*"
    # Name capture: starts with a letter, cannot contain ":" (a colon
    # marks the start of the *next* region — without this exclusion,
    # "CA: EU: Vendor" gets read as CA → "EU: Vendor").
    r"([A-Za-z][^\n:]{0,60}?)"
    rf"(?=\s+(?:{_REGION_RE})\s*[:\-]|[\n\r]|\.{{2,}}|$)",
    re.IGNORECASE,
)


def extract_vendor_regions(body: str) -> list[dict]:
    """Return a list of {"region": "US", "name": "NovelKeys"} dicts in
    source order, deduplicated by (region, name)."""
    if not body:
        return []
    out: list[dict] = []
    seen: set = set()
    for m in _VENDOR_LINE_RE.finditer(body):
        region = m.group(1).upper()
        # Normalize "AUS" → "AU", "USA" → "US", "OCO/OCN" → "OC"
        region = {"AUS": "AU", "USA": "US",
                  "OCO": "OC", "OCN": "OC"}.get(region, region)
        name = m.group(2).strip().rstrip(",.;:")
        # Skip if the captured "name" is itself a region token (means
        # the designer wrote "US: UK: Vendor" with an empty region).
        if not name or name.upper() in _REGIONS:
            continue
        # Skip if the name is suspiciously long (caught surrounding text).
        if len(name) > 50:
            continue
        # Skip names that look like prose (multiple sentences / lowercase start).
        if not name[0].isalpha():
            continue
        key = (region, name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"region": region, "name": name})
    return out


# ── Public entry point ──────────────────────────────────────────


def extract_gb_facets(item: dict,
                      today: datetime.date | None = None) -> dict:
    """Extract structured GB metadata from an item's title + takeaway.

    Returns a dict suitable for merging into `item["gb"]`. Pure, never
    raises, returns {} when no fields confidently extracted.
    """
    title = item.get("title") or ""
    body = item.get("takeaway") or ""
    today = today or datetime.date.today()

    out: dict = {}

    designer = extract_designer(body)
    if designer:
        out["designer"] = designer

    starts_at, ends_at = extract_dates(body, today=today)
    if starts_at:
        out["starts_at"] = starts_at
    if ends_at:
        out["ends_at"] = ends_at

    status = extract_status(title, body, ends_at=ends_at, today=today)
    if status:
        out["status"] = status

    moq = extract_moq(body)
    if moq is not None:
        out["moq"] = moq

    lo, hi = extract_price_range(body)
    if lo is not None:
        out["price_low"] = lo
    if hi is not None and hi != lo:
        out["price_high"] = hi

    vendors = extract_vendor_regions(body)
    if vendors:
        out["vendor_regions"] = vendors

    photo = extract_photo_credit(body)
    if photo:
        out["photo_credit"] = photo

    return out

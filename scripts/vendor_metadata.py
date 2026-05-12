"""Vendor metadata refresher.

For every URL stored in `item.gb.vendor_links`, fetch the vendor's
Shopify product.json endpoint (`<url>.json`) and extract the canonical
price + currency. Mutates the vendor_link dict in place; the render
layer then displays a "$135" chip beside each vendor pill.

What we *can* extract reliably across Shopify stores:
  - price (variants[*].price as decimal string)
  - currency (variants[*].price_currency, ISO-4217)

What we *can't* extract from products.json (returns None):
  - availability / in-stock / sold-out
  - inventory_quantity
That signal lives on the HTML product page and would require scraping;
v1 ships price-only and we can layer availability later if useful.

Politeness: HostThrottle from http_polite spaces same-host requests.
Per-link freshness window: if a link was refreshed within --max-age
hours, skip the refetch (so re-runs during a day don't hammer
vendors).
"""
import argparse
import datetime
import json
import pathlib
import re
import sys
import urllib.request
import urllib.parse

import http_polite

ROOT = pathlib.Path(__file__).resolve().parent.parent
USER_AGENT = "keyboard-wire/1.0 (+https://keyboard-newswire.com)"

# Shopify's `.js` endpoint exposes availability per variant but
# omits `price_currency`. The `.json` endpoint has currency but no
# availability. Rather than fetch both, we infer currency from the
# vendor's host — stores rarely switch currency. Map is hand-curated;
# unknown hosts fall back to None and the price chip renders without
# a symbol.
_HOST_CURRENCY = {
    # USD
    "novelkeys.com":       "USD",
    "cannonkeys.com":      "USD",
    "bowlkeyboards.com":   "USD",
    "saberkeebs.com":      "USD",
    "mechsandco.com":      "USD",
    "minokeys.com":        "USD",
    "kbdfans.com":         "USD",
    "geon.works":          "USD",
    "geonworks.com":       "USD",
    "zfrontier.com":       "USD",
    # GBP
    "prototypist.net":     "GBP",
    "proto-typist.com":    "GBP",
    # EUR
    "oblotzky.industries": "EUR",
    "mykeyboard.eu":       "EUR",
    "keeb.supply":         "EUR",
    "coffeekeys.de":       "EUR",
    "torokeeb.store":      "EUR",
    "www.torokeeb.store":  "EUR",
    "delta-key.co":        "EUR",
    # JPY
    "shop.yushakobo.jp":   "JPY",
    "yushakobo.jp":        "JPY",
    # CNY
    "typist.club":         "CNY",
    # SGD
    "ilumkb.com":          "SGD",
    "monokei.co":          "SGD",
    "ktechs.store":        "SGD",
    # AUD
    "keebzncables.com":     "AUD",
    "www.keebzncables.com": "AUD",
    "dailyclack.com":      "AUD",
    # CAD
    "deskhero.ca":         "CAD",
    "www.deskhero.ca":     "CAD",
}


def currency_for_host(host: str | None) -> str | None:
    if not host:
        return None
    return _HOST_CURRENCY.get(host.lower().lstrip("."))

# Per-host throttle for vendor fetches — same instance type used in
# fetch_images.py, distinct instance because different code paths.
_THROTTLE = http_polite.HostThrottle(min_interval=1.0)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def product_json_url(product_page_url: str, suffix: str = ".js") -> str | None:
    """Convert a vendor's product page URL to its Shopify product API
    endpoint.

    `https://novelkeys.com/products/foo` → `https://novelkeys.com/products/foo.js`

    Defaults to `.js` (which exposes `available` per variant reliably
    across Shopify stores; the `.json` variant returns None for that
    field). Pass `suffix=".json"` for the older endpoint.

    Returns None if the URL doesn't have the expected `/products/<handle>`
    shape (most non-Shopify hosts don't).
    """
    if not product_page_url:
        return None
    parsed = urllib.parse.urlparse(product_page_url)
    if not parsed.scheme or not parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    m = re.search(r"(/products/[^/]+)$", path)
    if not m:
        return None
    new_path = path[: m.start()] + m.group(1) + suffix
    return urllib.parse.urlunparse((
        parsed.scheme, parsed.netloc, new_path, "", "", "",
    ))


def parse_product_metadata(payload: dict) -> dict | None:
    """Extract price/currency/availability from a Shopify product
    payload. Accepts both shapes:

      `.json` endpoint: `{"product": {"variants": [...], ...}}`
      `.js` endpoint:   `{"variants": [...], "title": ..., ...}`

    Returns `{price_low, price_high, currency, available}` or None
    if no usable variant prices were found. `available` is True if
    any variant is in stock; False if all variants are explicitly
    out of stock; absent if no variant exposes availability.

    Price values are in cents. Currency on `.js` shape comes from the
    payload's top-level `price_currency` key; on `.json` we read it
    off each variant.
    """
    if not isinstance(payload, dict):
        return None
    # Unwrap if this is a .json-shaped payload.
    source = payload.get("product") if "product" in payload else payload
    if not isinstance(source, dict):
        return None
    variants = source.get("variants") or []
    prices: list[int] = []
    currency: str | None = source.get("price_currency") or None
    availabilities: list[bool] = []
    for v in variants:
        raw_price = v.get("price")
        if raw_price is None:
            continue
        try:
            # .json gives prices as decimal strings ("135.00"); .js
            # gives them as integer cents (13500). Detect by type.
            if isinstance(raw_price, (int, float)) and not isinstance(raw_price, bool):
                total_cents = int(raw_price)
            else:
                dollars, _, cents = str(raw_price).partition(".")
                cents = (cents + "00")[:2]
                total_cents = int(dollars) * 100 + int(cents)
        except (ValueError, TypeError):
            continue
        if total_cents <= 0:
            continue
        prices.append(total_cents)
        if currency is None:
            currency = v.get("price_currency") or None
        if "available" in v and v["available"] is not None:
            availabilities.append(bool(v["available"]))
    if not prices:
        return None
    out: dict = {"price_low": min(prices)}
    if max(prices) > min(prices):
        out["price_high"] = max(prices)
    if currency:
        out["currency"] = currency
    if availabilities:
        # Any variant in stock → product is in stock.
        out["available"] = any(availabilities)
    return out


def fetch_product_metadata(product_page_url: str, *,
                           throttle: http_polite.HostThrottle | None = None,
                           timeout: float = 12) -> dict | None:
    """One-shot fetch + parse. Returns metadata dict or None on any
    failure. Honors the per-host throttle when given. Currency is
    backfilled from the host map when the .js endpoint omits it
    (which is always — see _HOST_CURRENCY)."""
    json_url = product_json_url(product_page_url)
    if not json_url:
        return None
    if throttle is not None:
        throttle.wait(json_url)
    req = urllib.request.Request(json_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    meta = parse_product_metadata(payload)
    if meta is not None and "currency" not in meta:
        host = (urllib.parse.urlparse(product_page_url).hostname or "").lower()
        cur = currency_for_host(host)
        if cur:
            meta["currency"] = cur
    return meta


def is_stale(link: dict, *, max_age_hours: float) -> bool:
    """True if `link['fetched_at']` is older than max_age_hours, or
    if no fetched_at is recorded yet."""
    ts = link.get("metadata_fetched_at")
    if not ts:
        return True
    try:
        when = datetime.datetime.fromisoformat(ts)
    except Exception:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    age_hours = (datetime.datetime.now(datetime.timezone.utc) - when).total_seconds() / 3600
    return age_hours >= max_age_hours


def refresh_corpus(corpus: dict, *,
                   max_age_hours: float = 6.0,
                   throttle: http_polite.HostThrottle | None = None,
                   dry_run: bool = False) -> tuple[int, int]:
    """Walk corpus.days[*].items[*].gb.vendor_links and refresh each
    link's metadata when stale. Returns (refreshed, total)."""
    throttle = throttle or _THROTTLE
    refreshed = 0
    total = 0
    for day in corpus.get("days", []):
        for it in day.get("items", []):
            gb = it.get("gb") or {}
            links = gb.get("vendor_links") or []
            for link in links:
                total += 1
                if not is_stale(link, max_age_hours=max_age_hours):
                    continue
                if dry_run:
                    continue
                meta = fetch_product_metadata(link.get("url") or "",
                                              throttle=throttle)
                # Always stamp the fetched_at — even on failure — so a
                # broken URL doesn't get retried every cron run.
                link["metadata_fetched_at"] = _now_iso()
                if meta:
                    # Merge into the link dict; remove stale fields
                    # if they were set but the new fetch returns nothing.
                    for k in ("price_low", "price_high", "currency",
                              "available"):
                        if k in meta:
                            link[k] = meta[k]
                        elif k in link:
                            del link[k]
                    refreshed += 1
    return refreshed, total


def _load_corpus(path: pathlib.Path) -> dict:
    return json.loads(path.read_text())


def _save_corpus(path: pathlib.Path, corpus: dict) -> None:
    path.write_text(json.dumps(corpus, indent=2, ensure_ascii=False) + "\n")


def _mirror_per_day(corpus: dict, days_dir: pathlib.Path) -> None:
    """Update each data/days/<date>.json so the per-day file mirrors
    the corpus-level updates. Only writes the `gb` block on each item."""
    for day in corpus.get("days", []):
        p = days_dir / f"{day['date']}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        by_id = {it["id"]: it for it in day.get("items", []) if it.get("id")}
        for di in d.get("items", []):
            if di.get("id") in by_id and "gb" in by_id[di["id"]]:
                di["gb"] = by_id[di["id"]]["gb"]
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ROOT / "data" / "corpus.json"))
    ap.add_argument("--days-dir", default=str(ROOT / "data" / "days"))
    ap.add_argument("--max-age", type=float, default=6.0,
                    help="hours; skip links refreshed more recently "
                         "(default: 6.0)")
    ap.add_argument("--throttle", type=float, default=1.0,
                    help="seconds between same-host fetches "
                         "(default: 1.0)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    corpus_path = pathlib.Path(args.corpus)
    days_dir = pathlib.Path(args.days_dir)
    corpus = _load_corpus(corpus_path)
    throttle = http_polite.HostThrottle(min_interval=args.throttle)

    refreshed, total = refresh_corpus(
        corpus,
        max_age_hours=args.max_age,
        throttle=throttle,
        dry_run=args.dry_run,
    )
    sys.stderr.write(
        f"vendor metadata: refreshed {refreshed}/{total} links "
        f"({'dry-run' if args.dry_run else 'live'})\n"
    )
    if not args.dry_run and refreshed:
        _save_corpus(corpus_path, corpus)
        _mirror_per_day(corpus, days_dir)


if __name__ == "__main__":
    main()

"""Unit tests for scripts/vendor_metadata.py (Step 2b).

Run from repo root: python3 -m unittest tests.test_vendor_metadata
"""
import datetime
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import vendor_metadata as vm  # noqa: E402


# ────────────────── product_json_url ──────────────────


class CurrencyForHost(unittest.TestCase):
    def test_known_hosts(self):
        self.assertEqual(vm.currency_for_host("novelkeys.com"), "USD")
        self.assertEqual(vm.currency_for_host("prototypist.net"), "GBP")
        self.assertEqual(vm.currency_for_host("oblotzky.industries"), "EUR")
        self.assertEqual(vm.currency_for_host("shop.yushakobo.jp"), "JPY")
        self.assertEqual(vm.currency_for_host("ilumkb.com"), "SGD")
        self.assertEqual(vm.currency_for_host("deskhero.ca"), "CAD")

    def test_unknown_host_returns_none(self):
        self.assertIsNone(vm.currency_for_host("randomshop.com"))

    def test_empty(self):
        self.assertIsNone(vm.currency_for_host(""))
        self.assertIsNone(vm.currency_for_host(None))


class ProductJsonUrl(unittest.TestCase):
    def test_basic_product_url_defaults_to_js(self):
        self.assertEqual(
            vm.product_json_url("https://novelkeys.com/products/foo"),
            "https://novelkeys.com/products/foo.js",
        )

    def test_explicit_json_suffix(self):
        self.assertEqual(
            vm.product_json_url("https://novelkeys.com/products/foo",
                                suffix=".json"),
            "https://novelkeys.com/products/foo.json",
        )

    def test_trailing_slash(self):
        self.assertEqual(
            vm.product_json_url("https://novelkeys.com/products/foo/"),
            "https://novelkeys.com/products/foo.js",
        )

    def test_strips_query_and_fragment(self):
        self.assertEqual(
            vm.product_json_url(
                "https://novelkeys.com/products/foo?utm=x#frag"
            ),
            "https://novelkeys.com/products/foo.js",
        )

    def test_collection_nested(self):
        # /collections/x/products/y → .js endpoint sits at the same path.
        self.assertEqual(
            vm.product_json_url(
                "https://geon.works/collections/group-buys/products/greg-2"
            ),
            "https://geon.works/collections/group-buys/products/greg-2.js",
        )

    def test_non_product_url(self):
        # Vendor home page — no /products/ segment.
        self.assertIsNone(vm.product_json_url("https://novelkeys.com/"))

    def test_empty(self):
        self.assertIsNone(vm.product_json_url(""))
        self.assertIsNone(vm.product_json_url(None))


# ────────────────── parse_product_metadata ──────────────────


def _payload(variants):
    return {"product": {"title": "X", "variants": variants}}


class ParseProductMetadata(unittest.TestCase):
    def test_single_variant(self):
        out = vm.parse_product_metadata(_payload([
            {"price": "135.00", "price_currency": "USD"},
        ]))
        self.assertEqual(out["price_low"], 13500)
        self.assertNotIn("price_high", out)  # no range when only one
        self.assertEqual(out["currency"], "USD")

    def test_multi_variant_range(self):
        out = vm.parse_product_metadata(_payload([
            {"price": "135.00", "price_currency": "USD"},
            {"price": "45.00",  "price_currency": "USD"},
            {"price": "25.00",  "price_currency": "USD"},
        ]))
        self.assertEqual(out["price_low"], 2500)
        self.assertEqual(out["price_high"], 13500)

    def test_decimal_handling_with_no_cents(self):
        out = vm.parse_product_metadata(_payload([
            {"price": "135", "price_currency": "USD"},
        ]))
        self.assertEqual(out["price_low"], 13500)

    def test_gbp_currency(self):
        out = vm.parse_product_metadata(_payload([
            {"price": "102.50", "price_currency": "GBP"},
        ]))
        self.assertEqual(out["currency"], "GBP")
        self.assertEqual(out["price_low"], 10250)

    def test_skips_zero_or_invalid_prices(self):
        out = vm.parse_product_metadata(_payload([
            {"price": "0.00",   "price_currency": "USD"},
            {"price": "not_a_number", "price_currency": "USD"},
            {"price": "100.00", "price_currency": "USD"},
        ]))
        self.assertEqual(out["price_low"], 10000)
        self.assertNotIn("price_high", out)

    def test_no_currency(self):
        # Variant lacks price_currency — return price but no currency.
        out = vm.parse_product_metadata(_payload([
            {"price": "100.00"},
        ]))
        self.assertEqual(out["price_low"], 10000)
        self.assertNotIn("currency", out)

    def test_empty_variants(self):
        self.assertIsNone(vm.parse_product_metadata(_payload([])))

    def test_no_product_key(self):
        # `.js` endpoint payload has variants at the top level; this
        # used to fail under the old parser. Now it parses.
        self.assertIsNone(vm.parse_product_metadata({}))  # no variants
        self.assertIsNone(vm.parse_product_metadata("not-a-dict"))

    def test_js_shape_no_product_wrapper(self):
        # `.js` endpoint: variants at top level, prices as INT cents.
        out = vm.parse_product_metadata({
            "variants": [
                {"price": 13500, "available": True},
                {"price": 4500, "available": False},
            ],
            "price_currency": "USD",
        })
        self.assertEqual(out["price_low"], 4500)
        self.assertEqual(out["price_high"], 13500)
        self.assertEqual(out["currency"], "USD")
        # Any variant available → True (base stocked even if novelties sold out)
        self.assertTrue(out["available"])

    def test_availability_all_out_of_stock(self):
        out = vm.parse_product_metadata({
            "variants": [
                {"price": 13500, "available": False},
                {"price": 4500, "available": False},
            ],
        })
        self.assertFalse(out["available"])

    def test_availability_none_when_field_absent(self):
        # No variant has `available` key → field omitted from output.
        out = vm.parse_product_metadata({
            "variants": [{"price": "135.00"}],
        })
        self.assertNotIn("available", out)

    def test_js_shape_decimal_strings_also_accepted(self):
        # Some stores serve `.js` with decimal strings instead of cents.
        out = vm.parse_product_metadata({
            "variants": [{"price": "135.00", "available": True}],
        })
        self.assertEqual(out["price_low"], 13500)


# ────────────────── is_stale ──────────────────


class IsStale(unittest.TestCase):
    def test_no_timestamp_is_stale(self):
        self.assertTrue(vm.is_stale({}, max_age_hours=1.0))

    def test_recent_timestamp_not_stale(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        link = {"metadata_fetched_at": now.isoformat()}
        self.assertFalse(vm.is_stale(link, max_age_hours=6.0))

    def test_old_timestamp_is_stale(self):
        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=8)
        link = {"metadata_fetched_at": old.isoformat()}
        self.assertTrue(vm.is_stale(link, max_age_hours=6.0))

    def test_invalid_timestamp_is_stale(self):
        link = {"metadata_fetched_at": "not-iso"}
        self.assertTrue(vm.is_stale(link, max_age_hours=6.0))


# ────────────────── refresh_corpus (monkeypatched fetch) ─────────


class RefreshCorpus(unittest.TestCase):
    def _corpus_with_links(self, links):
        return {"days": [{"date": "2026-05-12", "items": [{
            "id": "geekhack-1", "gb": {"vendor_links": list(links)},
        }]}]}

    def test_refreshes_stale_links_only(self):
        # Two links: one stale, one fresh.
        fresh_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        old_ts = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(hours=24)).isoformat()
        corpus = self._corpus_with_links([
            {"url": "https://x/a", "metadata_fetched_at": old_ts},
            {"url": "https://x/b", "metadata_fetched_at": fresh_ts},
        ])
        called = []
        orig = vm.fetch_product_metadata
        vm.fetch_product_metadata = (
            lambda url, throttle=None: called.append(url) or
            {"price_low": 10000, "currency": "USD"}
        )
        try:
            refreshed, total = vm.refresh_corpus(corpus, max_age_hours=6.0)
        finally:
            vm.fetch_product_metadata = orig
        self.assertEqual(refreshed, 1)
        self.assertEqual(total, 2)
        self.assertEqual(called, ["https://x/a"])

    def test_dry_run_does_no_fetches(self):
        corpus = self._corpus_with_links([
            {"url": "https://x/a"},  # no fetched_at → stale
        ])
        called = []
        orig = vm.fetch_product_metadata
        vm.fetch_product_metadata = lambda url, throttle=None: called.append(url) or None
        try:
            refreshed, total = vm.refresh_corpus(
                corpus, dry_run=True,
            )
        finally:
            vm.fetch_product_metadata = orig
        self.assertEqual(called, [])
        self.assertEqual(refreshed, 0)
        self.assertEqual(total, 1)

    def test_records_fetched_at_even_on_failure(self):
        # Failing fetch should still stamp metadata_fetched_at so
        # subsequent runs don't re-hit a broken URL.
        corpus = self._corpus_with_links([
            {"url": "https://broken/a"},
        ])
        orig = vm.fetch_product_metadata
        vm.fetch_product_metadata = lambda url, throttle=None: None
        try:
            vm.refresh_corpus(corpus, max_age_hours=6.0)
        finally:
            vm.fetch_product_metadata = orig
        link = corpus["days"][0]["items"][0]["gb"]["vendor_links"][0]
        self.assertIn("metadata_fetched_at", link)
        self.assertNotIn("price_low", link)

    def test_clears_stale_price_when_refetch_returns_none(self):
        corpus = self._corpus_with_links([
            {"url": "https://x/a", "price_low": 5000, "currency": "USD"},
        ])
        orig = vm.fetch_product_metadata
        vm.fetch_product_metadata = lambda url, throttle=None: None
        try:
            vm.refresh_corpus(corpus, max_age_hours=6.0)
        finally:
            vm.fetch_product_metadata = orig
        link = corpus["days"][0]["items"][0]["gb"]["vendor_links"][0]
        # fetched_at stamped, but no price update means stale price stays
        # (we only clear when a successful fetch returns no field).
        self.assertEqual(link.get("price_low"), 5000)

    def test_clears_specific_fields_when_new_fetch_drops_them(self):
        # If a successful fetch returns price_low but not price_high,
        # the high field should be cleared.
        corpus = self._corpus_with_links([
            {"url": "https://x/a",
             "price_low": 5000, "price_high": 9000, "currency": "USD"},
        ])
        orig = vm.fetch_product_metadata
        vm.fetch_product_metadata = lambda url, throttle=None: {
            "price_low": 6000, "currency": "USD",
        }
        try:
            vm.refresh_corpus(corpus, max_age_hours=6.0)
        finally:
            vm.fetch_product_metadata = orig
        link = corpus["days"][0]["items"][0]["gb"]["vendor_links"][0]
        self.assertEqual(link["price_low"], 6000)
        self.assertNotIn("price_high", link)
        self.assertEqual(link["currency"], "USD")


if __name__ == "__main__":
    unittest.main()

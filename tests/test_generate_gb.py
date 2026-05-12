"""Unit tests for the GB/IC quarantine in scripts/generate.py.

Run from repo root: python3 -m unittest tests.test_generate_gb
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate as gen  # noqa: E402


def _item(source: str, item_id: str, topics=None) -> dict:
    return {
        "id": item_id,
        "title": f"item {item_id}",
        "url": f"https://example/{item_id}",
        "source": source,
        "category": "breaking",
        "takeaway": "",
        "topics": topics or [],
    }


CORPUS_FIXTURE = {
    "title": "test",
    "tagline": "test",
    "days": [
        {"date": "2026-05-12", "items": [
            _item("hn", "hn-1"),
            _item("geekhack", "geekhack-100", topics=["group-buys-vendors"]),
            _item("reddit", "reddit-1"),
            _item("shopify", "shopify-novelkeys-99",
                  topics=["group-buys-vendors"]),
        ]},
        {"date": "2026-05-11", "items": [
            _item("kbdnews", "kbdnews-7"),
            _item("geekhack", "geekhack-101", topics=["group-buys-vendors"]),
        ]},
        {"date": "2026-05-10", "items": []},  # empty day preserved
    ],
}


class SourceLabel(unittest.TestCase):
    """Regression coverage for the bug where every non-Reddit source
    was falling through to the literal label 'Reddit'."""

    def test_geekhack_uses_via(self):
        self.assertEqual(
            gen.source_label({"source": "geekhack",
                              "via": "Geekhack · Group Buys"}),
            "Geekhack · Group Buys",
        )

    def test_kbdnews_uses_via(self):
        self.assertEqual(
            gen.source_label({"source": "kbdnews", "via": "KBD.news"}),
            "KBD.news",
        )

    def test_reddit_with_sub(self):
        self.assertEqual(
            gen.source_label({"source": "reddit", "subreddit": "MechanicalKeyboards"}),
            "r/MechanicalKeyboards",
        )

    def test_reddit_no_sub(self):
        self.assertEqual(gen.source_label({"source": "reddit"}), "Reddit")

    def test_hn(self):
        self.assertEqual(gen.source_label({"source": "hn"}), "Hacker News")

    def test_email_uses_via(self):
        self.assertEqual(
            gen.source_label({"source": "email", "via": "Cannonkeys"}),
            "✉ Cannonkeys",
        )

    def test_unknown_source_no_via(self):
        # Defensive: never silently mislabel as Reddit.
        self.assertEqual(
            gen.source_label({"source": "shopify"}), "Shopify",
        )

    def test_geekhack_never_renders_as_reddit(self):
        # This is the bug the user actually reported.
        label = gen.source_label({"source": "geekhack",
                                  "via": "Geekhack · Group Buys"})
        self.assertNotEqual(label, "Reddit")
        self.assertNotIn("r/", label)


class IsGb(unittest.TestCase):
    def test_geekhack(self):
        self.assertTrue(gen.is_gb({"source": "geekhack"}))

    def test_shopify(self):
        self.assertTrue(gen.is_gb({"source": "shopify"}))

    def test_news_sources(self):
        for s in ("hn", "reddit", "email", "kbdnews"):
            self.assertFalse(gen.is_gb({"source": s}), f"{s} should be news")

    def test_missing_source(self):
        self.assertFalse(gen.is_gb({}))
        self.assertFalse(gen.is_gb({"source": None}))
        self.assertFalse(gen.is_gb({"source": ""}))

    def test_constants_consistent(self):
        # Guard against typo drift: if GB_SOURCES changes, callers need to know.
        self.assertEqual(gen.GB_SOURCES, {"geekhack", "shopify"})
        self.assertEqual(gen.GB_TOPIC_SLUG, "group-buys-vendors")


class FilterCorpus(unittest.TestCase):
    def test_news_only_excludes_gb(self):
        news = gen.filter_corpus(CORPUS_FIXTURE, lambda it: not gen.is_gb(it))
        ids = {it["id"] for d in news["days"] for it in d["items"]}
        self.assertEqual(ids, {"hn-1", "reddit-1", "kbdnews-7"})

    def test_gb_only_keeps_gb(self):
        gb = gen.filter_corpus(CORPUS_FIXTURE, gen.is_gb)
        ids = {it["id"] for d in gb["days"] for it in d["items"]}
        self.assertEqual(ids, {"geekhack-100", "geekhack-101",
                               "shopify-novelkeys-99"})

    def test_partition_is_lossless(self):
        # news ∪ gb == original (no item dropped or duplicated)
        news = gen.filter_corpus(CORPUS_FIXTURE, lambda it: not gen.is_gb(it))
        gb = gen.filter_corpus(CORPUS_FIXTURE, gen.is_gb)
        partitioned = {it["id"] for d in news["days"] for it in d["items"]}
        partitioned |= {it["id"] for d in gb["days"] for it in d["items"]}
        original = {it["id"] for d in CORPUS_FIXTURE["days"]
                    for it in d["items"]}
        self.assertEqual(partitioned, original)

    def test_day_order_preserved(self):
        news = gen.filter_corpus(CORPUS_FIXTURE, lambda it: not gen.is_gb(it))
        self.assertEqual([d["date"] for d in news["days"]],
                         ["2026-05-12", "2026-05-11", "2026-05-10"])

    def test_empty_days_preserved(self):
        news = gen.filter_corpus(CORPUS_FIXTURE, lambda it: not gen.is_gb(it))
        last = next(d for d in news["days"] if d["date"] == "2026-05-10")
        self.assertEqual(last["items"], [])

    def test_title_tagline_carried(self):
        news = gen.filter_corpus(CORPUS_FIXTURE, lambda it: not gen.is_gb(it))
        self.assertEqual(news["title"], CORPUS_FIXTURE["title"])
        self.assertEqual(news["tagline"], CORPUS_FIXTURE["tagline"])

    def test_assert_bad_predicate(self):
        with self.assertRaises(AssertionError):
            gen.filter_corpus(CORPUS_FIXTURE, "not callable")  # type: ignore[arg-type]

    def test_assert_bad_corpus(self):
        with self.assertRaises(AssertionError):
            gen.filter_corpus("not a dict", lambda _it: True)  # type: ignore[arg-type]
        with self.assertRaises(AssertionError):
            gen.filter_corpus({}, lambda _it: True)

    def test_predicate_returning_truthy(self):
        # Predicate may return non-bool truthy/falsy.
        result = gen.filter_corpus(CORPUS_FIXTURE, lambda it: it.get("id"))
        # Every item has an id, so nothing filtered.
        ids = {it["id"] for d in result["days"] for it in d["items"]}
        self.assertEqual(len(ids), 6)


if __name__ == "__main__":
    unittest.main()

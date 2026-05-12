"""Unit tests for scripts/cluster.py.

Run from repo root: python3 -m unittest tests.test_cluster
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from cluster import (  # noqa: E402
    canonical_url, cluster_items, source_kind, source_label,
)


# ────────────── canonical_url ──────────────


class CanonicalUrl(unittest.TestCase):
    def test_strips_utm_tracking(self):
        a = canonical_url("https://example.com/post?utm_source=tw&utm_medium=x")
        b = canonical_url("https://example.com/post")
        self.assertEqual(a, b)

    def test_strips_assorted_tracking(self):
        for tracker in ("fbclid", "gclid", "mc_cid", "mc_eid",
                        "ref", "ref_src", "_hsenc"):
            a = canonical_url(f"https://example.com/p?{tracker}=zzz")
            b = canonical_url("https://example.com/p")
            self.assertEqual(a, b, f"{tracker} not stripped")

    def test_preserves_meaningful_query_params(self):
        # Tracking-strip should NOT eat regular query like `?id=42`.
        out = canonical_url("https://example.com/p?id=42&utm_source=x")
        self.assertIn("id=42", out)
        self.assertNotIn("utm_source", out)

    def test_lowercases_host_and_scheme(self):
        a = canonical_url("HTTPS://EXAMPLE.COM/Foo")
        self.assertTrue(a.startswith("https://example.com/"))

    def test_strips_trailing_slash_except_root(self):
        self.assertEqual(
            canonical_url("https://example.com/foo/"),
            canonical_url("https://example.com/foo"),
        )
        # Root path stays "/"
        self.assertTrue(canonical_url("https://example.com/").endswith("/"))

    def test_drops_fragment(self):
        a = canonical_url("https://example.com/post#section-2")
        self.assertNotIn("#", a)

    def test_drops_default_ports(self):
        # 80 and 443 are implied by scheme; drop.
        out = canonical_url("https://example.com:443/x")
        self.assertNotIn(":443", out)
        out80 = canonical_url("http://example.com:80/x")
        self.assertNotIn(":80", out80)

    def test_preserves_nonstandard_port(self):
        out = canonical_url("https://example.com:8443/x")
        self.assertIn(":8443", out)

    def test_query_params_sorted(self):
        a = canonical_url("https://example.com/p?b=2&a=1")
        b = canonical_url("https://example.com/p?a=1&b=2")
        self.assertEqual(a, b)

    def test_empty_input(self):
        self.assertEqual(canonical_url(""), "")
        self.assertEqual(canonical_url(None), "")  # type: ignore[arg-type]


# ────────────── source_kind / source_label ──────────────


class SourceKind(unittest.TestCase):
    def test_email(self):
        self.assertEqual(source_kind({"source": "email"}), "email")

    def test_reddit(self):
        self.assertEqual(source_kind({"source": "reddit"}), "reddit")

    def test_hn_aliases(self):
        for s in ("hn", "hackernews", "hacker-news", "hacker news"):
            self.assertEqual(source_kind({"source": s}), "hn")

    def test_other_source_returned_as_is(self):
        self.assertEqual(source_kind({"source": "kbdnews"}), "kbdnews")

    def test_missing_source_returns_other(self):
        self.assertEqual(source_kind({}), "other")


class SourceLabel(unittest.TestCase):
    def test_email_uses_via(self):
        self.assertEqual(
            source_label({"source": "email", "via": "NovelKeys"}),
            "NovelKeys",
        )

    def test_email_no_via_fallback(self):
        self.assertEqual(source_label({"source": "email"}), "email")

    def test_reddit_with_sub(self):
        self.assertEqual(
            source_label({"source": "reddit", "subreddit": "MechanicalKeyboards"}),
            "r/MechanicalKeyboards",
        )

    def test_reddit_no_sub(self):
        self.assertEqual(source_label({"source": "reddit"}), "Reddit")

    def test_hn(self):
        self.assertEqual(source_label({"source": "hn"}), "Hacker News")


# ────────────── cluster_items: primary-source priority ──────────────


def _item(source, item_id, url, **extra):
    base = {
        "id": item_id, "source": source, "url": url,
        "title": f"item {item_id}", "discussion_url": url,
    }
    base.update(extra)
    return base


class ClusterItems(unittest.TestCase):
    def test_keeps_distinct_urls_separate(self):
        items = [
            _item("hn", "hn-1", "https://a/x"),
            _item("reddit", "r-1", "https://b/y"),
        ]
        out = cluster_items(items)
        self.assertEqual(len(out), 2)

    def test_merges_same_canonical_url(self):
        # Same URL via two sources → one cluster.
        items = [
            _item("hn", "hn-1", "https://a/x?utm_source=tw", score=120),
            _item("reddit", "r-1", "https://a/x", score=80, subreddit="mk"),
        ]
        out = cluster_items(items)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["sources"]), 2)

    def test_email_wins_primary_over_higher_score(self):
        items = [
            _item("hn", "hn-1", "https://a/x", score=999),
            _item("email", "em-1", "https://a/x", score=0, via="NovelKeys"),
        ]
        out = cluster_items(items)
        # Primary = email; id reflects that.
        self.assertEqual(out[0]["id"], "em-1")
        # sources list: email first.
        self.assertEqual(out[0]["sources"][0]["kind"], "email")
        self.assertEqual(out[0]["sources"][1]["kind"], "hn")

    def test_score_breaks_tie_within_kind(self):
        items = [
            _item("hn", "hn-low", "https://a/x", score=10),
            _item("hn", "hn-high", "https://a/x", score=500),
        ]
        out = cluster_items(items)
        # Highest score wins primary.
        self.assertEqual(out[0]["id"], "hn-high")

    def test_input_order_breaks_ties_when_no_score(self):
        items = [
            _item("hn", "hn-A", "https://a/x"),
            _item("hn", "hn-B", "https://a/x"),
        ]
        out = cluster_items(items)
        self.assertEqual(out[0]["id"], "hn-A")

    def test_aggregates_scores(self):
        items = [
            _item("hn", "hn-1", "https://a/x", score=100, comments=20),
            _item("reddit", "r-1", "https://a/x", score=50, comments=8),
        ]
        out = cluster_items(items)
        self.assertEqual(out[0]["score"], 150)
        self.assertEqual(out[0]["comments"], 28)

    def test_aggregates_none_when_no_scores(self):
        items = [
            _item("hn", "hn-1", "https://a/x"),
            _item("reddit", "r-1", "https://a/x"),
        ]
        out = cluster_items(items)
        self.assertIsNone(out[0]["score"])
        self.assertIsNone(out[0]["comments"])

    def test_union_of_topics_preserving_order(self):
        items = [
            _item("email", "em-1", "https://a/x",
                  topics=["firmware", "remapping-layouts"], via="NK"),
            _item("hn",    "hn-1", "https://a/x",
                  topics=["firmware", "tools-software"]),
        ]
        out = cluster_items(items)
        # Primary (email) first, then unique adds from hn.
        self.assertEqual(out[0]["topics"],
                         ["firmware", "remapping-layouts", "tools-software"])

    def test_keyless_items_kept_separate(self):
        # Items without a usable url get their own cluster each.
        items = [
            _item("hn", "hn-1", ""),
            _item("hn", "hn-2", ""),
        ]
        out = cluster_items(items)
        self.assertEqual(len(out), 2)

    def test_preserves_input_order(self):
        items = [
            _item("hn", "z", "https://z/1"),
            _item("hn", "a", "https://a/1"),
            _item("hn", "m", "https://m/1"),
        ]
        out = cluster_items(items)
        self.assertEqual([o["id"] for o in out], ["z", "a", "m"])

    def test_sources_list_has_expected_fields(self):
        items = [
            _item("hn", "hn-1", "https://a/x", score=100, comments=10),
        ]
        out = cluster_items(items)
        src = out[0]["sources"][0]
        for k in ("id", "label", "kind", "discussion_url",
                  "score", "comments"):
            self.assertIn(k, src)
        self.assertEqual(src["kind"], "hn")
        self.assertEqual(src["score"], 100)

    def test_tracking_param_diff_collapses_into_one_cluster(self):
        # Real-world: same article, different utm tags across sources.
        items = [
            _item("hn", "hn-1", "https://a.com/p?utm_source=tw"),
            _item("reddit", "r-1", "https://a.com/p?utm_source=rd"),
            _item("email", "em-1", "https://a.com/p", via="NK"),
        ]
        out = cluster_items(items)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["sources"]), 3)


if __name__ == "__main__":
    unittest.main()

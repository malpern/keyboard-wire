"""Unit tests for the GB/IC quarantine filter in scripts/post_twitter.py.

The rest of post_twitter.py (OAuth, HTTP, state-file IO) is intentionally
left untested here — see docs/TEST_COVERAGE.md.

Run from repo root: python3 -m unittest tests.test_post_twitter_filter
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import post_twitter as pt  # noqa: E402


class IsPostable(unittest.TestCase):
    def test_news_sources_postable(self):
        for s in ("hn", "reddit", "email", "kbdnews"):
            self.assertTrue(pt.is_postable({"source": s}),
                            f"{s} should be postable")

    def test_gb_sources_blocked(self):
        self.assertFalse(pt.is_postable({"source": "geekhack"}))
        self.assertFalse(pt.is_postable({"source": "shopify"}))

    def test_missing_source_postable(self):
        # An item with no source field is unusual but should not be silently
        # dropped from X — fall back to postable so the existing behavior
        # (post anyway) is preserved for non-GB regressions.
        self.assertTrue(pt.is_postable({}))
        self.assertTrue(pt.is_postable({"source": None}))
        self.assertTrue(pt.is_postable({"source": ""}))

    def test_quarantine_set_matches_generate(self):
        # Guard against drift: if generate.GB_SOURCES grows, this set must
        # grow with it. Otherwise new GB-class sources could tweet.
        sys.path.insert(0, str(ROOT / "scripts"))
        import generate as gen
        self.assertEqual(set(pt.NEVER_POST_SOURCES), gen.GB_SOURCES,
                         "post_twitter.NEVER_POST_SOURCES must equal "
                         "generate.GB_SOURCES")


if __name__ == "__main__":
    unittest.main()

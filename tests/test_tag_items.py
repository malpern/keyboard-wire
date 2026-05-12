"""Unit tests for the pure helper in scripts/tag_items.py.

Run from repo root: python3 -m unittest tests.test_tag_items
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from tag_items import merge_topics  # noqa: E402


VALID = {"firmware", "group-buys-vendors", "community", "builds-hardware"}


class MergeTopics(unittest.TestCase):
    def test_seeded_only(self):
        self.assertEqual(
            merge_topics(["group-buys-vendors"], [], VALID),
            ["group-buys-vendors"],
        )

    def test_parsed_only(self):
        self.assertEqual(
            merge_topics([], ["firmware"], VALID),
            ["firmware"],
        )

    def test_seeded_first_then_parsed(self):
        # Seeded topics keep their input order; parsed topics appended.
        result = merge_topics(
            ["group-buys-vendors"], ["firmware", "community"], VALID,
        )
        self.assertEqual(result, ["group-buys-vendors", "firmware", "community"])

    def test_parsed_dedups_against_seeded(self):
        result = merge_topics(
            ["group-buys-vendors"], ["group-buys-vendors", "firmware"], VALID,
        )
        self.assertEqual(result, ["group-buys-vendors", "firmware"])

    def test_parsed_capped_at_two(self):
        # Even if the LLM returns 5 topics, only first 2 are honored.
        result = merge_topics(
            [], ["firmware", "community", "builds-hardware"], VALID,
        )
        self.assertEqual(result, ["firmware", "community"])

    def test_seeded_not_capped(self):
        # All seeded topics survive (ingestor knows what it's doing).
        result = merge_topics(
            ["firmware", "community", "builds-hardware"], [], VALID,
        )
        self.assertEqual(result, ["firmware", "community", "builds-hardware"])

    def test_invalid_slugs_dropped(self):
        result = merge_topics(
            ["nonexistent-topic"], ["also-bogus", "firmware"], VALID,
        )
        self.assertEqual(result, ["firmware"])

    def test_fallback_when_all_empty(self):
        self.assertEqual(merge_topics([], [], VALID), ["community"])
        self.assertEqual(merge_topics(None, None, VALID), ["community"])

    def test_fallback_when_only_invalid(self):
        self.assertEqual(
            merge_topics(["nope"], ["also-nope"], VALID),
            ["community"],
        )

    def test_custom_fallback(self):
        self.assertEqual(
            merge_topics([], [], VALID, fallback="firmware"),
            ["firmware"],
        )

    def test_slug_normalization(self):
        # Input is run through slugify, so "Group Buys & Vendors" → slug.
        # Only matters if valid_topics contains the slugified form.
        result = merge_topics(["Group-Buys-Vendors"], [], VALID)
        self.assertEqual(result, ["group-buys-vendors"])

    def test_assert_valid_topics_must_be_set(self):
        with self.assertRaises(AssertionError):
            merge_topics([], [], list(VALID))  # type: ignore[arg-type]

    def test_order_within_seeded_preserved(self):
        result = merge_topics(
            ["firmware", "community"], [], VALID,
        )
        self.assertEqual(result, ["firmware", "community"])

    def test_seeded_internal_dedup(self):
        result = merge_topics(
            ["firmware", "firmware", "community"], [], VALID,
        )
        self.assertEqual(result, ["firmware", "community"])


if __name__ == "__main__":
    unittest.main()

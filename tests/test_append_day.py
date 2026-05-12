"""Unit tests for scripts/append_day.py — merge() + write_day_file().

Run from repo root: python3 -m unittest tests.test_append_day

The git push side of append_day.main() is not exercised here; only the
pure-data merge logic + the per-day JSON file persistence.
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import append_day  # noqa: E402


# ────────────── merge() ──────────────


class MergeNewDay(unittest.TestCase):
    def test_creates_day_when_absent(self):
        corpus = {"days": []}
        added, total = append_day.merge(
            corpus, "2026-05-12",
            [{"id": "hn-1", "title": "X"}],
        )
        self.assertEqual(added, 1)
        self.assertEqual(total, 1)
        self.assertEqual(len(corpus["days"]), 1)
        self.assertEqual(corpus["days"][0]["date"], "2026-05-12")
        self.assertEqual(corpus["days"][0]["items"][0]["id"], "hn-1")

    def test_initializes_days_when_missing(self):
        # Corpus has no `days` key at all — merge should create it.
        corpus = {}
        added, total = append_day.merge(
            corpus, "2026-05-12", [{"id": "hn-1"}],
        )
        self.assertEqual(added, 1)
        self.assertIn("days", corpus)


class MergeExistingDay(unittest.TestCase):
    def test_appends_new_items_to_existing_day(self):
        corpus = {"days": [
            {"date": "2026-05-12", "items": [{"id": "hn-1"}]},
        ]}
        added, total = append_day.merge(
            corpus, "2026-05-12", [{"id": "hn-2"}],
        )
        self.assertEqual(added, 1)
        self.assertEqual(total, 2)
        ids = [i["id"] for i in corpus["days"][0]["items"]]
        self.assertEqual(ids, ["hn-1", "hn-2"])

    def test_dedup_by_id_skips_existing(self):
        corpus = {"days": [
            {"date": "2026-05-12", "items": [{"id": "hn-1", "title": "first"}]},
        ]}
        added, total = append_day.merge(
            corpus, "2026-05-12",
            [{"id": "hn-1", "title": "duplicate"},
             {"id": "hn-2"}],
        )
        self.assertEqual(added, 1)
        self.assertEqual(total, 2)
        # Original item preserved (duplicate's title NOT applied).
        self.assertEqual(corpus["days"][0]["items"][0]["title"], "first")

    def test_dedup_within_same_call(self):
        corpus = {"days": []}
        added, total = append_day.merge(
            corpus, "2026-05-12",
            [{"id": "hn-1"}, {"id": "hn-1", "title": "dup"}, {"id": "hn-2"}],
        )
        self.assertEqual(added, 2)
        self.assertEqual(total, 2)


class MergeIdLessItems(unittest.TestCase):
    def test_items_without_id_always_appended(self):
        # Without an id we can't dedupe; we still append the item.
        corpus = {"days": []}
        added, total = append_day.merge(
            corpus, "2026-05-12",
            [{"title": "no id 1"}, {"title": "no id 2"}],
        )
        self.assertEqual(added, 2)
        self.assertEqual(total, 2)

    def test_idless_does_not_block_dedup_of_id_items(self):
        corpus = {"days": [
            {"date": "2026-05-12", "items": [
                {"title": "anon"},
                {"id": "hn-1"},
            ]},
        ]}
        added, total = append_day.merge(
            corpus, "2026-05-12",
            [{"id": "hn-1"}, {"id": "hn-2"}],
        )
        self.assertEqual(added, 1)
        self.assertEqual(total, 3)


class MergeDayOrdering(unittest.TestCase):
    def test_days_sorted_newest_first(self):
        corpus = {"days": [
            {"date": "2026-05-10", "items": []},
            {"date": "2026-05-12", "items": []},
            {"date": "2026-05-11", "items": []},
        ]}
        append_day.merge(corpus, "2026-05-13", [{"id": "x"}])
        dates = [d["date"] for d in corpus["days"]]
        self.assertEqual(dates,
                         ["2026-05-13", "2026-05-12",
                          "2026-05-11", "2026-05-10"])

    def test_inserts_old_date_at_correct_position(self):
        corpus = {"days": [
            {"date": "2026-05-12", "items": []},
        ]}
        append_day.merge(corpus, "2026-05-01", [{"id": "x"}])
        dates = [d["date"] for d in corpus["days"]]
        self.assertEqual(dates, ["2026-05-12", "2026-05-01"])


# ────────────── write_day_file() ──────────────


class WriteDayFile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp())
        self._orig_days_dir = append_day.DAYS_DIR
        append_day.DAYS_DIR = self.tmpdir

    def tearDown(self):
        append_day.DAYS_DIR = self._orig_days_dir

    def _read(self, date):
        return json.loads((self.tmpdir / f"{date}.json").read_text())

    def test_creates_file_when_absent(self):
        append_day.write_day_file("2026-05-12", [{"id": "hn-1"}])
        data = self._read("2026-05-12")
        self.assertEqual(data["date"], "2026-05-12")
        self.assertEqual(len(data["items"]), 1)

    def test_merges_into_existing_file(self):
        append_day.write_day_file("2026-05-12", [{"id": "hn-1"}])
        append_day.write_day_file("2026-05-12", [{"id": "hn-2"}])
        data = self._read("2026-05-12")
        ids = [i["id"] for i in data["items"]]
        self.assertEqual(ids, ["hn-1", "hn-2"])

    def test_dedup_by_id_on_merge(self):
        append_day.write_day_file("2026-05-12",
                                  [{"id": "hn-1", "title": "first"}])
        append_day.write_day_file("2026-05-12",
                                  [{"id": "hn-1", "title": "dup"},
                                   {"id": "hn-2"}])
        data = self._read("2026-05-12")
        self.assertEqual(len(data["items"]), 2)
        # First-write title preserved.
        self.assertEqual(data["items"][0]["title"], "first")

    def test_corrupt_existing_overwritten(self):
        # If the file exists but is unparseable, overwrite with fresh items.
        p = self.tmpdir / "2026-05-12.json"
        p.write_text("not valid json {[")
        append_day.write_day_file("2026-05-12", [{"id": "hn-1"}])
        data = self._read("2026-05-12")
        self.assertEqual(data["items"][0]["id"], "hn-1")


if __name__ == "__main__":
    unittest.main()

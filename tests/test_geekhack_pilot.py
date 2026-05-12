"""Unit tests for scripts/geekhack_pilot.py.

Run from repo root: python3 -m unittest tests.test_geekhack_pilot
"""
import datetime
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import geekhack_pilot as gp  # noqa: E402


class ThreadIdFromUrl(unittest.TestCase):
    def test_post_link(self):
        url = "https://geekhack.org/index.php?topic=126649.msg3215977#msg3215977"
        self.assertEqual(gp.thread_id_from_url(url), "126649")

    def test_root_link(self):
        self.assertEqual(
            gp.thread_id_from_url("https://geekhack.org/index.php?topic=126649.0"),
            "126649",
        )

    def test_semicolon_separator(self):
        # Geekhack sometimes uses ;topic= in legacy URLs
        self.assertEqual(
            gp.thread_id_from_url("https://geekhack.org/index.php?board=70;topic=99.0"),
            "99",
        )

    def test_no_topic(self):
        self.assertIsNone(gp.thread_id_from_url("https://geekhack.org/index.php"))

    def test_empty(self):
        self.assertIsNone(gp.thread_id_from_url(""))
        self.assertIsNone(gp.thread_id_from_url(None))  # type: ignore[arg-type]

    def test_topic_in_query_string_alone(self):
        # Not a real URL but tests the regex anchor: must be preceded by ?, &, or ;
        self.assertIsNone(gp.thread_id_from_url("topic=42"))


class ThreadRootUrl(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(
            gp.thread_root_url("126649"),
            "https://geekhack.org/index.php?topic=126649.0",
        )

    def test_assert_on_non_numeric(self):
        with self.assertRaises(AssertionError):
            gp.thread_root_url("abc")

    def test_assert_on_empty(self):
        with self.assertRaises(AssertionError):
            gp.thread_root_url("")


class CleanTitle(unittest.TestCase):
    def test_strips_single_re(self):
        self.assertEqual(gp.clean_title("Re: [GB] GMK Gregory 2"), "[GB] GMK Gregory 2")

    def test_strips_re_chain(self):
        self.assertEqual(
            gp.clean_title("Re: Re: Re: [IC] Some Project"),
            "[IC] Some Project",
        )

    def test_case_insensitive_re(self):
        self.assertEqual(gp.clean_title("RE: [GB] Thing"), "[GB] Thing")
        self.assertEqual(gp.clean_title("re: [GB] Thing"), "[GB] Thing")

    def test_no_re_preserved(self):
        self.assertEqual(gp.clean_title("[GB] GMK Gregory 2"), "[GB] GMK Gregory 2")

    def test_re_inside_title_not_stripped(self):
        # "Re:" only stripped at the leading position
        self.assertEqual(gp.clean_title("[GB] Re-issue"), "[GB] Re-issue")

    def test_empty(self):
        self.assertEqual(gp.clean_title(""), "")
        self.assertEqual(gp.clean_title(None), "")  # type: ignore[arg-type]

    def test_whitespace(self):
        self.assertEqual(gp.clean_title("  [GB] Foo  "), "[GB] Foo")


class ParseType(unittest.TestCase):
    def test_gb(self):
        self.assertEqual(gp.parse_type("[GB] Foo"), "GB")

    def test_ic(self):
        self.assertEqual(gp.parse_type("[IC] Foo"), "IC")

    def test_lowercase_normalized(self):
        self.assertEqual(gp.parse_type("[gb] Foo"), "GB")
        self.assertEqual(gp.parse_type("[ic] Foo"), "IC")

    def test_no_bracket(self):
        self.assertIsNone(gp.parse_type("GB Foo"))

    def test_unknown_bracket(self):
        self.assertIsNone(gp.parse_type("[OG] Foo"))

    def test_leading_whitespace(self):
        self.assertEqual(gp.parse_type("   [GB] Foo"), "GB")


class StripHtml(unittest.TestCase):
    def test_basic_tags(self):
        self.assertEqual(gp.strip_html("<p>hi <b>there</b></p>"), "hi there")

    def test_entities(self):
        self.assertEqual(gp.strip_html("It&#39;s &amp; that"), "It's & that")

    def test_collapse_whitespace(self):
        self.assertEqual(gp.strip_html("a   b\n\nc"), "a b c")

    def test_empty(self):
        self.assertEqual(gp.strip_html(""), "")
        self.assertEqual(gp.strip_html(None), "")  # type: ignore[arg-type]


class ParsePubdate(unittest.TestCase):
    def test_rfc2822(self):
        dt = gp.parse_pubdate("Tue, 12 May 2026 07:48:16 GMT")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, datetime.timezone.utc)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 5)

    def test_garbage(self):
        self.assertIsNone(gp.parse_pubdate("nonsense"))
        self.assertIsNone(gp.parse_pubdate(""))


SAMPLE_XML = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<rss version="0.92">
  <channel>
    <title>geekhack</title>
    <item>
      <title>Re: [GB] GMK Gregory 2</title>
      <link>https://geekhack.org/index.php?topic=126649.msg3216115#msg3216115</link>
      <description><![CDATA[reply text]]></description>
      <pubDate>Tue, 12 May 2026 07:48:16 GMT</pubDate>
    </item>
    <item>
      <title>[GB] GMK Gregory 2</title>
      <link>https://geekhack.org/index.php?topic=126649.msg3000000#msg3000000</link>
      <description><![CDATA[op body]]></description>
      <pubDate>Mon, 11 May 2026 07:48:16 GMT</pubDate>
    </item>
    <item>
      <title>Re: [IC] YuRui HE Switch</title>
      <link>https://geekhack.org/index.php?topic=126673.msg3216200#msg3216200</link>
      <description><![CDATA[very cool]]></description>
      <pubDate>Sun, 10 May 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>spam with no topic id</title>
      <link>https://geekhack.org/index.php</link>
      <description><![CDATA[skip]]></description>
      <pubDate>Sun, 10 May 2026 12:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""


class ParseFeed(unittest.TestCase):
    def test_basic_parse(self):
        recs = gp.parse_feed(SAMPLE_XML, board=70)
        # 3 items emitted, 1 skipped (no topic id in url)
        self.assertEqual(len(recs), 3)
        # Re: prefix stripped
        self.assertTrue(all(not r["title"].lower().startswith("re:") for r in recs))
        # thread_id extracted correctly
        ids = {r["thread_id"] for r in recs}
        self.assertEqual(ids, {"126649", "126673"})

    def test_carries_board(self):
        recs = gp.parse_feed(SAMPLE_XML, board=132)
        self.assertTrue(all(r["board"] == 132 for r in recs))


class Collect(unittest.TestCase):
    def test_one_item_per_thread(self):
        # Two posts for thread 126649 → exactly one item out.
        items = gp.collect([(70, SAMPLE_XML)], seen=set())
        ids = [it["id"] for it in items]
        self.assertEqual(sorted(ids), ["geekhack-126649", "geekhack-126673"])
        self.assertEqual(len(ids), len(set(ids)))  # no dupes

    def test_seen_thread_skipped(self):
        items = gp.collect([(70, SAMPLE_XML)], seen={"126649"})
        ids = [it["id"] for it in items]
        self.assertEqual(ids, ["geekhack-126673"])

    def test_all_seen(self):
        items = gp.collect([(70, SAMPLE_XML)], seen={"126649", "126673"})
        self.assertEqual(items, [])

    def test_item_schema_fields(self):
        items = gp.collect([(70, SAMPLE_XML)], seen=set())
        for it in items:
            # Required fields per INGESTORS.md item schema
            for key in ("id", "title", "url", "discussion_url", "source",
                        "via", "category", "takeaway", "topics"):
                self.assertIn(key, it, f"missing {key} in {it['id']}")
            self.assertEqual(it["source"], "geekhack")
            self.assertEqual(it["category"], "breaking")
            self.assertEqual(it["topics"], ["group-buys-vendors"])
            self.assertTrue(it["id"].startswith("geekhack-"))
            self.assertTrue(it["url"].endswith(".0"))  # thread root, not per-post
            self.assertEqual(it["url"], it["discussion_url"])

    def test_type_field_gb_ic(self):
        items = gp.collect([(70, SAMPLE_XML)], seen=set())
        types = {it["id"]: it.get("type") for it in items}
        self.assertEqual(types["geekhack-126649"], "GB")
        self.assertEqual(types["geekhack-126673"], "IC")

    def test_earliest_post_wins(self):
        # Thread 126649 has a reply (May 12) and the OP (May 11). collect()
        # should keep the May-11 record as the canonical title source. The
        # title is the same after Re: stripping, but the rule matters once
        # the OP and replies have diverged titles (status updates).
        items = gp.collect([(70, SAMPLE_XML)], seen=set())
        ggregory = next(it for it in items if it["id"] == "geekhack-126649")
        self.assertEqual(ggregory["title"], "[GB] GMK Gregory 2")

    def test_multi_board(self):
        # Same XML offered as both board 70 and 132: thread_id dedup still
        # collapses to one item per thread (whichever board comes first in
        # the input order wins on tie).
        items = gp.collect([(70, SAMPLE_XML), (132, SAMPLE_XML)], seen=set())
        ids = sorted(it["id"] for it in items)
        self.assertEqual(ids, ["geekhack-126649", "geekhack-126673"])


class ToItemAsserts(unittest.TestCase):
    def test_assert_bad_thread_id(self):
        with self.assertRaises(AssertionError):
            gp.to_item({"thread_id": "abc", "title": "[GB] x", "raw_url": "",
                        "takeaway": "", "pubdate": None, "board": 70})

    def test_assert_bad_board(self):
        with self.assertRaises(AssertionError):
            gp.to_item({"thread_id": "1", "title": "[GB] x", "raw_url": "",
                        "takeaway": "", "pubdate": None, "board": 999})


if __name__ == "__main__":
    unittest.main()

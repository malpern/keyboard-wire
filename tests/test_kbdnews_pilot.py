"""Unit tests for scripts/kbdnews_pilot.py.

Run from repo root: python3 -m unittest tests.test_kbdnews_pilot
"""
import datetime
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import kbdnews_pilot as kp  # noqa: E402


# ────────────── post_id_from_url ──────────────


class PostIdFromUrl(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(
            kp.post_id_from_url("https://kbd.news/Levels54-2851.html"),
            "2851",
        )

    def test_htm_suffix_accepted(self):
        self.assertEqual(
            kp.post_id_from_url("https://kbd.news/Levels54-2851.htm"),
            "2851",
        )

    def test_multi_word_slug(self):
        self.assertEqual(
            kp.post_id_from_url("https://kbd.news/some-long-title-9999.html"),
            "9999",
        )

    def test_no_trailing_id(self):
        self.assertIsNone(
            kp.post_id_from_url("https://kbd.news/something/"),
        )

    def test_empty(self):
        self.assertIsNone(kp.post_id_from_url(""))
        self.assertIsNone(kp.post_id_from_url(None))


# ────────────── parse_pubdate ──────────────


class ParsePubdate(unittest.TestCase):
    def test_rfc2822(self):
        dt = kp.parse_pubdate("Tue, 12 May 2026 07:48:16 GMT")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, datetime.timezone.utc)

    def test_garbage(self):
        self.assertIsNone(kp.parse_pubdate("not a date"))
        self.assertIsNone(kp.parse_pubdate(""))


# ────────────── strip_html ──────────────


class StripHtml(unittest.TestCase):
    def test_basic_tags(self):
        self.assertEqual(kp.strip_html("<p>hello <b>world</b></p>"),
                         "hello world")

    def test_drops_leading_img(self):
        # kbd.news RSS descriptions start with an <img> tag that we
        # already capture via <enclosure> — should be stripped.
        s = '<img src="https://kbd.news/x.jpg"/><p>caption</p>'
        self.assertEqual(kp.strip_html(s), "caption")

    def test_only_leading_img_dropped(self):
        # An <img> mid-body should NOT be dropped (kept as removed
        # tag — text around survives).
        s = '<p>before <img src="x"/> after</p>'
        self.assertEqual(kp.strip_html(s), "before after")

    def test_entity_decode(self):
        self.assertEqual(kp.strip_html("Tom&#39;s &amp; Jerry"),
                         "Tom's & Jerry")

    def test_collapse_whitespace(self):
        self.assertEqual(kp.strip_html("a   b\n\nc"), "a b c")

    def test_empty(self):
        self.assertEqual(kp.strip_html(""), "")
        self.assertEqual(kp.strip_html(None), "")


# ────────────── parse_feed ──────────────


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>kbd.news</title>
    <item>
      <title>Levels54 is now fully open source</title>
      <link>https://kbd.news/Levels54-2851.html</link>
      <description><![CDATA[<img src="https://kbd.news/img/2851.jpg"/>A split keyboard project goes open-source today.]]></description>
      <pubDate>Tue, 12 May 2026 09:00:00 GMT</pubDate>
      <enclosure url="https://kbd.news/img/2851.jpg" type="image/jpeg"/>
    </item>
    <item>
      <title>Behind the scenes — weekly meta</title>
      <link>https://kbd.news/behind-the-scenes-2850.html</link>
      <description>weekly summary</description>
      <pubDate>Tue, 12 May 2026 08:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Old news from earlier this month</title>
      <link>https://kbd.news/old-post-2800.html</link>
      <description>not in the 24h window</description>
      <pubDate>Sat, 01 May 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Post without a usable id</title>
      <link>https://kbd.news/no-id/</link>
      <description>skip</description>
      <pubDate>Tue, 12 May 2026 08:30:00 GMT</pubDate>
    </item>
    <item>
      <title>Empty link</title>
      <link></link>
      <description>skip</description>
      <pubDate>Tue, 12 May 2026 08:31:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""

SINCE = datetime.datetime(2026, 5, 11, 12, 0, 0,
                          tzinfo=datetime.timezone.utc)


class ParseFeed(unittest.TestCase):
    def test_emits_one_item(self):
        items = kp.parse_feed(SAMPLE_RSS, SINCE)
        # Only 1 should pass all filters: in-window, has id, not "Behind the scenes",
        # has title+link.
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "kbdnews-2851")

    def test_item_shape_matches_pipeline_schema(self):
        item = kp.parse_feed(SAMPLE_RSS, SINCE)[0]
        for key in ("id", "title", "url", "discussion_url", "source",
                    "via", "category", "takeaway"):
            self.assertIn(key, item, f"missing {key}")
        self.assertEqual(item["source"], "kbdnews")
        self.assertEqual(item["category"], "breaking")
        self.assertEqual(item["via"], "KBD.news")

    def test_takeaway_strips_leading_img(self):
        item = kp.parse_feed(SAMPLE_RSS, SINCE)[0]
        self.assertNotIn("<img", item["takeaway"])
        self.assertIn("A split keyboard project", item["takeaway"])

    def test_skips_behind_the_scenes(self):
        items = kp.parse_feed(SAMPLE_RSS, SINCE)
        ids = [it["id"] for it in items]
        self.assertNotIn("kbdnews-2850", ids)

    def test_skips_out_of_window(self):
        items = kp.parse_feed(SAMPLE_RSS, SINCE)
        ids = [it["id"] for it in items]
        self.assertNotIn("kbdnews-2800", ids)

    def test_skips_missing_id_or_link(self):
        items = kp.parse_feed(SAMPLE_RSS, SINCE)
        # No item with id derived from "no-id/" should survive,
        # and the empty-link entry should also be dropped.
        ids = [it["id"] for it in items]
        self.assertTrue(all(i.startswith("kbdnews-") for i in ids))

    def test_includes_image_hint_from_enclosure(self):
        item = kp.parse_feed(SAMPLE_RSS, SINCE)[0]
        self.assertEqual(item.get("image_hint"),
                         "https://kbd.news/img/2851.jpg")

    def test_no_image_hint_when_no_enclosure(self):
        # Build a fixture without enclosure.
        xml = SAMPLE_RSS.replace(
            '<enclosure url="https://kbd.news/img/2851.jpg" type="image/jpeg"/>',
            "",
        )
        item = kp.parse_feed(xml, SINCE)[0]
        self.assertNotIn("image_hint", item)


if __name__ == "__main__":
    unittest.main()

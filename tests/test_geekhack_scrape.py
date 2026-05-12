"""Unit tests for Geekhack thread-page scraping (Step 1b).

Run from repo root: python3 -m unittest tests.test_geekhack_scrape
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import geekhack_pilot as gp  # noqa: E402
import fetch_images as fi  # noqa: E402


# ── parse_thread_html: pure parser, monkeypatch-free ─────────────


SAMPLE_PAGE = """
<html><head><title>Topic: [GB] GMK Sample (Read 9,708 times)</title></head>
<body>
<div class="post">
  <p>GMK Sample by Designer X<br/>
     A keycap project.<br/>
     <img src="https://geekhack.org/Themes/Nostalgia/images/banner.png">
     <img src="https://cdn.geekhack.org/Smileys/solosmileys/thumbsup.gif">
     <img class="avatar" src="https://geekhack.org/index.php?action=dlattach;attach=1;type=avatar">
     <img src="https://i.postimg.cc/AAA/hero.png">
     <img src="https://i.postimg.cc/BBB/base.png">
     <img src="https://i.postimg.cc/AAA/hero.png"> <!-- dup -->
     <img src="https://i.imgur.com/CCC.jpg">
  </p>
  <div class="quoteheader"><a>Quote from: someone</a></div>
  <blockquote>This is a quoted message that should be excluded.</blockquote>
  <p>End of OP description.</p>
</div>
<div class="moderatorbar">…</div>

<div class="post">
  <p>Reply #1 — first reply, ignore for OP body</p>
  <img src="https://i.postimg.cc/REPLY/image.png">
</div>
<div class="post">
  <p>Reply #2</p>
</div>
<div class="post">
  <p>Reply #11 — highest reply number</p>
</div>

</body></html>
"""


class ParseThreadHtml(unittest.TestCase):
    def setUp(self):
        self.meta = gp.parse_thread_html(SAMPLE_PAGE)

    def test_views_parsed_with_comma(self):
        self.assertEqual(self.meta["views"], 9708)

    def test_views_missing(self):
        # If the (Read N times) marker is absent, views = None.
        empty = gp.parse_thread_html("<html></html>")
        self.assertIsNone(empty["views"])

    def test_replies_highest_seen(self):
        self.assertEqual(self.meta["replies"], 11)

    def test_replies_missing(self):
        empty = gp.parse_thread_html("<html></html>")
        self.assertIsNone(empty["replies"])

    def test_images_from_op_only(self):
        urls = self.meta["images"]
        # OP-area images only — reply-area images excluded.
        self.assertIn("https://i.postimg.cc/AAA/hero.png", urls)
        self.assertIn("https://i.postimg.cc/BBB/base.png", urls)
        self.assertIn("https://i.imgur.com/CCC.jpg", urls)
        self.assertNotIn("https://i.postimg.cc/REPLY/image.png", urls)

    def test_images_dedup_preserves_order(self):
        urls = self.meta["images"]
        self.assertEqual(urls[0], "https://i.postimg.cc/AAA/hero.png")
        # Duplicate of [0] should not reappear.
        self.assertEqual(urls.count("https://i.postimg.cc/AAA/hero.png"), 1)

    def test_images_filter_chrome(self):
        # Themes / smileys / avatars / geekhack.org-hosted assets excluded.
        urls = self.meta["images"]
        for u in urls:
            self.assertNotIn("geekhack.org", u)
            self.assertFalse(u.endswith("banner.png"))
            self.assertFalse(u.endswith("thumbsup.gif"))

    def test_op_body_text(self):
        body = self.meta["op_body"]
        self.assertIsNotNone(body)
        self.assertIn("GMK Sample by Designer X", body)
        self.assertIn("End of OP description.", body)

    def test_op_body_strips_quoted_message(self):
        body = self.meta["op_body"]
        self.assertNotIn(
            "This is a quoted message that should be excluded.", body,
        )

    def test_op_body_collapses_whitespace(self):
        body = self.meta["op_body"]
        self.assertNotIn("\n", body)
        self.assertNotIn("  ", body)

    def test_op_body_preserves_em_dash_when_decoded_correctly(self):
        # Real Geekhack pages declare ISO-8859-1 but serve cp1252; the
        # em-dash byte \x97 is invisible in strict ISO-8859-1. This
        # tests the post-decode invariant: if the caller decoded as
        # cp1252 (as fetch_thread_metadata does), em-dashes survive.
        raw_bytes = (
            b'<div class="post"><p>spent the process refining rather '
            b'than reinventing\x97shaping the design language</p></div>'
            b'<div class="moderatorbar">x</div>'
        )
        text = raw_bytes.decode("cp1252")
        meta = gp.parse_thread_html(text)
        self.assertIn("reinventing—shaping", meta["op_body"])

    def test_vendor_links_extracted(self):
        page = b"""<html><body>
        <div class="post"><p>
          Vendors:
          <a href="https://novelkeys.com/products/gmk-greg-2">NovelKeys</a>
          <a href="https://prototypist.net/products/gmk-greg-2">Proto[Typist]</a>
          <a href="https://kbdfans.com/products/gmk-greg-2">KBDfans</a>
          Render credits to
          <a href="https://imgur.com/album/abc">photo album</a>
          and join the
          <a href="https://discord.gg/somewhere">Discord</a>.
          Forum form at
          <a href="https://forms.gle/aaa">interest form</a>.
        </p></div>
        <div class="moderatorbar">x</div>
        </body></html>"""
        meta = gp.parse_thread_html(page.decode("cp1252"))
        links = meta["vendor_links"]
        urls = [v["url"] for v in links]
        # Vendor URLs included.
        self.assertIn("https://novelkeys.com/products/gmk-greg-2", urls)
        self.assertIn("https://prototypist.net/products/gmk-greg-2", urls)
        self.assertIn("https://kbdfans.com/products/gmk-greg-2", urls)
        # Media / admin URLs excluded.
        for bad in ("imgur.com", "discord.gg", "forms.gle"):
            for u in urls:
                self.assertNotIn(bad, u)

    def test_vendor_links_preserve_label_and_host(self):
        page = b"""<html><body>
        <div class="post"><p>
          <a href="https://novelkeys.com/products/x">NovelKeys</a>
        </p></div><div class="moderatorbar">x</div></body></html>"""
        meta = gp.parse_thread_html(page.decode("cp1252"))
        self.assertEqual(len(meta["vendor_links"]), 1)
        vl = meta["vendor_links"][0]
        self.assertEqual(vl["vendor"], "NovelKeys")
        self.assertEqual(vl["url"], "https://novelkeys.com/products/x")
        self.assertEqual(vl["host"], "novelkeys.com")

    def test_vendor_links_dedup_same_host_and_url(self):
        page = b"""<html><body>
        <div class="post"><p>
          <a href="https://x.com/p/a">First</a>
          <a href="https://x.com/p/a">Second copy of same link</a>
        </p></div><div class="moderatorbar">x</div></body></html>"""
        # x.com is in the blocklist — but use a real vendor host instead.
        page = page.replace(b"x.com", b"novelkeys.com")
        meta = gp.parse_thread_html(page.decode("cp1252"))
        self.assertEqual(len(meta["vendor_links"]), 1)

    def test_vendor_links_cap_at_12(self):
        links_html = "".join(
            f'<a href="https://vendor{i}.example/p">V{i}</a>'
            for i in range(20)
        )
        page = ("<html><body><div class=\"post\"><p>" + links_html
                + "</p></div><div class=\"moderatorbar\">x</div>"
                + "</body></html>").encode()
        meta = gp.parse_thread_html(page.decode("cp1252"))
        self.assertEqual(len(meta["vendor_links"]), 12)

    def test_related_threads_extracted(self):
        page = b"""<html><body>
        <div class="post"><p>
          GMK Sample 2 by X. Greetings,
          <a href="https://geekhack.org/index.php?topic=99999.0">the original</a>
          has returned. See also our
          <a href="https://geekhack.org/index.php?topic=88888.0">IC thread</a>.
          And buy at <a href="https://novelkeys.com/p/x">NovelKeys</a>.
        </p></div>
        <div class="moderatorbar">x</div>
        </body></html>"""
        meta = gp.parse_thread_html(page.decode("cp1252"))
        topic_ids = sorted(r["topic_id"] for r in meta["related_threads"])
        self.assertEqual(topic_ids, ["88888", "99999"])

    def test_related_threads_dedup_by_topic_id(self):
        page = b"""<html><body>
        <div class="post"><p>
          <a href="https://geekhack.org/index.php?topic=42.msg1#msg1">First link</a>
          <a href="https://geekhack.org/index.php?topic=42.0">Same thread again</a>
        </p></div>
        <div class="moderatorbar">x</div>
        </body></html>"""
        meta = gp.parse_thread_html(page.decode("cp1252"))
        self.assertEqual(len(meta["related_threads"]), 1)
        self.assertEqual(meta["related_threads"][0]["topic_id"], "42")

    def test_related_threads_empty_when_no_geekhack_links(self):
        page = (
            '<div class="post"><p><a href="https://imgur.com/x">image</a>'
            '</p></div><div class="moderatorbar">x</div>'
        )
        meta = gp.parse_thread_html(page)
        self.assertEqual(meta["related_threads"], [])

    def test_vendor_links_empty_when_none(self):
        empty = gp.parse_thread_html(
            '<div class="post"><p>no links here</p></div>'
            '<div class="moderatorbar">x</div>'
        )
        self.assertEqual(empty["vendor_links"], [])


class IsVendorHost(unittest.TestCase):
    def test_real_vendors(self):
        self.assertTrue(gp._is_vendor_host("novelkeys.com"))
        self.assertTrue(gp._is_vendor_host("cannonkeys.com"))
        self.assertTrue(gp._is_vendor_host("oblotzky.industries"))
        self.assertTrue(gp._is_vendor_host("prototypist.net"))

    def test_media_hosts_rejected(self):
        for h in ("imgur.com", "i.imgur.com", "postimg.cc",
                  "discord.gg", "forms.gle", "github.com",
                  "youtube.com", "x.com", "twitter.com", "reddit.com",
                  "geekhack.org"):
            self.assertFalse(gp._is_vendor_host(h), f"{h} should be rejected")

    def test_subdomains_of_blocked(self):
        # `cdn.imgur.com` should also be rejected.
        self.assertFalse(gp._is_vendor_host("cdn.imgur.com"))
        self.assertFalse(gp._is_vendor_host("video.youtube.com"))

    def test_empty(self):
        self.assertFalse(gp._is_vendor_host(""))
        self.assertFalse(gp._is_vendor_host(None))


    def test_op_body_missing_when_no_post_div(self):
        empty = gp.parse_thread_html("<html><body>no post block</body></html>")
        self.assertIsNone(empty["op_body"])
        self.assertEqual(empty["images"], [])


class IsOpImage(unittest.TestCase):
    def test_accepts_imgur(self):
        self.assertTrue(gp._is_op_image("https://i.imgur.com/abc.jpg"))

    def test_accepts_postimg(self):
        self.assertTrue(gp._is_op_image("https://i.postimg.cc/x/y.png"))

    def test_accepts_query_string_after_extension(self):
        self.assertTrue(
            gp._is_op_image("https://example.com/x.jpg?cache=1")
        )

    def test_rejects_geekhack_host(self):
        self.assertFalse(
            gp._is_op_image("https://geekhack.org/Themes/banner.png")
        )

    def test_rejects_cdn_geekhack(self):
        self.assertFalse(
            gp._is_op_image("https://cdn.geekhack.org/Smileys/smile.gif")
        )

    def test_accepts_geekhack_dlattach_image(self):
        # Designer uploaded via the forum's own attachment uploader.
        self.assertTrue(gp._is_op_image(
            "https://geekhack.org/index.php?PHPSESSID=x"
            "&action=dlattach;topic=1.0;attach=99;image"
        ))

    def test_rejects_geekhack_dlattach_avatar(self):
        # Same dlattach prefix but ;type=avatar — that's chrome.
        self.assertFalse(gp._is_op_image(
            "https://geekhack.org/index.php?PHPSESSID=x"
            "&action=dlattach;attach=99;type=avatar"
        ))

    def test_rejects_geekhack_non_dlattach_no_extension(self):
        # Geekhack-hosted URL without dlattach is not an OP image.
        self.assertFalse(gp._is_op_image(
            "https://geekhack.org/index.php?action=profile;u=1"
        ))

    def test_rejects_non_image_extension(self):
        self.assertFalse(gp._is_op_image("https://example.com/page.html"))

    def test_rejects_chrome_url_patterns(self):
        # Hosted off-site but chrome by URL pattern (rare but possible).
        self.assertFalse(
            gp._is_op_image("https://example.com/path/sigpic.png")
        )

    def test_rejects_empty(self):
        self.assertFalse(gp._is_op_image(""))
        self.assertFalse(gp._is_op_image(None))


# ── enrich_items: integration with monkeypatched fetch ──────────


class CleanPhpsessid(unittest.TestCase):
    def test_strips_first_param(self):
        self.assertEqual(
            gp._clean_phpsessid(
                "https://geekhack.org/index.php?PHPSESSID=abc&topic=1.0"
            ),
            "https://geekhack.org/index.php?topic=1.0",
        )

    def test_strips_mid_query(self):
        self.assertEqual(
            gp._clean_phpsessid(
                "https://geekhack.org/?a=1&PHPSESSID=abc&topic=1.0"
            ),
            "https://geekhack.org/?a=1&topic=1.0",
        )

    def test_no_change_when_absent(self):
        url = "https://geekhack.org/index.php?topic=1.0"
        self.assertEqual(gp._clean_phpsessid(url), url)


class FetchRelatedThreadLabel(unittest.TestCase):
    """fetch_related_thread_label parses the <title> tag of a related
    Geekhack thread to classify it as GB / IC / other. We monkeypatch
    urllib to avoid live network in tests."""

    class _FakeResp:
        def __init__(self, body):
            self._body = body
        def read(self): return self._body
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): self.close()

    def _patch(self, body_bytes):
        import urllib.request
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **kw: self._FakeResp(body_bytes)

    def _unpatch(self):
        import urllib.request
        urllib.request.urlopen = self._orig

    def test_classifies_gb_title(self):
        self._patch(
            b"<html><head><title>[GB] GMK Gregory (Read 41,000 times)"
            b"</title></head></html>"
        )
        try:
            out = gp.fetch_related_thread_label(
                "https://geekhack.org/index.php?topic=110101.0"
            )
        finally:
            self._unpatch()
        self.assertEqual(out["type"], "GB")
        self.assertEqual(out["title"], "GMK Gregory")

    def test_classifies_ic_title(self):
        self._patch(
            b"<html><head><title>[IC] Some Project</title></head></html>"
        )
        try:
            out = gp.fetch_related_thread_label("https://geekhack.org/?topic=1")
        finally:
            self._unpatch()
        self.assertEqual(out["type"], "IC")
        self.assertEqual(out["title"], "Some Project")

    def test_other_when_no_prefix(self):
        self._patch(
            b"<html><head><title>Random thread</title></head></html>"
        )
        try:
            out = gp.fetch_related_thread_label("https://geekhack.org/?topic=1")
        finally:
            self._unpatch()
        self.assertEqual(out["type"], "")
        self.assertEqual(out["title"], "Random thread")

    def test_returns_none_on_no_title(self):
        self._patch(b"<html><body>no title</body></html>")
        try:
            out = gp.fetch_related_thread_label("https://geekhack.org/?topic=1")
        finally:
            self._unpatch()
        self.assertIsNone(out)

    def test_strips_phpsessid_from_returned_url(self):
        self._patch(
            b"<html><head><title>[GB] Foo</title></head></html>"
        )
        try:
            out = gp.fetch_related_thread_label(
                "https://geekhack.org/index.php?PHPSESSID=abc&topic=1.0"
            )
        finally:
            self._unpatch()
        self.assertNotIn("PHPSESSID", out["url"])


class EnrichItems(unittest.TestCase):
    def test_enrich_populates_fields(self):
        fake_meta = {
            "views": 9708,
            "replies": 11,
            "images": ["https://i.postimg.cc/A/x.png",
                       "https://i.postimg.cc/B/y.png"],
            "op_body": "Real OP description goes here.",
        }
        orig = gp.fetch_thread_metadata
        gp.fetch_thread_metadata = lambda tid: fake_meta
        try:
            items = [{"id": "geekhack-126649", "takeaway": "stale reply"}]
            gp.enrich_items(items, throttle=0)
            it = items[0]
            self.assertEqual(it["score"], 9708)
            self.assertEqual(it["comments"], 11)
            self.assertEqual(it["images_remote"], fake_meta["images"])
            self.assertEqual(it["takeaway"], fake_meta["op_body"])
        finally:
            gp.fetch_thread_metadata = orig

    def test_enrich_tolerates_fetch_failure(self):
        orig = gp.fetch_thread_metadata
        gp.fetch_thread_metadata = lambda tid: None
        try:
            items = [{"id": "geekhack-1", "takeaway": "stale"}]
            gp.enrich_items(items, throttle=0)
            # Item unchanged — partial enrichment beats silent loss.
            self.assertEqual(items[0]["takeaway"], "stale")
            self.assertNotIn("score", items[0])
        finally:
            gp.fetch_thread_metadata = orig

    def test_enrich_truncates_long_op_body(self):
        fake_meta = {
            "views": None, "replies": None, "images": [],
            "op_body": "x" * 1000,
        }
        orig = gp.fetch_thread_metadata
        gp.fetch_thread_metadata = lambda tid: fake_meta
        try:
            items = [{"id": "geekhack-1", "takeaway": ""}]
            gp.enrich_items(items, throttle=0)
            self.assertLessEqual(len(items[0]["takeaway"]), 600)
        finally:
            gp.fetch_thread_metadata = orig

    def test_enrich_skips_partial_fields(self):
        # If meta has only views (no images or body), only views is set.
        fake_meta = {"views": 100, "replies": None,
                     "images": [], "op_body": None}
        orig = gp.fetch_thread_metadata
        gp.fetch_thread_metadata = lambda tid: fake_meta
        try:
            items = [{"id": "geekhack-1", "takeaway": "keep me"}]
            gp.enrich_items(items, throttle=0)
            self.assertEqual(items[0]["score"], 100)
            self.assertNotIn("comments", items[0])
            self.assertNotIn("images_remote", items[0])
            self.assertEqual(items[0]["takeaway"], "keep me")
        finally:
            gp.fetch_thread_metadata = orig


# ── fetch_images.fetch_for: multi-image download path ──────────


class FetchForMultiImage(unittest.TestCase):
    def test_images_remote_downloads_each(self):
        called = []
        orig = fi.download_and_save
        fi.download_and_save = (
            lambda url, dest, **kw: called.append((url, str(dest))) or True
        )
        try:
            item = {"id": "geekhack-99", "images_remote": [
                "https://x/0.png", "https://x/1.png",
            ]}
            result = fi.fetch_for(item)
            self.assertEqual(len(called), 2)
            self.assertEqual(result["images"][0], "img/geekhack-99-0.jpg")
            self.assertEqual(result["images"][1], "img/geekhack-99-1.jpg")
            # Back-compat: item.image = first frame.
            self.assertEqual(result["image"], result["images"][0])
        finally:
            fi.download_and_save = orig

    def test_idempotent_when_already_numbered(self):
        # Existing images already use the <slug>-<N>.jpg naming → skip.
        called = []
        orig = fi.download_and_save
        fi.download_and_save = (
            lambda url, dest, **kw: called.append(url) or True
        )
        try:
            item = {"id": "geekhack-99",
                    "images_remote": ["https://x/0.png", "https://x/1.png"],
                    "images": ["img/geekhack-99-0.jpg",
                               "img/geekhack-99-1.jpg"]}
            result = fi.fetch_for(item)
            self.assertEqual(called, [])  # no new downloads
            self.assertEqual(result["images"],
                             ["img/geekhack-99-0.jpg", "img/geekhack-99-1.jpg"])
        finally:
            fi.download_and_save = orig

    def test_upgrades_when_remote_grew_since_last_pass(self):
        # Prior pass downloaded 1 image into <slug>-0.jpg. Now
        # images_remote has more entries (re-scrape found more). The
        # numbered-naming check alone would skip; the length check
        # forces a refresh.
        called = []
        orig = fi.download_and_save
        fi.download_and_save = (
            lambda url, dest, **kw: called.append(str(dest)) or True
        )
        try:
            item = {
                "id": "geekhack-77",
                "images_remote": ["https://x/0.png", "https://x/1.png",
                                  "https://x/2.png"],
                "images": ["img/geekhack-77-0.jpg"],
            }
            result = fi.fetch_for(item)
            self.assertEqual(len(called), 3)
            self.assertEqual(len(result["images"]), 3)
        finally:
            fi.download_and_save = orig

    def test_upgrades_legacy_single_image_to_carousel(self):
        # A legacy entry from the single-image discovery path used
        # `<slug>.jpg` (no -N suffix). When images_remote is
        # subsequently added, fetch_for must re-download into the
        # numbered set rather than treating the legacy path as final.
        called = []
        orig = fi.download_and_save
        fi.download_and_save = (
            lambda url, dest, **kw: called.append(url) or True
        )
        try:
            item = {"id": "geekhack-99",
                    "images_remote": ["https://x/0.png", "https://x/1.png"],
                    "images": ["img/geekhack-99.jpg"]}  # legacy naming
            result = fi.fetch_for(item)
            self.assertEqual(len(called), 2)  # both downloaded
            self.assertEqual(result["images"],
                             ["img/geekhack-99-0.jpg",
                              "img/geekhack-99-1.jpg"])
            # image points at first frame, not the legacy path.
            self.assertEqual(result["image"], "img/geekhack-99-0.jpg")
        finally:
            fi.download_and_save = orig

    def test_caps_at_max_gb_images(self):
        called = []
        orig = fi.download_and_save
        fi.download_and_save = (
            lambda url, dest, **kw: called.append(url) or True
        )
        try:
            urls = [f"https://x/{i}.png" for i in range(20)]
            item = {"id": "geekhack-1", "images_remote": urls}
            fi.fetch_for(item)
            self.assertEqual(len(called), fi.MAX_GB_IMAGES)
        finally:
            fi.download_and_save = orig

    def test_partial_download_failure_keeps_successes(self):
        # Even indexes succeed, odd fail.
        orig = fi.download_and_save
        fi.download_and_save = lambda url, dest, **kw: ("0.png" in url or "2.png" in url)
        try:
            item = {"id": "geekhack-1", "images_remote": [
                "https://x/0.png", "https://x/1.png", "https://x/2.png",
            ]}
            result = fi.fetch_for(item)
            # Only 0 and 2 made it.
            self.assertEqual(
                result["images"],
                ["img/geekhack-1-0.jpg", "img/geekhack-1-2.jpg"],
            )
        finally:
            fi.download_and_save = orig

    def test_all_downloads_fail_no_images_field(self):
        orig = fi.download_and_save
        fi.download_and_save = lambda url, dest, **kw: False
        try:
            item = {"id": "geekhack-1",
                    "images_remote": ["https://x/0.png"]}
            result = fi.fetch_for(item)
            self.assertNotIn("images", result)
            self.assertNotIn("image", result)
        finally:
            fi.download_and_save = orig


if __name__ == "__main__":
    unittest.main()

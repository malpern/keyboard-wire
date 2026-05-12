"""Unit tests for the GB/IC card renderer + image discovery.

Run from repo root: python3 -m unittest tests.test_gb_card
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate as gen  # noqa: E402
import fetch_images as fi  # noqa: E402


# ─────────────────────────── helpers ───────────────────────────


class CleanRemoteImageUrl(unittest.TestCase):
    def test_strips_geekhack_phpsessid_first_param(self):
        url = ("https://geekhack.org/index.php?PHPSESSID=abc123"
               "&action=dlattach;topic=126649.0;attach=259204;image")
        self.assertEqual(
            gen.clean_remote_image_url(url),
            "https://geekhack.org/index.php?action=dlattach;topic=126649.0;attach=259204;image",
        )

    def test_strips_phpsessid_mid_query(self):
        url = ("https://geekhack.org/index.php?action=x&PHPSESSID=abc"
               "&topic=1")
        self.assertEqual(
            gen.clean_remote_image_url(url),
            "https://geekhack.org/index.php?action=x&topic=1",
        )

    def test_imgur_passthrough(self):
        url = "https://i.imgur.com/hv7OKVd.jpg"
        self.assertEqual(gen.clean_remote_image_url(url), url)

    def test_postimg_passthrough(self):
        url = "https://i.postimg.cc/7L0Xb5s5/GMK-CYL-Greg-Desk-CTKL-Wide.png"
        self.assertEqual(gen.clean_remote_image_url(url), url)

    def test_empty(self):
        self.assertEqual(gen.clean_remote_image_url(""), "")


class GbImages(unittest.TestCase):
    def test_images_array_preferred(self):
        item = {"images": ["a.jpg", "b.jpg"], "image": "old.jpg"}
        self.assertEqual(gen.gb_images(item), ["a.jpg", "b.jpg"])

    def test_falls_back_to_single_image(self):
        item = {"image": "only.jpg"}
        self.assertEqual(gen.gb_images(item), ["only.jpg"])

    def test_empty_when_no_image(self):
        self.assertEqual(gen.gb_images({}), [])

    def test_skips_blank_array_entries(self):
        item = {"images": ["a.jpg", "", None, "b.jpg"]}
        self.assertEqual(gen.gb_images(item), ["a.jpg", "b.jpg"])


class FmtPriceChip(unittest.TestCase):
    def test_range(self):
        self.assertEqual(
            gen.fmt_price_chip({"price_low": 14500, "price_high": 16000}),
            "$145-160",
        )

    def test_only_low(self):
        self.assertEqual(
            gen.fmt_price_chip({"price_low": 14500}), "$145+"
        )

    def test_only_high(self):
        self.assertEqual(
            gen.fmt_price_chip({"price_high": 16000}), "$160+"
        )

    def test_equal_low_high(self):
        self.assertEqual(
            gen.fmt_price_chip({"price_low": 14500, "price_high": 14500}),
            "$145+",
        )

    def test_missing(self):
        self.assertIsNone(gen.fmt_price_chip({}))

    def test_non_usd(self):
        self.assertEqual(
            gen.fmt_price_chip({"price_low": 10000, "currency": "EUR"}),
            "100+",
        )


class FmtDateChip(unittest.TestCase):
    def test_iso_to_human(self):
        self.assertEqual(
            gen.fmt_date_chip("2026-06-14", prefix="ends"),
            "ends Jun 14",
        )

    def test_starts(self):
        self.assertEqual(
            gen.fmt_date_chip("2026-01-03", prefix="starts"),
            "starts Jan 3",
        )

    def test_missing(self):
        self.assertIsNone(gen.fmt_date_chip(None, prefix="ends"))
        self.assertIsNone(gen.fmt_date_chip("", prefix="ends"))

    def test_bad_format(self):
        self.assertIsNone(gen.fmt_date_chip("not-a-date", prefix="ends"))


# ─────────────────────────── render ───────────────────────────


def make_gb_item(**overrides):
    base = {
        "id": "geekhack-1",
        "title": "[GB] GMK Gregory 2",
        "url": "https://geekhack.org/index.php?topic=126649.0",
        "discussion_url": "https://geekhack.org/index.php?topic=126649.0",
        "source": "geekhack",
        "via": "Geekhack · Group Buys",
        "category": "breaking",
        "takeaway": "Nice keycap set",
        "topics": ["group-buys-vendors"],
        "tags": [],
        "type": "GB",
        "image": "img/geekhack-1.jpg",
    }
    base.update(overrides)
    return base


class RenderGbItem(unittest.TestCase):
    def test_dispatches_via_render_item(self):
        # render_item should hand a geekhack item off to render_gb_item.
        out = gen.render_item(make_gb_item(), {}, {})
        self.assertIn("gb-item", out)
        self.assertIn("gb-title", out)

    def test_news_item_does_not_get_gb_card(self):
        news = {"id": "hn-1", "title": "Foo", "url": "https://example/",
                "source": "hn", "category": "breaking", "takeaway": ""}
        out = gen.render_item(news, {}, {})
        self.assertNotIn("gb-item", out)

    def test_title_strips_gb_prefix(self):
        out = gen.render_gb_item(make_gb_item(title="[GB] GMK Gregory 2"), {}, {})
        # The displayed title link should not have "[GB]" inside the <a>
        self.assertIn(">GMK Gregory 2<", out)
        # The chip should be present
        self.assertIn('class="gb-type gb-type-gb"', out)

    def test_ic_type(self):
        out = gen.render_gb_item(
            make_gb_item(type="IC", title="[IC] YuRui HE Switch"), {}, {},
        )
        self.assertIn('class="gb-type gb-type-ic"', out)
        self.assertIn(">YuRui HE Switch<", out)

    def test_single_image_no_chrome(self):
        # One image → gb-carousel-single, no dots, no nav
        out = gen.render_gb_item(make_gb_item(), {}, {})
        self.assertIn("gb-carousel-single", out)
        self.assertNotIn("gb-dot", out)
        self.assertNotIn("gb-nav", out)
        self.assertNotIn("aria-roledescription=\"carousel\"", out)

    def test_multi_image_has_dots_and_nav(self):
        out = gen.render_gb_item(
            make_gb_item(image=None, images=["a.jpg", "b.jpg", "c.jpg"]),
            {}, {},
        )
        self.assertIn('aria-roledescription="carousel"', out)
        self.assertEqual(out.count('class="gb-dot"'), 3)
        self.assertIn("gb-nav-prev", out)
        self.assertIn("gb-nav-next", out)
        # First slide eager, rest lazy
        self.assertEqual(out.count('loading="eager"'), 1)
        self.assertEqual(out.count('loading="lazy"'), 2)

    def test_no_image_no_carousel(self):
        out = gen.render_gb_item(make_gb_item(image=None), {}, {})
        self.assertNotIn("gb-carousel", out)

    def test_gb_metadata_chips(self):
        item = make_gb_item(gb={
            "status": "live", "moq": 200,
            "price_low": 14500, "price_high": 16000,
            "ends_at": "2026-06-14",
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("gb-status-live", out)
        self.assertIn(">live<", out)
        self.assertIn(">MOQ 200<", out)
        self.assertIn(">$145-160<", out)
        self.assertIn(">ends Jun 14<", out)

    def test_facets_line(self):
        item = make_gb_item(gb={"designer": "iNN Studio", "profile": "Cherry"})
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("iNN Studio", out)
        self.assertIn("Cherry", out)
        self.assertIn(" · ", out)

    def test_engagement_views_replies(self):
        item = make_gb_item(score=4231, comments=78)
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("4,231 views", out)
        self.assertIn("78 replies", out)

    def test_buylist_data_attrs_preserved(self):
        out = gen.render_gb_item(make_gb_item(), {}, {})
        for attr in ("data-id=", "data-title=", "data-url=",
                     "data-source=", "data-date="):
            self.assertIn(attr, out)

    def test_cta_label_for_geekhack(self):
        out = gen.render_gb_item(make_gb_item(), {}, {})
        self.assertIn("open on Geekhack", out)

    def test_cta_label_for_other_source(self):
        out = gen.render_gb_item(make_gb_item(source="shopify"), {}, {})
        self.assertIn("→ open<", out)
        self.assertNotIn("open on Geekhack", out)

    def test_rel_prefix_applied_to_image(self):
        out = gen.render_gb_item(
            make_gb_item(image="img/x.jpg"), {}, {}, rel_prefix="../",
        )
        self.assertIn('src="../img/x.jpg"', out)

    def test_ic_gets_subtitle(self):
        out = gen.render_gb_item(make_gb_item(type="IC"), {}, {})
        self.assertIn("gb-ic-subtitle", out)
        self.assertIn("gauging interest", out)

    def test_gb_no_subtitle(self):
        out = gen.render_gb_item(make_gb_item(type="GB"), {}, {})
        self.assertNotIn("gb-ic-subtitle", out)

    def test_ic_no_vendors_shows_empty_state(self):
        out = gen.render_gb_item(make_gb_item(type="IC"), {}, {})
        self.assertIn("No vendors signed yet", out)

    def test_gb_no_vendors_no_empty_state(self):
        # GB without vendors just shows nothing — no empty-state copy.
        out = gen.render_gb_item(make_gb_item(type="GB"), {}, {})
        self.assertNotIn("No vendors signed yet", out)

    def test_ic_with_vendors_shows_pills_not_empty_state(self):
        item = make_gb_item(type="IC", gb={
            "vendor_regions": [{"region": "US", "name": "NovelKeys"}]
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("NovelKeys", out)
        self.assertNotIn("No vendors signed yet", out)

    def test_ic_cta_says_join_the_discussion(self):
        out = gen.render_gb_item(make_gb_item(type="IC"), {}, {})
        self.assertIn("join the discussion", out)
        self.assertNotIn("open on Geekhack", out)

    def test_gb_cta_says_open_on_geekhack(self):
        out = gen.render_gb_item(make_gb_item(type="GB"), {}, {})
        self.assertIn("open on Geekhack", out)
        self.assertNotIn("join the discussion", out)

    def test_ic_class_marker(self):
        out = gen.render_gb_item(make_gb_item(type="IC"), {}, {})
        self.assertIn("gb-item-ic", out)
        out_gb = gen.render_gb_item(make_gb_item(type="GB"), {}, {})
        self.assertNotIn("gb-item-ic", out_gb)

    def test_data_full_attribute_emitted_when_remote_present(self):
        item = make_gb_item(
            image=None,
            images=["img/geekhack-1-0.jpg", "img/geekhack-1-1.jpg"],
            images_remote=[
                "https://i.imgur.com/A.jpg",
                "https://geekhack.org/index.php?PHPSESSID=x&action=dlattach;attach=99;image",
            ],
        )
        out = gen.render_gb_item(item, {}, {})
        self.assertIn('data-full="https://i.imgur.com/A.jpg"', out)
        # PHPSESSID stripped
        self.assertIn('data-full="https://geekhack.org/index.php?action=dlattach;attach=99;image"', out)
        self.assertNotIn("PHPSESSID", out)

    def test_data_full_omitted_when_no_remote(self):
        item = make_gb_item(image="img/x.jpg")
        out = gen.render_gb_item(item, {}, {})
        self.assertNotIn("data-full=", out)

    def test_data_full_skipped_for_missing_index(self):
        # 3 local images, 1 remote — only first slide gets data-full.
        item = make_gb_item(
            image=None,
            images=["img/a.jpg", "img/b.jpg", "img/c.jpg"],
            images_remote=["https://x/0.jpg"],
        )
        out = gen.render_gb_item(item, {}, {})
        self.assertEqual(out.count("data-full="), 1)

    def test_rel_prefix_skips_absolute_urls(self):
        out = gen.render_gb_item(
            make_gb_item(image=None, images=["https://cdn.example/x.jpg"]),
            {}, {}, rel_prefix="../",
        )
        self.assertIn('src="https://cdn.example/x.jpg"', out)
        self.assertNotIn('src="../https://', out)


# ──────────────────── geekhack image discovery ────────────────────


SAMPLE_THREAD_HTML = b"""<!doctype html><html><body>
<img src="https://geekhack.org/Themes/Nostalgia/images/banner.png" alt="x">
<img src="https://cdn.geekhack.org/Themes/Nostalgia/images/upshrink.png">
<img class="avatar" src="https://geekhack.org/index.php?action=dlattach;attach=1">
<img src="https://i.postimg.cc/AAAAAA/product-shot.png" alt="product">
<img src="https://i.postimg.cc/BBBBBB/another.jpg" alt="2nd">
</body></html>"""


class GeekhackFirstOpImage(unittest.TestCase):
    def test_picks_first_non_chrome(self):
        # Monkeypatch http_get to return our fixture.
        orig = fi.http_get
        fi.http_get = lambda url, **kw: SAMPLE_THREAD_HTML
        try:
            url = fi.geekhack_first_op_image("https://geekhack.org/index.php?topic=1.0")
            self.assertEqual(url, "https://i.postimg.cc/AAAAAA/product-shot.png")
        finally:
            fi.http_get = orig

    def test_returns_none_when_only_chrome(self):
        chrome_only = (
            b'<img src="https://geekhack.org/Themes/banner.png">'
            b'<img src="https://cdn.geekhack.org/Smileys/smile.gif">'
        )
        orig = fi.http_get
        fi.http_get = lambda url, **kw: chrome_only
        try:
            self.assertIsNone(
                fi.geekhack_first_op_image("https://geekhack.org/index.php?topic=1.0")
            )
        finally:
            fi.http_get = orig

    def test_returns_none_on_fetch_failure(self):
        orig = fi.http_get
        def boom(url, **kw): raise OSError("network down")
        fi.http_get = boom
        try:
            self.assertIsNone(
                fi.geekhack_first_op_image("https://geekhack.org/index.php?topic=1.0")
            )
        finally:
            fi.http_get = orig

    def test_skips_geekhack_subdomains(self):
        # Even if extension matches, geekhack.org and *.geekhack.org are chrome.
        html = (
            b'<img src="https://geekhack.org/Themes/banner.png">'
            b'<img src="https://cdn.geekhack.org/Themes/icon.png">'
            b'<img src="https://i.postimg.cc/X/real.png">'
        )
        orig = fi.http_get
        fi.http_get = lambda url, **kw: html
        try:
            url = fi.geekhack_first_op_image("https://geekhack.org/index.php?topic=1.0")
            self.assertEqual(url, "https://i.postimg.cc/X/real.png")
        finally:
            fi.http_get = orig


class DiscoverImageUrl(unittest.TestCase):
    def test_geekhack_branch_invokes_op_image(self):
        called = []
        orig = fi.geekhack_first_op_image
        fi.geekhack_first_op_image = lambda u: (called.append(u) or "https://x.jpg")
        try:
            url = fi.discover_image_url({
                "source": "geekhack",
                "url": "https://geekhack.org/index.php?topic=1.0",
            })
            self.assertEqual(url, "https://x.jpg")
            self.assertEqual(called, ["https://geekhack.org/index.php?topic=1.0"])
        finally:
            fi.geekhack_first_op_image = orig

    def test_unknown_source_returns_none(self):
        self.assertIsNone(
            fi.discover_image_url({"source": "shopify",
                                   "url": "https://x/products/y"})
        )


if __name__ == "__main__":
    unittest.main()

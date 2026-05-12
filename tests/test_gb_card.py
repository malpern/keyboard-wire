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

    def test_ic_with_vendor_links_graduates_to_gb_chrome(self):
        # Auto-graduate rule: once an IC has vendor_links, render as a GB.
        # No subtitle, no empty-vendor placeholder, no muted CTA.
        item = make_gb_item(type="IC", gb={
            "vendor_links": [{
                "vendor": "NovelKeys",
                "url": "https://novelkeys.com/products/x",
                "host": "novelkeys.com",
            }],
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertNotIn("gb-item-ic", out)
        self.assertNotIn("gb-ic-subtitle", out)
        self.assertIn("open on Geekhack", out)
        self.assertNotIn("join the discussion", out)

    def test_ic_without_vendor_links_stays_ic(self):
        item = make_gb_item(type="IC", gb={"vendor_links": []})
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("gb-item-ic", out)
        self.assertIn("gb-ic-subtitle", out)
        self.assertIn("join the discussion", out)

    def test_vendor_pill_renders_as_link_when_vendor_links_match(self):
        item = make_gb_item(gb={
            "vendor_regions": [
                {"region": "US", "name": "NovelKeys"},
                {"region": "UK", "name": "Proto[Typist]"},
                {"region": "EU", "name": "Oblotzky"},
            ],
            "vendor_links": [
                {"vendor": "NovelKeys",
                 "url": "https://novelkeys.com/products/x",
                 "host": "novelkeys.com"},
                {"vendor": "Proto[Typist]",
                 "url": "https://prototypist.net/products/x",
                 "host": "prototypist.net"},
                # No URL for Oblotzky — that pill stays as a span.
            ],
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertIn('href="https://novelkeys.com/products/x"', out)
        self.assertIn('href="https://prototypist.net/products/x"', out)
        # Oblotzky pill has no URL → span, not anchor.
        self.assertIn(
            '<span class="gb-vendor-pill" data-region="EU">'
            '<span class="gb-vendor-region">EU</span>Oblotzky</span>',
            out,
        )

    def test_vendor_pill_orphan_link_renders_with_inferred_region(self):
        # vendor_links carries a vendor that isn't in vendor_regions —
        # we still render it (post-unified-pill behavior). Region is
        # inferred from the host, here Yushakobo's .jp domain.
        item = make_gb_item(gb={
            "vendor_regions": [{"region": "JP", "name": "Existing"}],
            "vendor_links": [{
                "vendor": "Yushakobo",
                "url": "https://shop.yushakobo.jp/products/x",
                "host": "shop.yushakobo.jp",
            }],
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("Yushakobo", out)
        self.assertIn('data-region="JP"', out)
        # And it does link out since we have a URL.
        self.assertIn('href="https://shop.yushakobo.jp/products/x"', out)

    def test_vendor_pill_shows_price_chip_when_metadata_present(self):
        item = make_gb_item(gb={
            "vendor_regions": [{"region": "US", "name": "NovelKeys"}],
            "vendor_links": [{
                "vendor": "NovelKeys",
                "url": "https://novelkeys.com/products/x",
                "price_low": 13500,
                "currency": "USD",
            }],
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertIn('class="gb-vendor-price">$135<', out)

    def test_vendor_pill_shows_price_range(self):
        # 10250-17500 → 1.7x, under 2x threshold, so range.
        item = make_gb_item(gb={
            "vendor_regions": [{"region": "UK", "name": "Proto[Typist]"}],
            "vendor_links": [{
                "vendor": "Proto[Typist]",
                "url": "https://prototypist.net/products/x",
                "price_low": 10250,
                "price_high": 17500,
                "currency": "GBP",
            }],
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertIn(">£102-175<", out)

    def test_vendor_pill_collapses_add_on_range(self):
        # 4500-13500 → 3x, exceeds threshold → show base only.
        item = make_gb_item(gb={
            "vendor_regions": [{"region": "US", "name": "NovelKeys"}],
            "vendor_links": [{
                "vendor": "NovelKeys",
                "url": "https://novelkeys.com/products/x",
                "price_low": 4500, "price_high": 13500,
                "currency": "USD",
            }],
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertIn(">$135<", out)
        self.assertNotIn("$45", out)

    def test_vendor_pill_sold_out_replaces_price_chip(self):
        item = make_gb_item(gb={
            "vendor_regions": [{"region": "US", "name": "NovelKeys"}],
            "vendor_links": [{
                "vendor": "NovelKeys",
                "url": "https://novelkeys.com/products/x",
                "price_low": 13500,
                "currency": "USD",
                "available": False,
            }],
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("gb-vendor-status-out", out)
        self.assertIn(">sold out<", out)
        # Pill also gets a class so CSS can mute the whole thing.
        self.assertIn("gb-vendor-pill-out", out)
        # Price chip is replaced, not shown.
        self.assertNotIn("$135", out)

    def test_vendor_pill_in_stock_shows_price_not_status(self):
        item = make_gb_item(gb={
            "vendor_regions": [{"region": "US", "name": "NovelKeys"}],
            "vendor_links": [{
                "vendor": "NovelKeys",
                "url": "https://novelkeys.com/products/x",
                "price_low": 13500,
                "currency": "USD",
                "available": True,
            }],
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertIn(">$135<", out)
        self.assertNotIn("gb-vendor-status-out", out)
        self.assertNotIn("gb-vendor-pill-out", out)

    def test_vendor_pill_no_price_chip_when_metadata_absent(self):
        item = make_gb_item(gb={
            "vendor_regions": [{"region": "US", "name": "NovelKeys"}],
            "vendor_links": [{
                "vendor": "NovelKeys",
                "url": "https://novelkeys.com/products/x",
                # No price_low / currency
            }],
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertNotIn("gb-vendor-price", out)


class InferVendorRegion(unittest.TestCase):
    def test_known_hosts(self):
        self.assertEqual(gen.infer_vendor_region("novelkeys.com"), "US")
        self.assertEqual(gen.infer_vendor_region("prototypist.net"), "UK")
        self.assertEqual(gen.infer_vendor_region("kbdfans.com"), "CN")
        self.assertEqual(gen.infer_vendor_region("oblotzky.industries"), "EU")
        self.assertEqual(gen.infer_vendor_region("shop.yushakobo.jp"), "JP")
        self.assertEqual(gen.infer_vendor_region("ilumkb.com"), "SG")

    def test_tld_jp(self):
        self.assertEqual(gen.infer_vendor_region("vendor-unknown.jp"), "JP")

    def test_tld_au(self):
        self.assertEqual(gen.infer_vendor_region("foo.com.au"), "AU")

    def test_tld_co_uk(self):
        self.assertEqual(gen.infer_vendor_region("foo.co.uk"), "UK")

    def test_tld_de_eu(self):
        self.assertEqual(gen.infer_vendor_region("vendor.de"), "EU")

    def test_unknown_com_returns_none(self):
        # Ambiguous .com without an explicit map entry → don't guess.
        self.assertIsNone(gen.infer_vendor_region("randomvendor.com"))

    def test_empty(self):
        self.assertIsNone(gen.infer_vendor_region(""))
        self.assertIsNone(gen.infer_vendor_region(None))


class UnifiedVendorPills(unittest.TestCase):
    def test_regions_with_matching_links(self):
        gb = {
            "vendor_regions": [
                {"region": "US", "name": "NovelKeys"},
            ],
            "vendor_links": [
                {"vendor": "NovelKeys",
                 "url": "https://novelkeys.com/products/x",
                 "host": "novelkeys.com",
                 "price_low": 13500, "currency": "USD"},
            ],
        }
        out = gen.unified_vendor_pills(gb)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["region"], "US")
        self.assertEqual(out[0]["name"], "NovelKeys")
        self.assertEqual(out[0]["url"],
                         "https://novelkeys.com/products/x")
        self.assertEqual(out[0]["price_low"], 13500)

    def test_orphan_link_inherits_inferred_region(self):
        # Vendor in vendor_links but not in vendor_regions.
        gb = {
            "vendor_regions": [],
            "vendor_links": [{
                "vendor": "Yushakobo",
                "url": "https://shop.yushakobo.jp/products/x",
                "host": "shop.yushakobo.jp",
                "available": False,
            }],
        }
        out = gen.unified_vendor_pills(gb)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["region"], "JP")
        self.assertEqual(out[0]["available"], False)

    def test_orphan_link_no_region_when_unknown_host(self):
        gb = {
            "vendor_links": [{
                "vendor": "Mystery Vendor",
                "url": "https://mystery.com/products/x",
                "host": "mystery.com",
            }],
        }
        out = gen.unified_vendor_pills(gb)
        self.assertEqual(out[0]["name"], "Mystery Vendor")
        self.assertIsNone(out[0]["region"])

    def test_preserves_vendor_regions_order(self):
        gb = {
            "vendor_regions": [
                {"region": "US", "name": "NovelKeys"},
                {"region": "EU", "name": "Oblotzky"},
                {"region": "JP", "name": "Yushakobo"},
            ],
            "vendor_links": [],
        }
        out = gen.unified_vendor_pills(gb)
        self.assertEqual([e["name"] for e in out],
                         ["NovelKeys", "Oblotzky", "Yushakobo"])

    def test_dedup_when_link_and_region_overlap(self):
        gb = {
            "vendor_regions": [{"region": "US", "name": "NovelKeys"}],
            "vendor_links": [{
                "vendor": "NovelKeys",
                "url": "https://novelkeys.com/products/x",
                "host": "novelkeys.com",
            }],
        }
        out = gen.unified_vendor_pills(gb)
        self.assertEqual(len(out), 1)
        # Should pick up the URL from the link
        self.assertIn("url", out[0])

    def test_empty_gb_returns_empty(self):
        self.assertEqual(gen.unified_vendor_pills({}), [])


class FormatVendorPrice(unittest.TestCase):
    def test_usd_single(self):
        self.assertEqual(gen.format_vendor_price(13500), "$135")

    def test_usd_range(self):
        self.assertEqual(gen.format_vendor_price(13500, 17500), "$135-175")

    def test_gbp(self):
        self.assertEqual(
            gen.format_vendor_price(10250, currency="GBP"), "£102",
        )

    def test_eur(self):
        self.assertEqual(
            gen.format_vendor_price(13900, currency="EUR"), "€139",
        )

    def test_unknown_currency_prefix(self):
        # Unknown currencies render as the 3-letter code prefix.
        self.assertEqual(
            gen.format_vendor_price(10000, currency="ZAR"), "ZAR 100",
        )

    def test_collapses_when_high_equals_low(self):
        # High = low → single-value display.
        self.assertEqual(
            gen.format_vendor_price(13500, 13500), "$135",
        )

    def test_wide_range_shows_base_only(self):
        # $3 sticker + $100 base → show "$100", not "$3-100".
        self.assertEqual(gen.format_vendor_price(300, 10000), "$100")

    def test_narrow_range_shows_both(self):
        # $80-149 (Cannonkeys / Youmu shape) — under 2x, keep range.
        self.assertEqual(gen.format_vendor_price(8000, 14900), "$80-149")

    def test_2x_threshold_boundary(self):
        # Exactly 2x → still a range. Just above 2x → max only.
        self.assertEqual(gen.format_vendor_price(5000, 10000), "$50-100")
        self.assertEqual(gen.format_vendor_price(5000, 10001), "$100")


    def test_vendor_pill_name_match_is_case_insensitive(self):
        item = make_gb_item(gb={
            "vendor_regions": [{"region": "US", "name": "novelkeys"}],
            "vendor_links": [{"vendor": "NovelKeys",
                              "url": "https://novelkeys.com/x"}],
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("gb-vendor-pill-link", out)

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

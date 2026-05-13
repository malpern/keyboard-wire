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


class InferGbCategory(unittest.TestCase):
    def _make(self, **kw):
        base = {"title": "", "tags": []}
        base.update(kw)
        return base

    def test_keycap_tag_wins(self):
        self.assertEqual(
            gen.infer_gb_category(
                self._make(title="random title", tags=["keycap-design"])
            ),
            "Keycap",
        )

    def test_switch_tag_wins(self):
        self.assertEqual(
            gen.infer_gb_category(
                self._make(tags=["switch-development", "magnetic-switch"])
            ),
            "Switch",
        )

    def test_pcb_tag(self):
        self.assertEqual(
            gen.infer_gb_category(self._make(tags=["pcb-design"])), "PCB",
        )

    def test_keycap_title_fallback(self):
        # No tag hint — title says "GMK CYL Greg 2" → keycap.
        self.assertEqual(
            gen.infer_gb_category(self._make(title="[GB] GMK CYL Greg 2")),
            "Keycap",
        )

    def test_switch_title_fallback(self):
        self.assertEqual(
            gen.infer_gb_category(self._make(title="[IC] YuRui HE Switch")),
            "Switch",
        )

    def test_housing_falls_back_to_keyboard(self):
        self.assertEqual(
            gen.infer_gb_category(
                self._make(title="[IC] RF8X by keyhub - a housing for Realforce"),
            ),
            "Keyboard",
        )

    def test_deskmat(self):
        self.assertEqual(
            gen.infer_gb_category(self._make(title="[GB] DSS Deskmat")),
            "Deskmat",
        )

    def test_default_keyboard(self):
        # Nothing matches → default to Keyboard.
        self.assertEqual(
            gen.infer_gb_category(self._make(title="some unrelated thing")),
            "Keyboard",
        )


class GbStockState(unittest.TestCase):
    def test_in_stock(self):
        gb = {"vendor_links": [
            {"price_low": 100, "available": True},
            {"available": False},
        ]}
        self.assertEqual(gen.gb_stock_state(gb), "in_stock")

    def test_sold_out(self):
        gb = {"vendor_links": [
            {"price_low": 100, "available": False},
            {"available": False},
        ]}
        self.assertEqual(gen.gb_stock_state(gb), "sold_out")

    def test_unknown(self):
        gb = {"vendor_links": [{"vendor": "X"}]}  # no price, no avail
        self.assertEqual(gen.gb_stock_state(gb), "unknown")

    def test_unknown_availability_counts_as_in_stock(self):
        # We can't prove sold-out for unknowns, so unknown + price → in_stock.
        gb = {"vendor_links": [{"price_low": 100}]}
        self.assertEqual(gen.gb_stock_state(gb), "in_stock")


class VendorPriceByRegion(unittest.TestCase):
    def test_picks_lowest_per_region(self):
        gb = {"vendor_links": [
            {"vendor": "NK", "host": "novelkeys.com",
             "price_low": 14000, "currency": "USD", "available": True},
            {"vendor": "Bowl", "host": "bowlkeyboards.com",
             "price_low": 12000, "currency": "USD", "available": True},
            {"vendor": "Proto", "host": "prototypist.net",
             "price_low": 10000, "currency": "GBP", "available": True},
        ]}
        out = gen.vendor_price_by_region(gb)
        self.assertEqual(out["US"], "$120")   # Bowl < NovelKeys
        self.assertEqual(out["UK"], "£100")

    def test_skips_sold_out(self):
        gb = {"vendor_links": [
            {"vendor": "NK", "host": "novelkeys.com",
             "price_low": 14000, "currency": "USD", "available": False},
        ]}
        self.assertNotIn("US", gen.vendor_price_by_region(gb))

    def test_skips_when_no_inferred_region(self):
        gb = {"vendor_links": [
            {"vendor": "Mystery", "host": "unknown-shop.io",
             "price_low": 10000, "available": True},
        ]}
        self.assertEqual(gen.vendor_price_by_region(gb), {})

    def test_ranks_by_base_kit(self):
        # Vendor A: novelties $20 + base $135. Vendor B: base $130 only.
        # Same region — A's base ($135) > B's base ($130), so B wins.
        gb = {"vendor_links": [
            {"vendor": "A", "host": "novelkeys.com",
             "price_low": 2000, "price_high": 13500,
             "currency": "USD", "available": True},
            {"vendor": "B", "host": "bowlkeyboards.com",
             "price_low": 13000, "currency": "USD", "available": True},
        ]}
        out = gen.vendor_price_by_region(gb)
        self.assertEqual(out["US"], "$130")


class RepresentativeVendorPrice(unittest.TestCase):
    def test_picks_lowest_base_kit_in_stock(self):
        gb = {"vendor_links": [
            {"price_low": 15000, "currency": "USD", "available": True},
            {"price_low": 12000, "currency": "USD", "available": True},
            {"price_low": 9000,  "currency": "USD", "available": False},
        ]}
        price, vl = gen.representative_vendor_price(gb)
        self.assertEqual(price, "$120")
        self.assertEqual(vl["price_low"], 12000)

    def test_ranks_by_base_kit_not_lowest_variant(self):
        # Vendor A: 4500 (novelties) + 13500 (base kit). Base = 13500.
        # Vendor B: 10000 (base, no add-ons).
        # Vendor B's base is cheaper → it should win.
        gb = {"vendor_links": [
            {"vendor": "A", "price_low": 4500, "price_high": 13500,
             "currency": "USD", "available": True},
            {"vendor": "B", "price_low": 10000,
             "currency": "USD", "available": True},
        ]}
        price, vl = gen.representative_vendor_price(gb)
        self.assertEqual(vl["vendor"], "B")
        self.assertEqual(price, "$100")

    def test_falls_back_to_unknown_when_no_in_stock(self):
        gb = {"vendor_links": [
            {"price_low": 9000, "currency": "USD", "available": False},
            {"price_low": 10000, "currency": "USD"},  # available: None
        ]}
        price, vl = gen.representative_vendor_price(gb)
        self.assertEqual(price, "$100")
        self.assertEqual(vl["price_low"], 10000)

    def test_none_when_no_prices(self):
        gb = {"vendor_links": [{"vendor": "X"}]}  # no price_low
        self.assertEqual(gen.representative_vendor_price(gb), (None, None))

    def test_none_when_empty(self):
        self.assertEqual(gen.representative_vendor_price({}), (None, None))


class BadgeRender(unittest.TestCase):
    def _item(self, **kw):
        base = {
            "id": "geekhack-1", "title": "[GB] GMK Greg 2", "type": "GB",
            "source": "geekhack", "url": "https://x/",
            "via": "Geekhack", "category": "breaking", "takeaway": "",
            "tags": ["keycap-design"],
            "image": "img/x.jpg",
        }
        base.update(kw)
        return base

    def test_badge_on_first_slide_only(self):
        item = self._item(image=None, images=[
            "img/x-0.jpg", "img/x-1.jpg", "img/x-2.jpg",
        ])
        out = gen.render_gb_item(item, {}, {})
        # Exactly one badge across the entire carousel.
        self.assertEqual(out.count('class="gb-badge"'), 1)
        # The badge is inside the first slide (appears before slide 2).
        idx_badge = out.index('class="gb-badge"')
        idx_slide2 = out.index('aria-label="Image 2 of 3"')
        self.assertLess(idx_badge, idx_slide2)

    def test_badge_shows_category_and_stage(self):
        out = gen.render_gb_item(self._item(), {}, {})
        self.assertIn('class="gb-badge-cat">Keycap</span>', out)
        self.assertIn('class="gb-badge-stage gb-badge-stage-gb">GB<', out)

    def test_badge_shows_price_when_available(self):
        item = self._item(gb={"vendor_links": [
            {"vendor": "NK", "price_low": 13500, "currency": "USD",
             "available": True},
        ]})
        out = gen.render_gb_item(item, {}, {})
        self.assertIn('class="gb-badge-price">$135</span>', out)

    def test_badge_omits_price_when_no_metadata(self):
        out = gen.render_gb_item(self._item(), {}, {})
        self.assertNotIn("gb-badge-price", out)

    def test_badge_ic_stage(self):
        out = gen.render_gb_item(self._item(type="IC"), {}, {})
        self.assertIn("gb-badge-stage-ic", out)
        self.assertIn(">IC<", out)

    def test_badge_shows_sold_out_when_all_vendors_out(self):
        item = self._item(gb={"vendor_links": [
            {"vendor": "A", "host": "novelkeys.com",
             "price_low": 13500, "currency": "USD", "available": False},
            {"vendor": "B", "host": "kbdfans.com",
             "price_low": 14500, "currency": "USD", "available": False},
        ]})
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("gb-badge-soldout", out)
        self.assertIn(">sold out<", out)
        self.assertNotIn("gb-badge-price", out)

    def test_badge_keeps_price_when_at_least_one_in_stock(self):
        item = self._item(gb={"vendor_links": [
            {"vendor": "A", "host": "novelkeys.com",
             "price_low": 13500, "currency": "USD", "available": True},
            {"vendor": "B", "host": "kbdfans.com",
             "price_low": 14500, "currency": "USD", "available": False},
        ]})
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("gb-badge-price", out)
        self.assertNotIn("gb-badge-soldout", out)

    def test_badge_carries_region_prices_attr(self):
        item = self._item(gb={"vendor_links": [
            {"vendor": "NovelKeys", "host": "novelkeys.com",
             "price_low": 13500, "currency": "USD", "available": True},
            {"vendor": "Proto[Typist]", "host": "prototypist.net",
             "price_low": 2000, "price_high": 10250,
             "currency": "GBP", "available": True},
            {"vendor": "Oblotzky", "host": "oblotzky.industries",
             "price_low": 13900, "currency": "EUR", "available": True},
        ]})
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("data-region-prices", out)
        # Verify the JSON content carries every region.
        m = __import__("re").search(
            r'data-region-prices="([^"]+)"', out,
        )
        import json as _json
        m_obj = _json.loads(__import__("html").unescape(m.group(1)))
        self.assertEqual(m_obj["US"], "$135")
        self.assertEqual(m_obj["UK"], "£102")
        self.assertEqual(m_obj["EU"], "€139")

    def test_badge_omits_region_prices_attr_when_no_prices(self):
        out = gen.render_gb_item(self._item(), {}, {})
        self.assertNotIn("data-region-prices", out)

    def test_badge_region_prices_skips_sold_out(self):
        item = self._item(gb={"vendor_links": [
            {"vendor": "NovelKeys", "host": "novelkeys.com",
             "price_low": 13500, "currency": "USD", "available": False},
        ]})
        out = gen.render_gb_item(item, {}, {})
        # Sold-out US vendor → no US entry in the region map.
        self.assertNotIn("data-region-prices", out)

    def test_badge_absent_when_no_images(self):
        # No carousel at all → no badge.
        out = gen.render_gb_item(self._item(image=None), {}, {})
        self.assertNotIn("gb-badge", out)


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
    import datetime as _dt
    REF = _dt.date(2026, 5, 12)

    def test_starts_no_countdown(self):
        # "starts" prefix renders date only — no countdown.
        self.assertEqual(
            gen.fmt_date_chip("2026-01-03", prefix="starts", today=self.REF),
            "starts Jan 3",
        )

    def test_ends_future_includes_days_left(self):
        self.assertEqual(
            gen.fmt_date_chip("2026-06-14", prefix="ends", today=self.REF),
            "ends Jun 14 · 33 days",
        )

    def test_ends_today(self):
        self.assertEqual(
            gen.fmt_date_chip("2026-05-12", prefix="ends", today=self.REF),
            "ends today",
        )

    def test_ends_tomorrow(self):
        self.assertEqual(
            gen.fmt_date_chip("2026-05-13", prefix="ends", today=self.REF),
            "ends tomorrow",
        )

    def test_ended_yesterday(self):
        self.assertEqual(
            gen.fmt_date_chip("2026-05-11", prefix="ends", today=self.REF),
            "ended yesterday",
        )

    def test_ended_past_days_ago(self):
        self.assertEqual(
            gen.fmt_date_chip("2026-05-01", prefix="ends", today=self.REF),
            "ended May 1 · 11 days ago",
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

    def test_urgent_chip_when_ending_soon(self):
        # Freeze "today" via a tiny date subclass so the render path's
        # date.today() returns our reference date.
        import datetime as _dt
        ref = _dt.date(2026, 5, 12)
        orig = _dt.date

        class FrozenDate(_dt.date):
            @classmethod
            def today(cls):
                return ref
        _dt.date = FrozenDate
        gen.datetime.date = FrozenDate
        try:
            # Ends in 2 days exactly → urgent.
            item = make_gb_item(gb={
                "status": "live",
                "ends_at": "2026-05-14",
            })
            out = gen.render_gb_item(item, {}, {})
            self.assertIn("gb-chip-urgent", out)
            # Ends in 5 days → not urgent.
            item2 = make_gb_item(gb={
                "status": "live",
                "ends_at": "2026-05-17",
            })
            out2 = gen.render_gb_item(item2, {}, {})
            self.assertNotIn("gb-chip-urgent", out2)
            # Already ended yesterday → not urgent (historic).
            item3 = make_gb_item(gb={
                "status": "live",
                "ends_at": "2026-05-11",
            })
            out3 = gen.render_gb_item(item3, {}, {})
            self.assertNotIn("gb-chip-urgent", out3)
            # Ends today → urgent.
            item4 = make_gb_item(gb={
                "status": "live",
                "ends_at": "2026-05-12",
            })
            out4 = gen.render_gb_item(item4, {}, {})
            self.assertIn("gb-chip-urgent", out4)
        finally:
            _dt.date = orig
            gen.datetime.date = orig

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
        # End-date chip carries the date plus a countdown (e.g.
        # "ends Jun 14 · 33 days") — exact day count depends on
        # `today`, so just match the date portion.
        self.assertIn("ends Jun 14", out)

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

    def test_related_thread_ic_link_renders_for_gb(self):
        item = make_gb_item(type="GB", gb={
            "related_threads": [{
                "type": "IC", "title": "Original IC",
                "url": "https://geekhack.org/index.php?topic=99999.0",
                "topic_id": "99999",
            }],
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("Original Interest Check", out)
        self.assertIn('href="https://geekhack.org/index.php?topic=99999.0"',
                      out)
        self.assertIn("gb-related", out)

    def test_related_thread_earlier_gb_label(self):
        item = make_gb_item(type="GB", gb={
            "related_threads": [{
                "type": "GB", "title": "GMK Gregory",
                "url": "https://geekhack.org/index.php?topic=110101.0",
                "topic_id": "110101",
            }],
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("Earlier Group Buy", out)
        self.assertNotIn("Interest Check", out)

    def test_related_thread_priority_ic_over_other(self):
        # When a GB card has both IC and GB related threads, IC wins.
        item = make_gb_item(type="GB", gb={
            "related_threads": [
                {"type": "GB", "title": "Earlier GB",
                 "url": "https://geekhack.org/?topic=1", "topic_id": "1"},
                {"type": "IC", "title": "Original IC",
                 "url": "https://geekhack.org/?topic=2", "topic_id": "2"},
            ],
        })
        out = gen.render_gb_item(item, {}, {})
        self.assertIn("Original Interest Check", out)
        self.assertIn('href="https://geekhack.org/?topic=2"', out)
        # The other (earlier-GB) related thread should not also render.
        self.assertNotIn('href="https://geekhack.org/?topic=1"', out)
        self.assertNotIn("Earlier Group Buy", out)

    def test_related_thread_no_link_when_absent(self):
        item = make_gb_item(type="GB")
        out = gen.render_gb_item(item, {}, {})
        self.assertNotIn("gb-related", out)
        self.assertNotIn("Interest Check", out)

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


class ShortenGbTakeaway(unittest.TestCase):
    def test_cuts_at_vendors_section(self):
        body = ("Cool keycap set inspired by old keyboards. "
                "Vendors: US: NovelKeys UK: ProtoTypist EU: Oblotzky")
        out = gen.shorten_gb_takeaway(body)
        self.assertEqual(out, "Cool keycap set inspired by old keyboards")

    def test_cuts_at_pricing(self):
        body = ("Quick description here. "
                "Pricing: Base $135 ...")
        out = gen.shorten_gb_takeaway(body)
        self.assertEqual(out, "Quick description here")

    def test_cuts_at_group_buy_info(self):
        body = ("GMK Foo by Bar. Group Buy Info: Date May 1st ...")
        out = gen.shorten_gb_takeaway(body)
        self.assertEqual(out, "GMK Foo by Bar")

    def test_cuts_at_moq(self):
        body = ("Short pitch text. MOQ of 50 @ 110 USD Vendors:")
        out = gen.shorten_gb_takeaway(body)
        self.assertEqual(out, "Short pitch text")

    def test_cuts_at_where_to_buy(self):
        body = ("INSPIRATION The set was inspired by Vaporwave aesthetics. "
                "WHERE TO BUY: North America: Mechs & Co")
        out = gen.shorten_gb_takeaway(body)
        self.assertIn("Vaporwave aesthetics", out)
        self.assertNotIn("Mechs", out)

    def test_no_markers_returns_short_text(self):
        body = "Just a plain pitch with no structured sections at all."
        self.assertEqual(gen.shorten_gb_takeaway(body), body)

    def test_caps_at_240_chars(self):
        body = "Long description. " * 30  # ~540 chars, no markers
        out = gen.shorten_gb_takeaway(body)
        self.assertLessEqual(len(out), 240 + 2)  # +2 slack for trailing "…"

    def test_caps_at_sentence_boundary_when_possible(self):
        body = ("First sentence here describing a project in detail. "
                "Second sentence adds more context to the description. "
                "Third sentence goes on and continues talking about it. "
                "Fourth sentence is even more text just to pad out the body.")
        out = gen.shorten_gb_takeaway(body)
        # Should end with a "." (sentence boundary) within 240.
        self.assertTrue(out.endswith(".") or out.endswith("…"))
        self.assertLessEqual(len(out), 240 + 2)

    def test_empty(self):
        self.assertEqual(gen.shorten_gb_takeaway(""), "")
        self.assertEqual(gen.shorten_gb_takeaway(None), "")

    def test_real_dcs_grass_valley_sample(self):
        body = (
            "DCS Grass Valley Keycap set inspired by media / film "
            "editing keyboards from the '80s and '90s. Designer "
            "Discord Group Buy Info: Date: May 1st - June 1st "
            "Delivery Estimate: Q3-Q4 2026 Vendors: US: Bowl CA: "
            "Minokeys Korea: Geon CN: Typist Club"
        )
        out = gen.shorten_gb_takeaway(body)
        # Cut at "Designer Discord" or "Group Buy Info" — both come
        # before the long structured tail.
        self.assertIn("DCS Grass Valley", out)
        self.assertIn("'80s and '90s", out)
        self.assertNotIn("Vendors:", out)
        self.assertNotIn("Bowl", out)
        self.assertNotIn("Minokeys", out)


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

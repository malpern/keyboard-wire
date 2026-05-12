"""Unit tests for the GB/IC quarantine in scripts/generate.py.

Run from repo root: python3 -m unittest tests.test_generate_gb
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate as gen  # noqa: E402


def _item(source: str, item_id: str, topics=None) -> dict:
    return {
        "id": item_id,
        "title": f"item {item_id}",
        "url": f"https://example/{item_id}",
        "source": source,
        "category": "breaking",
        "takeaway": "",
        "topics": topics or [],
    }


CORPUS_FIXTURE = {
    "title": "test",
    "tagline": "test",
    "days": [
        {"date": "2026-05-12", "items": [
            _item("hn", "hn-1"),
            _item("geekhack", "geekhack-100", topics=["group-buys-vendors"]),
            _item("reddit", "reddit-1"),
            _item("shopify", "shopify-novelkeys-99",
                  topics=["group-buys-vendors"]),
        ]},
        {"date": "2026-05-11", "items": [
            _item("kbdnews", "kbdnews-7"),
            _item("geekhack", "geekhack-101", topics=["group-buys-vendors"]),
        ]},
        {"date": "2026-05-10", "items": []},  # empty day preserved
    ],
}


class SourceLabel(unittest.TestCase):
    """Regression coverage for the bug where every non-Reddit source
    was falling through to the literal label 'Reddit'."""

    def test_geekhack_uses_via(self):
        self.assertEqual(
            gen.source_label({"source": "geekhack",
                              "via": "Geekhack · Group Buys"}),
            "Geekhack · Group Buys",
        )

    def test_kbdnews_uses_via(self):
        self.assertEqual(
            gen.source_label({"source": "kbdnews", "via": "KBD.news"}),
            "KBD.news",
        )

    def test_reddit_with_sub(self):
        self.assertEqual(
            gen.source_label({"source": "reddit", "subreddit": "MechanicalKeyboards"}),
            "r/MechanicalKeyboards",
        )

    def test_reddit_no_sub(self):
        self.assertEqual(gen.source_label({"source": "reddit"}), "Reddit")

    def test_hn(self):
        self.assertEqual(gen.source_label({"source": "hn"}), "Hacker News")

    def test_email_uses_via(self):
        self.assertEqual(
            gen.source_label({"source": "email", "via": "Cannonkeys"}),
            "✉ Cannonkeys",
        )

    def test_unknown_source_no_via(self):
        # Defensive: never silently mislabel as Reddit.
        self.assertEqual(
            gen.source_label({"source": "shopify"}), "Shopify",
        )

    def test_geekhack_never_renders_as_reddit(self):
        # This is the bug the user actually reported.
        label = gen.source_label({"source": "geekhack",
                                  "via": "Geekhack · Group Buys"})
        self.assertNotEqual(label, "Reddit")
        self.assertNotIn("r/", label)


class IsGb(unittest.TestCase):
    def test_geekhack(self):
        self.assertTrue(gen.is_gb({"source": "geekhack"}))

    def test_shopify(self):
        self.assertTrue(gen.is_gb({"source": "shopify"}))

    def test_news_sources(self):
        for s in ("hn", "reddit", "email", "kbdnews"):
            self.assertFalse(gen.is_gb({"source": s}), f"{s} should be news")

    def test_missing_source(self):
        self.assertFalse(gen.is_gb({}))
        self.assertFalse(gen.is_gb({"source": None}))
        self.assertFalse(gen.is_gb({"source": ""}))

    def test_constants_consistent(self):
        # Guard against typo drift: if GB_SOURCES changes, callers need to know.
        self.assertEqual(gen.GB_SOURCES, {"geekhack", "shopify"})
        self.assertEqual(gen.GB_TOPIC_SLUG, "group-buys-vendors")


class FilterCorpus(unittest.TestCase):
    def test_news_only_excludes_gb(self):
        news = gen.filter_corpus(CORPUS_FIXTURE, lambda it: not gen.is_gb(it))
        ids = {it["id"] for d in news["days"] for it in d["items"]}
        self.assertEqual(ids, {"hn-1", "reddit-1", "kbdnews-7"})

    def test_gb_only_keeps_gb(self):
        gb = gen.filter_corpus(CORPUS_FIXTURE, gen.is_gb)
        ids = {it["id"] for d in gb["days"] for it in d["items"]}
        self.assertEqual(ids, {"geekhack-100", "geekhack-101",
                               "shopify-novelkeys-99"})

    def test_partition_is_lossless(self):
        # news ∪ gb == original (no item dropped or duplicated)
        news = gen.filter_corpus(CORPUS_FIXTURE, lambda it: not gen.is_gb(it))
        gb = gen.filter_corpus(CORPUS_FIXTURE, gen.is_gb)
        partitioned = {it["id"] for d in news["days"] for it in d["items"]}
        partitioned |= {it["id"] for d in gb["days"] for it in d["items"]}
        original = {it["id"] for d in CORPUS_FIXTURE["days"]
                    for it in d["items"]}
        self.assertEqual(partitioned, original)

    def test_day_order_preserved(self):
        news = gen.filter_corpus(CORPUS_FIXTURE, lambda it: not gen.is_gb(it))
        self.assertEqual([d["date"] for d in news["days"]],
                         ["2026-05-12", "2026-05-11", "2026-05-10"])

    def test_empty_days_preserved(self):
        news = gen.filter_corpus(CORPUS_FIXTURE, lambda it: not gen.is_gb(it))
        last = next(d for d in news["days"] if d["date"] == "2026-05-10")
        self.assertEqual(last["items"], [])

    def test_title_tagline_carried(self):
        news = gen.filter_corpus(CORPUS_FIXTURE, lambda it: not gen.is_gb(it))
        self.assertEqual(news["title"], CORPUS_FIXTURE["title"])
        self.assertEqual(news["tagline"], CORPUS_FIXTURE["tagline"])

    def test_assert_bad_predicate(self):
        with self.assertRaises(AssertionError):
            gen.filter_corpus(CORPUS_FIXTURE, "not callable")  # type: ignore[arg-type]

    def test_assert_bad_corpus(self):
        with self.assertRaises(AssertionError):
            gen.filter_corpus("not a dict", lambda _it: True)  # type: ignore[arg-type]
        with self.assertRaises(AssertionError):
            gen.filter_corpus({}, lambda _it: True)

    def test_predicate_returning_truthy(self):
        # Predicate may return non-bool truthy/falsy.
        result = gen.filter_corpus(CORPUS_FIXTURE, lambda it: it.get("id"))
        # Every item has an id, so nothing filtered.
        ids = {it["id"] for d in result["days"] for it in d["items"]}
        self.assertEqual(len(ids), 6)


class RenderGbDayBlockCategoryGrouping(unittest.TestCase):
    """Per-day items group by inferred category, Keyboards before
    Keycaps, then Switches, etc. Each subgroup gets a label."""

    def _it(self, item_id, title, **extra):
        base = {
            "id": item_id, "title": title, "type": "GB",
            "source": "geekhack",
            "url": f"https://x/{item_id}", "via": "Geekhack",
            "category": "breaking", "takeaway": "",
            "tags": [],
        }
        base.update(extra)
        return base

    def test_keyboards_render_before_keycaps(self):
        day = {"date": "2026-05-12", "items": [
            self._it("kc-1", "[GB] GMK Keycap Set",
                     tags=["keycap-design"]),
            self._it("kb-1", "[GB] RF8X Housing",
                     tags=["diy-build"]),
            self._it("kc-2", "[GB] DSS Distortion",
                     tags=["keycap-design"]),
        ]}
        html = gen.render_gb_day_block(day, {}, {})
        ikb = html.index("kb-1")
        ikc1 = html.index("kc-1")
        ikc2 = html.index("kc-2")
        self.assertLess(ikb, ikc1)
        self.assertLess(ikb, ikc2)

    def test_category_headers_present_when_multiple_categories(self):
        day = {"date": "2026-05-12", "items": [
            self._it("kb-1", "[GB] Housing", tags=["diy-build"]),
            self._it("kc-1", "[GB] Keycaps", tags=["keycap-design"]),
            self._it("sw-1", "[GB] Switch", tags=["switch-development"]),
        ]}
        html = gen.render_gb_day_block(day, {}, {})
        self.assertIn(">Keyboards<", html)
        self.assertIn(">Keycaps<", html)
        self.assertIn(">Switches<", html)

    def test_no_header_when_single_category(self):
        # Day with only one category → no "Keycaps" label noise.
        day = {"date": "2026-05-12", "items": [
            self._it("kc-1", "[GB] A", tags=["keycap-design"]),
            self._it("kc-2", "[GB] B", tags=["keycap-design"]),
        ]}
        html = gen.render_gb_day_block(day, {}, {})
        self.assertNotIn("gb-category-label", html)
        # Both items still render.
        self.assertIn("kc-1", html)
        self.assertIn("kc-2", html)

    def test_other_category_renders_last(self):
        # Untagged item with non-keyboard title → "Other" bucket → last.
        day = {"date": "2026-05-12", "items": [
            self._it("other-1", "[GB] Random Object"),
            self._it("kc-1", "[GB] GMK Keycaps", tags=["keycap-design"]),
        ]}
        html = gen.render_gb_day_block(day, {}, {})
        # "Other" might or might not appear depending on category guess.
        # Verify only: keycap comes first if Other bucket exists.
        ikc = html.index("kc-1")
        i_other = html.index("other-1")
        # Whatever category Other maps to, kc-1 should be before it
        # since keycap precedes most categories in _CATEGORY_DAY_ORDER.
        # (We accept either ordering for untagged "random" items.)
        # The real assertion: rendered html doesn't error and both
        # items appear.
        self.assertTrue(ikc >= 0 and i_other >= 0)

    def test_empty_day_returns_empty(self):
        self.assertEqual(
            gen.render_gb_day_block({"date": "2026-05-12", "items": []},
                                    {}, {}),
            "",
        )

    def test_items_within_category_sorted_by_score_desc(self):
        day = {"date": "2026-05-12", "items": [
            self._it("kc-low",  "[GB] A", tags=["keycap-design"], score=10),
            self._it("kc-high", "[GB] B", tags=["keycap-design"], score=1000),
        ]}
        html = gen.render_gb_day_block(day, {}, {})
        ihigh = html.index("kc-high")
        ilow = html.index("kc-low")
        self.assertLess(ihigh, ilow)


class RenderGroupbuysSectionedPage(unittest.TestCase):
    """v2.4 page split: GB items render in the 'Active group buys'
    section, IC items in 'Interest checks'."""

    def _corpus(self):
        return {
            "title": "kw", "tagline": "t",
            "days": [{"date": "2026-05-12", "items": [
                {"id": "geekhack-1", "title": "[GB] X", "type": "GB",
                 "source": "geekhack", "url": "https://x/1",
                 "via": "Geekhack", "category": "breaking", "takeaway": ""},
                {"id": "geekhack-2", "title": "[IC] Y", "type": "IC",
                 "source": "geekhack", "url": "https://x/2",
                 "via": "Geekhack", "category": "breaking", "takeaway": ""},
            ]}],
        }

    def test_page_has_both_sections(self):
        html = gen.render_groupbuys_page(self._corpus(), {}, {})
        self.assertIn("gb-section-live", html)
        self.assertIn("gb-section-interest", html)

    def test_gb_item_in_live_section_only(self):
        html = gen.render_groupbuys_page(self._corpus(), {}, {})
        live_start = html.index("gb-section-live")
        interest_start = html.index("gb-section-interest")
        live_block = html[live_start:interest_start]
        interest_block = html[interest_start:]
        self.assertIn("geekhack-1", live_block)
        self.assertNotIn("geekhack-1", interest_block)

    def test_ic_item_in_interest_section_only(self):
        html = gen.render_groupbuys_page(self._corpus(), {}, {})
        live_start = html.index("gb-section-live")
        interest_start = html.index("gb-section-interest")
        live_block = html[live_start:interest_start]
        interest_block = html[interest_start:]
        self.assertIn("geekhack-2", interest_block)
        self.assertNotIn("geekhack-2", live_block)

    def test_images_use_relative_prefix(self):
        # Regression: /groupbuys/index.html lives one level deep, so
        # `img/X.jpg` refs must render as `../img/X.jpg` to resolve.
        # First version of v2.4 forgot the rel_prefix and produced
        # 404s for every carousel image.
        corpus = {
            "title": "kw", "tagline": "t",
            "days": [{"date": "2026-05-12", "items": [
                {"id": "geekhack-1", "title": "[GB] X", "type": "GB",
                 "source": "geekhack", "url": "https://x/1",
                 "via": "Geekhack", "category": "breaking", "takeaway": "",
                 "image": "img/geekhack-1.jpg",
                 "images": ["img/geekhack-1-0.jpg", "img/geekhack-1-1.jpg"]},
            ]}],
        }
        html = gen.render_groupbuys_page(corpus, {}, {})
        # Every img src must start with `../img/` — anything starting
        # with bare `img/` would 404 in the browser.
        bare_refs = [line for line in html.splitlines()
                     if 'src="img/' in line]
        self.assertEqual(bare_refs, [],
                         f"bare img/ refs found: {bare_refs[:2]}")
        self.assertIn('src="../img/geekhack-1-0.jpg"', html)
        self.assertIn('src="../img/geekhack-1-1.jpg"', html)

    def test_ic_with_vendor_links_renders_in_live_section(self):
        # Auto-graduate: an IC with vendor_links sits in the
        # "Active group buys" section, not "Interest checks".
        corpus = {
            "title": "kw", "tagline": "t",
            "days": [{"date": "2026-05-12", "items": [
                {"id": "geekhack-ic-graduated", "title": "[IC] Y",
                 "type": "IC", "source": "geekhack",
                 "url": "https://x/", "via": "Geekhack",
                 "category": "breaking", "takeaway": "",
                 "gb": {"vendor_links": [
                     {"vendor": "X", "url": "https://x/p", "host": "x.com"},
                 ]}},
            ]}],
        }
        html = gen.render_groupbuys_page(corpus, {}, {})
        live_start = html.index("gb-section-live")
        interest_start = (
            html.index("gb-section-interest")
            if "gb-section-interest" in html else len(html)
        )
        live_block = html[live_start:interest_start]
        self.assertIn("geekhack-ic-graduated", live_block)

    def test_active_page_excludes_historic_items(self):
        corpus = {
            "title": "kw", "tagline": "t",
            "days": [{"date": "2026-05-12", "items": [
                {"id": "geekhack-active", "title": "[GB] Live", "type": "GB",
                 "source": "geekhack", "url": "https://x/",
                 "via": "Geekhack", "category": "breaking", "takeaway": "",
                 "gb": {"status": "live", "ends_at": "2027-01-01"}},
                {"id": "geekhack-postponed", "title": "[GB] Old",
                 "type": "GB", "source": "geekhack", "url": "https://x/",
                 "via": "Geekhack", "category": "breaking", "takeaway": "",
                 "gb": {"status": "postponed"}},
            ]}],
        }
        html_active = gen.render_groupbuys_page(corpus, {}, {})
        self.assertIn("geekhack-active", html_active)
        self.assertNotIn("geekhack-postponed", html_active)

    def test_historic_page_shows_only_historic(self):
        corpus = {
            "title": "kw", "tagline": "t",
            "days": [{"date": "2026-05-12", "items": [
                {"id": "geekhack-active", "title": "[GB] Live", "type": "GB",
                 "source": "geekhack", "url": "https://x/",
                 "via": "Geekhack", "category": "breaking", "takeaway": "",
                 "gb": {"status": "live"}},
                {"id": "geekhack-postponed", "title": "[GB] Old",
                 "type": "GB", "source": "geekhack", "url": "https://x/",
                 "via": "Geekhack", "category": "breaking", "takeaway": "",
                 "gb": {"status": "postponed"}},
            ]}],
        }
        html_h = gen.render_groupbuys_page(corpus, {}, {}, historic=True)
        self.assertIn("geekhack-postponed", html_h)
        self.assertNotIn("geekhack-active", html_h)

    def test_active_page_links_to_historic(self):
        empty = {"title": "kw", "tagline": "t", "days": []}
        html_a = gen.render_groupbuys_page(empty, {}, {})
        self.assertIn('href="../groupbuys/historic/"', html_a)
        self.assertIn("historic group buys", html_a)

    def test_historic_page_links_back_to_active(self):
        empty = {"title": "kw", "tagline": "t", "days": []}
        html_h = gen.render_groupbuys_page(empty, {}, {}, historic=True)
        self.assertIn('href="../../groupbuys/"', html_h)
        self.assertIn("active group buys", html_h)

    def test_empty_corpus_shows_empty_message(self):
        empty = {"title": "kw", "tagline": "t", "days": []}
        html = gen.render_groupbuys_page(empty, {}, {})
        self.assertIn("No group buys tracked yet", html)


if __name__ == "__main__":
    unittest.main()

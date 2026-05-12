"""Unit tests for the pure GB metadata extractor (Step 2.3).

Run from repo root: python3 -m unittest tests.test_gb_extract
"""
import datetime
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from gb_extract import (  # noqa: E402
    extract_dates,
    extract_designer,
    extract_gb_facets,
    extract_moq,
    extract_photo_credit,
    extract_price_range,
    extract_status,
    extract_vendor_regions,
)


REF = datetime.date(2026, 5, 12)


# ────────────────── status ──────────────────


class ExtractStatus(unittest.TestCase):
    def test_last_day_in_title(self):
        self.assertEqual(
            extract_status("[GB] X - LAST DAY", "", today=REF),
            "live",
        )

    def test_postponed_in_title(self):
        self.assertEqual(
            extract_status("[GB] X - GB Postponed", "", today=REF),
            "postponed",
        )

    def test_sold_out_title(self):
        self.assertEqual(
            extract_status("[GB] X SOLD OUT", "", today=REF), "sold-out",
        )

    def test_live_till_in_title(self):
        self.assertEqual(
            extract_status("[GB] X | Live till June 1st!", "", today=REF),
            "live",
        )

    def test_body_now_live(self):
        self.assertEqual(
            extract_status("[GB] X", "GB is now live and ends May 30",
                           today=REF),
            "live",
        )

    def test_ends_at_in_past_inferred_ended(self):
        # Body says nothing; ends_at < today → "ended"
        self.assertEqual(
            extract_status("Foo", "body", ends_at="2026-05-01", today=REF),
            "ended",
        )

    def test_ends_at_in_future_inferred_live(self):
        self.assertEqual(
            extract_status("Foo", "body", ends_at="2026-06-14", today=REF),
            "live",
        )

    def test_no_signal_returns_none(self):
        self.assertIsNone(extract_status("Foo", "body", today=REF))

    def test_explicit_title_beats_date_inference(self):
        self.assertEqual(
            extract_status("[GB] X - POSTPONED", "irrelevant",
                           ends_at="2026-06-14", today=REF),
            "postponed",
        )


# ────────────────── designer ──────────────────


class ExtractDesigner(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(
            extract_designer("GMK Gregory 2 by chamelemon_64. Greetings…"),
            "chamelemon_64",
        )

    def test_two_designers_and(self):
        self.assertEqual(
            extract_designer("GMK Gregory 2 by chamelemon_64 and pancake. "
                             "Greetings Greg Gang."),
            "chamelemon_64 and pancake",
        )

    def test_no_by_pattern(self):
        self.assertIsNone(extract_designer("Just a description with no credit"))

    def test_attribution_beyond_window_ignored(self):
        # "by X" appearing far down the body (past ~400 chars) is
        # almost always prose, not attribution. Window is 400.
        body = "Long preamble. " * 40 + " Now I'm credited by Designer."
        self.assertIsNone(extract_designer(body))

    def test_rejects_when_name_is_a_month(self):
        # Pathological: "ends by May" must not yield designer="May".
        self.assertIsNone(extract_designer("Available by May."))

    def test_handles_empty_body(self):
        self.assertIsNone(extract_designer(""))
        self.assertIsNone(extract_designer(None))

    def test_inspired_by_does_not_overwrite_real_designer(self):
        # Real audit pattern: real designer at top, then "inspired by
        # the aesthetics of …" later. The first match wins, and the
        # later prose phrase must not be returned even if first fails.
        body = ("Echo by beardgrylls (of the black parade). "
                "Set inspired by the aesthetics of early punk rock albums.")
        self.assertEqual(extract_designer(body), "beardgrylls")

    def test_rejects_inspired_by_when_no_other_designer(self):
        # No real designer line; only the false-positive "inspired by"
        # phrase. Must return None.
        body = "Set inspired by the aesthetics of early punk rock albums."
        self.assertIsNone(extract_designer(body))

    def test_rejects_designed_by_myself_in_collaboration(self):
        body = ("Sup, the set was designed by myself in collaboration "
                "with CannonKeys.")
        self.assertIsNone(extract_designer(body))

    def test_rejects_inspired_by_dinokidz(self):
        body = ("Hello dino lovers. Inspired by Dinokidz keyboard, "
                "featuring legends from Extended 2048.")
        self.assertIsNone(extract_designer(body))

    def test_rejects_manufactured_by(self):
        body = "Hot keycap set manufactured by NicePBT."
        self.assertIsNone(extract_designer(body))

    def test_design_from_studio(self):
        body = ("The sixth design from ORI CLUB. ORI 870. Design Notes "
                "This time around, I spent most of the process refining.")
        self.assertEqual(extract_designer(body), "ORI CLUB")

    def test_single_word_designed_from(self):
        body = "A new design from Salvun, the studio behind earlier sets."
        self.assertEqual(extract_designer(body), "Salvun")

    def test_this_is_studio(self):
        body = "Hello, this is Moyu.studio. This time we bring you a switch."
        self.assertEqual(extract_designer(body), "Moyu.studio")

    def test_my_name_is_lowercase_handle(self):
        body = ("Hi everyone! My name is keekeen. Im a fellow "
                "Topre / rubber dome enjoyer.")
        self.assertEqual(extract_designer(body), "keekeen")

    def test_widened_window_catches_buried_by(self):
        # Real audit case: "by keyhub.design" appears at char ~260,
        # past the old 240-char cap.
        preamble = ("Hi everyone! My name is keekeen. Im a fellow Topre / "
                    "rubber dome enjoyer. For me, the journey began with the "
                    "HHKB, which I consider my first end game keyboard. ")
        body = (preamble + "Today, I am thrilled to introduce a "
                "passion project that's been in the works: RF8X by keyhub.design.")
        # Both "My name is keekeen" (pattern 4) and "by keyhub.design"
        # (pattern 1) match. Pattern 1 is first in the list — so "by"
        # attribution wins.
        result = extract_designer(body)
        self.assertEqual(result, "keyhub.design")


# ────────────────── dates ──────────────────


class ExtractDates(unittest.TestCase):
    def test_range_with_two_months(self):
        s, e = extract_dates("Date: May 1st - June 1st", today=REF)
        self.assertEqual(s, "2026-05-01")
        self.assertEqual(e, "2026-06-01")

    def test_range_implicit_second_month(self):
        s, e = extract_dates("from May 1st to 29th", today=REF)
        self.assertEqual(s, "2026-05-01")
        self.assertEqual(e, "2026-05-29")

    def test_em_dash_range(self):
        s, e = extract_dates("Available May 5—June 10.", today=REF)
        self.assertEqual(s, "2026-05-05")
        self.assertEqual(e, "2026-06-10")

    def test_end_prefix_only(self):
        s, e = extract_dates("GB ends June 14.", today=REF)
        self.assertIsNone(s)
        self.assertEqual(e, "2026-06-14")

    def test_run_until(self):
        s, e = extract_dates("Will run until May 15", today=REF)
        self.assertEqual(e, "2026-05-15")

    def test_start_prefix_only(self):
        s, e = extract_dates("from April 13th the GB is open", today=REF)
        self.assertEqual(s, "2026-04-13")
        self.assertIsNone(e)

    def test_year_advances_when_past(self):
        # "January 5" mentioned on 2026-05-12 means Jan 2027 (next cycle).
        s, e = extract_dates("starts January 5", today=REF)
        self.assertEqual(s, "2027-01-05")

    def test_invalid_day_returns_none(self):
        s, e = extract_dates("ends February 31", today=REF)
        self.assertIsNone(e)

    def test_no_date_in_body(self):
        s, e = extract_dates("no dates here", today=REF)
        self.assertEqual((s, e), (None, None))


# ────────────────── MOQ ──────────────────


class ExtractMoq(unittest.TestCase):
    def test_moq_of_n(self):
        self.assertEqual(extract_moq("MOQ of 50 @ 110 USD"), 50)

    def test_n_moq(self):
        self.assertEqual(extract_moq("75 MOQ"), 75)

    def test_moq_n_no_of(self):
        self.assertEqual(extract_moq("MOQ 100"), 100)

    def test_first_match_wins(self):
        # First mention is the headline; subsequent are tier breakdowns.
        self.assertEqual(extract_moq("MOQ of 50 @ X. Also 75 MOQ tier."), 50)

    def test_rejects_too_small(self):
        # "5 MOQ" is probably noise; cap at 10.
        self.assertIsNone(extract_moq("only 5 MOQ"))

    def test_rejects_too_large(self):
        self.assertIsNone(extract_moq("MOQ 9999"))

    def test_no_match(self):
        self.assertIsNone(extract_moq("no requirement"))


# ────────────────── price range ──────────────────


class ExtractPriceRange(unittest.TestCase):
    def test_single_base_price(self):
        lo, hi = extract_price_range("Base $135 for the kit")
        self.assertEqual(lo, 13500)
        self.assertIsNone(hi)

    def test_pricing_ladder(self):
        body = "Pricing: Base: $149.00 - 50 MOQ, $130.00 - 75 MOQ, $113.00 - 150 MOQ"
        lo, hi = extract_price_range(body)
        self.assertEqual(lo, 11300)
        self.assertEqual(hi, 14900)

    def test_at_symbol_anchor(self):
        body = "MOQ of 50 @ 110 USD"
        # No $ — current extractor doesn't catch bare-currency prices.
        # That's OK — accept None as "didn't extract" rather than wrong.
        lo, hi = extract_price_range(body)
        self.assertIsNone(lo)

    def test_unanchored_fallback(self):
        # No "Base"/"Pricing" word; falls back to scanning first 600 chars.
        lo, hi = extract_price_range("Some intro text. Cost is $80 base.")
        # $80 is in range, will be picked up via fallback scan.
        self.assertEqual(lo, 8000)

    def test_rejects_out_of_range_prices(self):
        # $5 deskmat addon, $999 typo — both excluded by sanity caps.
        lo, hi = extract_price_range("Base $130. Deskmat $5. Bundle $999.")
        self.assertEqual(lo, 13000)
        self.assertIsNone(hi)  # only $130 survives sanity

    def test_empty_body(self):
        self.assertEqual(extract_price_range(""), (None, None))


# ────────────────── vendors ──────────────────


class ExtractVendorRegions(unittest.TestCase):
    def test_basic_list(self):
        body = "Vendors US: NovelKeys UK: Proto[Typist] EU: Oblotzky"
        out = extract_vendor_regions(body)
        self.assertEqual(out, [
            {"region": "US", "name": "NovelKeys"},
            {"region": "UK", "name": "Proto[Typist]"},
            {"region": "EU", "name": "Oblotzky"},
        ])

    def test_with_hyphen_separator(self):
        body = "US- SabreKeebs UK- ProtoTypist EU- Keeb.Supply"
        out = extract_vendor_regions(body)
        names = {v["region"]: v["name"] for v in out}
        self.assertEqual(names["US"], "SabreKeebs")
        self.assertEqual(names["UK"], "ProtoTypist")
        self.assertEqual(names["EU"], "Keeb.Supply")

    def test_skips_empty_regions(self):
        # "CA: EU: Delta Key Co." — CA has no vendor, only Delta Key Co. survives.
        body = "Vendors US: Bowl CA: EU: Delta Key Co. UK: Proto[Typist]"
        out = extract_vendor_regions(body)
        regions = [v["region"] for v in out]
        self.assertNotIn("CA", regions)
        self.assertIn("US", regions)
        self.assertIn("UK", regions)
        self.assertIn("EU", regions)

    def test_aus_normalized_to_au(self):
        out = extract_vendor_regions("AUS: Keebz N Cables")
        self.assertEqual(out[0]["region"], "AU")

    def test_oco_normalized_to_oc(self):
        out = extract_vendor_regions("OCO: Daily Clack")
        self.assertEqual(out[0]["region"], "OC")

    def test_dedup_same_region_name(self):
        # Same pair appearing twice (designer wrote a vendor list at
        # the top, repeated it verbatim in a recap section).
        body = "US: NovelKeys UK: ProtoTypist\nVendors: US: NovelKeys UK: ProtoTypist"
        out = extract_vendor_regions(body)
        # Two distinct pairs, each appearing twice → dedup'd to two.
        self.assertEqual(len(out), 2)

    def test_no_vendors(self):
        self.assertEqual(extract_vendor_regions("no vendors mentioned"), [])


# ────────────────── extract_gb_facets (public) ─────────────────


class ExtractPhotoCredit(unittest.TestCase):
    def test_photo_by_colon(self):
        self.assertEqual(
            extract_photo_credit("text… Photo by: keima. Kit:"),
            "keima",
        )

    def test_photos_by(self):
        self.assertEqual(
            extract_photo_credit("description Photos by X. More info."),
            "X",
        )

    def test_renders_by(self):
        self.assertEqual(
            extract_photo_credit("Renders by Geon for the kit"),
            "Geon",
        )

    def test_photography(self):
        self.assertEqual(
            extract_photo_credit("Photography by Dan, shot in 2026."),
            "Dan",
        )

    def test_multi_render_picks_first(self):
        # OPs sometimes list per-render credits ("Renders F1-40 by Geon
        # MB-44 by MelonBred"). First credit wins.
        # Note: "F1-40" is captured as the kit code prefix in our text,
        # so the regex starts at "by Geon" — captures "Geon".
        body = "Renders F1-40 by Geon MB-44 by MelonBred"
        out = extract_photo_credit(body)
        self.assertEqual(out, "Geon")

    def test_returns_none_when_no_credit(self):
        body = "Description with no photo credit line"
        self.assertIsNone(extract_photo_credit(body))

    def test_rejects_prose_capture(self):
        # "Renders may not picture actual colors" → no real name, reject.
        body = "Disclaimer: Renders may not picture actual colors"
        self.assertIsNone(extract_photo_credit(body))

    def test_empty_body(self):
        self.assertIsNone(extract_photo_credit(""))
        self.assertIsNone(extract_photo_credit(None))


class IsHistoricGbItem(unittest.TestCase):
    REF = datetime.date(2026, 5, 12)

    def _it(self, **gb):
        return {"id": "x", "gb": gb}

    def test_postponed_status_is_historic(self):
        import generate as gen  # local import
        self.assertTrue(gen.is_historic_gb_item(
            self._it(status="postponed"), self.REF,
        ))

    def test_ended_status_is_historic(self):
        import generate as gen
        self.assertTrue(gen.is_historic_gb_item(
            self._it(status="ended"), self.REF,
        ))

    def test_ends_at_past_is_historic(self):
        import generate as gen
        self.assertTrue(gen.is_historic_gb_item(
            self._it(ends_at="2026-05-10"), self.REF,
        ))

    def test_ends_at_today_is_active(self):
        import generate as gen
        self.assertFalse(gen.is_historic_gb_item(
            self._it(ends_at="2026-05-12"), self.REF,
        ))

    def test_ends_at_future_is_active(self):
        import generate as gen
        self.assertFalse(gen.is_historic_gb_item(
            self._it(ends_at="2026-05-20"), self.REF,
        ))

    def test_live_status_is_active(self):
        import generate as gen
        self.assertFalse(gen.is_historic_gb_item(
            self._it(status="live", ends_at="2026-05-29"), self.REF,
        ))

    def test_no_status_no_ends_at_is_active(self):
        import generate as gen
        self.assertFalse(gen.is_historic_gb_item(
            {"id": "x"}, self.REF,
        ))

    def test_status_overrides_ends_at(self):
        # Postponed beats a future ends_at — designer pulled it.
        import generate as gen
        self.assertTrue(gen.is_historic_gb_item(
            self._it(status="postponed", ends_at="2026-06-01"), self.REF,
        ))


class ExtractGbFacets(unittest.TestCase):
    def test_real_gregory_2_sample(self):
        item = {
            "title": "[GB] GMK Gregory 2",
            "takeaway": (
                "GMK Gregory 2 by chamelemon_64 and pancake. Greetings "
                "Greg Gang. He has returned. Available from May 1st to "
                "29th. Estimated fulfillment date Q3 2026 Vendors US: "
                "NovelKeys UK: Proto[Typist] EU: Oblotzky Industries "
                "KR: GEONWORKS CN: KBDfans SG: iLumkb Kits Base $135, "
                "Novelties $45, Deskpad $25"
            ),
        }
        out = extract_gb_facets(item, today=REF)
        self.assertEqual(out["designer"], "chamelemon_64 and pancake")
        self.assertEqual(out["starts_at"], "2026-05-01")
        self.assertEqual(out["ends_at"], "2026-05-29")
        self.assertEqual(out["status"], "live")
        self.assertEqual(out["price_low"], 13500)
        # Vendors list non-empty
        regions = {v["region"] for v in out["vendor_regions"]}
        self.assertIn("US", regions)
        self.assertIn("UK", regions)
        self.assertIn("EU", regions)

    def test_real_dss_distortion_sample(self):
        item = {
            "title": "[GB] DSS Distortion 40s",
            "takeaway": (
                "This set will go on sale on April 13th and run until "
                "May 15th. MOQ of 50 @ 110 USD Vendors US- SabreKeebs "
                "UK- ProtoTypist EU- Keeb.Supply"
            ),
        }
        out = extract_gb_facets(item, today=REF)
        self.assertEqual(out["ends_at"], "2026-05-15")
        # April 13 is before today's May 12 — still in current year cycle
        self.assertEqual(out["starts_at"], "2026-04-13")
        self.assertEqual(out["moq"], 50)

    def test_postponed_in_title_wins_over_date(self):
        item = {
            "title": "[GB] GMK Iconographic - GB Postponed",
            "takeaway": "Set will run May 1 - May 30",
        }
        out = extract_gb_facets(item, today=REF)
        self.assertEqual(out["status"], "postponed")

    def test_empty_returns_empty_dict(self):
        out = extract_gb_facets({"title": "", "takeaway": ""}, today=REF)
        self.assertEqual(out, {})

    def test_no_crash_on_missing_fields(self):
        # Should not raise even when keys are absent.
        out = extract_gb_facets({}, today=REF)
        self.assertIsInstance(out, dict)


if __name__ == "__main__":
    unittest.main()

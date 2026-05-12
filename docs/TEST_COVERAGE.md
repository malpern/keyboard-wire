# Test coverage — current state + punch list

Status: snapshot 2026-05-12. Run tests with
`python3 -m unittest discover tests` from the repo root (zero deps —
stdlib `unittest` only).

## Covered (67 tests)

| Module / function | Test file | Tests |
|---|---|---|
| `geekhack_pilot.*` — URL parsing, title cleaning, type extraction, HTML strip, pubdate parsing, feed parsing, `collect()` thread-dedup + earliest-wins + schema-shape, `to_item` asserts | `tests/test_geekhack_pilot.py` | 39 |
| `tag_items.merge_topics` — seeded/parsed merge, dedup, cap at 2, invalid-slug drop, fallbacks, asserts | `tests/test_tag_items.py` | 14 |
| `generate.is_gb`, `generate.filter_corpus` — GB quarantine, lossless partition invariant, day-order/empty-day preservation, asserts | `tests/test_generate_gb.py` | 14 |

## Untested — gap inventory

Listed in priority order. Top of the list is the cheapest + highest
value to add.

| Module | Surface area | Risk | Test difficulty |
|---|---|---|---|
| `append_day.merge()`     | Corpus mutation, per-id dedup, day-creation | **Corrupts archive on bug.** Pure function. | Easy — pure dict logic |
| `cluster.py`             | URL canonicalization + primary-source priority (email > score > input order) | Wrong cluster collapses obscure bugs that masquerade as missing items | Easy — pure |
| `append_day.write_day_file()` | Per-day JSON merge on disk | Silent data loss if dedup is wrong | Easy — tmp dir |
| `kbdnews_pilot.*`        | RSS parsing, post-id extraction, 24h window, skip-pattern filter | Quiet ingestor — bug = 0 items, indistinguishable from quiet days | Easy — synthetic XML |
| `parse_digest.py`        | HN + Reddit shared parser | High volume → bugs amplify | Medium — Qwen output stubs |
| `fetch_images.py`        | og:image extraction, image validation, crop | Bad image hurts share-card but doesn't break feed | Medium — needs HTTP mocking |
| `rewrite_titles.py`      | Title rewrites — applies to news but skipped for GB | Cosmetic; LLM behavior is the unknowable | Hard — LLM output |
| `email_pipeline.py`, `email_archive.py`, `sanitize_email.py` | Gmail ingest + sanitizer + landing-page | Sanitizer is security-adjacent (XSS surface) | Medium — fixture HTML |
| `post_twitter.py`        | X delivery, posted-state, rate-limit handling | **The 5/06–5/10 silent-skip lived here.** A unit test wouldn't have caught the cred/env-mount root cause, but logic around state-file + dedup is testable. | Medium — OAuth mocking |
| `generate.py` (most of it: `render_item`, `render_day_block`, `render_index`, `render_browse_page`, `render_archive_page`, `render_rss`) | ~1500 lines of HTML/RSS string building | Visual regressions, broken links | Medium — golden-file snapshots |
| `dedup_audit.py`         | Diagnostic | Read-only; bug = wrong numbers in a report | Easy — but low priority |

## Suggested expansion order

1. **`append_day.merge()` + `write_day_file()`** — pure, load-bearing, smallest commit. ~20 tests.
2. **`cluster.py`** — primary-source priority logic is the precedent the deferred cross-source clusterer will inherit; cementing it in tests now prevents drift. ~15 tests.
3. **`kbdnews_pilot.*`** — mirror of the `geekhack_pilot` test pattern; reusable scaffolding. ~12 tests.
4. **`generate.render_rss`** — RSS output is a contract subscribers depend on; golden-file snapshot test guards against silent breakage.
5. **`sanitize_email.py`** — security-adjacent; XSS-vector fixtures.
6. **Everything else** — defer.

Items 1–3 are ~3 hours of work and would lift coverage from "GB/IC
only" to "all core pure pipeline logic." Items 4–6 are harder and
each warrant their own session.

## What unit tests will NOT catch

- The `post_twitter.py` 5-day silent skip (cred/env-mount issue —
  needs the 5:30 PT health check, which already exists).
- LLM output regressions in `tag_items.py` / `rewrite_titles.py` —
  unit tests can lock the *prompt building* and *response parsing*,
  not the model.
- Network flakes in ingestors — mocking helps for logic, not for the
  reality of upstream HTTP behavior.
- Visual / CSS regressions on the rendered site.

## Conventions

- `tests/test_<module>.py`, mirroring `scripts/<module>.py`.
- `unittest.TestCase` subclasses; no third-party deps.
- Synthetic fixtures inline in the test file when small; in
  `tests/fixtures/` when shared or > ~1KB.
- One assertion concept per test method (multiple `assertEqual`s OK
  when they all guard the same property).
- Asserts inside production code at trust boundaries (input shape,
  invariant preservation) — fail loud, fail early.

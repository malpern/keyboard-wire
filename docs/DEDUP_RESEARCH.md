# Cross-source dedup — research notes

Status: **deferred (again)**. Last reviewed 2026-05-12.

## The hypothesis we tested

The 2026-05-11 design memo predicted that adding kbd.news to the daily
ingest mix would surface cross-source collisions — same upstream project
appearing on Reddit (firmware-watch) AND on kbd.news, with different
URLs and different per-source IDs. The plan was to land kbd.news, watch
for collisions for ~a week, then design clustering against real data
instead of guessing at the algorithm.

## What the data shows

Audit run on 2026-05-12 against `data/corpus.json` + `data/backfill.json`
(see `scripts/dedup_audit.py`):

- **129 unique items**, 2026-03-31 → 2026-05-12 (six weeks).
- Sources present: reddit=101, hn=13, email=15. **kbdnews=0.**
- Cross-source title-token Jaccard ≥ 0.25: **1 pair, false positive**
  ("Windows key not working" on r/pchelp vs. an HN post about a Copilot
  key remapping tool — they share the word "key", nothing more).
- Lower thresholds (0.15–0.20) produce only more noise of the same
  shape — generic keyboard vocabulary overlap, not the same project.

The reason there's no kbdnews collision data yet: the kbd.news ingestor
went live 2026-05-11 and has emitted 0 items in two runs. Tamás's RSS
is bursty (1–3 items some days, 0 on others), and we haven't hit a
populated day yet.

Existing three sources (HN front-page filter, Reddit firmware/ergo
subs, Gmail vendor-newsletter label) genuinely don't overlap much in
practice — keyboard-flavored Reddit threads rarely make HN front page,
and Gmail digests are first-party vendor content the others don't echo.

## Why not just build it anyway

A title-fuzzy-match clusterer is straightforward to write — maybe a
half-day with schema changes in `append_day.py` + generate.py merge
rendering. The reason not to:

1. **Threshold can't be calibrated without examples.** Jaccard 0.25
   was already producing false positives in this dataset. Choosing
   0.4? 0.5? Levenshtein over Jaccard? TF-IDF cosine? — all guesses
   without a positive-example set to tune against.
2. **Schema churn cost.** Adding `cluster_id` / `siblings[]` to items
   ripples through `append_day.py`, `generate.py`, the Slack digest
   formatter, and the Twitter post script. Worth doing once we know
   the algorithm, painful to redo if the first cut is wrong.
3. **The known collision generators aren't online yet.** The design
   memo explicitly flagged Geekhack + Shopify as the moment collisions
   spike (same GMK GB on Geekhack IC thread + cannonkeys product page +
   Reddit announcement thread). Those ingestors are still planned, not
   shipped.

## What we shipped instead

- `scripts/dedup_audit.py` — diagnostic that scans corpus + optional
  backfill, normalizes titles, and prints cross-source token-Jaccard
  pairs above a threshold. Read-only, no schema effects. Re-run when
  the source mix changes to see if it's time to build the clusterer.

## Next steps — in order

1. **Wait for kbd.news to actually produce items.** Re-run
   `scripts/dedup_audit.py` weekly. First real cross-source collision
   (kbdnews × reddit) will probably be a project-showcase post Tamás
   picked up that also hit r/MechanicalKeyboards.
2. **Ship Geekhack + Shopify ingestors** (already designed in
   `INGESTORS.md` "Planned ingestors" section). These are the predicted
   collision firehose.
3. **Run two weeks with all six sources, then re-audit.** Expect ≥10
   cross-source pairs; if not, the premise was wrong and we may be able
   to keep deferring indefinitely.
4. **Use those real pairs to choose an algorithm:**
   - If positives cluster cleanly on title-token overlap at some
     threshold, use Jaccard or TF-IDF cosine.
   - If titles diverge too much (e.g., Geekhack `[GB] GMK MTNU Welles`
     vs Reddit `Anyone in on the Welles GB?`) but a shared designer +
     product-name substring is detectable, prefer a named-entity
     extractor (Qwen call) over fuzzy string match.
   - LLM clustering (Qwen comparing pairs above a recall-friendly
     prefilter) is the heavyweight fallback. Cheap because volume is
     small (~50/day post-Geekhack).
5. **Schema decision when building:**
   - **Option A:** add `cluster_id` to every item, render in generate.py
     by collapsing items sharing a cluster_id into one card with
     multi-source attribution.
   - **Option B:** fold duplicates into a parent item with a
     `siblings: [{source, url, id}, ...]` array. Simpler render, but
     loses per-source metadata (scores, comments) unless we keep both.
   - Lean toward A for reversibility — clusters are advisory data, not
     destructive merges.
6. **Re-cluster historical corpus** behind a `--rebuild` flag on the
   clusterer (so we can iterate on the algorithm without permanent
   miscluster damage).
7. **UX:** Slack digest and X post should each show one entry per
   cluster with primary-source attribution + "also seen on X, Y" tail.
   `scripts/cluster.py` already has primary-source priority logic for
   URL-based merges (email > score > input order) — reuse that policy
   so the two clusterers behave consistently.

## Reference

- `scripts/cluster.py` — existing URL-canonicalization clusterer
  (in-pipeline, merges items with identical canonical URL). Handles
  the easy case. The deferred work is the harder cross-source case
  where URLs differ.
- `scripts/dedup_audit.py` — the diagnostic this doc justifies.
- `INGESTORS.md` — "Cross-source dedup (deferred)" section is the
  one-paragraph summary; this doc is the longer story.

# Group Buy / IC routing — design

Status: **revised again 2026-05-12 after user feedback.** Final shape.

Ingestor mechanics (Geekhack + Shopify pilots, combined driver,
pipeline tweaks) live in [INGESTORS.md](./INGESTORS.md) "Planned
ingestors". This doc covers **routing**: which surfaces GB/IC items
appear on.

## The model

GB/IC sources are fully quarantined from existing news surfaces while
the ingestors are debugged. They appear only on a dedicated page
linked from the site header. Once stable, we bring them into more
surfaces incrementally (likely: main feed → Slack → email → X, in
that order of risk).

## Routing per source — v1

| Surface | Geekhack | Shopify |
|---|---|---|
| Main news feed (`/`)                  | ⛔ | ⛔ |
| Main RSS (`/feed.xml`)                | ⛔ | ⛔ |
| Archive (`/archive/`)                 | ⛔ | ⛔ |
| Topic pages (other than GB-vendors)   | ⛔ | ⛔ |
| Tag pages                             | ⛔ | ⛔ |
| `/topics/group-buys-vendors/`         | ✅ (auto via topic seed) | ✅ |
| **`/groupbuys/` (new — linked in header)** | ✅ | ✅ |
| Slack news digest                     | ⛔ | ⛔ |
| X feed                                | ⛔ | ⛔ |
| Daily email                           | ⛔ | ⛔ |

Future (post-debug): flip the relevant ⛔s to ✅ one surface at a time.

## Implementation shape

Single corpus (`data/corpus.json`); GB items live there alongside news.
Two filters in `generate.py`:

```python
GB_SOURCES = {"geekhack", "shopify"}

def is_gb(item): return (item.get("source") or "") in GB_SOURCES
```

- **Exclusion** filter applied in: `render_index`, `render_archive_page`,
  the main `feed.xml` builder, and the topic-page loop (skip GB items
  on topic pages *other than* `group-buys-vendors`). Tag pages: same
  treatment.
- **Inclusion** filter applied in a new `render_groupbuys_page()`
  that writes `docs/groupbuys/index.html` + `docs/groupbuys/feed.xml`.
- Header nav (`site_header()` in generate.py) gets a new link to
  `/groupbuys/`.
- Slack digest / Twitter / email drivers: they read from their own
  command paths, not from `corpus.json` items directly — they're given
  the items the driver chose to publish. Since `group-buys-local.sh`
  is the only driver producing GB items, and it skips
  `_twitter-post.sh` + the email step, GB items naturally never reach
  X or email. No code change needed for those surfaces.

## Card layout

**v1: reuse the existing news card unchanged.** No `render_gb_item`
variant yet. Rationale: the user said "after they have been debugged"
— ship the simplest viable thing, see how the cards actually look
populated with real Geekhack + Shopify data, then decide whether
image-led product cards are worth the extra CSS. The existing card
already shows the image as a thumb and preserves `[GB]` / `[IC]`
prefixes once we skip `rewrite_titles.py`.

If image-first product cards are wanted later, that's a `render_item`
opt-in modifier (`.item.is-gb`), not a rewrite.

## Cross-source dedup

Deferred per [DEDUP_RESEARCH.md](./DEDUP_RESEARCH.md). On the
`/groupbuys/` page, expect to see the same project as separate cards
from Geekhack + multiple Shopify vendors. That's the data we need to
build the clusterer against (DEDUP_RESEARCH.md step 3). Don't
collapse them visually before the algorithm exists.

## Pipeline tweaks (recap from INGESTORS.md)

- Skip `rewrite_titles.py` for `source in (geekhack, shopify)` so
  `[GB] GMK Gregory 2` stays canonical.
- Pre-seed `topics: ["group-buys-vendors"]` in the pilot output so
  `tag_items.py` doesn't have to rediscover the topic and so the
  existing `/topics/group-buys-vendors/` page auto-populates.

## Buylist relationship

Unchanged. Press-and-hold ♡ on a GB card works via the existing
`.item` `data-*` contract, since we're reusing the standard card.

## The two-step rollout

### Step 1 — Geekhack only (this session)

- Build `geekhack_pilot.py` per INGESTORS.md.
- Build `group-buys-local.sh` driver (Geekhack only for now). No
  X post, no email step, no commit to news-feed Slack channel —
  send Slack notification to a separate channel or thread so noise
  is contained. (Open question — see below.)
- Add the two filter passes + `/groupbuys/` render to `generate.py`.
- Add header nav link.
- Test against synthetic items (don't wait for live data).

### Step 1b — Geekhack thread-page enrichment (future)

The RSS feed alone gives us almost no useful GB metadata: title (with
`[GB]`/`[IC]` prefix + occasional inline status hints like "LAST DAY"),
a per-post link, and the **latest reply** body as description (not the
OP). No start/end date, no sold-out signal, no price, no MOQ, no
reply/view counts.

Geekhack thread pages (HTML) do have:
- **Reply count + view count** — direct community-interest signal
- **OP body** — the actual GB announcement (price / MOQ / dates if the
  designer included them)
- Started-by + start date
- Title-updates carrying status hints (LAST DAY, Postponed, Closed)

Plan when we add it:
- One HTTP fetch per *newly seen* thread inside `geekhack_pilot.py`
  (state file gates this to once-per-thread-ever — bounded by new-GB
  cadence, ~1–5/day).
- Single regex each for `replies` and `views`.
- Replace the noisy reply-text takeaway with the OP body.
- Map `views → score` and `replies → comments` so the existing
  `render_item` shows `⬆ 4,231  💬 78` with no UI change.
- Tolerate scrape failure per-thread (emit item with empty
  score/comments rather than skip).

Deferred for the v1 cut so we ship something working end-to-end first
and see how the bare RSS items look on the page before adding scrape
complexity.

### Step 1c — HTTP politeness improvements (future)

Currently `geekhack_pilot.py` re-downloads the full RSS XML every
morning. Geekhack runs nginx and honors HTTP caching — adding
`If-Modified-Since` / `ETag` (state stored alongside
`data/geekhack_seen.json`) would return `304 Not Modified` on quiet
days. Trivial change, polite, free bandwidth savings.

When step 1b (thread-page scrape) lands, add a 1 sec inter-request
throttle so a burst of new threads doesn't fan out to a parallel
fetch storm.

When step 2 (Shopify) lands, add per-vendor inter-request throttle
(target 1 req/sec/vendor; Shopify's published storefront limit is
2 req/sec) and a small inter-vendor pause. Hard-cap pagination at
N=3 pages per collection.

### Step 2 — Shopify + clustering (later session)

- Re-audit dedup (`scripts/dedup_audit.py`) once real Geekhack data
  is in the archive.
- Build `shopify_pilot.py` with bootstrap seed.
- Design the cross-source clusterer (Geekhack ↔ Shopify) using real
  titles as training data.
- Wire clustering before `generate.py` so vendor SKUs collapse under
  parent announcements.
- Eventually: graduate Geekhack items into the main news feed and
  related surfaces, one at a time, with the clusterer hiding
  duplicate vendor noise.

### Step 3 — Broader pipeline test coverage (future)

GB/IC code is well covered (67 tests). The rest of the pipeline
(`append_day`, `cluster`, `kbdnews_pilot`, most of `generate.py`,
the Twitter / email modules) is uncovered. See
[TEST_COVERAGE.md](./TEST_COVERAGE.md) for the prioritized punch list.
Suggested order: `append_day.merge` → `cluster.py` → `kbdnews_pilot`
first (cheap, load-bearing); render-side snapshot tests later.

## Open questions before coding

1. **URL slug** — `/groupbuys/`? Or prefer `/gb/`, `/marketplace/`,
   `/drops/`? My lean: `/groupbuys/` (clearest to a new visitor).
2. **Header link label** — `Group Buys`? `GB / IC`? `Drops`?
   My lean: `Group Buys`.
3. **Slack notification destination for `group-buys-local.sh`** —
   same `C0AAENSTFP1` news channel (visible noise during debug) or
   a separate channel / DM-self thread to keep news clean? My lean:
   separate channel or thread.
4. **Date organization on `/groupbuys/`** — same chronological daily
   blocks as the news feed, or flat / status-sorted? My lean: daily
   blocks for v1 (cheapest, consistent), revisit once populated.

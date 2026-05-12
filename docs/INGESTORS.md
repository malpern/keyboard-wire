# Ingestors

Keyboard-newswire pulls items from multiple upstream sources each morning,
runs them through a shared pipeline, and publishes a unified archive +
Slack digest + X feed. This doc describes the ingestor pattern, the
existing ingestors, and the design for the two planned next (Geekhack &
Shopify group-buy feeds).

## The pipeline (shared by every ingestor)

```
  source feed
       │
       ▼
  pilot/ingest script  ─►  data/<source>_seen.json   (state, dedup)
       │      emits JSON array of items on stdout
       ▼
  scripts/tag_items.py        ─►  Qwen3.6 → topics + tags
       │
       ▼
  scripts/rewrite_titles.py   ─►  Qwen3.6 → cleaner headlines
       │                          (skipped for already-clean sources)
       ▼
  scripts/fetch_images.py     ─►  og:image, validate, crop 320×320
       │                          → docs/img/<id>.jpg
       ▼
  scripts/append_day.py       ─►  data/days/<YYYY-MM-DD>.json
                                  data/corpus.json
                                  (dedupes by item.id)
       │
       ▼
  driver script:
    - openclaw message send  → Slack channel C0AAENSTFP1
    - bash _twitter-post.sh  → X feed
    - (email-news driver also) generate.py + git commit + push
```

**Driver responsibilities** beyond running the pipeline:
- ERR trap → `_alert.sh` (Slack alert on crash)
- Explicit alerts on non-fatal failures (silent skips are a bug — see the
  2026-05-06 → 2026-05-11 incident where Twitter posting silently skipped
  for 5 days)
- Log to `~/.ollama/<driver-name>.log`

## Item schema

Every ingestor emits items in this shape:

```json
{
  "id": "<source>-<unique-stable-id>",
  "title": "...",
  "url": "...",
  "discussion_url": "...",
  "source": "hn|reddit|email|kbdnews|geekhack|shopify",
  "subreddit": null,
  "via": "human-readable attribution",
  "score": null,
  "comments": null,
  "category": "breaking|evergreen",
  "takeaway": "1-2 sentence summary"
}
```

After the pipeline runs, these fields are added/overwritten:
- `topics`: array, populated by `tag_items.py` from `data/topics.json`
- `tags`: array, populated by `tag_items.py` (and grown in `data/tags.json`)
- `title`: may be rewritten by `rewrite_titles.py`
- `image`: relative path `img/<id>.jpg` if `fetch_images.py` succeeded

**The `id` field is the dedup key.** `append_day.py` rejects duplicates by
exact `id` match (per-day and across the whole corpus). Use a stable
source-specific prefix and a stable upstream identifier.

## Cron timeline (PT)

| Time  | Driver                      | Source       |
|-------|-----------------------------|--------------|
| 5:02  | keyboard-news-local.sh      | HN digest    |
| 5:03  | firmware-watch-local.sh     | Reddit (firmware/ergo subs) |
| 5:04  | email-news-local.sh         | Gmail "Keyboard" label |
| 5:05  | kbd-news-local.sh           | KBD.news RSS |
| 5:06  | group-buys-local.sh         | Geekhack + Shopify (planned) |
| 5:30  | keyboard-wire-health.sh     | health check |

Drivers run sequentially, ~30-90s each. The 5:30 health check probes
X auth, archive freshness, cred-file integrity, and twitter quota
signals.

## Existing ingestors

### `keyboard-news-local.sh` → `keyboard-news-pilot.sh` → `parse_digest.py`

Pulls HN front page, runs a Qwen-based filter for keyboard-relevant
posts, parses the output into items with `id = "hn-<item_id>"`. Volume:
0-5 items/day, often 0.

### `firmware-watch-local.sh` → `firmware-watch-pilot.sh` → `parse_digest.py`

Pulls a curated list of Reddit subs (r/MechanicalKeyboards,
r/ErgoMechKeyboards, r/olkb, r/qmk, r/zmk, r/CustomKeyboards, etc.) via
their `.json` endpoints, dedupes, filters to last 24h, runs Qwen filter,
parses with `id = "reddit-<post_id>"`. Volume: 0-50 items/day (bursty —
weekend showcase days are biggest).

### `email-news-local.sh` → `email_pipeline.py`

Reads Gmail messages tagged "Keyboard", calls `gog` (Gmail Operator
Gemini) to extract structured items, classifies via Qwen. Also runs
`email_archive.py` to generate sanitized landing pages at
`docs/email/<thread_id>/` so the original (often Gmail-private) email
content has a public URL. `id = "email-<thread_id>"`. Volume: 0-3/day.

### `kbd-news-local.sh` → `kbdnews_pilot.py`

Parses the RSS feed at `https://kbd.news/rss.xml` (Tamás Dövényi-Nagy's
hand-curated weekly e-zine). Strict 24h window. Skips "Behind the scenes"
weekly meta-posts. `id = "kbdnews-<post_number>"`. Volume: 1-3/day.

## Planned ingestors

### `geekhack_pilot.py` — Geekhack boards 70 (GB) + 132 (IC)

**Feed URLs**:
- `https://geekhack.org/index.php?action=.xml;type=rss;board=70` (Group Buys)
- `https://geekhack.org/index.php?action=.xml;type=rss;board=132` (Interest Checks)

**Key design points**:

- **Feed returns *posts*, not threads.** Every reply counts. Dedup by
  thread id extracted from `topic=NNN` in each item's URL.
- **State file**: `data/geekhack_seen.json` — array of thread IDs already
  emitted. A thread is emitted exactly once. Replies after the first
  emit are ignored. (Could later add a "thread bumped" event type if
  signal/noise warrants it.)
- **Item URL**: point at thread root `https://geekhack.org/index.php?topic=<NNN>.0`,
  not the specific reply that triggered ingest.
- **Title cleaning**: strip leading `Re: ` from reply-derived titles.
  Keep `[GB]`/`[IC]` prefix — it's information, not clutter.
- **Type classification**: parse `[GB]` vs `[IC]` from title prefix into
  a `type` field. Future: extract MOQ, start/end dates, vendors from
  OP body. For now, leave that as item.takeaway = description text.
- **Window**: skip 24h window — state-file dedup is the primary
  mechanism. Just emit anything not in seen state.

**`id` format**: `geekhack-<thread_id>` (e.g. `geekhack-126649`).

### `shopify_pilot.py` — Multi-vendor Shopify collection feeds

**Vendor config** (in script, easy to extend):

| Domain            | Collection handle      | Approx items |
|-------------------|------------------------|--------------|
| novelkeys.com     | preorders              | 30 |
| cannonkeys.com    | group-buy              | 11 |
| omnitype.com      | interest-checks        | 7 |
| dixiemech.com     | interest-checks        | 7 |
| kbdfans.com       | group-buy / pre-order / interest-checks | 82 |
| prototypist.net   | pre-orders             | 13 |
| clickclack.io     | groupbuy               | 30+ |

**Endpoint**: `https://<domain>/collections/<handle>/products.json` (with
`?page=N` for pagination).

**Key design points**:

- **Bootstrap**: first run sees ~200 items as "new" because state file is
  empty. Pre-seed `data/shopify_seen.json` with current inventory once
  before enabling the daily cron, so the first real run emits only items
  *added* in the last 24h.
- **State file**: `data/shopify_seen.json` — array of
  `<vendor_domain>:<product_id>` keys. Per-product, not per-collection,
  since the same product can appear in multiple collections on the same
  store.
- **Item URL**: `https://<vendor_domain>/products/<handle>` (standard
  Shopify product page).
- **Pre-set image**: products.json has `images[0].src`. Set
  `item["image_remote"] = src` and have `fetch_images.py` use that
  directly without HTML scraping. Avoids a redundant fetch.
- **Sold-out filtering**: products.json `variants[*].available` —
  include item even if sold-out (the announcement matters, availability
  changes). Track availability state in extra fields if useful.
- **Designer/brand extraction**: the Shopify `vendor` field is unreliable
  (often the store's brand, not the designer). Extract designer from
  title prefix where possible (`[GB] DCS Grass Valley | iNN Studio` →
  designer = "iNN Studio"). For now, leave as is and post-process later.

**`id` format**: `shopify-<vendor_slug>-<product_id>` (e.g.
`shopify-novelkeys-8625946820775`). Using product_id (numeric, stable)
rather than handle (could change if vendor renames product).

### `group-buys-local.sh` — Combined driver

One driver runs both pilots, concatenates JSON arrays, runs through the
shared pipeline. Slack message header: `🛒 *Group Buys & ICs*` with
sub-sections for each source.

**Special pipeline tweaks for GB sources**:

- **Skip `rewrite_titles.py`** when `source in (geekhack, shopify)` —
  titles like "[GB] GMK Gregory 2" are already canonical; rewriting will
  hurt. Either patch `rewrite_titles.py` to no-op these sources, or
  branch in the driver to skip the rewriter step entirely for the GB
  pipeline (cleaner).
- **Topic seeding**: pre-set `topics: ["group-buys-vendors"]` so the LLM
  in `tag_items.py` doesn't have to discover the topic from scratch.
  `tag_items.py` may add more topics on top.

## Cross-source dedup (deferred)

Same project sometimes appears across multiple ingestors — e.g. GMK MTNU
Welles shows up on Geekhack (board 132 IC), on cannonkeys.com (group-buy
collection), and via firmware-watch (a Reddit thread linking to it). Each
produces a different id and a different URL.

**Current behavior**: all three appear as separate items. The `id`-based
dedup catches exact repeats but nothing cross-source.

**Deferred design**: a `cluster_items.py` step between `append_day` and
`generate`, doing title fuzzy-match (probably TF-IDF cosine or
levenshtein on designer + product name) to group related items.
Decision point: build this once Geekhack + Shopify are live and we have
enough collision data to drive the algorithm choice.

**Audit tool**: `scripts/dedup_audit.py` scans the archive for
cross-source title overlap. Re-run when the source mix changes — first
appearance of real positives is the signal to actually build the
clusterer. Full background and the next-steps checklist live in
`docs/DEDUP_RESEARCH.md`.

## Adding a new ingestor

Pattern (~150 lines total):

1. **Pilot script** at `scripts/<source>_pilot.py`:
   - Fetch upstream feed (RSS / JSON / API)
   - Maintain `data/<source>_seen.json` for dedup if upstream is bursty
   - Emit JSON array on stdout in the item schema above
   - Use `id = "<source>-<stable-upstream-id>"`
   - Default to 24h window if upstream isn't already deduped
2. **Driver script** at `/Users/clawd/clawd/scripts/<source>-local.sh`:
   - Copy `kbd-news-local.sh` as the template
   - Set `DRIVER`, `LOG`, and the `_twitter-post.sh` call
   - Wire ERR trap to `_alert.sh`
   - Build the Slack digest message with a distinct emoji header
3. **`fetch_images.py`**:
   - Add new `source ==` case if the source has its own image-URL
     convention; otherwise rely on og:image extraction
4. **Cron entry**: pick a slot between 5:02 and 5:29 PT
5. **Health check** (`keyboard-wire-health.sh`): no per-driver change
   needed unless the source has unique failure modes (X quota probe is
   the only source-specific check today)

## Reference: alerting & state files

- `/Users/clawd/clawd/scripts/_alert.sh` — shared Slack alert helper
- `~/.ollama/keyboard-wire-alerts.log` — alert history
- `~/.ollama/<driver>.log` — per-driver logs
- `data/twitter_posted.json` — X post dedup state
- `data/twitter_posted_digests.json` — X digest dedup state
- `data/geekhack_seen.json` (planned) — Geekhack thread dedup
- `data/shopify_seen.json` (planned) — Shopify product dedup
- `~/.config/keyboard-wire/x-creds.env` — X OAuth creds (mode 600)
- `~/.config/keyboard-wire/keychain-pw` — fallback keychain unlock

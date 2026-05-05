<p align="center">
  <img src="docs/post/assets/icon-final.png" alt="Keyboard Newswire" width="128" height="128" style="border-radius: 24px;">
</p>

<h1 align="center">keyboard newswire</h1>

<p align="center">
  A daily, auto-curated digest of mechanical-keyboard hardware, firmware, and tooling — pulled from Hacker News, several keyboard subreddits, and a Gmail-labeled inbox of vendor newsletters, then filtered, summarized, tagged, and published as a static site every morning.
</p>

[![Live site screenshot](screenshot.png)](https://keyboard-newswire.com)

### → [Read the live site](https://keyboard-newswire.com) · [RSS feed](https://keyboard-newswire.com/feed.xml) · [About / blog post](https://keyboard-newswire.com/post/)

---

## What it is

Three things in one repo:

1. **The site.** A static HTML/CSS site at `docs/`, served by GitHub Pages. One day per dated section, each item tagged by topic (Firmware, Builds & Hardware, Tools & Software, Splits & Ergo, etc.) and labeled `BREAKING` or `EVERGREEN`. There's an archive view, a "want to buy" page, an RSS feed, and per-email landing pages for newsletter content.
2. **The data.** `data/corpus.json` is the rolling source of truth — every item the site has ever shown. `data/days/<YYYY-MM-DD>.json` holds individual days for cleaner diffs.
3. **The pipeline.** `scripts/` contains the Python that ingests, classifies, sanitizes, and renders. The driver scripts (cron wrappers + Slack delivery) live in a separate private repo, but the pipeline scripts here are usable standalone.

The defaults are tuned to Micah Alpern's reading taste — particular keyboard subreddits, particular vendors, a particular threshold for "is this newsworthy." If you want to fork it for your own taste, see [Run your own](#run-your-own) below.

## How it works

```
                 ┌─ HN Algolia search (~15 keyword terms)
ingest ──────────┼─ Reddit JSON (r/MechanicalKeyboards, r/olkb, r/zmk, …)
                 └─ Gmail "Keyboard" label (vendor newsletters)
                            │
                            ▼
classify  ──── local LLM via Ollama (on-device)
                 ├─ strict include/exclude prompt — DEFAULT POSITION: EXCLUDE
                 └─ "is this newsworthy for a keyboard hobbyist?"
                            │
                            ▼
enrich    ──── tag_items.py · rewrite_titles.py · fetch_images.py
                 ├─ topic tags (Firmware, Builds & Hardware, …)
                 ├─ rewrite clickbait headlines into neutral phrasing
                 └─ fetch og:image thumbnails locally (no hotlinking)
                            │
                            ▼
sanitize  ──── (email items only) sanitize_email.py · email_archive.py
                 ├─ strip personal names, tracking pixels, unsubscribe links
                 └─ generate clean per-email landing pages under docs/email/<id>/
                            │
                            ▼
append    ──── append_day.py
                 ├─ writes to data/days/<YYYY-MM-DD>.json (deduped by item id)
                 └─ updates data/corpus.json
                            │
                            ▼
generate  ──── generate.py
                 ├─ emits docs/index.html, docs/feed.xml, topic/tag indexes
                 └─ commits and pushes — GitHub Pages rebuilds
```

A daily cron runs the three ingest sources at staggered times (5:02 / 5:03 / 5:04 PT), each posting a Slack summary and appending its items. The site regenerates whenever new items land.

### Why a local model?

Each ingest source originally ran on a hosted frontier model — about 109k input tokens/day combined. Moving to a local model on Ollama dropped that to zero, kept quality high after a backtest (5 clean / 2 marginal / 0 bad days out of 7), and freed up rate-limit headroom on the hosted model for interactive work. Inference takes ~10–30s per source on a Mac Mini using a 30B-class instruction-tuned model.

### Why this strict?

Broad keyword searches return mostly noise — `KMK` collides with Kubernetes, `ZMK` with zero-knowledge crypto, `Karabiner` is fine but `Raycast` collides with ray-casting graphics, `keyboard` collides with piano. The classifier's prompt explicitly biases toward exclusion ("DEFAULT POSITION: EXCLUDE — most days return NO_REPLY") with worked examples of false positives. Many days produce nothing, and that's correct: a noisy digest is worse than a quiet one.

## Repo layout

```
data/
  corpus.json          rolling combined corpus (source of truth)
  days/                per-day JSON files
  tags.json            tag → item-id index
  topics.json          topic → item-id index
docs/                  GitHub Pages root
  index.html           today + recent days
  feed.xml             RSS
  archive/             older days
  email/<id>/          sanitized newsletter landing pages
  topics/<slug>/       per-topic views
  tags/<slug>/         per-tag views
  buylist/             "want to buy" picks
  settings/            display options + pipeline docs
  favicon.svg
  style.css
scripts/
  parse_digest.py      Slack mrkdwn → structured items
  tag_items.py         topic tagging via local model
  rewrite_titles.py    headline cleanup via local model
  fetch_images.py      og:image download
  append_day.py        write to corpus, dedupe, commit, push
  generate.py          render docs/ from corpus.json
  email_pipeline.py    Gmail "Keyboard" label → items
  email_archive.py     per-email landing pages
  sanitize_email.py    strip names, trackers, unsub links
```

## Run your own

This works best as a fork-and-customize project. The pipeline is reusable but the *taste* (search terms, classifier prompt, vendor allow-list, topic taxonomy) is mine and you'll want to swap in your own.

### Prerequisites

- macOS or Linux (tested on macOS 25)
- Python 3.11+
- [Ollama](https://ollama.com/) with a capable instruction-tuned model (any 30B+ class model with reliable structured-output behavior should work)
- `jq` and `curl` for the cron drivers
- (Optional) a Gmail account with a label for vendor newsletters, and a CLI like [`gog`](https://github.com/jhillyerd/gog) or any IMAP client to fetch them

### Setup

```bash
# 1. Fork this repo and clone your fork
git clone git@github.com:YOU/keyboard-wire.git
cd keyboard-wire

# 2. Install a local model via Ollama (any capable 30B+ instruction-tuned model)
ollama pull <your-model-of-choice>

# 3. Edit the parts that encode the maintainer's defaults, not yours
#    - scripts/generate.py        SITE_URL, site title, GitHub repo URL
#    - scripts/email_archive.py   SITE_URL, ACCOUNT default
#    - scripts/sanitize_email.py  USER_FIRST_NAMES, USER_EMAIL_LOCAL_PARTS
#    - The classifier prompts in your driver scripts (see below)

# 4. Enable GitHub Pages on your fork
#    Settings → Pages → Source: main branch, /docs folder
```

### The driver scripts

This repo intentionally does **not** include the cron drivers — they're the part that hardcodes a specific Slack channel, OpenClaw delivery path, and Gmail account auth. To run your own, you'll need a small wrapper per source that:

1. Fetches candidate items (HN Algolia for HN; Reddit JSON for subreddits; IMAP/Gmail API for emails)
2. Builds a strict-classifier prompt around them and calls your local model via `http://localhost:11434/api/generate`
3. If the model returns anything other than `NO_REPLY`, pipes the output through `parse_digest.py`, then `tag_items.py | rewrite_titles.py | fetch_images.py | append_day.py | generate.py`
4. Commits `data/` and `docs/` and pushes

If you want a worked example to adapt, [open an issue](https://github.com/malpern/keyboard-wire/issues) asking for a sanitized driver-script template and I'll publish one.

### Customizing taste

The hot spots, in order of likely interest:

- **`tag_items.py`, `rewrite_titles.py`** — prompts that decide topic tags and rewrite headlines. The topic taxonomy (`Firmware`, `Builds & Hardware`, …) lives here.
- **The classifier prompt in your driver script** — the include/exclude rules are the highest-leverage thing to tune. The default biases toward exclusion; yours can be looser.
- **`scripts/generate.py`** — site title, layout, CSS-class hooks.
- **`docs/style.css`** — typography (currently a serif headline / sans body) and color.

### Cost

Running on a Mac Mini with a local model: $0/day in inference. GitHub Pages and Actions are free for public repos. The only paid dependency is electricity.

## License

The code in `scripts/` is offered as-is, no warranty, fork freely.

The site content under `docs/` is auto-curated from third-party sources (Reddit, HN, public newsletters); see those sources for their respective licenses.

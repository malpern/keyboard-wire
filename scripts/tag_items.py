#!/usr/bin/env python3
"""Tag items with topics + tags using local Qwen via Ollama.

Reads a JSON array of items (output of parse_digest.py) on stdin or argv,
adds `topics` and `tags` to each item, emits to stdout.

Maintains/grows data/tags.json as the model proposes new tags.
Topics are read-only (curated registry in data/topics.json).
"""
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOPICS_FILE = ROOT / "data" / "topics.json"
TAGS_FILE = ROOT / "data" / "tags.json"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL = os.environ.get("KW_TAG_MODEL", "qwen3.6:35b-a3b")


def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def load_topics() -> dict:
    return json.loads(TOPICS_FILE.read_text())["topics"]


def save_tags(tags: dict, raw: dict) -> None:
    raw["tags"] = tags
    TAGS_FILE.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")


def normalize_tag(name: str, tags_reg: dict) -> str | None:
    """Map a model-proposed tag to an existing slug, or create a new one.
    Returns canonical slug. Mutates tags_reg in place."""
    name = (name or "").strip()
    if not name:
        return None
    slug = slugify(name)
    if not slug:
        return None
    if slug in tags_reg:
        return slug
    for s, t in tags_reg.items():
        if name.lower() in {a.lower() for a in t.get("aliases", [])}:
            return s
    display = name.replace("-", " ").title() if not any(c.isupper() for c in name) else name
    tags_reg[slug] = {"name": display, "aliases": []}
    return slug


def build_prompt(item: dict, topics: dict, top_tags: list[str]) -> str:
    topics_block = "\n".join(
        f"- {slug}: {meta['name']} — {meta['description']}"
        for slug, meta in topics.items()
    )
    tags_hint = ", ".join(top_tags[:30]) if top_tags else "(none yet)"
    return f"""Classify this keyboard / firmware / tools news item.

ITEM:
title: {item['title']}
takeaway: {item.get('takeaway') or '(none)'}
source: {item.get('source')}
subreddit: {item.get('subreddit') or 'n/a'}
url: {item['url']}

TOPICS (pick 1–2 best fits, by SLUG):
{topics_block}

EXISTING TAGS (prefer these; propose NEW tags only when none fit):
{tags_hint}

Pick 2–4 fine-grained tags (lowercase-hyphenated slugs, e.g. "qmk", "split-keyboard", "open-source-pcb", "kanata"). Tags are descriptive and content-specific.

Return ONLY a JSON object on a single line, no prose, no code fences:
{{"topics": ["topic-slug-1"], "tags": ["tag-1", "tag-2"]}}"""


def call_qwen(prompt: str, timeout: int = 90) -> str:
    """Call Qwen via /api/chat with think:false (Qwen3.6 is a thinking model;
    skipping the think phase keeps tagging fast and deterministic)."""
    chat_url = OLLAMA_URL.rsplit("/api/", 1)[0] + "/api/chat"
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_predict": 300},
    })
    try:
        result = subprocess.run(
            ["curl", "-sS", "-X", "POST", chat_url,
             "-H", "Content-Type: application/json", "-d", payload],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ""
    if result.returncode != 0:
        sys.stderr.write(f"qwen error: {result.stderr[:300]}\n")
        return ""
    try:
        return json.loads(result.stdout).get("message", {}).get("content", "")
    except Exception as e:
        sys.stderr.write(f"qwen parse error: {e}\n")
        return ""


def parse_response(raw: str) -> dict:
    if not raw:
        return {}
    raw = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "")
    m = re.search(r"\{.*?\}", raw, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def merge_topics(seeded, parsed, valid_topics: set,
                 fallback: str = "community") -> list[str]:
    """Combine ingestor-pre-seeded topics with LLM-parsed topics.

    - Seeded topics come first and are preserved in their input order.
    - LLM-parsed topics (capped at 2) are appended only if not already present.
    - Invalid slugs (not in `valid_topics`) are dropped silently.
    - Returns [`fallback`] if both inputs are empty after filtering.

    Pure / no I/O — kept separate from `tag_item()` so it's unit-testable.
    """
    assert isinstance(valid_topics, set), "valid_topics must be a set"
    out: list[str] = []
    for t in (seeded or []):
        slug = slugify(str(t))
        if slug in valid_topics and slug not in out:
            out.append(slug)
    for t in (parsed or [])[:2]:
        slug = slugify(str(t))
        if slug in valid_topics and slug not in out:
            out.append(slug)
    if not out:
        out = [fallback]
    return out


def tag_item(item: dict, topics: dict, tags_reg: dict) -> dict:
    valid_topics = set(topics.keys())
    top_tag_slugs = list(tags_reg.keys())
    prompt = build_prompt(item, topics, top_tag_slugs)
    raw = call_qwen(prompt)
    parsed = parse_response(raw)

    item_topics = merge_topics(
        item.get("topics"), parsed.get("topics"), valid_topics,
    )

    item_tags = []
    for t in (parsed.get("tags") or [])[:5]:
        slug = normalize_tag(str(t), tags_reg)
        if slug and slug not in item_tags:
            item_tags.append(slug)

    new_item = dict(item)
    new_item["topics"] = item_topics
    new_item["tags"] = item_tags
    return new_item


def main():
    if len(sys.argv) > 1 and sys.argv[1] != "-":
        items = json.loads(open(sys.argv[1]).read())
    else:
        items = json.loads(sys.stdin.read())
    if not isinstance(items, list):
        sys.stderr.write("expected JSON array of items\n")
        sys.exit(1)

    topics = load_topics()
    tags_raw = json.loads(TAGS_FILE.read_text())
    tags_reg = tags_raw.get("tags", {})

    out = []
    for i, item in enumerate(items):
        sys.stderr.write(f"  tagging {i+1}/{len(items)}: {item['title'][:60]}\n")
        out.append(tag_item(item, topics, tags_reg))

    save_tags(tags_reg, tags_raw)
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

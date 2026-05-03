#!/usr/bin/env python3
"""Detect clickbait titles and rewrite them in Techmeme style via local Qwen.

Reads a JSON array of items on stdin or argv. For each item:
  - Asks Qwen whether the title is clickbait/sensational/vague.
  - If yes, asks for a Techmeme-style rewrite grounded in the takeaway.
  - If no, leaves the item unchanged.

When rewritten, sets:
  item["original_title"] = <original>
  item["title"]          = <rewrite>
  item["title_rewritten"] = True

Emits the JSON array on stdout.
"""
import json
import os
import re
import subprocess
import sys

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL = os.environ.get("KW_REWRITE_MODEL", "qwen3.6:35b-a3b")


SYSTEM_PROMPT = """You are a headline editor in the style of Techmeme.

Techmeme rewrites publisher headlines so they read as terse, factual, detail-rich
abstracts. Apply these principles when rewriting:

  1. LEAD WITH THE ENTITY. Start with the project, product, company, or person
     (e.g. "Levels54", "QMK", "Raycast", "Kanata"). If the entity is unknown
     and cannot be inferred from the takeaway, lead with the artifact ("Split
     54-key keyboard...", "macOS utility...").
  2. STATE THE ACTION FACTUALLY. Use active verbs: releases, open-sources,
     ships, adds, fixes, announces, documents.
  3. NO SUPERLATIVES. Drop "peak", "best", "amazing", "incredible", "ultimate",
     "you won't believe", "THIS is...", emphatic ALL CAPS.
  4. NO SECOND-PERSON / NO RHETORICAL FRAMING. Drop "you", "I", "we", "my",
     "nobody asked for", "may not like it but...". Convert questions to
     declaratives only when the takeaway gives an answer; otherwise keep the
     question terse and specific.
  5. NO MYSTERY. Replace vague pronouns ("this", "it", "something") with the
     actual subject from the takeaway.
  6. PRESERVE FACTS. Keep proper names, version numbers, dollar amounts, dates
     exactly. Do NOT invent facts not present in the title or takeaway. If
     unsure, prefer a shorter rewrite or return the original.
  7. CONCISE. Aim for under ~90 characters. No trailing punctuation, no
     emoji, no exclamation marks.
  8. NEUTRAL TONE. No opinion, no hype, no editorializing.

Many titles are ALREADY in this style and should be left alone. Examples of
fine titles: "Levels54 is now fully open source", "March in Servo: keyboard
navigation, better debugging, FreeBSD support", "Keycap.app – Generate and
visualize keycap models". For these, return clickbait=false.

Clickbait / rewrite signals:
  - Sensational adjectives or ALL CAPS emphasis ("THIS is peak...")
  - Vague pronouns without antecedent ("This changes everything")
  - First/second person framing ("I made...", "How I solved...", "you won't...")
  - Curiosity gap / mystery ("the one trick", "what nobody tells you")
  - Opinion as fact ("borderline false advertising")
  - Trailing ellipses, multiple exclamation marks
  - Vague headlines missing the subject ("Connecting Issues", "Copilot Key on
    My Keyboard") — rewrite ONLY if the takeaway supplies the missing subject.
"""


USER_TEMPLATE = """Evaluate this aggregator item.

TITLE: {title}
TAKEAWAY: {takeaway}
SOURCE: {source}{sub}

Decide if the title is clickbait / sensational / vague / opinion-laden by the
rules above. If so, rewrite it in Techmeme style using ONLY facts present in
the title or takeaway. If not, return the original unchanged.

Return ONLY a JSON object on a single line, no prose, no code fences:
{{"clickbait": true|false, "title": "<original or rewrite>", "reason": "<one short clause; empty string if not rewritten>"}}"""


def build_prompt(item: dict) -> list[dict]:
    sub = ""
    if item.get("subreddit"):
        sub = f" (r/{item['subreddit']})"
    user = USER_TEMPLATE.format(
        title=item.get("title", ""),
        takeaway=item.get("takeaway") or "(none)",
        source=item.get("source") or "unknown",
        sub=sub,
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def call_qwen(messages: list[dict], timeout: int = 90) -> str:
    chat_url = OLLAMA_URL.rsplit("/api/", 1)[0] + "/api/chat"
    payload = json.dumps({
        "model": MODEL,
        "messages": messages,
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
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _significant_words(s: str) -> set[str]:
    return {w for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", s.lower())
            if w not in {
                "with", "from", "that", "this", "into", "open", "your",
                "have", "make", "made", "built", "build", "release", "released",
                "user", "post", "discussion", "reddit", "github",
            }}


def rewrite_item(item: dict) -> dict:
    original = item.get("title", "")
    if not original:
        return item
    takeaway = (item.get("takeaway") or "").strip()
    # Guardrail: refuse to rewrite when there's no factual ground (takeaway
    # missing). The model cannot fix vagueness without inventing.
    if not takeaway:
        return item

    raw = call_qwen(build_prompt(item))
    parsed = parse_response(raw)
    if not parsed:
        return item

    is_click = bool(parsed.get("clickbait"))
    new_title = (parsed.get("title") or "").strip()
    reason = (parsed.get("reason") or "").strip()

    # Sanity guards: no rewrite if model returned empty, identical, or absurd.
    if not is_click or not new_title or new_title == original:
        return item
    if len(new_title) > 200:
        return item

    # Word-overlap guard: rewrite must share at least one significant word with
    # original OR takeaway, else the model is hallucinating. Catches things
    # like "dual-trackboard" coined from "dual-trackball" with zero anchoring
    # text.
    src_words = _significant_words(original) | _significant_words(takeaway)
    new_words = _significant_words(new_title)
    if src_words and not (src_words & new_words):
        return item

    new_title = new_title.rstrip("!.")

    out = dict(item)
    out["original_title"] = original
    out["title"] = new_title
    out["title_rewritten"] = True
    if reason:
        out["title_rewrite_reason"] = reason
    return out


def main():
    if len(sys.argv) > 1 and sys.argv[1] != "-":
        items = json.loads(open(sys.argv[1]).read())
    else:
        items = json.loads(sys.stdin.read())
    if not isinstance(items, list):
        sys.stderr.write("expected JSON array of items\n")
        sys.exit(1)

    out = []
    for i, item in enumerate(items):
        sys.stderr.write(
            f"  rewriting {i+1}/{len(items)}: {item.get('title','')[:60]}\n"
        )
        out.append(rewrite_item(item))

    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

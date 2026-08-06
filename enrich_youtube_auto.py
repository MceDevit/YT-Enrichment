#!/usr/bin/env python3
"""
enrich_youtube_auto.py — fully automatic run, no questions asked.

Reads a plain-Markdown settings file (default: _youtube_settings.md in your
vault root) that says whether to use Claude summaries, and picks a per-topic
"focus" for the summary by matching keywords against the video's title.

Edit the settings file itself (in Obsidian, like any other note) to change
behavior — no need to touch this script or answer prompts at runtime.

Settings file format:

    use_claude: yes
    transcript_retries: 0   # 0-2; how many times to retry a rate-limited (429)
                            # transcript fetch, with backoff (15s, then 30s). Default 0 = no retries.

    ## Default
    focus: <text used when nothing else matches>

    ## Some Topic
    keywords: word one, word two, word three
    focus: <text used when the video title contains any of those keywords>

    ## Another Topic
    keywords: ...
    focus: ...

    ## Summary Prompt
    <optional; overrides the editorial instructions in the summary prompt
    for regular (non-Short) videos — bullet count, prioritization, verdict
    criteria, tone, etc. Leave the section out to keep the built-in default.>

    ## Short Summary Prompt
    <optional; same idea but for YouTube Shorts (no verdict is requested
    for Shorts either way).>

First matching topic section (in file order, top to bottom, excluding
Default, Summary Prompt, and Short Summary Prompt) wins. Default is used if
nothing matches.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enrich_youtube as core  # reuses VAULT/INBOX/REVIEWED/main()

SETTINGS_FILE = core.VAULT / "_youtube_settings.md"

SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


TRANSCRIPT_RETRY_DELAYS = (15, 30)  # available backoff steps; transcript_retries picks how many to use


PROMPT_SECTIONS = {"summary prompt", "short summary prompt"}


def parse_settings(text):
    """Returns (use_claude: bool, max_transcript_minutes: int|None, transcript_retries: int|None, model_summary: str|None, model_reformat: str|None, summary_instructions: str|None, short_summary_instructions: str|None, sections: list[dict{name, keywords, focus}])."""
    use_claude_m = re.search(r"^use_claude:\s*(\S+)", text, re.MULTILINE | re.IGNORECASE)
    use_claude = bool(use_claude_m and use_claude_m.group(1).lower() in ("yes", "true", "on", "1"))

    max_minutes_m = re.search(r"^max_transcript_minutes:\s*(\d+)", text, re.MULTILINE | re.IGNORECASE)
    max_transcript_minutes = int(max_minutes_m.group(1)) if max_minutes_m else None

    retries_m = re.search(r"^transcript_retries:\s*(\d+)", text, re.MULTILINE | re.IGNORECASE)
    transcript_retries = int(retries_m.group(1)) if retries_m else None

    model_summary_m = re.search(r"^model_summary:\s*(\S+)", text, re.MULTILINE | re.IGNORECASE)
    model_summary = model_summary_m.group(1).strip() if model_summary_m else None

    model_reformat_m = re.search(r"^model_reformat:\s*(\S+)", text, re.MULTILINE | re.IGNORECASE)
    model_reformat = model_reformat_m.group(1).strip() if model_reformat_m else None

    # split into (name, body) per "## Heading" section
    headers = list(SECTION_RE.finditer(text))
    sections = []
    for i, h in enumerate(headers):
        name = h.group(1).strip()
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[start:end]

        kw_m = re.search(r"^keywords:\s*(.+)$", body, re.MULTILINE | re.IGNORECASE)
        keywords = [k.strip().lower() for k in kw_m.group(1).split(",")] if kw_m else []

        focus_m = re.search(r"^focus:\s*(.+(?:\n(?!\S+:).+)*)", body, re.MULTILINE | re.IGNORECASE)
        focus = focus_m.group(1).strip() if focus_m else ""

        sections.append({"name": name, "keywords": keywords, "focus": focus, "body": body})

    def reserved_body(name):
        body = next((s["body"].strip() for s in sections if s["name"].lower() == name), "")
        return body or None

    summary_instructions = reserved_body("summary prompt")
    short_summary_instructions = reserved_body("short summary prompt")
    # "Default"/topic sections stay in `sections` for make_focus_getter — only
    # drop the two prompt-override sections, which aren't keyword/focus topics.
    sections = [s for s in sections if s["name"].lower() not in PROMPT_SECTIONS]

    return (use_claude, max_transcript_minutes, transcript_retries, model_summary, model_reformat,
            summary_instructions, short_summary_instructions, sections)


def make_focus_getter(sections):
    default_focus = next((s["focus"] for s in sections if s["name"].lower() == "default"), None)
    topic_sections = [s for s in sections if s["name"].lower() != "default"]

    def get_focus(meta):
        haystack = (meta.get("title", "") + " " + meta.get("channel", "")).lower()
        for s in topic_sections:
            if any(kw and kw in haystack for kw in s["keywords"]):
                print(f'    (matched topic: {s["name"]})')
                return s["focus"] or None
        return default_focus or None

    return get_focus


def main():
    if not SETTINGS_FILE.exists():
        print(f"No settings file found at:\n  {SETTINGS_FILE}")
        print("Create it (see _youtube_settings.md example) or run enrich_youtube.py directly.")
        return

    text = SETTINGS_FILE.read_text(encoding="utf-8")
    (use_claude, max_transcript_minutes, transcript_retries, model_summary, model_reformat,
     summary_instructions, short_summary_instructions, sections) = parse_settings(text)

    core.USE_CLAUDE = use_claude
    if max_transcript_minutes is not None:
        core.MAX_TRANSCRIPT_SECONDS = max_transcript_minutes * 60
    retries = transcript_retries if transcript_retries is not None else 0
    core.TRANSCRIPT_RETRY_DELAYS = TRANSCRIPT_RETRY_DELAYS[:retries]
    if model_summary:
        core.CLAUDE_MODEL = model_summary
    if model_reformat:
        core.REFORMAT_MODEL = model_reformat
    if summary_instructions:
        core.SUMMARY_INSTRUCTIONS = summary_instructions
    if short_summary_instructions:
        core.SHORT_SUMMARY_INSTRUCTIONS = short_summary_instructions

    print(f"Settings loaded from {SETTINGS_FILE.name}")
    print(f"Summaries: {'ON' if use_claude else 'OFF'}")
    print(f"Max transcript length: {core.MAX_TRANSCRIPT_SECONDS // 60} min")
    print(f"Transcript rate-limit retries: {len(core.TRANSCRIPT_RETRY_DELAYS)}")
    print(f"Summary model: {core.CLAUDE_MODEL}")
    print(f"Reformat model: {core.REFORMAT_MODEL}")
    if summary_instructions:
        print("Summary prompt: overridden from settings")
    if short_summary_instructions:
        print("Short summary prompt: overridden from settings")
    if use_claude:
        names = ", ".join(s["name"] for s in sections if s["name"].lower() != "default")
        print(f"Topics configured: {names or '(none — Default only)'}\n")
    else:
        print()

    core.main(focus_getter=make_focus_getter(sections) if use_claude else None)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
enrich_youtube.py — drain your Obsidian "YouTube/Inbox" and produce clean notes.

Pipeline:
  1. Capture (any device, any way) drops a YouTube URL into the Inbox — either
     as a line in `_links.md`, or inside any .md note (frontmatter `url:` or a
     bare link in the body). This script doesn't care how it got there.
  2. This script reads each unprocessed URL, fetches metadata + transcript with
     yt-dlp, writes your frontmatter, optionally adds a Claude summary, marks it
     `processed: true`, and moves the finished note to Reviewed.

Safe to run any time: it skips notes already marked `processed: true`
(idempotent), so a cron/launchd schedule or a hotkey both work.

Requires: yt-dlp  (brew install yt-dlp)  and Python 3.9+ (requests).
Claude summary is optional — set USE_CLAUDE=True and export ANTHROPIC_API_KEY.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None  # only needed if USE_CLAUDE is True

# ------------------------------------------------------------------ config ----
VAULT       = Path(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/MyVault"
).expanduser()  # <-- your iCloud vault root (Obsidian's own iCloud container)
INBOX       = VAULT                          # new note files land directly in the vault root
REVIEWED    = VAULT / "Reviewed"
LINKS_FILE  = INBOX / "_links.md"          # optional: one URL per line, only used if present
LANG        = "en,fr"                       # preferred transcript language(s), comma-separated
STATUS_DONE = "reviewed"                    # frontmatter status after enrich
MOVE_TO_REVIEWED = True                     # False = keep enriched notes in Inbox

USE_CLAUDE  = False                         # True to add an AI summary
# Model names change over time — confirm current strings at
# https://docs.claude.com/en/docs/about-claude/models
CLAUDE_MODEL = "claude-sonnet-5"
CLAUDE_MAX_TOKENS = 700
# -----------------------------------------------------------------------------

YT_RE = re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com/watch\?[^\s)]+|youtu\.be/[\w-]+)")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def fetch_meta(url):
    """Title, channel, duration, video id via yt-dlp (no download)."""
    r = run(["yt-dlp", "-J", "--skip-download", url])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr else "yt-dlp failed")
    d = json.loads(r.stdout)
    return {
        "id": d.get("id", ""),
        "title": d.get("title", "Untitled"),
        "channel": d.get("uploader") or d.get("channel", ""),
        "channel_id": d.get("channel_id") or d.get("uploader_id") or "",
        "duration": fmt_duration(d.get("duration") or 0),
        "url": d.get("webpage_url", url),
    }


def fetch_transcript(url):
    """Return plain-text transcript, or '' if none. Uses yt-dlp json3 captions."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "%(id)s.%(ext)s")
        r = run([
            "yt-dlp", "--skip-download",
            "--write-subs", "--write-auto-subs",
            "--sub-langs", LANG, "--sub-format", "json3",
            "-o", out, url,
        ])
        files = [f for f in Path(tmp).iterdir() if f.suffix == ".json3"]
        if not files:
            if r.returncode != 0 and r.stderr.strip():
                print(f"  ! transcript fetch failed: {r.stderr.strip().splitlines()[-1]}", file=sys.stderr)
            return ""
        data = json.loads(files[0].read_text(encoding="utf-8"))
        parts = []
        for ev in data.get("events", []):
            if not ev.get("segs"):
                continue
            text = "".join(s.get("utf8", "") for s in ev["segs"]).replace("\n", " ").strip()
            if text:
                parts.append(text)
        return " ".join(parts)


def fmt_duration(seconds):
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def claude_summary(title, transcript, focus=None):
    if not (USE_CLAUDE and transcript):
        return ""
    if requests is None:
        print("  ! requests not installed; skipping summary", file=sys.stderr)
        return ""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("  ! ANTHROPIC_API_KEY not set; skipping summary", file=sys.stderr)
        return ""
    focus_line = f" Pay particular attention to: {focus}.\n\n" if focus else "\n\n"
    prompt = (
        f'Summarize this YouTube video titled "{title}" in 4-6 short, tight bullet points.'
        + focus_line +
        "Prioritize, in this order of importance:\n"
        "1. Technical takeaways — tools, techniques, code patterns, or concrete methods shown.\n"
        "2. Actionable steps I could apply myself.\n"
        "3. Any specific links, tools, libraries, or resources mentioned by name.\n\n"
        "End with one final bullet: a one-line verdict on whether it's worth watching in full, "
        "and why or why not.\n\n"
        "Keep each bullet tight — one line where possible. No preamble, no restating the title.\n\n"
        "Transcript:\n\n" + transcript[:100_000]
    )
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": CLAUDE_MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        blocks = resp.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    except Exception as e:
        print(f"  ! summary failed: {e}", file=sys.stderr)
        return ""


def sanitize(name):
    name = re.sub(r'[\\/:*?"<>|#^\[\]]', "", name)
    return re.sub(r"\s+", " ", name).strip()[:120] or "video"


def build_note(meta, transcript, summary):
    fm = [
        "---",
        f'title: "{meta["title"].replace(chr(34), chr(39))}"',
        f'channel: "{meta["channel"].replace(chr(34), chr(39))}"',
        f'channel_id: {meta.get("channel_id", "")}',
        f'url: {meta["url"]}',
        f'duration: {meta["duration"]}',
        f"date_watched: {date.today().isoformat()}",
        "tags: [youtube]",
        f"status: {STATUS_DONE}",
        "processed: true",
        "---",
        "",
    ]
    if summary:
        fm += ["## Summary", "", summary, ""]
    fm += ["## My notes", "- ", ""]
    fm += ["## Transcript", "", transcript or "_No transcript available._", ""]
    return "\n".join(fm)


def already_processed(text):
    return re.search(r"^processed:\s*true\s*$", text, re.MULTILINE) is not None


def collect_urls():
    """Yield (url, source_path_or_None). Sources: _links.md lines, and .md notes."""
    seen = set()

    # 1) queue file: one URL per line
    if LINKS_FILE.exists():
        for line in LINKS_FILE.read_text(encoding="utf-8").splitlines():
            m = YT_RE.search(line)
            if m and m.group(0) not in seen:
                seen.add(m.group(0))
                yield m.group(0), None

    # 2) stub / clipped notes already in the inbox
    for note in sorted(INBOX.glob("*.md")):
        if note.name == "_links.md":
            continue
        text = note.read_text(encoding="utf-8")
        if already_processed(text):
            continue
        m = YT_RE.search(text)
        if m and m.group(0) not in seen:
            seen.add(m.group(0))
            yield m.group(0), note


def main(focus_getter=None):
    """
    focus_getter: optional callable(meta) -> str | None
    Called once per video, right before summarizing, so a caller (e.g. the
    interactive wrapper) can ask "any particular focus for this one?" and
    steer that video's summary. Ignored if USE_CLAUDE is False.
    """
    INBOX.mkdir(parents=True, exist_ok=True)
    REVIEWED.mkdir(parents=True, exist_ok=True)

    items = list(collect_urls())
    if not items:
        print("Inbox empty — nothing to enrich.")
        return

    print(f"Found {len(items)} video(s) to process.\n")
    done_urls = []

    for url, src in items:
        print(f"• {url}")
        try:
            meta = fetch_meta(url)
            transcript = fetch_transcript(url)
            focus = focus_getter(meta) if focus_getter else None
            summary = claude_summary(meta["title"], transcript, focus=focus)
            body = build_note(meta, transcript, summary)

            dest_dir = REVIEWED if MOVE_TO_REVIEWED else INBOX
            dest = dest_dir / f"{sanitize(meta['title'])}.md"
            dest.write_text(body, encoding="utf-8")
            print(f"  → {dest.relative_to(VAULT)}  "
                  f"({'summary, ' if summary else ''}"
                  f"{len(transcript.split())} transcript words)")

            # clean up the source stub if it lived in the inbox
            if src and src.exists() and src.resolve() != dest.resolve():
                src.unlink()
            done_urls.append(url)
        except Exception as e:
            print(f"  ! failed: {e}", file=sys.stderr)

    # prune processed lines from the queue file
    if LINKS_FILE.exists() and done_urls:
        kept = [l for l in LINKS_FILE.read_text(encoding="utf-8").splitlines()
                if not any(u in l for u in done_urls)]
        LINKS_FILE.write_text("\n".join(kept).strip() + ("\n" if kept else ""), encoding="utf-8")

    print(f"\nDone. Enriched {len(done_urls)} of {len(items)}.")


if __name__ == "__main__":
    main()

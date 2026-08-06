#!/usr/bin/env python3
"""
enrich_youtube.py — drain your Obsidian "YouTube/Inbox" and produce clean notes.

Pipeline:
  1. Capture (any device, any way) drops a YouTube URL into the Inbox — either
     as a line in `_links.md`, or inside any .md note (frontmatter `url:` or a
     bare link in the body). This script doesn't care how it got there.
  2. This script reads each unprocessed URL, fetches metadata + transcript with
     yt-dlp, writes your frontmatter, optionally cleans the transcript and adds
     a Claude summary, marks it `processed: true`, and moves the finished note
     to Reviewed.

Safe to run any time: it skips notes already marked `processed: true`
(idempotent), so a cron/launchd schedule or a hotkey both work.

Requires: yt-dlp (brew install yt-dlp) for transcripts, and Python 3.9+ (requests).
Metadata (title/channel/duration) comes from the YouTube Data API v3 — get a
free key at https://console.cloud.google.com/apis/credentials and export it
as YOUTUBE_API_KEY.
Claude summary is optional — set USE_CLAUDE=True and export ANTHROPIC_API_KEY.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

import requests

from reformat_transcript import reformat_transcript

# ------------------------------------------------------------------ config ----
VAULT_PATH_FILE = Path(__file__).resolve().parent / "vault_path.txt"


def _load_vault():
    if not VAULT_PATH_FILE.exists():
        raise RuntimeError(
            f"No vault configured — create {VAULT_PATH_FILE.name} next to this script "
            "with your vault's full path on one line, e.g.:\n"
            "  ~/Library/Mobile Documents/iCloud~md~obsidian/Documents/MyVault"
        )
    raw = VAULT_PATH_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        raise RuntimeError(f"{VAULT_PATH_FILE.name} is empty — put your vault's path in it")
    return Path(raw).expanduser()


VAULT       = _load_vault()                  # <-- your vault root, configured in vault_path.txt
INBOX       = VAULT                          # new note files land directly in the vault root
REVIEWED    = VAULT / "Reviewed"
CLIPPINGS   = VAULT / "Clippings"            # Obsidian Web Clipper's output folder — also scanned,
                                              # since it can capture YouTube notes with their own
                                              # transcript already attached (see extract_existing_transcript)
LINKS_FILE  = INBOX / "_links.md"          # optional: one URL per line, only used if present
LANG        = "en,fr"                       # fallback transcript language(s) when the video's own
                                              # default audio language isn't reported by the API
MAX_TRANSCRIPT_SECONDS = 3600                # skip transcript fetch for videos longer than this (movies, etc.)
STATUS_DONE = "reviewed"                    # frontmatter status after enrich
MOVE_TO_REVIEWED = True                     # False = keep enriched notes in Inbox

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")  # required — see module docstring

USE_CLAUDE  = False                         # True to add an AI summary
# Model names change over time — confirm current strings at
# https://docs.claude.com/en/docs/about-claude/models
CLAUDE_MODEL = "claude-sonnet-5"            # used for the summary + verdict
REFORMAT_MODEL = "claude-sonnet-5"          # used for transcript cleanup
CLAUDE_MAX_TOKENS = 1500                    # 700 was too tight — hit the cap on an
                                              # ordinary 15min video and dropped the verdict
# -----------------------------------------------------------------------------

YT_RE = re.compile(r'https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s)"\']+|shorts/[\w-]+)|youtu\.be/[\w-]+)')
VIDEO_ID_RE = re.compile(r"(?:youtu\.be/|watch\?v=|shorts/)([\w-]+)")
DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def parse_iso8601_duration(duration):
    """'PT1H2M3S' -> seconds. Missing components default to 0."""
    m = DURATION_RE.fullmatch(duration or "")
    if not m:
        return 0
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + s


def fetch_meta(url):
    """Title, channel, duration, video id via the YouTube Data API v3."""
    if not YOUTUBE_API_KEY:
        raise RuntimeError(
            "YOUTUBE_API_KEY not set — get a free key at "
            "https://console.cloud.google.com/apis/credentials and export it"
        )
    id_match = VIDEO_ID_RE.search(url)
    if not id_match:
        raise RuntimeError(f"couldn't extract a video id from {url}")
    video_id = id_match.group(1)

    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "snippet,contentDetails", "id": video_id, "key": YOUTUBE_API_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        raise RuntimeError(f"video not found or private: {video_id}")

    snippet = items[0]["snippet"]
    duration_s = parse_iso8601_duration(items[0]["contentDetails"]["duration"])
    return {
        "id": video_id,
        "language": snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage"),
        "title": snippet.get("title", "Untitled"),
        "channel": snippet.get("channelTitle", ""),
        "channel_id": snippet.get("channelId", ""),
        "duration": fmt_duration(duration_s),
        "duration_seconds": duration_s,
        "url": f"https://youtu.be/{video_id}",
    }


TRANSCRIPT_RETRY_DELAYS = ()  # seconds to wait after each 429 before retrying; set via
                               # transcript_retries in _youtube_settings.md (default: no retries)


class TranscriptRateLimited(Exception):
    """Raised when yt-dlp still hits a 429 after all retries are exhausted."""


def fetch_transcript(url, language=None):
    """Return plain-text transcript, or '' if no captions exist.

    `language` is the video's own default audio language (from the YouTube
    Data API, when available). When set, we request only that language
    instead of the full LANG fallback list — for a non-English video like a
    French one, requesting "en,fr" makes yt-dlp fetch its native `fr` track
    *and* YouTube's auto-translated `en` track, and that translation
    endpoint is throttled harder than native captions. Requesting just `fr`
    skips the translated track entirely, roughly halving requests and
    avoiding the harsher-throttled endpoint — this is the main fix for 429s
    that cluster on non-English videos. Falls back to LANG when the API
    didn't report a language.

    The API reports BCP-47 codes like "fr-FR", but yt-dlp's caption track
    codes are bare ("fr", "fr-orig") — matching the raw API value against
    those finds nothing and silently returns an empty transcript. So we
    strip the region suffix and anchor the match to the exact base code,
    which also avoids incidentally picking up "fr-orig" alongside "fr".

    Raises TranscriptRateLimited if every attempt was rejected with a 429 —
    callers should leave the item untouched so it's retried on the next run,
    rather than finalizing a note with a permanently-missing transcript.
    """
    if language:
        base_lang = language.split("-")[0]
        sub_langs = f"^{base_lang}$"
    else:
        sub_langs = LANG
    delays = list(TRANSCRIPT_RETRY_DELAYS) + [None]  # None = last attempt, no more retries
    for attempt, delay_after_failure in enumerate(delays):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "%(id)s.%(ext)s")
            r = run([
                "yt-dlp", "--skip-download",
                "--write-subs", "--write-auto-subs",
                "--sub-langs", sub_langs, "--sub-format", "json3",
                "--sleep-subtitles", "2",
                "-o", out, url,
            ])
            files = [f for f in Path(tmp).iterdir() if f.suffix == ".json3"]
            if not files:
                rate_limited = "429" in r.stderr
                if r.returncode != 0 and r.stderr.strip():
                    print(f"  ! transcript fetch failed: {r.stderr.strip().splitlines()[-1]}", file=sys.stderr)
                if rate_limited and delay_after_failure is not None:
                    print(f"  ! rate limited — retrying in {delay_after_failure}s "
                          f"(attempt {attempt + 2}/{len(delays)})", file=sys.stderr)
                    time.sleep(delay_after_failure)
                    continue
                if rate_limited:
                    raise TranscriptRateLimited(url)
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


def claude_summary(title, transcript, focus=None, language=None, is_short=False):
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
    # French-language videos get a French summary/verdict — the VERDICT:
    # prefix itself stays in English (extract_verdict/VERDICT_RE match on
    # it literally), only the leading yes/no/maybe word and reason after it
    # switch language, since verdict_callout() maps both.
    is_french = bool(language) and language.split("-")[0].lower() == "fr"
    language_line = (
        "Write the summary bullets and the verdict reason in French, since this "
        "video is in French. Keep the literal 'VERDICT: ' prefix in English, but "
        "follow it with Oui/Non/Peut-être and a French reason "
        "(e.g. 'VERDICT: Oui — raison').\n\n"
        if is_french else ""
    )
    if is_short:
        # Shorts are already quick to watch, so a "worth watching in full?"
        # verdict is pointless — just summarize.
        prompt = (
            f'Summarize this YouTube Short titled "{title}" in 1-3 short, tight bullet points, '
            "capturing just the core idea or takeaway."
            + focus_line + language_line +
            "No verdict — don't judge whether it's worth watching, Shorts are short enough "
            "already. Keep each bullet tight — one line where possible. No preamble, no "
            "restating the title.\n\n"
            "Transcript:\n\n" + transcript[:100_000]
        )
    else:
        prompt = (
            f'Summarize this YouTube video titled "{title}" in 4-6 short, tight bullet points.'
            + focus_line + language_line +
            "Prioritize, in this order of importance:\n"
            "1. Technical takeaways — tools, techniques, code patterns, or concrete methods shown.\n"
            "2. Actionable steps I could apply myself.\n"
            "3. Any specific links, tools, libraries, or resources mentioned by name.\n\n"
            "End with one final bullet: a one-line verdict on whether it's worth watching in full — "
            "judged against my stated interest if I gave one, otherwise judge generally — "
            "and why or why not.\n\n"
            "Be decisive. Default to Yes or No — only use Maybe if the video is a genuine "
            "toss-up (e.g. good content but a format you may not enjoy). Don't use Maybe just to "
            "hedge. If the video is mostly filler, hype, opinion without substance, or this "
            "summary already captures everything of value so the full video adds little, "
            "say No plainly and say why. Be critical — most videos are not worth watching in "
            "full even if they're fine to summarize.\n\n"
            "Keep each bullet tight — one line where possible. No preamble, no restating the title. "
            "Don't describe the summary's position in the note (e.g. 'above' or 'below') — refer "
            "to it only as 'this summary'.\n\n"
            "After the bullets, on its own line, repeat just that verdict prefixed with 'VERDICT: ' "
            "(e.g. 'VERDICT: Yes — reason' or 'VERDICT: No — reason' or 'VERDICT: Maybe — reason').\n\n"
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
        data = resp.json()
        blocks = data.get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if data.get("stop_reason") == "max_tokens":
            # The VERDICT line is appended at the very end of the response, so a
            # truncated response is likely to have cut it off mid-sentence rather
            # than dropped it cleanly — VERDICT_RE would still match that partial
            # fragment and extract_verdict() would show a cut-off callout as if it
            # were a real verdict. Strip any trailing (possibly partial) VERDICT
            # line so no callout is shown at all rather than a broken one.
            print(f"  ! summary hit the {CLAUDE_MAX_TOKENS}-token cap; dropping any "
                  "partial verdict line", file=sys.stderr)
            text = re.sub(r"\n?[\s>*•\-]*VERDICT:.*$", "", text,
                           flags=re.IGNORECASE | re.DOTALL).strip()
            text += "\n\n_(summary truncated — hit the token limit before the verdict)_"
        return text
    except Exception as e:
        print(f"  ! summary failed: {e}", file=sys.stderr)
        return ""


VERDICT_RE = re.compile(r"^[\s>*•\-]*VERDICT:\s*(.+?)\**\s*$", re.MULTILINE | re.IGNORECASE)


def extract_verdict(summary):
    """Pulls the 'VERDICT: ...' line out of a summary. Returns (verdict, summary_without_it).

    Tolerant of leading bullet/blockquote/bold markup (some models, e.g. Haiku,
    fold the verdict into the last bullet — '• **VERDICT: ...**' — rather than
    putting it on its own bare line as instructed).
    """
    m = VERDICT_RE.search(summary or "")
    if not m:
        return None, summary
    verdict = m.group(1).strip()
    cleaned = VERDICT_RE.sub("", summary).strip()
    return verdict, cleaned


def sanitize(name):
    name = re.sub(r'[\\/:*?"<>|#^\[\]]', "", name)
    return re.sub(r"\s+", " ", name).strip()[:120] or "video"


VERDICT_CALLOUT_RE = re.compile(r"^\s*(yes|maybe|no|oui|peut-être|non)\b", re.IGNORECASE)
VERDICT_CALLOUTS = {
    "yes": "success", "maybe": "warning", "no": "danger",
    "oui": "success", "peut-être": "warning", "non": "danger",
}


def verdict_callout(verdict):
    """Maps a verdict's leading Yes/Maybe/No to an Obsidian callout type
    (green/orange/red respectively). Falls back to 'danger' if unrecognized."""
    m = VERDICT_CALLOUT_RE.match(verdict or "")
    return VERDICT_CALLOUTS.get(m.group(1).lower(), "danger") if m else "danger"


def build_note(meta, transcript, summary, transcript_note=None, verdict=None,
                reformatted=False, summarized=False, transcript_done_at=None, is_short=False):
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
    ]
    if transcript_done_at:
        fm.append(f"transcript_done: {transcript_done_at}")
    if reformatted:
        fm.append(f"model_reformat: {REFORMAT_MODEL}")
    if summarized:
        fm.append(f"model_summary: {CLAUDE_MODEL}")
    fm += ["---", ""]
    if verdict:
        fm += [f"> [!{verdict_callout(verdict)}] Worth watching? {verdict}", ""]
    elif is_short:
        fm += ["_Short video — no worth-watching verdict needed._", ""]
    if summary:
        fm += ["## Summary", "", summary, ""]
    fm += ["## My notes", "- ", ""]
    fm += ["## Transcript", "", transcript or transcript_note or "_No transcript available._", ""]
    return "\n".join(fm)


def already_processed(text):
    return re.search(r"^processed:\s*true\s*$", text, re.MULTILINE) is not None


RATE_LIMIT_MARKER = "> [!warning] Transcript rate-limited (429) — will retry automatically next run"
FRONTMATTER_RE = re.compile(r"^(---\n.*?\n---\n)", re.DOTALL)


def mark_rate_limited(src):
    """Prepend a visible warning to a stub note so a stuck 429 is obvious at a glance."""
    text = src.read_text(encoding="utf-8")
    if RATE_LIMIT_MARKER in text:
        return
    m = FRONTMATTER_RE.match(text)
    insert_at = m.end() if m else 0
    marker_block = f"{RATE_LIMIT_MARKER}\n\n"
    src.write_text(text[:insert_at] + marker_block + text[insert_at:], encoding="utf-8")


def extract_existing_transcript(text):
    """If a stub note already has a '## Transcript' section with real content
    (e.g. pasted in manually, or from a browser extension), return it so the
    pipeline can reuse it instead of re-fetching via yt-dlp. Returns '' if
    there's no such section or it's empty/placeholder text.
    """
    m = re.search(r"^## Transcript\s*\n+(.*)", text, re.MULTILINE | re.DOTALL)
    if not m:
        return ""
    content = m.group(1)
    next_h2 = re.search(r"\n## ", content)
    if next_h2:
        content = content[:next_h2.start()]
    content = content.strip()
    if content.startswith("_") and content.endswith("_") and len(content) < 60:
        return ""  # our own "_No transcript available._" / "_...skipped..._" placeholders
    return content


def collect_urls():
    """Yield (url, source_path_or_None). Sources: _links.md lines, .md notes in the
    inbox, and .md notes in Clippings/ (Obsidian Web Clipper's output folder)."""
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

    # 3) Web Clipper notes in Clippings/ — same idea, but these never carry our
    # own `processed: true` frontmatter (Web Clipper writes its own frontmatter
    # shape: `source:` instead of `url:`, `tags: [clippings]`), so there's no
    # already_processed() check here — a Clippings note is consumed and deleted
    # by main() the same way an inbox stub is, so it only gets picked up once.
    if CLIPPINGS.exists():
        for note in sorted(CLIPPINGS.glob("*.md")):
            text = note.read_text(encoding="utf-8")
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
            is_short = "/shorts/" in url
            transcript_note = None
            reformatted = False
            transcript_done_at = None

            existing_transcript = ""
            if src is not None:
                existing_transcript = extract_existing_transcript(src.read_text(encoding="utf-8"))

            if existing_transcript:
                print(f"  (transcript already in the note — using it, skipping fetch)")
                transcript = existing_transcript
            elif meta["duration_seconds"] > MAX_TRANSCRIPT_SECONDS:
                print(f"  (skipping transcript — {meta['duration']} exceeds "
                      f"{MAX_TRANSCRIPT_SECONDS // 60}min limit)")
                transcript = ""
                transcript_note = "_Transcript skipped — video exceeds the length limit._"
            else:
                transcript = fetch_transcript(url, language=meta.get("language"))

            if transcript:
                if USE_CLAUDE and not existing_transcript:
                    cleaned = reformat_transcript(transcript, model=REFORMAT_MODEL)
                    if cleaned:
                        transcript = cleaned
                        reformatted = True
                transcript_done_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            focus = focus_getter(meta) if focus_getter else None
            summary = claude_summary(meta["title"], transcript, focus=focus,
                                      language=meta.get("language"), is_short=is_short)
            summarized = bool(summary)
            verdict, summary = extract_verdict(summary) if summary else (None, summary)
            body = build_note(meta, transcript, summary, transcript_note=transcript_note, verdict=verdict,
                               reformatted=reformatted, summarized=summarized,
                               transcript_done_at=transcript_done_at, is_short=is_short)

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
        except TranscriptRateLimited:
            print("  ! still rate-limited after retries — flagging note, "
                  "will retry on the next run", file=sys.stderr)
            if src and src.exists():
                mark_rate_limited(src)
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

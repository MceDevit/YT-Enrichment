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
free key at https://console.cloud.google.com/apis/credentials and put it in
.env (see env_setup.py) as YOUTUBE_API_KEY.
Claude summary is optional — set USE_CLAUDE=True and put ANTHROPIC_API_KEY in .env.
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

from claude_api import ClaudeError, call_claude, estimate_cost
from reformat_transcript import reformat_transcript
from translate_transcript import translate_transcript

# ------------------------------------------------------------------ config ----
VAULT_PATH_FILE = Path(__file__).resolve().parent / "vault_path.txt"


def _load_vault():
    # The env var lets the module be imported without a configured vault —
    # test_enrich.py sets it, since vault_path.txt is gitignored and a fresh
    # clone would otherwise fail at import time and be unable to run the tests.
    override = os.environ.get("YT_ENRICH_VAULT")
    if override:
        return Path(override).expanduser()
    if not VAULT_PATH_FILE.exists():
        raise RuntimeError(
            f"No vault configured — create {VAULT_PATH_FILE.name} next to this script "
            "with your vault's full path on one line, e.g.:\n"
            "  ~/Library/Mobile Documents/iCloud~md~obsidian/Documents/MyVault\n"
            "(or set YT_ENRICH_VAULT to override it for one run)"
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
STATUS_ATTENTION = "needs-attention"        # ...unless something degraded; search this in Obsidian
MOVE_TO_REVIEWED = True                     # False = keep enriched notes in Inbox

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")  # required — see module docstring

USE_CLAUDE  = False                         # True to add an AI summary
# Model names change over time — confirm current strings at
# https://docs.claude.com/en/docs/about-claude/models
CLAUDE_MODEL = "claude-sonnet-5"            # used for the summary + verdict
REFORMAT_MODEL = "claude-haiku-4-5-20251001"  # used for transcript cleanup
TRANSLATE_MODEL = "claude-haiku-4-5-20251001"  # used for non-en/fr -> French transcript translation
CLAUDE_MAX_TOKENS = 1500                    # 700 was too tight — hit the cap on an
                                              # ordinary 15min video and dropped the verdict
# -----------------------------------------------------------------------------

YT_RE = re.compile(r'https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s)"\']+|shorts/[\w-]+)|youtu\.be/[\w-]+)')
VIDEO_ID_RE = re.compile(r"(?:youtu\.be/|shorts/)([\w-]+)|[?&]v=([\w-]+)")
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
    video_id = id_match.group(1) or id_match.group(2)

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


def transcript_target_lang(language):
    """The transcript's desired *final* language — not what we ask yt-dlp
    for (fetch_transcript always requests the video's own native track; see
    its docstring for why), but what translate_transcript() should turn it
    into afterwards via Claude. English and French videos are left alone;
    anything else targets French. Returns None when the API didn't report a
    language, meaning we can't tell and skip translation.
    """
    if not language:
        return None
    base_lang = language.split("-")[0].lower()
    return base_lang if base_lang in ("en", "fr") else "fr"


def fetch_transcript(url, language=None):
    """Return plain-text transcript, or '' if no captions exist.

    `language` is the video's own default audio language (from the YouTube
    Data API, when available). We request only that single native language
    instead of the full LANG fallback list — for a non-English video like a
    French one, requesting "en,fr" makes yt-dlp fetch its native `fr` track
    *and* YouTube's auto-translated `en` track, and that translation
    endpoint is throttled harder than native captions. Requesting just `fr`
    skips the translated track entirely, roughly halving requests and
    avoiding the harsher-throttled endpoint — this is the main fix for 429s
    that cluster on non-English videos. This function NEVER requests a
    translated track, even when the caller wants a French transcript out of
    e.g. a Portuguese video — that translation happens afterwards via
    Claude (see translate_transcript()), specifically to keep this function
    from ever touching the throttled endpoint. Falls back to LANG when the
    API didn't report a language.

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


# Editorial instructions for the summary prompt (bullet-count prioritization,
# verdict criteria, tone, ...) — NOT set here. enrich_youtube_auto.py loads
# these from _youtube_settings.md's "## Summary Prompt" / "## Short Summary
# Prompt" sections and assigns them onto this module before calling main(),
# same pattern as USE_CLAUDE/CLAUDE_MODEL. Empty by default: a caller that
# never sets these (e.g. enrich_youtube_interactive.py) gets a minimal
# prompt with no editorial guidance, just the bare bullet/VERDICT/BOOK framing
# below. The VERDICT:/BOOK: formatting requirements stay hardcoded regardless,
# since extract_verdict()/verdict_callout()/extract_books() parse that literal format.
SUMMARY_INSTRUCTIONS = ""
SHORT_SUMMARY_INSTRUCTIONS = ""


def claude_summary(title, transcript, focus=None, language=None, is_short=False):
    """Returns (summary_text, problem, cost). `problem` is None when the summary
    is trustworthy, else a short string describing how it degraded — the caller
    surfaces it on the note rather than silently writing a partial summary.
    `cost` (USD) is returned even on a degraded summary, since the call was
    still billed."""
    if not USE_CLAUDE:
        return "", None, 0.0
    if not transcript:
        return "", "no transcript to summarize", 0.0
    focus_line = f" Pay particular attention to: {focus}.\n\n" if focus else "\n\n"
    # French-language videos get a French summary/verdict — the VERDICT:
    # prefix itself stays in English (extract_verdict/VERDICT_RE match on
    # it literally), only the leading yes/no/maybe word and reason after it
    # switch language, since verdict_callout() maps both.
    is_french = bool(language) and language.split("-")[0].lower() == "fr"
    language_line = (
        "Write the summary bullets and the verdict reason in French, since this "
        "video is in French. Keep the literal 'VERDICT: ' prefix in English, but "
        "follow it with Oui/Non/Peut-être/Lire and a French reason "
        "(e.g. 'VERDICT: Oui — raison').\n\n"
        if is_french else ""
    )
    book_line = (
        "If any books are mentioned by name, list each on its own line prefixed with "
        "'BOOK: ' (e.g. 'BOOK: Atomic Habits — James Clear', including the author if "
        "stated). If no books are mentioned, omit this entirely — don't write 'BOOK: none'.\n\n"
    )
    if is_short:
        # Shorts are already quick to watch, so a "worth watching in full?"
        # verdict is pointless — just summarize.
        prompt = (
            f'Summarize this YouTube Short titled "{title}" in 1-3 short, tight bullet points, '
            "capturing just the core idea or takeaway."
            + focus_line + language_line + SHORT_SUMMARY_INSTRUCTIONS + "\n\n"
            + book_line +
            "Transcript:\n\n" + transcript[:100_000]
        )
    else:
        prompt = (
            f'Summarize this YouTube video titled "{title}" in 4-6 short, tight bullet points.'
            + focus_line + language_line + SUMMARY_INSTRUCTIONS + "\n\n"
            "After the bullets, on its own line, repeat just that verdict prefixed with 'VERDICT: ' "
            "(e.g. 'VERDICT: Yes — reason' or 'VERDICT: No — reason' or 'VERDICT: Maybe — reason'). "
            "If the video itself isn't worth watching but the bullets above already capture "
            "everything worth knowing, use 'VERDICT: Read — reason' instead of 'No' — that tells "
            "the reader to skip the video but still read the summary.\n\n"
            + book_line +
            "Transcript:\n\n" + transcript[:100_000]
        )
    try:
        text, stop_reason, usage = call_claude(
            prompt,
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            timeout=120,
            label="summary",
        )
    except ClaudeError as e:
        print(f"  ! {e}", file=sys.stderr)
        return "", str(e), 0.0
    cost = estimate_cost(CLAUDE_MODEL, usage)

    if stop_reason == "max_tokens":
        # The VERDICT line is appended at the very end of the response, so a
        # truncated response is likely to have cut it off mid-sentence rather
        # than dropped it cleanly — VERDICT_RE would still match that partial
        # fragment and extract_verdict() would show a cut-off callout as if it
        # were a real verdict. Strip any trailing (possibly partial) VERDICT
        # line so no callout is shown at all rather than a broken one.
        problem = f"summary hit the {CLAUDE_MAX_TOKENS}-token cap before finishing"
        print(f"  ! {problem}; dropping any partial verdict line", file=sys.stderr)
        text = re.sub(r"\n?[\s>*•\-]*VERDICT:.*$", "", text,
                       flags=re.IGNORECASE | re.DOTALL).strip()
        text += "\n\n_(summary truncated — hit the token limit before the verdict)_"
        return text, problem, cost

    return text, None, cost


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


BOOK_RE = re.compile(r"^[\s>*•\-]*BOOK:\s*(.+?)\**\s*$", re.MULTILINE | re.IGNORECASE)


def extract_books(summary):
    """Pulls any 'BOOK: ...' lines out of the summary. Returns (books, summary_without_them),
    where books is a list of "Title — Author" strings (possibly empty, never None)."""
    books = [m.strip() for m in BOOK_RE.findall(summary or "")]
    cleaned = BOOK_RE.sub("", summary or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return books, cleaned


def sanitize(name):
    name = re.sub(r'[\\/:*?"<>|#^\[\]]', "", name)
    return re.sub(r"\s+", " ", name).strip()[:120] or "video"


def topic_tag(topic):
    """Turns a topic name (e.g. "Home Automation") into an Obsidian-safe
    nested tag suffix ("home-automation") — Obsidian tags can't contain
    spaces, so clicking `#topic/home-automation` is what links every video
    on that subject together."""
    slug = re.sub(r"[^\w-]+", "-", topic.strip().lower()).strip("-")
    return f"topic/{slug}" if slug else None


VERDICT_CALLOUT_RE = re.compile(r"^\s*(yes|maybe|no|read|oui|peut-être|non|lire)\b", re.IGNORECASE)
VERDICT_CALLOUTS = {
    "yes": "success", "maybe": "warning", "no": "danger", "read": "info",
    "oui": "success", "peut-être": "warning", "non": "danger", "lire": "info",
}


def verdict_callout(verdict):
    """Maps a verdict's leading Yes/Maybe/No/Read to an Obsidian callout type
    (green/orange/red/blue respectively) — 'Read' is the "skip the video but
    the summary alone is worth it" case, kept visually distinct (blue, not
    red) from 'No' so it doesn't read as "skip this note entirely" too.
    Falls back to 'danger' if unrecognized."""
    m = VERDICT_CALLOUT_RE.match(verdict or "")
    return VERDICT_CALLOUTS.get(m.group(1).lower(), "danger") if m else "danger"


def build_note(meta, transcript, summary, transcript_note=None, verdict=None,
                reformatted=False, summarized=False, translated=False, transcript_done_at=None,
                is_short=False, warnings=None, books=None, topic=None, api_cost=0.0):
    tags = ["youtube"]
    tag = topic_tag(topic) if topic else None
    if tag:
        tags.append(tag)
    fm = [
        "---",
        f'title: "{meta["title"].replace(chr(34), chr(39))}"',
        f'channel: "{meta["channel"].replace(chr(34), chr(39))}"',
        f'channel_id: {meta.get("channel_id", "")}',
        f'url: {meta["url"]}',
        f'duration: {meta["duration"]}',
        f"date_watched: {date.today().isoformat()}",
        f"tags: [{', '.join(tags)}]",
        f"status: {STATUS_ATTENTION if warnings else STATUS_DONE}",
        "processed: true",
    ]
    if transcript_done_at:
        fm.append(f"transcript_done: {transcript_done_at}")
    if reformatted:
        fm.append(f"model_reformat: {REFORMAT_MODEL}")
    if translated:
        fm.append(f"model_translate: {TRANSLATE_MODEL}")
    if summarized:
        fm.append(f"model_summary: {CLAUDE_MODEL}")
    if api_cost:
        # Estimated from the Anthropic API's own token usage, not a billing
        # record — see claude_api.estimate_cost(). Rounds to 4dp so a cheap
        # Haiku call ($0.0003) doesn't display as $0.00.
        fm.append(f"api_cost: ${api_cost:.4f}")
    fm += ["---", ""]
    if warnings:
        # Runs happen headless (cron / iPhone Shortcut over SSH) where nobody
        # reads stderr, so a degraded note has to say so in the vault itself.
        # Find them later with an Obsidian search for status:needs-attention.
        fm.append("> [!warning] Enrichment issues — this note may be incomplete")
        fm += [f"> - {w}" for w in warnings]
        fm.append("")
    if verdict:
        fm += [f"> [!{verdict_callout(verdict)}] Worth watching? {verdict}", ""]
    elif is_short:
        fm += ["> [!info] Short video — no worth-watching verdict needed.", ""]
    if topic:
        # Obsidian only draws a graph edge between notes that share a
        # [[wikilink]] target, not a #tag — the tag stays for filtering,
        # this line is what actually connects same-topic videos in the graph.
        fm += [f"Topic: [[{topic.strip()}]]", ""]
    if summary:
        fm += ["## Summary", "", summary, ""]
    if books:
        fm += ["## Books mentioned", ""]
        fm += [f"- {b}" for b in books]
        fm.append("")
    fm += ["## My notes", "- ", ""]
    fm += ["## Transcript", "", transcript or transcript_note or "_No transcript available._", ""]
    return "\n".join(fm)


def already_processed(text):
    return re.search(r"^processed:\s*true\s*$", text, re.MULTILINE) is not None


PROCESSED_FALSE_RE = re.compile(r"^processed:\s*false\s*$", re.MULTILINE)
TRANSCRIPT_DONE_RE = re.compile(r"^transcript_done:\s*\S", re.MULTILINE)


def needs_fresh_transcript(text):
    """True when a note was previously fully enriched (transcript_done: has
    a date, meaning a transcript was actually fetched, not just skipped or
    absent) and has since been explicitly reset (processed: false) — e.g.
    via reprocess.py, or a human clearing the flag and moving it back to
    Inbox. Such a note should be treated as new: fresh transcript, verdict,
    and summary, not a reuse of whatever's still sitting in ## Transcript.

    A genuinely new note (Web Clipper import, or a hand-added stub with just
    a URL) never has transcript_done: at all, so it's unaffected — its
    baked-in transcript (if any) is still reused as before.
    """
    return bool(PROCESSED_FALSE_RE.search(text) and TRANSCRIPT_DONE_RE.search(text))


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


TIMESTAMP_MARKER_RE = re.compile(r"\*\*\d{1,2}:\d{2}(?::\d{2})?\*\*\s*[·•]")


def looks_raw(transcript):
    """Heuristic: does an existing transcript look like unedited auto-captions
    (bold timestamp markers like '**0:00** ·', or near-zero punctuation) rather
    than already-clean prose someone deliberately pasted in? Existing
    transcripts are normally left untouched (see main()), but a raw one still
    needs reformat_transcript() cleanup — otherwise it's stuck low-quality
    forever since nothing ever re-fetches it.
    """
    if not transcript:
        return False
    if TIMESTAMP_MARKER_RE.search(transcript):
        return True
    words = transcript.split()
    if len(words) < 20:
        return False
    sentence_enders = transcript.count(".") + transcript.count("!") + transcript.count("?")
    return sentence_enders / len(words) < 0.02


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


def main(focus_getter=None, topic_getter=None):
    """
    focus_getter: optional callable(meta) -> str | None
    Called once per video, right before summarizing, so a caller (e.g. the
    interactive wrapper) can ask "any particular focus for this one?" and
    steer that video's summary. Ignored if USE_CLAUDE is False.

    topic_getter: optional callable(meta) -> str | None
    Called once per video to get a subject name (e.g. "Home Automation")
    used to tag the note `topic/<slug>` so every video on that subject is
    one click away from every other in Obsidian. Independent of USE_CLAUDE —
    it's plain keyword matching, no API call involved.
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
            translated = False
            transcript_done_at = None
            transcript_skipped_on_purpose = False
            warnings = []
            api_cost = 0.0

            existing_transcript = ""
            if src is not None:
                src_text = src.read_text(encoding="utf-8")
                if not needs_fresh_transcript(src_text):
                    existing_transcript = extract_existing_transcript(src_text)

            fetched_fresh = False
            if existing_transcript:
                if looks_raw(existing_transcript):
                    print(f"  (transcript already in the note, but looks like raw "
                          "auto-captions — cleaning it up)")
                else:
                    print(f"  (transcript already in the note — using it, skipping fetch)")
                transcript = existing_transcript
                summary_language = meta.get("language")
            elif meta["duration_seconds"] > MAX_TRANSCRIPT_SECONDS:
                print(f"  (skipping transcript — {meta['duration']} exceeds "
                      f"{MAX_TRANSCRIPT_SECONDS // 60}min limit)")
                transcript = ""
                transcript_note = "_Transcript skipped — video exceeds the length limit._"
                # Deliberate, per max_transcript_minutes — not a degradation,
                # so this note stays `reviewed` rather than needs-attention.
                transcript_skipped_on_purpose = True
                summary_language = meta.get("language")
            else:
                transcript = fetch_transcript(url, language=meta.get("language"))
                fetched_fresh = True
                summary_language = meta.get("language")  # updated below if translation succeeds
                if not transcript:
                    warnings.append("no captions available for this video")

            if transcript:
                if USE_CLAUDE and (not existing_transcript or looks_raw(existing_transcript)):
                    cleaned, problem, cost = reformat_transcript(transcript, model=REFORMAT_MODEL)
                    api_cost += cost
                    if cleaned:
                        transcript = cleaned
                        reformatted = True
                    elif problem:
                        warnings.append(f"transcript left as raw captions — {problem}")
                # Only for a freshly-fetched transcript (never a reused one —
                # its language is unknown) whose native language isn't
                # already English or French: translate it to French via
                # Claude rather than asking YouTube for a translated caption
                # track, which fetch_transcript() deliberately never does
                # (that endpoint is throttled far harder — see its docstring).
                if (USE_CLAUDE and fetched_fresh
                        and transcript_target_lang(meta.get("language")) == "fr"
                        and (meta.get("language") or "").split("-")[0].lower() != "fr"):
                    translated_text, problem, cost = translate_transcript(transcript, model=TRANSLATE_MODEL)
                    api_cost += cost
                    if translated_text:
                        transcript = translated_text
                        summary_language = "fr"
                        translated = True
                    elif problem:
                        warnings.append(f"transcript left untranslated — {problem}")
                transcript_done_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            focus = focus_getter(meta) if focus_getter else None
            topic = topic_getter(meta) if topic_getter else None
            summary, problem, cost = claude_summary(meta["title"], transcript, focus=focus,
                                                      language=summary_language, is_short=is_short)
            api_cost += cost
            # Skip the summary's own complaint when there was never a transcript
            # to work from: either that was deliberate (length limit), or the
            # missing captions are already recorded above — no need to say it twice.
            if problem and not (not transcript and (transcript_skipped_on_purpose or warnings)):
                warnings.append(problem)
            summarized = bool(summary)
            verdict, summary = extract_verdict(summary) if summary else (None, summary)
            if summary and not verdict and not is_short:
                warnings.append("no verdict line in the summary")
            books, summary = extract_books(summary) if summary else ([], summary)
            body = build_note(meta, transcript, summary, transcript_note=transcript_note, verdict=verdict,
                               reformatted=reformatted, summarized=summarized, translated=translated,
                               transcript_done_at=transcript_done_at, is_short=is_short,
                               warnings=warnings, books=books, topic=topic, api_cost=api_cost)

            dest_dir = REVIEWED if MOVE_TO_REVIEWED else INBOX
            dest = dest_dir / f"{sanitize(meta['title'])}.md"
            dest.write_text(body, encoding="utf-8")
            print(f"  → {dest.relative_to(VAULT)}  "
                  f"({'summary, ' if summary else ''}"
                  f"{len(transcript.split())} transcript words)"
                  + (f"  [!] {len(warnings)} issue(s) — flagged needs-attention"
                     if warnings else ""))

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

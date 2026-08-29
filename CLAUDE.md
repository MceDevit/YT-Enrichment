# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local pipeline that turns a shared YouTube link into an enriched Obsidian
note: metadata (YouTube Data API v3), a cleaned-up transcript (yt-dlp +
Claude), a per-topic-focused Claude summary, and a one-line "worth watching?"
verdict in a `[!danger]` callout. No server, no accounts — everything runs on
the user's Mac against their own vault and API keys.

## Running / testing

There is no build step or package manifest — this is a handful of standalone
scripts run directly with `python3`.

```bash
python3 -m unittest test_enrich -v     # or: python3 test_enrich.py
```

`test_enrich.py` covers the pure logic — verdict parsing, callout colours,
`looks_raw()`, `detect_language()`/`check_reformat()`, settings parsing,
`build_note()`'s warning path, `call_claude()`'s retry rules, and
`reprocess.py`'s selection/requeue. Stdlib `unittest` on purpose (no pytest
dependency); no network, and the filesystem tests build a throwaway vault in
a temp dir rather than touching the real one. Most cases correspond to a bug
that actually shipped — the comments say which — so prefer adding a failing
case there over re-testing by hand against a real video.

```bash
# the main pipeline (reads _youtube_settings.md from the vault, no prompts)
python3 enrich_youtube_auto.py

# same pipeline but asks once at the start whether to use Claude summaries
python3 enrich_youtube_interactive.py

# check reviewed channels for new uploads, auto-queue into the vault
python3 watch_channels.py

# same channel scan, but via a local approve/skip webpage at 127.0.0.1:8743
python3 review_videos.py

# push already-enriched notes back through the pipeline (prompt/model changes,
# or repairing notes flagged status: needs-attention)
python3 reprocess.py --flagged --list     # --list is a safe dry run
python3 reprocess.py "partial name" --run
```

Manual smoke test for transcript cleanup — unlike `test_enrich.py`, this one
makes a real billed API call, so it's the way to check the model/key/prompt
end to end rather than just the surrounding logic:
```bash
python3 reformat_transcript.py   # runs the __main__ sample at the bottom of the file
```

Requires `vault_path.txt` (gitignored, copy from `vault_path.txt.example`)
pointing at a real Obsidian vault, plus `YOUTUBE_API_KEY` and (optionally)
`ANTHROPIC_API_KEY` in a gitignored `.env` file in this folder (see
`env_setup.py`, imported by `claude_api.py`/`dashboard.py` so it's loaded
before anything reads those vars). The keys are deliberately *not* exported
in `~/.zshrc` — that would leak them into every shell (including Claude Code
sessions launched from it), not just this pipeline. Without a real vault +
API keys, these scripts can't be exercised end-to-end — there's no
mock/fixture mode.

The `.command` files are Finder-double-click wrappers around the same
scripts (pause at the end via `read -p`); the `_Headless.sh` twins drop that
pause for non-interactive SSH invocation (e.g. from an iPhone Shortcut) — no
special env sourcing needed since `.env` is loaded by the Python process
itself. `review_videos.py` has no headless twin — it blocks forever serving
a local webpage, so it only makes sense run interactively at the Mac.

## Architecture

**`enrich_youtube.py` is the core engine** — every other script imports it
(`import enrich_youtube as core`) rather than duplicating logic. It owns:
- vault path resolution (`_load_vault()` reads `vault_path.txt` once at import time)
- the module-level config block (`VAULT`, `INBOX`, `REVIEWED`, `LANG`,
  `MAX_TRANSCRIPT_SECONDS`, `USE_CLAUDE`, `CLAUDE_MODEL`, ...) — callers
  mutate these attributes on the imported module (`core.USE_CLAUDE = True`)
  rather than passing parameters, since `main()` reads them as globals
- `collect_urls()` — finds unprocessed YouTube links either as lines in
  `_links.md` or embedded in any `.md` note sitting in the vault root
- `fetch_meta()` / `fetch_transcript()` — YouTube Data API v3 for metadata,
  yt-dlp (via `--write-auto-subs --sub-format json3`) for captions
- `main(focus_getter=None)` — the actual pipeline: fetch → maybe skip
  transcript by length → optionally reformat + summarize via Claude →
  build the note → move it to `Reviewed/` → prune `_links.md`. The
  `focus_getter` callback lets a caller inject a per-video summary focus
  right before summarizing without `main()` needing to know why.

**Settings-driven vs. interactive vs. programmatic** are three thin
wrappers over that one `main()`:
- `enrich_youtube_auto.py` parses `_youtube_settings.md` from the vault (a
  plain-Markdown config the user edits in Obsidian) into
  `(use_claude, max_transcript_minutes, transcript_retries, sections)`,
  builds a keyword-matching `focus_getter` from the `## Topic` sections, and
  drives `core.main()`. This is what the `Run_Enrich*` launchers call.
- `enrich_youtube_interactive.py` asks one y/n question at startup instead
  of reading settings, then calls `core.main()` with an `input()`-based
  `focus_getter`.
- Both mutate `core.USE_CLAUDE` / `core.MAX_TRANSCRIPT_SECONDS` /
  `core.TRANSCRIPT_RETRY_DELAYS` on the shared module before calling `main()`.

**Channel watching** (`watch_channels.py`, `review_videos.py`) is a second
subsystem built on the same `core` import, plus YouTube's public per-channel
RSS feed (no API key needed): it scans `Reviewed/` notes for
`channel` / `channel_id` frontmatter, resolves missing channel IDs via a
one-off `yt-dlp -J` lookup, tracks a `seen_video_ids` / `bootstrapped` cache
in `.channel_watch_cache.json`, and on each run either establishes a
per-channel baseline (first run) or drops new-upload stub notes into the
vault root for `enrich_youtube.py` to pick up next. `review_videos.py`
reuses `watch_channels.py`'s cache/feed functions (`import watch_channels as
wc`) and adds a stdlib-only local HTTP server (no Flask) serving an
approve/skip page, so accepting a video just writes the same stub note
`watch_channels.py` would have written automatically.

**`reformat_transcript.py`** turns a raw run-on yt-dlp transcript into
punctuated prose. Imported by `enrich_youtube.py`, only invoked when
`USE_CLAUDE` is on. Returns a `(text, problem)` pair, not a bare string: a
reformat can come back plausible-but-wrong (truncated at the token cap, or
silently translated out of the source language — both have happened), so
`check_reformat()` compares word-count ratio and a coarse stopword-based
`detect_language()` fingerprint against the input, and the caller keeps the
raw captions and flags the note rather than writing the damaged version.

**`translate_transcript.py`** turns a freshly-fetched, already-reformatted
transcript into French via Claude, for any video whose native language is
neither English nor French — used so the vault always gets a French-language
transcript without ever asking YouTube for an auto-translated caption track
(that endpoint throttles far harder than native captions; see
`fetch_transcript()`'s docstring). Same `(text, problem)` shape as
`reformat_transcript()`, with its own `check_translation()`: word-count ratio
plus `detect_language()` (imported from `reformat_transcript.py`) confirming
the output actually reads as French, not a silent no-op. Only ever applied to
a freshly-fetched transcript, never a reused one (an existing/Web-Clipper
transcript's language isn't known).

**`claude_api.py`** owns the actual Anthropic Messages API call for
`claude_summary()`, `reformat_transcript()`, and `translate_transcript()`
(plain `requests` POST, not the `anthropic` SDK — `requests` stays the only
non-stdlib dependency).
`call_claude()` retries timeouts/429/5xx with backoff and fails fast on 4xx
(bad key, unknown model), returning `(text, stop_reason)` so callers can
detect `max_tokens` truncation. This exists because a single un-retried
timeout used to silently drop a transcript cleanup and keep the raw captions.

**`reprocess.py`** flips enriched notes back to `processed: false` and moves
them to the vault root so the next run rebuilds them — by filename, `--url`,
`--flagged` (i.e. `status: needs-attention`), or `--all`. Only `processed:`
is touched, since `build_note()` regenerates the whole note anyway. Whether
the transcript itself gets refetched is decided by `enrich_youtube.py`'s
`needs_fresh_transcript()`: a `transcript_done:` date plus `processed: false`
means it was previously fully enriched and reset, so it's treated as new —
fresh transcript, verdict, and summary — rather than reusing what's still in
`## Transcript`.

### Key invariants

- **Idempotency**: a note with `processed: true` in frontmatter is never
  re-touched by `enrich_youtube.py` (`already_processed()`). Reprocessing
  means clearing that flag and moving the note back to the vault root — the
  script only scans the root, not `Reviewed/`. `reprocess.py` automates
  exactly that.
- **No silent degradation.** This pipeline runs headless (cron, or an iPhone
  Shortcut over SSH) where nobody reads stderr, so anything that produces a
  worse-but-still-valid note must be surfaced *in the vault*: `main()`
  accumulates a `warnings` list per video, and `build_note()` turns a
  non-empty list into `status: needs-attention` plus a `[!warning]` callout
  listing the reasons. Find them with an Obsidian search for
  `status:needs-attention`, repair with `reprocess.py --flagged`. When adding
  a step that can partially fail, append to `warnings` rather than only
  printing — several long-lived bugs here (silent translation, silent
  truncation, silent reformat timeouts) survived precisely because they only
  ever wrote to stderr.
- **429 handling**: `fetch_transcript()` raises `TranscriptRateLimited`
  after exhausting `TRANSCRIPT_RETRY_DELAYS` (set from
  `transcript_retries` in the settings file). The caller in `main()`
  catches this and leaves the source note untouched but visibly flagged
  (`mark_rate_limited()` prepends a `[!warning]` callout) rather than
  finalizing a note with no transcript — this makes the note eligible for
  automatic retry on the next run instead of silently losing the transcript.
- **`_youtube_settings.md` lives in two places**: this repo's copy is a
  template; the live config Claude/the scripts actually read is the copy
  the user keeps in their vault root (`core.VAULT / "_youtube_settings.md"`).
- Model name strings (`claude-sonnet-5`) are duplicated across
  `enrich_youtube.py`, `reformat_transcript.py`, and `translate_transcript.py`
  — check https://docs.claude.com/en/docs/about-claude/models before
  assuming any of them is current when debugging summary/reformat/translate
  issues.

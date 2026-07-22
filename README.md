# YouTube → Obsidian Enrichment Pipeline

Captures YouTube videos shared from any device into an Obsidian vault
(synced via iCloud), then enriches them locally on the Mac with full
transcripts, cleaned-up prose, and optional Claude-generated summaries with
a quick "worth watching?" verdict.

## The flow, end to end

```
1. CAPTURE (any device)
   Phone: Share sheet → Obsidian → new note with just the URL
   Mac:   press the "YTEnrich" shortcut in Brave/Safari → same thing
   Anywhere: paste a link as a line in _links.md, or into any .md note
        │
        ▼
2. Note lands in the vault root (iCloud syncs it to the Mac automatically)
        │
        ▼
3. Run "Run Enrich.command" (or python3 enrich_youtube_auto.py)
     - fetches title/channel/duration via the YouTube Data API v3
     - skips the transcript for anything over MAX_TRANSCRIPT_SECONDS
       (movies, long lectures — default 1 hour)
     - otherwise fetches the transcript via yt-dlp (English + French)
     - if use_claude: yes → cleans the transcript's grammar/paragraphs,
       summarizes it with a per-topic focus, and adds a red verdict
       callout ("Worth watching?") at the top of the note
     - writes the final note into Reviewed/, deletes the stub
        │
        ▼
4. Optional: "Run Watch Channels.command" / "Run Review Videos.command"
   check channels you've already reviewed for new uploads and feed them
   back into step 3 automatically (or via a one-click approve/skip page)
```

## Which command does what

| Command | What it does | When to run it |
|---|---|---|
| **`Run Enrich.command`** | Processes every unprocessed link sitting in the vault root: metadata, transcript, cleanup, summary, verdict, moves to `Reviewed/`. This is the one you actually run day-to-day. | Any time you've captured new videos and want them enriched. |
| **`Run Watch Channels.command`** | Checks the RSS feed of every channel behind your `Reviewed/` notes for new uploads. New ones get dropped into the vault root as bare-link stubs (so `Run Enrich.command` picks them up next). First run per channel just sets a baseline — it won't flood you with their whole back-catalog. | Whenever you want to catch new videos from channels you already trust, without deciding for yourself. |
| **`Run Review Videos.command`** | Same channel-scanning as above, but instead of auto-adding, it opens a local webpage (`127.0.0.1:8743`) showing new videos with thumbnails so you click **Add to Inbox** or **Skip** per video. Skipped ones never resurface. | When you want to filter new uploads yourself instead of auto-adding everything. |

Double-click any of these in Finder — they open Terminal, run the script, and pause at the end so you can read the output.

## Files (what's actually doing the work)

- **`enrich_youtube.py`** — core engine. Everything else imports this; it has no command of its own.
- **`enrich_youtube_auto.py`** — reads `_youtube_settings.md` and runs `enrich_youtube.py`'s pipeline with no prompts. This is what `Run Enrich.command` actually calls.
- **`_youtube_settings.md`** *(lives in the vault, not this repo)* — `use_claude: yes/no`, `max_transcript_minutes: 60`, plus topic sections (keywords → a custom summary focus per topic, used to judge the verdict). Edit this in Obsidian — no code changes needed.
- **`reformat_transcript.py`** — cleans the raw transcript (filler words, punctuation, paragraphs) before it's summarized, when `use_claude` is on. Falls back to the raw transcript if the call fails.
- **`watch_channels.py`** — the engine behind `Run Watch Channels.command`.
- **`review_videos.py`** — the engine behind `Run Review Videos.command`.
- **`vault_path.txt`** *(gitignored, machine-specific)* — one line, your vault's full path. Copy `vault_path.txt.example` to create your own.
- **`enrich_youtube_interactive.py`** *(optional, superseded by `_youtube_settings.md`)* — older version that asks per-run/per-video questions instead of reading a config file.

## Capturing from Brave/Safari on the Mac (the "YTEnrich" shortcut)

Mobile Brave shares a URL straight into a new Obsidian note via the Share
Sheet. There's no equivalent browser plugin on Mac, so instead there's a
macOS Quick Action that does the same thing:

- Installed at `~/Library/Services/YTEnrich.workflow` (Automator, not part of
  this repo — it's OS-level config, so it isn't synced/shared automatically).
- Grabs the frontmost browser tab's URL via AppleScript and writes it as a
  bare-link stub note into the vault root — same shape as the mobile share.
- Triggered via a keyboard shortcut set in **System Settings → Keyboard →
  Keyboard Shortcuts → Services → General → "YTEnrich"**.
- If you set this up on another Mac, you'll need to rebuild the Quick Action
  there too (Automator → New → Quick Action → "no input, any application" →
  Run Shell Script targeting Brave or Safari's AppleScript dictionary).

## Setup

```bash
brew install yt-dlp
python3 -m pip install requests --break-system-packages
cp vault_path.txt.example vault_path.txt   # then edit it — one line, your vault's full path
export YOUTUBE_API_KEY="..."            # required — get a free key at
                                         # https://console.cloud.google.com/apis/credentials
export ANTHROPIC_API_KEY="sk-ant-..."   # only needed if use_claude: yes
```

`vault_path.txt` is gitignored — it's specific to your machine, so each
person running this (e.g. if you hand the repo to someone else) makes their
own copy from the `.example` file and points it at their own vault.

## Notes on how the pieces work

**Metadata** (title/channel/duration) comes from the YouTube Data API v3, not
`yt-dlp` — it's cheap (1 quota unit/request, 10,000 free/day) and doesn't hit
the scraping-related rate limits `yt-dlp` can run into. **Transcripts** still
use `yt-dlp` (English + French auto-captions), since YouTube's official API
only exposes captions for videos you own — there's no free, official way
around occasional `yt-dlp` rate-limit errors on this part. When that happens
you'll see `! transcript fetch failed: ...` in the output instead of a silent
empty transcript.

**Long videos**: anything over `max_transcript_minutes` (configurable in
`_youtube_settings.md`, default 60) skips the transcript fetch entirely —
meant for movies/long-form content where a full transcript isn't useful. No
transcript means no summary either (summaries are built from it), but the
note still gets full metadata.

**The verdict callout**: when a summary is generated, Claude also judges
whether the video is worth watching against the matched topic's stated
focus, and that judgement gets pulled out into a red `[!danger]` callout at
the very top of the note — a one-glance answer before you read anything else.

# YT Enrich — YouTube → Obsidian, with a verdict

Share a YouTube video from any device into your Obsidian vault. A local
script then enriches it into a full note: metadata, cleaned-up transcript,
an AI summary focused on *your* interests — and a one-line answer to the
question that actually matters:

> **Is this video worth my time?**

Most YouTube-summary tools give you a generic summary. YT Enrich judges
each video **against goals you define per topic** (in a plain Markdown
settings file) and puts the verdict in a red callout at the top of the
note, so you can triage your watch-later pile at a glance.

Everything runs locally on your Mac, with your own API keys, into your
own vault. No accounts, no server, no subscription.

## What an enriched note looks like

```markdown
---
title: "Anthropic Just Changed How We Build Skills Forever"
channel: "Brock Mesarich | AI for Non Techies"
url: https://youtu.be/jbiMx17fEK0
duration: 9:35
date_watched: 2026-07-22
tags: [youtube]
status: reviewed
processed: true
---

> [!danger] Worth watching? Maybe — useful if you actively use the
> feature being demoed, but the core insight is fully captured in
> this summary.

## Summary
- ...focused bullet points, per your topic settings...

## My notes
-

## Transcript
...full transcript, cleaned into readable prose...
```

## Features

- **Capture from anywhere** — phone Share Sheet → Obsidian, a macOS Quick
  Action for Brave/Safari, or just paste links into `_links.md`.
- **Per-topic focus** — define topics by keywords in `_youtube_settings.md`;
  each topic gets its own summary focus ("give me the exact prompts",
  "flag hype vs. reproducible how-to", "note chord voicings mentioned").
- **The verdict** — a red `[!danger]` callout at the top of every note:
  worth watching in full, or is the summary enough?
- **Transcript cleanup** — raw auto-captions are rewritten into readable
  paragraphs before summarizing.
- **Channel watching** — checks channels you've already reviewed for new
  uploads, either auto-queued or via a local approve/skip webpage.
- **Length cutoff** — videos over `max_transcript_minutes` (default 60)
  skip the transcript entirely; you still get metadata.
- **Works offline from YouTube's UI** — your watch-later lives in your
  vault as Markdown, searchable and linkable forever.

## The flow

```
1. CAPTURE (any device)
   Phone: Share Sheet → Obsidian → new note with just the URL
   Mac:   keyboard shortcut in Brave/Safari (Quick Action, see below)
   Anywhere: paste a link into _links.md or any .md note
        │
        ▼   (iCloud/your sync brings it to the Mac)
2. Run "Run_Enrich.command"
     · metadata via the YouTube Data API v3
     · transcript via yt-dlp (skipped over the length limit)
     · Claude cleans the transcript, summarizes with your topic's
       focus, and issues the verdict
     · finished note moves to Reviewed/, the stub is deleted
     · if the transcript stays rate-limited (429) after retries, the
       note is left in place with a warning callout — just run again
        │
        ▼
3. Optional: "Run_Watch_Channels.command" / "Run_Review_Videos.command"
   surface new uploads from channels you've already reviewed
```

## Commands

| Command | What it does |
|---|---|
| **`Run_Enrich.command`** | The day-to-day one. Processes every captured link: metadata, transcript, summary, verdict, moves to `Reviewed/`. |
| **`Run_Watch_Channels.command`** | Auto-queues new uploads from every channel behind your `Reviewed/` notes (first run per channel only sets a baseline). |
| **`Run_Review_Videos.command`** | Same scan, but opens a local page (`127.0.0.1:8743`) with thumbnails and per-video **Add** / **Skip** buttons. |

Double-click in Finder; they open Terminal and pause at the end so you
can read the output.

## Setup

**Requirements:** macOS, Python 3, [Obsidian](https://obsidian.md) with a
synced vault, a free [YouTube Data API v3 key](https://console.cloud.google.com/apis/credentials),
and (for summaries/verdicts) an [Anthropic API key](https://console.anthropic.com/).

```bash
git clone https://github.com/MceDevit/YT-Enrichment.git
cd YT-Enrichment
brew install yt-dlp
python3 -m pip install requests --break-system-packages

# point the scripts at your vault (gitignored, machine-specific)
cp vault_path.txt.example vault_path.txt   # then edit: one line, your vault's full path

# API keys — add to ~/.zshrc
export YOUTUBE_API_KEY="..."             # Google Cloud Console → enable "YouTube Data API v3" → create key
export ANTHROPIC_API_KEY="sk-ant-..."    # only needed if use_claude: yes
```

Then copy `_youtube_settings.md` from this repo **into your vault root**
and edit it in Obsidian — that copy is the live config:

```markdown
use_claude: yes
max_transcript_minutes: 60

## Default
focus: General summary — actionable takeaways, resources mentioned,
and whether it's worth watching in full.

## My Topic
keywords: keyword1, keyword2, ...
focus: What you want the summary (and verdict) to prioritize for
videos matching these keywords.
```

Capture on mobile needs nothing extra — Obsidian's Share Sheet target
creates the stub note. On the Mac, build a small Automator Quick Action
(New → Quick Action → *no input, any application* → Run Shell Script)
that grabs the front tab's URL via AppleScript and writes it into the
vault root, then bind it to a keyboard shortcut under **System Settings →
Keyboard Shortcuts → Services**. Example script for Brave:

```bash
VAULT="$HOME/path/to/your/vault"
URL=$(osascript -e 'tell application "Brave Browser" to get URL of active tab of front window')
echo "$URL" > "$VAULT/YouTube Share $(date +%Y%m%d-%H%M%S).md"
```

## Costs

- **YouTube metadata**: free — 1 quota unit per video against a 10,000/day
  free quota.
- **Claude summaries**: pay-as-you-go on your own Anthropic API account;
  typically a few cents per video, so light use runs $1–3/month. Set
  `use_claude: no` to skip summaries entirely (metadata + transcript
  still work, and cost nothing).

## Known limitations (honest ones)

- **Transcript fetching uses yt-dlp**, which scrapes YouTube's internal
  caption endpoint. That endpoint rate-limits aggressively (HTTP 429) —
  the script retries twice with backoff (15s, then 30s) before giving up.
  If it's still rate-limited after that, the note is left untouched in
  the vault root (not finalized, not marked `processed`) with a
  `[!warning] Transcript rate-limited (429)` callout prepended, so it's
  obvious at a glance and gets retried automatically the next time you
  run `Run_Enrich.command`. There is no official free API for other
  people's captions; this is the trade-off the whole category lives with.
- **Reprocessing a note** means moving it out of `Reviewed/` back to the
  vault root and deleting the `processed: true` frontmatter line — the
  script only scans the vault root.
- **macOS-centric** — the Python scripts are portable, but the launchers
  and capture Quick Action are Mac-only.
- The `_youtube_settings.md` in this repo is a **template**; the copy in
  your vault is what's actually read at runtime.

## Files

- `enrich_youtube.py` — core engine (imported by everything else)
- `enrich_youtube_auto.py` — reads `_youtube_settings.md`, runs the pipeline with no prompts (what `Run_Enrich.command` calls)
- `reformat_transcript.py` — transcript → readable prose (when `use_claude: yes`)
- `watch_channels.py` / `review_videos.py` — channel watching engines
- `_youtube_settings.md` — settings **template** (live copy goes in your vault)
- `vault_path.txt.example` — copy to `vault_path.txt`, point at your vault

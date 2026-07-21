# YouTube → Obsidian Enrichment Pipeline

Captures YouTube videos shared from any device into an Obsidian vault
(synced via iCloud), then enriches them locally on the Mac with full
transcripts and optional Claude-generated summaries.

## Files

- **`enrich_youtube.py`** — core engine. Fetches metadata + transcript via
  `yt-dlp`, writes clean frontmatter, optionally summarizes with Claude,
  moves finished notes to `Reviewed`. Everything else imports this.
- **`enrich_youtube_auto.py`** — the day-to-day entry point. Reads
  `_youtube_settings.md` and runs fully automatically, no prompts.
- **`_youtube_settings.md`** — config: `use_claude: yes/no` plus topic
  sections (keywords → a custom summary focus per topic). Edit this in
  Obsidian to change behavior, no code changes needed.
- **`watch_channels.py`** — checks channels from your `Reviewed` notes for
  new uploads via YouTube's RSS feed. First run per channel sets a
  baseline rather than flooding you with the back-catalog.
- **`review_videos.py`** — run this to check for new uploads. Opens a
  local webpage (`127.0.0.1:8743`) listing new videos with thumbnails;
  click **Add to Inbox** or **Skip** per video. Skipped videos are
  remembered and never resurface.
- **`enrich_youtube_interactive.py`** *(optional, superseded by
  `_youtube_settings.md`)* — older version that asks per-run/per-video
  questions instead of reading a config file.

## Workflow

1. Share a video (Brave → New note) on any device → syncs into the vault
   via iCloud.
2. Optionally run `python3 review_videos.py` to catch new uploads from
   channels you already trust.
3. Run `python3 enrich_youtube_auto.py` to process everything sitting in
   the vault root: transcript, summary, clean frontmatter, moved to
   `Reviewed`.

## Setup

```bash
brew install yt-dlp
python3 -m pip install requests --break-system-packages
export ANTHROPIC_API_KEY="sk-ant-..."   # only needed if use_claude: yes
```

Edit the `VAULT` path near the top of `enrich_youtube.py` to point at your
vault (see comments in the file for how to find the real path on disk).

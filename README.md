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
transcript_done: 2026-07-22 14:53
model_reformat: claude-haiku-4-5-20251001
model_summary: claude-haiku-4-5-20251001
---

> [!warning] Worth watching? Maybe — useful if you actively use the
> feature being demoed, but the core insight is fully captured in
> this summary.

## Summary
- ...focused bullet points, per your topic settings...

## My notes
-

## Transcript
...full transcript, cleaned into readable prose...
```

The verdict callout is color-coded so you can triage at a glance: **Yes**
→ green, **Maybe** → orange (shown above), **No** → red.

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

`Run_Enrich.command` and `Run_Watch_Channels.command` also have a
`_Headless.sh` twin — same logic, but no `read -p` pause at the end,
meant for non-interactive triggers like an SSH command from an
iPad/iPhone Shortcut rather than a Finder double-click (a pause with
nothing to press Enter on would just hang forever). `Run_Review_Videos`
doesn't have one: it opens a local webpage bound to `127.0.0.1` and
blocks forever serving it, so it only makes sense run interactively at
the Mac itself.

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

New Anthropic accounts get a small amount of free API credit (no credit
card, just phone verification) — plenty to try this out before deciding
whether to add billing. If you'd rather not sign up for anything yet, set
`use_claude: no` in `_youtube_settings.md`: metadata + transcript still
work with just the free YouTube API key, no summary/verdict.

Then copy `_youtube_settings.md` from this repo **into your vault root**
and edit it in Obsidian — that copy is the live config:

```markdown
use_claude: yes
max_transcript_minutes: 60
transcript_retries: 0
model_summary: claude-sonnet-5      # or claude-haiku-4-5-20251001 for faster/cheaper summaries
model_reformat: claude-sonnet-5     # transcript cleanup model — can differ from model_summary

## Default
focus: General summary — actionable takeaways, resources mentioned,
and whether it's worth watching in full.

## My Topic
keywords: keyword1, keyword2, ...
focus: What you want the summary (and verdict) to prioritize for
videos matching these keywords.
```

`model_summary` and `model_reformat` are independent — e.g. run transcript
cleanup on the cheaper/faster Haiku while keeping the summary + verdict on
Sonnet, since verdict judgment tends to benefit more from the stronger model.
Both default to `claude-sonnet-5` if omitted. Model names change over time —
confirm current strings at https://docs.claude.com/en/docs/about-claude/models.

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

## Windows setup

The Python engine (`enrich_youtube.py` and friends) is portable and runs
fine on Windows — but the `.command`/`.sh` launchers and the macOS Quick
Action for capturing a browser tab are Mac-only, so a few things are done
manually instead.

```powershell
git clone https://github.com/MceDevit/YT-Enrichment.git
cd YT-Enrichment
pip install requests yt-dlp

# point the scripts at your vault (gitignored, machine-specific)
copy vault_path.txt.example vault_path.txt
notepad vault_path.txt   # one line, your vault's full Windows path, e.g.
                          # C:\Users\yourname\YourVault

# API keys — set as user environment variables (Settings > System > About >
# Advanced system settings > Environment Variables), or per PowerShell session:
setx YOUTUBE_API_KEY "..."
setx ANTHROPIC_API_KEY "sk-ant-..."
```

Copy `_youtube_settings.md` from this repo **into your vault root** and
edit it, same as the macOS setup.

There's no Windows equivalent of the AppleScript Quick Action, so capture
a video by pasting its link into `_links.md` in your vault root (works on
any platform — it's the fallback method built into `collect_urls()`), or
by sharing from Obsidian mobile if your vault syncs to the PC.

To run it, open a new PowerShell/terminal window after setting the
environment variables (so it picks them up) and run the script directly
instead of double-clicking a launcher:

```powershell
python enrich_youtube_auto.py
python watch_channels.py
python review_videos.py
```

## Running it remotely from an iPad/iPhone (SSH + Shortcuts)

If you keep an always-on Mac (e.g. a Mac mini) running this pipeline,
you can trigger `Run_Enrich.command` from your phone or iPad without
being anywhere near that Mac — no separate app, just Shortcuts + SSH.

**1. Put both devices on the same private network with Tailscale.**
Install [Tailscale](https://tailscale.com) on the Mac and on your
iPad/iPhone, sign in with the same account on both, and the Mac gets a
stable address (e.g. `100.x.x.x` or a name like `mces-mac-mini`) reachable
from anywhere, without opening any ports on your router.

**2. Set up SSH key auth to the Mac** (so Shortcuts never has to type a
password):

```bash
# on whichever device will initiate the SSH connection
ssh-keygen -t ed25519 -C "shortcut"
cat ~/.ssh/id_ed25519.pub
```

Append that public key to `~/.ssh/authorized_keys` on the Mac you're
connecting to, then confirm it works with no password prompt:

```bash
ssh -T yourmac-hostname
```

**3. Use the `_Headless.sh` variant, not the `.command` one.** The
`.command` scripts end with `read -p "Press Enter to close..."`, which
assumes an interactive terminal — over SSH with no one there to press
Enter, that just hangs forever. `Run_Enrich_Headless.sh` and
`Run_Watch_Channels_Headless.sh` drop that pause and explicitly load
your shell's environment (`PATH`, `YOUTUBE_API_KEY`,
`ANTHROPIC_API_KEY`), since a non-interactive SSH session doesn't
source `~/.zshrc` on its own. `Run_Review_Videos` has no headless
twin — it opens a local review webpage and blocks forever serving it,
so it only makes sense to run that one at the Mac itself.

**4. Build the Shortcut.** In the Shortcuts app: add a **"Run Script Over
SSH"** action, fill in the Mac's Tailscale hostname/IP, your username,
and authenticate with the SSH key (not a password). For the script to
run, point it at the headless script's full path, e.g.:

```bash
bash "/Users/yourname/Documents/YT-Enrichment/Run_Enrich_Headless.sh"
```

Save the Shortcut, optionally add it to your Home Screen or give it a
Siri phrase, and running it will SSH into the Mac, enrich everything
sitting in your vault, and return the script's output straight into
Shortcuts — all without touching the Mac itself.

**To actually see that output**, add a display step after "Run Script
Over SSH" — e.g. **Show Result** (or **Show Content** / **Show Alert** /
**Show Notification**, whichever your iOS version surfaces), fed with the
magic variable for the SSH action's result. Without a step like this,
Shortcuts runs the script silently and shows nothing on screen.

## Costs

- **YouTube metadata**: free — 1 quota unit per video against a 10,000/day
  free quota.
- **Claude summaries**: pay-as-you-go on your own Anthropic API account;
  typically a few cents per video, so light use runs $1–3/month. New
  accounts start with a small amount of free credit (no credit card
  needed), enough to test the whole pipeline before spending anything.
  Set `use_claude: no` to skip summaries entirely (metadata + transcript
  still work, and cost nothing).

## Known limitations (honest ones)

- **Transcript fetching uses yt-dlp**, which scrapes YouTube's internal
  caption endpoint. That endpoint rate-limits aggressively (HTTP 429).
  By default the script doesn't retry — a rate-limited video is left
  untouched in the vault root (not finalized, not marked `processed`)
  with a `[!warning] Transcript rate-limited (429)` callout prepended,
  so it's obvious at a glance and gets retried automatically the next
  time you run `Run_Enrich.command`. Set `transcript_retries: 1` or `2`
  in `_youtube_settings.md` if you'd rather it wait and retry within
  the same run (15s, then 30s backoff) before giving up — in practice
  this rarely helps, since the rate limit tends to outlast a couple of
  short waits. There is no official free API for other people's
  captions; this is the trade-off the whole category lives with.
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

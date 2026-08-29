# YT Enrich — YouTube → Obsidian, with a verdict

Share a YouTube video from any device into your Obsidian vault. A local
script then enriches it into a full note: metadata, cleaned-up transcript,
an AI summary focused on *your* interests — and a one-line answer to the
question that actually matters:

> **Is this video worth my time?**

Most YouTube-summary tools give you a generic summary. YT Enrich judges
each video **against goals you define per topic** (in a plain Markdown
settings file) and puts the verdict in a color-coded callout at the top
of the note, so you can triage your watch-later pile at a glance.

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
tags: [youtube, topic/ai-tools]
status: reviewed
processed: true
transcript_done: 2026-07-22 14:53
model_reformat: claude-haiku-4-5-20251001
model_summary: claude-haiku-4-5-20251001
---

> [!warning] Worth watching? Maybe — useful if you actively use the
> feature being demoed, but the core insight is fully captured in
> this summary.

Topic: [[AI Tools]]

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
  Every matched note gets both a `#topic/slug` tag (for filtering) and a
  `[[Topic]]` wikilink in the body, so every video on that subject shows up
  connected in Obsidian's graph view.
- **The verdict** — a color-coded callout at the top of every note:
  green/orange/red for Yes/Maybe/No on whether it's worth watching in
  full. YouTube Shorts get a blue info callout instead — a 30-second
  video doesn't need a "worth watching?" judgment, just a summary.
- **French-aware** — a video whose default audio language is French gets
  its summary and verdict written in French too, not just translated
  English.
- **Transcript cleanup** — raw auto-captions are rewritten into readable
  paragraphs before summarizing, with retries on transient API failures
  and sanity checks (word-count ratio, language match) that keep the raw
  captions rather than write a truncated or mistranslated transcript.
- **Nothing fails silently** — this pipeline runs headless (cron, or an
  SSH-triggered Shortcut with no one watching stderr), so any degraded
  step — a failed reformat, a truncated summary, missing captions — flips
  that note to `status: needs-attention` with a callout explaining why,
  instead of writing a note that looks fine but isn't. Find them with an
  Obsidian search for `status:needs-attention`, or `python3 dashboard.py`.
- **Channel watching** — checks channels you've already reviewed for new
  uploads, either auto-queued or via a local approve/skip webpage.
- **Length cutoff** — videos over `max_transcript_minutes` (default 60)
  skip the transcript entirely; you still get metadata.
- **Works offline from YouTube's UI** — your watch-later lives in your
  vault as Markdown, searchable and linkable forever.

## The flow

A visual walkthrough of every stage and branch — including what happens
when a caption's missing, a reformat times out, or a video is a Short —
is in [`docs/pipeline-flow.html`](docs/pipeline-flow.html) (open it
directly in a browser, no server needed).

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
| **`Run_Dashboard.command`** | Read-only status screen: environment, live settings, queue depth, library counts, and anything flagged `needs-attention`. Safe to run any time — no writes, fetches, or API calls. |

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
python3 -m pip install requests python-dotenv --break-system-packages

# point the scripts at your vault (gitignored, machine-specific)
cp vault_path.txt.example vault_path.txt   # then edit: one line, your vault's full path

# API keys — create a gitignored .env file in this folder (not ~/.zshrc:
# that would leak them into every shell, including unrelated tools)
cat > .env <<'EOF'
YOUTUBE_API_KEY=...            # Google Cloud Console → enable "YouTube Data API v3" → create key
ANTHROPIC_API_KEY=sk-ant-...   # only needed if use_claude: yes
EOF
```

`env_setup.py` loads `.env` via `python-dotenv` before anything reads those
variables (imported by `claude_api.py` / `dashboard.py`) — no shell sourcing
or `export` needed, and no risk of the keys ending up in every terminal
session on the machine.

Sanity-check the install before pointing it at a real video — the test
suite needs no API keys, no vault, and no network:

```bash
python3 -m unittest test_enrich
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
model_reformat: claude-haiku-4-5-20251001   # transcript cleanup model — can differ from model_summary

## Default
focus: General summary — actionable takeaways, resources mentioned,
and whether it's worth watching in full.

## My Topic
keywords: keyword1, keyword2, ...
focus: What you want the summary (and verdict) to prioritize for
videos matching these keywords.

## Summary Prompt
Optional — replaces the built-in editorial instructions for the summary
(bullet count, prioritization, verdict criteria, tone). Leave the section
out and summaries fall back to a bare bullet/VERDICT framing with no
editorial guidance, so once you're using Claude summaries you'll generally
want this section filled in.

## Short Summary Prompt
Same idea, for YouTube Shorts specifically — no verdict is ever requested
for Shorts regardless of what's here (a 30-second video doesn't need a
"worth watching in full?" judgment).
```

`model_summary` and `model_reformat` are independent — e.g. run transcript
cleanup on the cheaper/faster Haiku while keeping the summary + verdict on
Sonnet, since verdict judgment tends to benefit more from the stronger model.
If omitted, `model_summary` defaults to `claude-sonnet-5` and `model_reformat`
to `claude-haiku-4-5-20251001`. Model names change over time — confirm
current strings at https://docs.claude.com/en/docs/about-claude/models.

The `## Summary Prompt` / `## Short Summary Prompt` text isn't duplicated
anywhere in the code — this settings file is the only place it lives, so
edit it here to change how Claude judges and writes every summary.

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
pip install requests python-dotenv yt-dlp

# point the scripts at your vault (gitignored, machine-specific)
copy vault_path.txt.example vault_path.txt
notepad vault_path.txt   # one line, your vault's full Windows path, e.g.
                          # C:\Users\yourname\YourVault

# API keys — create a gitignored .env file in this folder
notepad .env
```

```
YOUTUBE_API_KEY=...
ANTHROPIC_API_KEY=sk-ant-...
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
`Run_Watch_Channels_Headless.sh` drop that pause; API keys need no
special handling since they live in `.env` and are loaded by the Python
process itself (`env_setup.py`), not sourced from the shell.
`Run_Review_Videos` has no headless twin — it opens a local review
webpage and blocks forever serving it, so it only makes sense to run
that one at the Mac itself.

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
  caption endpoint — there is no official free API for other people's
  captions, so this is the trade-off the whole category lives with.
- **Reprocessing a note** (after tuning the summary prompt, switching
  models, or fixing a flagged note) means moving it out of `Reviewed/`
  back to the vault root and clearing the `processed: true` frontmatter
  line — `reprocess.py` automates this: `python3 reprocess.py --flagged --run`.
- **macOS-centric** — the Python scripts are portable, but the launchers
  and capture Quick Action are Mac-only.
- The `_youtube_settings.md` in this repo is a **template**; the copy in
  your vault is what's actually read at runtime.

## Files

- `enrich_youtube.py` — core engine (imported by everything else)
- `enrich_youtube_auto.py` — reads `_youtube_settings.md`, runs the pipeline with no prompts (what `Run_Enrich.command` calls)
- `reformat_transcript.py` — transcript → readable prose (when `use_claude: yes`), with truncation/translation sanity checks
- `claude_api.py` — shared Anthropic API call with retry/backoff, used by both the summary and the reformat step
- `env_setup.py` — loads `YOUTUBE_API_KEY` / `ANTHROPIC_API_KEY` from a gitignored `.env` file (imported by `claude_api.py` / `dashboard.py`)
- `watch_channels.py` / `review_videos.py` — channel watching engines
- `dashboard.py` — read-only status screen (`python3 dashboard.py`, or `Run_Dashboard.command`)
- `reprocess.py` — rebuild already-enriched notes: by name, `--url`, `--flagged`, or `--all`
- `test_enrich.py` — unit tests for the pure logic, no network/vault needed (`python3 -m unittest test_enrich`)
- `_youtube_settings.md` — settings **template** (live copy goes in your vault)
- `vault_path.txt.example` — copy to `vault_path.txt`, point at your vault

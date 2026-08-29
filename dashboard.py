#!/usr/bin/env python3
"""
dashboard.py — one screen showing the state of the pipeline.

    python3 dashboard.py            # full dashboard
    python3 dashboard.py --flagged  # only the notes needing attention
    python3 dashboard.py --plain    # no colour (for logs / cron output)

Strictly read-only: it inspects the vault and settings but never writes,
fetches, or calls an API, so it's safe to run at any time.

Answers the questions you actually have day to day — is anything queued, did
anything degrade, is the environment still wired up correctly — without
opening Obsidian or reading a log.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import env_setup  # noqa: F401 — loads ANTHROPIC_API_KEY / YOUTUBE_API_KEY from .env

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Show a helpful message instead of a traceback when the vault isn't set up —
# a broken vault_path.txt is exactly the kind of thing you'd run this to find.
try:
    import enrich_youtube as core
    import enrich_youtube_auto as auto
except RuntimeError as e:
    print(f"Cannot start: {e}")
    sys.exit(1)


class Style:
    """ANSI colours, disabled when piped to a file or when --plain is passed."""

    def __init__(self, enabled=True):
        self.enabled = enabled and sys.stdout.isatty()

    def _wrap(self, code, text):
        return f"\033[{code}m{text}\033[0m" if self.enabled else str(text)

    def bold(self, t): return self._wrap("1", t)
    def dim(self, t): return self._wrap("2", t)
    def green(self, t): return self._wrap("32", t)
    def yellow(self, t): return self._wrap("33", t)
    def red(self, t): return self._wrap("31", t)
    def cyan(self, t): return self._wrap("36", t)


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
WARNING_RE = re.compile(r"^> \[!warning\][^\n]*\n((?:> - [^\n]*\n?)*)", re.MULTILINE)


def frontmatter(text):
    """Flat key -> value from a note's frontmatter. Good enough for our own
    notes; not a general YAML parser."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fields = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip('"')
    return fields


def note_warnings(text):
    m = WARNING_RE.search(text)
    if not m:
        return []
    return [ln.lstrip("> -").strip() for ln in m.group(1).splitlines() if ln.strip()]


def scan_notes():
    """Read every enriched note once and return the facts the panels need."""
    notes = []
    if not core.REVIEWED.exists():
        return notes
    for path in core.REVIEWED.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = frontmatter(text)
        notes.append({
            "path": path,
            "title": fm.get("title", path.stem),
            "status": fm.get("status", "?"),
            "channel": fm.get("channel", ""),
            "date": fm.get("date_watched", ""),
            "duration": fm.get("duration", ""),
            "reformatted": "model_reformat" in fm,
            "summarized": "model_summary" in fm,
            "has_transcript": "_No transcript available._" not in text,
            "warnings": note_warnings(text),
        })
    return notes


def pending_queue():
    """URLs waiting to be enriched: queue-file lines plus unprocessed stubs."""
    urls, stubs = [], []
    if core.LINKS_FILE.exists():
        for line in core.LINKS_FILE.read_text(encoding="utf-8").splitlines():
            m = core.YT_RE.search(line)
            if m:
                urls.append(m.group(0))
    for note in sorted(core.INBOX.glob("*.md")):
        if note.name.startswith("_"):
            continue
        try:
            text = note.read_text(encoding="utf-8")
        except OSError:
            continue
        if not core.already_processed(text) and core.YT_RE.search(text):
            stubs.append(note.name)
    return urls, stubs


def load_settings():
    path = core.VAULT / "_youtube_settings.md"
    if not path.exists():
        return None
    return auto.parse_settings(path.read_text(encoding="utf-8"))


def tool_version(name):
    exe = shutil.which(name)
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).strip().splitlines()[0][:20]
    except Exception:
        return "installed"


def panel(s, title):
    print(f"\n{s.bold(title)}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--flagged", action="store_true", help="only list notes needing attention")
    p.add_argument("--plain", action="store_true", help="disable colour")
    p.add_argument("-n", type=int, default=5, metavar="N", help="recent notes to show (default 5)")
    args = p.parse_args()
    s = Style(enabled=not args.plain)

    notes = scan_notes()
    flagged = [n for n in notes if n["warnings"] or n["status"] == core.STATUS_ATTENTION]

    if args.flagged:
        if not flagged:
            print(s.green("Nothing flagged — all notes look healthy."))
            return
        print(s.bold(f"{len(flagged)} note(s) need attention\n"))
        for n in sorted(flagged, key=lambda n: n["date"], reverse=True):
            print(f"  {s.yellow('!')} {n['title']}")
            for w in n["warnings"]:
                print(f"      {s.dim('- ' + w)}")
        print(s.dim("\n  Repair with: python3 reprocess.py --flagged --run"))
        return

    print(s.bold("YT Enrich") + s.dim(f"  ·  {datetime.now():%Y-%m-%d %H:%M}"))

    # ---- environment -------------------------------------------------------
    panel(s, "Environment")
    ok, bad = s.green("ok"), s.red("MISSING")
    vault_ok = core.VAULT.exists()
    print(f"  vault              {core.VAULT}  {ok if vault_ok else bad}")
    for var in ("YOUTUBE_API_KEY", "ANTHROPIC_API_KEY"):
        print(f"  {var:<18} {ok if os.environ.get(var) else bad}")
    ytdlp = tool_version("yt-dlp")
    print(f"  yt-dlp             {s.dim(ytdlp) if ytdlp else bad}")

    # ---- settings ----------------------------------------------------------
    settings = load_settings()
    panel(s, "Settings")
    if settings is None:
        print(f"  {s.red('_youtube_settings.md not found in the vault')}")
    else:
        use_claude, max_min, retries, m_sum, m_ref, sum_i, short_i, sections = settings
        print(f"  summaries          {s.green('ON') if use_claude else s.dim('off')}")
        print(f"  summary model      {m_sum or s.dim('(script default)')}")
        print(f"  reformat model     {m_ref or s.dim('(script default)')}")
        print(f"  max transcript     {max_min or core.MAX_TRANSCRIPT_SECONDS // 60} min"
              f"   ·   429 retries {retries or 0}")
        if not sum_i:
            print(f"  {s.yellow('! no ## Summary Prompt section — summaries use minimal framing')}")
        topics = [x["name"] for x in sections if x["name"].lower() != "default"]
        print(f"  topics             {', '.join(topics) if topics else s.dim('(Default only)')}")

    # ---- queue -------------------------------------------------------------
    urls, stubs = pending_queue()
    panel(s, "Queue")
    waiting = len(urls) + len(stubs)
    if waiting:
        print(f"  {s.cyan(str(waiting))} waiting  "
              f"{s.dim(f'({len(urls)} in _links.md, {len(stubs)} stub notes)')}")
        for name in stubs[:5]:
            print(f"    {s.dim('· ' + name)}")
    else:
        print(f"  {s.dim('empty — nothing waiting')}")

    # ---- library -----------------------------------------------------------
    panel(s, "Library")
    if not notes:
        print(f"  {s.dim('no enriched notes yet')}")
    else:
        no_transcript = sum(1 for n in notes if not n["has_transcript"])
        print(f"  enriched           {len(notes)}")
        print(f"  summarized         {sum(1 for n in notes if n['summarized'])}"
              f"   ·   reformatted {sum(1 for n in notes if n['reformatted'])}")
        if no_transcript:
            print(f"  without transcript {no_transcript}")
        flagged_txt = (s.yellow(f"{len(flagged)} needs attention")
                       if flagged else s.green("0 needs attention"))
        print(f"  flagged            {flagged_txt}")

        top = Counter(n["channel"] for n in notes if n["channel"]).most_common(3)
        if top:
            print("  top channels       "
                  + s.dim(", ".join(f"{c} ({k})" for c, k in top)))

    # ---- attention ---------------------------------------------------------
    if flagged:
        panel(s, "Needs attention")
        for n in sorted(flagged, key=lambda n: n["date"], reverse=True)[:8]:
            print(f"  {s.yellow('!')} {n['title'][:62]}")
            for w in n["warnings"][:2]:
                print(f"      {s.dim('- ' + w[:70])}")
        if len(flagged) > 8:
            print(s.dim(f"  ... and {len(flagged) - 8} more"))
        print(s.dim("  Repair: python3 reprocess.py --flagged --run"))

    # ---- recent ------------------------------------------------------------
    if notes and args.n > 0:
        panel(s, f"Recent ({args.n})")
        for n in sorted(notes, key=lambda n: n["date"], reverse=True)[:args.n]:
            mark = s.yellow("!") if n["warnings"] else " "
            when = s.dim(f"{n['date'] or '?':<10}")
            print(f"  {mark} {when} {n['title'][:58]}")

    today = sum(1 for n in notes if n["date"] == date.today().isoformat())
    print(s.dim(f"\n{len(notes)} notes total · {today} added today · vault: {core.VAULT.name}"))


if __name__ == "__main__":
    main()

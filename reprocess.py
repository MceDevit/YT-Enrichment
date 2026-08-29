#!/usr/bin/env python3
"""
reprocess.py — put already-enriched notes back through the pipeline.

Notes are enriched once and then skipped forever (`processed: true`), which is
what you want day to day but not when you've just changed the summary prompt,
switched models, or fixed a bug and want existing notes rebuilt. Doing that by
hand means editing frontmatter and moving files around for every note; this
does it in one command.

    python3 reprocess.py bach                 # notes whose filename matches "bach"
    python3 reprocess.py --flagged            # every note marked status: needs-attention
    python3 reprocess.py --url s7Nxb3N8iak    # by video id / URL fragment
    python3 reprocess.py --all                # everything in Reviewed/ (asks first)

    --run       run enrich_youtube_auto.py immediately afterwards
    --list      show what would be reprocessed, change nothing
    --yes       skip the confirmation prompt

The note itself is fully rebuilt by enrich_youtube.py, so old summaries,
verdicts and warning callouts are replaced rather than accumulated. If the
note had a transcript_done: date (a transcript was actually fetched before),
enrich_youtube.py's needs_fresh_transcript() re-fetches it fresh too rather
than reusing whatever's still in ## Transcript — see enrich_youtube.py.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enrich_youtube as core

PROCESSED_RE = re.compile(r"^processed:\s*true\s*$", re.MULTILINE)
STATUS_RE = re.compile(r"^status:\s*(.+?)\s*$", re.MULTILINE)


def candidate_notes():
    """Enriched notes live in Reviewed/, but MOVE_TO_REVIEWED=False leaves them
    in the vault root, so check both."""
    seen = set()
    for folder in (core.REVIEWED, core.INBOX):
        if not folder.exists():
            continue
        for note in sorted(folder.glob("*.md")):
            if note.name.startswith("_") or note in seen:
                continue
            seen.add(note)
            yield note


def matches(note, args):
    text = note.read_text(encoding="utf-8")
    if not PROCESSED_RE.search(text):
        return False  # not enriched yet — it'll be picked up on the next run anyway
    if args.all:
        return True
    if args.flagged:
        m = STATUS_RE.search(text)
        return bool(m and m.group(1).strip() == core.STATUS_ATTENTION)
    if args.url:
        return args.url.lower() in text.lower()
    return args.query.lower() in note.stem.lower()


def unprocess(note):
    """Flip the note back to unprocessed and move it where collect_urls() looks.

    Only `processed:` is touched — everything else is rewritten from scratch
    by build_note() on the next run (including the transcript, if
    transcript_done: shows one was actually fetched before — see
    enrich_youtube.py's needs_fresh_transcript()).
    """
    text = note.read_text(encoding="utf-8")
    text = PROCESSED_RE.sub("processed: false", text)

    dest = core.INBOX / note.name
    dest.write_text(text, encoding="utf-8")
    if note.resolve() != dest.resolve():
        note.unlink()
    return dest


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query", nargs="?", default="", help="match against the note filename")
    p.add_argument("--url", help="match against a video id / URL fragment instead")
    p.add_argument("--flagged", action="store_true",
                   help=f"select notes with status: {core.STATUS_ATTENTION}")
    p.add_argument("--all", action="store_true", help="select every enriched note")
    p.add_argument("--run", action="store_true",
                   help="run enrich_youtube_auto.py after queueing")
    p.add_argument("--list", action="store_true", help="show matches and exit")
    p.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    args = p.parse_args()

    if not any([args.query, args.url, args.flagged, args.all]):
        p.error("give a filename fragment, --url, --flagged, or --all")

    selected = [n for n in candidate_notes() if matches(n, args)]
    if not selected:
        print("No matching enriched notes found.")
        return

    print(f"{len(selected)} note(s) to reprocess:")
    for note in selected:
        print(f"  • {note.name}")

    if args.list:
        return
    if not args.yes:
        # These notes get rebuilt from scratch and re-billed against the API,
        # so confirm before touching a large selection.
        answer = input(f"\nReprocess {len(selected)} note(s)? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return

    for note in selected:
        dest = unprocess(note)
        print(f"  queued {dest.relative_to(core.VAULT)}")

    print(f"\n{len(selected)} note(s) queued in the vault root.")
    if args.run:
        print("Running enrich_youtube_auto.py...\n")
        subprocess.run([sys.executable, str(Path(__file__).resolve().parent
                                            / "enrich_youtube_auto.py")], check=False)
    else:
        print("Run enrich_youtube_auto.py (or Run_Enrich.command) to rebuild them.")


if __name__ == "__main__":
    main()

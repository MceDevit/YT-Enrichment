#!/usr/bin/env python3
"""
enrich_youtube_interactive.py — same pipeline as enrich_youtube.py, but asks
you at the start whether to generate Claude summaries for this run.

Usage:
    python3 enrich_youtube_interactive.py

Requires enrich_youtube.py to be in the same folder.
"""

import os
import sys
from pathlib import Path

# Make sure Python can find enrich_youtube.py even if run from elsewhere
sys.path.insert(0, str(Path(__file__).resolve().parent))

import enrich_youtube as core  # noqa: E402


def ask_yes_no(question):
    while True:
        answer = input(f"{question} [y/n]: ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer y or n.")


def main():
    print("Obsidian YouTube enrichment\n" + "-" * 28)

    use_claude = ask_yes_no("Use Claude to add a summary to each video?")

    if use_claude:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "\nANTHROPIC_API_KEY is not set.\n"
                "Add it to .env in this folder, then restart this script:\n"
                '  ANTHROPIC_API_KEY="your-key-here"\n'
            )
            if not ask_yes_no("Continue anyway without summaries?"):
                print("Stopped — no changes made.")
                return
            use_claude = False

    # Apply the choice to the shared module before running the pipeline
    core.USE_CLAUDE = use_claude
    print(f"\nSummaries: {'ON' if use_claude else 'OFF'}\n")

    def ask_focus(meta):
        title = meta.get("title", "this video")
        answer = input(
            f'  Any particular focus for "{title}"? '
            "(leave blank for a general summary): "
        ).strip()
        return answer or None

    core.main(focus_getter=ask_focus if use_claude else None)


if __name__ == "__main__":
    main()

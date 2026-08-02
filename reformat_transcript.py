"""
reformat_transcript.py

Drop-in function for your enrich_youtube.py pipeline.
Takes a raw yt-dlp transcript (often one run-on block of text with
filler words, no punctuation/paragraphs) and asks the Anthropic API
to clean it into readable prose.

Uses plain HTTP via `requests` (same pattern as claude_summary() in
enrich_youtube.py) rather than the `anthropic` SDK, so this project
doesn't need an extra dependency beyond what it already requires.

Usage:
    from reformat_transcript import reformat_transcript

    clean_text = reformat_transcript(raw_transcript)
"""

import os
import sys

import requests

# Model names change over time — confirm current strings at
# https://docs.claude.com/en/docs/about-claude/models
CLAUDE_MODEL = "claude-sonnet-5"
# Cleaned output is roughly the same length as the input transcript (the
# prompt forbids shortening), so this needs to comfortably cover a long
# video's transcript, not just a short reply. 4096 was too tight — it cut
# a ~15min video off mid-sentence with no error, since the API just stops
# at the token cap and returns a normal 200 response.
CLAUDE_MAX_TOKENS = 16000

REFORMAT_PROMPT = """You are cleaning up a raw auto-generated YouTube transcript.

Rules:
- Remove filler words (um, uh, you know, like) ONLY when they add no meaning.
- Add proper punctuation and capitalization.
- Break the text into natural paragraphs based on topic shifts.
- Do NOT summarize, shorten, or omit any actual content/ideas.
- Do NOT add commentary, headers, or bullet points — just clean prose.
- Preserve the speaker's original wording and meaning as closely as possible.

Raw transcript:
{transcript}

Return ONLY the cleaned transcript text, nothing else."""


def reformat_transcript(raw_transcript, model=CLAUDE_MODEL):
    """
    Send a raw transcript to Claude and return a cleaned, paragraphed version.
    Returns '' (with a stderr message) if the key is missing or the call fails,
    so a caller can fall back to the raw transcript instead of crashing.
    """
    if not raw_transcript or not raw_transcript.strip():
        return ""

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("  ! ANTHROPIC_API_KEY not set; skipping transcript reformat", file=sys.stderr)
        return ""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": CLAUDE_MAX_TOKENS,
                "messages": [
                    {"role": "user", "content": REFORMAT_PROMPT.format(transcript=raw_transcript)}
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("stop_reason") == "max_tokens":
            print(f"  ! transcript reformat hit the {CLAUDE_MAX_TOKENS}-token cap and was "
                  "truncated; keeping the raw transcript instead", file=sys.stderr)
            return ""
        blocks = data.get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    except Exception as e:
        print(f"  ! transcript reformat failed: {e}", file=sys.stderr)
        return ""


if __name__ == "__main__":
    # Quick manual test — paste a short raw transcript snippet here.
    sample = (
        "so um today we're gonna talk about like how to set up "
        "a a python virtual environment you know its pretty simple "
        "first you open your terminal and then um you type python3 -m venv env"
    )
    print(reformat_transcript(sample))

"""
translate_transcript.py

Translates a transcript already fetched in its own native language (see
enrich_youtube.py's fetch_transcript() and transcript_target_lang()) into
French via Claude — used for videos that are neither English nor French, so
the vault still gets a French-language transcript without ever asking
YouTube for an auto-translated caption track (that endpoint is throttled far
harder than native captions; see fetch_transcript()'s docstring).

Returns a (text, problem) pair, same pattern as reformat_transcript(): a
translation can come back plausible-but-wrong (truncated by the token cap,
or not actually translated at all), so the caller falls back to the
untranslated transcript and flags the note instead of writing a broken
translation as if it were fine.

Usage:
    from translate_transcript import translate_transcript

    french_text, problem = translate_transcript(transcript)
    if problem:
        ...  # keep the untranslated transcript, surface `problem`
"""

import sys

from claude_api import ClaudeError, call_claude
from reformat_transcript import detect_language

# Model names change over time — confirm current strings at
# https://docs.claude.com/en/docs/about-claude/models
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
# Same reasoning as reformat_transcript.py: translated output is roughly the
# same length as the input, so this needs to cover a long video's transcript.
CLAUDE_MAX_TOKENS = 16000
CLAUDE_TIMEOUT = 300

# Word counts don't map 1:1 across languages, so these are looser than
# reformat_transcript's ratio bounds — wide enough to allow for normal
# cross-language length variance while still catching truncation/hallucination.
MIN_LENGTH_RATIO = 0.5
MAX_LENGTH_RATIO = 2.0

TRANSLATE_PROMPT = """Translate the following video transcript into French.

Rules:
- Translate the full meaning faithfully. Do NOT summarize, shorten, or omit any content.
- Do NOT add commentary, headers, or bullet points — just the translated transcript text.
- Preserve the original paragraph structure where possible.

Transcript:
{transcript}

Return ONLY the French translation, nothing else."""


def check_translation(raw, translated):
    """Post-checks on a translation. Returns a problem string, or None if it
    looks sane — same idea as reformat_transcript.check_reformat()."""
    if not translated or not translated.strip():
        return "translation returned nothing"

    raw_words, translated_words = len(raw.split()), len(translated.split())
    if raw_words:
        ratio = translated_words / raw_words
        if ratio < MIN_LENGTH_RATIO:
            return (f"translation lost content ({translated_words} words from "
                    f"{raw_words}, {ratio:.0%}) — likely truncated")
        if ratio > MAX_LENGTH_RATIO:
            return (f"translation inflated content ({translated_words} words from "
                    f"{raw_words}, {ratio:.0%}) — likely hallucinated")

    lang = detect_language(translated)
    if lang and lang != "fr":
        return f"translation doesn't look like French (detected {lang}) — likely failed silently"

    return None


def translate_transcript(transcript, model=CLAUDE_MODEL):
    """Send a transcript to Claude and return (french_text, problem).

    On any failure or failed sanity check, returns ('', problem) so the
    caller keeps the untranslated transcript rather than writing a damaged one.
    """
    if not transcript or not transcript.strip():
        return "", None

    try:
        translated, stop_reason = call_claude(
            TRANSLATE_PROMPT.format(transcript=transcript),
            model=model,
            max_tokens=CLAUDE_MAX_TOKENS,
            timeout=CLAUDE_TIMEOUT,
            label="transcript translation",
        )
    except ClaudeError as e:
        print(f"  ! {e}", file=sys.stderr)
        return "", str(e)

    if stop_reason == "max_tokens":
        problem = (f"translation hit the {CLAUDE_MAX_TOKENS}-token cap and was truncated; "
                   "kept the untranslated transcript")
        print(f"  ! {problem}", file=sys.stderr)
        return "", problem

    problem = check_translation(transcript, translated)
    if problem:
        print(f"  ! {problem}; kept the untranslated transcript", file=sys.stderr)
        return "", problem

    return translated, None


if __name__ == "__main__":
    # Quick manual test — paste a short non-French transcript snippet here.
    sample = (
        "Hoje vamos falar sobre como configurar um ambiente virtual em Python. "
        "E bem simples, primeiro voce abre o terminal e digita python3 -m venv env."
    )
    text, problem = translate_transcript(sample)
    print(text or f"(no output — {problem})")

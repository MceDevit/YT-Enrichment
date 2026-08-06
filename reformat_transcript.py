"""
reformat_transcript.py

Takes a raw yt-dlp transcript (often one run-on block of text with filler
words, no punctuation/paragraphs) and asks Claude to clean it into readable
prose.

Returns a (text, problem) pair rather than a bare string: a reformat can come
back *plausible but wrong* — truncated by the token cap, or silently
translated out of the source language — and the caller needs to know that so
it can fall back to the raw captions and flag the note instead of writing the
damaged version as if it were fine.

Usage:
    from reformat_transcript import reformat_transcript

    clean_text, problem = reformat_transcript(raw_transcript)
    if problem:
        ...  # keep the raw transcript, surface `problem`
"""

import re
import sys

from claude_api import ClaudeError, call_claude

# Model names change over time — confirm current strings at
# https://docs.claude.com/en/docs/about-claude/models
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
# Cleaned output is roughly the same length as the input transcript (the
# prompt forbids shortening), so this needs to comfortably cover a long
# video's transcript, not just a short reply. 4096 was too tight — it cut
# a ~15min video off mid-sentence with no error, since the API just stops
# at the token cap and returns a normal 200 response.
CLAUDE_MAX_TOKENS = 16000
# A 15-20min transcript takes well over a minute to rewrite; 120s was tight
# enough that long videos timed out and silently kept their raw captions.
CLAUDE_TIMEOUT = 300

# The prompt forbids shortening, so real output lands near 100% of the input
# (observed: 89-100%). Anything under this is truncation or dropped content,
# not tightening.
MIN_LENGTH_RATIO = 0.6
MAX_LENGTH_RATIO = 1.6

REFORMAT_PROMPT = """You are cleaning up a raw auto-generated YouTube transcript.

Rules:
- Remove filler words (um, uh, you know, like) ONLY when they add no meaning.
- Add proper punctuation and capitalization.
- Break the text into natural paragraphs based on topic shifts.
- Strip any inline timestamp markers (e.g. "0:00", "**1:23:45**", "12:34 ·") —
  they're caption metadata, not spoken content.
- Do NOT summarize, shorten, or omit any actual content/ideas.
- Do NOT add commentary, headers, or bullet points — just clean prose.
- Preserve the speaker's original wording and meaning as closely as possible.
- Do NOT translate. Keep the exact same language as the raw transcript below,
  even if it's not English.

Raw transcript:
{transcript}

Return ONLY the cleaned transcript text, nothing else."""


# Coarse language fingerprinting. This is NOT general-purpose language ID —
# it exists to catch one specific observed failure: a French transcript coming
# back wholesale translated into English. Function words are the signal since
# they survive any topic, and we only act on a clear winner (see detect_language).
STOPWORDS = {
    "en": {"the", "and", "of", "to", "is", "that", "it", "you", "this", "with",
           "for", "are", "was", "have", "but", "not", "they", "what", "all"},
    "fr": {"le", "la", "les", "de", "des", "et", "que", "qui", "dans", "pour",
           "est", "une", "un", "je", "pas", "vous", "ce", "sur", "en", "au"},
    "es": {"el", "la", "los", "las", "de", "que", "y", "en", "es", "un", "una",
           "por", "para", "con", "no", "se", "su", "lo", "como"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "ein", "eine", "zu",
           "mit", "für", "den", "auf", "es", "sich", "auch", "dass"},
    "it": {"il", "la", "di", "che", "e", "un", "una", "per", "con", "non",
           "sono", "del", "della", "in", "si", "come"},
    "pt": {"o", "a", "de", "que", "e", "do", "da", "em", "um", "uma", "para",
           "com", "não", "os", "as", "se", "por"},
}

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_language(text, sample_words=400):
    """Best-guess language code, or None when there's no clear winner.

    Returns None rather than guessing when the top two languages are close —
    the caller treats None as "can't tell, don't flag", so an ambiguous result
    never produces a false alarm.
    """
    words = [w.lower() for w in WORD_RE.findall(text or "")][:sample_words]
    if len(words) < 30:
        return None
    scores = {lang: sum(w in stops for w in words) for lang, stops in STOPWORDS.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, best_score), (_, runner_up) = ranked[0], ranked[1]
    if best_score < len(words) * 0.04:
        return None  # too few function-word hits to trust
    if best_score < runner_up * 1.5:
        return None  # neighbours too close (romance languages share many)
    return best


def check_reformat(raw, cleaned):
    """Post-checks on a reformat. Returns a problem string, or None if it looks sane.

    Both checks come from real failures: a 15min transcript silently truncated
    at the token cap, and a French transcript returned fully translated to
    English. Neither surfaces as an API error, so they have to be caught here.
    """
    if not cleaned or not cleaned.strip():
        return "reformat returned nothing"

    raw_words, cleaned_words = len(raw.split()), len(cleaned.split())
    if raw_words:
        ratio = cleaned_words / raw_words
        if ratio < MIN_LENGTH_RATIO:
            return (f"reformat lost content ({cleaned_words} words from {raw_words}, "
                    f"{ratio:.0%}) — likely truncated")
        if ratio > MAX_LENGTH_RATIO:
            return (f"reformat inflated content ({cleaned_words} words from "
                    f"{raw_words}, {ratio:.0%}) — likely hallucinated")

    raw_lang, cleaned_lang = detect_language(raw), detect_language(cleaned)
    if raw_lang and cleaned_lang and raw_lang != cleaned_lang:
        return (f"reformat changed language ({raw_lang} → {cleaned_lang}) — "
                "likely translated")

    return None


def reformat_transcript(raw_transcript, model=CLAUDE_MODEL):
    """Send a raw transcript to Claude and return (cleaned_text, problem).

    On any failure or failed sanity check, returns ('', problem) so the caller
    keeps the raw transcript rather than writing a damaged one.
    """
    if not raw_transcript or not raw_transcript.strip():
        return "", None

    try:
        cleaned, stop_reason = call_claude(
            REFORMAT_PROMPT.format(transcript=raw_transcript),
            model=model,
            max_tokens=CLAUDE_MAX_TOKENS,
            timeout=CLAUDE_TIMEOUT,
            label="transcript reformat",
        )
    except ClaudeError as e:
        print(f"  ! {e}", file=sys.stderr)
        return "", str(e)

    if stop_reason == "max_tokens":
        problem = (f"reformat hit the {CLAUDE_MAX_TOKENS}-token cap and was truncated; "
                   "kept the raw transcript")
        print(f"  ! {problem}", file=sys.stderr)
        return "", problem

    problem = check_reformat(raw_transcript, cleaned)
    if problem:
        print(f"  ! {problem}; kept the raw transcript", file=sys.stderr)
        return "", problem

    return cleaned, None


if __name__ == "__main__":
    # Quick manual test — paste a short raw transcript snippet here.
    sample = (
        "so um today we're gonna talk about like how to set up "
        "a a python virtual environment you know its pretty simple "
        "first you open your terminal and then um you type python3 -m venv env"
    )
    text, problem = reformat_transcript(sample)
    print(text or f"(no output — {problem})")

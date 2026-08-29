#!/usr/bin/env python3
"""
test_enrich.py — unit tests for the pure logic in this project.

    python3 -m unittest test_enrich -v
    python3 test_enrich.py

Stdlib `unittest` on purpose: the project has no package manifest and keeps
`requests` as its only non-stdlib dependency, so tests shouldn't add one.

Nothing here touches the network or your real vault. The only tests that hit
the filesystem build a throwaway vault in a temp dir and repoint the module
globals at it. Every case below is either a bug that actually shipped (see the
comments) or a tuned threshold that would otherwise drift unnoticed.
"""

import argparse
import contextlib
import io
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import requests

# enrich_youtube resolves the vault at import time and raises without one.
# vault_path.txt is gitignored, so on a fresh clone that would stop the suite
# from running at all — point it somewhere harmless before importing. Nothing
# here writes to it; the filesystem tests use their own temp dirs.
os.environ.setdefault("YT_ENRICH_VAULT", tempfile.gettempdir() + "/yt-enrich-test-vault")

import claude_api  # noqa: E402
import enrich_youtube as core  # noqa: E402
import enrich_youtube_auto as auto  # noqa: E402
import reformat_transcript as rt  # noqa: E402
import reprocess  # noqa: E402
import translate_transcript as tt  # noqa: E402

# Realistic prose samples for the language checks. These need to be long enough
# to clear detect_language()'s 30-word floor and function-word-density check.
FRENCH_TEXT = (
    "Je vous propose de prendre un peu de hauteur pour comprendre ce qui se passe "
    "dans le monde de la musique et de la littérature. Les mots que nous utilisons "
    "sont des notes, et c'est pour cela que je pense qui il faut les écouter avec "
    "attention. Ce n'est pas une question de goût mais une question de sens."
)
ENGLISH_TEXT = (
    "I propose that we take a step back to understand what is happening in the "
    "world of music and literature. The words that we use are notes, and that is "
    "why I think you have to listen to them with attention. It is not a question "
    "of taste but a question of what they all mean to us."
)


@contextlib.contextmanager
def quiet():
    """Swallow the progress/warning chatter the code prints, so a passing run
    reads as a clean list of dots rather than a wall of retry messages."""
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        yield


def http_response(status=200, payload=None, text=""):
    """A stand-in for requests.Response that fails like the real thing.

    mock.Mock() auto-creates raise_for_status() as a silent no-op, which makes
    an error response look successful — the mock has to actually raise, or a
    test can pass against code that would break in production.
    """
    resp = mock.Mock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = payload if payload is not None else {}

    def raise_for_status():
        if status >= 400:
            raise requests.HTTPError(f"HTTP {status}", response=resp)

    resp.raise_for_status = raise_for_status
    return resp


class TestVideoIdExtraction(unittest.TestCase):
    def _id(self, url):
        m = core.VIDEO_ID_RE.search(url)
        return (m.group(1) or m.group(2)) if m else None

    def test_v_param_not_first_in_query_string(self):
        # Real bug: a share link with app=desktop before v= failed to match
        # because the old regex required the literal "watch?v=" prefix.
        self.assertEqual(
            self._id("https://www.youtube.com/watch?app=desktop&v=DehWFTu7yMo&feature=youtu.be"),
            "DehWFTu7yMo")

    def test_v_param_first_in_query_string(self):
        self.assertEqual(self._id("https://www.youtube.com/watch?v=abc123"), "abc123")

    def test_short_url(self):
        self.assertEqual(self._id("https://youtu.be/abc123"), "abc123")

    def test_shorts_url(self):
        self.assertEqual(self._id("https://www.youtube.com/shorts/abc123"), "abc123")


class TestDurationAndNaming(unittest.TestCase):
    def test_parse_iso8601_duration(self):
        self.assertEqual(core.parse_iso8601_duration("PT1H2M3S"), 3723)
        self.assertEqual(core.parse_iso8601_duration("PT49S"), 49)
        self.assertEqual(core.parse_iso8601_duration("PT17M4S"), 1024)

    def test_parse_iso8601_duration_bad_input_is_zero(self):
        # A malformed value must not crash the run; 0 flows into the length check.
        self.assertEqual(core.parse_iso8601_duration(""), 0)
        self.assertEqual(core.parse_iso8601_duration(None), 0)
        self.assertEqual(core.parse_iso8601_duration("garbage"), 0)

    def test_fmt_duration(self):
        self.assertEqual(core.fmt_duration(49), "0:49")
        self.assertEqual(core.fmt_duration(1024), "17:04")
        self.assertEqual(core.fmt_duration(3723), "1:02:03")

    def test_sanitize_strips_filesystem_and_obsidian_hostile_chars(self):
        self.assertEqual(core.sanitize('a/b:c*d?e"f<g>h|i#j^k[l]m'), "abcdefghijklm")

    def test_sanitize_collapses_whitespace_and_caps_length(self):
        self.assertEqual(core.sanitize("  too   many\nspaces  "), "too many spaces")
        self.assertEqual(len(core.sanitize("x" * 300)), 120)

    def test_sanitize_never_returns_empty(self):
        # An empty filename would make dest a directory write and blow up.
        self.assertEqual(core.sanitize("///"), "video")
        self.assertEqual(core.sanitize(""), "video")


class TestVerdictParsing(unittest.TestCase):
    def test_extracts_bare_verdict_line(self):
        verdict, cleaned = core.extract_verdict("- point one\n\nVERDICT: Yes — worth it")
        self.assertEqual(verdict, "Yes — worth it")
        self.assertNotIn("VERDICT", cleaned)
        self.assertIn("point one", cleaned)

    def test_extracts_verdict_wrapped_in_bullet_and_bold(self):
        # Haiku folds the verdict into the last bullet instead of a bare line.
        verdict, _ = core.extract_verdict("- a\n- **VERDICT: No — filler**")
        self.assertEqual(verdict, "No — filler")

    def test_extracts_french_verdict(self):
        verdict, _ = core.extract_verdict("- un point\n\nVERDICT: Non — sans intérêt")
        self.assertEqual(verdict, "Non — sans intérêt")

    def test_returns_none_when_absent(self):
        verdict, cleaned = core.extract_verdict("- just bullets, no verdict")
        self.assertIsNone(verdict)
        self.assertEqual(cleaned, "- just bullets, no verdict")

    def test_handles_empty_and_none(self):
        self.assertEqual(core.extract_verdict(""), (None, ""))
        self.assertEqual(core.extract_verdict(None), (None, None))


class TestVerdictCallout(unittest.TestCase):
    def test_english_verdicts_map_to_colours(self):
        self.assertEqual(core.verdict_callout("Yes — great"), "success")
        self.assertEqual(core.verdict_callout("Maybe — depends"), "warning")
        self.assertEqual(core.verdict_callout("No — skip it"), "danger")
        self.assertEqual(core.verdict_callout("Read — summary covers it"), "info")

    def test_french_verdicts_map_to_the_same_colours(self):
        # Added when French summaries shipped; without these every French
        # verdict silently rendered red regardless of what it said.
        self.assertEqual(core.verdict_callout("Oui — excellent"), "success")
        self.assertEqual(core.verdict_callout("Peut-être — bof"), "warning")
        self.assertEqual(core.verdict_callout("Non — sans intérêt"), "danger")
        self.assertEqual(core.verdict_callout("Lire — le résumé suffit"), "info")

    def test_case_insensitive(self):
        self.assertEqual(core.verdict_callout("YES — loud"), "success")
        self.assertEqual(core.verdict_callout("oui — calme"), "success")

    def test_unrecognised_falls_back_to_danger(self):
        self.assertEqual(core.verdict_callout("Perhaps, hard to say"), "danger")
        self.assertEqual(core.verdict_callout(""), "danger")
        self.assertEqual(core.verdict_callout(None), "danger")


class TestExtractBooks(unittest.TestCase):
    def test_extracts_multiple_book_lines(self):
        books, cleaned = core.extract_books(
            "- point one\n\nBOOK: Atomic Habits — James Clear\nBOOK: Deep Work — Cal Newport"
        )
        self.assertEqual(books, ["Atomic Habits — James Clear", "Deep Work — Cal Newport"])
        self.assertNotIn("BOOK", cleaned)
        self.assertIn("point one", cleaned)

    def test_extracts_book_wrapped_in_bullet_and_bold(self):
        books, _ = core.extract_books("- a\n- **BOOK: Sapiens — Yuval Noah Harari**")
        self.assertEqual(books, ["Sapiens — Yuval Noah Harari"])

    def test_returns_empty_list_when_absent(self):
        books, cleaned = core.extract_books("- just bullets, no books")
        self.assertEqual(books, [])
        self.assertEqual(cleaned, "- just bullets, no books")

    def test_handles_empty_and_none(self):
        self.assertEqual(core.extract_books(""), ([], ""))
        self.assertEqual(core.extract_books(None), ([], ""))


class TestLooksRaw(unittest.TestCase):
    def test_timestamp_markers_are_raw(self):
        self.assertTrue(core.looks_raw("**0:00** · bonjour tout le monde ça va bien"))
        self.assertTrue(core.looks_raw("**1:23:45** · hello there everyone"))

    def test_unpunctuated_run_on_text_is_raw(self):
        self.assertTrue(core.looks_raw(" ".join(["mot"] * 60)))

    def test_clean_prose_is_not_raw(self):
        clean = ("This is a properly punctuated paragraph. It has several sentences, "
                 "and it reads like prose. Nothing about it looks like raw captions. "
                 "That is the whole point of the check working correctly here.")
        self.assertFalse(core.looks_raw(clean))

    def test_short_text_is_not_flagged(self):
        # Too little signal to judge; must not trigger a pointless reformat.
        self.assertFalse(core.looks_raw("hello there"))
        self.assertFalse(core.looks_raw(""))
        self.assertFalse(core.looks_raw(None))


class TestExtractExistingTranscript(unittest.TestCase):
    def test_returns_transcript_body(self):
        note = "---\nurl: x\n---\n\n## Transcript\n\nSome real transcript text here.\n"
        self.assertEqual(core.extract_existing_transcript(note),
                         "Some real transcript text here.")

    def test_stops_at_the_next_heading(self):
        note = "## Transcript\n\nthe transcript\n\n## My notes\n- something private\n"
        self.assertEqual(core.extract_existing_transcript(note), "the transcript")

    def test_rejects_our_own_placeholders(self):
        # Otherwise the placeholder gets treated as a real transcript and the
        # note is never re-fetched.
        for placeholder in ("_No transcript available._",
                            "_Transcript skipped — video exceeds the length limit._"):
            note = f"## Transcript\n\n{placeholder}\n"
            self.assertEqual(core.extract_existing_transcript(note), "",
                             f"should reject {placeholder!r}")

    def test_missing_section_returns_empty(self):
        self.assertEqual(core.extract_existing_transcript("---\nurl: x\n---\n"), "")


class TestNeedsFreshTranscript(unittest.TestCase):
    def test_reset_note_that_was_fully_enriched_needs_a_fresh_transcript(self):
        note = "---\nprocessed: false\ntranscript_done: 2026-08-29 16:26\n---\n"
        self.assertTrue(core.needs_fresh_transcript(note))

    def test_web_clipper_import_is_unaffected(self):
        # Never touched our pipeline before, so no transcript_done: at all —
        # its baked-in transcript should still be reused.
        note = "---\nsource: https://youtu.be/x\n---\n\n## Transcript\n\ntext\n"
        self.assertFalse(core.needs_fresh_transcript(note))

    def test_length_skipped_note_is_unaffected(self):
        # Reset but never actually got a transcript (skipped for length), so
        # there's nothing stale to force a refetch of.
        note = "---\nprocessed: false\n---\n"
        self.assertFalse(core.needs_fresh_transcript(note))

    def test_still_processed_note_is_unaffected(self):
        note = "---\nprocessed: true\ntranscript_done: 2026-08-29 16:26\n---\n"
        self.assertFalse(core.needs_fresh_transcript(note))


class TestAlreadyProcessed(unittest.TestCase):
    def test_detects_processed_flag(self):
        self.assertTrue(core.already_processed("---\nprocessed: true\n---\n"))

    def test_false_and_absent_are_unprocessed(self):
        self.assertFalse(core.already_processed("---\nprocessed: false\n---\n"))
        self.assertFalse(core.already_processed("---\nurl: x\n---\n"))

    def test_does_not_match_the_word_inside_prose(self):
        self.assertFalse(core.already_processed("I processed: true story, honestly\n"))


class TestTranscriptTargetLang(unittest.TestCase):
    def test_english_and_french_keep_their_own_track(self):
        self.assertEqual(core.transcript_target_lang("en"), "en")
        self.assertEqual(core.transcript_target_lang("fr-FR"), "fr")

    def test_other_languages_are_translated_to_french(self):
        self.assertEqual(core.transcript_target_lang("es"), "fr")
        self.assertEqual(core.transcript_target_lang("de-DE"), "fr")

    def test_missing_language_returns_none(self):
        self.assertIsNone(core.transcript_target_lang(None))
        self.assertIsNone(core.transcript_target_lang(""))


class TestDetectLanguage(unittest.TestCase):
    def test_identifies_french_and_english(self):
        self.assertEqual(rt.detect_language(FRENCH_TEXT), "fr")
        self.assertEqual(rt.detect_language(ENGLISH_TEXT), "en")

    def test_too_short_returns_none(self):
        # Better to abstain than guess — None means "don't flag".
        self.assertIsNone(rt.detect_language("bonjour le monde"))
        self.assertIsNone(rt.detect_language(""))
        self.assertIsNone(rt.detect_language(None))

    def test_content_without_function_words_returns_none(self):
        self.assertIsNone(rt.detect_language(" ".join(["zzz"] * 100)))


class TestCheckReformat(unittest.TestCase):
    def test_accepts_a_faithful_reformat(self):
        raw = FRENCH_TEXT
        cleaned = FRENCH_TEXT.replace(" et ", ". Et ")
        self.assertIsNone(rt.check_reformat(raw, cleaned))

    def test_flags_translation(self):
        # The real bug: a French transcript came back fully translated.
        problem = rt.check_reformat(FRENCH_TEXT, ENGLISH_TEXT)
        self.assertIsNotNone(problem)
        self.assertIn("language", problem)

    def test_flags_truncation(self):
        raw = " ".join(["mot"] * 1000)
        problem = rt.check_reformat(raw, " ".join(["mot"] * 200))
        self.assertIsNotNone(problem)
        self.assertIn("truncated", problem)

    def test_flags_inflation(self):
        raw = " ".join(["mot"] * 100)
        problem = rt.check_reformat(raw, " ".join(["mot"] * 300))
        self.assertIsNotNone(problem)
        self.assertIn("hallucinated", problem)

    def test_flags_empty_output(self):
        self.assertIsNotNone(rt.check_reformat("some raw text here", ""))
        self.assertIsNotNone(rt.check_reformat("some raw text here", "   "))

    def test_english_to_english_is_not_flagged(self):
        # Guards against a false positive that would flag every English video.
        cleaned = ENGLISH_TEXT.replace(" and ", ". And ")
        self.assertIsNone(rt.check_reformat(ENGLISH_TEXT, cleaned))


class TestBuildNote(unittest.TestCase):
    META = {"title": "A Video", "channel": "Chan", "channel_id": "UC123",
            "url": "https://youtu.be/abc", "duration": "10:00"}

    def test_topic_adds_a_nested_tag(self):
        note = core.build_note(self.META, "t", "s", topic="Home Automation")
        self.assertIn("tags: [youtube, topic/home-automation]", note)

    def test_no_topic_keeps_the_plain_tag(self):
        note = core.build_note(self.META, "t", "s")
        self.assertIn("tags: [youtube]", note)

    def test_clean_note_is_reviewed_with_no_warning_block(self):
        note = core.build_note(self.META, "transcript", "summary", verdict="Yes — good")
        self.assertIn(f"status: {core.STATUS_DONE}", note)
        self.assertNotIn("[!warning]", note)
        self.assertIn("> [!success] Worth watching? Yes — good", note)

    def test_warnings_flip_status_and_render_a_callout(self):
        note = core.build_note(self.META, "t", "s", verdict="No — meh",
                               warnings=["first problem", "second problem"])
        self.assertIn(f"status: {core.STATUS_ATTENTION}", note)
        self.assertIn("> [!warning] Enrichment issues", note)
        # Continuation lines need the '>' prefix or Obsidian breaks the callout.
        self.assertIn("> - first problem", note)
        self.assertIn("> - second problem", note)

    def test_empty_warning_list_stays_reviewed(self):
        note = core.build_note(self.META, "t", "s", warnings=[])
        self.assertIn(f"status: {core.STATUS_DONE}", note)
        self.assertNotIn("[!warning]", note)

    def test_short_without_verdict_gets_the_info_callout(self):
        note = core.build_note(self.META, "t", "s", is_short=True)
        self.assertIn("> [!info] Short video", note)

    def test_books_render_as_a_list_when_present(self):
        note = core.build_note(self.META, "t", "s",
                               books=["Atomic Habits — James Clear", "Deep Work — Cal Newport"])
        self.assertIn("## Books mentioned", note)
        self.assertIn("- Atomic Habits — James Clear", note)
        self.assertIn("- Deep Work — Cal Newport", note)

    def test_no_books_section_when_none_mentioned(self):
        note = core.build_note(self.META, "t", "s", books=[])
        self.assertNotIn("## Books mentioned", note)

    def test_model_lines_only_appear_when_the_step_ran(self):
        without = core.build_note(self.META, "t", "s")
        self.assertNotIn("model_reformat:", without)
        self.assertNotIn("model_summary:", without)
        with_both = core.build_note(self.META, "t", "s", reformatted=True, summarized=True)
        self.assertIn("model_reformat:", with_both)
        self.assertIn("model_summary:", with_both)

    def test_quotes_in_title_do_not_break_frontmatter(self):
        note = core.build_note({**self.META, "title": 'He said "hello"'}, "t", "s")
        self.assertIn("""title: "He said 'hello'\"""", note)

    def test_placeholder_used_when_there_is_no_transcript(self):
        note = core.build_note(self.META, "", "s")
        self.assertIn("_No transcript available._", note)

    def test_date_watched_is_today(self):
        self.assertIn(f"date_watched: {date.today().isoformat()}",
                      core.build_note(self.META, "t", "s"))


class TestParseSettings(unittest.TestCase):
    SETTINGS = """use_claude: yes
max_transcript_minutes: 45
transcript_retries: 2
model_summary: claude-sonnet-5
model_reformat: claude-haiku-4-5-20251001

## Default
focus: General summary.

## AI Topic
keywords: ai, llm, claude
focus: AI-specific focus.

## Summary Prompt
Custom summary instructions.
Second line of them.

## Short Summary Prompt
Custom short instructions.
"""

    def setUp(self):
        self.parsed = auto.parse_settings(self.SETTINGS)

    def test_scalar_settings(self):
        use_claude, max_min, retries, model_summary, model_reformat, _, _, _ = self.parsed
        self.assertTrue(use_claude)
        self.assertEqual(max_min, 45)
        self.assertEqual(retries, 2)
        self.assertEqual(model_summary, "claude-sonnet-5")
        self.assertEqual(model_reformat, "claude-haiku-4-5-20251001")

    def test_prompt_sections_are_captured_verbatim(self):
        summary_instr, short_instr = self.parsed[5], self.parsed[6]
        self.assertEqual(summary_instr,
                         "Custom summary instructions.\nSecond line of them.")
        self.assertEqual(short_instr, "Custom short instructions.")

    def test_prompt_sections_are_not_topic_sections(self):
        # They have no keywords/focus, so leaving them in would let a video
        # title containing e.g. "prompt" match them as a topic.
        names = [s["name"] for s in self.parsed[7]]
        self.assertNotIn("Summary Prompt", names)
        self.assertNotIn("Short Summary Prompt", names)

    def test_default_section_is_retained(self):
        # Regression: an early version excluded Default alongside the prompt
        # sections, which silently dropped the fallback focus entirely.
        names = [s["name"] for s in self.parsed[7]]
        self.assertIn("Default", names)
        self.assertIn("AI Topic", names)

    def test_missing_prompt_sections_yield_none(self):
        parsed = auto.parse_settings("use_claude: yes\n\n## Default\nfocus: General.\n")
        self.assertIsNone(parsed[5])
        self.assertIsNone(parsed[6])

    def test_use_claude_accepts_several_truthy_spellings(self):
        for value in ("yes", "true", "on", "1", "YES"):
            self.assertTrue(auto.parse_settings(f"use_claude: {value}\n")[0], value)
        for value in ("no", "false", "off", "0"):
            self.assertFalse(auto.parse_settings(f"use_claude: {value}\n")[0], value)

    def test_keywords_are_lowercased_and_split(self):
        section = next(s for s in self.parsed[7] if s["name"] == "AI Topic")
        self.assertEqual(section["keywords"], ["ai", "llm", "claude"])


class TestFocusGetter(unittest.TestCase):
    def setUp(self):
        self.sections = auto.parse_settings(TestParseSettings.SETTINGS)[7]
        self.getter = auto.make_focus_getter(self.sections)

    def _focus(self, meta):
        with quiet():
            return self.getter(meta)

    def test_keyword_match_wins(self):
        self.assertEqual(self._focus({"title": "Using Claude for work", "channel": ""}),
                         "AI-specific focus.")

    def test_channel_name_also_matches(self):
        self.assertEqual(self._focus({"title": "Cooking", "channel": "LLM Weekly"}),
                         "AI-specific focus.")

    def test_falls_back_to_default(self):
        self.assertEqual(self._focus({"title": "Baking bread", "channel": "Food"}),
                         "General summary.")

    def test_matching_is_case_insensitive(self):
        self.assertEqual(self._focus({"title": "ALL ABOUT AI", "channel": ""}),
                         "AI-specific focus.")

    def test_short_keyword_does_not_match_inside_another_word(self):
        # Real bug: channel "Hamilton de Holanda Oficial" false-matched the
        # "ai" keyword via plain substring containment ("...ofici-AI-l...").
        self.assertEqual(self._focus({"title": "Some song analysis",
                                      "channel": "Hamilton de Holanda Oficial"}),
                         "General summary.")


class TestTopicGetter(unittest.TestCase):
    def setUp(self):
        self.sections = auto.parse_settings(TestParseSettings.SETTINGS)[7]
        self.getter = auto.make_topic_getter(self.sections)

    def test_keyword_match_returns_the_section_name(self):
        self.assertEqual(self.getter({"title": "Using Claude for work", "channel": ""}),
                         "AI Topic")

    def test_no_match_returns_none(self):
        self.assertIsNone(self.getter({"title": "Baking bread", "channel": "Food"}))

    def test_default_section_is_never_returned_as_a_topic(self):
        # Default is a catch-all, not a subject worth tagging notes with.
        self.assertIsNone(self.getter({"title": "Anything at all", "channel": ""}))

    def test_short_keyword_does_not_match_inside_another_word(self):
        self.assertIsNone(self.getter({"title": "Some song analysis",
                                       "channel": "Hamilton de Holanda Oficial"}))


class TestTopicTag(unittest.TestCase):
    def test_slugifies_spaces_and_case(self):
        self.assertEqual(core.topic_tag("Home Automation"), "topic/home-automation")

    def test_strips_punctuation(self):
        self.assertEqual(core.topic_tag("AI/ML & Robotics!"), "topic/ai-ml-robotics")

    def test_blank_topic_returns_none(self):
        self.assertIsNone(core.topic_tag("   "))


class TestCallClaudeRetries(unittest.TestCase):
    """call_claude() is the reason a single timeout no longer loses the work."""

    @staticmethod
    def _ok(text="ok", stop_reason="end_turn"):
        return http_response(payload={"content": [{"type": "text", "text": text}],
                                      "stop_reason": stop_reason})

    @mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_returns_text_and_stop_reason(self):
        with mock.patch.object(claude_api.requests, "post",
                               return_value=self._ok(text="hello")) as post:
            text, stop = claude_api.call_claude("p", model="m", max_tokens=10)
        self.assertEqual((text, stop), ("hello", "end_turn"))
        self.assertEqual(post.call_count, 1)

    @mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_concatenates_multiple_text_blocks(self):
        resp = http_response(payload={"content": [{"type": "text", "text": "part one "},
                                                  {"type": "thinking", "text": "ignore me"},
                                                  {"type": "text", "text": "part two"}],
                                      "stop_reason": "end_turn"})
        with mock.patch.object(claude_api.requests, "post", return_value=resp):
            text, _ = claude_api.call_claude("p", model="m", max_tokens=10)
        self.assertEqual(text, "part one part two")

    @mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_retries_transient_failures_then_succeeds(self):
        attempts = [requests.Timeout("boom"), requests.ConnectionError("boom"),
                    self._ok(text="recovered")]

        def flaky(*_a, **_k):
            outcome = attempts.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with mock.patch.object(claude_api.requests, "post", side_effect=flaky), quiet():
            text, _ = claude_api.call_claude("p", model="m", max_tokens=10,
                                             retry_delays=(0, 0))
        self.assertEqual(text, "recovered")
        self.assertEqual(attempts, [])

    @mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_gives_up_after_exhausting_retries(self):
        with mock.patch.object(claude_api.requests, "post",
                               side_effect=requests.Timeout("boom")) as post, quiet():
            with self.assertRaises(claude_api.ClaudeError):
                claude_api.call_claude("p", model="m", max_tokens=10, retry_delays=(0, 0))
        self.assertEqual(post.call_count, 3)  # initial + 2 retries

    @mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_does_not_retry_client_errors(self):
        # A bad model name or key can never succeed on retry — failing fast
        # keeps a broken config from costing minutes of backoff per video.
        bad = http_response(status=404, payload={"error": {"message": "model: nope"}})
        with mock.patch.object(claude_api.requests, "post", return_value=bad) as post:
            with self.assertRaises(claude_api.ClaudeError) as ctx:
                claude_api.call_claude("p", model="nope", max_tokens=10, retry_delays=(0, 0))
        self.assertEqual(post.call_count, 1)
        self.assertIn("model: nope", str(ctx.exception))

    @mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_retries_server_errors_and_429(self):
        for status in (429, 500, 503, 529):
            with self.subTest(status=status):
                resp = http_response(status=status)
                with mock.patch.object(claude_api.requests, "post",
                                       return_value=resp) as post, quiet():
                    with self.assertRaises(claude_api.ClaudeError):
                        claude_api.call_claude("p", model="m", max_tokens=10,
                                               retry_delays=(0,))
                self.assertEqual(post.call_count, 2)

    @mock.patch.dict("os.environ", {}, clear=True)
    def test_missing_api_key_raises_before_any_request(self):
        with mock.patch.object(claude_api.requests, "post") as post:
            with self.assertRaises(claude_api.ClaudeError):
                claude_api.call_claude("p", model="m", max_tokens=10)
        post.assert_not_called()


class TestReformatTranscriptWrapper(unittest.TestCase):
    """reformat_transcript() must never hand back output that failed a check —
    the caller keeps the raw captions instead."""

    def test_blank_input_is_a_no_op(self):
        self.assertEqual(rt.reformat_transcript(""), ("", None))
        self.assertEqual(rt.reformat_transcript("   "), ("", None))

    def test_token_cap_truncation_is_rejected(self):
        with mock.patch.object(rt, "call_claude",
                               return_value=("partial", "max_tokens")), quiet():
            text, problem = rt.reformat_transcript(FRENCH_TEXT)
        self.assertEqual(text, "")
        self.assertIn("token cap", problem)

    def test_translated_output_is_rejected(self):
        with mock.patch.object(rt, "call_claude",
                               return_value=(ENGLISH_TEXT, "end_turn")), quiet():
            text, problem = rt.reformat_transcript(FRENCH_TEXT)
        self.assertEqual(text, "")
        self.assertIn("language", problem)

    def test_api_failure_is_reported_not_raised(self):
        with mock.patch.object(rt, "call_claude",
                               side_effect=rt.ClaudeError("boom")), quiet():
            text, problem = rt.reformat_transcript(FRENCH_TEXT)
        self.assertEqual(text, "")
        self.assertIn("boom", problem)

    def test_good_output_passes_through(self):
        good = FRENCH_TEXT.replace(" et ", ". Et ")
        with mock.patch.object(rt, "call_claude", return_value=(good, "end_turn")):
            text, problem = rt.reformat_transcript(FRENCH_TEXT)
        self.assertEqual(text, good)
        self.assertIsNone(problem)


class TestTranslateTranscriptWrapper(unittest.TestCase):
    """translate_transcript() must never hand back output that failed a check —
    the caller keeps the untranslated transcript instead."""

    def test_blank_input_is_a_no_op(self):
        self.assertEqual(tt.translate_transcript(""), ("", None))
        self.assertEqual(tt.translate_transcript("   "), ("", None))

    def test_token_cap_truncation_is_rejected(self):
        with mock.patch.object(tt, "call_claude",
                               return_value=("partial", "max_tokens")), quiet():
            text, problem = tt.translate_transcript(ENGLISH_TEXT)
        self.assertEqual(text, "")
        self.assertIn("token cap", problem)

    def test_untranslated_output_is_rejected(self):
        # Claude echoed the source back instead of translating it to French.
        with mock.patch.object(tt, "call_claude",
                               return_value=(ENGLISH_TEXT, "end_turn")), quiet():
            text, problem = tt.translate_transcript(ENGLISH_TEXT)
        self.assertEqual(text, "")
        self.assertIn("French", problem)

    def test_api_failure_is_reported_not_raised(self):
        with mock.patch.object(tt, "call_claude",
                               side_effect=tt.ClaudeError("boom")), quiet():
            text, problem = tt.translate_transcript(ENGLISH_TEXT)
        self.assertEqual(text, "")
        self.assertIn("boom", problem)

    def test_good_translation_passes_through(self):
        with mock.patch.object(tt, "call_claude", return_value=(FRENCH_TEXT, "end_turn")):
            text, problem = tt.translate_transcript(ENGLISH_TEXT)
        self.assertEqual(text, FRENCH_TEXT)
        self.assertIsNone(problem)


class TestReprocess(unittest.TestCase):
    """Filesystem tests against a throwaway vault, never the real one."""

    NOTE = ("---\ntitle: \"Some Video\"\nurl: https://youtu.be/abc123\n"
            "status: {status}\nprocessed: {processed}\n---\n\n"
            "> [!warning] Enrichment issues — this note may be incomplete\n"
            "> - something went wrong\n\n"
            "## Summary\n\n- a bullet\n\n## Transcript\n\nthe transcript body\n")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "Reviewed").mkdir()
        self._saved = (core.VAULT, core.INBOX, core.REVIEWED)
        core.VAULT, core.INBOX, core.REVIEWED = root, root, root / "Reviewed"

    def tearDown(self):
        core.VAULT, core.INBOX, core.REVIEWED = self._saved
        self._tmp.cleanup()

    def _write(self, name, status="reviewed", processed="true"):
        path = core.REVIEWED / name
        path.write_text(self.NOTE.format(status=status, processed=processed),
                        encoding="utf-8")
        return path

    @staticmethod
    def _args(query="", url=None, flagged=False, all=False):
        return argparse.Namespace(query=query, url=url, flagged=flagged, all=all)

    def test_matches_by_filename_fragment_case_insensitively(self):
        note = self._write("Bach and Music.md")
        self.assertTrue(reprocess.matches(note, self._args(query="bach")))
        self.assertFalse(reprocess.matches(note, self._args(query="mozart")))

    def test_matches_by_url_fragment(self):
        note = self._write("Some Video.md")
        self.assertTrue(reprocess.matches(note, self._args(url="abc123")))
        self.assertFalse(reprocess.matches(note, self._args(url="zzz999")))

    def test_flagged_selects_only_needs_attention(self):
        flagged = self._write("Flagged.md", status=core.STATUS_ATTENTION)
        clean = self._write("Clean.md", status="reviewed")
        self.assertTrue(reprocess.matches(flagged, self._args(flagged=True)))
        self.assertFalse(reprocess.matches(clean, self._args(flagged=True)))

    def test_unprocessed_notes_are_never_selected(self):
        # They're already queued; re-queueing would be a no-op at best.
        note = self._write("Pending.md", processed="false")
        self.assertFalse(reprocess.matches(note, self._args(all=True)))

    def test_unprocess_moves_to_root_and_clears_the_flag(self):
        note = self._write("Some Video.md")
        dest = reprocess.unprocess(note)
        self.assertEqual(dest.parent, core.INBOX)
        self.assertFalse(note.exists(), "original should be moved, not copied")
        text = dest.read_text(encoding="utf-8")
        self.assertIn("processed: false", text)
        self.assertFalse(core.already_processed(text))

    def test_unprocess_keeps_the_transcript_in_the_file(self):
        # Whether that transcript actually gets reused is enrich_youtube.py's
        # needs_fresh_transcript() call, not reprocess.py's concern.
        dest = reprocess.unprocess(self._write("Some Video.md"))
        self.assertIn("the transcript body", dest.read_text(encoding="utf-8"))

    def test_requeued_note_still_carries_a_findable_url(self):
        # collect_urls() finds work by regex-matching the note body; losing the
        # URL here would strand the note in the vault root forever.
        dest = reprocess.unprocess(self._write("Some Video.md"))
        self.assertTrue(core.YT_RE.search(dest.read_text(encoding="utf-8")))

    def test_candidate_notes_skips_underscore_files(self):
        self._write("Real Note.md")
        (core.INBOX / "_links.md").write_text("https://youtu.be/x\n", encoding="utf-8")
        (core.INBOX / "_youtube_settings.md").write_text("use_claude: yes\n",
                                                         encoding="utf-8")
        names = [n.name for n in reprocess.candidate_notes()]
        self.assertIn("Real Note.md", names)
        self.assertNotIn("_links.md", names)
        self.assertNotIn("_youtube_settings.md", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)

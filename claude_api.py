"""
claude_api.py — one place for calling the Anthropic Messages API.

Both enrich_youtube.claude_summary() and reformat_transcript.reformat_transcript()
POST to the same endpoint with the same headers, so the retry/backoff and error
handling live here rather than being duplicated (and drifting) in each.

Uses plain HTTP via `requests` rather than the `anthropic` SDK, so this project
keeps `requests` as its only non-stdlib dependency.
"""

import os
import sys
import time

import requests

# Transient failures (timeouts, 429s, 5xx) get retried with these backoffs.
# A long transcript reformat can genuinely take a couple of minutes, and a
# single timeout used to silently drop the cleanup and keep the raw captions.
DEFAULT_RETRY_DELAYS = (5, 15, 30)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504, 529}


class ClaudeError(Exception):
    """Raised when a call fails and is not worth retrying (or retries ran out)."""


def call_claude(prompt, model, max_tokens, timeout=180,
                retry_delays=DEFAULT_RETRY_DELAYS, label="claude call"):
    """POST a single-user-message request. Returns (text, stop_reason).

    Retries on timeouts / connection errors / 429 / 5xx with backoff. Fails
    fast (no retry) on 4xx like a bad API key or unknown model, where retrying
    just wastes time. Raises ClaudeError if the key is missing or every attempt
    failed — callers decide whether that's fatal or a degraded-but-continue.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ClaudeError("ANTHROPIC_API_KEY not set")

    delays = list(retry_delays) + [None]  # None = final attempt, no more retries
    last_error = None

    for attempt, delay_after_failure in enumerate(delays):
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
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=timeout,
            )
            if resp.status_code in RETRYABLE_STATUS:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            data = resp.json()
            blocks = data.get("content", [])
            text = "".join(b.get("text", "") for b in blocks
                           if b.get("type") == "text").strip()
            return text, data.get("stop_reason")

        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status is not None and status not in RETRYABLE_STATUS:
                # 400/401/403/404 — bad key, bad model name, malformed request.
                # Retrying cannot help, so surface it immediately.
                raise ClaudeError(f"{label}: HTTP {status} {_body_hint(e.response)}") from e
            last_error = e
        except (requests.Timeout, requests.ConnectionError) as e:
            last_error = e
        except Exception as e:  # noqa: BLE001 — unexpected shapes shouldn't kill the run
            raise ClaudeError(f"{label}: {e}") from e

        if delay_after_failure is None:
            break
        print(f"  ! {label} failed ({last_error}) — retrying in {delay_after_failure}s "
              f"(attempt {attempt + 2}/{len(delays)})", file=sys.stderr)
        time.sleep(delay_after_failure)

    raise ClaudeError(f"{label}: giving up after {len(delays)} attempts ({last_error})")


def _body_hint(resp):
    """A short slice of the error body — the API puts the useful detail there
    (e.g. 'model: unknown model'), which the bare status code doesn't tell you."""
    if resp is None:
        return ""
    try:
        return str(resp.json().get("error", {}).get("message", ""))[:200]
    except Exception:
        return resp.text[:200] if resp.text else ""

#!/usr/bin/env python3
"""
watch_channels.py — checks the channels behind your Reviewed videos for new
uploads, and drops any new one straight into your vault root (Inbox) so
enrich_youtube.py picks it up on its next run.

Uses YouTube's public RSS feed per channel — no API key, no scraping.

Run this manually whenever you like, e.g. right before enrich_youtube.py.

First time it sees a channel, it establishes a baseline (marks that
channel's current videos as "already known") rather than flooding your
Inbox with its whole back-catalog. Only uploads *after* that point get
added on later runs.

Requires: requests, yt-dlp (only for one-time channel_id lookups on older
notes that don't have channel_id in frontmatter yet).
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enrich_youtube as core  # reuses VAULT/INBOX/REVIEWED/YT_RE/sanitize/run

CACHE_FILE = core.VAULT / ".channel_watch_cache.json"
RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
YT_NS = "{http://www.youtube.com/xml/schemas/2015}"

VIDEO_ID_RE = re.compile(r"(?:youtu\.be/|watch\?v=)([\w-]+)")


def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {"channel_ids": {}, "bootstrapped": [], "seen_video_ids": []}


def save_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def lookup_channel_id(video_url):
    r = core.run(["yt-dlp", "-J", "--skip-download", video_url])
    if r.returncode != 0:
        return None
    data = json.loads(r.stdout)
    return data.get("channel_id")


def gather_channels(cache):
    """Scan Reviewed notes for channel name + id (or resolve id via yt-dlp)."""
    channels = {}  # name -> channel_id
    seeded_ids = set()

    for note in core.REVIEWED.glob("*.md"):
        text = note.read_text(encoding="utf-8")

        vid_m = VIDEO_ID_RE.search(text)
        if vid_m:
            seeded_ids.add(vid_m.group(1))

        name_m = re.search(r'^channel:\s*"?([^"\n]+)"?\s*$', text, re.MULTILINE)
        if not name_m:
            continue
        name = name_m.group(1).strip()
        if not name or name in channels:
            continue

        cid_m = re.search(r"^channel_id:\s*(\S+)\s*$", text, re.MULTILINE)
        if cid_m and cid_m.group(1).strip():
            channels[name] = cid_m.group(1).strip()
            continue
        if name in cache["channel_ids"]:
            channels[name] = cache["channel_ids"][name]
            continue

        url_m = core.YT_RE.search(text)
        if url_m:
            print(f"  resolving channel id for {name}...")
            cid = lookup_channel_id(url_m.group(0))
            if cid:
                channels[name] = cid
                cache["channel_ids"][name] = cid

    return channels, seeded_ids


def fetch_feed(channel_id):
    resp = requests.get(RSS_URL.format(channel_id), timeout=20)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        vid = entry.findtext(f"{YT_NS}videoId")
        title = entry.findtext(f"{ATOM_NS}title")
        link_el = entry.find(f"{ATOM_NS}link")
        link = link_el.get("href") if link_el is not None else (
            f"https://youtu.be/{vid}" if vid else None
        )
        if vid and link:
            items.append((vid, title or vid, link))
    return items


def main():
    core.INBOX.mkdir(parents=True, exist_ok=True)
    core.REVIEWED.mkdir(parents=True, exist_ok=True)

    cache = load_cache()
    seen = set(cache["seen_video_ids"])
    bootstrapped = set(cache["bootstrapped"])

    print("Scanning your Reviewed channels...")
    channels, seeded_ids = gather_channels(cache)
    seen |= seeded_ids  # videos you've already reviewed are never "new"

    if not channels:
        print("No channels found yet — review at least one video first.")
        save_cache(cache)
        return

    print(f"Watching {len(channels)} channel(s): {', '.join(channels)}\n")

    added = 0
    for name, cid in channels.items():
        try:
            feed_items = fetch_feed(cid)
        except Exception as e:
            print(f"  ! {name}: couldn't check feed ({e})")
            continue

        if cid not in bootstrapped:
            # First time watching this channel: record its current videos
            # as the baseline, don't flag any of them as "new".
            for vid, _, _ in feed_items:
                seen.add(vid)
            bootstrapped.add(cid)
            print(f"  {name}: baseline set ({len(feed_items)} existing videos)")
            continue

        for vid, title, link in feed_items:
            if vid in seen:
                continue
            filename = core.sanitize(title) + ".md"
            dest = core.INBOX / filename
            if not dest.exists():
                dest.write_text(f"{link}\n", encoding="utf-8")
                print(f"  + new from {name}: {title}")
                added += 1
            seen.add(vid)

    cache["seen_video_ids"] = list(seen)
    cache["bootstrapped"] = list(bootstrapped)
    save_cache(cache)

    if added:
        print(f"\n{added} new video(s) added to the Inbox — run enrich_youtube.py to process them.")
    else:
        print("\nNo new videos since last check.")


if __name__ == "__main__":
    main()

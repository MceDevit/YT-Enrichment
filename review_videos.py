#!/usr/bin/env python3
"""
review_videos.py — checks your reviewed channels for new uploads (same logic
as watch_channels.py), then opens a local webpage so you can click through
them: "Add to Inbox" sends it into your enrich_youtube.py pipeline, "Skip"
dismisses it for good — skipped videos never show up again.

Nothing leaves your Mac; the page is served locally on 127.0.0.1 only.

Run manually whenever you like:
    python3 review_videos.py
"""

import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import watch_channels as wc  # reuses cache/feed/channel logic
core = wc.core

PORT = 8743

# ---------------------------------------------------------------- gather ----

def collect_pending():
    cache = wc.load_cache()
    seen = set(cache["seen_video_ids"])
    bootstrapped = set(cache["bootstrapped"])

    print("Scanning your reviewed channels...")
    channels, seeded_ids = wc.gather_channels(cache)
    seen |= seeded_ids

    pending = []
    for name, cid in channels.items():
        try:
            feed_items = wc.fetch_feed(cid)
        except Exception as e:
            print(f"  ! {name}: couldn't check feed ({e})")
            continue

        if cid not in bootstrapped:
            for vid, _, _ in feed_items:
                seen.add(vid)
            bootstrapped.add(cid)
            print(f"  {name}: baseline set ({len(feed_items)} existing videos)")
            continue

        for vid, title, link in feed_items:
            if vid not in seen:
                pending.append({"id": vid, "title": title, "link": link, "channel": name})

    cache["seen_video_ids"] = list(seen)  # baseline updates only; pending stay un-seen until acted on
    cache["bootstrapped"] = list(bootstrapped)
    wc.save_cache(cache)
    return pending, seen, bootstrapped


# ---------------------------------------------------------------- actions ----

def add_video(video):
    core.INBOX.mkdir(parents=True, exist_ok=True)
    filename = core.sanitize(video["title"]) + ".md"
    dest = core.INBOX / filename
    if not dest.exists():
        dest.write_text(f'{video["link"]}\n', encoding="utf-8")


def mark_seen(cache, video_id):
    seen = set(cache["seen_video_ids"])
    seen.add(video_id)
    cache["seen_video_ids"] = list(seen)
    wc.save_cache(cache)


# ------------------------------------------------------------------ html ----

def render_page(pending):
    if not pending:
        cards = "<p class='empty'>No new videos right now — check back later.</p>"
    else:
        cards = "\n".join(
            f'''
            <div class="card" id="card-{v['id']}">
              <img src="https://img.youtube.com/vi/{v['id']}/mqdefault.jpg" alt="">
              <div class="info">
                <a class="title" href="{v['link']}" target="_blank" rel="noopener">{v['title']}</a>
                <div class="channel">{v['channel']}</div>
                <div class="actions">
                  <button class="add" onclick="act('{v['id']}','add')">Add to Inbox</button>
                  <button class="skip" onclick="act('{v['id']}','skip')">Skip</button>
                </div>
              </div>
            </div>'''
            for v in pending
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>New videos to review</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ background:#1c1b22; color:#e9e7ef; font:15px/1.4 -apple-system, system-ui, sans-serif;
          max-width:640px; margin:0 auto; padding:24px 16px 80px; }}
  h1 {{ font-size:18px; font-weight:600; }}
  .card {{ display:flex; gap:14px; background:#26242e; border:1px solid #37343f;
           border-radius:10px; padding:12px; margin-bottom:12px; transition:opacity .2s, transform .2s; }}
  .card img {{ width:140px; height:79px; object-fit:cover; border-radius:6px; flex-shrink:0; }}
  .info {{ display:flex; flex-direction:column; gap:6px; min-width:0; }}
  .title {{ color:#e9e7ef; font-weight:600; text-decoration:none; }}
  .title:hover {{ text-decoration:underline; }}
  .channel {{ color:#9a96a8; font-size:13px; }}
  .actions {{ display:flex; gap:8px; margin-top:4px; }}
  button {{ font:inherit; font-weight:600; font-size:13px; border-radius:6px; border:none;
            padding:6px 12px; cursor:pointer; }}
  .add {{ background:#8b7bd8; color:#fff; }}
  .skip {{ background:transparent; color:#9a96a8; border:1px solid #37343f; }}
  .card.done {{ opacity:0; transform:translateX(12px); pointer-events:none; }}
  .empty {{ color:#9a96a8; }}
  .footer {{ position:fixed; bottom:0; left:0; right:0; background:#1c1b22;
             border-top:1px solid #37343f; padding:12px 16px; text-align:center; }}
  .footer button {{ background:#37343f; color:#e9e7ef; padding:8px 18px; }}
</style></head>
<body>
  <h1>{len(pending)} new video(s)</h1>
  {cards}
  <div class="footer"><button onclick="finish()">Finish reviewing</button></div>
<script>
function act(id, action) {{
  fetch('/action?id=' + id + '&do=' + action).then(() => {{
    document.getElementById('card-' + id).classList.add('done');
  }});
}}
function finish() {{
  fetch('/shutdown').then(() => {{
    document.body.innerHTML = '<h1>Done — you can close this tab.</h1>';
  }});
}}
</script>
</body></html>"""


# --------------------------------------------------------------- server -----

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep terminal quiet

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            body = render_page(self.server.pending).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/action":
            qs = parse_qs(parsed.query)
            vid = qs.get("id", [None])[0]
            action = qs.get("do", [None])[0]
            video = next((v for v in self.server.pending if v["id"] == vid), None)
            if video and action in ("add", "skip"):
                if action == "add":
                    add_video(video)
                    self.server.added += 1
                    print(f"  + added: {video['title']}")
                else:
                    self.server.skipped += 1
                    print(f"  - skipped: {video['title']}")
                mark_seen(self.server.cache, vid)
                self.server.pending = [v for v in self.server.pending if v["id"] != vid]
            self.send_response(200)
            self.end_headers()

        elif parsed.path == "/shutdown":
            self.send_response(200)
            self.end_headers()
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        else:
            self.send_response(404)
            self.end_headers()


def main():
    pending, seen, bootstrapped = collect_pending()

    if not pending:
        print("\nNo new videos to review.")
        return

    cache = wc.load_cache()  # reload fresh copy for the server to mutate as you click
    httpd = HTTPServer(("127.0.0.1", PORT), Handler)
    httpd.pending = pending
    httpd.cache = cache
    httpd.added = 0
    httpd.skipped = 0

    url = f"http://127.0.0.1:{PORT}/"
    print(f"\n{len(pending)} new video(s) to review.")
    print(f"Opening {url}  (or open it manually if your browser doesn't launch)")
    print("Click 'Finish reviewing' when done, or just Ctrl+C here.\n")

    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

    print(f"\nDone. Added {httpd.added}, skipped {httpd.skipped}.")
    if httpd.added:
        print("Run enrich_youtube.py (or the auto version) to process the added ones.")


if __name__ == "__main__":
    main()

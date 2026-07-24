#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")"
/opt/homebrew/bin/python3 review_videos.py
echo
read -p "Press Enter to close..."

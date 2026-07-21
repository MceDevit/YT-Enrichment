#!/bin/bash
cd "$(dirname "$0")"
python3 review_videos.py
echo
read -p "Press Enter to close..."

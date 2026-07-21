#!/bin/bash
cd "$(dirname "$0")"
python3 enrich_youtube_auto.py
echo
read -p "Press Enter to close..."

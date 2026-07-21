#!/bin/bash
cd "$(dirname "$0")"
python3 watch_channels.py
echo
read -p "Press Enter to close..."

#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
source ~/.zshrc 2>/dev/null   # picks up YOUTUBE_API_KEY / ANTHROPIC_API_KEY — not sourced by non-interactive SSH otherwise
cd "$(dirname "$0")"
/opt/homebrew/bin/python3 watch_channels.py

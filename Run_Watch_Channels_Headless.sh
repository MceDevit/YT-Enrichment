#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")"
/opt/homebrew/bin/python3 -u watch_channels.py

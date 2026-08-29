"""Loads ANTHROPIC_API_KEY / YOUTUBE_API_KEY from .env (gitignored, project-local)
so the keys never need to live in the shell environment (~/.zshrc etc).
Imported for its side effect — see claude_api.py / dashboard.py.
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

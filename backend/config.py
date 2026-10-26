"""Settings come from environment variables, optionally loaded from a .env file (see .env.example).

The app looks for the .env file in two places and uses the first one it finds:
1. the repo's own folder: <repo>/.env
2. the folder above the repo: <repo>/../.env
Variables already set in the real environment always win over the file.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
ENV_FILES = (REPO / ".env", REPO.parent / ".env")


def load_settings(candidates=ENV_FILES) -> Path | None:
    """Load the first .env file that exists. Returns its path, or None if there is none."""
    for path in candidates:
        if Path(path).is_file():
            load_dotenv(path, override=False)
            return Path(path)
    return None


ENV_FILE = load_settings()

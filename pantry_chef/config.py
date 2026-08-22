"""Where things live."""

from __future__ import annotations

import os
from pathlib import Path

ENV_DB = "PANTRY_CHEF_DB"
LEGACY_ENV_DB = "PANTRY_DB"        # honoured so older setups keep working
DEFAULT_NAME = Path("data/pantry-chef.db")
LEGACY_NAMES = (Path("data/pantry.db"),)

# The project root, i.e. the directory containing the `pantry_chef` package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def db_path(override: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the database location.

    Order: an explicit flag, then $PANTRY_CHEF_DB, then `data/pantry-chef.db`
    beside the working directory, then the same path inside the project. The
    last step lets the CLI and the web app find the database when they are
    launched from somewhere else, which is the normal case for a server.

    A database left over from before the rename is picked up if the current
    one does not exist, so an existing library is not silently abandoned.
    """
    if override:
        return Path(override).expanduser()

    from_env = os.environ.get(ENV_DB) or os.environ.get(LEGACY_ENV_DB)
    if from_env:
        return Path(from_env).expanduser()

    candidates = [
        DEFAULT_NAME,
        *LEGACY_NAMES,
        PROJECT_ROOT / DEFAULT_NAME,
        *(PROJECT_ROOT / name for name in LEGACY_NAMES),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return DEFAULT_NAME

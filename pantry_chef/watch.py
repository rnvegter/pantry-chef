"""Automatic indexing: notice new and changed cookbooks, and index them.

The web app runs one watcher. Every few minutes it looks through the library
folders and hands any book that is new, or has changed, to the indexing job
the library page already shows.

It polls rather than subscribing to file-system events. Cookbook libraries
often live on a NAS, and over SMB or NFS change events are unreliable or
missing altogether; looking at the size and modification time of a few
hundred files costs milliseconds. A change is:

  * a book the index has never seen, or whose size differs from the indexed
    copy (a replaced file) — checked against the database, so books added
    while the app was not running are found when it starts;
  * a book whose size or modification time differs from the last look.

Only those books are indexed, and the indexer still hashes them, so a file
that was touched but not changed costs a hash, never a parse.

Two cautions. A book written in the last SETTLE_SECONDS is left for the next
look, so a file still being copied in is not read half-written. And a folder
that is missing — an unplugged drive, an unmounted share — is skipped: the
watcher only ever adds, it never takes recipes away.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db
from .index import find_books

logger = logging.getLogger(__name__)

ENV_AUTO_INDEX = "PANTRY_CHEF_AUTO_INDEX"      # minutes between looks, or "off"
DEFAULT_MINUTES = 5
SETTING = "auto_index"                          # on | off, chosen on the library page
SETTLE_SECONDS = 30
FIRST_LOOK_SECONDS = 15                         # after start-up, not during it


def configured_minutes(environ: dict[str, str] | None = None) -> int:
    """Minutes between looks from the environment; 0 means switched off there."""
    raw = (environ if environ is not None else os.environ).get(ENV_AUTO_INDEX, "").strip()
    if not raw:
        return DEFAULT_MINUTES
    if raw.lower() in ("off", "false", "no", "0"):
        return 0
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("%s=%r is not a number of minutes; using %d",
                       ENV_AUTO_INDEX, raw, DEFAULT_MINUTES)
        return DEFAULT_MINUTES


@dataclass
class _Status:
    last_check: float = 0.0
    next_check: float = 0.0
    last_found: int = 0
    last_started: float = 0.0
    last_error: str = ""
    folders: int = 0
    books: int = 0


class LibraryWatcher:
    """Looks for new books and starts the indexer on them."""

    def __init__(self, manager: Any, db_path: Callable[[], str | Path],
                 clock: Callable[[], float] = time.time,
                 environ: dict[str, str] | None = None) -> None:
        self._manager = manager
        self._db_path = db_path
        self._clock = clock
        self._minutes = configured_minutes(environ)
        self._seen: dict[str, tuple[int, int]] = {}      # path -> (size, mtime_ns)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = _Status()

    # --- settings ------------------------------------------------------------

    @property
    def minutes(self) -> int:
        return self._minutes

    def enabled(self) -> bool:
        """On unless switched off on the library page or in the environment."""
        if not self._minutes:
            return False
        path = Path(self._db_path())
        if not path.exists():
            return True
        conn = db.connect(path, read_only=True)
        try:
            return db.get_setting(conn, SETTING, "on") != "off"
        finally:
            conn.close()

    def set_enabled(self, on: bool) -> None:
        conn = db.connect(self._db_path())
        try:
            db.set_setting(conn, SETTING, "on" if on else "off")
        finally:
            conn.close()
        if on:
            self._wake.set()          # look now rather than in five minutes

    # --- looking ----------------------------------------------------------------

    def check_once(self) -> list[str]:
        """Look once. Returns the books handed to the indexer (possibly none)."""
        with self._lock:
            now = self._clock()
            self._status.last_check = now
            path = Path(self._db_path())
            if not path.exists():
                return []
            conn = db.connect(path)
            try:
                folders = [row["path"] for row in db.list_sources(conn) if row["enabled"]]
                indexed = {row["path"]: int(row["size_bytes"]) for row in conn.execute(
                    "SELECT path, size_bytes FROM books")}
            finally:
                conn.close()

            changed: list[str] = []
            present = [f for f in folders if Path(f).is_dir()]
            seen_now = 0
            for book in find_books(present):
                try:
                    stat = book.stat()
                except OSError:
                    continue
                seen_now += 1
                key = str(book)
                signature = (stat.st_size, stat.st_mtime_ns)
                previous = self._seen.get(key)
                if previous == signature:
                    continue
                if previous is None and indexed.get(key) == stat.st_size:
                    self._seen[key] = signature         # already indexed as it is
                    continue
                if now - stat.st_mtime < SETTLE_SECONDS:
                    continue                            # still being written; next time
                changed.append(key)

            self._status.folders = len(present)
            self._status.books = seen_now
            self._status.last_found = len(changed)
            if not changed:
                return []

            if self._manager.running:
                # The running job may well be indexing these already; if not,
                # the next look finds them again, since they are not marked seen.
                return []
            try:
                self._manager.start(changed, str(path), sources=present, origin="automatic")
            except RuntimeError:
                return []
            for key in changed:
                try:
                    stat = Path(key).stat()
                    self._seen[key] = (stat.st_size, stat.st_mtime_ns)
                except OSError:
                    pass
            self._status.last_started = now
            return changed

    # --- the thread -------------------------------------------------------------

    def start(self) -> None:
        """Begin looking in the background. Does nothing if switched off in the
        environment; the library page's switch is read at every look."""
        if not self._minutes or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="pantry-library-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None

    def _loop(self) -> None:
        delay = FIRST_LOOK_SECONDS
        while not self._stop.is_set():
            self._status.next_check = self._clock() + delay
            self._wake.wait(delay)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                if self.enabled():
                    self.check_once()
                    self._status.last_error = ""
            except Exception as exc:
                logger.warning("automatic indexing could not look at the library",
                               exc_info=True)
                self._status.last_error = f"{type(exc).__name__}: {exc}"
            delay = self._minutes * 60

    def status(self) -> dict[str, Any]:
        """What the library page shows about it."""
        on = self.enabled()
        running = bool(self._thread and self._thread.is_alive())
        return {
            "available": bool(self._minutes),
            "enabled": on,
            "running": running,
            "minutes": self._minutes,
            "last_check": self._status.last_check or None,
            "next_check": (self._status.next_check or None) if on and running else None,
            "last_found": self._status.last_found,
            "last_started": self._status.last_started or None,
            "folders": self._status.folders,
            "books": self._status.books,
            "error": self._status.last_error,
        }

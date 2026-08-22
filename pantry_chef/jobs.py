"""Run indexing in the background so the library page can watch it happen.

One job at a time, on a daemon thread. The web layer never calls `ingest`
directly: it starts a job and then polls `snapshot()`, which is cheap and
thread-safe.

Failures get a diagnosis rather than a stack trace. Almost every real-world
indexing failure has one of a handful of causes -- DRM, a scanned PDF, a
missing optional dependency -- and each has a concrete fix the user can apply.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import db
from .extract import SUPPORTED_SUFFIXES, is_supported
from .index import find_books, ingest

MAX_LOG_LINES = 400


# --- turning an error into something the user can act on -------------------

@dataclass(slots=True)
class Diagnosis:
    """A human explanation of a failure, and what to do about it."""

    cause: str
    fix: str
    fixable_here: bool = False   # True when a retry alone might work


_DIAGNOSES: list[tuple[str, Diagnosis]] = [
    (r"BadZipFile|not a zip|File is not a zip",
     Diagnosis(
         "The file is not readable as an EPUB archive. This is nearly always "
         "DRM, occasionally a truncated download.",
         "If the book is DRM-protected, remove the DRM from your own copy "
         "before indexing (Calibre with the DeDRM plugin is the usual route). "
         "Otherwise re-download the file.")),
    (r"install pymupdf|PDFUnavailable",
     Diagnosis(
         "PDF support is not installed.",
         "Install it with: pip install pymupdf",
         fixable_here=False)),
    (r"install the 'mobi' package|MobiUnavailable",
     Diagnosis(
         "Kindle-format support is not installed.",
         "Install it with: pip install mobi",
         fixable_here=False)),
    (r"PermissionError|Permission denied",
     Diagnosis(
         "The file could not be opened.",
         "Check the file's permissions, and that this app has access to the "
         "folder (on macOS, Files and Folders access in System Settings).")),
    (r"FileNotFoundError|No such file",
     Diagnosis(
         "The file has moved or been deleted since it was last seen.",
         "Re-scan the folder to drop it from the library.")),
    (r"file too large",
     Diagnosis(
         "The file is larger than the 400 MB safety limit, usually a scanned "
         "book stored as images.",
         "Such a book has no text to extract. Run OCR on it first, or leave "
         "it out of the library.")),
    (r"no reader for",
     Diagnosis(
         "The file format is not supported.",
         f"Supported formats are {', '.join(sorted(SUPPORTED_SUFFIXES))}. "
         "Convert the book with Calibre if you want it indexed.")),
    (r"worker crashed|terminated abruptly",
     Diagnosis(
         "The parser process stopped unexpectedly, usually on a malformed file.",
         "Retrying often works. If it fails again, the file is likely corrupt.",
         fixable_here=True)),
    (r"MemoryError",
     Diagnosis(
         "The book was too large to hold in memory.",
         "Index it on its own, with fewer parallel workers.",
         fixable_here=True)),
    (r"UnicodeDecodeError|codec can't decode",
     Diagnosis(
         "The book uses a text encoding we could not decode.",
         "Re-save or convert the book to a modern EPUB with Calibre.")),
]

_GENERIC = Diagnosis(
    "The book could not be parsed.",
    "Retry once; if it fails again, converting the file to EPUB with Calibre "
    "usually resolves it.",
    fixable_here=True,
)


def diagnose(error: str) -> Diagnosis:
    """Map a raw parser error onto a cause and a fix."""
    for pattern, diagnosis in _DIAGNOSES:
        if re.search(pattern, error or "", re.IGNORECASE):
            return diagnosis
    return _GENERIC


def diagnose_empty(book_format: str) -> Diagnosis:
    """Why a book read cleanly but produced no recipes."""
    if book_format == "pdf":
        return Diagnosis(
            "The PDF was read but contained no recognisable recipes. Most often "
            "this is a scanned book: the pages are images, so there is no text "
            "to extract.",
            "Run OCR over the file (macOS Preview, Acrobat, or ocrmypdf) and "
            "index it again. If it does have text, the layout may be too "
            "unusual for the parser.")
    return Diagnosis(
        "The book was read but no recipes were recognised.",
        "It may not be a cookbook, or its recipes may be laid out without "
        "ingredient lists. Lowering the confidence threshold when indexing can "
        "help marginal books.")


# --- the job ---------------------------------------------------------------

@dataclass
class JobState:
    """Live state of one indexing run."""

    id: str = ""
    status: str = "idle"        # idle | running | done | cancelled | failed
    roots: list[str] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    total: int = 0
    done: int = 0
    indexed: int = 0
    skipped: int = 0
    failed: int = 0
    recipes: int = 0
    current: str = ""
    message: str = ""
    log: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))

    def as_dict(self) -> dict[str, Any]:
        elapsed = (self.finished_at or time.time()) - self.started_at if self.started_at else 0.0
        remaining = 0.0
        if self.status == "running" and self.done and self.total:
            remaining = (elapsed / self.done) * max(0, self.total - self.done)
        return {
            "id": self.id,
            "status": self.status,
            "roots": self.roots,
            "total": self.total,
            "done": self.done,
            "indexed": self.indexed,
            "skipped": self.skipped,
            "failed": self.failed,
            "recipes": self.recipes,
            "current": self.current,
            "message": self.message,
            "elapsed": round(elapsed, 1),
            "eta": round(remaining, 1),
            "percent": round(100 * self.done / self.total, 1) if self.total else 0.0,
            "log": list(self.log),
        }


_PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]\s+(.*?)\s+->\s+(.*)$")
_FOUND_RE = re.compile(r"^found (\d+) book")


class IndexJobManager:
    """Owns the single background indexing job."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = JobState()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    # -- queries ----------------------------------------------------------

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state.status == "running"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._state.as_dict()

    # -- control ----------------------------------------------------------

    def start(self, roots: list[str], db_path: str | Path, *,
              force: bool = False, min_confidence: float = 0.4,
              workers: int | None = None) -> dict[str, Any]:
        """Begin indexing. Raises RuntimeError if a job is already running."""
        with self._lock:
            if self._state.status == "running":
                raise RuntimeError("an indexing run is already in progress")

            self._cancel.clear()
            self._state = JobState(
                id=uuid.uuid4().hex[:12],
                status="running",
                roots=[str(r) for r in roots],
                started_at=time.time(),
                message="scanning folders…",
            )
            state = self._state

        self._thread = threading.Thread(
            target=self._run,
            args=(list(roots), str(db_path), force, min_confidence, workers),
            daemon=True,
            name=f"pantry-index-{state.id}",
        )
        self._thread.start()
        return self.snapshot()

    def cancel(self) -> bool:
        """Ask a running job to stop after the current book."""
        with self._lock:
            if self._state.status != "running":
                return False
            self._state.message = "finishing the current book, then stopping…"
        self._cancel.set()
        return True

    # -- the worker -------------------------------------------------------

    def _on_progress(self, line: str) -> None:
        """Parse one line of ingest output into structured state."""
        with self._lock:
            state = self._state
            state.log.append(line)

            found = _FOUND_RE.match(line)
            if found:
                state.total = int(found.group(1))
                state.message = f"found {state.total} book(s)"
                return

            if line.startswith("skipping "):
                state.message = line
                return

            match = _PROGRESS_RE.match(line)
            if match:
                state.done = int(match.group(1))
                state.total = max(state.total, int(match.group(2)))
                state.current = match.group(3)
                outcome = match.group(4)
                if outcome.startswith("FAILED"):
                    state.failed += 1
                else:
                    state.indexed += 1
                state.message = f"indexing {state.current}"

    def _run(self, roots: list[str], db_path: str, force: bool,
             min_confidence: float, workers: int | None) -> None:
        try:
            report = ingest(
                roots, db_path,
                workers=workers,
                force=force,
                min_confidence=min_confidence,
                progress=self._on_progress,
                should_stop=self._cancel.is_set,
            )
            conn = db.connect(db_path)
            try:
                for root in roots:
                    db.mark_source_indexed(conn, str(root))
            finally:
                conn.close()

            with self._lock:
                state = self._state
                state.indexed = report.indexed
                state.skipped = report.skipped
                state.failed = report.failed
                state.recipes = report.recipes
                state.done = report.indexed + report.failed
                state.total = max(state.total, state.done)
                state.finished_at = time.time()
                state.current = ""
                state.status = "cancelled" if report.cancelled else "done"
                state.message = (
                    f"stopped after {report.indexed} book(s)"
                    if report.cancelled else
                    f"indexed {report.indexed} book(s), {report.recipes} recipes"
                    + (f", {report.skipped} unchanged" if report.skipped else "")
                    + (f", {report.failed} failed" if report.failed else "")
                )
        except Exception as exc:
            with self._lock:
                self._state.status = "failed"
                self._state.finished_at = time.time()
                self._state.message = f"{type(exc).__name__}: {exc}"
                self._state.log.append(f"run failed: {exc}")


# One manager per process; the web app imports this.
manager = IndexJobManager()


# --- folder inspection, for the "add a folder" flow ------------------------

def inspect_folder(path: str | Path) -> dict[str, Any]:
    """Check a candidate folder and count the books it holds."""
    folder = Path(path).expanduser()
    if not folder.exists():
        return {"ok": False, "error": "that path does not exist"}
    if folder.is_file():
        if not is_supported(folder):
            return {"ok": False, "error": "that file is not a supported ebook"}
        return {"ok": True, "path": str(folder.resolve()), "is_file": True,
                "books": 1, "by_format": {folder.suffix.lstrip("."): 1}}
    if not folder.is_dir():
        return {"ok": False, "error": "that path is not a folder"}

    by_format: dict[str, int] = {}
    total = 0
    for book in find_books([folder]):
        total += 1
        suffix = book.suffix.lower().lstrip(".")
        by_format[suffix] = by_format.get(suffix, 0) + 1
        if total >= 100_000:      # guard against a pathological tree
            break

    return {
        "ok": True,
        "path": str(folder.resolve()),
        "is_file": False,
        "books": total,
        "by_format": dict(sorted(by_format.items(), key=lambda kv: -kv[1])),
    }


def list_directories(path: str | Path | None) -> dict[str, Any]:
    """List sub-folders, so the page can offer a simple browser.

    A browser cannot hand a server a filesystem path, so the user either types
    one or walks the tree with this.
    """
    folder = Path(path).expanduser() if path else Path.home()
    try:
        folder = folder.resolve()
        if not folder.is_dir():
            folder = folder.parent
        entries = []
        for child in sorted(folder.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    entries.append({"name": child.name, "path": str(child)})
            except OSError:
                continue
    except (OSError, PermissionError) as exc:
        return {"ok": False, "error": str(exc), "path": str(folder)}

    return {
        "ok": True,
        "path": str(folder),
        "parent": str(folder.parent) if folder.parent != folder else None,
        "directories": entries[:500],
    }

"""Build the recipe database from a library of ebook files.

Parsing is CPU-bound and embarrassingly parallel, so books are parsed in a
process pool while a single thread does all the SQLite writing. Runs are
resumable: a book whose contents hash unchanged is skipped, so re-running over
a 500-book library after adding ten new ones costs ten books of work.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import traceback
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from . import db
from .extract import format_of, is_supported, read_book
from .models import Book, Recipe
from .parse.segment import find_recipes

# Books this large are almost always image-heavy scans; parsing them can stall.
MAX_BYTES = 400 * 1024 * 1024


@dataclass(slots=True)
class BookResult:
    """What one worker produced for one file."""

    path: str
    sha256: str
    size_bytes: int
    format: str
    title: str = ""
    author: str = ""
    recipes: list[Recipe] | None = None
    error: str = ""
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.error


def file_hash(path: str | Path, chunk: int = 1 << 20) -> str:
    """SHA-256 of a file, read in chunks so large books stay cheap."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def find_books(roots: Iterable[str | Path]) -> Iterator[Path]:
    """Walk one or more directories for supported ebook files."""
    for root in roots:
        root = Path(root).expanduser()
        if root.is_file():
            if is_supported(root):
                yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # Skip the noise macOS and calibre leave behind.
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                path = Path(dirpath) / name
                if is_supported(path):
                    yield path


def parse_one(path_str: str, min_confidence: float = 0.4) -> BookResult:
    """Parse a single book. Runs in a worker process; never raises."""
    started = time.perf_counter()
    path = Path(path_str)

    try:
        size = path.stat().st_size
        result = BookResult(
            path=str(path), sha256="", size_bytes=size, format=format_of(path)
        )
        if size > MAX_BYTES:
            result.error = f"file too large ({size / 1e6:.0f} MB)"
            return result

        result.sha256 = file_hash(path)
        blocks, meta = read_book(path)
        title = meta.get("title", "") or path.stem.replace("_", " ")
        recipes = find_recipes(
            blocks, min_confidence=min_confidence, book_title=title)

        result.title = title
        result.author = meta.get("creator", "")
        result.recipes = recipes
        result.seconds = time.perf_counter() - started
        return result
    except Exception as exc:  # a bad file must not stop the run
        return BookResult(
            path=str(path),
            sha256="",
            size_bytes=path.stat().st_size if path.exists() else 0,
            format=path.suffix.lstrip(".").lower(),
            error=f"{type(exc).__name__}: {exc}".strip()[:500],
            seconds=time.perf_counter() - started,
        )


def _canary() -> bool:
    """Trivial task used to prove a worker pool can actually start."""
    return True


def _main_is_importable() -> bool:
    """Whether the `spawn` start method can re-import the calling module.

    Checked statically, before any process is started. Spawning to find out is
    itself destructive: a caller run from stdin, or a script without an
    `if __name__ == "__main__"` guard, re-executes its top-level code inside
    every worker. Detecting the condition without spawning avoids that.
    """
    main = sys.modules.get("__main__")
    if main is None:
        return False
    if getattr(main, "__spec__", None) is not None:
        return True          # started with -m, always importable
    path = getattr(main, "__file__", None)
    if not path or path in ("<stdin>", "<string>", "-"):
        return False
    return Path(path).is_file()


def pool_usable(workers: int) -> bool:
    """Whether a process pool works here.

    Python uses the `spawn` start method on macOS, which re-imports the calling
    module. If that cannot work, one lost canary is cheaper than a whole
    library's worth of parsing.
    """
    if not _main_is_importable():
        return False
    try:
        with ProcessPoolExecutor(max_workers=min(2, workers)) as pool:
            return bool(pool.submit(_canary).result(timeout=60))
    except Exception:
        return False


def _store(conn, result: BookResult) -> int:
    """Write one parsed book into the database. Returns recipes stored."""
    book = Book(
        path=result.path,
        sha256=result.sha256,
        title=result.title,
        author=result.author,
        format=result.format,
        size_bytes=result.size_bytes,
        n_recipes=len(result.recipes or []),
        status="indexed" if result.ok else "failed",
        error=result.error,
    )
    with db.transaction(conn):
        book_id = db.upsert_book(conn, book)
        db.delete_book_recipes(conn, book_id)
        stored = db.insert_recipes(conn, book_id, result.recipes or [])
    return stored


@dataclass(slots=True)
class IngestReport:
    """Summary of one ingest run."""

    scanned: int = 0
    skipped: int = 0
    indexed: int = 0
    failed: int = 0
    recipes: int = 0
    seconds: float = 0.0
    cancelled: bool = False
    errors: list[tuple[str, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def ingest(
    roots: Iterable[str | Path],
    db_path: str | Path,
    *,
    workers: int | None = None,
    force: bool = False,
    limit: int | None = None,
    min_confidence: float = 0.4,
    progress: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> IngestReport:
    """Index every supported book under `roots` into the database at `db_path`."""
    started = time.perf_counter()
    report = IngestReport()
    say = progress or (lambda _msg: None)
    stop = should_stop or (lambda: False)

    conn = db.connect(db_path)
    try:
        paths = list(find_books(roots))
        if limit:
            paths = paths[:limit]
        report.scanned = len(paths)
        say(f"found {len(paths)} book(s)")

        # Cheap pre-filter: skip files whose hash is already indexed. Hashing is
        # I/O-bound and far cheaper than parsing, so it happens up front.
        pending: list[str] = []
        for path in paths:
            if stop():
                break
            if force:
                pending.append(str(path))
                continue
            try:
                if db.book_is_current(conn, str(path), file_hash(path)):
                    report.skipped += 1
                    continue
            except OSError:
                pass
            pending.append(str(path))

        if report.skipped:
            say(f"skipping {report.skipped} unchanged book(s)")
        if not pending:
            report.seconds = time.perf_counter() - started
            return report

        workers = workers or min(8, (os.cpu_count() or 4))
        done = 0

        if workers > 1 and not pool_usable(workers):
            say("process pool unavailable here; parsing serially")
            workers = 1

        if workers <= 1:
            results: Iterable[BookResult] = (
                parse_one(p, min_confidence) for p in pending
            )
            for result in results:
                done += 1
                _record(conn, result, report, say, done, len(pending))
                if stop():
                    report.cancelled = True
                    break
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(parse_one, p, min_confidence): p for p in pending
                }
                for future in as_completed(futures):
                    done += 1
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = BookResult(
                            path=futures[future], sha256="", size_bytes=0,
                            format="", error=f"worker crashed: {exc}",
                        )
                    _record(conn, result, report, say, done, len(pending))
                    if stop():
                        report.cancelled = True
                        # Drop queued work; books already parsed are kept.
                        for pending_future in futures:
                            pending_future.cancel()
                        break

        db.refresh_ingredient_counts(conn)
        conn.execute("PRAGMA optimize")
        conn.commit()
    finally:
        conn.close()

    report.seconds = time.perf_counter() - started
    return report


def _record(conn, result: BookResult, report: IngestReport,
            say: Callable[[str], None], done: int, total: int) -> None:
    """Store one result and report on it."""
    name = Path(result.path).name
    if result.ok:
        stored = _store(conn, result)
        report.indexed += 1
        report.recipes += stored
        say(f"[{done}/{total}] {name} -> {stored} recipe(s) in {result.seconds:.1f}s")
    else:
        _store(conn, result)
        report.failed += 1
        report.errors.append((name, result.error))
        say(f"[{done}/{total}] {name} -> FAILED: {result.error}")

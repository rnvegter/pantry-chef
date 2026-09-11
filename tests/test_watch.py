"""Automatic indexing: new and changed books in the library folders are found
and handed to the indexer, and nothing else is."""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

import make_fixtures

from pantry_chef import db
from pantry_chef.jobs import IndexJobManager
from pantry_chef.models import Book
from pantry_chef.watch import LibraryWatcher, configured_minutes

FIXTURES = Path(__file__).parent / "fixtures"
BOOK = "small-kitchen-well.epub"


class FakeManager:
    """Records what the watcher asks for instead of indexing."""

    def __init__(self) -> None:
        self.running = False
        self.calls: list[tuple[list[str], dict]] = []

    def start(self, roots, db_path, **kwargs):
        self.calls.append((list(roots), kwargs))
        return {}


def _age(path: Path, seconds: float = 300) -> None:
    """Make a file look as if it was written a while ago — finished copying."""
    past = time.time() - seconds
    os.utime(path, (past, past))


@pytest.fixture
def library(tmp_path):
    make_fixtures.build_all(FIXTURES)
    folder = tmp_path / "Cookbooks"
    folder.mkdir()
    database = tmp_path / "library.db"
    conn = db.connect(database)
    db.add_source(conn, str(folder))
    conn.close()
    return folder, database


def _watcher(database, manager=None, **env):
    return LibraryWatcher(manager or FakeManager(), lambda: str(database),
                          environ=dict(env))


def _drop(folder: Path, name: str = BOOK, *, settled: bool = True) -> Path:
    target = folder / name
    shutil.copy(FIXTURES / BOOK, target)
    if settled:
        _age(target)
    return target


# --- configuration -------------------------------------------------------------------

@pytest.mark.parametrize("value,minutes", [
    ("", 5), ("10", 10), ("1", 1), ("off", 0), ("0", 0), ("no", 0), ("soon", 5),
])
def test_the_interval_comes_from_the_environment(value, minutes):
    assert configured_minutes({"PANTRY_CHEF_AUTO_INDEX": value}) == minutes


def test_it_can_be_switched_off_and_on(library):
    _folder, database = library
    watcher = _watcher(database)
    assert watcher.enabled()
    watcher.set_enabled(False)
    assert not watcher.enabled()
    watcher.set_enabled(True)
    assert watcher.enabled()


def test_switched_off_in_the_environment_it_never_starts(library):
    _folder, database = library
    watcher = _watcher(database, PANTRY_CHEF_AUTO_INDEX="off")
    assert not watcher.enabled()
    watcher.start()
    assert watcher.status()["running"] is False
    assert watcher.status()["available"] is False


# --- what it finds --------------------------------------------------------------------

def test_a_new_book_is_handed_to_the_indexer(library):
    folder, database = library
    book = _drop(folder)
    manager = FakeManager()

    assert _watcher(database, manager).check_once() == [str(book)]
    (roots, kwargs), = manager.calls
    assert roots == [str(book)]
    assert kwargs["origin"] == "automatic"
    assert kwargs["sources"] == [str(folder)]


def test_a_book_still_being_copied_waits_for_the_next_look(library):
    folder, database = library
    book = _drop(folder, settled=False)
    watcher = _watcher(database)

    assert watcher.check_once() == []
    _age(book)
    assert watcher.check_once() == [str(book)]


def test_nothing_new_means_nothing_started(library):
    folder, database = library
    _drop(folder)
    manager = FakeManager()
    watcher = _watcher(database, manager)
    watcher.check_once()
    assert watcher.check_once() == []
    assert len(manager.calls) == 1


def test_a_changed_book_is_indexed_again(library):
    folder, database = library
    book = _drop(folder)
    watcher = _watcher(database)
    watcher.check_once()

    with open(book, "ab") as handle:        # replaced by a longer edition
        handle.write(b"\0" * 64)
    _age(book, 200)
    assert watcher.check_once() == [str(book)]


def test_a_book_already_indexed_is_left_alone_after_a_restart(library):
    """On its first look the watcher has seen nothing, so it asks the index."""
    folder, database = library
    book = _drop(folder)
    conn = db.connect(database)
    db.upsert_book(conn, Book(path=str(book), sha256="x", size_bytes=book.stat().st_size,
                              status="indexed"))
    conn.commit()
    conn.close()
    assert _watcher(database).check_once() == []


def test_a_missing_folder_is_skipped_and_nothing_is_removed(library, tmp_path):
    folder, database = library
    _drop(folder)
    conn = db.connect(database)
    db.add_source(conn, str(tmp_path / "Unplugged drive"))
    conn.close()
    manager = FakeManager()
    assert len(_watcher(database, manager).check_once()) == 1
    assert manager.calls[0][1]["sources"] == [str(folder)]


def test_while_a_job_runs_it_waits_and_looks_again(library):
    folder, database = library
    book = _drop(folder)
    manager = FakeManager()
    manager.running = True
    watcher = _watcher(database, manager)

    assert watcher.check_once() == []
    manager.running = False
    assert watcher.check_once() == [str(book)], "not marked seen while the job was busy"


def test_status_reports_the_last_look(library):
    folder, database = library
    _drop(folder)
    watcher = _watcher(database)
    watcher.check_once()
    status = watcher.status()
    assert status["enabled"] and status["minutes"] == 5
    assert status["last_found"] == 1 and status["books"] == 1 and status["folders"] == 1
    assert status["last_check"] and status["last_started"]


# --- the real thing ---------------------------------------------------------------

def test_a_dropped_book_ends_up_searchable(library):
    folder, database = library
    _drop(folder)
    manager = IndexJobManager()
    watcher = LibraryWatcher(manager, lambda: str(database), environ={})

    assert watcher.check_once()
    deadline = time.time() + 60
    while manager.running and time.time() < deadline:
        time.sleep(0.1)

    job = manager.snapshot()
    assert job["status"] == "done" and job["origin"] == "automatic"
    conn = db.connect(database, read_only=True)
    assert conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] >= 5
    assert conn.execute("SELECT last_indexed FROM sources").fetchone()[0], \
        "the folder should be marked as indexed, not just the book"
    conn.close()
    assert watcher.check_once() == [], "an indexed book is not indexed again"


# --- over HTTP ------------------------------------------------------------------

@pytest.fixture
def client(library, monkeypatch):
    _folder, database = library
    monkeypatch.setenv("PANTRY_CHEF_DB", str(database))
    from pantry_chef.web.app import app
    with TestClient(app) as test_client:
        yield test_client


def test_http_status_switch_and_check(client, library):
    folder, _database = library
    status = client.get("/api/library").json()["watch"]
    assert status["enabled"] is True

    assert client.post("/api/library/watch", json={"enabled": False}).json()["enabled"] is False
    assert client.get("/api/library/watch").json()["watch"]["enabled"] is False
    assert client.post("/api/library/watch", json={"enabled": True}).json()["enabled"] is True

    _drop(folder)
    # Switching back on wakes the background watcher for a look of its own, so
    # either that look or this one may be the one that starts the job; what
    # matters is that the book is indexed, by an automatic run.
    client.post("/api/library/watch/check")
    deadline = time.time() + 60
    while client.get("/api/library/job").json()["status"] == "running" and time.time() < deadline:
        time.sleep(0.1)
    job = client.get("/api/library/job").json()
    assert job["status"] == "done" and job["origin"] == "automatic"
    assert client.get("/api/library").json()["stats"]["recipes"] >= 5

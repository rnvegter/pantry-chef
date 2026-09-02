"""FastAPI app serving the pantry search UI and its JSON API.

Runs locally against the SQLite database built by `pantry index`. Connections
are per-request and read-only, so the app can stay up while a re-index runs.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query as Q
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import db
from ..config import db_path
from ..images import load_image
from ..jobs import diagnose, diagnose_empty, inspect_folder, list_directories, manager
from ..models import display_title, split_steps
from ..parse.classify import difficulty
from ..parse.metric import convert_text, to_metric_line
from ..search import Query, get_recipe, search, suggest_ingredients

STATIC_DIR = Path(__file__).parent / "static"

# Pages are revalidated for the same reason the static files are: they are
# read off local disk, so caching buys nothing and a stale copy costs an
# afternoon of wondering why an edit did not appear.
NO_CACHE = {"Cache-Control": "no-cache"}


def _page(name: str) -> FileResponse:
    return FileResponse(STATIC_DIR / name, headers=NO_CACHE)



app = FastAPI(title="Pantry Chef", docs_url="/api/docs", redoc_url=None)


def get_conn() -> sqlite3.Connection:
    """Open the database for one request."""
    target = db_path()
    if not Path(target).exists():
        raise HTTPException(
            status_code=503,
            detail=f"no database at {target} — run: python -m pantry_chef index <folder>",
        )
    return db.connect(target, read_only=True)


def get_writable_conn() -> sqlite3.Connection:
    """Open the database for writing, creating it if this is a first run.

    The library page has to work before any database exists -- that is what it
    is for -- so unlike the search endpoints it never 503s on a missing file.
    """
    return db.connect(db_path())


class SearchRequest(BaseModel):
    """What the UI sends when the user hits search."""

    have: list[str] = Field(default_factory=list)
    max_minutes: int | None = None
    max_missing: int = 3
    without: list[str] = Field(default_factory=list)
    text: str = ""
    title: str = ""
    author: str = ""
    book: str = ""
    meals: list[str] = Field(default_factory=list)
    cuisines: list[str] = Field(default_factory=list)
    diets: list[str] = Field(default_factory=list)
    free_from: list[str] = Field(default_factory=list)
    strict_diet: bool = False
    sort: str = "best"
    require_timed: bool = False
    allow_long_wait: bool = True
    limit: int = 40
    offset: int = 0


def _ingredient_json(item, *, metric: bool = False) -> dict:
    line = item.raw or item.display
    return {
        "canonical": item.canonical,
        "display": item.display,
        "line": to_metric_line(line) if metric else line,
        "original": line,
        "have": item.have,
        "staple": item.is_staple,
    }


def _result_json(result, *, full: bool = False, metric: bool = False) -> dict:
    data = {
        "id": result.recipe_id,
        "title": display_title(result.title),
        "title_original": result.title,
        "book": result.book_title or Path(result.book_path).name,
        "book_path": result.book_path,
        "section": result.section,
        "servings": result.servings,
        "page": result.page,
        "total_minutes": result.total_minutes,
        "active_minutes": result.active_minutes,
        "time_source": result.time_source,
        "time_is_estimate": result.time_source not in ("label", "labels-summed"),
        "has_long_wait": result.has_long_wait,
        "confidence": round(result.confidence, 2),
        "meals": result.meals,
        "cuisine": result.cuisine,
        "diets": result.diets,
        "allergens": result.allergens,
        "diet_caveats": result.diet_caveats,
        "n_unknown": result.n_unknown,
        "has_image": bool(result.image_ref),
        "image_url": f"/api/recipe/{result.recipe_id}/image" if result.image_ref else None,
        "n_core": result.n_core,
        "n_matched": result.n_matched,
        "coverage": round(result.coverage, 3),
        "score": round(result.score, 1),
        "missing": [_ingredient_json(i, metric=metric) for i in result.missing],
        "ingredients": [_ingredient_json(i, metric=metric) for i in result.ingredients],
    }
    if full:
        steps = split_steps(result.instructions)
        data["instructions"] = (
            convert_text(result.instructions) if metric else result.instructions)
        data["steps"] = [convert_text(s) if metric else s for s in steps]
        data["difficulty"] = difficulty(
            len(result.ingredients), len(steps),
            result.total_minutes, result.instructions)
    return data


@app.post("/api/search")
def api_search(request: SearchRequest) -> JSONResponse:
    """Rank recipes against a pantry and a time budget."""
    conn = get_conn()
    try:
        results, info = search(conn, Query(
            have=request.have,
            max_minutes=request.max_minutes,
            max_missing=request.max_missing,
            without=request.without,
            text=request.text,
            title=request.title,
            author=request.author,
            book=request.book,
            meals=request.meals,
            cuisines=request.cuisines,
            diets=request.diets,
            free_from=request.free_from,
            strict_diet=request.strict_diet,
            sort=request.sort,
            require_timed=request.require_timed,
            allow_long_wait=request.allow_long_wait,
            limit=max(1, min(request.limit, 200)),
            offset=max(0, request.offset),
        ))
        return JSONResponse({
            "results": [_result_json(r) for r in results],
            "pantry": info["pantry"],
            "unknown": info["unknown"],
            "relaxed_to": info.get("relaxed_to"),
            "duplicates_collapsed": info.get("duplicates_collapsed", 0),
        })
    finally:
        conn.close()


@app.get("/api/recipe/{recipe_id}")
def api_recipe(recipe_id: int, have: str = Q(default="", max_length=2000),
               units: str = Q(default="metric")) -> JSONResponse:
    """One recipe in full, including its method.

    `have` carries the caller's pantry so the ingredient list can show what is
    already in the kitchen rather than marking everything as missing.
    """
    conn = get_conn()
    try:
        pantry = [p.strip() for p in have.split(",") if p.strip()]
        recipe = get_recipe(conn, recipe_id, have=pantry)
        if not recipe:
            raise HTTPException(status_code=404, detail="no such recipe")
        return JSONResponse(
            _result_json(recipe, full=True, metric=(units != "original")))
    finally:
        conn.close()


@app.get("/api/complete")
def api_complete(field: str = Q(default="title"),
                 q: str = Q(default="", max_length=120),
                 limit: int = Q(default=8, ge=1, le=200)) -> JSONResponse:
    """Autocomplete for the by-name search fields, drawn from the library.

    Every suggestion is a value that actually exists in the index, so picking
    one cannot produce an empty result — which is the point, given the spelling
    of a name like "Bulsiewicz" is exactly what a person cannot guess.
    """
    # An empty query is the dropdown being opened to browse. A single letter is
    # someone mid-word, and answering that is noise.
    text = q.strip()
    if len(text) == 1:
        return JSONResponse([])

    conn = get_conn()
    try:
        if field == "author":
            rows = db.complete_authors(conn, text, limit)
        elif field == "book":
            rows = db.complete_book_titles(conn, text, limit)
        else:
            rows = [(display_title(t), n) for t, n in db.complete_titles(conn, text, limit)]
    finally:
        conn.close()

    return JSONResponse([{"value": value, "recipes": n} for value, n in rows])


@app.get("/api/recipe/{recipe_id}/image")
def api_recipe_image(recipe_id: int) -> Response:
    """The recipe's photograph, read out of the book it came from."""
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT r.image_ref, b.path FROM recipes r
               JOIN books b ON b.id = r.book_id WHERE r.id = ?""",
            (recipe_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row or not row["image_ref"]:
        raise HTTPException(status_code=404, detail="no photo for this recipe")

    loaded = load_image(row["path"], row["image_ref"],
                        cache_root=Path(db_path()).parent)
    if loaded is None:
        raise HTTPException(
            status_code=404,
            detail="the photo could not be read; the book may have moved")

    data, content_type = loaded
    return Response(
        content=data,
        media_type=content_type,
        # The photo cannot change without the book changing, so cache hard.
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.get("/api/suggest")
def api_suggest(q: str = Q(default="", max_length=60)) -> JSONResponse:
    """Autocomplete over ingredients present in the library."""
    if not q.strip():
        return JSONResponse([])
    conn = get_conn()
    try:
        return JSONResponse(suggest_ingredients(conn, q, limit=10))
    finally:
        conn.close()


@app.get("/api/stats")
def api_stats() -> JSONResponse:
    """Headline numbers about the indexed library."""
    conn = get_conn()
    try:
        data = db.stats(conn)
        # Facets for the filter controls: only offer what the library holds.
        data["meals"] = [
            {"name": v, "recipes": n} for v, n in db.tag_counts(conn, "meal")
        ]
        data["cuisines"] = [
            {"name": v, "recipes": n} for v, n in db.tag_counts(conn, "cuisine")
        ]
        data["authors"] = [
            {"name": name, "recipes": n} for name, n in db.list_authors(conn)
        ]
        # Not "books": that key already holds the indexed book count.
        data["book_titles"] = [
            {"name": name, "recipes": n} for name, n in db.list_book_titles(conn)
        ]
        data["diets"] = [
            {"name": v, "recipes": n} for v, n in db.tag_counts(conn, "diet")
        ]
        data["allergens"] = [
            {"name": v, "recipes": n} for v, n in db.tag_counts(conn, "allergen")
        ]
        data["popular"] = [
            {"name": r["canonical"], "recipes": int(r["n_recipes"])}
            for r in conn.execute(
                "SELECT canonical, n_recipes FROM ingredients"
                " WHERE is_staple = 0 AND n_recipes > 0"
                " ORDER BY n_recipes DESC LIMIT 24"
            )
        ]
        return JSONResponse(data)
    finally:
        conn.close()


# --- library: folders, indexing runs and failures -------------------------

class SourceRequest(BaseModel):
    path: str


class IndexRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)   # empty means all sources
    force: bool = False
    min_confidence: float = 0.4
    workers: int | None = None


class RetryRequest(BaseModel):
    path: str


def _book_json(row, diagnosis) -> dict:
    return {
        "path": row["path"],
        "name": Path(row["path"]).name,
        "title": row["title"],
        "format": row["format"],
        "size_mb": round((row["size_bytes"] or 0) / 1e6, 1),
        "n_recipes": row["n_recipes"],
        "error": row["error"],
        "cause": diagnosis.cause,
        "fix": diagnosis.fix,
        "retry_may_help": diagnosis.fixable_here,
    }


@app.get("/api/library")
def api_library() -> JSONResponse:
    """Folders, totals, and everything that needs attention."""
    conn = get_writable_conn()
    try:
        sources = []
        for row in db.list_sources(conn):
            info = inspect_folder(row["path"])
            sources.append({
                "id": row["id"],
                "path": row["path"],
                "added_at": row["added_at"],
                "last_indexed": row["last_indexed"],
                "exists": info.get("ok", False),
                "books_on_disk": info.get("books", 0),
                "by_format": info.get("by_format", {}),
                "error": None if info.get("ok") else info.get("error"),
            })

        return JSONResponse({
            "database": str(db_path()),
            "stats": db.stats(conn),
            "sources": sources,
            "failures": [
                _book_json(row, diagnose(row["error"])) for row in db.failed_books(conn)
            ],
            "empty": [
                _book_json(row, diagnose_empty(row["format"]))
                for row in db.empty_books(conn)
            ],
            "job": manager.snapshot(),
        })
    finally:
        conn.close()


@app.post("/api/library/inspect")
def api_inspect(request: SourceRequest) -> JSONResponse:
    """Check a folder before adding it, and report what is inside."""
    return JSONResponse(inspect_folder(request.path))


@app.get("/api/library/browse")
def api_browse(path: str = Q(default="")) -> JSONResponse:
    """List sub-folders, so a path can be picked rather than typed."""
    return JSONResponse(list_directories(path or None))


@app.post("/api/library/sources")
def api_add_source(request: SourceRequest) -> JSONResponse:
    """Add a folder to the library."""
    info = inspect_folder(request.path)
    if not info.get("ok"):
        raise HTTPException(status_code=400, detail=info.get("error", "invalid path"))

    conn = get_writable_conn()
    try:
        source_id = db.add_source(conn, info["path"])
        return JSONResponse({"id": source_id, **info})
    finally:
        conn.close()


@app.delete("/api/library/sources/{source_id}")
def api_remove_source(source_id: int) -> JSONResponse:
    """Stop watching a folder. Already-indexed recipes are left alone."""
    conn = get_writable_conn()
    try:
        if not db.remove_source(conn, source_id):
            raise HTTPException(status_code=404, detail="no such folder")
        return JSONResponse({"removed": source_id})
    finally:
        conn.close()


@app.post("/api/library/index")
def api_start_index(request: IndexRequest) -> JSONResponse:
    """Start an indexing run over the chosen folders, or all of them."""
    conn = get_writable_conn()
    try:
        paths = request.paths or [row["path"] for row in db.list_sources(conn)]
    finally:
        conn.close()

    if not paths:
        raise HTTPException(status_code=400, detail="add a folder first")

    try:
        return JSONResponse(manager.start(
            paths, db_path(),
            force=request.force,
            min_confidence=request.min_confidence,
            workers=request.workers,
        ))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/library/retry")
def api_retry(request: RetryRequest) -> JSONResponse:
    """Re-index one book that previously failed."""
    if not Path(request.path).exists():
        raise HTTPException(status_code=404, detail="that file is no longer there")
    try:
        return JSONResponse(manager.start([request.path], db_path(), force=True))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/library/job")
def api_job() -> JSONResponse:
    """Live progress of the current or most recent indexing run."""
    return JSONResponse(manager.snapshot())


@app.post("/api/library/job/cancel")
def api_cancel() -> JSONResponse:
    """Stop the running job after the book in flight."""
    return JSONResponse({"cancelled": manager.cancel(), **manager.snapshot()})


@app.get("/recipe/{recipe_id}")
def recipe_page(recipe_id: int) -> FileResponse:
    """Serve the standalone recipe card; it fetches its own data."""
    return _page("recipe.html")


@app.get("/library")
def library_page() -> FileResponse:
    """Serve the library management page."""
    return _page("library.html")


@app.get("/")
def index() -> FileResponse:
    """Serve the single-page UI."""
    return _page("index.html")


class FreshStaticFiles(StaticFiles):
    """Static files that are always revalidated.

    The default caching left the browser serving a stale stylesheet after an
    edit, which is confusing and, on a local app, buys nothing: these files come
    off the same disk the browser is running on. `no-cache` still allows a 304,
    so an unchanged file costs a request header and no body.
    """

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


if STATIC_DIR.exists():
    app.mount("/static", FreshStaticFiles(directory=STATIC_DIR), name="static")

"""SQLite storage.

Sized for a real library: 500+ books is on the order of 50,000-150,000 recipes
and a few million ingredient rows. SQLite handles that comfortably as long as
the ingredient join is properly indexed, which is what the search leans on.

The schema is deliberately normalised around `ingredients`, so matching a
pantry is an integer join rather than a text comparison.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from .models import Book, Recipe, RecipeIngredient

SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Folders the user has asked us to watch. Kept so the library page can
-- re-scan them without the user retyping paths.
CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY,
    path         TEXT NOT NULL UNIQUE,
    added_at     TEXT NOT NULL DEFAULT (datetime('now')),
    last_indexed TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS books (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    sha256      TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    author      TEXT NOT NULL DEFAULT '',
    format      TEXT NOT NULL DEFAULT '',
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    n_recipes   INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',
    error       TEXT NOT NULL DEFAULT '',
    indexed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_books_sha ON books(sha256);
CREATE INDEX IF NOT EXISTS idx_books_status ON books(status);

CREATE TABLE IF NOT EXISTS recipes (
    id             INTEGER PRIMARY KEY,
    book_id        INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    section        TEXT NOT NULL DEFAULT '',
    servings       TEXT NOT NULL DEFAULT '',
    instructions   TEXT NOT NULL DEFAULT '',
    total_minutes  INTEGER,
    active_minutes INTEGER,
    time_source    TEXT NOT NULL DEFAULT 'unknown',
    has_long_wait  INTEGER NOT NULL DEFAULT 0,
    page           INTEGER NOT NULL DEFAULT 0,
    order_in_book  INTEGER NOT NULL DEFAULT 0,
    confidence     REAL NOT NULL DEFAULT 0,
    n_ingredients  INTEGER NOT NULL DEFAULT 0,
    n_core         INTEGER NOT NULL DEFAULT 0,
    -- How many ingredients we could not recognise. Allergen filtering is
    -- positive-only, so this is what stops an unrecognised ingredient being
    -- silently read as "safe".
    n_unknown      INTEGER NOT NULL DEFAULT 0,
    diet_caveats   TEXT NOT NULL DEFAULT '',
    -- Where the recipe's photograph lives inside the source book. Images are
    -- referenced rather than copied: a library this size would otherwise carry
    -- gigabytes of duplicated artwork.
    image_ref      TEXT NOT NULL DEFAULT ''
);
-- The time filter runs before the ingredient join, so it needs its own index.
CREATE INDEX IF NOT EXISTS idx_recipes_time ON recipes(total_minutes);
CREATE INDEX IF NOT EXISTS idx_recipes_book ON recipes(book_id);
CREATE INDEX IF NOT EXISTS idx_recipes_core ON recipes(n_core);

CREATE TABLE IF NOT EXISTS ingredients (
    id        INTEGER PRIMARY KEY,
    canonical TEXT NOT NULL UNIQUE,
    is_staple INTEGER NOT NULL DEFAULT 0,
    n_recipes INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ingredients_canonical ON ingredients(canonical);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
    recipe_id     INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
    display       TEXT NOT NULL DEFAULT '',
    raw           TEXT NOT NULL DEFAULT '',
    quantity      REAL,
    unit          TEXT,
    note          TEXT NOT NULL DEFAULT '',
    is_staple     INTEGER NOT NULL DEFAULT 0,
    position      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (recipe_id, ingredient_id)
) WITHOUT ROWID;
-- This is the index the whole pantry search depends on: given the ingredients
-- a user has, find every recipe that uses any of them.
CREATE INDEX IF NOT EXISTS idx_ri_ingredient ON recipe_ingredients(ingredient_id, recipe_id);

-- Meal type and cuisine, as tags rather than columns: a recipe honestly suits
-- more than one meal, and a tag table keeps the filter a cheap indexed join.
CREATE TABLE IF NOT EXISTS recipe_tags (
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    kind      TEXT NOT NULL,        -- 'meal' | 'cuisine' | 'diet' | 'allergen'
    value     TEXT NOT NULL,
    PRIMARY KEY (recipe_id, kind, value)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_tags_lookup ON recipe_tags(kind, value, recipe_id);

-- contentless_delete=1 is what makes re-indexing possible: a plain
-- contentless FTS5 table rejects DELETE, so replacing a book's recipes would
-- fail. Requires SQLite 3.43+.
CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(
    title,
    ingredients_text,
    instructions,
    content='',
    contentless_delete=1,
    tokenize='unicode61 remove_diacritics 2'
);
"""


def connect(path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open the database, creating and tuning it if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    # ~64MB page cache; the ingredient index wants to stay resident.
    conn.execute("PRAGMA cache_size=-64000")
    if not read_only:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    return conn


# Columns added after the first release. CREATE TABLE IF NOT EXISTS will not
# add a column to a table that already exists, so they are applied by hand.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "recipes": [
        ("n_unknown", "INTEGER NOT NULL DEFAULT 0"),
        ("diet_caveats", "TEXT NOT NULL DEFAULT ''"),
        ("image_ref", "TEXT NOT NULL DEFAULT ''"),
    ],
}


def _rebuild_fts_if_needed(conn: sqlite3.Connection) -> bool:
    """Recreate the search index if it was built without contentless_delete.

    Without that option SQLite refuses to DELETE from the table, so re-indexing
    an already-indexed book fails. Databases created before this was fixed are
    rebuilt in place, from the recipes they already hold.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='recipes_fts'"
    ).fetchone()
    if not row or "contentless_delete" in (row["sql"] or ""):
        return False

    conn.execute("DROP TABLE recipes_fts")
    conn.executescript(
        """
        CREATE VIRTUAL TABLE recipes_fts USING fts5(
            title, ingredients_text, instructions,
            content='', contentless_delete=1,
            tokenize='unicode61 remove_diacritics 2'
        );
        """
    )
    conn.execute(
        """
        INSERT INTO recipes_fts (rowid, title, ingredients_text, instructions)
        SELECT r.id, r.title,
               COALESCE((SELECT GROUP_CONCAT(ri.display, ' ')
                         FROM recipe_ingredients ri WHERE ri.recipe_id = r.id), ''),
               r.instructions
        FROM recipes r
        """
    )
    return True


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older database up to the current schema, in place."""
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    _rebuild_fts_if_needed(conn)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a unit of work, rolling back on error."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# --- books -----------------------------------------------------------------

def get_book_by_path(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM books WHERE path = ?", (path,)).fetchone()


def book_is_current(conn: sqlite3.Connection, path: str, sha256: str) -> bool:
    """Whether this exact file has already been indexed successfully."""
    row = conn.execute(
        "SELECT sha256, status FROM books WHERE path = ?", (path,)
    ).fetchone()
    return bool(row and row["sha256"] == sha256 and row["status"] == "indexed")


def upsert_book(conn: sqlite3.Connection, book: Book) -> int:
    """Insert or update a book row and return its id."""
    conn.execute(
        """
        INSERT INTO books (path, sha256, title, author, format, size_bytes,
                           n_recipes, status, error, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(path) DO UPDATE SET
            sha256=excluded.sha256, title=excluded.title, author=excluded.author,
            format=excluded.format, size_bytes=excluded.size_bytes,
            n_recipes=excluded.n_recipes, status=excluded.status,
            error=excluded.error, indexed_at=datetime('now')
        """,
        (book.path, book.sha256, book.title, book.author, book.format,
         book.size_bytes, book.n_recipes, book.status, book.error),
    )
    row = conn.execute("SELECT id FROM books WHERE path = ?", (book.path,)).fetchone()
    return int(row["id"])


def delete_book_recipes(conn: sqlite3.Connection, book_id: int) -> None:
    """Drop a book's recipes so it can be re-indexed cleanly."""
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM recipes WHERE book_id = ?", (book_id,))]
    if not ids:
        return
    conn.executemany("DELETE FROM recipes_fts WHERE rowid = ?", [(i,) for i in ids])
    conn.execute("DELETE FROM recipes WHERE book_id = ?", (book_id,))


def list_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every watched folder, newest first."""
    return conn.execute(
        "SELECT * FROM sources ORDER BY added_at DESC"
    ).fetchall()


def add_source(conn: sqlite3.Connection, path: str) -> int:
    """Register a folder to index. Returns its id."""
    conn.execute(
        "INSERT INTO sources (path) VALUES (?) ON CONFLICT(path) DO NOTHING",
        (path,),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM sources WHERE path = ?", (path,)).fetchone()
    return int(row["id"])


def remove_source(conn: sqlite3.Connection, source_id: int) -> bool:
    """Stop watching a folder. Indexed recipes from it are left in place."""
    cursor = conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()
    return cursor.rowcount > 0


def mark_source_indexed(conn: sqlite3.Connection, path: str) -> None:
    conn.execute(
        "UPDATE sources SET last_indexed = datetime('now') WHERE path = ?", (path,)
    )
    conn.commit()


def list_authors(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Authors in the library, with how many recipes each accounts for."""
    return [
        (row["author"], int(row["n"]))
        for row in conn.execute(
            """SELECT b.author AS author, COUNT(r.id) AS n
               FROM books b JOIN recipes r ON r.book_id = b.id
               WHERE b.author <> '' GROUP BY b.author
               ORDER BY n DESC, b.author"""
        )
    ]


def list_book_titles(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Book titles in the library, with their recipe counts."""
    return [
        (row["title"], int(row["n"]))
        for row in conn.execute(
            """SELECT b.title AS title, COUNT(r.id) AS n
               FROM books b JOIN recipes r ON r.book_id = b.id
               WHERE b.title <> '' GROUP BY b.title
               ORDER BY n DESC, b.title"""
        )
    ]


def complete_titles(conn: sqlite3.Connection, prefix: str,
                    limit: int = 8) -> list[tuple[str, int]]:
    """Recipe titles beginning with, or containing a word beginning with, `prefix`.

    Matched through the FTS title column rather than with LIKE, so this stays
    fast on a library of 50,000 recipes. The same dish appears in several
    editions of a book, so titles are folded case-insensitively and counted.
    """
    words = [w for w in "".join(
        c if c.isalnum() or c.isspace() else " " for c in prefix
    ).split() if w]

    # Browsing with an empty box: the most-repeated titles first, which are the
    # dishes that appear across several books.
    if not words:
        rows = conn.execute(
            """SELECT MAX(title) AS title, COUNT(*) AS n FROM recipes
               GROUP BY LOWER(title) ORDER BY LOWER(title) LIMIT ?""",
            (limit,),
        ).fetchall()
        return [(row["title"], int(row["n"])) for row in rows]

    match = " AND ".join(f'title:"{w}"*' for w in words)

    try:
        rows = conn.execute(
            """SELECT MAX(r.title) AS title, COUNT(*) AS n
               FROM recipes r
               WHERE r.id IN (SELECT rowid FROM recipes_fts WHERE recipes_fts MATCH ?)
               GROUP BY LOWER(r.title)
               ORDER BY n DESC, LENGTH(r.title), LOWER(r.title)
               LIMIT ?""",
            (match, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []       # a query FTS cannot parse is a miss, not an error
    return [(row["title"], int(row["n"])) for row in rows]


def complete_authors(conn: sqlite3.Connection, fragment: str,
                     limit: int = 8) -> list[tuple[str, int]]:
    """Authors whose name contains `fragment`, with their recipe counts."""
    return _complete_book_column(conn, "author", fragment, limit)


def complete_book_titles(conn: sqlite3.Connection, fragment: str,
                         limit: int = 8) -> list[tuple[str, int]]:
    """Book titles containing `fragment`, with their recipe counts."""
    return _complete_book_column(conn, "title", fragment, limit)


def _complete_book_column(conn: sqlite3.Connection, column: str,
                          fragment: str, limit: int) -> list[tuple[str, int]]:
    """Substring match over one column of `books`.

    LIKE is fine here where it would not be for recipes: there is one row per
    book, so even a large library is a few hundred rows. Substring rather than
    prefix matters — people type "Ottolenghi" for a book called
    "Simple: A Cookbook by Yotam Ottolenghi".
    """
    fragment = fragment.strip()
    # An empty fragment is a request to browse, not a mistake: the dropdown
    # opens with the whole list so you can pick rather than guess a spelling.
    where, params = "", []
    if fragment:
        where = f"AND b.{column} LIKE ?"
        params.append(f"%{fragment}%")

    rows = conn.execute(
        f"""SELECT b.{column} AS value, COUNT(r.id) AS n
            FROM books b JOIN recipes r ON r.book_id = b.id
            WHERE b.{column} <> '' {where}
            GROUP BY b.{column}
            ORDER BY n DESC, b.{column}
            LIMIT ?""",
        (*params, limit),
    ).fetchall()
    return [(row["value"], int(row["n"])) for row in rows]


def failed_books(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Books that could not be read."""
    return conn.execute(
        "SELECT * FROM books WHERE status = 'failed' ORDER BY indexed_at DESC"
    ).fetchall()


def empty_books(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Books that were read fine but yielded no recipes.

    Not an error, but almost always worth showing: it usually means a scanned
    PDF with no text layer, or a book that is not a cookbook at all.
    """
    return conn.execute(
        "SELECT * FROM books WHERE status = 'indexed' AND n_recipes = 0"
        " ORDER BY indexed_at DESC"
    ).fetchall()


# --- ingredients -----------------------------------------------------------

def intern_ingredients(
    conn: sqlite3.Connection, names: dict[str, bool]
) -> dict[str, int]:
    """Map canonical names to ids, inserting any that are new.

    `names` maps canonical name -> is_staple.
    """
    if not names:
        return {}

    conn.executemany(
        "INSERT OR IGNORE INTO ingredients (canonical, is_staple) VALUES (?, ?)",
        [(name, int(staple)) for name, staple in names.items()],
    )

    out: dict[str, int] = {}
    keys = list(names)
    for start in range(0, len(keys), 500):
        chunk = keys[start:start + 500]
        placeholders = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT id, canonical FROM ingredients WHERE canonical IN ({placeholders})",
            chunk,
        ):
            out[row["canonical"]] = int(row["id"])
    return out


def lookup_ingredient_ids(
    conn: sqlite3.Connection, canonicals: list[str]
) -> dict[str, int]:
    """Ids for canonical names that already exist; unknown names are omitted."""
    if not canonicals:
        return {}
    placeholders = ",".join("?" * len(canonicals))
    return {
        row["canonical"]: int(row["id"])
        for row in conn.execute(
            f"SELECT id, canonical FROM ingredients WHERE canonical IN ({placeholders})",
            canonicals,
        )
    }


# --- recipes ---------------------------------------------------------------

def insert_recipes(
    conn: sqlite3.Connection, book_id: int, recipes: list[Recipe]
) -> int:
    """Write a book's recipes, their ingredients and their search index."""
    if not recipes:
        return 0

    wanted: dict[str, bool] = {}
    for recipe in recipes:
        for ingredient in recipe.ingredients:
            wanted[ingredient.canonical] = ingredient.is_staple
    ingredient_ids = intern_ingredients(conn, wanted)

    for recipe in recipes:
        core = [i for i in recipe.ingredients if not i.is_staple]
        cursor = conn.execute(
            """
            INSERT INTO recipes (book_id, title, section, servings, instructions,
                total_minutes, active_minutes, time_source, has_long_wait, page,
                order_in_book, confidence, n_ingredients, n_core,
                n_unknown, diet_caveats, image_ref)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (book_id, recipe.title, recipe.section, recipe.servings,
             recipe.instructions, recipe.total_minutes, recipe.active_minutes,
             recipe.time_source, int(recipe.has_long_wait), recipe.page,
             recipe.order_in_book, recipe.confidence,
             len(recipe.ingredients), len(core),
             recipe.n_unknown, recipe.diet_caveats, recipe.image_ref),
        )
        recipe_id = int(cursor.lastrowid)
        recipe.id = recipe_id

        rows = []
        seen: set[int] = set()
        for ingredient in recipe.ingredients:
            ingredient_id = ingredient_ids.get(ingredient.canonical)
            if ingredient_id is None or ingredient_id in seen:
                continue
            seen.add(ingredient_id)
            rows.append((
                recipe_id, ingredient_id, ingredient.display, ingredient.raw,
                ingredient.quantity, ingredient.unit, ingredient.note,
                int(ingredient.is_staple), ingredient.position,
            ))
        conn.executemany(
            """INSERT OR IGNORE INTO recipe_ingredients
               (recipe_id, ingredient_id, display, raw, quantity, unit, note,
                is_staple, position)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            rows,
        )

        tags = [(recipe_id, "meal", meal) for meal in recipe.meals]
        if recipe.cuisine:
            tags.append((recipe_id, "cuisine", recipe.cuisine))
        tags += [(recipe_id, "diet", diet) for diet in recipe.diets]
        tags += [(recipe_id, "allergen", a) for a in recipe.allergens]
        conn.executemany(
            "INSERT OR IGNORE INTO recipe_tags (recipe_id, kind, value)"
            " VALUES (?, ?, ?)",
            tags,
        )

        conn.execute(
            "INSERT INTO recipes_fts (rowid, title, ingredients_text, instructions)"
            " VALUES (?, ?, ?, ?)",
            (recipe_id, recipe.title,
             " ".join(i.display for i in recipe.ingredients),
             recipe.instructions),
        )

    return len(recipes)


def refresh_ingredient_counts(conn: sqlite3.Connection) -> None:
    """Recompute how many recipes use each ingredient (drives suggestions)."""
    conn.execute(
        """
        UPDATE ingredients SET n_recipes = COALESCE((
            SELECT COUNT(*) FROM recipe_ingredients ri
            WHERE ri.ingredient_id = ingredients.id
        ), 0)
        """
    )
    conn.commit()


def stats(conn: sqlite3.Connection) -> dict[str, int | float]:
    """Headline numbers for the CLI and the web UI."""
    def one(sql: str, default: int | float = 0) -> int | float:
        row = conn.execute(sql).fetchone()
        return (row[0] if row and row[0] is not None else default)

    return {
        "books": int(one("SELECT COUNT(*) FROM books WHERE status='indexed'")),
        "books_failed": int(one("SELECT COUNT(*) FROM books WHERE status='failed'")),
        "recipes": int(one("SELECT COUNT(*) FROM recipes")),
        "ingredients": int(one("SELECT COUNT(*) FROM ingredients")),
        "links": int(one("SELECT COUNT(*) FROM recipe_ingredients")),
        "avg_confidence": round(float(one("SELECT AVG(confidence) FROM recipes")), 3),
        "with_photo": int(one(
            "SELECT COUNT(*) FROM recipes WHERE image_ref <> ''"
        )),
        "timed": int(one(
            "SELECT COUNT(*) FROM recipes WHERE time_source IN ('label','labels-summed')"
        )),
        "tagged": int(one(
            "SELECT COUNT(DISTINCT recipe_id) FROM recipe_tags WHERE kind='meal'"
        )),
        "with_cuisine": int(one(
            "SELECT COUNT(DISTINCT recipe_id) FROM recipe_tags WHERE kind='cuisine'"
        )),
    }


def tag_counts(conn: sqlite3.Connection, kind: str) -> list[tuple[str, int]]:
    """Every value of a tag kind, with how many recipes carry it."""
    return [
        (row["value"], int(row["n"]))
        for row in conn.execute(
            "SELECT value, COUNT(*) AS n FROM recipe_tags WHERE kind = ?"
            " GROUP BY value ORDER BY n DESC",
            (kind,),
        )
    ]

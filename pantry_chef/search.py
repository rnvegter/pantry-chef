"""Find recipes you can cook from what you have, in the time you have.

The ranking answers a cook's real question. Coverage alone is a bad sort: a
three-ingredient recipe you fully have should beat a twelve-ingredient one you
have nine of, but a recipe missing a single item is often still worth showing.
So missing items are penalised steeply, coverage rewarded, and ties broken by
time and extraction confidence.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, replace

from .parse.ingredients import canonicalize
from .parse.lexicon import STAPLES

# Weights, in points. Missing an ingredient hurts roughly a third of a recipe's
# worth of coverage, so "almost makeable" still surfaces but never outranks
# "makeable right now".
W_COVERAGE = 100.0
W_MISSING = 34.0
W_CONFIDENCE = 8.0
W_TIME_FIT = 6.0
W_PANTRY_USE = 6.0


@dataclass(slots=True)
class MatchedIngredient:
    """One ingredient of a result, and whether the cook has it."""

    canonical: str
    display: str
    have: bool
    is_staple: bool
    raw: str = ""      # the line exactly as the book wrote it


@dataclass(slots=True)
class SearchResult:
    """One recipe, scored against the pantry."""

    recipe_id: int
    title: str
    book_title: str
    book_path: str
    section: str
    servings: str
    page: int
    total_minutes: int | None
    active_minutes: int | None
    time_source: str
    has_long_wait: bool
    confidence: float
    meals: list[str]
    cuisine: str
    diets: list[str]
    allergens: list[str]
    diet_caveats: str
    n_unknown: int
    image_ref: str
    n_core: int
    n_matched: int
    score: float
    instructions: str = ""
    ingredients: list[MatchedIngredient] = field(default_factory=list)

    @property
    def missing(self) -> list[MatchedIngredient]:
        return [i for i in self.ingredients if not i.have and not i.is_staple]

    @property
    def coverage(self) -> float:
        return 1.0 if self.n_core == 0 else self.n_matched / self.n_core


@dataclass(slots=True)
class Query:
    """Everything a search can be asked for."""

    have: list[str] = field(default_factory=list)
    max_minutes: int | None = None
    max_missing: int = 2
    without: list[str] = field(default_factory=list)
    text: str = ""
    title: str = ""     # words in the recipe's own title
    author: str = ""    # the book's author
    book: str = ""      # the book's title
    meals: list[str] = field(default_factory=list)      # breakfast/lunch/dinner/...
    cuisines: list[str] = field(default_factory=list)   # italian/thai/...
    diets: list[str] = field(default_factory=list)      # vegetarian/vegan/...
    free_from: list[str] = field(default_factory=list)  # allergens to exclude
    strict_diet: bool = False   # drop recipes containing unrecognised ingredients
    min_confidence: float = 0.0
    require_timed: bool = False       # only recipes whose time is stated
    allow_long_wait: bool = True
    expand: bool = True               # "chicken" also matches "chicken thigh"
    sort: str = "best"                # best | quickest | coverage
    dedupe: bool = True               # collapse the same dish across editions
    auto_relax: bool = True           # widen max_missing rather than show nothing
    limit: int = 50
    offset: int = 0


def normalise_pantry(items: list[str]) -> list[str]:
    """Turn whatever the user typed into canonical ingredient names."""
    out: list[str] = []
    for item in items:
        for part in item.replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            canonical = canonicalize(part)
            if canonical and canonical not in out:
                out.append(canonical)
    return out


def _expand_ids(
    conn: sqlite3.Connection, canonicals: list[str], expand: bool
) -> tuple[dict[int, str], list[str]]:
    """Resolve canonical names to ingredient ids, optionally widening.

    Widening matches on whole words, so "chicken" picks up "chicken thigh" and
    "cheese" picks up "goat cheese", while "ice" never picks up "rice".
    """
    if not canonicals:
        return {}, []

    ids: dict[int, str] = {}
    unknown: list[str] = []

    for canonical in canonicals:
        rows = conn.execute(
            "SELECT id, canonical FROM ingredients WHERE canonical = ?", (canonical,)
        ).fetchall()

        if expand:
            rows += conn.execute(
                """SELECT id, canonical FROM ingredients
                   WHERE canonical LIKE ? OR canonical LIKE ? OR canonical LIKE ?""",
                (f"{canonical} %", f"% {canonical}", f"% {canonical} %"),
            ).fetchall()

        if not rows:
            unknown.append(canonical)
            continue
        for row in rows:
            ids.setdefault(int(row["id"]), canonical)

    return ids, unknown


def _placeholders(n: int) -> str:
    return ",".join("?" * n)


def search(conn: sqlite3.Connection, query: Query) -> tuple[list[SearchResult], dict]:
    """Run a pantry search, widening the tolerance if nothing matches.

    Real recipes carry ten to fifteen ingredients, so a modest pantry misses
    several in almost everything. Rather than show an empty screen, retry with
    a larger shopping list and say so.
    """
    results, info = _search_once(conn, query)
    if results or not query.auto_relax or not query.have:
        return results, info

    for tolerance in (query.max_missing + 2, query.max_missing + 4, 99):
        if tolerance <= query.max_missing:
            continue
        widened = replace(query, max_missing=tolerance, auto_relax=False)
        results, info = _search_once(conn, widened)
        if results:
            info["relaxed_to"] = tolerance
            return results, info

    return results, info


def _build_filters(conn: sqlite3.Connection, query: Query,
                   skip_facet: str | None = None) -> tuple[str, list[str], list[object], dict, dict]:
    """Assemble the FROM clause, WHERE fragments and parameters for a query.

    `skip_facet` leaves out one facet kind's own filter. Counting facets needs
    that: the number beside "Lunch" has to be counted with the meal selection
    removed, or the button would only ever report the meals already chosen.
    """
    pantry = normalise_pantry(query.have)
    pantry_ids, unknown = _expand_ids(conn, pantry, query.expand)
    excluded = normalise_pantry(query.without)
    excluded_ids, _ = _expand_ids(conn, excluded, query.expand)

    info = {
        "pantry": pantry,
        "unknown": unknown,
        "matched_ingredient_ids": len(pantry_ids),
        "excluded": excluded,
    }

    where: list[str] = []
    params: list[object] = []

    if pantry_ids:
        # Count only non-staple matches: staples are assumed present anyway, so
        # letting them count would flatter every recipe equally.
        sql = f"""
        WITH matched AS (
            SELECT ri.recipe_id AS rid,
                   SUM(CASE WHEN ri.is_staple = 0 THEN 1 ELSE 0 END) AS n_matched
            FROM recipe_ingredients ri
            WHERE ri.ingredient_id IN ({_placeholders(len(pantry_ids))})
            GROUP BY ri.recipe_id
        )
        SELECT r.*, b.title AS book_title, b.path AS book_path,
               COALESCE(m.n_matched, 0) AS n_matched
        FROM matched m
        JOIN recipes r ON r.id = m.rid
        JOIN books b ON b.id = r.book_id
        """
        params.extend(pantry_ids.keys())
        where.append("(r.n_core - COALESCE(m.n_matched, 0)) <= ?")
        params.append(max(0, query.max_missing))
    else:
        # No pantry given: this is a plain browse, ranked by time and quality.
        sql = """
        SELECT r.*, b.title AS book_title, b.path AS book_path, 0 AS n_matched
        FROM recipes r
        JOIN books b ON b.id = r.book_id
        """

    if query.max_minutes is not None:
        where.append("r.total_minutes IS NOT NULL AND r.total_minutes <= ?")
        params.append(query.max_minutes)
    if query.require_timed:
        where.append("r.time_source IN ('label','labels-summed')")
    if not query.allow_long_wait:
        where.append("r.has_long_wait = 0")
    if query.min_confidence > 0:
        where.append("r.confidence >= ?")
        params.append(query.min_confidence)

    # Meal and cuisine are OR within a kind, AND across kinds: "breakfast or
    # brunch, but Italian only".
    for kind, values in (("meal", query.meals), ("cuisine", query.cuisines)):
        wanted = [v.strip().lower() for v in values if v and v.strip()]
        if not wanted or kind == skip_facet:
            continue
        where.append(
            f"""EXISTS (SELECT 1 FROM recipe_tags t WHERE t.recipe_id = r.id
                AND t.kind = ? AND t.value IN ({_placeholders(len(wanted))}))"""
        )
        params.append(kind)
        params.extend(wanted)

    # Diets are ANDed: "vegetarian" plus "no red meat" must both hold.
    for diet in ({} if skip_facet == "diet"
                 else {d.strip().lower() for d in query.diets if d and d.strip()}):
        where.append(
            """EXISTS (SELECT 1 FROM recipe_tags t WHERE t.recipe_id = r.id
                AND t.kind = 'diet' AND t.value = ?)"""
        )
        params.append(diet)

    # Allergen exclusion is the one filter where a false negative matters, so
    # it is a plain NOT EXISTS over positively-detected allergens, optionally
    # backed by strict mode below.
    free_from = ({} if skip_facet == "allergen"
                 else {a.strip().lower() for a in query.free_from if a and a.strip()})
    if free_from:
        where.append(
            f"""NOT EXISTS (SELECT 1 FROM recipe_tags t WHERE t.recipe_id = r.id
                AND t.kind = 'allergen' AND t.value IN ({_placeholders(len(free_from))}))"""
        )
        params.extend(sorted(free_from))

    # Strict mode only makes sense alongside a diet or allergen filter: it drops
    # anything containing an ingredient we could not identify, trading recall
    # for the ability to say the answer is complete.
    if query.strict_diet and (free_from or query.diets):
        where.append("r.n_unknown = 0")

    if query.text.strip():
        where.append(
            "r.id IN (SELECT rowid FROM recipes_fts WHERE recipes_fts MATCH ?)"
        )
        params.append(_fts_query(query.text))

    # Title search runs against the FTS title column only, so "lemon" finds
    # recipes named for lemon rather than every recipe that uses one.
    if query.title.strip():
        where.append(
            "r.id IN (SELECT rowid FROM recipes_fts WHERE recipes_fts MATCH ?)"
        )
        params.append(_fts_query(query.title, column="title"))

    # Author and book are plain substring matches: the set of books is small,
    # and people type half a name.
    # Substring matches: the set of books is small, and people type half a name.
    if query.author.strip():
        where.append("b.author LIKE ?")
        params.append(f"%{query.author.strip()}%")
    if query.book.strip():
        where.append("b.title LIKE ?")
        params.append(f"%{query.book.strip()}%")

    if excluded_ids:
        where.append(
            f"""NOT EXISTS (SELECT 1 FROM recipe_ingredients x
                WHERE x.recipe_id = r.id
                  AND x.ingredient_id IN ({_placeholders(len(excluded_ids))}))"""
        )
        params.extend(excluded_ids.keys())

    return sql, where, params, info, pantry_ids


FACET_KINDS = ("meal", "cuisine", "diet", "allergen")


def facet_counts(conn: sqlite3.Connection, query: Query) -> dict[str, dict[str, int]]:
    """How many recipes sit under each facet value, for the search as it stands.

    The numbers on the filter buttons are only useful if they describe what is
    actually in front of you: with an Italian filter on, "Lunch" should say how
    many Italian lunches there are, not how many lunches the library holds.

    Each facet is counted with its own filter removed, which is what makes the
    buttons usable — a meal count computed through the meal filter could only
    ever report the meals already chosen. Facets with nothing selected all
    share a single pass, so this is usually one query and never more than four.
    """
    counts: dict[str, dict[str, int]] = {kind: {} for kind in FACET_KINDS}

    selected = {
        "meal": [v for v in query.meals if v and v.strip()],
        "cuisine": [v for v in query.cuisines if v and v.strip()],
        "diet": [v for v in query.diets if v and v.strip()],
        "allergen": [v for v in query.free_from if v and v.strip()],
    }

    passes: dict[str | None, list[str]] = {
        None: [k for k in FACET_KINDS if not selected[k]]
    }
    for kind in FACET_KINDS:
        if selected[kind]:
            passes[kind] = [kind]

    for skip, kinds in passes.items():
        if not kinds:
            continue
        sql, where, params, _info, _ids = _build_filters(conn, query, skip_facet=skip)
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = conn.execute(
            f"""SELECT t.kind AS kind, t.value AS value, COUNT(*) AS n
                FROM ({sql}) sub
                JOIN recipe_tags t ON t.recipe_id = sub.id
                WHERE t.kind IN ({_placeholders(len(kinds))})
                GROUP BY t.kind, t.value""",
            (*params, *kinds),
        ).fetchall()
        for row in rows:
            counts[row["kind"]][row["value"]] = int(row["n"])

    return counts


def _search_once(conn: sqlite3.Connection, query: Query) -> tuple[list[SearchResult], dict]:
    """One pass of the search at exactly the tolerance asked for."""
    sql, where, params, info, pantry_ids = _build_filters(conn, query)

    if where:
        sql += " WHERE " + " AND ".join(where)

    # Scoring lives in SQL so paging stays correct on large libraries. These
    # expressions run against the subquery, where `r.*` has already been
    # flattened, so the columns are referenced unqualified.
    coverage = "(CASE WHEN n_core = 0 THEN 1.0 ELSE CAST(n_matched AS REAL) / n_core END)"
    missing = "(n_core - n_matched)"
    time_fit = ("(CASE WHEN total_minutes IS NULL THEN 0.0 "
                "ELSE MAX(0.0, 1.0 - total_minutes / 180.0) END)")
    if pantry_ids:
        score = (f"{W_COVERAGE} * {coverage} - {W_MISSING} * MAX(0, {missing}) "
                 f"+ {W_CONFIDENCE} * confidence + {W_TIME_FIT} * {time_fit} "
                 f"+ {W_PANTRY_USE} * MIN(n_matched, 6) / 6.0")
    else:
        # Browsing without a pantry: penalising "missing" would just rank by
        # ingredient count, so rank on quality and speed instead.
        score = f"{W_CONFIDENCE} * confidence + {W_TIME_FIT} * {time_fit}"

    if query.sort == "quickest":
        order = ("COALESCE(total_minutes, 999999) ASC, " + score + " DESC")
    elif query.sort == "coverage":
        order = f"{coverage} DESC, {missing} ASC, confidence DESC"
    else:
        order = f"{score} DESC, COALESCE(total_minutes, 999999) ASC"

    # Over-fetch when de-duplicating so the page still fills after collapsing.
    fetch = query.limit * 3 if query.dedupe else query.limit
    sql = f"SELECT *, ({score}) AS score FROM ({sql}) ORDER BY {order} LIMIT ? OFFSET ?"
    params.extend([fetch, query.offset])

    rows = conn.execute(sql, params).fetchall()
    results = [_build_result(conn, row, set(pantry_ids)) for row in rows]

    if query.dedupe:
        results, duplicates = _dedupe(results)
        info["duplicates_collapsed"] = duplicates
    results = results[:query.limit]

    info["returned"] = len(results)
    return results, info


def _dedupe(results: list[SearchResult]) -> tuple[list[SearchResult], int]:
    """Collapse the same dish appearing in several books or editions.

    Libraries of this size routinely hold a book twice -- an EPUB and a MOBI of
    the same title, or two editions. Keying on the title plus the core
    ingredient set catches those without merging genuinely different recipes
    that happen to share a name.
    """
    seen: dict[tuple[str, frozenset[str]], SearchResult] = {}
    ordered: list[SearchResult] = []
    collapsed = 0

    for result in results:
        key = (
            "".join(c for c in result.title.lower() if c.isalnum()),
            frozenset(i.canonical for i in result.ingredients if not i.is_staple),
        )
        if key in seen:
            collapsed += 1
            continue
        seen[key] = result
        ordered.append(result)

    return ordered, collapsed


def _build_result(
    conn: sqlite3.Connection, row: sqlite3.Row, pantry_ids: set[int]
) -> SearchResult:
    """Attach the ingredient breakdown that tells a cook what they lack."""
    ingredients = [
        MatchedIngredient(
            canonical=r["canonical"],
            display=r["display"] or r["canonical"],
            have=int(r["ingredient_id"]) in pantry_ids or bool(r["is_staple"]),
            is_staple=bool(r["is_staple"]),
            raw=r["raw"] or "",
        )
        for r in conn.execute(
            """SELECT ri.ingredient_id, ri.display, ri.is_staple, ri.raw, i.canonical
               FROM recipe_ingredients ri
               JOIN ingredients i ON i.id = ri.ingredient_id
               WHERE ri.recipe_id = ? ORDER BY ri.position""",
            (row["id"],),
        )
    ]

    meals: list[str] = []
    diets: list[str] = []
    allergens: list[str] = []
    cuisine = ""
    for tag in conn.execute(
        "SELECT kind, value FROM recipe_tags WHERE recipe_id = ?", (row["id"],)
    ):
        kind, value = tag["kind"], tag["value"]
        if kind == "meal":
            meals.append(value)
        elif kind == "cuisine":
            cuisine = value
        elif kind == "diet":
            diets.append(value)
        elif kind == "allergen":
            allergens.append(value)

    return SearchResult(
        recipe_id=int(row["id"]),
        title=row["title"],
        book_title=row["book_title"],
        book_path=row["book_path"],
        section=row["section"],
        servings=row["servings"],
        page=int(row["page"]),
        total_minutes=row["total_minutes"],
        active_minutes=row["active_minutes"],
        time_source=row["time_source"],
        has_long_wait=bool(row["has_long_wait"]),
        confidence=float(row["confidence"]),
        meals=meals,
        cuisine=cuisine,
        diets=sorted(diets),
        allergens=sorted(allergens),
        diet_caveats=row["diet_caveats"] if "diet_caveats" in row.keys() else "",
        n_unknown=int(row["n_unknown"]) if "n_unknown" in row.keys() else 0,
        image_ref=row["image_ref"] if "image_ref" in row.keys() else "",
        n_core=int(row["n_core"]),
        n_matched=int(row["n_matched"]),
        score=float(row["score"]),
        instructions=row["instructions"],
        ingredients=ingredients,
    )


def _fts_query(text: str, column: str = "") -> str:
    """Build a safe FTS5 query: every word required, prefix-matched.

    Punctuation is stripped rather than escaped, so a stray quote in user input
    cannot break out of the phrase and produce a syntax error.
    """
    words = [w for w in "".join(
        c if c.isalnum() or c.isspace() else " " for c in text
    ).split() if w]
    if not words:
        return '""'
    prefix = f"{column}:" if column else ""
    return " AND ".join(f'{prefix}"{w}"*' for w in words)


def suggest_ingredients(
    conn: sqlite3.Connection, prefix: str, limit: int = 12
) -> list[dict]:
    """Autocomplete over ingredients actually present in the library."""
    prefix = canonicalize(prefix) or prefix.strip().lower()
    if not prefix:
        return []
    rows = conn.execute(
        """SELECT canonical, n_recipes FROM ingredients
           WHERE canonical LIKE ? AND n_recipes > 0
           ORDER BY (canonical = ?) DESC, n_recipes DESC, LENGTH(canonical) ASC
           LIMIT ?""",
        (f"%{prefix}%", prefix, limit),
    )
    return [
        {"name": r["canonical"], "recipes": int(r["n_recipes"]),
         "staple": r["canonical"] in STAPLES}
        for r in rows
    ]


def get_recipe(conn: sqlite3.Connection, recipe_id: int,
               have: list[str] | None = None) -> SearchResult | None:
    """Fetch one recipe in full.

    `have` is optional but matters: without it every ingredient shows as
    missing, which is misleading in a detail view opened from a search.
    """
    pantry_ids: dict[int, str] = {}
    if have:
        pantry_ids, _unknown = _expand_ids(conn, normalise_pantry(have), True)

    row = conn.execute(
        """SELECT r.*, b.title AS book_title, b.path AS book_path, 0 AS n_matched,
                  0.0 AS score
           FROM recipes r JOIN books b ON b.id = r.book_id WHERE r.id = ?""",
        (recipe_id,),
    ).fetchone()
    return _build_result(conn, row, set(pantry_ids)) if row else None

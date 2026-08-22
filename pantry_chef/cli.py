"""Command line for building and querying the recipe database."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db
from .config import db_path
from .index import ingest
from .models import display_title
from .search import Query, get_recipe, search, suggest_ingredients

# --- output helpers --------------------------------------------------------

BOLD, DIM, GREEN, YELLOW, RED, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"
)


def _colour(enabled: bool):
    if enabled:
        return BOLD, DIM, GREEN, YELLOW, RED, RESET
    return "", "", "", "", "", ""


def _minutes(value: int | None) -> str:
    if value is None:
        return "  ? "
    if value < 60:
        return f"{value:3d}m"
    hours, rest = divmod(value, 60)
    return f"{hours}h{rest:02d}" if rest else f"{hours}h  "


def _split_list(text: str | None) -> list[str]:
    if not text:
        return []
    return [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]


# --- commands --------------------------------------------------------------

def cmd_index(args: argparse.Namespace) -> int:
    """Build or update the database from a library of ebooks."""
    roots = [Path(p) for p in args.paths]
    missing = [p for p in roots if not p.exists()]
    if missing:
        print(f"no such path: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 2

    target = db_path(args.db)
    print(f"indexing into {target}")
    report = ingest(
        roots,
        target,
        workers=args.workers,
        force=args.force,
        limit=args.limit,
        min_confidence=args.min_confidence,
        progress=(print if not args.quiet else None),
    )

    print(
        f"\nscanned {report.scanned} · indexed {report.indexed} · "
        f"skipped {report.skipped} · failed {report.failed} · "
        f"{report.recipes} recipes in {report.seconds:.1f}s"
    )
    if report.errors:
        print("\nfailures:")
        for name, error in report.errors[:20]:
            print(f"  {name}: {error}")
        if len(report.errors) > 20:
            print(f"  ... and {len(report.errors) - 20} more")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Find recipes matching a pantry and a time budget."""
    target = db_path(args.db)
    if not Path(target).exists():
        print(f"no database at {target} -- run `pantry index` first", file=sys.stderr)
        return 2

    conn = db.connect(target, read_only=True)
    query = Query(
        have=_split_list(args.have),
        max_minutes=args.time,
        max_missing=args.max_missing,
        without=_split_list(args.without),
        text=args.text or "",
        title=args.title or "",
        author=args.author or "",
        book=args.book or "",
        meals=_split_list(args.meal),
        cuisines=_split_list(args.cuisine),
        diets=_split_list(args.diet),
        free_from=_split_list(args.free_from),
        strict_diet=args.strict,
        require_timed=args.timed_only,
        allow_long_wait=not args.no_wait,
        sort=args.sort,
        limit=args.limit,
    )
    results, info = search(conn, query)

    bold, dim, green, yellow, red, reset = _colour(sys.stdout.isatty() and not args.no_colour)

    if info["unknown"]:
        print(f"{yellow}not in this library: {', '.join(info['unknown'])}{reset}\n")
    if not results:
        print("nothing matched. try --max-missing 3, a longer --time, or fewer items.")
        return 1

    print(f"{dim}{len(results)} result(s)"
          + (f", {info['duplicates_collapsed']} duplicate(s) collapsed"
             if info.get("duplicates_collapsed") else "")
          + f"{reset}\n")

    for result in results:
        missing = result.missing
        flag = f"{green}ready{reset}" if not missing else f"{yellow}-{len(missing)}{reset}"
        print(f"{bold}{display_title(result.title)}{reset}  {dim}[{result.recipe_id}]{reset}")
        tags = "  ·  ".join(x for x in (
            "/".join(result.meals), result.cuisine,
            ", ".join(result.diets)) if x)
        print(f"  {_minutes(result.total_minutes)}  {flag}  "
              f"{result.n_matched}/{result.n_core} on hand"
              + (f"  ·  serves {result.servings}" if result.servings else "")
              + (f"  ·  {dim}{tags}{reset}" if tags else ""))
        if missing:
            print(f"  {red}need:{reset} {', '.join(m.display for m in missing)}")
        source = result.book_title or Path(result.book_path).name
        location = f", p.{result.page}" if result.page else ""
        print(f"  {dim}{source}{location}{reset}")
        if result.allergens:
            print(f"  {dim}contains: {', '.join(result.allergens)}{reset}")
        if result.n_unknown:
            print(f"  {yellow}{result.n_unknown} ingredient(s) not recognised — "
                  f"allergen check is incomplete{reset}")
        if result.diet_caveats:
            print(f"  {yellow}note: {result.diet_caveats}{reset}")
        if result.has_long_wait:
            print(f"  {dim}(includes an overnight or long rest){reset}")
        print()

    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Print one recipe in full."""
    conn = db.connect(db_path(args.db), read_only=True)
    recipe = get_recipe(conn, args.recipe_id)
    if not recipe:
        print(f"no recipe with id {args.recipe_id}", file=sys.stderr)
        return 2

    bold, dim, *_rest, reset = _colour(sys.stdout.isatty() and not args.no_colour)
    print(f"\n{bold}{display_title(recipe.title)}{reset}")
    meta = [x for x in (
        f"{recipe.total_minutes} min" if recipe.total_minutes else "",
        f"serves {recipe.servings}" if recipe.servings else "",
        recipe.section,
    ) if x]
    if meta:
        print(f"{dim}{'  ·  '.join(meta)}{reset}")
    print(f"{dim}from {recipe.book_title or recipe.book_path}"
          + (f", p.{recipe.page}" if recipe.page else "") + f"{reset}\n")

    print(f"{bold}Ingredients{reset}")
    for ingredient in recipe.ingredients:
        print(f"  · {ingredient.display}")
    if recipe.instructions:
        print(f"\n{bold}Method{reset}")
        for line in recipe.instructions.split("\n"):
            print(f"  {line}")
    print()
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """Show what is in the database."""
    target = db_path(args.db)
    if not Path(target).exists():
        print(f"no database at {target}", file=sys.stderr)
        return 2

    conn = db.connect(target, read_only=True)
    data = db.stats(conn)
    size = Path(target).stat().st_size

    print(f"database    {target}  ({size / 1e6:.1f} MB)")
    print(f"books       {data['books']} indexed, {data['books_failed']} failed")
    print(f"recipes     {data['recipes']}")
    print(f"ingredients {data['ingredients']} distinct, {data['links']} links")
    print(f"quality     {data['avg_confidence']} mean confidence, "
          f"{data['timed']} with a stated time, {data['with_photo']} with a photo")

    meals = db.tag_counts(conn, "meal")
    if meals:
        print("\nmeals       " + ", ".join(f"{v} ({n})" for v, n in meals))
    cuisines = db.tag_counts(conn, "cuisine")
    if cuisines:
        print("cuisines    " + ", ".join(f"{v} ({n})" for v, n in cuisines[:10]))
    diets = db.tag_counts(conn, "diet")
    if diets:
        print("diets       " + ", ".join(f"{v} ({n})" for v, n in diets))
    allergens = db.tag_counts(conn, "allergen")
    if allergens:
        print("allergens   " + ", ".join(f"{v} ({n})" for v, n in allergens))
    if data["recipes"] and not data["tagged"]:
        print("\nnote: this database predates meal/cuisine tagging — "
              "re-run `pantry index --force` to add them")

    top = conn.execute(
        "SELECT canonical, n_recipes FROM ingredients ORDER BY n_recipes DESC LIMIT 12"
    ).fetchall()
    if top:
        print("\nmost common ingredients")
        for row in top:
            print(f"  {row['n_recipes']:6d}  {row['canonical']}")
    return 0


def cmd_books(args: argparse.Namespace) -> int:
    """List the indexed books."""
    conn = db.connect(db_path(args.db), read_only=True)
    rows = conn.execute(
        "SELECT title, author, format, n_recipes, status, error, path"
        " FROM books ORDER BY n_recipes DESC"
    ).fetchall()
    if not rows:
        print("no books indexed yet")
        return 1

    for row in rows:
        mark = "ok " if row["status"] == "indexed" else "ERR"
        print(f"{mark} {row['n_recipes']:5d}  {(row['title'] or '?')[:52]:54} "
              f"{row['format']:6} {Path(row['path']).name[:40]}")
        if row["error"]:
            print(f"          {row['error']}")
    return 0


def cmd_ingredients(args: argparse.Namespace) -> int:
    """Look up which ingredient names the library knows."""
    conn = db.connect(db_path(args.db), read_only=True)
    for item in suggest_ingredients(conn, args.prefix, limit=args.limit):
        staple = " (staple)" if item["staple"] else ""
        print(f"{item['recipes']:6d}  {item['name']}{staple}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the local web app."""
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed; run: pip install 'uvicorn[standard]' fastapi",
              file=sys.stderr)
        return 2

    import os
    os.environ.setdefault("PANTRY_CHEF_DB", str(db_path(args.db)))
    print(f"serving {db_path(args.db)} at http://{args.host}:{args.port}")
    uvicorn.run("pantry_chef.web.app:app", host=args.host, port=args.port,
                reload=args.reload, log_level="warning")
    return 0


# --- wiring ----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pantry-chef",
        description="Pantry Chef — search your cookbook library by what is in your kitchen.",
    )
    parser.add_argument("--db", help="database file (default: $PANTRY_CHEF_DB or data/pantry-chef.db)")
    parser.add_argument("--no-colour", action="store_true", help="disable coloured output")

    # The same flags again on every subcommand, so `pantry search --db x` works
    # as readily as `pantry --db x search`. SUPPRESS keeps an unused subcommand
    # flag from overwriting the value given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--no-colour", action="store_true",
                        default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="build the database from ebook files", parents=[common])
    p_index.add_argument("paths", nargs="+", help="files or folders to scan")
    p_index.add_argument("--workers", type=int, default=None, help="parallel parsers")
    p_index.add_argument("--force", action="store_true", help="re-index unchanged books")
    p_index.add_argument("--limit", type=int, default=None, help="stop after N books")
    p_index.add_argument("--min-confidence", type=float, default=0.4,
                         help="discard recipes below this confidence (default 0.4)")
    p_index.add_argument("--quiet", action="store_true", help="suppress per-book output")
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="find recipes you can cook", parents=[common])
    p_search.add_argument("--have", help="comma-separated ingredients you have")
    p_search.add_argument("--time", type=int, help="minutes available")
    p_search.add_argument("--max-missing", type=int, default=2,
                          help="ingredients you are willing to shop for (default 2)")
    p_search.add_argument("--without", help="comma-separated ingredients to avoid")
    p_search.add_argument("--text", help="also match this text anywhere")
    p_search.add_argument("--title", help="words in the recipe title")
    p_search.add_argument("--author", help="the cookbook's author")
    p_search.add_argument("--book", help="the cookbook's title")
    p_search.add_argument("--meal",
                          help="breakfast, lunch, dinner, dessert, snack, side "
                               "(comma-separated for any of them)")
    p_search.add_argument("--cuisine",
                          help="italian, thai, indian, mexican… (comma-separated)")
    p_search.add_argument("--diet",
                          help="vegetarian, vegan, pescatarian, 'no red meat' "
                               "(comma-separated; all must hold)")
    p_search.add_argument("--free-from",
                          help="allergens to exclude: gluten, milk, egg, nuts, "
                               "peanuts, fish, crustaceans, molluscs, soy, "
                               "sesame, celery, mustard, sulphites")
    p_search.add_argument("--strict", action="store_true",
                          help="with --diet/--free-from, drop recipes containing "
                               "any ingredient we could not identify")
    p_search.add_argument("--sort", choices=["best", "quickest", "coverage"], default="best")
    p_search.add_argument("--timed-only", action="store_true",
                          help="only recipes whose time is stated, not estimated")
    p_search.add_argument("--no-wait", action="store_true",
                          help="exclude recipes needing an overnight rest")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.set_defaults(func=cmd_search)

    p_show = sub.add_parser("show", help="print one recipe", parents=[common])
    p_show.add_argument("recipe_id", type=int)
    p_show.set_defaults(func=cmd_show)

    p_stats = sub.add_parser("stats", help="summarise the database", parents=[common])
    p_stats.set_defaults(func=cmd_stats)

    p_books = sub.add_parser("books", help="list indexed books", parents=[common])
    p_books.set_defaults(func=cmd_books)

    p_ing = sub.add_parser("ingredients", help="search known ingredient names", parents=[common])
    p_ing.add_argument("prefix")
    p_ing.add_argument("--limit", type=int, default=25)
    p_ing.set_defaults(func=cmd_ingredients)

    p_serve = sub.add_parser("serve", help="run the local web app", parents=[common])
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8077)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

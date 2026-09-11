"""The shopping list: the recipes you plan to cook, their ingredients added up.

Each recipe goes on the list at a number of servings; the list is recomputed
from them every time it is shown, so correcting a recipe or changing its
servings changes the list with it. Amounts are read from the same metric,
scaled lines the recipe page shows, and added up where they can honestly be
added: grams with grams, millilitres with millilitres, eggs with eggs. Where
they cannot — "4 cloves" of garlic and "1 tbsp" of minced garlic — both are
shown rather than a made-up total.

Store-cupboard staples (salt, oil, flour…) are listed apart: you probably
have them, but a recipe that needs a whole block of butter is worth a look.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import db
from .models import display_title
from .parse.diet import CRUSTACEANS, DAIRY, EGG, FISH, MEAT, MOLLUSCS, NUTS
from .parse.metric import G_PER_UNIT, ML_PER_UNIT, _format, _round_g, _round_ml, to_metric_line
from .parse.quantities import parse_quantity
from .parse.scale import _agree, nice_number, scale_line

MAX_SCALE = 20.0
MAX_EXTRA_LENGTH = 200
MAX_EXTRAS = 200

# In the order a walk round a typical supermarket meets them.
AISLES = (
    "Fruit & veg", "Bakery", "Meat", "Fish & seafood", "Dairy, eggs & chilled",
    "Dry goods & baking", "Tins, jars & sauces", "Oils & vinegars",
    "Herbs & spices", "Frozen", "Drinks", "Other",
)

_PRODUCE = frozenset({
    "garlic", "garlic bulb", "onion", "red onion", "shallot", "scallion", "leek",
    "green onion top", "green onion white green part", "spring onion",
    "lemon", "lime", "orange", "grapefruit", "bell pepper", "carrot", "tomato",
    "cherry tomato", "potato", "sweet potato", "avocado", "ginger", "parsley",
    "parsley leaf", "cilantro", "cilantro leaf", "coriander", "basil", "mint",
    "mint leaf", "dill", "chive", "rosemary", "thyme", "sage", "tarragon",
    "spinach", "kale", "lettuce", "arugula", "rocket", "cucumber", "cabbage",
    "celery", "celery rib", "mushroom", "zucchini", "courgette", "squash",
    "eggplant", "aubergine", "broccoli", "broccolini", "cauliflower",
    "asparagus", "green bean", "corn", "radish", "beet", "beetroot", "parsnip",
    "artichoke", "brussels sprout", "jalapeño", "jalape o", "green chile",
    "chili", "chilli", "fennel", "bok choy", "pumpkin", "banana", "apple",
    "pear", "peach", "nectarine", "plum", "mango", "pineapple", "strawberry",
    "raspberry", "blueberry", "blackberry", "cherry", "grape", "kiwifruit",
    "apricot", "cranberry", "fig", "melon", "watermelon", "pomegranate",
    "sprout", "bean sprout", "turnip", "swede", "okra", "tomatillo",
    "snow pea", "snap pea", "coleslaw mix", "bag coleslaw mix",
})
_PRODUCE_HEADS = ("onion", "tomato", "lettuce", "squash", "berry", "potato",
                  "mushroom", "cabbage", "chile", "greens", "apple", "juice")

_SPICES = frozenset({
    "salt", "black pepper", "pepper", "cinnamon", "paprika", "cumin", "oregano",
    "nutmeg", "turmeric", "chili powder", "curry powder", "allspice", "cardamom",
    "bay leaf", "sumac", "za atar", "zaatar", "seven spice", "italian seasoning",
    "taco seasoning", "garlic powder", "onion powder", "chili flake", "clove",
    "fennel seed", "cumin seed", "mustard seed", "star anise", "saffron",
    "cayenne", "garam masala", "ranch seasoning powder", "chicken bouillon powder",
    "herbes de provence", "five spice", "smoked paprika", "black peppercorn",
    "peppercorn", "caraway", "msg", "lavender", "culinary-grade lavender",
})
_SPICE_WORDS = ("ground ", "dried ", "powder", "flake", "seasoning", "spice")

_BAKERY = frozenset({
    "bread", "tortilla", "bun", "pita", "bagel", "naan", "roll", "brioche",
    "croissant", "baguette", "flatbread", "english muffin", "wrap", "sourdough",
    "focaccia", "ciabatta",
})

_TINS = frozenset({
    "coconut milk", "tomato puree", "tomato paste", "chickpea", "black bean",
    "kidney bean", "cannellini bean", "butter bean", "bean", "pumpkin puree",
    "marinara sauce", "salsa", "soy sauce", "coconut amino", "sriracha",
    "ketchup", "worcestershire sauce", "fish sauce", "mustard", "dijon mustard",
    "mayonnaise", "honey", "maple syrup", "molasses", "syrup", "peanut butter",
    "almond butter", "tahini", "miso", "kimchi", "pickle", "olive", "caper",
    "applesauce", "jam", "hoisin", "gochujang", "hot sauce", "salsa verde",
    "tuna", "anchovy", "sardine", "canned tomato",
})
_TIN_WORDS = ("broth", "stock", "sauce", "paste", "puree", "syrup", "bouillon", "pesto",
              "dressing", "salsa", "tamari", "spread", "fluff")

_DRY_WORDS = ("flour", "sugar", "rice", "pasta", "spaghetti", "noodle", "oat",
              "cocoa", "cacao", "chocolate", "extract", "seed", "flaxseed",
              "quinoa", "lentil", "cornstarch", "cornmeal", "polenta", "starch",
              "yeast", "baking powder", "baking soda", "protein powder",
              "collagen", "panko", "breadcrumb", "cracker", "raisin", "date",
              "coconut flake", "couscous", "bulgur", "gelatin", "arrowroot",
              "vanilla", "sprinkle", "granola", "cereal", "chex", "espresso", "currant",
              "penne", "macaroni", "orzo", "lasagna", "barley", "split pea", "prune",
              "linguine", "fettuccine", "tagliatelle", "rigatoni", "fusilli", "farfalle",
              "vermicelli", "ramen", "udon", "farro",
              "pretzel", "marshmallow", "caramel", "toffee", "candy", "cookie", "popcorn",
              "pudding mix", "cake mix", "hemp heart")

_DRINKS = frozenset({"wine", "red wine", "white wine", "beer", "coffee", "tea",
                     "espresso", "sparkling water", "cider", "rum", "vodka",
                     "brandy", "sherry", "port", "vermouth", "sake"})


# Words that place a multi-word ingredient: "boneless beef short rib" is meat
# even though that exact phrase is in no list.
_MEAT_WORDS = frozenset({"beef", "pork", "chicken", "lamb", "turkey", "bacon", "sausage",
                         "steak", "ham", "veal", "duck", "mince", "chorizo",
                         "prosciutto", "pancetta", "brisket", "butt", "loin"})
# "Powder" usually means a spice — but not in powdered sugar or cocoa powder.
_NOT_SPICE = ("protein", "baking", "cocoa", "cacao", "sugar", "arrowroot", "espresso",
              "coffee", "coconut", "collagen", "flour", "starch")
_CHILLED = frozenset({"tofu", "tempeh", "hummus", "fresh pasta", "gnocchi", "pizza dough",
                      "refrigerated pizza dough", "phyllo dough", "puff pastry"})
_FROZEN = frozenset({"pea", "edamame", "potato tot", "ice cream", "ice"})
_BAKING = ("vanilla", "extract", "shortening", "peanut", "coconut")
_FISH_WORDS = frozenset({"fish", "salmon", "tuna", "cod", "shrimp", "prawn", "crab",
                         "scallop", "mussel", "clam", "trout", "halibut", "haddock"})
_DAIRY_WORDS = frozenset({"cheese", "milk", "cream", "yogurt", "yoghurt", "egg", "eggs",
                          "queso", "half-and-half"})
_PRODUCE_WORDS = frozenset(w for w in _PRODUCE if " " not in w)
_NOT_FRESH = ("stock", "broth", "bouillon", "gelatin", "lard", "fat", "powder", "sauce")


def aisle_for(canonical: str, name: str = "") -> str:
    """Where in a shop this ingredient is likely to be. A best guess."""
    text = f"{canonical} {name}".lower()
    words = set(canonical.split())
    head = canonical.split()[-1] if canonical else ""
    processed = any(w in canonical for w in _NOT_FRESH)
    if "frozen" in text or canonical in _FROZEN:
        return "Frozen"
    if canonical in _TINS or canonical == "mirin":
        return "Tins, jars & sauces"
    if (canonical in FISH | CRUSTACEANS | MOLLUSCS or words & _FISH_WORDS) and not processed:
        return "Fish & seafood"
    if (canonical in MEAT or words & _MEAT_WORDS or "short rib" in canonical) and not processed:
        return "Meat"
    if canonical in _CHILLED or ((canonical in DAIRY | EGG or words & _DAIRY_WORDS)
                                 and "chocolate" not in canonical):
        return "Dairy, eggs & chilled"
    if canonical in _SPICES or (any(w in text for w in _SPICE_WORDS)
                                and not any(w in text for w in _NOT_SPICE)):
        return "Herbs & spices"
    if any(w in canonical for w in ("oil", "vinegar", "spray")):
        return "Oils & vinegars"
    if any(w in canonical for w in _BAKING):
        return "Dry goods & baking"
    dry = any(w in canonical for w in _DRY_WORDS)
    if (canonical in _PRODUCE or head in _PRODUCE_HEADS
            or (words & _PRODUCE_WORDS and not dry)):        # "broccoli floret"
        return "Fruit & veg"
    if canonical in _BAKERY or head in _BAKERY:
        return "Bakery"
    if any(w in canonical for w in _TIN_WORDS):
        return "Tins, jars & sauces"
    if canonical in NUTS or head == "nut" or any(w in canonical for w in _DRY_WORDS):
        return "Dry goods & baking"
    if canonical in _DRINKS:
        return "Drinks"
    return "Other"


# --- adding up ------------------------------------------------------------------------

_COUNT_WORDS = {"clove", "can", "slice", "sprig", "bunch", "head", "stalk", "sheet",
                "fillet", "rasher", "package", "jar", "piece", "handful", "pinch", "dash"}


@dataclass
class _Item:
    key: str
    names: list[str] = field(default_factory=list)
    staple: bool = False
    grams: float = 0.0
    ml: float = 0.0
    tsp: float = 0.0
    counts: dict[str, float] = field(default_factory=dict)
    recipes: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    def add(self, line: str) -> None:
        """Add the amount at the head of one (metric, scaled) line."""
        quantity, unit, _rest = parse_quantity(line)
        if quantity is None:
            return
        if unit in ("g", "kg"):
            self.grams += quantity * (1000 if unit == "kg" else 1)
        elif unit in ("ml", "l"):
            self.ml += quantity * (1000 if unit == "l" else 1)
        elif unit == "tsp":
            self.tsp += quantity
        elif unit == "tbsp":
            self.tsp += quantity * 3
        elif unit in ML_PER_UNIT:
            self.ml += quantity * ML_PER_UNIT[unit]
        elif unit in G_PER_UNIT:
            self.grams += quantity * G_PER_UNIT[unit]
        elif unit in _COUNT_WORDS:
            self.counts[unit] = self.counts.get(unit, 0.0) + quantity
        elif unit is None:
            # "3 garlic cloves" puts the count before the word; it is still cloves.
            bare = "clove" if self.key == "garlic" else ""
            self.counts[bare] = self.counts.get(bare, 0.0) + quantity
        # Anything else (an inch of ginger) is left to the lines to explain.

    def amount(self) -> str:
        parts = []
        if self.grams:
            parts.append(_format(max(1, _round_g(self.grams)), "g"))
        if self.ml:
            parts.append(_format(max(1, _round_ml(self.ml)), "ml"))
        if self.tsp:
            parts.append(f"{nice_number(self.tsp / 3)} tbsp" if self.tsp >= 3
                         else f"{nice_number(self.tsp)} tsp")
        for unit, total in self.counts.items():
            parts.append(nice_number(total) + (f" {_agree(unit, total)}" if unit else ""))
        return " + ".join(parts)

    def name(self) -> str:
        """What to call it on a list: "parsley", not "chopped fresh parsley".

        The ingredient's own name, which is what the recipes were grouped by —
        unless it is one the lexicon mangled ("jalape o"), when the plainest
        wording a recipe used is better.
        """
        words = self.key.split()
        if 1 <= len(words) <= 3 and all(len(w) > 1 for w in words):
            return self.key
        names = [n for n in self.names if n] or [self.key]
        return min(names, key=lambda n: (len(n), n))

    def as_dict(self, ticked: set[str]) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name(),
            "amount": self.amount(),
            "recipes": self.recipes,
            "lines": self.lines,
            "ticked": self.key in ticked,
        }


# --- the list ---------------------------------------------------------------------------

def entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The recipes on the list that still resolve, with their servings."""
    if not db.has_table(conn, "shopping_recipes"):
        return []
    keyed = db.keyed_ids_sql(conn, "shopping_recipes",
                             "ranked.id AS id, k.scale AS scale, k.added_at AS added_at")
    rows = conn.execute(
        f"""SELECT r.id AS id, r.title AS title, r.servings AS servings,
                   b.title AS book, k.scale AS scale
            FROM ({keyed}) k
            JOIN recipes r ON r.id = k.id JOIN books b ON b.id = r.book_id
            ORDER BY k.added_at, r.id"""
    ).fetchall()
    # Titles as the recipe page shows them: "Pork Butt Battle", not the book's capitals.
    return [{**dict(row), "title": display_title(row["title"])} for row in rows]


def _collect(conn: sqlite3.Connection, on_list: list[dict[str, Any]]) -> dict[str, _Item]:
    items: dict[str, _Item] = {}
    for entry in on_list:
        scale = float(entry["scale"])
        for row in conn.execute(
            """SELECT ri.raw, ri.display, ri.quantity, ri.is_staple, i.canonical
               FROM recipe_ingredients ri JOIN ingredients i ON i.id = ri.ingredient_id
               WHERE ri.recipe_id = ? ORDER BY ri.position""",
            (entry["id"],),
        ):
            item = items.setdefault(row["canonical"], _Item(row["canonical"]))
            item.names.append((row["display"] or "").strip())
            item.staple = item.staple or bool(row["is_staple"])
            if entry["title"] not in item.recipes:
                item.recipes.append(entry["title"])
            raw = (row["raw"] or "").strip()
            # A line naming two ingredients carries its amount on the first;
            # the second is on the list without one.
            if not raw or row["quantity"] is None:
                continue
            line = to_metric_line(raw)
            if scale != 1:
                line, _changed = scale_line(line, scale)
            item.add(line)
            item.lines.append(line)
    return items


def build(conn: sqlite3.Connection) -> dict[str, Any]:
    """Everything the shopping page shows."""
    on_list = entries(conn)
    ticked = _ticked(conn)
    items = _collect(conn, on_list)

    aisles: dict[str, list[dict[str, Any]]] = {}
    cupboard = []
    for item in sorted(items.values(), key=lambda i: i.name().lower()):
        shown = item.as_dict(ticked)
        if item.staple:
            cupboard.append(shown)
        else:
            aisles.setdefault(aisle_for(item.key, shown["name"]), []).append(shown)

    extras: list[dict[str, Any]] = []
    stored = 0
    if db.has_table(conn, "shopping_extras"):
        extras = [{"id": row["id"], "text": row["text"], "done": bool(row["done"])}
                  for row in conn.execute("SELECT id, text, done FROM shopping_extras ORDER BY id")]
        stored = int(conn.execute("SELECT COUNT(*) FROM shopping_recipes").fetchone()[0])

    to_buy = [i for group in aisles.values() for i in group]
    return {
        "recipes": on_list,
        "aisles": [{"name": name, "items": aisles[name]} for name in AISLES if name in aisles],
        "cupboard": cupboard,
        "extras": extras,
        "missing": max(0, stored - len(on_list)),
        "counts": {
            "items": len(to_buy) + len(extras),
            "ticked": sum(i["ticked"] for i in to_buy) + sum(e["done"] for e in extras),
        },
    }


def summary(conn: sqlite3.Connection) -> dict[str, int]:
    """For the badge on the navigation: how many recipes are on the list."""
    return {"recipes": len(entries(conn))}


def scale_on_list(conn: sqlite3.Connection, recipe_id: int) -> float | None:
    """The servings a recipe is on the list at, or None if it is not on it."""
    if not db.has_table(conn, "shopping_recipes"):
        return None
    key = db.recipe_key(conn, recipe_id)
    if key is None:
        return None
    row = conn.execute(
        """SELECT scale FROM shopping_recipes
           WHERE book_path = ? AND title_key = ? AND occurrence = ?""",
        key[:3],
    ).fetchone()
    return float(row[0]) if row else None


# --- changing it ------------------------------------------------------------------------

def add(conn: sqlite3.Connection, recipe_id: int, scale: float = 1.0) -> bool:
    """Put a recipe on the list, or change its servings. False if no such recipe."""
    if not 0 < scale <= MAX_SCALE:
        raise ValueError(f"scale must be above 0 and at most {MAX_SCALE:g}")
    key = db.recipe_key(conn, recipe_id)
    if key is None:
        return False
    path, title_key, occurrence, title = key
    conn.execute(
        """INSERT INTO shopping_recipes (book_path, title_key, occurrence, title, scale)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(book_path, title_key, occurrence) DO UPDATE SET scale = excluded.scale""",
        (path, title_key, occurrence, title, scale),
    )
    conn.commit()
    return True


def remove(conn: sqlite3.Connection, recipe_id: int) -> bool:
    """Take a recipe off the list. False if no such recipe."""
    key = db.recipe_key(conn, recipe_id)
    if key is None:
        return False
    conn.execute(
        "DELETE FROM shopping_recipes WHERE book_path = ? AND title_key = ? AND occurrence = ?",
        key[:3],
    )
    # A tick for something no recipe on the list still needs would otherwise
    # tick it off in advance the next time it is needed.
    wanted = set(_collect(conn, entries(conn)))
    for stale in _ticked(conn) - wanted:
        conn.execute("DELETE FROM shopping_ticks WHERE item_key = ?", (stale,))
    conn.commit()
    return True


def tick(conn: sqlite3.Connection, item_key: str, ticked: bool) -> None:
    if ticked:
        conn.execute("INSERT OR IGNORE INTO shopping_ticks (item_key) VALUES (?)", (item_key,))
    else:
        conn.execute("DELETE FROM shopping_ticks WHERE item_key = ?", (item_key,))
    conn.commit()


def add_extra(conn: sqlite3.Connection, text: str) -> int:
    """Add something no recipe asked for. Raises ValueError with a reason."""
    text = " ".join(text.split())
    if not text:
        raise ValueError("there is nothing to add")
    if len(text) > MAX_EXTRA_LENGTH:
        raise ValueError(f"keep it under {MAX_EXTRA_LENGTH} characters")
    if conn.execute("SELECT COUNT(*) FROM shopping_extras").fetchone()[0] >= MAX_EXTRAS:
        raise ValueError(f"the list already has {MAX_EXTRAS} extra items")
    cursor = conn.execute("INSERT INTO shopping_extras (text) VALUES (?)", (text,))
    conn.commit()
    return int(cursor.lastrowid or 0)


def set_extra_done(conn: sqlite3.Connection, extra_id: int, done: bool) -> bool:
    cursor = conn.execute("UPDATE shopping_extras SET done = ? WHERE id = ?",
                          (int(done), extra_id))
    conn.commit()
    return cursor.rowcount > 0


def remove_extra(conn: sqlite3.Connection, extra_id: int) -> bool:
    cursor = conn.execute("DELETE FROM shopping_extras WHERE id = ?", (extra_id,))
    conn.commit()
    return cursor.rowcount > 0


def clear(conn: sqlite3.Connection) -> None:
    """Start a new list: recipes, ticks and extras all go."""
    for table in ("shopping_recipes", "shopping_ticks", "shopping_extras"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def _ticked(conn: sqlite3.Connection) -> set[str]:
    if not db.has_table(conn, "shopping_ticks"):
        return set()
    return {row[0] for row in conn.execute("SELECT item_key FROM shopping_ticks")}

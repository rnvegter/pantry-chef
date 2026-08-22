"""Work out what meal a recipe is for, and what kitchen it comes from.

Both answers are inferred from three pieces of evidence, in descending order of
reliability:

  1. the chapter heading the recipe sits under  -- "Puddings", "Breakfast"
  2. words in the recipe's own title           -- "tagine", "pancakes"
  3. the ingredient signature                  -- miso + mirin, harissa + preserved lemon

Precision beats recall here. A recipe wrongly filed under "Italian" is worse
than one left unfiled, so cuisine is only assigned when the evidence clears a
threshold, and `None` is a perfectly good answer.

Meal type is deliberately multi-valued: most savoury mains are honestly both
lunch and dinner, and forcing a single label would make the filter useless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MEALS = ("breakfast", "lunch", "dinner", "dessert", "snack", "side")

CUISINES = (
    "italian", "french", "spanish", "greek", "british", "american",
    "mexican", "indian", "thai", "chinese", "japanese", "korean",
    "vietnamese", "middle eastern", "north african", "caribbean",
    "german", "eastern european", "nordic", "portuguese", "turkish",
)

# --- meal evidence ---------------------------------------------------------

# Chapter headings are the strongest signal a book gives us.
_SECTION_MEALS: dict[str, tuple[str, ...]] = {
    "breakfast": ("breakfast", "brunch", "morning", "eggs", "porridge"),
    "dessert": ("dessert", "desserts", "pudding", "puddings", "sweet", "sweets",
                "baking", "cakes", "biscuits", "cookies", "ice cream", "patisserie",
                "tarts", "pies and tarts", "chocolate"),
    "lunch": ("lunch", "lunches", "light", "light bites", "salads", "sandwiches",
              "soups", "starters", "small plates", "snacks", "packed lunch"),
    "dinner": ("dinner", "dinners", "supper", "suppers", "mains", "main courses",
               "weeknight", "roasts", "curries", "stews", "pasta", "one pot",
               "grills", "barbecue", "feasts"),
    "side": ("sides", "side dishes", "vegetables", "salads and sides",
             "accompaniments", "condiments", "sauces", "dressings", "basics"),
    "snack": ("snacks", "nibbles", "canapes", "bites", "party"),
}

# Words in the recipe's own title.
_TITLE_MEALS: dict[str, tuple[str, ...]] = {
    "breakfast": ("pancake", "waffle", "granola", "muesli", "porridge", "oatmeal",
                  "omelette", "omelet", "frittata", "shakshuka", "scrambled",
                  "poached egg", "fried egg", "boiled egg", "bacon sandwich",
                  "croissant", "french toast", "smoothie", "overnight oats",
                  "breakfast", "brunch", "bagel", "crumpet", "congee"),
    "dessert": ("cake", "brownie", "blondie", "cookie", "biscuit", "tart", "pie",
                "crumble", "cobbler", "trifle", "mousse", "parfait", "sorbet",
                "ice cream", "gelato", "custard", "panna cotta", "tiramisu",
                "cheesecake", "pudding", "flan", "meringue", "pavlova", "eclair",
                "doughnut", "donut", "macaron", "truffle", "fudge", "compote",
                "sundae", "cupcake", "scone", "shortbread", "baklava", "churro",
                "curd", "ganache", "frosting", "icing", "buttercream", "praline",
                "jam", "preserve", "marmalade", "syrup", "caramel", "toffee",
                "brittle", "marshmallow", "sorbet", "granita", "affogato"),
    "lunch": ("sandwich", "salad", "soup", "wrap", "toastie", "panini", "baguette",
              "quiche", "tartine", "bowl", "broth", "chowder", "bisque"),
    "dinner": ("roast", "stew", "casserole", "curry", "tagine", "braise", "braised",
               "ragu", "lasagne", "lasagna", "risotto", "paella", "pilaf", "biryani",
               "traybake", "skillet", "stir-fry", "stir fry", "grilled", "steak",
               "chop", "fillet", "shepherd's pie", "pot roast", "meatball"),
    "side": ("slaw", "pickle", "pickles", "dressing", "sauce", "chutney", "relish",
             "mash", "fries", "roast potatoes", "flatbread", "focaccia", "dip",
             "hummus", "condiment", "stock", "marinade", "salsa"),
    "snack": ("crisps", "chips", "popcorn", "nuts", "energy ball", "bar",
              "cracker", "crostini", "bruschetta", "canape"),
}

# Ingredients that push a recipe towards dessert, and those that pull it away.
_SWEET_MARKERS = frozenset({
    "sugar", "brown sugar", "powdered sugar", "chocolate", "dark chocolate",
    "white chocolate", "chocolate chip", "cocoa", "honey", "maple syrup",
    "syrup", "molasses", "vanilla extract", "condensed milk", "marshmallow",
    "caramel", "jam", "marmalade", "icing", "custard",
})
_SAVOURY_MARKERS = frozenset({
    "onion", "garlic", "stock", "chicken stock", "beef stock", "vegetable stock",
    "soy sauce", "fish sauce", "worcestershire sauce", "mustard", "dijon mustard",
    "anchovy", "bacon", "chorizo", "parmesan", "miso", "harissa", "curry powder",
    "cumin", "paprika", "chili", "chili powder", "black pepper",
})
_BAKING_MARKERS = frozenset({"flour", "baking powder", "baking soda", "yeast", "egg"})

# --- cuisine evidence ------------------------------------------------------

# Ingredient signatures. Weight reflects how strongly one item implies a kitchen:
# 3 = near-decisive, 2 = strong, 1 = suggestive.
_CUISINE_INGREDIENTS: dict[str, dict[str, int]] = {
    "italian": {"parmesan": 2, "mozzarella": 2, "mascarpone": 2, "ricotta": 2,
                "pancetta": 3, "prosciutto": 3, "arborio rice": 3, "basil": 1,
                "balsamic vinegar": 2, "spaghetti": 2, "linguine": 2, "penne": 2,
                "tagliatelle": 2, "gnocchi": 3, "polenta": 2, "olive oil": 0},
    "french": {"crème fraîche": 3, "gruyere": 3, "brie": 2, "dijon mustard": 2,
               "tarragon": 2, "shallot": 1, "white wine": 1, "heavy cream": 1,
               "vermouth": 2, "brandy": 1},
    "spanish": {"chorizo": 3, "paprika": 1, "saffron": 2, "sherry": 2,
                "sherry vinegar": 3, "manchego": 3},
    "greek": {"feta": 3, "oregano": 1, "olive": 1, "greek yogurt": 2, "dill": 1},
    "middle eastern": {"tahini": 3, "sumac": 3, "zaatar": 3, "pomegranate": 2,
                       "chickpea": 1, "hummus": 2, "harissa": 1, "cardamom": 1,
                       "bulgur": 2, "halloumi": 2},
    "north african": {"harissa": 3, "ras el hanout": 3, "preserved lemon": 3,
                      "couscous": 3, "cumin": 1, "coriander": 1, "date": 1},
    "indian": {"garam masala": 3, "turmeric": 2, "cumin": 1, "cardamom": 2,
               "curry powder": 3, "ghee": 3, "paneer": 3, "basmati rice": 2,
               "lentil": 1, "coriander": 1, "fenugreek": 3, "naan": 3},
    "thai": {"fish sauce": 2, "lemongrass": 3, "coconut milk": 2, "lime": 1,
             "galangal": 3, "thai basil": 3, "rice noodle": 1},
    "vietnamese": {"fish sauce": 2, "rice noodle": 2, "coriander": 1, "mint": 1,
                   "lime": 1, "star anise": 2},
    "chinese": {"soy sauce": 2, "rice vinegar": 2, "sesame oil": 2, "five spice": 3,
                "hoisin": 3, "oyster sauce": 3, "shaoxing": 3, "ginger": 1,
                "star anise": 1, "tofu": 1},
    "japanese": {"miso": 3, "mirin": 3, "dashi": 3, "sake": 2, "nori": 3,
                 "wasabi": 3, "panko": 2, "soba": 3, "udon": 3, "soy sauce": 1},
    "korean": {"gochujang": 3, "kimchi": 3, "sesame oil": 1, "soy sauce": 1},
    "mexican": {"tortilla": 3, "jalapeno": 2, "cilantro": 1, "lime": 1,
                "black bean": 2, "avocado": 1, "chipotle": 3, "cumin": 1},
    "british": {"suet": 3, "golden syrup": 3, "cheddar": 2, "worcestershire sauce": 2,
                "custard": 2, "treacle": 3},
    "american": {"buttermilk": 2, "cornmeal": 2, "maple syrup": 2, "bourbon": 3,
                 "chocolate chip": 2, "pecan": 2},
    "german": {"sauerkraut": 3, "caraway": 2, "rye": 2},
    "eastern european": {"sour cream": 1, "dill": 1, "beet": 2, "paprika": 1,
                         "caraway": 1, "cabbage": 1},
    "nordic": {"dill": 2, "rye": 2, "salmon": 1, "juniper": 3, "cardamom": 1},
    "caribbean": {"allspice": 3, "scotch bonnet": 3, "plantain": 3, "coconut milk": 1},
    "turkish": {"sumac": 2, "pomegranate": 2, "bulgur": 2, "yogurt": 1, "mint": 1},
    "portuguese": {"piri piri": 3, "salt cod": 3, "chorizo": 1},
}

# Dish names that name their own cuisine outright.
_CUISINE_TITLES: dict[str, tuple[str, ...]] = {
    "italian": ("pasta", "risotto", "carbonara", "bolognese", "puttanesca",
                "pizza", "focaccia", "bruschetta", "caponata", "osso buco",
                "lasagne", "lasagna", "tiramisu", "panna cotta", "arrabbiata",
                "cacio e pepe", "ragu", "gremolata", "piccata", "milanese"),
    "french": ("ratatouille", "cassoulet", "confit", "gratin", "tarte tatin",
               "crème brûlée", "coq au vin", "bouillabaisse", "beurre blanc",
               "quiche", "croque", "clafoutis", "veloute", "provencal"),
    "spanish": ("paella", "tapas", "gazpacho", "tortilla espanola", "romesco",
                "patatas bravas", "albondigas"),
    "greek": ("moussaka", "souvlaki", "tzatziki", "spanakopita", "gyros", "horiatiki"),
    "middle eastern": ("falafel", "shawarma", "fattoush", "tabbouleh", "baba ganoush",
                       "muhammara", "kofta", "shakshuka", "labneh", "baklava"),
    "north african": ("tagine", "harira", "chermoula", "merguez", "shakshuka"),
    "indian": ("curry", "dal", "dhal", "biryani", "korma", "tikka", "masala",
               "vindaloo", "raita", "samosa", "pakora", "rogan josh", "saag",
               "chana", "paratha", "dosa"),
    "thai": ("pad thai", "tom yum", "tom kha", "green curry", "red curry",
             "massaman", "som tam", "larb"),
    "vietnamese": ("pho", "banh mi", "bun cha", "goi cuon", "nuoc cham"),
    "chinese": ("stir-fry", "stir fry", "chow mein", "lo mein", "dumpling",
                "wonton", "kung pao", "mapo", "char siu", "dan dan", "congee"),
    "japanese": ("ramen", "sushi", "teriyaki", "katsu", "tempura", "donburi",
                 "yakitori", "okonomiyaki", "gyoza", "onigiri", "miso soup"),
    "korean": ("bibimbap", "bulgogi", "japchae", "tteokbokki", "banchan"),
    "mexican": ("taco", "burrito", "quesadilla", "enchilada", "fajita", "guacamole",
                "salsa", "tostada", "elote", "pozole", "mole", "ceviche"),
    "british": ("shepherd's pie", "cottage pie", "toad in the hole", "bubble and squeak",
                "eton mess", "crumble", "trifle", "scone", "bangers", "ploughman",
                "sticky toffee", "yorkshire pudding", "welsh rarebit"),
    "american": ("burger", "mac and cheese", "cornbread", "brownie", "pancake",
                 "buffalo", "jambalaya", "gumbo", "clam chowder", "s'more",
                 "key lime", "pecan pie", "sloppy joe", "coleslaw"),
    "german": ("schnitzel", "spaetzle", "bratwurst", "strudel", "sauerbraten"),
    "eastern european": ("borscht", "pierogi", "goulash", "stroganoff", "blini"),
    "nordic": ("gravlax", "smorrebrod", "cardamom bun"),
    "caribbean": ("jerk", "callaloo", "rice and peas", "escovitch"),
    "turkish": ("borek", "menemen", "pide", "lahmacun", "kebab"),
    "portuguese": ("piri piri", "bacalhau", "pastel de nata", "cataplana"),
}

# Regional adjectives that appear in titles and chapter headings.
_CUISINE_WORDS: dict[str, tuple[str, ...]] = {
    "italian": ("italian", "tuscan", "sicilian", "roman", "neapolitan", "venetian"),
    "french": ("french", "parisian", "breton", "burgundy", "normandy"),
    "spanish": ("spanish", "catalan", "basque", "andalusian"),
    "greek": ("greek", "cretan", "cypriot"),
    "middle eastern": ("middle eastern", "lebanese", "persian", "iranian",
                       "israeli", "syrian", "palestinian", "levantine"),
    "north african": ("moroccan", "tunisian", "algerian", "north african"),
    "indian": ("indian", "punjabi", "keralan", "goan", "bengali", "gujarati",
               "sri lankan", "pakistani"),
    "thai": ("thai",), "vietnamese": ("vietnamese",),
    "chinese": ("chinese", "cantonese", "sichuan", "szechuan", "hunan", "taiwanese"),
    "japanese": ("japanese",), "korean": ("korean",),
    "mexican": ("mexican", "oaxacan", "yucatan", "tex-mex", "baja"),
    "british": ("british", "english", "scottish", "welsh", "irish", "cornish"),
    "american": ("american", "southern", "cajun", "creole", "californian",
                 "new england", "texan"),
    "german": ("german", "bavarian", "austrian"),
    "eastern european": ("polish", "russian", "ukrainian", "hungarian", "czech",
                         "georgian", "romanian"),
    "nordic": ("swedish", "danish", "norwegian", "finnish", "nordic", "scandinavian"),
    "caribbean": ("caribbean", "jamaican", "cuban", "trinidadian"),
    "turkish": ("turkish", "ottoman", "anatolian"),
    "portuguese": ("portuguese", "azorean"),
}

CUISINE_THRESHOLD = 3.0     # below this, the evidence is too thin to label


@dataclass(slots=True)
class Classification:
    """What a recipe was judged to be."""

    meals: list[str]
    cuisine: str | None
    cuisine_score: float = 0.0


def _contains(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(n)}", haystack) for n in needles)


def classify_meal(title: str, section: str, ingredients: list[str],
                  instructions: str = "") -> list[str]:
    """Which meals a recipe suits. Always returns at least one.

    Evidence is used in strict priority order rather than pooled. The chapter
    heading is the author's own filing and beats everything; the title comes
    next; the ingredient signature is consulted only when the book has told us
    nothing, because it can only really distinguish sweet from savoury.
    """
    title_l = title.lower()
    section_l = section.lower()

    from_section = [m for m, words in _SECTION_MEALS.items()
                    if section_l and _contains(section_l, words)]
    from_title = [m for m, words in _TITLE_MEALS.items()
                  if _contains(title_l, words)]

    explicit = from_section or from_title
    if explicit:
        return _finish_meals(explicit)

    # Nothing stated. Fall back to what the ingredients imply.
    names = set(ingredients)
    sweet = len(names & _SWEET_MARKERS)
    savoury = len(names & _SAVOURY_MARKERS)
    baking = len(names & _BAKING_MARKERS)
    if savoury == 0 and (sweet >= 2 or (sweet >= 1 and baking >= 2)):
        return ["dessert"]

    # A savoury dish with no other signal is a main, and mains serve twice.
    return ["lunch", "dinner"]


def _finish_meals(found: list[str]) -> list[str]:
    """Normalise a set of meal signals into the final list."""
    # Sweet wins outright: a dessert is not also a dinner.
    if "dessert" in found:
        return ["dessert"]

    ordered = [m for m in MEALS if m in found]
    # A main course suits both midday and evening unless it is clearly neither.
    if "dinner" in ordered and "lunch" not in ordered and not (
            {"breakfast", "side", "snack"} & set(ordered)):
        ordered.append("lunch")
        ordered = [m for m in MEALS if m in ordered]
    return ordered


def classify_cuisine(title: str, section: str, ingredients: list[str],
                     book_title: str = "") -> tuple[str | None, float]:
    """Best guess at a cuisine, or None when the evidence is too thin."""
    title_l = title.lower()
    context = f"{section.lower()} {book_title.lower()}"
    names = set(ingredients)
    scores: dict[str, float] = dict.fromkeys(CUISINES, 0.0)

    for cuisine in CUISINES:
        # A named dish is close to conclusive.
        if _contains(title_l, _CUISINE_TITLES.get(cuisine, ())):
            scores[cuisine] += 4.0
        # A regional adjective in the title is conclusive; in the chapter or
        # book title it is suggestive, since a book may span several regions.
        words = _CUISINE_WORDS.get(cuisine, ())
        if _contains(title_l, words):
            scores[cuisine] += 4.0
        elif _contains(context, words):
            scores[cuisine] += 2.0
        # Ingredient signatures accumulate.
        for name, weight in _CUISINE_INGREDIENTS.get(cuisine, {}).items():
            if name in names:
                scores[cuisine] += weight

    best = max(scores, key=lambda c: scores[c])
    top = scores[best]
    if top < CUISINE_THRESHOLD:
        return None, top

    # If two kitchens score near-identically the signal is shared, not specific
    # (fish sauce is Thai and Vietnamese), so decline rather than guess.
    runner_up = max((v for c, v in scores.items() if c != best), default=0.0)
    if top - runner_up < 1.0:
        return None, top

    return best, top


def classify(title: str, section: str, ingredients: list[str],
             instructions: str = "", book_title: str = "") -> Classification:
    """Classify a recipe's meal types and cuisine."""
    meals = classify_meal(title, section, ingredients, instructions)
    cuisine, score = classify_cuisine(title, section, ingredients, book_title)
    return Classification(meals=meals, cuisine=cuisine, cuisine_score=score)


# --- difficulty ------------------------------------------------------------

# Techniques that need a practised hand, or that punish a mistake.
ADVANCED_TECHNIQUES: frozenset[str] = frozenset({
    "temper", "tempering", "emulsify", "emulsion", "laminate", "confit",
    "sous vide", "clarify", "caramelise", "caramelize", "deglaze", "flambé",
    "flambe", "render", "truss", "fillet", "julienne", "brunoise", "proof",
    "proving", "knead", "blanch", "braise", "poach", "reduce", "fold",
    "whip", "whisk to", "ferment", "cure", "brine", "score", "spatchcock",
    "double boiler", "bain-marie", "candy thermometer", "piping bag",
})


def difficulty(n_ingredients: int, n_steps: int, total_minutes: int | None,
               instructions: str = "") -> str:
    """Rate a recipe Easy, Medium or Hard.

    A judgement, not a measurement: it counts how much there is to buy, how
    many moves to make, how long it runs, and whether it calls for a technique
    that takes practice.
    """
    score = 0.0

    if n_ingredients >= 15:
        score += 2
    elif n_ingredients >= 10:
        score += 1

    if n_steps >= 12:
        score += 2
    elif n_steps >= 7:
        score += 1

    if total_minutes:
        if total_minutes > 150:
            score += 2
        elif total_minutes > 60:
            score += 1

    lowered = instructions.lower()
    techniques = sum(1 for t in ADVANCED_TECHNIQUES if t in lowered)
    score += min(2, techniques * 0.5)

    if score >= 5:
        return "Hard"
    if score >= 2:
        return "Medium"
    return "Easy"

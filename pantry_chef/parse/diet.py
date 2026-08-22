"""Detect allergens in a recipe, and derive the diets it fits.

Read this before trusting it: detection is **positive-only**. We find the
allergens we recognise; we cannot prove their absence. An ingredient the
lexicon has never seen is counted as unknown rather than assumed safe, and that
count travels with the recipe so the interface can say how sure it is.

That distinction is the whole design. "Contains nuts" is a fact we can
establish from an ingredient list. "Nut free" is a claim about everything the
book did not say, which no parser can make from text alone.

The mappings below deliberately include the classic hidden sources -- fish
sauce and Worcestershire sauce contain fish, soy sauce contains wheat, most
parmesan is made with animal rennet -- because those are exactly the ones a
naive ingredient scan misses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The EU's 14 declarable allergens, minus lupin (vanishingly rare in cookbooks),
# plus the meat/fish groupings the diet rules need.
ALLERGENS = (
    "gluten", "crustaceans", "egg", "fish", "peanuts", "soy", "milk",
    "nuts", "celery", "mustard", "sesame", "sulphites", "molluscs",
)

DIETS = ("vegetarian", "vegan", "pescatarian", "no red meat")

# --- what counts as meat, fish and shellfish -------------------------------

MEAT: frozenset[str] = frozenset({
    "chicken", "chicken breast", "chicken thigh", "chicken wing", "turkey",
    "duck", "goose", "quail", "beef", "steak", "brisket", "short rib",
    "ground beef", "veal", "pork", "pork belly", "pork chop", "sausage",
    "bacon", "pancetta", "prosciutto", "ham", "chorizo", "salami", "lamb",
    "lamb chop", "mutton", "rabbit", "venison", "liver", "oxtail", "mince",
    "ground pork", "ground lamb", "chicken stock", "beef stock", "lard",
    "gelatin", "suet", "duck fat", "merguez",
})

RED_MEAT: frozenset[str] = frozenset({
    "beef", "steak", "brisket", "short rib", "ground beef", "veal", "pork",
    "pork belly", "pork chop", "bacon", "pancetta", "prosciutto", "ham",
    "chorizo", "salami", "lamb", "lamb chop", "mutton", "venison", "oxtail",
    "ground pork", "ground lamb", "beef stock", "liver", "sausage", "mince",
})

FISH: frozenset[str] = frozenset({
    "fish", "salmon", "tuna", "cod", "haddock", "halibut", "sea bass",
    "snapper", "trout", "mackerel", "sardine", "anchovy", "herring", "sole",
    "plaice", "monkfish", "fish stock", "fish sauce", "dashi", "salt cod",
    "worcestershire sauce", "gravlax", "bonito",
})

CRUSTACEANS: frozenset[str] = frozenset({
    "shrimp", "prawn", "crab", "lobster", "langoustine", "crayfish", "scampi",
})

MOLLUSCS: frozenset[str] = frozenset({
    "scallop", "mussel", "clam", "oyster", "squid", "octopus", "calamari",
    "cuttlefish", "whelk", "winkle",
})

DAIRY: frozenset[str] = frozenset({
    "milk", "buttermilk", "cream", "heavy cream", "light cream", "sour cream",
    "crème fraîche", "yogurt", "greek yogurt", "butter", "ghee", "cheese",
    "cheddar", "parmesan", "mozzarella", "feta", "ricotta", "mascarpone",
    "halloumi", "gruyere", "brie", "goat cheese", "blue cheese", "cream cheese",
    "manchego", "paneer", "condensed milk", "evaporated milk", "custard",
    "ice cream", "labneh", "kefir", "clotted cream", "whey", "casein",
    # Plain and white chocolate contain milk; dark chocolate usually does not
    # and is treated separately below.
    "chocolate", "milk chocolate", "white chocolate", "nutella",
})

EGG: frozenset[str] = frozenset({
    "egg", "egg white", "egg yolk", "mayonnaise", "meringue", "aioli",
})

GLUTEN: frozenset[str] = frozenset({
    "flour", "bread flour", "self-rising flour", "bread", "breadcrumb", "panko",
    "pasta", "spaghetti", "penne", "fusilli", "linguine", "tagliatelle",
    "fettuccine", "macaroni", "lasagna", "noodle", "ramen", "udon",
    "vermicelli", "gnocchi", "couscous", "bulgur", "barley", "semolina",
    "farro", "rye", "spelt", "seitan", "pita", "naan", "baguette", "brioche",
    "crouton", "tortilla", "puff pastry", "filo", "phyllo", "beer", "soy sauce",
    "hoisin", "oyster sauce", "miso", "orzo", "flatbread", "focaccia",
    "digestive biscuit", "cracker", "pretzel", "wheat", "gochujang",
})

NUTS: frozenset[str] = frozenset({
    "almond", "walnut", "pecan", "cashew", "pistachio", "hazelnut",
    "macadamia", "brazil nut", "pine nut", "chestnut", "praline", "marzipan",
    "nutella", "almond flour", "almond milk", "frangipane",
})

PEANUTS: frozenset[str] = frozenset({"peanut", "peanut butter", "peanut oil", "satay"})

SOY: frozenset[str] = frozenset({
    "soy sauce", "tofu", "tempeh", "edamame", "miso", "tamari", "soy milk",
    "hoisin", "gochujang", "doubanjiang", "ponzu",
    "seitan",   # seitan is wheat, but is nearly always soy-seasoned
})

SESAME: frozenset[str] = frozenset({"sesame seed", "sesame oil", "tahini", "hummus", "zaatar"})

MUSTARD: frozenset[str] = frozenset({"mustard", "dijon mustard", "mustard seed", "wholegrain mustard"})

CELERY: frozenset[str] = frozenset({"celery", "celeriac", "celery salt"})

SULPHITES: frozenset[str] = frozenset({
    "wine", "white wine", "red wine", "sherry", "port", "vermouth", "cider",
    "balsamic vinegar", "wine vinegar", "white wine vinegar", "red wine vinegar",
    "dried apricot", "raisin", "sultana", "molasses",
})

# canonical ingredient -> the allergens it carries
_ALLERGEN_SOURCES: dict[str, frozenset[str]] = {
    "gluten": GLUTEN,
    "crustaceans": CRUSTACEANS,
    "egg": EGG,
    "fish": FISH,
    "peanuts": PEANUTS,
    "soy": SOY,
    "milk": DAIRY,
    "nuts": NUTS,
    "celery": CELERY,
    "mustard": MUSTARD,
    "sesame": SESAME,
    "sulphites": SULPHITES,
    "molluscs": MOLLUSCS,
}

ALLERGEN_INDEX: dict[str, set[str]] = {}
for _allergen, _members in _ALLERGEN_SOURCES.items():
    for _name in _members:
        ALLERGEN_INDEX.setdefault(_name, set()).add(_allergen)

# Animal-derived ingredients that read as neither meat nor fish at a glance are
# already members of MEAT (gelatin, suet, lard, duck fat) and FISH (anchovy,
# fish sauce, Worcestershire sauce, dashi, bonito), so the diet rules below need
# no separate exception list.

# Cheeses traditionally made with animal rennet. Flagged rather than excluded,
# because vegetarian versions of all of them are widely sold.
RENNET_CHEESES: frozenset[str] = frozenset({
    "parmesan", "gruyere", "manchego", "pecorino", "grana padano",
})

# Everything we can positively vouch for as containing no allergen at all.
# Used only to decide whether an ingredient counts as "unknown".
KNOWN_SAFE: frozenset[str] = frozenset({
    "salt", "pepper", "black pepper", "white pepper", "water", "ice", "sugar",
    "brown sugar", "powdered sugar", "honey", "maple syrup", "olive oil",
    "vegetable oil", "sunflower oil", "canola oil", "coconut oil", "oil",
    "cooking spray", "baking powder", "baking soda", "yeast", "cornstarch",
    "vanilla extract", "rice", "basmati rice", "arborio rice", "jasmine rice",
    "brown rice", "wild rice", "quinoa", "polenta", "cornmeal", "oat",
    "oatmeal", "potato", "sweet potato", "onion", "red onion", "shallot",
    "garlic", "leek", "scallion", "chive", "carrot", "parsnip", "turnip",
    "beet", "radish", "fennel", "cabbage", "kale", "spinach", "chard",
    "lettuce", "arugula", "watercress", "broccoli", "cauliflower",
    "brussels sprout", "asparagus", "artichoke", "zucchini", "squash",
    "pumpkin", "cucumber", "eggplant", "tomato", "cherry tomato",
    "canned tomato", "tomato puree", "passata", "bell pepper", "chili",
    "jalapeno", "habanero", "mushroom", "corn", "pea", "green bean",
    "snow pea", "snap pea", "okra", "avocado", "olive", "caper", "pickle",
    "sauerkraut", "kimchi", "apple", "pear", "banana", "orange", "lemon",
    "lime", "grapefruit", "peach", "plum", "apricot", "cherry", "strawberry",
    "raspberry", "blueberry", "blackberry", "cranberry", "grape", "melon",
    "watermelon", "mango", "pineapple", "kiwi", "fig", "date", "prune",
    "coconut", "coconut milk", "pomegranate", "rhubarb", "black bean",
    "kidney bean", "cannellini bean", "butter bean", "chickpea", "lentil",
    "red lentil", "split pea", "bean", "vegetable stock", "paprika", "cumin",
    "coriander", "cardamom", "cinnamon", "nutmeg", "clove", "allspice",
    "turmeric", "ginger", "saffron", "vanilla", "bay leaf", "oregano",
    "thyme", "rosemary", "sage", "basil", "parsley", "dill", "mint",
    "tarragon", "marjoram", "star anise", "fennel seed", "caraway", "sumac",
    "garam masala", "curry powder", "chili powder", "chili flake", "cayenne",
    "five spice", "harissa", "sriracha", "hot sauce", "tabasco", "ketchup",
    "rice vinegar", "apple cider vinegar", "vinegar", "lemon zest",
    "orange zest", "rice noodle", "sunflower seed", "pumpkin seed",
    "poppy seed", "chia seed", "flaxseed", "cocoa", "raisin bran",
    "nutritional yeast", "jam", "marmalade", "syrup", "plantain",
    "dark chocolate", "chocolate chip", "coffee", "espresso", "tea",
})


@dataclass(slots=True)
class DietProfile:
    """What a recipe contains, and what it therefore suits."""

    allergens: set[str] = field(default_factory=set)
    diets: set[str] = field(default_factory=set)
    unknown: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    @property
    def n_unknown(self) -> int:
        return len(self.unknown)

    @property
    def is_certain(self) -> bool:
        """Whether every ingredient was recognised."""
        return not self.unknown


def allergens_for(canonical: str) -> set[str]:
    """Allergens carried by one canonical ingredient."""
    return set(ALLERGEN_INDEX.get(canonical, ()))


def is_recognised(canonical: str) -> bool:
    """Whether we know enough about an ingredient to reason about it."""
    return (
        canonical in KNOWN_SAFE
        or canonical in ALLERGEN_INDEX
        or canonical in MEAT
        or canonical in FISH
        or canonical in CRUSTACEANS
        or canonical in MOLLUSCS
    )


def profile(ingredients: list[str]) -> DietProfile:
    """Analyse a recipe's canonical ingredients for allergens and diets."""
    result = DietProfile()
    names = set(ingredients)

    for name in ingredients:
        result.allergens |= allergens_for(name)
        if not is_recognised(name):
            result.unknown.append(name)

    has_meat = bool(names & MEAT)
    has_fish = bool(names & FISH)
    has_shellfish = bool(names & (CRUSTACEANS | MOLLUSCS))

    vegetarian = not (has_meat or has_fish or has_shellfish)
    if vegetarian:
        result.diets.add("vegetarian")
        if not (names & DAIRY or names & EGG or "honey" in names):
            result.diets.add("vegan")

    # Pescatarians eat fish and shellfish but no meat.
    if not has_meat:
        result.diets.add("pescatarian")

    if not (names & RED_MEAT):
        result.diets.add("no red meat")

    # Caveats are shown to the reader rather than acted on silently.
    rennet = names & RENNET_CHEESES
    if vegetarian and rennet:
        result.caveats.append(
            f"{', '.join(sorted(rennet))} is traditionally made with animal rennet"
        )
    if "soy sauce" in names:
        result.caveats.append("soy sauce usually contains wheat")
    if "dark chocolate" in names:
        result.caveats.append("dark chocolate is often made on shared dairy equipment")
    if names & {"worcestershire sauce", "fish sauce"}:
        result.caveats.append("contains a fish-based sauce")

    return result

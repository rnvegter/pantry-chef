"""Vocabulary that drives heuristic recipe extraction and ingredient matching.

Everything here is data, not logic. The parser leans on three questions:
  - does this line look like an ingredient line?   -> UNITS, QUANTITY_WORDS, INGREDIENT_NOUNS
  - what ingredient is it, really?                 -> DESCRIPTORS, SYNONYMS
  - does the cook already have it anyway?          -> STAPLES
"""

from __future__ import annotations

# --- measurement -----------------------------------------------------------

# Maps every spelling we expect to see onto one canonical unit token.
UNITS: dict[str, str] = {}


def _unit(canonical: str, *spellings: str) -> None:
    UNITS[canonical] = canonical
    for s in spellings:
        UNITS[s] = canonical


_unit("tsp", "teaspoon", "teaspoons", "tsps", "t.", "tsp.")
_unit("tbsp", "tablespoon", "tablespoons", "tbsps", "tbs", "tbl", "tbsp.", "T.")
_unit("cup", "cups", "c.")
_unit("oz", "ounce", "ounces", "oz.")
_unit("lb", "pound", "pounds", "lbs", "lb.", "#")
_unit("g", "gram", "grams", "gr", "gm", "g.")
_unit("kg", "kilogram", "kilograms", "kilo", "kilos", "kg.")
_unit("ml", "milliliter", "milliliters", "millilitre", "millilitres", "ml.", "cc")
_unit("l", "liter", "liters", "litre", "litres", "l.")
_unit("pint", "pints", "pt")
_unit("quart", "quarts", "qt")
_unit("gallon", "gallons", "gal")
_unit("clove", "cloves")
_unit("bunch", "bunches")
_unit("sprig", "sprigs")
_unit("stalk", "stalks")
_unit("head", "heads")
_unit("slice", "slices")
_unit("piece", "pieces")
_unit("can", "cans", "tin", "tins")
_unit("jar", "jars")
_unit("package", "packages", "pkg", "packet", "packets")
_unit("stick", "sticks")
_unit("pinch", "pinches")
_unit("dash", "dashes")
_unit("handful", "handfuls")
_unit("sheet", "sheets")
_unit("fillet", "fillets", "filet", "filets")
_unit("rasher", "rashers")
_unit("cm", "centimeter", "centimeters", "centimetre", "centimetres")
_unit("inch", "inches", "in.")

# Words that stand in for a number at the head of an ingredient line.
QUANTITY_WORDS: frozenset[str] = frozenset(
    """a an one two three four five six seven eight nine ten eleven twelve
    half quarter third couple few several some dozen""".split()
)

# Unicode vulgar fractions -> decimal value.
VULGAR_FRACTIONS: dict[str, float] = {
    "½": 0.5, "⅓": 1 / 3, "⅔": 2 / 3, "¼": 0.25,
    "¾": 0.75, "⅕": 0.2, "⅖": 0.4, "⅗": 0.6,
    "⅘": 0.8, "⅙": 1 / 6, "⅚": 5 / 6, "⅛": 0.125,
    "⅜": 0.375, "⅝": 0.625, "⅞": 0.875, "⅐": 1 / 7,
    "⅑": 1 / 9, "⅒": 0.1,
}

# --- words to strip off an ingredient before canonicalising ----------------

# Preparation states, sizes, qualities. Removed wherever they appear.
DESCRIPTORS: frozenset[str] = frozenset(
    """fresh freshly dried dry frozen thawed canned tinned jarred bottled raw cooked
    uncooked precooked leftover ripe unripe overripe firm soft hard tender
    large small medium big little tiny extra jumbo baby young mature whole halved
    quartered chopped finely coarsely roughly minced diced sliced thinly thickly
    shredded grated ground crushed cracked julienned cubed torn shaved peeled
    unpeeled seeded deseeded cored stemmed trimmed rinsed washed drained
    boneless skinless bone-in skin-on lean fatty
    softened melted cooled warmed chilled room temperature
    unsalted salted sweetened unsweetened plain natural pure organic
    virgin extra-virgin light heavy full-fat low-fat reduced-fat nonfat fat-free
    good-quality best-quality quality store-bought homemade
    packed lightly loosely heaping level scant generous approximately about
    optional divided plus more needed taste garnish serving drizzling
    thick thin fine coarse long short round flat
    toasted roasted smoked cured pickled fermented
    boiling hot cold lukewarm warm iced
    all-purpose granulated caster confectioners powdered superfine
    skimmed semi-skimmed whole-milk
    finely-chopped roughly-chopped""".split()
)

# Trailing clauses that describe handling rather than identity.
NOTE_MARKERS: tuple[str, ...] = (
    " plus more", " plus extra", " to taste", " for garnish", " for serving",
    " for drizzling", " for brushing", " for dusting", " for frying",
    " for greasing", " if needed", " if desired", " or to taste",
    " at room temperature", " divided", " optional",
)

# Lines that look like ingredients but are section labels inside the list.
SUBHEAD_MARKERS: frozenset[str] = frozenset(
    """for the sauce dressing filling topping crust dough marinade glaze
    garnish base batter frosting icing assembly serve""".split()
)

# --- ingredient identity ---------------------------------------------------

# Ingredients assumed to be in every kitchen. They never count as "missing".
STAPLES: frozenset[str] = frozenset({
    "salt", "pepper", "black pepper", "white pepper", "water", "ice",
    "olive oil", "vegetable oil", "cooking oil", "sunflower oil",
    "canola oil", "oil", "cooking spray", "butter", "sugar", "flour",
    "baking powder", "baking soda", "vanilla extract",
})

# Alternate names -> canonical name. Regional variants matter a lot here.
SYNONYMS: dict[str, str] = {
    "spring onion": "scallion", "green onion": "scallion", "salad onion": "scallion",
    "coriander leaves": "coriander", "cilantro": "coriander",
    "aubergine": "eggplant", "courgette": "zucchini", "rocket": "arugula",
    "capsicum": "bell pepper", "sweet pepper": "bell pepper",
    "chickpeas": "chickpea", "garbanzo bean": "chickpea", "garbanzo": "chickpea",
    "coriander seed": "coriander", "beetroot": "beet", "swede": "rutabaga",
    "mange tout": "snow pea", "mangetout": "snow pea", "sugar snap": "snap pea",
    "double cream": "heavy cream", "whipping cream": "heavy cream",
    "single cream": "light cream", "soured cream": "sour cream",
    "natural yogurt": "yogurt", "yoghurt": "yogurt", "greek yoghurt": "greek yogurt",
    "cornflour": "cornstarch", "corn flour": "cornstarch",
    "plain flour": "flour", "all purpose flour": "flour", "ap flour": "flour",
    "strong flour": "bread flour", "self raising flour": "self-rising flour",
    "icing sugar": "powdered sugar", "confectioners sugar": "powdered sugar",
    "caster sugar": "sugar", "granulated sugar": "sugar", "white sugar": "sugar",
    "demerara": "brown sugar", "muscovado": "brown sugar",
    "bicarbonate of soda": "baking soda", "bicarb": "baking soda",
    "prawn": "shrimp", "prawns": "shrimp", "king prawn": "shrimp",
    "mince": "ground beef", "minced beef": "ground beef", "beef mince": "ground beef",
    # "ground" is stripped as a preparation word everywhere else ("ground
    # cumin" is cumin), but for meat it is the identity: ground beef is not
    # steak. These are matched before descriptors are removed.
    "ground beef": "ground beef", "ground pork": "ground pork",
    "ground lamb": "ground lamb", "ground turkey": "ground turkey",
    "ground chicken": "ground chicken", "ground veal": "ground veal",
    "minced pork": "ground pork", "pork mince": "ground pork",
    "minced lamb": "ground lamb", "streaky bacon": "bacon", "back bacon": "bacon",
    "gammon": "ham", "chicken breasts": "chicken breast",
    "chicken thighs": "chicken thigh", "stock cube": "stock",
    "bouillon": "stock", "broth": "stock", "chicken broth": "chicken stock",
    "beef broth": "beef stock", "vegetable broth": "vegetable stock",
    "passata": "tomato puree", "tomato paste": "tomato puree",
    "tinned tomatoes": "canned tomato", "chopped tomatoes": "canned tomato",
    "plum tomatoes": "tomato", "cherry tomatoes": "cherry tomato",
    "aubergines": "eggplant", "chilli": "chili", "chile": "chili",
    "chilli flakes": "chili flake", "red pepper flakes": "chili flake",
    "chilli powder": "chili powder", "paprika sweet": "paprika",
    "soy": "soy sauce", "shoyu": "soy sauce", "tamari": "soy sauce",
    "fish sauce nam pla": "fish sauce", "nam pla": "fish sauce",
    "creme fraiche": "crème fraîche", "parmigiano": "parmesan",
    "parmigiano reggiano": "parmesan", "grana padano": "parmesan",
    "mozzarella cheese": "mozzarella", "cheddar cheese": "cheddar",
    "feta cheese": "feta", "goats cheese": "goat cheese",
    "linguini": "linguine", "spaghetti pasta": "spaghetti",
    "risotto rice": "arborio rice", "basmati": "basmati rice",
    "peanut": "peanut", "groundnut": "peanut", "groundnut oil": "peanut oil",
    "rapeseed oil": "canola oil", "corn oil": "vegetable oil",
    "eggs": "egg", "egg whites": "egg white", "egg yolks": "egg yolk",
    "lemons": "lemon", "limes": "lime", "oranges": "orange",
    "lemon zest": "lemon", "lemon juice": "lemon", "lime juice": "lime",
    "orange zest": "orange", "orange juice": "orange",
    "garlic cloves": "garlic", "clove of garlic": "garlic",
    "onions": "onion", "red onions": "red onion", "shallots": "shallot",
    "potatoes": "potato", "sweet potatoes": "sweet potato",
    "carrots": "carrot", "mushrooms": "mushroom",
    "walnuts": "walnut", "almonds": "almond", "cashews": "cashew",
    "pine nuts": "pine nut", "hazelnuts": "hazelnut", "pistachios": "pistachio",
    "raisins": "raisin", "sultanas": "raisin", "currants": "currant",
    "cannellini beans": "cannellini bean", "black beans": "black bean",
    "kidney beans": "kidney bean", "butter beans": "butter bean",
    "lentils": "lentil", "red lentils": "red lentil", "puy lentils": "lentil",
    "peas": "pea", "green beans": "green bean", "runner beans": "green bean",
    "breadcrumbs": "breadcrumb", "panko breadcrumbs": "panko",
    "maple syrup pure": "maple syrup", "golden syrup": "syrup",
    "vinegar white wine": "white wine vinegar",
    "balsamic": "balsamic vinegar", "cider vinegar": "apple cider vinegar",
}

# Synonyms whose meaning depends on the exact wording, so they must be matched
# literally and never singularised. "Peppers" and "red pepper" are the
# vegetable; bare "pepper" is the spice and stays a staple. Folding the two
# together would quietly hide a real ingredient inside the staples.
EXACT_SYNONYMS: dict[str, str] = {
    "peppers": "bell pepper",
    "bell peppers": "bell pepper",
    "red pepper": "bell pepper",
    "red peppers": "bell pepper",
    "green pepper": "bell pepper",
    "green peppers": "bell pepper",
    "yellow pepper": "bell pepper",
    "yellow peppers": "bell pepper",
    "romano pepper": "bell pepper",
    "romano peppers": "bell pepper",
}

# Base vocabulary of ingredient head-nouns. Used to score whether a line is an
# ingredient line at all, and to find the head noun inside a long phrase.
INGREDIENT_NOUNS: frozenset[str] = frozenset("""
onion shallot garlic leek scallion chive celery carrot parsnip turnip rutabaga
potato sweet_potato yam beet radish fennel cabbage kale spinach chard lettuce
arugula watercress broccoli cauliflower brussels_sprout asparagus artichoke
zucchini squash pumpkin cucumber eggplant tomato cherry_tomato pepper
bell_pepper chili jalapeno habanero mushroom corn pea green_bean snow_pea
snap_pea okra avocado olive caper pickle sauerkraut kimchi
apple pear banana orange lemon lime grapefruit mandarin peach nectarine plum
apricot cherry strawberry raspberry blueberry blackberry cranberry grape melon
watermelon mango pineapple papaya kiwi fig date raisin currant prune coconut
pomegranate rhubarb
chicken chicken_breast chicken_thigh chicken_wing turkey duck goose quail
beef steak brisket short_rib ground_beef veal pork pork_belly pork_chop
sausage bacon pancetta prosciutto ham chorizo salami lamb lamb_chop mutton
rabbit venison liver oxtail mince
fish salmon tuna cod haddock halibut sea_bass snapper trout mackerel sardine
anchovy herring sole plaice monkfish shrimp prawn crab lobster scallop mussel
clam oyster squid octopus calamari
egg egg_white egg_yolk milk buttermilk cream heavy_cream sour_cream yogurt
greek_yogurt butter ghee cheese cheddar parmesan mozzarella feta ricotta
mascarpone halloumi gruyere brie goat_cheese blue_cheese cream_cheese
rice basmati_rice arborio_rice jasmine_rice brown_rice wild_rice quinoa
couscous bulgur barley farro oat oatmeal polenta cornmeal semolina
pasta spaghetti penne fusilli linguine tagliatelle fettuccine macaroni lasagna
noodle ramen udon soba rice_noodle vermicelli gnocchi
bread breadcrumb panko tortilla pita naan baguette brioche crouton
flour bread_flour self-rising_flour cornstarch yeast baking_powder baking_soda
sugar brown_sugar powdered_sugar honey maple_syrup syrup molasses treacle
chocolate cocoa dark_chocolate white_chocolate chocolate_chip
bean black_bean kidney_bean cannellini_bean butter_bean chickpea lentil
red_lentil split_pea tofu tempeh seitan edamame
almond walnut pecan cashew pistachio hazelnut peanut pine_nut macadamia
sesame_seed sunflower_seed pumpkin_seed poppy_seed chia_seed flaxseed
salt pepper black_pepper paprika cumin coriander cardamom cinnamon nutmeg
clove allspice turmeric ginger saffron vanilla bay_leaf oregano thyme rosemary
sage basil parsley cilantro dill mint tarragon marjoram fenugreek star_anise
fennel_seed mustard_seed caraway sumac zaatar garam_masala curry_powder
chili_powder chili_flake cayenne five_spice herbes_de_provence
oil olive_oil vegetable_oil sesame_oil coconut_oil peanut_oil canola_oil
vinegar balsamic_vinegar white_wine_vinegar red_wine_vinegar rice_vinegar
apple_cider_vinegar sherry_vinegar
stock chicken_stock beef_stock vegetable_stock fish_stock dashi
wine white_wine red_wine sherry port vermouth brandy rum whiskey vodka beer
cider sake mirin
soy_sauce fish_sauce worcestershire_sauce hot_sauce sriracha tabasco harissa
miso tahini hummus mayonnaise mustard dijon_mustard ketchup tomato_puree
canned_tomato passata coconut_milk cream_of_coconut peanut_butter jam marmalade
gelatin cornflake raisin_bran nutritional_yeast coffee espresso tea
water ice lemon_zest orange_zest
""".split())

# Stored with underscores for multi-word entries; expose the spaced form too.
INGREDIENT_NOUNS_SPACED: frozenset[str] = frozenset(
    n.replace("_", " ") for n in INGREDIENT_NOUNS
)

# Head nouns of multi-word ingredients, for fallback matching
# ("smoked streaky bacon" -> "bacon").
HEAD_NOUNS: frozenset[str] = frozenset(
    n.replace("_", " ").split()[-1] for n in INGREDIENT_NOUNS
)

# --- book structure --------------------------------------------------------

# Headings that mark non-recipe matter; used to suppress false recipe starts.
FRONT_BACK_MATTER: frozenset[str] = frozenset({
    "contents", "table of contents", "copyright", "acknowledgements",
    "acknowledgments", "dedication", "foreword", "preface", "introduction",
    "about the author", "index", "glossary", "appendix", "bibliography",
    "notes", "further reading", "resources", "conversion chart", "conversions",
    "equipment", "pantry basics", "techniques", "how to use this book",
    "measurements", "suppliers", "stockists", "thanks", "credits",
    "title page", "epilogue", "afterword", "imprint", "colophon",
})

# Labels that introduce an ingredient list.
INGREDIENT_HEADERS: frozenset[str] = frozenset(
    {"ingredients", "ingredient", "you will need", "you'll need", "what you need",
     "for the recipe", "shopping list", "the ingredients"}
)

# Labels that introduce the method.
METHOD_HEADERS: frozenset[str] = frozenset(
    {"method", "instructions", "directions", "preparation", "steps", "to make",
     "how to make", "procedure", "the method", "what to do"}
)

# Verbs that start an instruction step. Used to tell method from ingredients.
COOKING_VERBS: frozenset[str] = frozenset(
    """add adjust arrange assemble bake baste beat blanch blend boil bring broil
    brown brush bubble carve chill chop coat combine cook cool cover cream crush
    cut deglaze dice dip divide drain dress drizzle drop dust fill flip fold fry
    garnish grate grease grill grind heat knead layer leave lift line mash melt
    mix moisten pat peel place poach position pour preheat press prick puree
    reduce refrigerate remove repeat reserve return rinse roast roll rub scatter
    scrape season serve set shake simmer skim slice soak spoon spread sprinkle
    squeeze steam stir strain stuff swirl taste tip toast toss transfer trim
    turn using wait warm wash whip whisk wipe work wrap""".split()
)

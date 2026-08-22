"""Build synthetic cookbooks that mimic how real ebooks are structured.

Three shapes, because real libraries contain all three:
  well-formed  -- semantic headings and <ul> ingredient lists
  loose        -- no lists, no "Ingredients" label, everything is <p>
  pdf          -- no markup at all, structure implied by font size
"""

from __future__ import annotations

import pathlib
import zipfile

RECIPES = [
    {
        "title": "Lemon and Garlic Roast Chicken",
        "meta": "Serves 4 | Prep 15 minutes | Cook 1 hour 20 minutes",
        "ingredients": [
            "1 whole chicken, about 1.6kg",
            "2 lemons, halved",
            "1 whole head of garlic, halved crosswise",
            "3 tbsp olive oil",
            "2 sprigs fresh rosemary",
            "Salt and freshly ground black pepper",
        ],
        "method": [
            "Preheat the oven to 200C/180C fan.",
            "Rub the chicken all over with the olive oil and season generously.",
            "Stuff the cavity with the lemon halves, garlic and rosemary.",
            "Roast for 1 hour 20 minutes, basting twice, until the juices run clear.",
            "Rest for 15 minutes before carving.",
        ],
    },
    {
        "title": "Weeknight Tomato Linguine",
        "meta": "Serves 2 | Total time: 20 minutes",
        "ingredients": [
            "200g dried linguine",
            "2 tbsp extra-virgin olive oil",
            "3 cloves garlic, thinly sliced",
            "1 x 400g can chopped tomatoes",
            "1/2 tsp chilli flakes",
            "A handful of fresh basil, torn",
            "50g parmesan, grated",
        ],
        "method": [
            "Cook the linguine in well-salted boiling water for 9 minutes.",
            "Meanwhile, warm the oil and fry the garlic until fragrant.",
            "Add the tomatoes and chilli flakes and simmer for 8 minutes.",
            "Toss the drained pasta through the sauce with the basil.",
            "Serve with the grated parmesan.",
        ],
    },
    {
        "title": "Slow-Braised Beef Shin",
        "meta": "Serves 6 | Prep 25 minutes | Cook 3 hours",
        "ingredients": [
            "1.5kg beef shin, cut into large chunks",
            "2 onions, roughly chopped",
            "3 carrots, cut into batons",
            "2 sticks celery, sliced",
            "300ml red wine",
            "500ml beef stock",
            "2 tbsp tomato puree",
            "2 bay leaves",
        ],
        "method": [
            "Brown the beef in batches in a heavy casserole and set aside.",
            "Soften the onions, carrots and celery in the same pan for 10 minutes.",
            "Stir in the tomato puree, then pour in the wine and let it bubble.",
            "Return the beef, add the stock and bay leaves, and cover.",
            "Braise in a low oven for 3 hours until the meat falls apart.",
        ],
    },
    {
        "title": "Five-Minute Yoghurt Flatbreads",
        "meta": "Makes 6 | Ready in 15 minutes",
        "ingredients": [
            "250g self-raising flour",
            "250g Greek yoghurt",
            "1/2 tsp fine salt",
            "1 tbsp olive oil, for cooking",
        ],
        "method": [
            "Mix the flour, yoghurt and salt into a soft dough.",
            "Divide into six and roll each piece out thinly.",
            "Cook in a dry hot pan for 2 minutes per side until puffed and charred.",
        ],
    },
    {
        "title": "Overnight Chocolate Cold Brew Pots",
        "meta": "Serves 4 | Prep 10 minutes, plus overnight chilling",
        "ingredients": [
            "200g dark chocolate, chopped",
            "300ml double cream",
            "100ml strong cold brew coffee",
            "2 tbsp caster sugar",
            "1 pinch of sea salt",
        ],
        "method": [
            "Heat the cream and sugar until steaming but not boiling.",
            "Pour over the chocolate and stir until glossy and smooth.",
            "Whisk in the coffee and salt, then divide between four pots.",
            "Chill overnight before serving.",
        ],
    },
]

FRONT_MATTER = [
    ("Copyright", ["First published in 2019. All rights reserved.",
                   "No part of this book may be reproduced without permission."]),
    ("Introduction", [
        "I wrote this book for the weeknight cook who has twenty minutes and "
        "half an onion. Every recipe here has been tested in a small kitchen.",
        "You will need a heavy pan, a sharp knife and very little else.",
    ]),
    ("Contents", ["Chicken", "Pasta", "Slow Cooking", "Baking", "Puddings"]),
]

BACK_MATTER = [
    ("Index", ["basil, 24", "beef shin, 88", "chocolate, 140", "linguine, 24"]),
    ("Acknowledgements", ["Thanks to everyone who ate the failures."]),
]


def _xhtml(body: str, title: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        f'<title>{title}</title></head><body>{body}</body></html>'
    )


def _photo_bytes(seed: int) -> bytes:
    """A JPEG big enough to pass the "is this a photograph" size filter."""
    import pymupdf

    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 900, 600))
    # Texture, so the JPEG does not compress below the size threshold.
    for i in range(0, 900, 5):
        pixmap.set_rect(pymupdf.IRect(i, 0, i + 3, 600),
                        ((i + seed * 13) % 255, (i * 3) % 255, (i * 7) % 255))
    return pixmap.tobytes("jpeg", jpg_quality=95)


def _recipe_html_well_formed(r: dict) -> str:
    items = "".join(f"<li>{i}</li>" for i in r["ingredients"])
    steps = "".join(f"<p>{s}</p>" for s in r["method"])
    return (
        f'<img src="../images/photo{r["index"]}.jpg" alt=""/>'
        f'<h2>{r["title"]}</h2>'
        f'<p class="meta">{r["meta"]}</p>'
        f'<h3>Ingredients</h3><ul>{items}</ul>'
        f'<h3>Method</h3>{steps}'
    )


def _recipe_html_loose(r: dict) -> str:
    """No lists, no labels -- ingredients are bare paragraphs."""
    items = "".join(f"<p>{i}</p>" for i in r["ingredients"])
    steps = "".join(f"<p>{s}</p>" for s in r["method"])
    return f'<p><b>{r["title"]}</b></p><p>{r["meta"]}</p>{items}{steps}'


def write_epub(path: pathlib.Path, style: str = "well") -> pathlib.Path:
    """Write a minimal but valid EPUB containing the sample recipes."""
    render = _recipe_html_well_formed if style == "well" else _recipe_html_loose
    documents: list[tuple[str, str]] = []

    for i, (heading, paras) in enumerate(FRONT_MATTER):
        body = f"<h1>{heading}</h1>" + "".join(f"<p>{p}</p>" for p in paras)
        documents.append((f"front{i}.xhtml", _xhtml(body, heading)))

    for i, recipe in enumerate(RECIPES):
        recipe = {**recipe, "index": i}
        body = ("<h1>Chapter</h1>" if i == 0 else "") + render(recipe)
        documents.append((f"recipe{i}.xhtml", _xhtml(body, recipe["title"])))

    for i, (heading, paras) in enumerate(BACK_MATTER):
        body = f"<h1>{heading}</h1>" + "".join(f"<p>{p}</p>" for p in paras)
        documents.append((f"back{i}.xhtml", _xhtml(body, heading)))

    manifest = "".join(
        f'<item id="d{i}" href="{name}" media-type="application/xhtml+xml"/>'
        for i, (name, _) in enumerate(documents)
    )
    spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(documents)))
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:title>The Small Kitchen ({style})</dc:title>'
        '<dc:creator>A. Cook</dc:creator><dc:language>en</dc:language>'
        '<dc:identifier id="id">urn:uuid:test-fixture</dc:identifier>'
        f'</metadata><manifest>{manifest}</manifest><spine>{spine}</spine></package>'
    )
    container = (
        '<?xml version="1.0"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        for name, html in documents:
            zf.writestr(f"OEBPS/{name}", html)
        # Photographs, so the image pipeline has something real to find.
        if style == "well":
            for i in range(len(RECIPES)):
                zf.writestr(f"OEBPS/images/photo{i}.jpg", _photo_bytes(i))
    return path


def write_pdf(path: pathlib.Path) -> pathlib.Path:
    """Write a PDF whose only structural signal is font size."""
    import pymupdf

    doc = pymupdf.open()
    for recipe in RECIPES:
        page = doc.new_page()
        y = 72.0
        page.insert_text((72, y), recipe["title"], fontsize=20, fontname="hebo")
        y += 30
        page.insert_text((72, y), recipe["meta"], fontsize=9)
        y += 26
        page.insert_text((72, y), "Ingredients", fontsize=13, fontname="hebo")
        y += 20
        for line in recipe["ingredients"]:
            page.insert_text((72, y), line, fontsize=10)
            y += 15
        y += 12
        page.insert_text((72, y), "Method", fontsize=13, fontname="hebo")
        y += 20
        for step in recipe["method"]:
            page.insert_text((72, y), step, fontsize=10)
            y += 15

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def build_all(out: pathlib.Path) -> list[pathlib.Path]:
    return [
        write_epub(out / "small-kitchen-well.epub", "well"),
        write_epub(out / "small-kitchen-loose.epub", "loose"),
        write_pdf(out / "small-kitchen.pdf"),
    ]


if __name__ == "__main__":
    here = pathlib.Path(__file__).parent / "fixtures"
    for p in build_all(here):
        print(f"{p}  {p.stat().st_size:,} bytes")

"""Read PDFs with PyMuPDF, using font size to recover the heading structure.

PDFs carry no semantic markup, so the only signal for "this is a recipe title"
is that the text is larger or bolder than the body around it. We measure the
book's modal body size first, then judge every line against it.
"""

from __future__ import annotations

from collections import Counter

from .blocks import IMAGE, Block, renumber, tidy

# Below this, page artwork is a rule or a logo rather than a photograph.
MIN_IMAGE_BYTES = 15_000

try:
    import pymupdf  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    try:
        import fitz as pymupdf  # type: ignore
    except ImportError:
        pymupdf = None


class PDFUnavailable(RuntimeError):
    """Raised when no PDF backend is installed."""


def _lines(page) -> list[tuple[str, float, bool]]:
    """Every line on a page as (text, max font size, any bold span)."""
    out: list[tuple[str, float, bool]] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:      # 0 = text, 1 = image
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = tidy("".join(s.get("text", "") for s in spans))
            if not text:
                continue
            size = max(float(s.get("size", 0)) for s in spans)
            bold = any(
                "bold" in str(s.get("font", "")).lower() or (s.get("flags", 0) & 2 ** 4)
                for s in spans
            )
            out.append((text, round(size, 1), bold))
    return out


def read_pdf(path: str, max_pages: int | None = None) -> tuple[list[Block], dict[str, str]]:
    """Extract ordered Blocks and metadata from a PDF."""
    if pymupdf is None:
        raise PDFUnavailable("install pymupdf to index PDF cookbooks")

    blocks: list[Block] = []
    per_page: list[list[tuple[str, float, bool]]] = []
    sizes: Counter[float] = Counter()

    page_images: dict[int, str] = {}

    with pymupdf.open(path) as doc:
        meta_raw = doc.metadata or {}
        pages = range(len(doc)) if max_pages is None else range(min(len(doc), max_pages))
        for page_no in pages:
            lines = _lines(doc[page_no])
            per_page.append(lines)
            for text, size, _ in lines:
                # Weight by length so body text dominates the mode.
                sizes[size] += len(text)

            # Keep the largest photograph on the page, if there is one.
            best_xref, best_size = 0, MIN_IMAGE_BYTES
            for image in doc[page_no].get_images(full=True):
                xref = image[0]
                try:
                    extracted = doc.extract_image(xref)
                except Exception:
                    continue
                size = len(extracted.get("image", b"")) if extracted else 0
                if size > best_size:
                    best_xref, best_size = xref, size
            if best_xref:
                page_images[page_no] = f"pdf:{page_no}:{best_xref}"

    body_size = sizes.most_common(1)[0][0] if sizes else 10.0

    for page_no, lines in enumerate(per_page):
        if page_no in page_images:
            blocks.append(
                Block(text=page_images[page_no], kind=IMAGE, doc=page_no))
        for text, size, bold in lines:
            words = len(text.split())
            # A heading is bigger than body text, or bold and short.
            is_heading = (
                (size >= body_size * 1.15 and words <= 14)
                or (bold and words <= 8 and size >= body_size)
            )
            level = 1 if size >= body_size * 1.5 else 2 if is_heading else 0
            blocks.append(
                Block(
                    text=text,
                    kind="heading" if is_heading else "para",
                    level=level,
                    doc=page_no,
                    font_size=size,
                    bold=bold,
                )
            )

    meta = {
        "title": (meta_raw.get("title") or "").strip(),
        "creator": (meta_raw.get("author") or "").strip(),
    }
    return renumber(blocks), {k: v for k, v in meta.items() if v}

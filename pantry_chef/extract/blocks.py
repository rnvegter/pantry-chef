"""The common currency between file formats: an ordered list of text blocks.

Every reader -- EPUB, PDF, MOBI -- produces `Block`s, and the segmenter only
ever sees Blocks. That keeps format quirks out of the recipe logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# Structural roles the segmenter cares about.
HEADING = "heading"
PARA = "para"
LIST_ITEM = "list_item"
TABLE_CELL = "table_cell"
IMAGE = "image"          # text holds the image's location, not prose


@dataclass(slots=True)
class Block:
    """One run of text, with whatever structural hints the format gave us."""

    text: str
    kind: str = PARA
    level: int = 0          # heading depth; 0 when not a heading
    doc: int = 0            # spine index / page number
    order: int = 0          # position within the whole book
    font_size: float = 0.0  # PDFs only; 0 when unknown
    bold: bool = False
    css_class: str = ""

    @property
    def is_heading(self) -> bool:
        return self.kind == HEADING

    @property
    def is_image(self) -> bool:
        return self.kind == IMAGE

    @property
    def words(self) -> int:
        return 0 if self.kind == IMAGE else len(self.text.split())


_WS_RE = re.compile(r"[ \t   ]+")
_NL_RE = re.compile(r"\n{2,}")


def tidy(text: str) -> str:
    """Collapse whitespace without losing deliberate line breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    text = _NL_RE.sub("\n", text)
    return text.strip()


def clean_metadata(value: str) -> str:
    """Tidy a title or author string out of a book's own metadata.

    Publishers leave separators behind: `dc:creator` frequently reads
    "Danielle Sepsy;" because the field was built by joining a list that had one
    entry. Trailing separators are stripped, internal ones left alone — "Will
    Bulsiewicz, MD" is a name, not a list.
    """
    value = _WS_RE.sub(" ", (value or "").replace("\n", " ")).strip()
    return value.strip(" ;,·|/&").strip()


# Tags whose content is never body text.
_SKIP_TAGS = {"script", "style", "head", "title", "meta", "link", "svg", "nav"}
_BLOCK_TAGS = {
    "p", "div", "section", "article", "blockquote", "figcaption", "dd", "dt",
    "pre", "address", "aside", "main", "header", "footer", "hgroup",
}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


class _HTMLBlockParser(HTMLParser):
    """Flatten XHTML into Blocks, keeping headings and list structure."""

    def __init__(self, doc: int = 0) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self._doc = doc
        self._buf: list[str] = []
        self._kind = PARA
        self._level = 0
        self._class = ""
        self._skip_depth = 0
        self._stack: list[tuple[str, str, int, str]] = []

    # -- buffer handling ---------------------------------------------------

    def _flush(self) -> None:
        text = tidy("".join(self._buf))
        self._buf.clear()
        if text:
            self.blocks.append(
                Block(text=text, kind=self._kind, level=self._level,
                      doc=self._doc, css_class=self._class)
            )
        self._kind, self._level, self._class = PARA, 0, ""

    def _open(self, kind: str, level: int, css: str) -> None:
        self._flush()
        self._kind, self._level, self._class = kind, level, css

    # -- HTMLParser hooks --------------------------------------------------

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        css = ""
        for name, value in attrs:
            if name == "class" and value:
                css = value.lower()

        if tag == "img" or tag == "image":
            # Images are emitted as their own block so the segmenter can pair a
            # photo with the recipe it sits beside.
            src = ""
            for name, value in attrs:
                if name in ("src", "xlink:href", "href") and value:
                    src = value
                    break
            if src:
                self._flush()
                self.blocks.append(
                    Block(text=src, kind=IMAGE, doc=self._doc, css_class=css))
        elif tag == "br":
            self._buf.append("\n")
        elif tag in _HEADINGS:
            self._open(HEADING, _HEADINGS[tag], css)
        elif tag == "li":
            self._open(LIST_ITEM, 0, css)
        elif tag in ("td", "th"):
            self._open(TABLE_CELL, 0, css)
        elif tag in _BLOCK_TAGS:
            self._open(PARA, 0, css)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in _HEADINGS or tag in _BLOCK_TAGS or tag in {"li", "td", "th", "ul", "ol", "table"}:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._buf.append(data)

    def close(self) -> None:  # type: ignore[override]
        super().close()
        self._flush()


def blocks_from_html(html: str, doc: int = 0) -> list[Block]:
    """Parse an XHTML/HTML document into Blocks."""
    parser = _HTMLBlockParser(doc=doc)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Malformed markup is common in older ebooks; keep whatever parsed.
        pass
    return parser.blocks


def blocks_from_text(text: str, doc: int = 0) -> list[Block]:
    """Split flat text into Blocks, one per line, guessing headings."""
    out: list[Block] = []
    for line in text.split("\n"):
        line = tidy(line)
        if not line:
            continue
        kind = HEADING if _looks_like_plain_heading(line) else PARA
        out.append(Block(text=line, kind=kind, level=2 if kind == HEADING else 0, doc=doc))
    return out


def _looks_like_plain_heading(line: str) -> bool:
    """Heading guess for formats with no markup: short, titled, unpunctuated."""
    words = line.split()
    if not (1 <= len(words) <= 12):
        return False
    if line.endswith((".", ",", ";", ":")):
        return False
    if line.isupper():
        return True
    capitalised = sum(1 for w in words if w[:1].isupper())
    return capitalised >= max(1, int(len(words) * 0.6))


def renumber(blocks: list[Block]) -> list[Block]:
    """Assign each block its position in the book."""
    for i, block in enumerate(blocks):
        block.order = i
    return blocks

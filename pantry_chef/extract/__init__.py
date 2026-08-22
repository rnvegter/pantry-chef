"""Format dispatch: any supported ebook path in, ordered Blocks out."""

from __future__ import annotations

import pathlib

from .blocks import Block

# Kobo's .kepub is an EPUB with extra spans; the plain EPUB reader handles it.
EPUB_SUFFIXES = {".epub", ".kepub"}
PDF_SUFFIXES = {".pdf"}
MOBI_SUFFIXES = {".mobi", ".azw", ".azw3", ".azw4", ".prc"}
SUPPORTED_SUFFIXES = EPUB_SUFFIXES | PDF_SUFFIXES | MOBI_SUFFIXES


class UnsupportedFormat(ValueError):
    """Raised for a file extension we have no reader for."""


def format_of(path: str | pathlib.Path) -> str:
    """Short format name for a path, e.g. 'epub'."""
    suffix = pathlib.Path(path).suffix.lower()
    # .kepub.epub is the common Kobo spelling.
    if str(path).lower().endswith(".kepub.epub"):
        return "kepub"
    if suffix in EPUB_SUFFIXES:
        return suffix.lstrip(".")
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in MOBI_SUFFIXES:
        return suffix.lstrip(".")
    raise UnsupportedFormat(f"no reader for {suffix or path!r}")


def read_book(path: str | pathlib.Path) -> tuple[list[Block], dict[str, str]]:
    """Read any supported ebook into (blocks, metadata)."""
    path = str(path)
    suffix = pathlib.Path(path).suffix.lower()

    if suffix in EPUB_SUFFIXES:
        from .epub import read_epub
        return read_epub(path)
    if suffix in PDF_SUFFIXES:
        from .pdf import read_pdf
        return read_pdf(path)
    if suffix in MOBI_SUFFIXES:
        from .mobi import read_mobi
        return read_mobi(path)

    raise UnsupportedFormat(f"no reader for {suffix or path!r}")


def is_supported(path: str | pathlib.Path) -> bool:
    """True when a reader exists for this path."""
    name = str(path).lower()
    if name.endswith(".kepub.epub"):
        return True
    return pathlib.Path(name).suffix in SUPPORTED_SUFFIXES

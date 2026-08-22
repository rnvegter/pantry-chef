"""Read Kindle formats (.mobi, .azw, .azw3, .prc).

The `mobi` package unpacks the container to a temporary directory, yielding
either an EPUB or loose HTML. Either way we hand off to the existing readers.
"""

from __future__ import annotations

import pathlib
import shutil

from .blocks import Block, blocks_from_html, renumber

try:
    import mobi as _mobi  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    _mobi = None


class MobiUnavailable(RuntimeError):
    """Raised when the mobi backend is not installed."""


def read_mobi(path: str) -> tuple[list[Block], dict[str, str]]:
    """Extract ordered Blocks and metadata from a Kindle-format ebook."""
    if _mobi is None:
        raise MobiUnavailable("install the 'mobi' package to index Kindle books")

    tempdir, filepath = _mobi.extract(str(path))
    try:
        target = pathlib.Path(filepath)

        if target.suffix.lower() == ".epub":
            from .epub import read_epub
            return read_epub(str(target))

        # Otherwise the unpacker left us HTML; concatenate in filename order.
        root = target.parent
        html_files = sorted(
            p for p in root.rglob("*")
            if p.suffix.lower() in {".html", ".htm", ".xhtml"}
        )
        if target.is_file() and target not in html_files:
            html_files.insert(0, target)

        blocks: list[Block] = []
        for index, file in enumerate(html_files):
            try:
                html = file.read_text("utf-8", errors="replace")
            except OSError:
                continue
            blocks.extend(blocks_from_html(html, doc=index))

        return renumber(blocks), {}
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)

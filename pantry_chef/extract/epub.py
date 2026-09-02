"""Read EPUB (and Kobo .kepub) files with nothing but the standard library.

An EPUB is a zip: META-INF/container.xml points at an OPF package file, whose
manifest names the documents and whose spine gives their reading order.
"""

from __future__ import annotations

import posixpath
import zipfile
from xml.etree import ElementTree as ET

from .blocks import IMAGE, Block, blocks_from_html, clean_metadata, renumber

# Images that are furniture rather than photographs.
_NOT_A_PHOTO = (
    "cover", "logo", "ornament", "orn_", "rule", "divider", "flourish",
    "titlepage", "title_page", "bullet", "icon", "spacer", "line_art",
    "backad", "ad_", "author", "signature", "qr",
)
# Below this a file is a decoration, not a recipe photograph.
MIN_IMAGE_BYTES = 15_000

_OPF_NS = "{http://www.idpf.org/2007/opf}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"
_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"


def _find_opf(zf: zipfile.ZipFile) -> str | None:
    try:
        root = ET.fromstring(zf.read("META-INF/container.xml"))
    except (KeyError, ET.ParseError):
        # Some books ship a broken container; fall back to any .opf present.
        for name in zf.namelist():
            if name.lower().endswith(".opf"):
                return name
        return None

    for rootfile in root.iter(f"{_CONTAINER_NS}rootfile"):
        path = rootfile.get("full-path")
        if path:
            return path
    return None


def _spine_documents(zf: zipfile.ZipFile, opf_path: str) -> tuple[list[str], dict[str, str]]:
    """Return document paths in reading order, plus Dublin Core metadata."""
    try:
        opf = ET.fromstring(zf.read(opf_path))
    except (KeyError, ET.ParseError):
        return [], {}

    base = posixpath.dirname(opf_path)
    meta: dict[str, str] = {}
    for tag in ("title", "creator", "language", "publisher", "date"):
        node = opf.find(f".//{_DC_NS}{tag}")
        if node is not None and node.text:
            cleaned = clean_metadata(node.text)
            if cleaned:
                meta[tag] = cleaned

    manifest: dict[str, tuple[str, str]] = {}
    for item in opf.iter(f"{_OPF_NS}item"):
        item_id, href = item.get("id"), item.get("href")
        if item_id and href:
            manifest[item_id] = (href, item.get("media-type", ""))

    ordered: list[str] = []
    for ref in opf.iter(f"{_OPF_NS}itemref"):
        idref = ref.get("idref")
        if not idref or idref not in manifest:
            continue
        href, media = manifest[idref]
        if media and "html" not in media and "xml" not in media:
            continue
        ordered.append(posixpath.normpath(posixpath.join(base, href)) if base else href)

    if not ordered:
        # No usable spine: take every html document in the archive, in order.
        ordered = [
            n for n in zf.namelist()
            if n.lower().endswith((".xhtml", ".html", ".htm"))
        ]

    return ordered, meta


def read_epub(path: str) -> tuple[list[Block], dict[str, str]]:
    """Extract ordered Blocks and metadata from an EPUB file."""
    blocks: list[Block] = []
    meta: dict[str, str] = {}

    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        opf_path = _find_opf(zf)
        documents: list[str] = []
        if opf_path:
            documents, meta = _spine_documents(zf, opf_path)

        for index, doc in enumerate(documents):
            if doc not in names:
                # Hrefs are sometimes percent-encoded or oddly cased.
                candidates = [n for n in names if n.lower().endswith(doc.lower())]
                if not candidates:
                    continue
                doc = candidates[0]
            try:
                raw = zf.read(doc)
            except KeyError:
                continue
            html = raw.decode("utf-8", errors="replace")
            parsed = blocks_from_html(html, doc=index)
            _resolve_images(parsed, doc, names, zf)
            blocks.extend(parsed)

    return renumber(blocks), meta


def _resolve_images(blocks: list[Block], doc_path: str, names: set[str],
                    zf: zipfile.ZipFile) -> None:
    """Turn each image block's href into a zip entry path, or drop it.

    Hrefs are relative to the document that referenced them, and a book is full
    of images that are not photographs -- covers, ornaments, rules -- so both
    the name and the file size are used to weed them out.
    """
    base = posixpath.dirname(doc_path)
    keep: list[Block] = []

    for block in blocks:
        if block.kind != IMAGE:
            keep.append(block)
            continue

        href = block.text.split("#")[0].strip()
        if not href:
            continue
        entry = posixpath.normpath(posixpath.join(base, href)) if base else href
        if entry not in names:
            matches = [n for n in names if n.lower().endswith(href.lower().lstrip("./"))]
            if not matches:
                continue
            entry = matches[0]

        lowered = entry.lower()
        if any(marker in lowered for marker in _NOT_A_PHOTO):
            continue
        try:
            if zf.getinfo(entry).file_size < MIN_IMAGE_BYTES:
                continue
        except KeyError:
            continue

        block.text = entry
        keep.append(block)

    blocks[:] = keep

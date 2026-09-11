"""Serve a recipe's photograph straight out of the book it came from.

Images are referenced, never copied at index time: a 500-book library holds
tens of thousands of photographs, and duplicating them would cost gigabytes to
store artwork the user already has on disk. The first request for a photo
extracts it and caches the bytes; every later request is a file read.
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR_NAME = "image-cache"

CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
}

# A page's artwork is only worth showing if it is a real photograph.
MIN_BYTES = 15_000

# Print-resolution artwork runs to several megabytes a page, which is absurd
# for a screen. Photos are downscaled once, on first request, and the smaller
# version is what gets cached and served.
MAX_WIDTH = 1400
JPEG_QUALITY = 82

# Search results show each photo in a small square. The short side is what
# fills the square once it is cropped, so that is the side sized: 240px is
# twice the 112px slot, which stays sharp on a high-density screen. A page of
# fifty results then costs about a megabyte rather than a hundred.
THUMB_SIDE = 240
THUMB_QUALITY = 78


def content_type_for(name: str) -> str:
    return CONTENT_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def sniff_type(data: bytes, fallback: str) -> str:
    """The content type the bytes actually are.

    A cached file's suffix is not trustworthy: it comes from the name inside
    the book, and a PNG that was downscaled is stored as JPEG under the PNG's
    name. PDF photos have no name at all.
    """
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return fallback


def cache_path(cache_root: Path, book_path: str, image_ref: str, *,
               thumb: bool = False) -> Path:
    """A stable cache filename for one image in one book."""
    identity = f"{book_path}|{image_ref}" + ("|thumb" if thumb else "")
    key = hashlib.sha256(identity.encode()).hexdigest()[:20]
    suffix = Path(image_ref).suffix.lower()
    if thumb or suffix not in CONTENT_TYPES:
        suffix = ".jpg"
    return cache_root / CACHE_DIR_NAME / f"{key}{suffix}"


def version_tag(book_path: str, image_ref: str) -> str:
    """A short token that changes whenever a recipe's photo does.

    Photo URLs are keyed on recipe id and cached by the browser for a week, but
    a forced re-index hands out ids afresh. Putting this in the URL means a
    reused id can never show the previous recipe's photo.
    """
    return hashlib.sha256(f"{book_path}|{image_ref}".encode()).hexdigest()[:10]


def _from_epub(book_path: str, image_ref: str) -> tuple[bytes, str] | None:
    try:
        with zipfile.ZipFile(book_path) as zf:
            try:
                data = zf.read(image_ref)
            except KeyError:
                # The archive may name the entry slightly differently.
                tail = posixpath.basename(image_ref).lower()
                matches = [n for n in zf.namelist() if n.lower().endswith(tail)]
                if not matches:
                    return None
                data = zf.read(matches[0])
                image_ref = matches[0]
    except (OSError, zipfile.BadZipFile):
        return None
    return data, content_type_for(image_ref)


def _from_pdf(book_path: str, image_ref: str) -> tuple[bytes, str] | None:
    """`image_ref` is "pdf:<page>:<xref>"."""
    try:
        import pymupdf
    except ImportError:
        return None

    parts = image_ref.split(":")
    if len(parts) != 3:
        return None
    try:
        xref = int(parts[2])
    except ValueError:
        return None

    try:
        with pymupdf.open(book_path) as doc:
            extracted = doc.extract_image(xref)
    except Exception:
        # Swallowed on purpose — one unreadable photo must not break the page —
        # but logged, or a book that has lost every image looks the same as a
        # book that never had any.
        logger.warning("could not read image %s from %s", image_ref, book_path,
                       exc_info=True)
        return None
    if not extracted or not extracted.get("image"):
        return None
    return extracted["image"], f"image/{extracted.get('ext', 'jpeg')}"


def downscale(data: bytes) -> tuple[bytes, str] | None:
    """Shrink a print-resolution photo to something sensible for a page.

    Returns None when the image is already small enough, or cannot be decoded,
    in which case the original bytes are served untouched.
    """
    try:
        import pymupdf
    except ImportError:
        return None

    try:
        pixmap = pymupdf.Pixmap(data)
        if pixmap.width <= MAX_WIDTH:
            return None
        # shrink() halves each time, which is fast and artefact-free. Halving
        # past the target is fine: a 2000px photo becomes 1000px, which is
        # still sharper than the column it is displayed in.
        while pixmap.width > MAX_WIDTH:
            pixmap.shrink(1)
        return _jpeg(pixmap, JPEG_QUALITY)
    except Exception:
        # Falling back to the original bytes is correct, so this is not an
        # error for the reader — but if it starts happening to every photo the
        # log is the only place that would say so.
        logger.warning("could not downscale an image; serving it unchanged",
                       exc_info=True)
        return None


def thumbnail(data: bytes) -> tuple[bytes, str] | None:
    """A small JPEG for the results list, short side THUMB_SIDE pixels.

    Returns None when the image cannot be decoded (an SVG, say), in which case
    the caller serves what it has; the browser scales it down on its own.
    """
    try:
        import pymupdf
    except ImportError:
        return None

    try:
        pixmap = pymupdf.Pixmap(data)
        # Halve first, which is fast and averages cleanly, then make one exact
        # final step. Scaling a print-sized photo straight to 240px in a single
        # step would sample rather than average, and look grainy.
        while min(pixmap.width, pixmap.height) >= 2 * THUMB_SIDE:
            pixmap.shrink(1)
        short = min(pixmap.width, pixmap.height)
        if short > THUMB_SIDE:
            scale = THUMB_SIDE / short
            pixmap = pymupdf.Pixmap(pixmap, max(1, round(pixmap.width * scale)),
                                    max(1, round(pixmap.height * scale)))
        return _jpeg(pixmap, THUMB_QUALITY)
    except Exception:
        logger.warning("could not make a thumbnail; serving the photo unchanged",
                       exc_info=True)
        return None


def _jpeg(pixmap, quality: int) -> tuple[bytes, str]:
    import pymupdf

    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)
    if pixmap.colorspace is None or pixmap.n > 3:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
    return pixmap.tobytes("jpeg", jpg_quality=quality), "image/jpeg"


def _read_cache(path: Path | None) -> tuple[bytes, str] | None:
    if not path or not path.exists():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data, sniff_type(data, content_type_for(path.name))


def _write_cache(path: Path | None, data: bytes) -> None:
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        pass       # a cache miss is not worth failing the request over


def _from_book(book_path: str, image_ref: str) -> tuple[bytes, str] | None:
    if not Path(book_path).exists():
        return None
    if image_ref.startswith("pdf:"):
        return _from_pdf(book_path, image_ref)
    return _from_epub(book_path, image_ref)


def load_image(book_path: str, image_ref: str, cache_root: Path | None = None,
               *, thumb: bool = False) -> tuple[bytes, str] | None:
    """Fetch one recipe photo, using the on-disk cache when it is warm.

    With `thumb`, the small version for the results list. Thumbnails are made
    from the full-size cache when that is already there, and from the book
    otherwise — without caching the full-size photo along the way, because a
    page of results would otherwise fill the cache with fifty photos nobody
    opened.
    """
    if not image_ref or not book_path:
        return None

    full_path = cache_path(cache_root, book_path, image_ref) if cache_root else None

    if thumb:
        thumb_path = (cache_path(cache_root, book_path, image_ref, thumb=True)
                      if cache_root else None)
        hit = _read_cache(thumb_path)
        if hit:
            return hit
        source = _read_cache(full_path) or _from_book(book_path, image_ref)
        if source is None:
            return None
        small = thumbnail(source[0])
        if small is None:
            return source[0], sniff_type(source[0], source[1])
        _write_cache(thumb_path, small[0])
        return small

    hit = _read_cache(full_path)
    if hit:
        return hit

    result = _from_book(book_path, image_ref)
    if result is None:
        return None

    smaller = downscale(result[0])
    if smaller is not None:
        result = smaller

    _write_cache(full_path, result[0])
    return result

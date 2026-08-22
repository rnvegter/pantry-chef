"""Serve a recipe's photograph straight out of the book it came from.

Images are referenced, never copied at index time: a 500-book library holds
tens of thousands of photographs, and duplicating them would cost gigabytes to
store artwork the user already has on disk. The first request for a photo
extracts it and caches the bytes; every later request is a file read.
"""

from __future__ import annotations

import hashlib
import posixpath
import zipfile
from pathlib import Path

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


def content_type_for(name: str) -> str:
    return CONTENT_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def cache_path(cache_root: Path, book_path: str, image_ref: str) -> Path:
    """A stable cache filename for one image in one book."""
    key = hashlib.sha256(f"{book_path}|{image_ref}".encode()).hexdigest()[:20]
    suffix = Path(image_ref).suffix.lower()
    if suffix not in CONTENT_TYPES:
        suffix = ".jpg"
    return cache_root / CACHE_DIR_NAME / f"{key}{suffix}"


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
        if pixmap.alpha:
            pixmap = pymupdf.Pixmap(pixmap, 0)
        if pixmap.colorspace is None or pixmap.n > 3:
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
        return pixmap.tobytes("jpeg", jpg_quality=JPEG_QUALITY), "image/jpeg"
    except Exception:
        return None


def load_image(book_path: str, image_ref: str,
               cache_root: Path | None = None) -> tuple[bytes, str] | None:
    """Fetch one recipe photo, using the on-disk cache when it is warm."""
    if not image_ref or not book_path:
        return None

    cached = cache_path(cache_root, book_path, image_ref) if cache_root else None
    if cached and cached.exists():
        try:
            return cached.read_bytes(), content_type_for(cached.name)
        except OSError:
            pass

    if not Path(book_path).exists():
        return None

    if image_ref.startswith("pdf:"):
        result = _from_pdf(book_path, image_ref)
    else:
        result = _from_epub(book_path, image_ref)

    if result is None:
        return None

    smaller = downscale(result[0])
    if smaller is not None:
        result = smaller

    if cached:
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(result[0])
        except OSError:
            pass       # a cache miss is not worth failing the request over

    return result

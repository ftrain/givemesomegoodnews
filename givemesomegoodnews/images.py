"""Local image cache.

Feed images are downloaded, downscaled, and served from this site. Nothing
is hotlinked: publishers' servers are hit once per image, not once per
reader, and the site keeps working when they reorganize their CDN.

Cached files live in site/img/ and are named by the SHA-1 of the source
URL, so a repeat crawl of the same image is a no-op.
"""

import hashlib
from io import BytesIO

from PIL import Image

# A feed pointed us at a 134-megapixel JPEG. Decoding one of those costs
# hundreds of megabytes of RAM on a box that is also running Postgres.
Image.MAX_IMAGE_PIXELS = 40_000_000

from . import config
from .fetchutil import get

# A feed image that is smaller than this is a logo, a byline portrait, or a
# tracking pixel — not art for the story.
MIN_SOURCE_WIDTH = 200
MAX_BYTES = 8 * 1024 * 1024
THUMB_WIDTH = 480
# WebP at this quality lands roughly a third smaller than JPEG q80 at the
# same width, which matters at a thousand feeds. Files already cached as
# .jpg stay valid and are never re-fetched.
WEBP_QUALITY = 78
JPEG_QUALITY = 80


def cache_dir():
    d = config.SITE_DIR / "img"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached_name(url, ext=".webp"):
    return hashlib.sha1(url.encode("utf-8")).hexdigest() + ext


def dimensions(name):
    """(width, height) of an already-cached file, or (None, None)."""
    try:
        with Image.open(cache_dir() / name) as img:
            return img.width, img.height
    except Exception:
        return None, None


def cache_image(url):
    """Fetch, downscale, and store one image.

    Returns (filename, width, height), or (None, None, None). Never raises:
    a broken image is a missing image, not a failed crawl. An image already
    on disk is never re-fetched — the filename is the hash of its source.
    """
    if not url or not url.lower().startswith(("http://", "https://")):
        return None, None, None
    name = cached_name(url)
    path = cache_dir() / name
    if path.exists():
        w, h = dimensions(name)
        return name, w, h
    # Anything cached before the switch to WebP is still good.
    legacy = cached_name(url, ".jpg")
    if (cache_dir() / legacy).exists():
        w, h = dimensions(legacy)
        return legacy, w, h

    try:
        resp = get(url, retries=0)
        if resp.status_code != 200:
            return None, None, None
        ctype = resp.headers.get("content-type", "")
        if ctype and not ctype.lower().startswith("image/"):
            return None, None, None
        data = resp.content
        if not data or len(data) > MAX_BYTES:
            return None, None, None

        img = Image.open(BytesIO(data))
        if img.width < MIN_SOURCE_WIDTH:
            return None, None, None
        img.load()
        # Flatten transparency and drop any animation onto a white card.
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.split()[-1])
            img = flat
        else:
            img = img.convert("RGB")
        if img.width > THUMB_WIDTH:
            height = round(img.height * THUMB_WIDTH / img.width)
            img = img.resize((THUMB_WIDTH, height), Image.LANCZOS)
        try:
            img.save(path, "WEBP", quality=WEBP_QUALITY, method=6)
        except (KeyError, OSError, ValueError):
            # No WebP support in this Pillow build; JPEG is a fine fallback.
            name = cached_name(url, ".jpg")
            path = cache_dir() / name
            img.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        return name, img.width, img.height
    except Exception:
        # Bad bytes, refused connection, decompression bomb — one bad
        # image must never stop a crawl.
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
        return None, None, None

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

from . import config
from .fetchutil import get

# A feed image that is smaller than this is a logo, a byline portrait, or a
# tracking pixel — not art for the story.
MIN_SOURCE_WIDTH = 200
MAX_BYTES = 8 * 1024 * 1024
THUMB_WIDTH = 480
JPEG_QUALITY = 80


def cache_dir():
    d = config.SITE_DIR / "img"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached_name(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest() + ".jpg"


def cache_image(url):
    """Fetch, downscale, and store one image. Returns its filename, or None.

    Never raises: a broken image is a missing image, not a failed crawl.
    """
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    name = cached_name(url)
    path = cache_dir() / name
    if path.exists():
        return name

    try:
        resp = get(url, retries=0)
        if resp.status_code != 200:
            return None
        ctype = resp.headers.get("content-type", "")
        if ctype and not ctype.lower().startswith("image/"):
            return None
        data = resp.content
        if not data or len(data) > MAX_BYTES:
            return None

        img = Image.open(BytesIO(data))
        if img.width < MIN_SOURCE_WIDTH:
            return None
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
        img.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        return name
    except Exception:
        # Bad bytes, refused connection, decompression bomb — one bad
        # image must never stop a crawl.
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
        return None

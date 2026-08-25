import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///localpaper")
EMBEDDER = os.environ.get("EMBEDDER", "hashing")

DATA_DIR = ROOT / "data"
SITE_DIR = ROOT / "site"
ORGS_FILE = DATA_DIR / "orgs.yaml"
STATES_GEOJSON = DATA_DIR / "us_states.geojson"
ABOUT_OVERRIDES_DIR = DATA_DIR / "about_overrides"

USER_AGENT = "LocalpaperBot/0.1 (+https://github.com/ftrain/localpaper; a directory and feed reader celebrating independent local news)"
FETCH_TIMEOUT = 25

# Feeds: how many entries to keep per org per crawl, and how old is too old.
MAX_ENTRIES_PER_FEED = 40
MAX_ARTICLE_AGE_DAYS = 180

# Skip syndicated wire copy (e.g. the Inquirer republishing AP) — the feed
# should carry each newsroom's own journalism.
EXCLUDE_URL_SUBSTRINGS = ["/wires/"]

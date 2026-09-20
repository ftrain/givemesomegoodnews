import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///givemesomegoodnews")
EMBEDDER = os.environ.get("EMBEDDER", "hashing")

DATA_DIR = ROOT / "data"
SITE_DIR = ROOT / "site"
ORGS_FILE = DATA_DIR / "orgs.yaml"
STATES_GEOJSON = DATA_DIR / "us_states.geojson"
ABOUT_OVERRIDES_DIR = DATA_DIR / "about_overrides"
ASSETS_DIR = ROOT / "assets"

SITE_NAME = "Give Me Some Good News"
SITE_URL = "https://givemesomegood.news"
# The site in one sentence: the line on the share card, the description a
# page gives a search engine when it has nothing more particular to say, and
# what a post of a link to it carries underneath the picture. One sentence
# in one place, so those three can never drift apart.
SITE_DESCRIPTION = ("Local not-for-profit newsrooms and community news sources "
                    "from around the United States.")
# Drawn by givemesomegoodnews.share_card and copied into the site by the
# build; the size is what the platforms crop least from.
SHARE_IMAGE = "share.png"
SHARE_IMAGE_SIZE = (1200, 630)
SHARE_IMAGE_ALT = ("The words Give Me Some Good News above a map of the "
                   "United States with a red dot on every town that has a "
                   "newsroom in the catalog.")
REPO_URL = "https://github.com/ftrain/givemesomegoodnews"
REPO_LABEL = "ftrain/givemesomegoodnews"

USER_AGENT = (
    f"GiveMeSomeGoodNewsBot/0.1 (+{SITE_URL}; a directory and feed reader "
    "celebrating independent local news)"
)
FETCH_TIMEOUT = 25
# Crawling ~1,900 feeds is network-bound, not CPU-bound.
CRAWL_WORKERS = int(os.environ.get("CRAWL_WORKERS", "16"))

# Feeds: how many entries to keep per org per crawl, and how old is too old.
MAX_ENTRIES_PER_FEED = 40
MAX_ARTICLE_AGE_DAYS = 180

# Skip syndicated wire copy (e.g. the Inquirer republishing AP) — the feed
# should carry each newsroom's own journalism.
EXCLUDE_URL_SUBSTRINGS = ["/wires/"]

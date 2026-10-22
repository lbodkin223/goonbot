import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("GOONBOT_DATA", str(ROOT / "data")))
DB_PATH = DATA_DIR / "goonbot.db"

BASE_URL = "https://www.gaymaletube.com"
IMPERSONATE = "chrome"          # curl_cffi TLS fingerprint; gets past Cloudflare's managed challenge

# Embedding model. Qwen3-Embedding is Alibaba's open-weight (Apache-2.0) model; embedding models
# never refuse content, they just turn text into vectors. Any sentence-transformers model works here.
EMBED_MODEL = os.environ.get("GOONBOT_MODEL", "Qwen/Qwen3-Embedding-0.6B")
MAX_SEQ_LEN = 128               # titles and tag names are short; keeps encoding fast

MAX_PAGE = 100                  # site caps every listing at 100 pages x 120 cards
CONCURRENCY = 4                 # parallel page fetches
REQUEST_DELAY = 0.2             # min seconds between request starts, globally

# Query -> taxonomy matching. Taxonomy names are matched symmetrically (no retrieval prompt), so
# cosines run high: 1.0 is an exact name, ~0.9 a synonym, ~0.7 "related".
MAX_COLLECTIONS = 8             # how many site pages (cats / pornstars / terms) to pull per query
GRAM_MIN_SIM_1 = 0.90           # a single query word must hit a near-synonym page name
GRAM_MIN_SIM_N = 0.78           # a 2-3 word phrase of the query may hit a looser page name...
GRAM_SURE_SIM = 0.90            # ...and below this it must also share a word with the phrase
TERM_MAX_RANK = 3000            # a single query word may only open a canned-term page this popular
COLL_MIN_SIM = 0.60             # whole-query matches below this are ignored...
COLL_REL_DROP = 0.08            # ...as are those more than this below the best whole-query match
DOC_MAX_TAGS = 15               # collection names folded into a video's embedding text

# Final ranking weights
W_SIM, W_KEYWORD, W_COLLECTION, W_RATING = 0.70, 0.15, 0.15, 0.02

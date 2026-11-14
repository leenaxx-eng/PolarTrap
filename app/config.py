"""Central configuration. Detection thresholds and seeds live here and are
committed BEFORE any sandbox analysis (locked decision 3)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"

DB_PATH = os.getenv("DB_PATH", str(ROOT / "data" / "polartrap.db"))
ANON_HMAC_KEY = os.getenv("ANON_HMAC_KEY", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1") != "0"
GIT_COMMIT = os.getenv("GIT_COMMIT", "unknown")

AI_ROUTER_API_KEY = os.getenv("AI_ROUTER_API_KEY", "")
AI_ROUTER_BASE_URL = os.getenv("AI_ROUTER_BASE_URL", "")
AI_MODEL_CHEAP = os.getenv("AI_MODEL_CHEAP", "")
AI_MODEL_STRONG = os.getenv("AI_MODEL_STRONG", "")

# --- Sandbox -------------------------------------------------------------
SESSION_TTL_HOURS = 72
POST_MAX_CHARS = 1000
MIN_SECONDS_BETWEEN_POSTS = 3        # simple per-persona rate limit
MAX_POSTS_PER_10_MIN = 60
SANDBOX_TARGETS = {"replies": 10, "threads": 3, "feed_posts": 10, "words": 600}

# --- Detection (frozen before sandbox analysis) --------------------------
RANDOM_SEED = 20261114
EXTREME_PERCENTILE = 95.0            # T
MIN_CONTROLS = 50                    # below this -> "low reference quality"
MIN_EXCHANGES = 4                    # Key I minimum A<->B exchanges
STYLE_FULL_WORDS = 1500              # >= full volume
STYLE_REDUCED_WORDS = 500            # 500..1500 reduced, < 500 insufficient
SIMULTANEITY_WINDOW_S = 60

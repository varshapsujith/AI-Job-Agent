"""All settings in one place. Change values here, not inside the code."""

# --- Model ---
MODEL = "gemini-3.5-flash-lite"
MAX_RETRIES = 3
RATE_LIMIT_WAIT_SECONDS = 20  # wait before retrying after a 429 (rate limit) error

# --- Input safety ---
ALLOWED_TYPES = ["pdf", "docx", "txt"]
MAX_FILE_MB = 5               # per uploaded file
MAX_JOBS_PER_RUN = 10         # job descriptions per Analyze click
MAX_CHARS = 20000             # text sent to Gemini per document (CV or JD)

# --- Scoring rubric (must add up to 100) ---
SKILLS_MAX = 50
EXPERIENCE_MAX = 30
TOOLS_MAX = 20

# --- Recommendation thresholds ---
APPLY_THRESHOLD = 70
CHANGES_THRESHOLD = 50

# --- Files ---
DB_FILE = "jobs.db"
LOG_FILE = "app.log"

# --- Automatic evaluation (evaluate.py) ---
EVAL_CONSISTENCY_TOLERANCE = 5   # same CV + job twice: scores may differ by at most this
EVAL_MIN_DROP = 10               # score must fall by at least this when matched skills are removed
EVAL_MIN_SPREAD = 10             # best and worst job score should differ by at least this

# --- Cache ---
# Bump this number whenever you change the prompt, rubric or skill checks.
# Cached results made with an older version are then re-analyzed.
PROMPT_VERSION = 2
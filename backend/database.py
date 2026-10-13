"""SQLite storage for CVs, jobs and analyses.

Tables
  cvs              one row per CV version (identified by a hash of its text)
  jobs             one row per job description (identified by a hash of its text)
  analyses         one row per CV + job pair: scores, recommendation, notes
  analysis_skills  one row per skill per analysis (matching / missing / unverified)
View
  results_view     analyses joined with CV and job names, for the app and for SQL queries

Only hashes and names are stored, never the full CV or job text.
"""

import logging
import sqlite3
from contextlib import contextmanager

import pandas as pd

from config import DB_FILE

logger = logging.getLogger("jobmatcher.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cvs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    text_hash  TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    company     TEXT,
    source_file TEXT,
    text_hash   TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analyses (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    cv_id              INTEGER NOT NULL REFERENCES cvs(id) ON DELETE CASCADE,
    job_id             INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    skills_score       INTEGER NOT NULL,
    experience_score   INTEGER NOT NULL,
    tools_score        INTEGER NOT NULL,
    match_score        INTEGER NOT NULL,
    recommendation     TEXT NOT NULL,
    experience_gap     TEXT,
    resume_suggestions TEXT,
    model              TEXT,
    prompt_version     INTEGER,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (cv_id, job_id)
);

CREATE TABLE IF NOT EXISTS analysis_skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    skill       TEXT NOT NULL,
    skill_norm  TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('matching', 'missing', 'unverified'))
);

CREATE INDEX IF NOT EXISTS idx_skills_analysis ON analysis_skills(analysis_id);
CREATE INDEX IF NOT EXISTS idx_skills_norm ON analysis_skills(skill_norm, status);

CREATE VIEW IF NOT EXISTS results_view AS
SELECT
    a.id AS analysis_id,
    CASE WHEN j.company IS NULL OR j.company = ''
         THEN j.title
         ELSE j.title || ' - ' || j.company END AS job,
    c.name AS cv,
    a.match_score,
    a.recommendation,
    (SELECT group_concat(s.skill, ', ') FROM analysis_skills s
      WHERE s.analysis_id = a.id AND s.status = 'matching')   AS matching_skills,
    (SELECT group_concat(s.skill, ', ') FROM analysis_skills s
      WHERE s.analysis_id = a.id AND s.status = 'missing')    AS missing_skills,
    (SELECT group_concat(s.skill, ', ') FROM analysis_skills s
      WHERE s.analysis_id = a.id AND s.status = 'unverified') AS unverified_skills,
    a.experience_gap,
    a.resume_suggestions,
    a.skills_score,
    a.experience_score,
    a.tools_score,
    j.source_file,
    a.created_at AS analyzed_at
FROM analyses a
JOIN cvs c  ON c.id = a.cv_id
JOIN jobs j ON j.id = a.job_id;

CREATE TABLE IF NOT EXISTS application_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    status      TEXT NOT NULL
                CHECK (status IN ('Saved', 'Applied', 'Interview', 'Offer', 'Rejected')),
    event_date  TEXT NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_analysis ON application_events(analysis_id);

CREATE VIEW IF NOT EXISTS tracker_view AS
SELECT
    r.analysis_id,
    r.job,
    r.cv,
    r.match_score,
    r.recommendation,
    COALESCE((SELECT e.status FROM application_events e
               WHERE e.analysis_id = r.analysis_id
               ORDER BY e.event_date DESC, e.id DESC LIMIT 1), 'Saved') AS status,
    (SELECT e.event_date FROM application_events e
      WHERE e.analysis_id = r.analysis_id
      ORDER BY e.event_date DESC, e.id DESC LIMIT 1) AS status_date,
    (SELECT e.note FROM application_events e
      WHERE e.analysis_id = r.analysis_id
      ORDER BY e.event_date DESC, e.id DESC LIMIT 1) AS note
FROM results_view r;
"""

COLUMN_NAMES = {
    "analysis_id": "ID",
    "job": "Job",
    "cv": "CV",
    "match_score": "Match Score",
    "recommendation": "Recommendation",
    "matching_skills": "Matching Skills",
    "missing_skills": "Missing Skills",
    "unverified_skills": "Unverified Skills",
    "experience_gap": "Experience Gap",
    "resume_suggestions": "Resume Suggestions",
    "skills_score": "Skills Score",
    "experience_score": "Experience Score",
    "tools_score": "Tools Score",
    "source_file": "Source File",
    "analyzed_at": "Analyzed At",
    "status": "Status",
    "status_date": "Status Date",
    "note": "Note",
    "event_id": "Event ID",
    "event_date": "Date",
}
NUMERIC_COLUMNS = {"ID", "Match Score", "Skills Score", "Experience Score", "Tools Score", "Event ID"}


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def _frame(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = sqlite3.connect(DB_FILE)
    try:
        df = pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()
    df = df.rename(columns=COLUMN_NAMES)
    text_cols = [c for c in df.columns if c not in NUMERIC_COLUMNS]
    df[text_cols] = df[text_cols].fillna("")
    return df


# ==============================
# Cache lookup
# ==============================

def find_cached(cv_hash: str, job_hash: str, model: str, prompt_version: int):
    """Return the analysis id if this exact CV + job was already analyzed
    with the same model and prompt version, otherwise None."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT a.id FROM analyses a
            JOIN cvs c  ON c.id = a.cv_id
            JOIN jobs j ON j.id = a.job_id
            WHERE c.text_hash = ? AND j.text_hash = ?
              AND a.model = ? AND a.prompt_version = ?
            """,
            (cv_hash, job_hash, model, prompt_version),
        ).fetchone()
    return row["id"] if row else None


# ==============================
# Save
# ==============================

def save_analysis(
    cv_name: str,
    cv_hash: str,
    job: dict,
    scores: dict,
    recommendation: str,
    experience_gap: str,
    resume_suggestions: str,
    model: str,
    prompt_version: int,
    skills: dict,
) -> int:
    """Insert or update a CV, a job and their analysis. Returns the analysis id.

    job:    title, company, source_file, hash
    scores: skills, experience, tools
    skills: {"matching": [...], "missing": [...], "unverified": [...]}
    """
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO cvs (name, text_hash) VALUES (?, ?)
               ON CONFLICT(text_hash) DO UPDATE SET name = excluded.name""",
            (cv_name, cv_hash),
        )
        cv_id = conn.execute("SELECT id FROM cvs WHERE text_hash = ?", (cv_hash,)).fetchone()["id"]

        conn.execute(
            """INSERT INTO jobs (title, company, source_file, text_hash) VALUES (?, ?, ?, ?)
               ON CONFLICT(text_hash) DO UPDATE SET
                   title = excluded.title,
                   company = excluded.company,
                   source_file = excluded.source_file""",
            (job["title"], job["company"], job["source_file"], job["hash"]),
        )
        job_id = conn.execute("SELECT id FROM jobs WHERE text_hash = ?", (job["hash"],)).fetchone()["id"]

        total = scores["skills"] + scores["experience"] + scores["tools"]
        conn.execute(
            """INSERT INTO analyses
                   (cv_id, job_id, skills_score, experience_score, tools_score, match_score,
                    recommendation, experience_gap, resume_suggestions, model, prompt_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(cv_id, job_id) DO UPDATE SET
                   skills_score = excluded.skills_score,
                   experience_score = excluded.experience_score,
                   tools_score = excluded.tools_score,
                   match_score = excluded.match_score,
                   recommendation = excluded.recommendation,
                   experience_gap = excluded.experience_gap,
                   resume_suggestions = excluded.resume_suggestions,
                   model = excluded.model,
                   prompt_version = excluded.prompt_version,
                   created_at = datetime('now')""",
            (cv_id, job_id, scores["skills"], scores["experience"], scores["tools"], total,
             recommendation, experience_gap, resume_suggestions, model, prompt_version),
        )
        analysis_id = conn.execute(
            "SELECT id FROM analyses WHERE cv_id = ? AND job_id = ?", (cv_id, job_id)
        ).fetchone()["id"]

        conn.execute("DELETE FROM analysis_skills WHERE analysis_id = ?", (analysis_id,))
        conn.executemany(
            "INSERT INTO analysis_skills (analysis_id, skill, skill_norm, status) VALUES (?, ?, ?, ?)",
            [
                (analysis_id, skill.strip(), skill.strip().lower(), status)
                for status, names in skills.items()
                for skill in names
                if skill.strip()
            ],
        )

    logger.info("Saved analysis %d (cv %d, job %d)", analysis_id, cv_id, job_id)
    return analysis_id


# ==============================
# Read
# ==============================

def load_results() -> pd.DataFrame:
    return _frame("SELECT * FROM results_view ORDER BY match_score DESC, analysis_id")


def get_result(analysis_id: int) -> dict:
    df = _frame("SELECT * FROM results_view WHERE analysis_id = ?", (analysis_id,))
    return df.iloc[0].to_dict()


# ==============================
# Analytics
# ==============================

def skill_counts(status: str, cv_name: str | None = None, limit: int = 10) -> pd.DataFrame:
    """Most common skills with the given status ('matching', 'missing' or
    'unverified'), as columns: skill, jobs (number of analyses it appears in)."""
    return _frame(
        """
        SELECT MIN(s.skill) AS skill, COUNT(DISTINCT a.id) AS jobs
        FROM analysis_skills s
        JOIN analyses a ON a.id = s.analysis_id
        JOIN cvs c      ON c.id = a.cv_id
        WHERE s.status = ? AND (? IS NULL OR c.name = ?)
        GROUP BY s.skill_norm
        ORDER BY jobs DESC, s.skill_norm
        LIMIT ?
        """,
        (status, cv_name, cv_name, limit),
    )


def verification_rate(cv_name: str | None = None):
    """Share of the skills found in the CV text, out of all skills the model
    claimed as matching. Returns None when there is no data."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT SUM(s.status = 'matching')   AS ok,
                   SUM(s.status = 'unverified') AS bad
            FROM analysis_skills s
            JOIN analyses a ON a.id = s.analysis_id
            JOIN cvs c      ON c.id = a.cv_id
            WHERE s.status IN ('matching', 'unverified') AND (? IS NULL OR c.name = ?)
            """,
            (cv_name, cv_name),
        ).fetchone()
    ok, bad = row["ok"] or 0, row["bad"] or 0
    return ok / (ok + bad) if (ok + bad) else None


# ==============================
# Application tracker
# ==============================

STATUSES = ["Saved", "Applied", "Interview", "Offer", "Rejected"]

# Funnel: a job "reached" a stage if it has any update with one of these statuses
FUNNEL_STAGES = {
    "Analyzed": None,  # every analysis
    "Applied": ("Applied", "Interview", "Offer"),
    "Interview": ("Interview", "Offer"),
    "Offer": ("Offer",),
}


def add_event(analysis_id: int, status: str, event_date: str, note: str = "") -> None:
    """Record a status change. event_date is an ISO date string (YYYY-MM-DD)."""
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status}")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO application_events (analysis_id, status, event_date, note) VALUES (?, ?, ?, ?)",
            (int(analysis_id), status, event_date, (note or "").strip()),
        )
    logger.info("Analysis %d -> %s on %s", analysis_id, status, event_date)


def delete_event(event_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM application_events WHERE id = ?", (int(event_id),))
    logger.info("Deleted application event %d", event_id)


def load_tracker() -> pd.DataFrame:
    """One row per analysis with its current status (latest update, 'Saved' if none)."""
    return _frame("SELECT * FROM tracker_view ORDER BY match_score DESC, analysis_id")


def load_events() -> pd.DataFrame:
    """Every status update, oldest first."""
    return _frame(
        """
        SELECT e.id AS event_id, e.analysis_id, r.job, r.cv, e.status, e.event_date, e.note
        FROM application_events e
        JOIN results_view r ON r.analysis_id = e.analysis_id
        ORDER BY e.event_date, e.id
        """
    )


def funnel_counts() -> dict:
    """Number of jobs that reached each stage: Analyzed, Applied, Interview, Offer."""
    counts = {}
    with get_conn() as conn:
        for stage, statuses in FUNNEL_STAGES.items():
            if statuses is None:
                counts[stage] = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
            else:
                marks = ",".join("?" * len(statuses))
                counts[stage] = conn.execute(
                    f"SELECT COUNT(DISTINCT analysis_id) FROM application_events WHERE status IN ({marks})",
                    statuses,
                ).fetchone()[0]
    return counts


# ==============================
# Delete
# ==============================

def delete_analyses(ids: list[int]) -> None:
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    with get_conn() as conn:
        conn.execute(f"DELETE FROM analyses WHERE id IN ({marks})", [int(i) for i in ids])
        # remove CVs and jobs that no longer have any analysis
        conn.execute("DELETE FROM jobs WHERE id NOT IN (SELECT job_id FROM analyses)")
        conn.execute("DELETE FROM cvs WHERE id NOT IN (SELECT cv_id FROM analyses)")
    logger.info("Deleted %d analysis row(s)", len(ids))


def clear_all() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM analyses")
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM cvs")
    logger.info("All results cleared")
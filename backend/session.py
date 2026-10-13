"""Public deployment support: one private database per visitor.

Public mode is switched on by a secret or environment variable:
    PUBLIC_MODE = "true"
Without it (for example on your own computer) nothing changes: results are kept in
jobs.db and the API key comes from your .env file.
"""

import os
import tempfile
import time
import uuid
from pathlib import Path

import streamlit as st

import database as db

SESSION_DIR = Path(tempfile.gettempdir()) / "jobmatcher_sessions"
MAX_AGE_SECONDS = 24 * 3600  # session databases older than this are deleted


def is_public_mode() -> bool:
    try:
        value = st.secrets["PUBLIC_MODE"]
    except Exception:  # no secret configured
        value = os.getenv("PUBLIC_MODE", "")
    return str(value).strip().lower() in {"1", "true", "yes"}


def _cleanup_old_sessions() -> None:
    try:
        cutoff = time.time() - MAX_AGE_SECONDS
        for path in SESSION_DIR.glob("*.db"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
    except OSError:
        pass  # cleanup is best effort


def init_session() -> None:
    """In public mode, give this visitor a private database. Call at the top of every page."""
    if not is_public_mode():
        return
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    if "sid" not in st.session_state:
        st.session_state["sid"] = uuid.uuid4().hex
        _cleanup_old_sessions()
    db.use_db(SESSION_DIR / f"{st.session_state['sid']}.db")
    db.init_db()

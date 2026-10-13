import logging
import tempfile
from pathlib import Path

import streamlit as st

import database as db
from config import (
    ALLOWED_TYPES,
    EXPERIENCE_MAX,
    LOG_FILE,
    MAX_FILE_MB,
    MAX_JOBS_PER_RUN,
    SKILLS_MAX,
    TOOLS_MAX,
)
from auth import require_password
from job_agent import analyze

MAX_BYTES = MAX_FILE_MB * 1024 * 1024
RECOMMENDATIONS = ["APPLY", "APPLY WITH RESUME CHANGES", "LOW MATCH"]
BADGE = {"APPLY": "🟢", "APPLY WITH RESUME CHANGES": "🟡", "LOW MATCH": "🔴"}
SHORT = {
    "APPLY": "🟢 Apply",
    "APPLY WITH RESUME CHANGES": "🟡 Apply + edits",
    "LOW MATCH": "🔴 Low match",
}
ALL_CVS = "All CVs"
TABLE_COLUMNS = [
    "Job", "CV", "Match Score", "Recommendation", "Matching Skills", "Missing Skills",
    "Unverified Skills", "Experience Gap", "Resume Suggestions", "Source File", "Analyzed At",
]

st.set_page_config(page_title="CV ↔ Job Matcher", page_icon="🎯", layout="wide")
require_password()


# ==============================
# Logging (set up once; file names only, never CV content)
# ==============================

@st.cache_resource
def setup_logging() -> None:
    log = logging.getLogger("jobmatcher")
    log.setLevel(logging.INFO)
    log.propagate = False
    if not log.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        for handler in (logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")):
            handler.setFormatter(fmt)
            log.addHandler(handler)


setup_logging()
logger = logging.getLogger("jobmatcher.app")
db.init_db()


# ==============================
# Helpers
# ==============================

def save_upload(uploaded_file, folder: str) -> Path:
    path = Path(folder) / uploaded_file.name
    path.write_bytes(uploaded_file.getbuffer())
    return path


def make_labels(frame) -> dict:
    """Readable label -> analysis id, for select boxes."""
    return {
        f"{r['Job']} · {r['CV']} (#{int(r['ID'])})": int(r["ID"])
        for _, r in frame.iterrows()
    }


# ==============================
# Sidebar: uploads
# ==============================

with st.sidebar:
    st.header("1. Upload")
    cv_upload = st.file_uploader("Your CV", type=ALLOWED_TYPES)
    jd_uploads = st.file_uploader(
        "Job descriptions",
        type=ALLOWED_TYPES,
        accept_multiple_files=True,
    )
    jd_uploads = jd_uploads or []

    force = st.checkbox(
        "Re-analyze even if already done",
        help="By default, a CV + job pair that was analyzed before is reused without calling Gemini.",
    )

    # Input safety checks
    too_big = [f.name for f in ([cv_upload] if cv_upload else []) + jd_uploads if f.size > MAX_BYTES]
    too_many = len(jd_uploads) > MAX_JOBS_PER_RUN

    if too_big:
        st.error(f"Over {MAX_FILE_MB} MB: {', '.join(too_big)}")
    if too_many:
        st.error(f"Please upload at most {MAX_JOBS_PER_RUN} job descriptions at a time.")

    ready = bool(cv_upload and jd_uploads) and not too_big and not too_many
    run = st.button("Analyze", type="primary", width="stretch", disabled=not ready)

    st.divider()
    st.header("Manage results")
    if st.button("Clear all results", width="stretch"):
        db.clear_all()
        st.rerun()


# ==============================
# Run analysis
# ==============================

st.title("🎯 CV ↔ Job Matcher")
st.caption("Upload your CV and one or more job descriptions to see how well you match.")

if run:
    errors = []
    reused = 0
    progress = st.progress(0.0, text="Starting...")
    with tempfile.TemporaryDirectory() as tmp:
        cv_path = save_upload(cv_upload, tmp)
        total = len(jd_uploads)
        for i, jd in enumerate(jd_uploads, start=1):
            progress.progress((i - 1) / total, text=f"Analyzing {jd.name} ({i}/{total})")
            try:
                jd_path = save_upload(jd, tmp)
                row = analyze(jd_path, cv_path, force=force)
                reused += int(row["cached"])
            except Exception as e:
                logger.error("Failed on %s: %s", jd.name, e)
                errors.append(f"{jd.name}: {e}")
    progress.progress(1.0, text="Done")

    if errors:
        st.error("Some files failed:\n\n" + "\n\n".join(f"- {e}" for e in errors))
    else:
        note = f" ({reused} reused from earlier runs)" if reused else ""
        st.success(f"Analyzed {total} job description(s){note}.")


# ==============================
# Results
# ==============================

df = db.load_results()

if df.empty:
    st.info("No results yet. Upload a CV and job descriptions in the sidebar, then click **Analyze**.")
    st.stop()

# --- Filters ---
if "recs" not in st.session_state:
    st.session_state.recs = RECOMMENDATIONS
    st.session_state.min_score = 0
    st.session_state.query = ""
    st.session_state.cv_filter = ALL_CVS

cv_options = [ALL_CVS] + sorted(df["CV"].unique().tolist())
if st.session_state.cv_filter not in cv_options:
    st.session_state.cv_filter = ALL_CVS


def reset_filters():
    st.session_state.recs = RECOMMENDATIONS
    st.session_state.min_score = 0
    st.session_state.query = ""
    st.session_state.cv_filter = ALL_CVS


f1, f2, f3, f4, f5 = st.columns([3, 3, 3, 2.5, 1.2], vertical_alignment="bottom")
with f1:
    selected_recs = st.multiselect(
        "Recommendation", RECOMMENDATIONS, key="recs", format_func=SHORT.get
    )
with f2:
    min_score = st.slider("Minimum match score", 0, 100, key="min_score")
with f3:
    query = st.text_input("Search", placeholder="Job name or skill, e.g. SQL", key="query")
with f4:
    cv_choice = st.selectbox("CV", cv_options, key="cv_filter")
with f5:
    st.button("Reset", on_click=reset_filters, width="stretch")

filtered = df[df["Recommendation"].isin(selected_recs) & (df["Match Score"] >= min_score)]

if cv_choice != ALL_CVS:
    filtered = filtered[filtered["CV"] == cv_choice]

if query:
    q = query.lower()
    haystack = filtered[["Job", "Matching Skills", "Missing Skills"]].astype(str).agg(" ".join, axis=1).str.lower()
    filtered = filtered[haystack.str.contains(q, regex=False)]

filtered = filtered.sort_values("Match Score", ascending=False)

# --- Summary metrics ---
m1, m2, m3 = st.columns(3)
m1.metric("Results shown", f"{len(filtered)} / {len(df)}")
m2.metric("Average score", f"{filtered['Match Score'].mean():.0f}" if len(filtered) else "–")
m3.metric("Ready to apply", int((filtered["Recommendation"] == "APPLY").sum()))

# --- Table ---
display = filtered.copy()
display["Recommendation"] = display["Recommendation"].map(lambda r: f"{BADGE.get(r, '⚪')} {r}")

st.dataframe(
    display,
    width="stretch",
    hide_index=True,
    column_order=TABLE_COLUMNS,
    column_config={
        "Match Score": st.column_config.ProgressColumn(
            "Match Score", min_value=0, max_value=100, format="%d"
        ),
    },
)

# --- Export ---
st.download_button(
    "⬇️ Export filtered results (CSV)",
    data=filtered.drop(columns=["ID"]).to_csv(index=False).encode("utf-8-sig"),
    file_name="job_matches.csv",
    mime="text/csv",
)

# --- CV comparison (only when more than one CV version exists) ---
if df["CV"].nunique() > 1:
    with st.expander("Compare CV versions"):
        st.caption("Match score for each job, side by side. A blank cell means that CV wasn't run against that job.")
        pivot = df.pivot_table(index="Job", columns="CV", values="Match Score", aggfunc="mean")
        st.dataframe(pivot.round(0), width="stretch")
        st.markdown("**Average score per CV (over the jobs it was run against):**")
        st.dataframe(
            df.groupby("CV")["Match Score"].agg(Jobs="count", Average="mean").round(1),
            width="stretch",
        )

# --- Detail view + delete ---
st.divider()
if len(filtered):
    left, right = st.columns([3, 2])

    with left:
        st.subheader("Details")
        labels = make_labels(filtered)
        choice = st.selectbox("Select a result", list(labels))
        row = filtered[filtered["ID"] == labels[choice]].iloc[0]
        st.markdown(f"**Score:** {row['Match Score']:.0f}  |  **Recommendation:** {BADGE.get(row['Recommendation'], '⚪')} {row['Recommendation']}")
        st.markdown(
            f"**Score breakdown:** Skills {row['Skills Score']:.0f}/{SKILLS_MAX} | "
            f"Experience {row['Experience Score']:.0f}/{EXPERIENCE_MAX} | "
            f"Tools {row['Tools Score']:.0f}/{TOOLS_MAX}"
        )
        st.markdown(f"**CV:** {row['CV']}")
        st.markdown(f"**Matching skills:** {row['Matching Skills']}")
        if row["Unverified Skills"]:
            st.markdown(f"**Unverified (not found in CV text):** {row['Unverified Skills']}")
        st.markdown(f"**Missing skills:** {row['Missing Skills']}")
        st.markdown(f"**Experience gap:** {row['Experience Gap']}")
        st.markdown(f"**Resume suggestions:** {row['Resume Suggestions']}")

    with right:
        st.subheader("Delete")
        delete_labels = make_labels(df)
        to_delete = st.multiselect("Results to delete", list(delete_labels))
        if st.button("Delete selected", disabled=not to_delete):
            db.delete_analyses([delete_labels[label] for label in to_delete])
            st.rerun()
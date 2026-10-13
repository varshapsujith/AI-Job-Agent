from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

import database as db
from auth import require_password

st.set_page_config(page_title="Application Tracker", page_icon="📌", layout="wide")
require_password()
db.init_db()

ICONS = {"Saved": "📌", "Applied": "📨", "Interview": "🗣️", "Offer": "🎉", "Rejected": "❌"}

st.title("📌 Application Tracker")

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

tracker = db.load_tracker()
if tracker.empty:
    st.info("No results yet. Go to the main page, upload a CV and job descriptions, and click **Analyze**.")
    st.stop()

# ==============================
# Update a status
# ==============================

st.subheader("Update a status")
labels = {
    f"{r['Job']} · {r['CV']} (#{int(r['ID'])})": int(r["ID"])
    for _, r in tracker.iterrows()
}

with st.form("update_status", clear_on_submit=True):
    c1, c2, c3 = st.columns([3, 2, 2])
    choice = c1.selectbox("Job", list(labels))
    status = c2.selectbox("New status", db.STATUSES, index=1)
    when = c3.date_input("Date", value=date.today())
    note = st.text_input("Note (optional)", placeholder="e.g. recruiter call on Friday")
    submitted = st.form_submit_button("Save update", type="primary")

if submitted:
    db.add_event(labels[choice], status, when.isoformat(), note)
    st.session_state["flash"] = f"{choice}: {ICONS[status]} {status} on {when:%d %b %Y}"
    st.rerun()

# ==============================
# Pipeline
# ==============================

st.subheader("Pipeline")
current = tracker["Status"].value_counts()
cols = st.columns(len(db.STATUSES))
for col, s in zip(cols, db.STATUSES):
    col.metric(f"{ICONS[s]} {s}", int(current.get(s, 0)))

selected = st.multiselect(
    "Show statuses", db.STATUSES, default=db.STATUSES, format_func=lambda s: f"{ICONS[s]} {s}"
)
view = tracker[tracker["Status"].isin(selected)].copy()
view["Status"] = view["Status"].map(lambda s: f"{ICONS[s]} {s}")

st.dataframe(
    view,
    width="stretch",
    hide_index=True,
    column_order=["Job", "CV", "Match Score", "Recommendation", "Status", "Status Date", "Note"],
    column_config={
        "Match Score": st.column_config.ProgressColumn(
            "Match Score", min_value=0, max_value=100, format="%d"
        ),
    },
)

# ==============================
# Funnel and activity
# ==============================

st.subheader("Funnel and activity")
funnel = db.funnel_counts()
events = db.load_events()

f1, f2 = st.columns(2)

with f1:
    st.markdown("**Funnel** (jobs that reached each stage)")
    funnel_df = pd.DataFrame({"Stage": list(funnel), "Jobs": list(funnel.values())})
    base = alt.Chart(funnel_df).encode(
        y=alt.Y("Stage:N", sort=list(funnel), title=None),
        x=alt.X("Jobs:Q", title="Jobs", axis=alt.Axis(tickMinStep=1)),
    )
    st.altair_chart(
        (base.mark_bar(color="#1565c0") + base.mark_text(align="left", dx=4).encode(text="Jobs:Q"))
        .properties(height=220),
        width="stretch",
    )

with f2:
    st.markdown("**Updates per week**")
    if events.empty:
        st.write("No updates yet. Save a status change above to see activity here.")
    else:
        ev = events.copy()
        ev["Week"] = pd.to_datetime(ev["Date"]).dt.to_period("W").dt.start_time
        st.altair_chart(
            alt.Chart(ev)
            .mark_bar()
            .encode(
                x=alt.X("Week:T", title="Week starting"),
                y=alt.Y("count():Q", title="Updates", axis=alt.Axis(tickMinStep=1)),
                color=alt.Color("Status:N", sort=db.STATUSES),
                tooltip=["Week:T", "Status:N", "count():Q"],
            )
            .properties(height=220),
            width="stretch",
        )

applied, interviews = funnel["Applied"], funnel["Interview"]
rejected = int(current.get("Rejected", 0))
if applied:
    st.info(
        f"**{interviews} of {applied}** applications reached an interview "
        f"({interviews / applied:.0%}). Rejections so far: {rejected}."
    )
else:
    st.caption("Mark jobs as Applied to see your funnel conversion.")

# ==============================
# Does a higher score lead to progress?
# ==============================

st.subheader("Does a higher match score lead to progress?")
by_status = (
    tracker.groupby("Status")
    .agg(Jobs=("ID", "count"), **{"Average match score": ("Match Score", "mean")})
    .reindex(db.STATUSES)
    .dropna()
)
by_status["Average match score"] = by_status["Average match score"].round(0)
st.dataframe(by_status, width="stretch")
st.caption("Small samples mislead. Treat this as a pattern to watch, not a conclusion, until you have 20+ applications.")

# ==============================
# History and corrections
# ==============================

with st.expander("Update history"):
    if events.empty:
        st.write("No updates yet.")
    else:
        st.dataframe(
            events.sort_values(["Date", "Event ID"], ascending=False),
            width="stretch",
            hide_index=True,
            column_order=["Date", "Job", "CV", "Status", "Note", "Event ID"],
        )
        event_labels = {
            f"#{int(r['Event ID'])} · {r['Job']} · {r['Status']} · {r['Date']}": int(r["Event ID"])
            for _, r in events.sort_values("Event ID", ascending=False).iterrows()
        }
        to_remove = st.selectbox("Remove a mistaken update", list(event_labels))
        if st.button("Delete this update"):
            db.delete_event(event_labels[to_remove])
            st.rerun()
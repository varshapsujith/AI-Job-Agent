import altair as alt
import pandas as pd
import streamlit as st

import database as db
from auth import require_password
from session import init_session
from config import EXPERIENCE_MAX, SKILLS_MAX, TOOLS_MAX

st.set_page_config(page_title="Analytics", page_icon="📊", layout="wide")
require_password()
init_session()
db.init_db()

ALL_CVS = "All CVs"
REC_ORDER = ["APPLY", "APPLY WITH RESUME CHANGES", "LOW MATCH"]
REC_COLORS = ["#2e7d32", "#f9a825", "#c62828"]
MIN_SAMPLE = 5

st.title("📊 Analytics")

df = db.load_results()
if df.empty:
    st.info("No results yet. Go to the main page, upload a CV and job descriptions, and click **Analyze**.")
    st.stop()

# --- Scope: all CVs or one CV version ---
cv_choice = st.selectbox("CV", [ALL_CVS] + sorted(df["CV"].unique().tolist()))
cv_arg = None if cv_choice == ALL_CVS else cv_choice
scope = df if cv_arg is None else df[df["CV"] == cv_arg]
n = len(scope)

if n < MIN_SAMPLE:
    st.caption(f"Based on only {n} analysis(es). The charts become much more meaningful with {MIN_SAMPLE}+ jobs.")

# --- KPIs ---
rate = db.verification_rate(cv_arg)
k1, k2, k3, k4 = st.columns(4)
k1.metric("Analyses", n)
k2.metric("Average score", f"{scope['Match Score'].mean():.0f}")
k3.metric("Ready to apply", f"{(scope['Recommendation'] == 'APPLY').sum()} / {n}")
k4.metric(
    "Skills confirmed in CV text",
    f"{rate:.0%}" if rate is not None else "–",
    help="Share of skills the model listed as matching that were actually found in the CV text.",
)

# --- Skill gaps and strengths ---
st.subheader("Skills")
top_n = st.slider("Skills to show", 5, 15, 10)
missing = db.skill_counts("missing", cv_arg, top_n)
matching = db.skill_counts("matching", cv_arg, top_n)


def skill_chart(data: pd.DataFrame, color: str, x_title: str):
    chart = (
        alt.Chart(data)
        .mark_bar(color=color)
        .encode(
            x=alt.X("jobs:Q", title=x_title, axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("skill:N", sort="-x", title=None),
            tooltip=[alt.Tooltip("skill:N", title="Skill"), alt.Tooltip("jobs:Q", title="Jobs")],
        )
        .properties(height=max(120, 28 * len(data)))
    )
    st.altair_chart(chart, width="stretch")


c1, c2 = st.columns(2)
with c1:
    st.markdown("**Top missing skills** (what to learn first)")
    if missing.empty:
        st.write("No missing skills recorded.")
    else:
        skill_chart(missing, "#c62828", f"Jobs asking for it (of {n})")
with c2:
    st.markdown("**Top matching skills** (your strengths)")
    if matching.empty:
        st.write("No matching skills recorded.")
    else:
        skill_chart(matching, "#2e7d32", f"Jobs where it matched (of {n})")

if not missing.empty:
    top = missing.iloc[0]
    st.info(f"Most common gap: **{top['skill']}**, missing in {int(top['jobs'])} of {n} analyses.")

# --- Score distribution and components ---
st.subheader("Scores")
d1, d2 = st.columns(2)

with d1:
    st.markdown("**Score distribution**")
    hist = (
        alt.Chart(scope)
        .mark_bar()
        .encode(
            x=alt.X("Match Score:Q", bin=alt.Bin(step=10, extent=[0, 100]), title="Match score"),
            y=alt.Y("count():Q", title="Jobs", axis=alt.Axis(tickMinStep=1)),
            color=alt.Color(
                "Recommendation:N",
                scale=alt.Scale(domain=REC_ORDER, range=REC_COLORS),
                legend=alt.Legend(title=None, orient="bottom"),
            ),
            tooltip=["count():Q", "Recommendation:N"],
        )
        .properties(height=300)
    )
    st.altair_chart(hist, width="stretch")

with d2:
    st.markdown("**Where you score lowest** (average, % of available points)")
    components = pd.DataFrame(
        {
            "Component": ["Skills", "Experience", "Tools"],
            "Percent": [
                scope["Skills Score"].mean() / SKILLS_MAX * 100,
                scope["Experience Score"].mean() / EXPERIENCE_MAX * 100,
                scope["Tools Score"].mean() / TOOLS_MAX * 100,
            ],
        }
    )
    bars = (
        alt.Chart(components)
        .mark_bar(color="#1565c0")
        .encode(
            x=alt.X("Percent:Q", scale=alt.Scale(domain=[0, 100]), title="% of available points"),
            y=alt.Y("Component:N", sort="-x", title=None),
            tooltip=[alt.Tooltip("Percent:Q", format=".0f")],
        )
        .properties(height=300)
    )
    st.altair_chart(bars, width="stretch")

weakest = components.sort_values("Percent").iloc[0]
st.info(f"Weakest area: **{weakest['Component']}** ({weakest['Percent']:.0f}% of available points on average).")

# --- Best and worst matches ---
st.subheader("Best and weakest matches")
cols = ["Job", "CV", "Match Score", "Recommendation"]
b1, b2 = st.columns(2)
with b1:
    st.markdown("**Top 5**")
    st.dataframe(scope.nlargest(5, "Match Score")[cols], hide_index=True, width="stretch")
with b2:
    st.markdown("**Bottom 5**")
    st.dataframe(scope.nsmallest(5, "Match Score")[cols], hide_index=True, width="stretch")
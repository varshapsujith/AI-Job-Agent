"""Simple password gate.

The password comes from the APP_PASSWORD secret, never from the code:
  - on Streamlit Community Cloud: App settings -> Secrets
  - locally: .streamlit/secrets.toml  (optional)
If no APP_PASSWORD is set, the app opens without a password, which is what you want
when running it on your own computer.
"""

import hmac

import streamlit as st

MAX_ATTEMPTS = 5


def require_password() -> None:
    try:
        expected = str(st.secrets["APP_PASSWORD"])
    except Exception:  # no secret configured -> local use, no gate
        return

    if st.session_state.get("authenticated"):
        return

    st.title("🔒 CV ↔ Job Matcher")
    attempts = st.session_state.get("login_attempts", 0)

    if attempts >= MAX_ATTEMPTS:
        st.error("Too many wrong attempts. Reload the page to try again.")
        st.stop()

    with st.form("login"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Enter")

    if submitted:
        if hmac.compare_digest(password.encode("utf-8"), expected.encode("utf-8")):
            st.session_state["authenticated"] = True
            st.session_state["login_attempts"] = 0
            st.rerun()
        st.session_state["login_attempts"] = attempts + 1
        st.error("Wrong password.")

    st.stop()

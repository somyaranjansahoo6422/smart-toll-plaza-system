import streamlit as st

from auth import ROLE_DIRECTOR, attempt_login, init_session_state, logout
from database import get_dynamic_pricing

st.set_page_config(page_title="NHAI Command Center", page_icon="🛣️", layout="wide")

init_session_state()


def login_page():
    st.title("NHAI national infrastructure gateway")
    st.caption("Awaiting secure node authentication.")

    _, center, _ = st.columns([1, 1.3, 1])
    with center:
        with st.form("auth_form"):
            st.subheader(":material/shield_person: Identity & access")
            username = st.text_input("Enterprise ID (email)", placeholder="e.g. director@nhai.gov")
            token = st.text_input("Authentication token", type="password")
            submitted = st.form_submit_button(
                "Authenticate securely", type="primary", icon=":material/login:", width="stretch"
            )

        if submitted:
            success, error = attempt_login(username, token)
            if success:
                st.rerun()
            st.error(error, icon=":material/gpp_bad:")

        # Helpful hint for local testing; remove for any shared deployment.
        with st.expander("Demo accounts"):
            st.markdown(
                "| Role | ID | Token |\n|---|---|---|\n"
                "| Regional Director | `director@nhai.gov` | `admin88` |\n"
                "| Plaza Operator | `op1@manguli.nhai` | `toll2026` |"
            )


# --- DASHBOARD ROUTING ---
if st.session_state.user_role is None:
    page = st.navigation([st.Page(login_page, title="Sign in", icon=":material/login:")])
else:
    home = st.Page("app_pages/home.py", title="Operations overview", icon=":material/dashboard:", default=True)
    camera = st.Page("app_pages/live_camera.py", title="Live camera", icon=":material/photo_camera:")
    gis = st.Page("app_pages/gis_command_center.py", title="GIS command center", icon=":material/map:")
    analytics = st.Page("app_pages/analytics.py", title="Analytics", icon=":material/monitoring:")

    if st.session_state.user_role == ROLE_DIRECTOR:
        pages = [home, gis, camera, analytics]
    else:
        pages = [home, camera, analytics]
    page = st.navigation({"Operations": pages})

    with st.sidebar:
        with st.container(border=True):
            st.markdown(f":material/verified_user: **{st.session_state.user_role}**")
            st.caption(f":material/location_on: {st.session_state.user_node}")
            st.caption(f":material/person: {st.session_state.username}")
        _, is_surge, multiplier = get_dynamic_pricing()
        if is_surge:
            st.warning(f"Congestion pricing active (×{multiplier:g})", icon=":material/bolt:")
        st.button("Terminate session", icon=":material/logout:", on_click=logout, width="stretch")

page.run()

import streamlit as st

st.set_page_config(page_title="NHAI Command Center", page_icon="🛣️", layout="wide")

st.markdown("""
    <style>
        .block-container { padding-top: 2rem; }
        .enterprise-header { font-size: 2.5rem; font-weight: 700; color: #FFFFFF; text-align: center; border-bottom: 2px solid #333; padding-bottom: 20px; margin-bottom: 30px;}
    </style>
""", unsafe_allow_html=True)

# --- ENTERPRISE IAM (Identity & Access Management) ---
# In production, this maps to Auth0 or AWS Cognito JWT tokens.
RBAC_USERS = {
    "director@nhai.gov": {"pin": "admin88", "role": "Regional Director", "node": "All Nodes (Pan-India)"},
    "op1@manguli.nhai": {"pin": "toll2026", "role": "Plaza Operator", "node": "Manguli Toll Plaza (Cuttack)"}
}

if 'user_role' not in st.session_state: st.session_state.user_role = None
if 'user_node' not in st.session_state: st.session_state.user_node = None
if 'username' not in st.session_state: st.session_state.username = None

st.sidebar.markdown("### 🔐 Identity & Access")

if st.session_state.user_role is None:
    with st.sidebar.form("auth_form"):
        username = st.text_input("Enterprise ID (Email)", placeholder="e.g. director@nhai.gov")
        password = st.text_input("Authentication Token", type="password")
        submit = st.form_submit_button("Authenticate Securely", use_container_width=True)
        
        if submit:
            if username in RBAC_USERS and RBAC_USERS[username]["pin"] == password:
                st.session_state.user_role = RBAC_USERS[username]["role"]
                st.session_state.user_node = RBAC_USERS[username]["node"]
                st.session_state.username = username
                st.rerun()
            else:
                st.error("Authentication Failed: Invalid ID or Token.")
    
    # Helpful hint for your testing purposes
    st.sidebar.info("**Demo Accounts:**\n\nDirector: `director@nhai.gov` / `admin88`\n\nOperator: `op1@manguli.nhai` / `toll2026`")
else:
    st.sidebar.success(f"✅ **{st.session_state.user_role}**")
    st.sidebar.info(f"📍 {st.session_state.user_node}")
    if st.sidebar.button("🔒 Terminate Session", use_container_width=True):
        st.session_state.user_role = None
        st.session_state.user_node = None
        st.session_state.username = None
        st.rerun()

# --- DASHBOARD ROUTING ---
if st.session_state.user_role is None:
    st.markdown("<div class='enterprise-header'>NHAI National Infrastructure Gateway</div>", unsafe_allow_html=True)
    st.warning("Awaiting Secure Node Authentication... Please log in via the IAM sidebar.")
else:
    st.markdown("<div class='enterprise-header'>🛣️ Central Infrastructure Operations</div>", unsafe_allow_html=True)
    
    if st.session_state.user_role == "Regional Director":
        st.success("Welcome, Director. All geographic and financial telemetry subsystems are online. Navigate to GIS Command Center or Analytics.")
    elif st.session_state.user_role == "Plaza Operator":
        st.success("Welcome, Operator. ANPR Vision Models are online. Navigate to Live Camera for processing.")

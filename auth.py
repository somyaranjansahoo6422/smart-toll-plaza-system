import hashlib
import hmac
import time

import streamlit as st

# --- ENTERPRISE IAM (Identity & Access Management) ---
# In production, this maps to Auth0 or AWS Cognito JWT tokens.
ROLE_DIRECTOR = "Regional Director"
ROLE_OPERATOR = "Plaza Operator"
ALL_ROLES = (ROLE_DIRECTOR, ROLE_OPERATOR)

PBKDF2_ITERATIONS = 200_000
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60

# Only salted PBKDF2 hashes are stored, never the tokens themselves.
# Override these by adding a [users.<email>] table to .streamlit/secrets.toml
# (same keys: salt, hash, role, node). Use hash_token() to generate a hash.
DEFAULT_USERS = {
    "director@nhai.gov": {
        "salt": "2d25438b750593506f20cc8e1ee5cbd9",
        "hash": "d56a6db462d297a8ae5a441391d7e909ba57359ce0f360c47aef638ab204d872",
        "role": ROLE_DIRECTOR,
        "node": "All Nodes (Pan-India)",
    },
    "op1@manguli.nhai": {
        "salt": "0022d54c601ae72f5cf64d82a7231697",
        "hash": "c169b9573805bf088077846b865d75eb260e1dadb944bd3e3458383ff7a294ec",
        "role": ROLE_OPERATOR,
        "node": "Manguli Toll Plaza (Cuttack)",
    },
}


def hash_token(token, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", token.encode(), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS).hex()


def _load_users():
    try:
        users = st.secrets.get("users")
    except Exception:
        # No secrets.toml present: fall back to the built-in demo accounts.
        users = None
    if users:
        return {email.strip().lower(): dict(user) for email, user in users.items()}
    return DEFAULT_USERS


def authenticate(username, token):
    """Returns the user record for valid credentials, else None."""
    user = _load_users().get(username.strip().lower())
    # Hash even for unknown users so response time doesn't reveal valid IDs.
    salt = user["salt"] if user else "00" * 16
    candidate = hash_token(token, salt)
    if user and hmac.compare_digest(candidate, user["hash"]):
        return user
    return None


def init_session_state():
    st.session_state.setdefault("user_role", None)
    st.session_state.setdefault("user_node", None)
    st.session_state.setdefault("username", None)
    st.session_state.setdefault("failed_logins", 0)
    st.session_state.setdefault("lockout_until", 0.0)


def lockout_remaining():
    return max(0, int(st.session_state.lockout_until - time.time()))


def attempt_login(username, token):
    """Returns (success, error_message)."""
    if lockout_remaining():
        return False, f"Too many failed attempts. Try again in {lockout_remaining()}s."

    user = authenticate(username, token)
    if user is None:
        st.session_state.failed_logins += 1
        if st.session_state.failed_logins >= MAX_FAILED_ATTEMPTS:
            st.session_state.failed_logins = 0
            st.session_state.lockout_until = time.time() + LOCKOUT_SECONDS
            return False, f"Too many failed attempts. Terminal locked for {LOCKOUT_SECONDS}s."
        return False, "Authentication failed: invalid ID or token."

    st.session_state.failed_logins = 0
    st.session_state.user_role = user["role"]
    st.session_state.user_node = user["node"]
    st.session_state.username = username.strip().lower()
    return True, None


def logout():
    # Clear everything so plates, queues and flash messages don't leak to the next user.
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    init_session_state()


def require_role(*roles):
    """Defense in depth: pages are already hidden by st.navigation, but refuse to render anyway."""
    if st.session_state.get("user_role") not in roles:
        st.error("RBAC alert: insufficient clearance. Terminal locked.", icon=":material/lock:")
        st.stop()

import pandas as pd
import streamlit as st

from auth import ALL_ROLES, ROLE_DIRECTOR, require_role
from database import BASE_TOLL_RATES, cloud_db, get_dynamic_pricing

require_role(*ALL_ROLES)

is_director = st.session_state.user_role == ROLE_DIRECTOR
# Directors see every plaza; operators only their own.
plaza_scope = None if is_director else st.session_state.user_node

st.title("Central infrastructure operations")
if is_director:
    st.success(
        "Welcome, Director. All geographic and financial telemetry subsystems are online. "
        "Use the GIS command center or Analytics from the sidebar.",
        icon=":material/check_circle:",
    )
else:
    st.success(
        "Welcome, Operator. ANPR vision models are online. Open Live camera to process vehicles.",
        icon=":material/check_circle:",
    )

txn_df = cloud_db.get_dataframe("transactions", plaza_scope)
audit_df = cloud_db.get_dataframe("security_audit", plaza_scope)
today = pd.Timestamp.now().normalize()
txn_today = txn_df[txn_df["Timestamp"] >= today]
audit_today = audit_df[audit_df["Timestamp"] >= today]
fastag_share = (txn_df["Payment_Method"] == "FASTag").mean() * 100 if not txn_df.empty else 0.0

st.subheader("Today at a glance")
with st.container(horizontal=True):
    st.metric("Revenue today", f"₹{txn_today['Toll_Collected'].sum():,.2f}", border=True)
    st.metric("Transits today", f"{len(txn_today):,}", border=True)
    st.metric("Security events today", f"{len(audit_today):,}", border=True)
    st.metric("FASTag adoption (all time)", f"{fastag_share:.0f}%", border=True)

left, right = st.columns([1, 1.4])
with left:
    st.subheader("Current tariff card")
    rates, is_surge, multiplier = get_dynamic_pricing()
    tariff_df = pd.DataFrame({
        "Vehicle class": list(BASE_TOLL_RATES),
        "Base toll": list(BASE_TOLL_RATES.values()),
        "Current toll": list(rates.values()),
    })
    st.dataframe(
        tariff_df,
        hide_index=True,
        column_config={
            "Base toll": st.column_config.NumberColumn(format="₹%.2f"),
            "Current toll": st.column_config.NumberColumn(format="₹%.2f"),
        },
    )
    if is_surge:
        st.caption(f":material/bolt: Peak-hour surcharge of ×{multiplier:g} is applied right now.")
    else:
        st.caption("Off-peak: base tariff applies. Surge windows are 08:00–10:00 and 17:00–20:00.")

with right:
    st.subheader("Recent transactions")
    if txn_df.empty:
        st.info("No transactions recorded yet.", icon=":material/info:")
    else:
        st.dataframe(
            txn_df.head(8)[["Timestamp", "Plate_Number", "Vehicle_Type", "Payment_Method", "Toll_Collected"]],
            hide_index=True,
            column_config={"Toll_Collected": st.column_config.NumberColumn("Toll", format="₹%.2f")},
        )

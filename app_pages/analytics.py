import plotly.express as px
import streamlit as st

from auth import ALL_ROLES, ROLE_DIRECTOR, ROLE_OPERATOR, require_role
from database import cloud_db, get_dynamic_pricing

require_role(*ALL_ROLES)

is_director = st.session_state.user_role == ROLE_DIRECTOR
plaza = st.session_state.user_node
# Operators are scoped to their own plaza; directors see the whole network.
plaza_scope = None if is_director else plaza

PAYMENT_COLORS = {"FASTag": "#0080FF", "Cash": "#00C46A", "Exempt": "#FF8C00"}
MONEY = st.column_config.NumberColumn(format="₹%.2f")

st.title("System intelligence & finance oversight")

txn_df = cloud_db.get_dataframe("transactions", plaza_scope)
audit_df = cloud_db.get_dataframe("security_audit", plaza_scope)
_, is_surge, multiplier = get_dynamic_pricing()

if is_director:
    known_plazas = sorted(set(txn_df["Plaza_Location"].dropna()) | set(audit_df["Handled_By_Plaza"].dropna()))
    selected_plazas = st.multiselect("Plaza filter", known_plazas, placeholder="All plazas")
    if selected_plazas:
        txn_df = txn_df[txn_df["Plaza_Location"].isin(selected_plazas)]
        audit_df = audit_df[audit_df["Handled_By_Plaza"].isin(selected_plazas)]
else:
    st.caption(f":material/location_on: Showing data for {plaza}")

tab_names = ["Financial ledger", "Security audit"]
tab_names.append("Shift reports" if is_director else "Shift closure")
ledger_tab, audit_tab, shift_tab = st.tabs(tab_names)

# --- TAB 1: FINANCIAL LEDGER & CHARTS ---
with ledger_tab:
    if is_surge:
        st.warning(f"**Congestion pricing active:** tolls currently at {multiplier:g}× baseline.", icon=":material/bolt:")

    if txn_df.empty:
        st.info("No transaction telemetry recorded yet.", icon=":material/info:")
    else:
        fastag_share = (txn_df["Payment_Method"] == "FASTag").mean() * 100
        exempt_count = int(txn_df["Is_Exempt"].eq(True).sum())
        with st.container(horizontal=True):
            st.metric("Total gross revenue", f"₹{txn_df['Toll_Collected'].sum():,.2f}", border=True)
            st.metric("Total valid transits", f"{len(txn_df):,}", border=True)
            st.metric("FASTag adoption", f"{fastag_share:.0f}%", border=True)
            st.metric("Exempt passages", f"{exempt_count:,}", border=True)

        # --- ENTERPRISE CHARTS ---
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            with st.container(border=True):
                st.markdown("**Adoption: FASTag vs cash**")
                pie_data = txn_df["Payment_Method"].value_counts().reset_index()
                fig_pie = px.pie(
                    pie_data, names="Payment_Method", values="count", hole=0.5,
                    color="Payment_Method", color_discrete_map=PAYMENT_COLORS,
                )
                fig_pie.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=320)
                st.plotly_chart(fig_pie)

        with col_c2:
            with st.container(border=True):
                st.markdown("**Revenue by vehicle class**")
                rev_data = txn_df.groupby("Vehicle_Type", as_index=False)["Toll_Collected"].sum()
                st.bar_chart(
                    rev_data, x="Vehicle_Type", y="Toll_Collected",
                    x_label="Vehicle class", y_label="Revenue (₹)", height=320,
                )

        with st.container(border=True):
            st.markdown("**Daily revenue trend**")
            daily = (
                txn_df.assign(Date=txn_df["Timestamp"].dt.date)
                .groupby(["Date", "Payment_Method"], as_index=False)["Toll_Collected"].sum()
            )
            st.bar_chart(daily, x="Date", y="Toll_Collected", color="Payment_Method", y_label="Revenue (₹)", height=280)

        st.markdown("#### Master ledger")
        st.dataframe(txn_df, hide_index=True, column_config={"Toll_Collected": MONEY})
        st.download_button(
            "Download ledger CSV",
            txn_df.to_csv(index=False).encode("utf-8"),
            file_name="toll_ledger.csv",
            mime="text/csv",
            icon=":material/download:",
            on_click="ignore",
        )

# --- TAB 2: SECURITY AUDIT ---
with audit_tab:
    if audit_df.empty:
        st.success("Clean record. No security events written to the database.", icon=":material/verified_user:")
    else:
        actions = audit_df["Action_Taken"].astype(str)
        with st.container(horizontal=True):
            st.metric("Blacklist interceptions", int(actions.str.startswith("Interception").sum()), border=True)
            st.metric("FASTag declines", int(actions.str.contains("Declined").sum()), border=True)
            st.metric("VIP exemptions", int(actions.str.contains("Exemption").sum()), border=True)
        st.dataframe(audit_df, hide_index=True)

# --- TAB 3: SHIFT CLOSURE (Operators) / SHIFT REPORTS (Directors) ---
with shift_tab:
    if st.session_state.user_role == ROLE_OPERATOR:
        st.markdown("### End of shift cash reconciliation")
        st.caption("Operators must declare physical cash register totals prior to session termination.")

        shift_result = st.session_state.pop("shift_result", None)
        if shift_result:
            kind, message = shift_result
            getattr(st, kind)(message)

        last_closure = cloud_db.last_shift_closure_time(plaza)
        cash_txns = txn_df[txn_df["Payment_Method"] == "Cash"]
        if last_closure is not None:
            cash_txns = cash_txns[cash_txns["Timestamp"] > last_closure]
        expected_cash = round(float(cash_txns["Toll_Collected"].sum()), 2)

        since = f"since last closure at {last_closure:%d %b %Y %H:%M}" if last_closure else "for this plaza (no previous closure)"
        st.info(
            f"System-calculated expected cash {since}: **₹{expected_cash:,.2f}** across {len(cash_txns)} cash transactions.",
            icon=":material/calculate:",
        )

        with st.form("shift_closure"):
            actual_cash = st.number_input("Actual physical cash counted (₹)", min_value=0.0, step=10.0, format="%.2f")
            submit_shift = st.form_submit_button("Submit shift closure & reconcile", type="primary", icon=":material/task_alt:")

        if submit_shift:
            variance = round(actual_cash - expected_cash, 2)
            cloud_db.close_shift(st.session_state.username, plaza, expected_cash, actual_cash, variance)
            if variance == 0:
                st.session_state.shift_result = ("success", "Shift reconciled perfectly. Variance: ₹0.00.")
            elif variance > 0:
                st.session_state.shift_result = ("warning", f"Positive variance (overage): +₹{variance:,.2f}. Logged for audit.")
            else:
                st.session_state.shift_result = ("error", f"Negative variance (shortage): ₹{variance:,.2f}. Flagged for review.")
            st.rerun()

        history = cloud_db.get_dataframe("shift_closures", plaza)
        if not history.empty:
            st.markdown("#### Shift history")
            st.dataframe(
                history, hide_index=True,
                column_config={"Expected_Cash": MONEY, "Actual_Cash": MONEY, "Variance": MONEY},
            )
    else:
        st.markdown("### Shift reconciliation reports")
        shifts_df = cloud_db.get_dataframe("shift_closures")
        if shifts_df.empty:
            st.info("No shift closures submitted yet.", icon=":material/info:")
        else:
            with st.container(horizontal=True):
                st.metric("Shift closures", f"{len(shifts_df):,}", border=True)
                st.metric("Net variance", f"₹{shifts_df['Variance'].sum():,.2f}", border=True)
                st.metric("Shifts with shortage", int((shifts_df["Variance"] < 0).sum()), border=True)
            st.dataframe(
                shifts_df, hide_index=True,
                column_config={"Expected_Cash": MONEY, "Actual_Cash": MONEY, "Variance": MONEY},
            )

        st.markdown("#### Data export")
        st.caption("Rebuild `toll_plaza_odisha.db` (SQLite) from the live JSON store for migration to a SQL engine.")
        if st.button("Export SQLite snapshot", icon=":material/database:"):
            counts = cloud_db.export_to_sqlite()
            st.success(
                "SQLite snapshot written: " + ", ".join(f"{table} ({n})" for table, n in counts.items()),
                icon=":material/check_circle:",
            )

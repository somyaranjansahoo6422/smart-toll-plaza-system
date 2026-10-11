import streamlit as st
import plotly.express as px
import pandas as pd
from database import cloud_db, get_dynamic_pricing

st.set_page_config(page_title="Financial Analytics", page_icon="📊", layout="wide")

if st.session_state.get('user_role') not in ["Plaza Operator", "Regional Director"]:
    st.error("🚨 Access Denied."); st.stop()

st.header(f"System Intelligence & Finance Oversight")

txn_df = cloud_db.get_dataframe("transactions")
_, is_surge, multiplier = get_dynamic_pricing()

tabs = ["💰 Financial Ledger", "🛡️ Security Audit"]
if st.session_state.user_role == "Plaza Operator":
    tabs.append("💼 Shift Closure")
    
tab_objs = st.tabs(tabs)

# --- TAB 1: FINANCIAL LEDGER & CHARTS ---
with tab_objs[0]:
    if is_surge: st.warning(f"⚡ **Congestion Pricing Active:** Tolls currently operating at {multiplier}x baseline.")
    
    if txn_df.empty:
        st.info("No transaction telemetry recorded yet.")
    else:
        kpi1, kpi2, kpi3 = st.columns(3)
        collected_rev = txn_df['Toll_Collected'].sum()
        kpi1.metric(label="Total Gross Revenue", value=f"₹{collected_rev:,.2f}")
        kpi2.metric(label="Total Valid Transits", value=f"{len(txn_df):,}")
        kpi3.metric(label="Active Node", value=st.session_state.user_node)
        st.markdown("---")
        
        # --- ENTERPRISE CHARTS ---
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            st.markdown("#### Adoption: FASTag vs Cash")
            pie_data = txn_df['Payment_Method'].value_counts().reset_index()
            fig_pie = px.pie(pie_data, names='Payment_Method', values='count', hole=0.5, 
                             color_discrete_sequence=['#0080FF', '#00FF7F', '#FF4B4B'])
            fig_pie.update_layout(margin=dict(t=20, b=20, l=20, r=20), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_pie, use_container_width=True)
            
        with col_c2:
            st.markdown("#### Revenue by Vehicle Class")
            rev_data = txn_df.groupby('Vehicle_Type')['Toll_Collected'].sum().reset_index()
            fig_bar = px.bar(rev_data, x='Vehicle_Type', y='Toll_Collected', text_auto='.2s', color='Toll_Collected', color_continuous_scale="Viridis")
            fig_bar.update_layout(margin=dict(t=20, b=20, l=20, r=20), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=False)
            st.plotly_chart(fig_bar, use_container_width=True)

        st.markdown("#### Master Ledger")
        st.dataframe(txn_df, hide_index=True, use_container_width=True)

# --- TAB 2: SECURITY AUDIT ---
with tab_objs[1]:
    audit_df = cloud_db.get_dataframe("security_audit")
    if audit_df.empty:
        st.success("✅ Clean Record. No security events written to database.")
    else:
        st.dataframe(audit_df, hide_index=True, use_container_width=True)

# --- TAB 3: SHIFT CLOSURE (Operators Only) ---
if st.session_state.user_role == "Plaza Operator":
    with tab_objs[2]:
        st.markdown("### 💼 End of Shift Cash Reconciliation")
        st.caption("Operators must declare physical cash register totals prior to session termination.")
        
        if not txn_df.empty:
            cash_txns = txn_df[(txn_df['Payment_Method'] == 'Cash') & (txn_df['Plaza_Location'] == st.session_state.user_node)]
            expected_cash = cash_txns['Toll_Collected'].sum()
        else:
            expected_cash = 0.0
            
        st.info(f"💻 System Calculated Expected Cash: **₹{expected_cash:,.2f}**")
        
        with st.form("shift_closure"):
            actual_cash = st.number_input("Enter Actual Physical Cash Counted (₹)", min_value=0.0, step=10.0)
            submit_shift = st.form_submit_button("Submit Shift Closure & Reconcile")
            
            if submit_shift:
                variance = actual_cash - expected_cash
                cloud_db.close_shift(st.session_state.username, st.session_state.user_node, expected_cash, actual_cash, variance)
                
                if variance == 0:
                    st.success("✅ Shift Reconciled Perfectly. Variance: ₹0.00.")
                elif variance > 0:
                    st.warning(f"⚠️ Positive Variance (Overage): +₹{variance:,.2f}. Logged for audit.")
                else:
                    st.error(f"🚨 Negative Variance (Shrinkage/Shortage): ₹{variance:,.2f}. Flagged for review.")

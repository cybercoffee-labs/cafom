"""CAFOM Streamlit Dashboard — Cybersecurity Asset Financial Operations Manager.

Interactive dashboard for tracking asset lifecycle, vendor health, renewal exposure,
and financial forecast of cybersecurity tools.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import streamlit as st
import pandas as pd
from datetime import date

import anomaly_detector
import asset_tracker
import financial_forecast
import renewal_alerts
import report_generator
import vendor_health
from dual_writer import log_asset

logger = logging.getLogger("cafom.app")

# Set page config
st.set_page_config(
    page_title="CAFOM",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize DB
db_path = Path(__file__).parent / "data" / "cafom.db"
asset_tracker.init_db(db_path)


def main():
    """Run the Streamlit app."""
    st.title("🛡️ CAFOM — Cybersecurity Asset Financial Operations")

    # --- KPI Strip ---
    assets = asset_tracker.get_assets()
    total_assets = len(assets)
    annual_spend = sum(
        float(a.get("annual_cost_usd", 0) or 0) for a in assets
    )
    renewals_30d = len(asset_tracker.get_renewals_within(30))

    # Count healthy vendors
    vendor_urls = {a.get("health_check_url") for a in assets if a.get("health_check_url")}
    vendor_urls = [u for u in vendor_urls if u]
    healthy_count = 0
    if vendor_urls:
        checker = vendor_health.VendorHealthChecker()
        results = checker.check_all(vendor_urls)
        healthy_count = sum(
            1 for r in results.values() if r.get("status") == "Healthy"
        )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Assets", total_assets)
    with col2:
        st.metric("Annual Spend", f"${annual_spend:,.2f}")
    with col3:
        st.metric("Renewals (30d)", renewals_30d)
    with col4:
        st.metric("Vendors Healthy", healthy_count)

    st.divider()

    # --- Tabs ---
    tab1, tab2, tab3, tab4 = st.tabs(
        ["Asset Inventory", "Vendor Health", "Renewal Calendar", "Financial Forecast"]
    )

    # --- Tab 1: Asset Inventory ---
    with tab1:
        st.header("Asset Inventory")

        # Filters
        col_vendor, col_category, col_status = st.columns(3)
        with col_vendor:
            vendors = sorted(set(a.get("vendor", "") for a in assets if a.get("vendor")))
            filter_vendor = st.multiselect("Vendor", vendors)
        with col_category:
            categories = sorted(
                set(a.get("category", "") for a in assets if a.get("category"))
            )
            filter_category = st.multiselect("Category", categories)
        with col_status:
            statuses = sorted(
                set(a.get("status", "") for a in assets if a.get("status"))
            )
            filter_status = st.multiselect("Status", statuses)

        # Filter assets
        filtered = assets
        if filter_vendor:
            filtered = [a for a in filtered if a.get("vendor") in filter_vendor]
        if filter_category:
            filtered = [a for a in filtered if a.get("category") in filter_category]
        if filter_status:
            filtered = [a for a in filtered if a.get("status") in filter_status]

        # Display table
        if filtered:
            df = pd.DataFrame(filtered)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No assets match filters.")

        # Add asset form
        st.subheader("Add New Asset")
        with st.form("add_asset_form"):
            col1, col2 = st.columns(2)
            with col1:
                asset_id = st.text_input("Asset ID (AST-XXX)", "AST-")
                product = st.text_input("Product Name")
                vendor = st.text_input("Vendor")
                category = st.text_input("Category")
            with col2:
                purchase_date = st.date_input("Purchase Date")
                renewal_date = st.date_input("Renewal Date")
                contract_months = st.number_input("Contract Term (months)", min_value=1, value=12)
                annual_cost = st.number_input("Annual Cost (USD)", min_value=0.0, value=0.0)

            col1, col2 = st.columns(2)
            with col1:
                capex_opex = st.selectbox("Type", ["OPEX", "CAPEX"])
                owner = st.text_input("Owner")
            with col2:
                status = st.selectbox("Status", ["Active", "Expired", "Renewed", "Decommissioned"])
                health_url = st.text_input("Health Check URL", "https://")

            if st.form_submit_button("Add Asset"):
                new_asset = {
                    "id": asset_id,
                    "product": product,
                    "vendor": vendor,
                    "category": category,
                    "purchase_date": purchase_date.isoformat(),
                    "renewal_date": renewal_date.isoformat(),
                    "contract_term_months": contract_months,
                    "annual_cost_usd": annual_cost,
                    "capex_opex": capex_opex,
                    "owner": owner,
                    "status": status,
                    "health_check_url": health_url,
                }
                try:
                    log_asset(new_asset)
                    st.success(f"Asset {asset_id} added!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to add asset: {e}")

    # --- Tab 2: Vendor Health ---
    with tab2:
        st.header("Vendor Health Status")

        col_run, col_sep = st.columns([1, 10])
        with col_run:
            if st.button("🔄 Run All Checks"):
                checker = vendor_health.VendorHealthChecker()
                results = checker.check_all(vendor_urls)
                st.session_state.vendor_results = results

        # Display results
        if "vendor_results" in st.session_state:
            results = st.session_state.vendor_results
            health_data = []
            for url, result in results.items():
                health_data.append({
                    "URL": url,
                    "Status": result.get("status"),
                    "Status Code": result.get("status_code"),
                    "Response (ms)": result.get("response_ms"),
                    "Checked At": result.get("checked_at"),
                })

            if health_data:
                df_health = pd.DataFrame(health_data)
                # Color status
                def color_status(val):
                    if val == "Healthy":
                        return "background-color: #d4f1d4"
                    elif val == "Degraded":
                        return "background-color: #fff4e6"
                    elif val == "Down":
                        return "background-color: #ffd4d4"
                    return ""

                df_styled = df_health.style.applymap(
                    color_status, subset=["Status"]
                )
                st.dataframe(df_styled, use_container_width=True)
        else:
            st.info("Click 'Run All Checks' to probe vendor endpoints.")

    # --- Tab 3: Renewal Calendar ---
    with tab3:
        st.header("Renewal Alerts & Calendar")

        result = renewal_alerts.evaluate_portfolio(assets)
        counts = result["counts"]
        alerts = result["alerts"]

        # Summary by level
        col_g, col_y, col_r, col_c = st.columns(4)
        with col_g:
            st.metric("🟢 Green", counts["GREEN"])
        with col_y:
            st.metric("🟡 Yellow", counts["YELLOW"])
        with col_r:
            st.metric("🔴 Red", counts["RED"])
        with col_c:
            st.metric("⚠️ Critical", counts["CRITICAL"])

        st.divider()

        # Alert list (Red + Critical only)
        if alerts:
            alert_df = pd.DataFrame(alerts)
            st.subheader("Active Alerts (Red & Critical)")
            st.dataframe(alert_df, use_container_width=True)
        else:
            st.info("No active renewal alerts.")

    # --- Tab 4: Financial Forecast ---
    with tab4:
        col_h, col_dl = st.columns([4, 1])
        with col_h:
            st.header("Financial Forecast & Analytics")
        with col_dl:
            st.write("")  # vertical spacing
            try:
                pdf_bytes = report_generator.build_report(assets=assets)
                st.download_button(
                    label="📄 Download Report",
                    data=pdf_bytes,
                    file_name="cafom_portfolio_report.pdf",
                    mime="application/pdf",
                    help="Download a full PDF portfolio report",
                )
            except Exception as e:
                st.error(f"Report unavailable: {e}")

        # 12-month projection
        projection = financial_forecast.project_12_months(assets)
        if projection:
            df_forecast = pd.DataFrame(projection)
            st.subheader("12-Month Cost Projection")
            st.line_chart(df_forecast.set_index("month_name")[["projected_cost"]])
            st.dataframe(df_forecast, use_container_width=True)

        st.divider()

        # CAPEX vs OPEX
        capex_sum = sum(
            float(a.get("annual_cost_usd", 0) or 0)
            for a in assets
            if a.get("capex_opex") == "CAPEX"
        )
        opex_sum = sum(
            float(a.get("annual_cost_usd", 0) or 0)
            for a in assets
            if a.get("capex_opex") == "OPEX"
        )

        if capex_sum > 0 or opex_sum > 0:
            st.subheader("CAPEX vs OPEX")
            col1, col2 = st.columns(2)
            with col1:
                pie_data = {"CAPEX": capex_sum, "OPEX": opex_sum}
                st.bar_chart(pd.Series(pie_data))
            with col2:
                st.metric("Total CAPEX", f"${capex_sum:,.2f}")
                st.metric("Total OPEX", f"${opex_sum:,.2f}")

        st.divider()

        # Anomalies
        outliers = anomaly_detector.flag_outliers(assets)
        if outliers:
            st.subheader("⚠️ Cost Anomalies (Mean + 2σ)")
            outlier_df = pd.DataFrame(outliers)
            st.dataframe(outlier_df[["id", "product", "vendor", "annual_cost_usd", "threshold"]], use_container_width=True)
        else:
            st.info("No cost anomalies detected.")


if __name__ == "__main__":
    main()

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
import executive_kpis
import financial_forecast
import historical_analysis
import market_data
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


def _enrich_with_historical(assets: list[dict]) -> list[dict]:
    """Merge raw_json fields (incl. historical costs + budget) into each asset.

    The SQLite ledger stores fixed columns; historical_cost_2023/2024/2025
    and budget_annual_usd live inside raw_json. This helper merges them so
    Tab 4 (Financial Intelligence) can consume a flat dict per asset.
    """
    enriched = []
    for a in assets:
        merged = dict(a)
        raw = a.get("raw_json")
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    for k, v in parsed.items():
                        if merged.get(k) is None:
                            merged[k] = v
            except (json.JSONDecodeError, TypeError):
                pass
        enriched.append(merged)
    return enriched


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
        ["Asset Inventory", "Vendor Health", "Renewal Calendar", "Financial Intelligence"]
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

                df_styled = df_health.style.map(
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

        # Critical Gap Recovery Plan — executive view of overdue exposure
        gap = executive_kpis.get_critical_gap_recovery(assets)
        with st.expander(
            f"🚨 Critical Gap Recovery Plan "
            f"({gap['count']} overdue · ${gap['total_exposure']:,.0f} exposure)",
            expanded=gap["count"] > 0,
        ):
            if gap["items"]:
                gap_df = pd.DataFrame(gap["items"])

                # Style: highlight recommended_action by severity
                def _action_color(val):
                    if val == "Replace":
                        return "background-color: #4a0000; color: #ffaaaa"
                    if val == "Negotiate":
                        return "background-color: #5a3a00; color: #ffd9a3"
                    if val == "Renew immediately":
                        return "background-color: #003a1a; color: #aaffcf"
                    return ""

                display_cols = [
                    "id", "product", "vendor", "category",
                    "renewal_date", "days_past_renewal",
                    "annual_cost_usd", "daily_cost", "financial_exposure",
                    "recommended_action",
                ]
                display_cols = [c for c in display_cols if c in gap_df.columns]
                styled = gap_df[display_cols].style.format({
                    "annual_cost_usd": "${:,.2f}",
                    "daily_cost": "${:,.2f}",
                    "financial_exposure": "${:,.2f}",
                }).map(_action_color, subset=["recommended_action"])
                st.dataframe(styled, use_container_width=True)

                kpi_a, kpi_b, kpi_c = st.columns(3)
                with kpi_a:
                    st.metric("Total Overdue", f"{gap['count']} assets")
                with kpi_b:
                    st.metric("Total Exposure", f"${gap['total_exposure']:,.0f}")
                with kpi_c:
                    top = gap["items"][0]
                    st.metric(
                        "Largest Risk",
                        top["product"][:18] + ("…" if len(top["product"]) > 18 else ""),
                        delta=f"${top['financial_exposure']:,.0f}",
                        delta_color="inverse",
                    )

                # Also show category-level coverage lost
                lost = executive_kpis.get_days_of_coverage_lost(assets)
                if lost["by_category"]:
                    st.caption(
                        f'**Days of Coverage Lost:** {lost["total_days_lost"]} cumulative days · '
                        f'**Total Risk:** ${lost["total_financial_risk"]:,.0f}'
                    )
                    cat_rows = [
                        {
                            "Category": cat,
                            "Days Lost": data["days_lost"],
                            "Financial Risk": data["financial_risk"],
                            "Assets": data["asset_count"],
                        }
                        for cat, data in lost["by_category"].items()
                    ]
                    cat_df = pd.DataFrame(cat_rows)
                    cat_df = cat_df.sort_values("Financial Risk", ascending=False)
                    cat_styled = cat_df.style.format({
                        "Financial Risk": "${:,.2f}",
                    })
                    st.dataframe(cat_styled, use_container_width=True, hide_index=True)
            else:
                st.success("✅ No overdue contracts — portfolio is current.")

    # --- Tab 4: Financial Intelligence ---
    with tab4:
        col_h, col_dl = st.columns([4, 1])
        with col_h:
            st.header("Financial Intelligence")
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

        enriched = _enrich_with_historical(assets)

        # ----- Section A: Historical Spend 2023-2026 -----
        st.subheader("📊 Section A — Historical Spend 2023-2026")
        hist = historical_analysis.get_historical_spend(enriched)
        yearly = hist["yearly_totals"]
        if any(v > 0 for v in yearly.values()):
            df_hist = pd.DataFrame({
                "Year": [str(y) for y in yearly.keys()],
                "Total Spend (USD)": list(yearly.values()),
            }).set_index("Year")
            st.bar_chart(df_hist)

            yoy = hist["yoy_change"]
            cagr = hist["cagr_pct"]
            total_growth = hist["total_growth_pct"]

            kpi_cols = st.columns(4)
            with kpi_cols[0]:
                st.metric(
                    "YoY 2024",
                    f"{yoy.get(2024):.1f}%" if yoy.get(2024) is not None else "n/a",
                )
            with kpi_cols[1]:
                st.metric(
                    "YoY 2025",
                    f"{yoy.get(2025):.1f}%" if yoy.get(2025) is not None else "n/a",
                )
            with kpi_cols[2]:
                st.metric(
                    "YoY 2026",
                    f"{yoy.get(2026):.1f}%" if yoy.get(2026) is not None else "n/a",
                )
            with kpi_cols[3]:
                st.metric(
                    "CAGR 2023-2026",
                    f"{cagr:.2f}%" if cagr is not None else "n/a",
                    delta=f"{total_growth:.1f}% total" if total_growth is not None else None,
                )
        else:
            st.info("Historical cost fields not present on assets.")

        st.divider()

        # ----- Section B: Market Context -----
        st.subheader("📈 Section B — Market Context: Spend Growth vs Sector Stock Index")
        st.caption("Indexed to 1.0 at start of 2023. Internal spend rebased to same baseline for direct comparison.")
        try:
            with st.spinner("Loading market data (yfinance)…"):
                sector = market_data.get_sector_index()
            if sector["values"]:
                # Build a unified frame: sector index + spend growth (rebased to 1.0)
                df_sector = pd.DataFrame({
                    "Date": pd.to_datetime(sector["dates"]),
                    "Sector Index": sector["values"],
                }).set_index("Date")

                if any(v > 0 for v in yearly.values()) and yearly[2023] > 0:
                    spend_index = {
                        f"{y}-01-01": yearly[y] / yearly[2023]
                        for y in [2023, 2024, 2025, 2026]
                    }
                    df_spend = pd.DataFrame({
                        "Date": pd.to_datetime(list(spend_index.keys())),
                        "Spend Growth": list(spend_index.values()),
                    }).set_index("Date")
                    combined = df_sector.join(df_spend, how="outer").ffill()
                else:
                    combined = df_sector

                st.line_chart(combined)
                st.caption(
                    f'**{sector["label"]}** — Tickers used: '
                    f'{", ".join(sector["tickers_used"]) or "none"}'
                )
                if sector["tickers_missing"]:
                    st.caption(
                        f'⚠️ Tickers unavailable (skipped): {", ".join(sector["tickers_missing"])}'
                    )
            else:
                st.warning(
                    "Sector index unavailable (no tickers downloaded). "
                    "Network or yfinance issue — graceful skip applied."
                )
        except Exception as e:
            st.warning(f"Market data unavailable: {e}")

        st.divider()

        # ----- Section C: Vendor Concentration -----
        st.subheader("🥧 Section C — Vendor Concentration")
        conc = historical_analysis.get_vendor_concentration(enriched)
        if conc["per_vendor"]:
            top_col, hhi_col = st.columns(2)
            with top_col:
                st.metric(
                    "Top Vendor",
                    conc["top_vendor"] or "n/a",
                    delta=f'{conc["top_pct"]:.1f}% of total spend',
                )
            with hhi_col:
                st.metric(
                    "HHI Concentration",
                    f'{conc["hhi"]:.0f}',
                    help="Herfindahl-Hirschman Index — <1500 unconcentrated, 1500-2500 moderate, >2500 high",
                )

            # Pie chart via matplotlib (Streamlit's bar_chart doesn't do pies)
            try:
                import matplotlib.pyplot as plt
                fig, ax = plt.subplots(figsize=(6, 6))
                labels = [r["vendor"] for r in conc["per_vendor"]]
                sizes = [r["total"] for r in conc["per_vendor"]]
                ax.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=140)
                ax.axis("equal")
                st.pyplot(fig)
                plt.close(fig)
            except Exception as e:
                # Fallback: bar chart of vendor totals
                df_conc = pd.DataFrame(conc["per_vendor"]).set_index("vendor")[["total"]]
                st.bar_chart(df_conc)
        else:
            st.info("No vendor spend data available.")

        st.divider()

        # ----- Section D: Monthly Burn Rate -----
        st.subheader("🔥 Section D — Monthly Burn Rate (Actual vs Budget)")
        burn = historical_analysis.get_burn_rate(enriched)
        if burn["months"]:
            df_burn = pd.DataFrame({
                "Month": burn["months"],
                "Actual": burn["actual"],
                "Budget": burn["budget"],
            }).set_index("Month")
            st.bar_chart(df_burn)

            burn_cols = st.columns(3)
            with burn_cols[0]:
                st.metric("Total Actual", f'${burn["total_actual"]:,.0f}')
            with burn_cols[1]:
                st.metric("Total Budget", f'${burn["total_budget"]:,.0f}')
            with burn_cols[2]:
                variance = burn["variance_pct"]
                st.metric(
                    "Variance",
                    f"{variance:+.2f}%",
                    delta="Under budget" if variance < 0 else "Over budget",
                    delta_color="normal" if variance < 0 else "inverse",
                )

            # Cumulative burn rate trajectory (executive KPI view)
            st.markdown("**Cumulative Trajectory — are we converging over- or under-budget?**")
            trend = executive_kpis.get_monthly_burn_rate_trend(enriched)
            df_cum = pd.DataFrame({
                "Month": trend["months"],
                "Cumulative Actual": trend["cumulative_actual"],
                "Cumulative Budget": trend["cumulative_budget"],
            }).set_index("Month")
            st.line_chart(df_cum)

            cum_cols = st.columns(2)
            with cum_cols[0]:
                eoy = trend["end_of_year_variance_pct"]
                st.metric(
                    "End-of-Year Variance",
                    f"{eoy:+.2f}%",
                    delta=trend["trending"],
                    delta_color=(
                        "normal" if trend["trending"] == "Under budget"
                        else "inverse" if trend["trending"] == "Over budget"
                        else "off"
                    ),
                )
            with cum_cols[1]:
                # Variance at mid-year (June, index 5) for an early signal
                mid_year_pct = trend["cumulative_variance_pct"][5]
                st.metric(
                    "Mid-Year Variance (Jun)",
                    f"{mid_year_pct:+.2f}%",
                    help="Cumulative variance at month 6 — early-warning signal",
                )
        else:
            st.info("Burn rate unavailable.")

        st.divider()

        # ----- Section E: Cost Anomalies (4-Year Baseline) -----
        st.subheader("⚠️ Section E — Cost Anomalies (4-Year Baseline)")
        st.caption("Improved detection: baseline = mean ± 2σ across **all category cost observations 2023-2026** (instead of single-year only).")
        improved = historical_analysis.get_improved_anomalies(enriched)
        if improved:
            improved_df = pd.DataFrame(improved)
            display_cols = [
                "id", "product", "vendor", "category",
                "annual_cost_usd", "baseline_mean", "threshold", "observations_count",
            ]
            display_cols = [c for c in display_cols if c in improved_df.columns]
            st.dataframe(improved_df[display_cols], use_container_width=True)
        else:
            st.success("✅ No cost anomalies — all 2026 costs within 4-year category baselines.")

        # Also show legacy single-year detection for comparison
        legacy = anomaly_detector.flag_outliers(enriched)
        if legacy:
            with st.expander("Compare with single-year detection (legacy)"):
                legacy_df = pd.DataFrame(legacy)
                cols = [c for c in ["id", "product", "vendor", "annual_cost_usd", "threshold"] if c in legacy_df.columns]
                st.dataframe(legacy_df[cols], use_container_width=True)

        st.divider()

        # ----- Section F: 12-Month Forecast (existing) -----
        st.subheader("📅 Section F — 12-Month Cost Projection")
        projection = financial_forecast.project_12_months(enriched)
        if projection:
            df_forecast = pd.DataFrame(projection)
            st.line_chart(df_forecast.set_index("month_name")[["projected_cost"]])
            st.dataframe(df_forecast, use_container_width=True)

        st.divider()

        # CAPEX vs OPEX (preserved from original)
        capex_sum = sum(
            float(a.get("annual_cost_usd", 0) or 0)
            for a in enriched
            if a.get("capex_opex") == "CAPEX"
        )
        opex_sum = sum(
            float(a.get("annual_cost_usd", 0) or 0)
            for a in enriched
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


if __name__ == "__main__":
    main()

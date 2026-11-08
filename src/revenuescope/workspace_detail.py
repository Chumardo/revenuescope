"""Investigation evidence for a selected detector flag."""

from datetime import date

import pandas as pd


DIMENSIONS = {"Region": "region", "Product": "product", "Channel": "channel"}


def _money(cents: int, *, signed: bool = False) -> str:
    sign = "−" if cents < 0 else "+" if signed and cents > 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{remainder:02d}"


def history_window(daily: pd.DataFrame, day: date, *, prior_days: int = 55) -> pd.DataFrame:
    """Keep a bounded calendar window ending at the investigation date."""
    end = pd.Timestamp(day)
    start = end - pd.Timedelta(days=prior_days)
    return daily.loc[daily["date"].between(start, end)].sort_values("date")


def contribution_rows(alert: dict, dimension: str) -> list[dict] | None:
    """Prepare a complete, cent-reconciling dimension, including any unallocated expectation."""
    breakdown = alert["contributions"].get(dimension)
    if breakdown is None:
        return None
    rows = list(breakdown["rows"])
    if breakdown["unallocated_expected_cents"] or breakdown["unallocated_deviation_cents"]:
        rows.append({"segment": "Unallocated expectation", "actual_cents": 0,
                     "allocated_expected_cents": breakdown["unallocated_expected_cents"],
                     "deviation_cents": breakdown["unallocated_deviation_cents"]})
    pairs = (
        ("actual_cents", "actual_revenue_cents"),
        ("allocated_expected_cents", "expected_revenue_cents"),
        ("deviation_cents", "deviation_cents"),
    )
    if any(sum(row[component] for row in rows) != alert[total] for component, total in pairs) or any(
        row["actual_cents"] - row["allocated_expected_cents"] != row["deviation_cents"] for row in rows
    ):
        raise ValueError("Contribution rows do not reconcile to the alert totals.")
    return rows


def _history_chart(history: pd.DataFrame, alert: dict):
    import plotly.graph_objects as go

    dates = history["date"].tolist()
    actual = [int(value) for value in history["net_revenue_cents"]]
    expected = [int(value) if pd.notna(value) else None for value in history["expected_revenue_cents"]]
    chart = go.Figure()
    chart.add_trace(go.Scatter(x=dates, y=[value / 100 for value in actual], mode="lines", name="Actual",
                               line={"color": "#087f75", "width": 2.5}, text=[_money(value) for value in actual],
                               hovertemplate="%{text}<extra>Actual</extra>"))
    chart.add_trace(go.Scatter(x=dates, y=[value / 100 if value is not None else None for value in expected],
                               mode="lines", name="Expected", line={"color": "#56708f", "dash": "dash", "width": 2},
                               text=[_money(value) if value is not None else None for value in expected],
                               connectgaps=False, hovertemplate="%{text}<extra>Expected</extra>"))
    chart.add_trace(go.Scatter(x=[alert["date"]], y=[alert["actual_revenue_cents"] / 100], mode="markers",
                               name="Selected flag", marker={"color": "#aa521c", "size": 10,
                               "line": {"color": "white", "width": 1.5}},
                               hovertemplate=f"{_money(alert['actual_revenue_cents'])}<extra>Selected flag</extra>"))
    chart.update_layout(height=300, margin={"l": 8, "r": 12, "t": 32, "b": 12}, hovermode="x unified",
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font={"color": "#334967", "size": 12},
                        legend={"orientation": "h", "y": 1.14, "x": 0, "font": {"size": 11}})
    chart.update_xaxes(showgrid=False, tickformat="%b %d", nticks=6, automargin=True)
    chart.update_yaxes(gridcolor="#e3ebf3", zerolinecolor="#cbd8e5", tickprefix="$", automargin=True)
    return chart


def render_detail(daily: pd.DataFrame, alert: dict | None, *, metric_label: str) -> None:
    """Display the selected flag, prior context and complete contribution evidence."""
    import streamlit as st

    st.title("Investigation detail")
    if alert is None:
        st.info("Select a flagged date in the Review Queue to open its evidence.")
        return

    day = date.fromisoformat(alert["date"])
    percentage = alert["percentage_deviation"]
    pct = f"{percentage:+.2f}%" if percentage is not None else "Percentage unavailable"
    st.subheader(day.strftime("%d %b %Y"))
    st.caption(f"{metric_label} · Synthetic data · Flagged for review · {alert['severity'].title()} impact")
    with st.container(key="rs_detail_kpis"):
        cards = st.columns(3, gap="small")
        cards[0].metric("Actual revenue", _money(alert["actual_revenue_cents"]), border=True)
        cards[1].metric("Expected revenue", _money(alert["expected_revenue_cents"]), border=True)
        cards[2].metric("Deviation", _money(alert["deviation_cents"], signed=True), pct,
                        delta_color="off", border=True, help="Actual less expected. Percentage uses the absolute expectation.")

    st.subheader("Segment contributions")
    st.caption("Each dimension adds up to the same gap. The model expectation is split using positive historical revenue mix. "
               "The contributions locate the gap. Confirming a cause requires additional evidence.")
    label = st.segmented_control("Contribution dimension", list(DIMENSIONS), default="Region", required=True,
                                 key="detail_dimension", wrap=True)
    try:
        rows = contribution_rows(alert, DIMENSIONS[label])
    except ValueError:
        st.error("The contribution rows could not be reconciled. Check the source evidence for this alert.")
    else:
        if rows is None:
            st.info(f"{label} contribution evidence is unavailable for this date.")
        elif not rows:
            st.info(f"No {label.lower()} contribution rows were recorded for this date.")
        else:
            st.caption(f"Reconciled to the ledger and baseline · {_money(alert['actual_revenue_cents'])} actual · "
                       f"{_money(alert['expected_revenue_cents'])} allocated expected · "
                       f"{_money(alert['deviation_cents'], signed=True)} deviation")
            if label == "Product":
                st.caption("Product-level history can be sparse. Allocated expectations use the overall "
                           "flag's reference-day mix, so treat product amounts as contribution evidence.")
            table = [{"Contributor": row["segment"], "Deviation": _money(row["deviation_cents"], signed=True),
                      "Actual": _money(row["actual_cents"]),
                      "Allocated expected": _money(row["allocated_expected_cents"])} for row in rows]
            st.dataframe(table, width="stretch", height="auto" if len(rows) <= 8 else 300, hide_index=True,
                         key="detail_contribution_table", column_config={
                             "Contributor": st.column_config.TextColumn(width=163),
                             "Deviation": st.column_config.TextColumn(width=104),
                         })

    st.subheader("Revenue history")
    st.caption("Daily actuals and available expectations for up to eight weeks ending on the selected date.")
    history = history_window(daily, day)
    if history.empty:
        st.info("Historical daily revenue is unavailable for the selected date.")
    else:
        st.plotly_chart(_history_chart(history, alert), width="stretch", config={"displayModeBar": False},
                        theme=None, key="detail_history_plot")

    with st.expander("Scoring evidence and data quality"):
        diagnostics = alert["diagnostics"]
        score = diagnostics.get("score")
        scale = diagnostics.get("scale_cents")
        threshold = diagnostics.get("alert_threshold_cents")
        st.markdown("**Method** · Locked weekday trend and historical median absolute deviation")
        if score is not None and scale is not None and threshold is not None:
            st.markdown(f"**Score** · {score:+.2f}  \n**Dispersion scale** · ${scale / 100:,.2f}  \n"
                        f"**Effective deviation threshold** · ${threshold / 100:,.2f}")
        references = alert["reference_dates"]
        st.markdown(f"**Reference dates** · {len(references)} earlier observations")
        if references:
            st.caption(", ".join(references))
        for warning in alert["data_quality_warnings"]:
            st.caption(warning)

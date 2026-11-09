"""Revenue overview and review queue for the local analyst workspace."""

from datetime import timedelta
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .pit import POSTED_METRIC, RETROSPECTIVE_METRIC
from .workspace_data import filter_dates, overview_kpis, review_queue, scope_daily

PAGES = ("Revenue Overview", "Analyst Review Queue", "Investigation Detail", "Evaluation")
BASIS_LABELS = {
    POSTED_METRIC: "Posted-day activity",
    RETROSPECTIVE_METRIC: "Order-day net revenue · retrospective",
}
BASIS_NOTES = {
    POSTED_METRIC: "UTC · USD. Bookings count on checkout day and refunds on posting day.",
    RETROSPECTIVE_METRIC: "UTC · USD. Refunds known at the snapshot cutoff restate the original order day. Use this view for retrospective comparison.",
}
DIMENSION_LABELS = {None: "All revenue", "region": "Region", "product": "Product", "channel": "Channel"}
DIRECTIONS = {"All directions": (), "Increase": ("up",), "Decrease": ("down",)}
SEVERITIES = {"All severities": (), "High": ("high",), "Medium": ("medium",), "Low": ("low",)}
SORT_ORDERS = ("Newest first", "Largest absolute deviation", "Oldest first")
DEFAULT_QUEUE_SORT = "Largest absolute deviation"


def _money(cents, *, signed=False):
    if pd.isna(cents):
        return "—"
    value = int(cents)
    sign = "−" if value < 0 else "+" if signed and value > 0 else ""
    return f"{sign}${Decimal(abs(value)) / 100:,.2f}"


def _percent(value):
    return "—" if pd.isna(value) else f"{value:+.2f}%"


def date_controls(coverage_start, coverage_end, columns):
    last = coverage_end - timedelta(days=1)
    bounds = (coverage_start, last)
    saved = st.session_state.get("rs_window", bounds)
    start, end = [max(coverage_start, min(last, day)) for day in saved]
    start_default = None if "rs_window_start" in st.session_state else start
    end_default = None if "rs_window_end" in st.session_state else end
    start = columns[0].date_input("Start date", start_default, min_value=coverage_start, max_value=last,
                                  key="rs_window_start", format="YYYY-MM-DD")
    end = columns[1].date_input("End date", end_default, min_value=coverage_start, max_value=last,
                               key="rs_window_end", format="YYYY-MM-DD")
    if start is None or end is None:
        st.warning("Choose both a start and an end date.")
        return None
    st.session_state.rs_window = (start, end)
    if start > end:
        st.warning("Choose an end date on or after the start date.")
        return None
    return start, end


def overview_scope(data, columns):
    previous_dimension, previous_segment = st.session_state.get("rs_overview_scope", (None, None))
    dimensions = list(DIMENSION_LABELS)
    dimension = columns[0].selectbox("Dimension", dimensions, index=dimensions.index(previous_dimension),
                                      format_func=DIMENSION_LABELS.__getitem__, key="rs_overview_dimension")
    options = data.segments.get(dimension, ()) if dimension else ()
    segment = columns[1].selectbox("Segment", options or ["All segments"], disabled=not options,
                                   index=options.index(previous_segment) if previous_segment in options else 0,
                                   key=f"rs_segment_{dimension}")
    scope = (dimension, segment) if options else (None, None)
    st.session_state.rs_overview_scope = scope
    return scope


def _set_window(start, end):
    st.session_state.update(rs_window=(start, end), rs_window_start=start, rs_window_end=end)


def _revenue_chart(frame):
    figure = go.Figure()
    actual = frame.net_revenue_cents.astype("Int64").where(frame.is_complete)
    expected = frame.expected_revenue_cents
    custom = [[_money(a), _money(e), _money(d, signed=True), _money(abs(d)) if pd.notna(d) else "—", _percent(p)]
              for a, e, d, p in zip(actual, expected, frame.residual_cents, frame.percentage_deviation, strict=True)]
    hover = ("%{x|%d %b %Y}<br>Actual %{customdata[0]}<br>Expected %{customdata[1]}"
             "<br>Deviation %{customdata[2]} (%{customdata[4]})<br>Absolute deviation %{customdata[3]}<extra></extra>")
    trace_mode = "markers" if len(frame) == 1 else "lines"
    figure.add_scatter(name="Actual", x=frame.date, y=actual.astype(float) / 100, mode=trace_mode, customdata=custom,
                       line={"color": "#087f73", "width": 1.8}, hovertemplate=hover)
    if expected.notna().any():
        figure.add_scatter(name="Expected", x=frame.date, y=expected.astype(float) / 100, mode=trace_mode, customdata=custom,
                           line={"color": "#385777", "width": 1.7, "dash": "dot"}, hovertemplate=hover)
    flagged = frame.is_anomaly.tolist()
    if any(flagged):
        figure.add_scatter(name="Detector flag", x=frame.loc[flagged, "date"], y=actual.loc[flagged].astype(float) / 100,
                           mode="markers", customdata=[row for row, flag in zip(custom, flagged, strict=True) if flag],
                           marker={"color": "#a24d08", "symbol": "diamond", "size": 8,
                                   "line": {"color": "white", "width": 1}}, hovertemplate=hover)
    figure.update_layout(height=345, margin={"l": 8, "r": 12, "t": 12, "b": 8},
                         paper_bgcolor="#ffffff", plot_bgcolor="#ffffff", hovermode="closest",
                         font={"color": "#40596f"}, legend={"orientation": "h", "y": 1.06, "yanchor": "bottom"},
                         yaxis={"tickprefix": "$", "tickformat": "~s", "gridcolor": "#e4edf3", "automargin": True},
                         xaxis={"showgrid": False, "automargin": True})
    return figure


def _daily_values(frame):
    rows = pd.DataFrame({
        "Date (UTC)": frame.date.dt.strftime("%Y-%m-%d"),
        "Actual": [_money(cents) for cents in frame.net_revenue_cents.astype("Int64").where(frame.is_complete)],
        "Expected": [_money(cents) for cents in frame.expected_revenue_cents],
        "Deviation": [_money(cents, signed=True) for cents in frame.residual_cents],
        "Absolute deviation": [_money(cents) for cents in frame.absolute_deviation_cents],
        "Deviation (%)": [_percent(value) for value in frame.percentage_deviation],
        "Detector flag": frame.is_anomaly,
        "Status": frame.status.map({"scored": "Scored", "insufficient_history": "Warmup",
                                    "sparse_history": "Sparse history", "incomplete_day": "Incomplete",
                                    "actual_only": "Segment actual only"}).fillna("Expectation unavailable"),
    })
    st.dataframe(rows, hide_index=True, width="stretch", height=265)


def render_overview(data, start, end, *, dimension=None, segment=None):
    frame = filter_dates(scope_daily(data, dimension, segment), start, end)
    kpis = overview_kpis(frame)
    if not kpis.calendar_days:
        st.info("No calendar dates fall inside this selection.")
        return
    st.caption(BASIS_NOTES[data.metric])
    with st.container(key="rs_kpis"):
        if dimension:
            st.metric("Segment actual · complete dates", _money(kpis.actual_cents), border=True)
            st.caption(f"{DIMENSION_LABELS[dimension]} · {segment}. Segment revenue is shown as context. The validated baseline and queue use overall revenue.")
        else:
            cards = st.columns(4)
            cards[0].metric("Actual · all complete dates", _money(kpis.actual_cents), border=True,
                            help="Includes warmup dates with recorded revenue.")
            cards[1].metric("Actual · scored dates", _money(kpis.scored_actual_cents), border=True,
                            help="Actual revenue on the same dates used for Expected and Net deviation.")
            cards[2].metric("Expected · scored dates", _money(kpis.expected_cents), border=True,
                            help="Sum of the locked weekday baseline on scored dates.")
            cards[3].metric("Net deviation · scored dates", _money(kpis.deviation_cents, signed=True), border=True,
                            delta=_percent(kpis.percentage_deviation) if kpis.percentage_deviation is not None else None,
                            delta_color="off", help="Scored actual less expected, divided by the absolute expectation for the percentage.")
    with st.container(border=True, key="rs_overview_chart"):
        st.subheader("Daily revenue")
        if not dimension and kpis.scored_days:
            st.caption(f"{kpis.scored_days:,} of {kpis.calendar_days:,} days scored · "
                       f"{kpis.flagged_days:,} flagged for review")
        elif not dimension:
            st.info("Expected revenue is unavailable for the selected dates. See the coverage notes below.")
        last = data.metadata.coverage_end - timedelta(days=1)
        recent = (max(data.metadata.coverage_start, last - timedelta(days=89)), last)
        full = (data.metadata.coverage_start, last)
        with st.container(horizontal=True):
            if not dimension and kpis.flagged_days:
                st.button("Open review queue", on_click=lambda: st.session_state.update(rs_page=PAGES[1]))
            if (start, end) != recent:
                st.button("Last 90 days", on_click=_set_window, args=recent)
            if (start, end) != full:
                st.button("Full snapshot", on_click=_set_window, args=full)
        st.plotly_chart(_revenue_chart(frame), width="stretch", theme=None, config={"displayModeBar": False})
        notes = []
        if kpis.warmup_days:
            notes.append(f"{kpis.warmup_days:,} warmup days with fewer than eight complete same-weekday references")
        if kpis.sparse_days:
            notes.append(f"{kpis.sparse_days:,} sparse days")
        if kpis.incomplete_days:
            notes.append(f"{kpis.incomplete_days:,} incomplete days")
        if notes:
            st.caption("Expectations are unavailable on " + ", ".join(notes) + ". Date filters keep the original baseline history.")
        with st.expander("Daily values and coverage"):
            _daily_values(frame)


def _queue_order(queue, order):
    if order == SORT_ORDERS[1]:
        return queue.sort_values(["absolute_deviation_cents", "date"], ascending=[False, False])
    return queue.sort_values("date", ascending=order == SORT_ORDERS[2])


def render_queue(data, start, end):
    st.caption(BASIS_NOTES[data.metric])
    with st.container(border=True, key="rs_queue"):
        scope = review_queue(data, start, end)
        impacts = [label for label, values in SEVERITIES.items() if values and scope.severity.isin(values).any()]
        controls = st.columns(3 if len(impacts) > 1 else 2)
        defaults = (next(iter(DIRECTIONS)), next(iter(SEVERITIES)), DEFAULT_QUEUE_SORT)
        previous = st.session_state.get("rs_queue_filters", defaults)
        direction = controls[0].selectbox("Direction", list(DIRECTIONS), index=list(DIRECTIONS).index(previous[0]),
                                          key="rs_queue_direction")
        severity = defaults[1]
        if len(impacts) > 1:
            options = [severity, *impacts]
            severity = controls[1].selectbox("Impact", options, index=options.index(previous[1]) if previous[1] in options else 0,
                                             key="rs_queue_severity")
        order = controls[-1].selectbox("Sort by", SORT_ORDERS, index=SORT_ORDERS.index(previous[2]),
                                       format_func=lambda value: "Largest gap" if value == DEFAULT_QUEUE_SORT else value,
                                       key="rs_queue_sort", help="Largest gap sorts by absolute dollar deviation.")
        st.session_state.rs_queue_filters = (direction, severity, order)
        queue = review_queue(data, start, end, directions=DIRECTIONS[direction], severities=SEVERITIES[severity])
        if queue.empty:
            st.info("No detector flags match these dates and filters. Try a wider date range or reset the filters to show all available flags.")
            return
        queue = _queue_order(queue, order)
        st.subheader(f"{len(queue):,} dates to review")
        largest = _queue_order(queue, DEFAULT_QUEUE_SORT).iloc[0]
        st.markdown(f"**Largest dollar deviation** · {_money(largest.deviation_cents, signed=True)} on {largest.date:%d %b %Y}")
        dates = queue.date.dt.date.tolist()
        previous_day = st.session_state.get("rs_queue_day")
        picker, action = st.columns([2, 1], vertical_alignment="bottom")
        day = picker.selectbox("Flagged date", dates, index=dates.index(previous_day) if previous_day in dates else 0,
                               format_func=lambda date: date.isoformat())
        st.session_state.rs_queue_day = day

        def open_investigation():
            st.session_state.rs_investigation = (data.metric, day)
            st.session_state.rs_page = PAGES[2]

        action.button("Open investigation", type="primary", on_click=open_investigation)

        table = pd.DataFrame({"Date (UTC)": queue.date.dt.strftime("%Y-%m-%d"),
                              "Deviation": queue.deviation_cents / 100, "Impact": queue.severity.str.title(),
                              "Direction": queue.direction.map({"up": "Increase", "down": "Decrease"}),
                              "Actual": queue.actual_revenue_cents / 100, "Expected": queue.expected_revenue_cents / 100,
                              "Deviation (%)": queue.percentage_deviation})
        height = min(325, max(100, 38 + len(table) * 35))
        st.dataframe(table, hide_index=True, width="stretch", height=height, column_config={
            name: st.column_config.NumberColumn(name, format="dollar", width=112)
            for name in ("Actual", "Expected", "Deviation")
        } | {"Date (UTC)": st.column_config.TextColumn(width=104), "Impact": st.column_config.TextColumn(width=75),
             "Deviation (%)": st.column_config.NumberColumn(format="%+.2f%%")})
        if len(impacts) == 1:
            st.caption(f"All flags in this date window have {impacts[0].lower()} impact under the existing severity rules.")
        st.caption("High impact requires a deviation of at least $1,000 and 25%. Medium requires at least $500 and 15%.")

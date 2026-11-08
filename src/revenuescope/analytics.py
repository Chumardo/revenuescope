"""Read-only DuckDB queries for financial KPIs and purchase behavior."""

from datetime import date, datetime
from functools import cache
from importlib.resources import files

import duckdb
import pandas as pd

from .db import metadata

_SEGMENTS = {
    "category": "f.category",
    "product": "f.product_name || ' [#' || f.product_id::VARCHAR || ']'",
    "region": "f.region",
    "channel": "f.channel",
}


@cache
def _sql(name: str) -> str:
    return files("revenuescope").joinpath("sql", f"{name}.sql").read_text(encoding="utf-8")


def _report_date(value: date | str) -> date:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Invalid report date: {value!r}") from exc
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError("Report bounds must be dates or ISO date strings")
    return value


def _bounds(conn: duckdb.DuckDBPyConnection, start: date | str, end: date | str) -> list[date]:
    first, stop = _report_date(start), _report_date(end)
    if first >= stop:
        raise ValueError("The report start must precede its exclusive end")
    coverage_start, coverage_end, _, _ = metadata(conn)
    if not coverage_start <= first < stop <= coverage_end:
        raise ValueError(f"Report dates must fall inside [{coverage_start}, {coverage_end})")
    return [first, stop]


def _segment_expression(dimension: str) -> str:
    try:
        return _SEGMENTS[dimension]
    except KeyError as exc:
        raise ValueError(f"Unknown dimension {dimension!r}. Choose {', '.join(_SEGMENTS)}.") from exc


def _kpi_query(name: str, **kwargs: str) -> str:
    return _sql(name).format(
        line_facts=_sql("line_facts"),
        kpi_sums=_sql("kpi_sums"),
        kpi_ratios=_sql("kpi_ratios"),
        **kwargs,
    )


def summarize(
    conn: duckdb.DuckDBPyConnection,
    start: date | str,
    end: date | str,
    dimension: str | None = None,
) -> pd.DataFrame:
    """Return one overall KPI row or one row per observed segment.

    Segment orders and customers are distinct within each segment. These
    counts are non-additive across product categories and any dimensions a
    customer can cross. Revenue and other cent sums are additive.
    """
    params = _bounds(conn, start, end)
    expression = _segment_expression(dimension) if dimension is not None else None
    query = _kpi_query(
        "summarize",
        segment_projection=f"{expression} AS segment," if expression else "",
        line_join="JOIN" if expression else "LEFT JOIN",
        segment_group="GROUP BY segment" if expression else "",
        segment_order="ORDER BY segment" if expression else "",
    )
    return conn.execute(query, params).fetchdf()


def daily_revenue(
    conn: duckdb.DuckDBPyConnection,
    start: date | str,
    end: date | str,
    dimension: str | None = None,
    segment: str | None = None,
) -> pd.DataFrame:
    """Return one row per requested date, including days with zero orders.

    Dates after the snapshot cutoff, including a partial cutoff day, are
    marked incomplete. Exclude incomplete rows before modeling a baseline.
    """
    if (dimension is None) != (segment is None):
        raise ValueError("Provide both dimension and segment, or neither")
    params = _bounds(conn, start, end)
    predicate = ""
    if dimension is not None:
        predicate = f"AND ({_segment_expression(dimension)}) = ?"
        params = [*params, segment]
    query = _sql("daily_revenue").format(line_facts=_sql("line_facts"), segment_predicate=predicate)
    return conn.execute(query, params).fetchdf()


def monthly_kpis(
    conn: duckdb.DuckDBPyConnection, start: date | str, end: date | str
) -> pd.DataFrame:
    """Return calendar-month KPIs and fractional MoM and YoY growth.

    Growth uses full month comparators from the complete snapshot even when
    they precede the requested window. Partial months have no growth result.
    """
    return conn.execute(_kpi_query("monthly_kpis"), _bounds(conn, start, end)).fetchdf()


def customer_cohorts(
    conn: duckdb.DuckDBPyConnection, start: date | str, end: date | str
) -> pd.DataFrame:
    """Return observed purchase activity by cohort month and calendar age.

    Repeat customers place an order after their first within the activity
    month. Cumulative repeats count members whose second purchase occurred
    by the end of that month. Acquisition dates are independent of cohorts.
    """
    query = _sql("customer_cohorts").format(line_facts=_sql("line_facts"))
    return conn.execute(query, _bounds(conn, start, end)).fetchdf()

"""Synthetic posted-day revenue; independent of retrospective refund attribution."""

from datetime import datetime, time

import pandas as pd

from .analytics import _bounds, _sql
from .db import metadata

POSTED_METRIC = "posted_day_net_activity"
RETROSPECTIVE_METRIC = "retrospective_order_day_net_revenue"
SYNTHETIC_STATUS_WARNING = (
    "Synthetic checkout outcomes are known at order time. Real data needs timestamped "
    "recognition, cancellation and ingestion history to reproduce what operators saw."
)


def posted_activity(conn, start, end, *, cutoff: datetime | None = None, dimension: str | None = None,
                    segment: str | None = None):
    """Return daily totals and long-form segment facts through an exclusive UTC cutoff.

    In this generator, completed vs canceled/pending is set at checkout. Snapshot
    partially/fully refunded labels both imply a completed booking, but their refund
    amount enters only on the independently timestamped refund day. This contract
    is not suitable for an arbitrary database with untimestamped status transitions.
    """
    start, end = _bounds(conn, start, end)
    meta = metadata(conn)
    if cutoff is None:
        cutoff = meta.data_as_of
    if not isinstance(cutoff, datetime) or cutoff.tzinfo is not None or not (
        datetime.combine(meta.coverage_start, time.min) <= cutoff <= meta.data_as_of
    ):
        raise ValueError("Cutoff must be a naive UTC datetime within the available snapshot.")
    if (dimension is None) != (segment is None) or dimension not in {None, "product", "region", "channel"}:
        raise ValueError("Choose a supported dimension and its segment together.")
    facts = conn.execute(_sql("posted_activity"), [start, end, cutoff, dimension, segment]).fetchdf()
    dates = pd.date_range(start, end, inclusive="left")
    spine = pd.DataFrame({"date": dates, "is_complete": dates + pd.Timedelta(days=1) <= cutoff})
    columns = ["date", "net_revenue_cents", "orders"]
    facts[columns[1:]] = facts[columns[1:]].astype("Int64")
    overall = spine.merge(facts.loc[facts.dimension.eq("overall"), columns], on="date", how="left")
    for column in columns[1:]:
        overall[column] = overall[column].fillna(0).astype("int64")
    return overall, facts.loc[facts.dimension.ne("overall")].reset_index(drop=True)


def segment_series(daily, segment_daily, dimension: str, segment: str) -> pd.DataFrame:
    """Align sparse segment events to the full calendar; preserve coverage flags."""
    facts = segment_daily.loc[
        segment_daily.dimension.eq(dimension) & segment_daily.segment.eq(segment),
        ["date", "net_revenue_cents", "orders"],
    ].astype({"net_revenue_cents": "Int64", "orders": "Int64"})
    result = daily[["date", "is_complete"]].merge(facts, on="date", how="left", validate="one_to_one")
    for column in ("net_revenue_cents", "orders"):
        result[column] = result[column].fillna(0).astype("int64")
    return result

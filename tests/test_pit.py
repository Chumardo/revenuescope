from datetime import datetime

import duckdb
import pytest

from revenuescope.analytics import daily_revenue
from revenuescope.db import initialize_schema
from revenuescope.pit import posted_activity, segment_series


@pytest.fixture
def snapshot():
    with duckdb.connect() as conn:
        initialize_schema(conn)
        conn.execute("""
            INSERT INTO dataset_meta VALUES ('2024-01-01','2024-01-06','2024-01-06',1);
            INSERT INTO customers VALUES (1,'2023-12-01');
            INSERT INTO products VALUES (1,'Desk','Office'), (2,'Chair','Office');
            INSERT INTO orders VALUES
                (1,1,'2024-01-01 10:00','North','Web','partially_refunded'),
                (2,1,'2024-01-02 11:00','South','Mobile','canceled'),
                (3,1,'2024-01-03 12:00','South','Web','completed');
            INSERT INTO order_lines VALUES
                (1,1,1,2,700,100,400), (2,1,2,1,900,0,300), (3,2,2,1,500,0,200), (4,3,2,1,2400,0,1000);
            INSERT INTO refunds VALUES (1,1,'2024-01-02 09:00',400,0), (2,1,'2024-01-04 00:00',600,1);
        """)
        yield conn


def test_posted_refunds_do_not_restate_closed_booking_days(snapshot):
    daily, long = posted_activity(snapshot, "2024-01-01", "2024-01-06")
    assert daily.net_revenue_cents.tolist() == [2200, -400, 2400, -600, 0]
    assert daily.orders.tolist() == [1, 0, 1, 0, 0]
    original = daily_revenue(snapshot, "2024-01-01", "2024-01-06")
    assert original.net_revenue_cents.tolist() == [1200, 0, 2400, 0, 0]
    assert sum(daily.net_revenue_cents) == sum(original.net_revenue_cents) == 3600
    for dimension in ("region", "channel", "product"):
        assert long.loc[long.dimension.eq(dimension)].net_revenue_cents.sum() == 3600
    north = segment_series(daily, long, "region", "North")
    assert north.net_revenue_cents.tolist() == [2200, -400, 0, -600, 0]
    scoped, detail = posted_activity(snapshot, "2024-01-01", "2024-01-06", dimension="region", segment="North")
    assert scoped.net_revenue_cents.tolist() == north.net_revenue_cents.tolist()
    assert detail.loc[detail.dimension.eq("product")].net_revenue_cents.sum() == 1200


def test_posted_exclusive_cutoff_and_partial_day(snapshot):
    daily, _ = posted_activity(snapshot, "2024-01-01", "2024-01-06", cutoff=datetime(2024, 1, 4))
    assert daily.net_revenue_cents.tolist() == [2200, -400, 2400, 0, 0]
    assert daily.is_complete.tolist() == [True, True, True, False, False]
    partial, _ = posted_activity(snapshot, "2024-01-01", "2024-01-06", cutoff=datetime(2024, 1, 4, 1))
    assert partial.net_revenue_cents.tolist() == [2200, -400, 2400, -600, 0]
    assert partial.is_complete.tolist() == daily.is_complete.tolist()
    with pytest.raises(ValueError, match="within the available snapshot"):
        posted_activity(snapshot, "2024-01-01", "2024-01-06", cutoff=datetime(2024, 1, 7))

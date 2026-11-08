from datetime import date, datetime
from numbers import Integral

import duckdb
import pandas as pd
import pytest

from revenuescope.analytics import customer_cohorts, daily_revenue, monthly_kpis, summarize
from revenuescope.db import initialize_schema


@pytest.fixture
def audit():
    """Small sales ledger with independently calculable totals and repeat behavior."""
    with duckdb.connect() as conn:
        initialize_schema(conn)
        conn.execute("""
            INSERT INTO dataset_meta (coverage_start, coverage_end, data_as_of, seed)
            VALUES ('2023-01-01', '2024-05-01', '2024-04-15 12:00:00', 17)
        """)
        conn.execute("""
            INSERT INTO customers (customer_id, acquired_at) VALUES
            (1, '2022-12-15'), (2, '2023-01-02'), (3, '2023-12-31'), (4, '2023-03-01');
            INSERT INTO products (product_id, product_name, category) VALUES
            (1, 'Press', 'Equipment'), (2, 'Towel', 'Home');
            INSERT INTO orders (order_id, customer_id, ordered_at, region, channel, status) VALUES
            (1, 1, '2023-01-01', 'East', 'Web', 'canceled'),
            (2, 2, '2023-01-02', 'West', 'Marketplace', 'pending'),
            (11, 1, '2023-01-05', 'West', 'Web', 'completed'),
            (10, 1, '2023-01-05', 'East', 'Web', 'partially_refunded'),
            (20, 2, '2023-01-15', 'West', 'Marketplace', 'refunded'),
            (12, 1, '2023-02-15', 'East', 'Web', 'completed'),
            (21, 2, '2023-03-10', 'West', 'Marketplace', 'completed'),
            (40, 4, '2023-03-10', 'West', 'Web', 'completed'),
            (30, 3, '2024-01-01', 'East', 'Web', 'completed'),
            (31, 3, '2024-04-15 09:00:00', 'East', 'Web', 'completed'),
            (32, 3, '2024-04-15 12:00:00', 'East', 'Web', 'completed'),
            (33, 3, '2024-04-16', 'East', 'Web', 'completed');
            INSERT INTO order_lines (
                line_id, order_id, product_id, quantity, unit_price_cents, discount_cents, unit_cost_cents
            ) VALUES
            (1, 1, 1, 1, 100000, 0, 50000), (2, 2, 1, 1, 100000, 0, 50000),
            (110, 11, 1, 1, 700, 0, 300),
            (100, 10, 1, 2, 1000, 200, 400), (101, 10, 2, 1, 500, 0, 200),
            (200, 20, 1, 1, 900, 0, 300),
            (120, 12, 1, 1, 1000, 0, 400),
            (210, 21, 2, 1, 600, 0, 200), (400, 40, 2, 1, 500, 0, 200),
            (300, 30, 1, 1, 2500, 0, 700),
            (310, 31, 2, 1, 300, 0, 100),
            (320, 32, 2, 1, 900, 0, 100), (330, 33, 2, 1, 900, 0, 100);
            INSERT INTO refunds (refund_id, line_id, refunded_at, refund_cents, returned_quantity) VALUES
            (1, 100, '2023-01-20', 900, 1), (2, 100, '2023-02-01', 100, 0),
            (3, 100, '2024-04-15 12:00:00', 800, 1),
            (4, 200, '2023-01-16', 900, 1);
        """)
        yield conn


def test_reconciles_financial_metrics_and_refund_grain(audit):
    metrics = summarize(audit, "2023-01-01", "2023-02-01").to_dict("records")[0]
    assert metrics == pytest.approx({
        "gross_sales_cents": 4100,
        "discounts_cents": 200,
        "refunds_cents": 1900,
        "net_revenue_cents": 2000,
        "cogs_cents": 900,
        "gross_profit_cents": 1100,
        "orders": 3,
        "active_customers": 2,
        "new_customers": 2,
        "returning_customers": 0,
        "new_customer_orders": 2,
        "returning_customer_orders": 1,
        "new_customer_net_revenue_cents": 1300,
        "returning_customer_net_revenue_cents": 700,
        "average_order_value_cents": 2000 / 3,
        "revenue_per_customer_cents": 1000,
        "gross_margin": .55,
        "net_revenue_share": 1.0,
    })
    assert all(isinstance(metrics[k], Integral) for k in (
        "gross_sales_cents", "discounts_cents", "refunds_cents", "net_revenue_cents",
        "cogs_cents", "gross_profit_cents",
    ))
    feb = summarize(audit, "2023-02-01", "2023-03-01").to_dict("records")[0]
    assert (feb["refunds_cents"], feb["net_revenue_cents"], feb["returning_customers"]) == (0, 1000, 1)


def test_canceled_and_pending_orders_do_not_open_cohorts_or_contribute(audit):
    first_days = summarize(audit, "2023-01-01", "2023-01-05").to_dict("records")[0]
    assert (first_days["orders"], first_days["active_customers"], first_days["net_revenue_cents"]) == (0, 0, 0)
    assert pd.isna(first_days["average_order_value_cents"])
    assert pd.isna(first_days["revenue_per_customer_cents"])
    assert pd.isna(first_days["gross_margin"])

    refunded = summarize(audit, "2023-01-15", "2023-01-16").to_dict("records")[0]
    assert (refunded["net_revenue_cents"], refunded["cogs_cents"], refunded["gross_profit_cents"]) == (0, 0, 0)
    assert (refunded["orders"], refunded["new_customers"], refunded["average_order_value_cents"]) == (1, 1, 0)
    assert pd.isna(refunded["gross_margin"])


def test_segment_totals_add_but_distinct_counts_can_overlap(audit):
    start, end = "2023-01-01", "2023-02-01"
    categories = summarize(audit, start, end, "category").set_index("segment")
    assert categories["net_revenue_cents"].to_dict() == {"Equipment": 1500, "Home": 500}
    assert categories["net_revenue_share"].to_dict() == {"Equipment": .75, "Home": .25}
    assert categories["orders"].to_dict() == {"Equipment": 3, "Home": 1}
    assert categories["active_customers"].to_dict() == {"Equipment": 2, "Home": 1}
    assert summarize(audit, start, end, "region").set_index("segment")["net_revenue_cents"].to_dict() == {
        "East": 1300, "West": 700,
    }
    assert summarize(audit, start, end, "channel").set_index("segment")["net_revenue_cents"].to_dict() == {
        "Marketplace": 0, "Web": 2000,
    }
    assert summarize(audit, start, end, "product").set_index("segment")["net_revenue_cents"].to_dict() == {
        "Press [#1]": 1500, "Towel [#2]": 500,
    }


def test_daily_spine_half_open_filter_and_incomplete_cutoff(audit):
    data = daily_revenue(audit, "2023-01-04", "2023-01-06", "region", "East")
    assert data["date"].dt.date.tolist() == [date(2023, 1, 4), date(2023, 1, 5)]
    assert data["net_revenue_cents"].tolist() == [0, 1300]
    assert data["orders"].tolist() == [0, 1]
    assert data["is_complete"].tolist() == [True, True]

    cutoff = daily_revenue(audit, "2024-04-14", "2024-04-17")
    assert cutoff["net_revenue_cents"].tolist() == [0, 300, 0]
    assert cutoff["orders"].tolist() == [0, 1, 0]
    assert cutoff["is_complete"].tolist() == [True, False, False]


def test_month_growth_uses_full_calendar_comparators(audit):
    data = monthly_kpis(audit, "2023-01-01", "2024-05-01").set_index("month")
    assert len(data) == 16
    assert data.loc["2023-01-01", "net_revenue_cents"] == 2000
    assert pd.isna(data.loc["2023-01-01", "mom_growth"])
    assert data.loc["2023-02-01", "mom_growth"] == pytest.approx(-.5)
    assert data.loc["2023-03-01", "mom_growth"] == pytest.approx(.1)
    assert data.loc["2023-04-01", "mom_growth"] == -1
    assert pd.isna(data.loc["2023-05-01", "mom_growth"])
    assert data.loc["2024-01-01", "yoy_growth"] == pytest.approx(.25)
    assert data.loc["2024-02-01", "yoy_growth"] == -1
    assert not data.loc["2024-04-01", "is_complete"]
    assert data.loc["2024-04-01", "net_revenue_cents"] == 300
    assert pd.isna(data.loc["2024-04-01", "mom_growth"])
    assert pd.isna(data.loc["2024-04-01", "yoy_growth"])

    feb = monthly_kpis(audit, "2023-02-01", "2023-03-01").to_dict("records")[0]
    assert feb["mom_growth"] == pytest.approx(-.5)
    jan_partial = monthly_kpis(audit, "2023-01-10", "2023-03-01").to_dict("records")[0]
    assert not jan_partial["is_complete"]
    assert jan_partial["net_revenue_cents"] == 0
    assert jan_partial["orders"] == 1
    assert pd.isna(jan_partial["mom_growth"])


def test_repeat_cohorts_distinguish_zero_activity_from_unobserved_periods(audit):
    cohorts = customer_cohorts(audit, "2023-01-01", "2024-05-01")
    january = cohorts[cohorts["cohort_month"].dt.date == date(2023, 1, 1)].set_index("age_months")
    columns = ["cohort_size", "active_customers", "repeat_customers", "cumulative_repeat_customers"]
    assert january.loc[0:3, columns].values.tolist() == [
        [2, 2, 1, 1], [2, 1, 1, 1], [2, 1, 1, 2], [2, 0, 0, 2],
    ]
    assert january["retention_rate"].iloc[:4].tolist() == [1.0, .5, .5, 0]
    assert january["cumulative_repeat_rate"].iloc[:4].tolist() == [.5, .5, 1.0, 1.0]
    recent = cohorts[cohorts["cohort_month"].dt.date == date(2024, 1, 1)]
    assert recent["age_months"].tolist() == [0, 1, 2]
    assert recent["active_customers"].tolist() == [1, 0, 0]
    cropped = customer_cohorts(audit, "2023-01-07", "2023-04-01")
    assert cropped["cohort_month"].dt.date.tolist() == [date(2023, 3, 1)]


def test_money_above_binary_float_exact_range_stays_integer(audit):
    audit.execute("""
        INSERT INTO orders (order_id, customer_id, ordered_at, region, channel, status)
        VALUES (50, 1, '2023-06-15', 'East', 'Web', 'completed');
        INSERT INTO order_lines (
            line_id, order_id, product_id, quantity, unit_price_cents, discount_cents, unit_cost_cents
        ) VALUES (500, 50, 1, 1, 9007199254740993, 0, 0);
    """)
    expected = 9007199254740993
    assert summarize(audit, "2023-06-01", "2023-07-01")["net_revenue_cents"].tolist() == [expected]
    assert daily_revenue(audit, "2023-06-15", "2023-06-16")["net_revenue_cents"].tolist() == [expected]
    assert monthly_kpis(audit, "2023-06-01", "2023-07-01")["net_revenue_cents"].tolist() == [expected]


def test_period_and_segment_validation(audit):
    with pytest.raises(ValueError, match="inside"):
        summarize(audit, "2022-12-01", "2023-01-02")
    with pytest.raises(ValueError, match="precede"):
        summarize(audit, "2023-01-01", "2023-01-01")
    with pytest.raises(TypeError, match="dates"):
        summarize(audit, datetime(2023, 1, 1), "2023-01-02")
    with pytest.raises(ValueError, match="Unknown dimension"):
        summarize(audit, "2023-01-01", "2023-02-01", "f.category")
    with pytest.raises(ValueError, match="both dimension and segment"):
        daily_revenue(audit, "2023-01-01", "2023-01-02", "region")
    assert daily_revenue(audit, "2023-01-05", "2023-01-06", "region", "' OR 1=1")["orders"].tolist() == [0]

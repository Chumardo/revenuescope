from datetime import date
from fractions import Fraction

import duckdb
import pytest

from revenuescope.db import initialize_schema
from revenuescope.variance import Period, compare_periods, revenue_bridge


def test_bridge_preserves_fractional_cent_effects_exactly():
    bridge = revenue_bridge(3, 100, 7, 201)
    assert bridge.baseline_aov_cents == Fraction(100, 3)
    assert bridge.current_aov_cents == Fraction(201, 7)
    assert bridge.volume_effect_cents == Fraction(2606, 21)
    assert bridge.aov_effect_cents == Fraction(-485, 21)
    assert bridge.revenue_change_cents == 101


@pytest.mark.parametrize(("n0", "r0", "n1", "r1", "expected"), [
    (0, 0, 2, 510, 510),
    (2, 510, 0, 0, -510),
    (0, 0, 0, 0, 0),
])
def test_zero_orders_do_not_imply_an_observed_aov(n0, r0, n1, r1, expected):
    bridge = revenue_bridge(n0, r0, n1, r1)
    assert bridge.volume_effect_cents == expected
    assert bridge.aov_effect_cents == 0
    if n0 == 0:
        assert bridge.baseline_aov_cents is None
    if n1 == 0:
        assert bridge.current_aov_cents is None


@pytest.fixture
def store():
    with duckdb.connect() as conn:
        initialize_schema(conn)
        conn.execute("""
            INSERT INTO dataset_meta VALUES ('2024-01-01', '2024-04-01', '2024-03-15 12:00:00', 1);
            INSERT INTO customers VALUES (1, '2023-12-30'), (2, '2023-12-29'), (3, '2024-02-01');
            INSERT INTO products VALUES (1, 'Alpha', 'A'), (2, 'Beta', 'B'), (3, 'Gamma', 'C');
            INSERT INTO orders VALUES
                (1, 1, '2024-01-05', 'East', 'Web', 'completed'),
                (2, 2, '2024-01-07', 'West', 'Web', 'completed'),
                (3, 1, '2024-02-03', 'East', 'App', 'completed'),
                (4, 2, '2024-02-08', 'West', 'App', 'completed'),
                (5, 3, '2024-02-11', 'West', 'Web', 'completed');
            INSERT INTO order_lines VALUES
                (1, 1, 1, 1, 400, 0, 30), (2, 1, 2, 1, 600, 0, 40),
                (3, 2, 1, 1, 1000, 0, 50),
                (4, 3, 1, 1, 2000, 0, 60), (5, 3, 3, 1, 1000, 0, 70),
                (6, 4, 1, 1, 400, 0, 80), (7, 5, 3, 1, 1100, 0, 90);
        """)
        yield conn


def test_multi_category_order_and_new_or_disappearing_segments_reconcile(store):
    jan, feb, mar = (date(2024, m, 1) for m in (1, 2, 3))
    result = compare_periods(store, Period(jan, feb), Period(feb, mar))
    assert (result.bridge.baseline_orders, result.bridge.current_orders) == (2, 3)
    assert (result.bridge.baseline_revenue_cents, result.bridge.current_revenue_cents) == (2000, 4500)
    assert result.bridge.volume_effect_cents == 1250
    assert result.bridge.aov_effect_cents == 1250
    assert [(r.segment, r.revenue_change_cents) for r in result.contributions["category"]] == [
        ("C", 2100), ("A", 1000), ("B", -600),
    ]
    assert {r.segment: r.revenue_change_cents for r in result.contributions["channel"]} == {
        "App": 3400, "Web": -900,
    }
    assert {r.segment: r.revenue_change_cents for r in result.contributions["region"]} == {
        "East": 2000, "West": 500,
    }


def test_overlapping_and_incomplete_windows_are_rejected(store):
    jan, feb, mar, apr = (date(2024, m, 1) for m in (1, 2, 3, 4))
    with pytest.raises(ValueError, match="on or before"):
        compare_periods(store, Period(jan, mar), Period(feb, mar))
    with pytest.raises(ValueError, match="fully observed"):
        compare_periods(store, Period(feb, mar), Period(mar, apr))


def test_bridge_rejects_impossible_zero_order_totals():
    with pytest.raises(ValueError, match="zero orders require zero revenue"):
        revenue_bridge(0, 1, 1, 2)


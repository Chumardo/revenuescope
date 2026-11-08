"""Independent checks for reproducibility and known synthetic interventions."""

from datetime import date, datetime, timedelta

import duckdb
import pytest

from revenuescope.generator import generate

TABLES = ("dataset_meta", "customers", "products", "orders", "order_lines", "refunds")


def _logical_rows(conn, table):
    return conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()


def _daily_purchases(conn):
    """Independently subtract refunds from revenue without multiplying line charges."""
    revenue = dict(conn.execute("""
        SELECT o.ordered_at::DATE, sum(l.quantity * l.unit_price_cents - l.discount_cents)
        FROM orders o JOIN order_lines l USING (order_id)
        WHERE o.status NOT IN ('canceled', 'pending') GROUP BY 1
    """).fetchall())
    refunds = conn.execute("""
        SELECT o.ordered_at::DATE, sum(r.refund_cents)
        FROM refunds r JOIN order_lines l USING (line_id) JOIN orders o USING (order_id)
        GROUP BY 1
    """).fetchall()
    for day, cents in refunds:
        revenue[day] -= cents
    orders = dict(conn.execute("""
        SELECT ordered_at::DATE, count(*)
        FROM orders WHERE status NOT IN ('canceled', 'pending') GROUP BY 1
    """).fetchall())
    return revenue, orders


@pytest.fixture(scope="module")
def demo_pair():
    with duckdb.connect() as synthetic, duckdb.connect() as counterfactual:
        manifest = generate(synthetic)
        generate(counterfactual, inject_anomalies=False)
        yield synthetic, counterfactual, manifest


def test_same_seed_reproduces_all_logical_rows_and_different_seed_changes_transactions():
    settings = {"start": date(2024, 2, 1), "months": 1}
    with duckdb.connect() as first, duckdb.connect() as second, duckdb.connect() as other:
        manifest = generate(first, seed=17, **settings)
        assert manifest == generate(second, seed=17, **settings)
        generate(other, seed=18, **settings)
        for table in TABLES:
            assert _logical_rows(first, table) == _logical_rows(second, table), table
        assert _logical_rows(first, "order_lines") != _logical_rows(other, "order_lines")
        assert _logical_rows(first, "dataset_meta") == [
            (date(2024, 2, 1), date(2024, 3, 1), datetime(2024, 3, 1), 17),
        ]
        assert first.execute("SELECT count(DISTINCT ordered_at::DATE) FROM orders").fetchone() == (29,)


def test_dataset_contains_realistic_grains_and_balanced_refunds(demo_pair):
    synthetic, _, manifest = demo_pair
    assert _logical_rows(synthetic, "dataset_meta") == [
        (date(2023, 1, 1), date(2025, 1, 1), datetime(2025, 1, 1), 202501),
    ]
    assert manifest["synthetic"] is True
    assert manifest["currency"] == "USD"
    assert manifest["row_counts"] == {
        table: synthetic.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in TABLES if table != "dataset_meta"
    }
    assert manifest["row_counts"]["orders"] > 25_000
    assert synthetic.execute("""
        SELECT count(DISTINCT ordered_at::DATE), count(DISTINCT region), count(DISTINCT channel),
               count(DISTINCT status), count(*) FILTER (WHERE ordered_at < acquired_at)
        FROM orders JOIN customers USING (customer_id)
    """).fetchone() == (731, 5, 3, 5, 0)
    assert synthetic.execute("""
        SELECT count(*) FROM (
            SELECT o.order_id FROM orders o LEFT JOIN order_lines l USING (order_id)
            GROUP BY o.order_id HAVING count(l.line_id) = 0
        )
    """).fetchone() == (0,)
    assert synthetic.execute("""
        SELECT count(*) FROM refunds r
        JOIN order_lines l USING (line_id) JOIN orders o USING (order_id)
        WHERE refunded_at < ordered_at OR refunded_at >= TIMESTAMP '2025-01-01'
              OR o.status IN ('canceled', 'pending')
    """).fetchone() == (0,)
    assert synthetic.execute("""
        SELECT count(*) FROM (
            SELECT r.line_id FROM refunds r JOIN order_lines l USING (line_id)
            GROUP BY r.line_id, l.quantity, l.unit_price_cents, l.discount_cents
            HAVING sum(r.refund_cents) > l.quantity * l.unit_price_cents - l.discount_cents
                OR sum(r.returned_quantity) > l.quantity
        )
    """).fetchone() == (0,)
    assert synthetic.execute("""
        SELECT count(*) FROM (SELECT line_id FROM refunds GROUP BY line_id HAVING count(*) > 1)
    """).fetchone()[0] > 0


def test_manifest_measures_every_injection_without_changing_non_event_days(demo_pair):
    synthetic, counterfactual, manifest = demo_pair
    baseline_revenue, baseline_orders = _daily_purchases(counterfactual)
    observed_revenue, observed_orders = _daily_purchases(synthetic)
    expected_signs = {
        "mobile-acquisition-campaign": 1, "owned-checkout-outage": -1, "warehouse-returns-wave": -1,
        "premium-bundle-feature": 1, "mobile-coupon-over-redemption": -1,
    }
    assert {item["id"] for item in manifest["anomalies"]} == set(expected_signs)
    touched_days = set()
    for item in manifest["anomalies"]:
        start, end = date.fromisoformat(item["start"]), date.fromisoformat(item["end"])
        days = {start + timedelta(days=i) for i in range((end - start).days)}
        assert days and not touched_days.intersection(days)
        touched_days.update(days)
        baseline = sum(baseline_revenue.get(day, 0) for day in days)
        observed = sum(observed_revenue.get(day, 0) for day in days)
        assert (baseline, observed) == (item["baseline_net_revenue_cents"], item["observed_net_revenue_cents"])
        assert observed - baseline == item["net_revenue_delta_cents"]
        assert (observed - baseline) * expected_signs[item["id"]] > 0
        assert item["affected_orders"] > 0
        before = sum(baseline_orders.get(day, 0) for day in days)
        after = sum(observed_orders.get(day, 0) for day in days)
        assert (before, after) == (item["baseline_recognized_orders"], item["observed_recognized_orders"])
        if item["id"] == "mobile-acquisition-campaign":
            assert after - before == item["affected_orders"]
        elif item["id"] == "owned-checkout-outage":
            assert before - after == item["affected_orders"]
        else:
            assert before == after
    for day in set(baseline_revenue) - touched_days:
        assert (observed_revenue[day], observed_orders[day]) == (baseline_revenue[day], baseline_orders[day])


def test_baseline_exhibits_demand_product_mix_and_repeat_purchase_patterns(demo_pair):
    _, counterfactual, _ = demo_pair
    weekday = dict(counterfactual.execute("""
        WITH daily AS (SELECT ordered_at::DATE AS day, count(*) AS orders FROM orders GROUP BY 1)
        SELECT dayofweek(day), avg(orders) FROM daily GROUP BY 1
    """).fetchall())
    month = dict(counterfactual.execute("""
        WITH daily AS (SELECT ordered_at::DATE AS day, count(*) AS orders FROM orders GROUP BY 1)
        SELECT month(day), avg(orders) FROM daily GROUP BY 1
    """).fetchall())
    assert weekday[5] > 1.1 * weekday[0]  # Friday vs. Sunday
    assert month[11] > 1.2 * month[1]    # November vs. January
    outdoor_mix = dict(counterfactual.execute("""
        WITH baskets AS (
            SELECT category,
                   CASE WHEN month(ordered_at) IN (5, 6, 7, 8) THEN 'warm'
                        WHEN month(ordered_at) IN (11, 12, 1, 2) THEN 'cold' END AS season
            FROM orders JOIN order_lines USING (order_id) JOIN products USING (product_id)
        )
        SELECT season, count(*) FILTER (WHERE category = 'Outdoor')::DOUBLE / count(*)
        FROM baskets WHERE season IS NOT NULL GROUP BY 1
    """).fetchall())
    assert outdoor_mix["warm"] > 1.2 * outdoor_mix["cold"]
    repeat_share = counterfactual.execute("""
        WITH purchaser_orders AS (
            SELECT customer_id, count(*) AS orders FROM orders
            WHERE status NOT IN ('canceled', 'pending') GROUP BY 1
        )
        SELECT count(*) FILTER (WHERE orders > 1)::DOUBLE / count(*) FROM purchaser_orders
    """).fetchone()[0]
    assert 0.50 < repeat_share < 0.95


@pytest.mark.parametrize(
    ("settings", "message"), [
        ({"start": date(2024, 1, 15)}, "first day"),
        ({"months": 0}, "months"),
        ({"seed": 2**63}, "seed"),
    ],
)
def test_invalid_generator_inputs_are_rejected(settings, message):
    with duckdb.connect() as conn, pytest.raises(ValueError, match=message):
        generate(conn, **settings)

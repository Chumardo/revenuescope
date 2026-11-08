WITH
bounds AS (SELECT ?::DATE AS start_date, ?::DATE AS end_date),
{line_facts},
month_spine AS (
    SELECT d::DATE AS month,
           GREATEST(d::DATE, b.start_date) AS start_date,
           LEAST((d + INTERVAL '1 month')::DATE, b.end_date) AS end_date,
           d >= b.start_date
               AND d + INTERVAL '1 month' <= b.end_date
               AND d + INTERVAL '1 month' <= m.data_as_of AS is_complete
    FROM bounds AS b
    CROSS JOIN dataset_meta AS m,
    LATERAL GENERATE_SERIES(DATE_TRUNC('month', b.start_date),
                            DATE_TRUNC('month', b.end_date - 1), INTERVAL '1 month') AS months(d)
),
reported AS (
    SELECT b.month, b.is_complete,
           {kpi_sums}
    FROM month_spine AS b
    LEFT JOIN line_facts AS f ON f.order_date >= b.start_date AND f.order_date < b.end_date
    GROUP BY b.month, b.is_complete
),
-- Comparison months can precede the requested window. Only full months in
-- the snapshot are eligible and missing activity in covered months is zero.
comparison_totals AS (
    SELECT DATE_TRUNC('month', order_date)::DATE AS month,
           SUM(net_revenue_cents)::BIGINT AS comparison_revenue_cents
    FROM line_facts
    GROUP BY 1
)
SELECT r.*,
       {kpi_ratios},
       CASE WHEN r.is_complete
                  AND r.month - INTERVAL '1 month' >= m.coverage_start
                  AND p.comparison_revenue_cents > 0
            THEN (r.net_revenue_cents::HUGEINT - p.comparison_revenue_cents)::DOUBLE / p.comparison_revenue_cents
       END AS mom_growth,
       CASE WHEN r.is_complete
                  AND r.month - INTERVAL '1 year' >= m.coverage_start
                  AND y.comparison_revenue_cents > 0
            THEN (r.net_revenue_cents::HUGEINT - y.comparison_revenue_cents)::DOUBLE / y.comparison_revenue_cents
       END AS yoy_growth
FROM reported AS r
CROSS JOIN dataset_meta AS m
LEFT JOIN comparison_totals AS p ON p.month = r.month - INTERVAL '1 month'
LEFT JOIN comparison_totals AS y ON y.month = r.month - INTERVAL '1 year'
ORDER BY r.month

WITH
bounds AS (SELECT ?::DATE AS start_date, ?::DATE AS end_date),
{line_facts},
cohort_members AS (
    SELECT o.customer_id, DATE_TRUNC('month', o.ordered_at)::DATE AS cohort_month
    FROM ranked_orders AS o
    CROSS JOIN bounds AS b
    CROSS JOIN dataset_meta AS m
    WHERE o.customer_order_number = 1
      AND DATE_TRUNC('month', o.ordered_at) >= b.start_date
      AND DATE_TRUNC('month', o.ordered_at) + INTERVAL '1 month' <= b.end_date
      AND DATE_TRUNC('month', o.ordered_at) + INTERVAL '1 month' <= m.data_as_of
),
cohort_sizes AS (
    SELECT cohort_month, COUNT(*) AS cohort_size
    FROM cohort_members
    GROUP BY cohort_month
),
cohort_grid AS (
    SELECT s.cohort_month, d::DATE AS activity_month, s.cohort_size
    FROM cohort_sizes AS s
    CROSS JOIN bounds AS b
    CROSS JOIN dataset_meta AS m,
    LATERAL GENERATE_SERIES(s.cohort_month,
        LEAST(b.end_date::TIMESTAMP, m.data_as_of) - INTERVAL '1 month',
        INTERVAL '1 month') AS months(d)
),
monthly_activity AS (
    SELECT c.cohort_month, DATE_TRUNC('month', o.ordered_at)::DATE AS activity_month,
           COUNT(DISTINCT c.customer_id) AS active_customers,
           COUNT(DISTINCT c.customer_id) FILTER (
               WHERE o.customer_order_number > 1
           ) AS repeat_customers,
           COUNT(DISTINCT c.customer_id) FILTER (
               WHERE o.customer_order_number = 2
           ) AS first_repeat_customers
    FROM cohort_members AS c
    JOIN ranked_orders AS o ON o.customer_id = c.customer_id
    GROUP BY c.cohort_month, activity_month
),
cohort_metrics AS (
    SELECT g.cohort_month,
           DATE_DIFF('month', g.cohort_month, g.activity_month) AS age_months,
           g.activity_month, g.cohort_size,
           COALESCE(a.active_customers, 0) AS active_customers,
           COALESCE(a.repeat_customers, 0) AS repeat_customers,
           SUM(COALESCE(a.first_repeat_customers, 0)) OVER (
               PARTITION BY g.cohort_month ORDER BY g.activity_month
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           )::BIGINT AS cumulative_repeat_customers
    FROM cohort_grid AS g
    LEFT JOIN monthly_activity AS a USING (cohort_month, activity_month)
)
SELECT *,
       active_customers::DOUBLE / cohort_size AS retention_rate,
       cumulative_repeat_customers::DOUBLE / cohort_size AS cumulative_repeat_rate
FROM cohort_metrics
ORDER BY cohort_month, age_months

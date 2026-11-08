WITH
bounds AS (SELECT ?::DATE AS start_date, ?::DATE AS end_date),
{line_facts},
calendar AS (
    SELECT d::DATE AS date,
           d + INTERVAL '1 day' <= m.data_as_of AS is_complete
    FROM bounds AS b
    CROSS JOIN dataset_meta AS m,
    LATERAL GENERATE_SERIES(b.start_date, b.end_date - 1, INTERVAL '1 day') AS dates(d)
),
daily AS (
    SELECT f.order_date AS date,
           SUM(f.net_revenue_cents)::BIGINT AS net_revenue_cents,
           COUNT(DISTINCT f.order_id) AS orders
    FROM line_facts AS f
    CROSS JOIN bounds AS b
    WHERE f.order_date >= b.start_date AND f.order_date < b.end_date
      {segment_predicate}
    GROUP BY f.order_date
)
SELECT c.date,
       COALESCE(d.net_revenue_cents, 0) AS net_revenue_cents,
       COALESCE(d.orders, 0) AS orders,
       c.is_complete
FROM calendar AS c
LEFT JOIN daily AS d USING (date)
ORDER BY c.date

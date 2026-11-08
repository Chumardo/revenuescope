WITH
bounds AS (SELECT ?::DATE AS start_date, ?::DATE AS end_date),
{line_facts},
aggregated AS (
    SELECT {segment_projection}
           {kpi_sums}
    FROM bounds AS b
    {line_join} line_facts AS f
      ON f.order_date >= b.start_date AND f.order_date < b.end_date
    {segment_group}
)
SELECT *,
       {kpi_ratios},
       CASE WHEN SUM(net_revenue_cents) OVER () > 0
            THEN net_revenue_cents::DOUBLE / SUM(net_revenue_cents) OVER ()
       END AS net_revenue_share
FROM aggregated
{segment_order}

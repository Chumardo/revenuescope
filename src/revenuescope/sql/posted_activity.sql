-- Valid for the synthetic snapshot's checkout-time status contract only.
WITH bounds AS (SELECT ?::DATE AS start_date, ?::DATE AS end_date, ?::TIMESTAMP AS cutoff,
                       ?::VARCHAR AS scope_dimension, ?::VARCHAR AS scope_segment),
booked_lines AS (
    SELECT o.order_id, o.ordered_at, o.region, o.channel, l.line_id,
           p.product_name || ' [#' || p.product_id::VARCHAR || ']' AS product,
           l.quantity * l.unit_price_cents - l.discount_cents AS booked_cents
    FROM orders o
    JOIN order_lines l USING (order_id)
    JOIN products p USING (product_id)
    CROSS JOIN dataset_meta m
    WHERE o.status IN ('completed', 'partially_refunded', 'refunded')
      AND o.ordered_at >= m.coverage_start AND o.ordered_at < m.coverage_end
      AND o.ordered_at < m.data_as_of
), events AS (
    SELECT ordered_at AS posted_at, region, channel, product, order_id AS booking_id,
           booked_cents AS cents FROM booked_lines
    UNION ALL
    SELECT r.refunded_at, l.region, l.channel, l.product, NULL, -r.refund_cents
    FROM refunds r JOIN booked_lines l USING (line_id)
), in_window AS (
    SELECT posted_at::DATE AS date, region, channel, product, booking_id, cents
    FROM events CROSS JOIN bounds b
    WHERE posted_at >= b.start_date AND posted_at < b.end_date AND posted_at < b.cutoff
      AND (b.scope_dimension IS NULL OR
           CASE b.scope_dimension WHEN 'region' THEN region WHEN 'channel' THEN channel WHEN 'product' THEN product END
           = b.scope_segment)
)
SELECT date,
       CASE WHEN GROUPING(region) = 0 THEN 'region'
            WHEN GROUPING(channel) = 0 THEN 'channel'
            WHEN GROUPING(product) = 0 THEN 'product' ELSE 'overall' END AS dimension,
       COALESCE(region, channel, product) AS segment,
       SUM(cents)::BIGINT AS net_revenue_cents,
       COUNT(DISTINCT booking_id) AS orders
FROM in_window
GROUP BY GROUPING SETS ((date), (date, region), (date, channel), (date, product))
ORDER BY date, dimension, segment

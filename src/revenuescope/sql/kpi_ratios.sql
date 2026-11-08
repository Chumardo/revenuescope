CASE WHEN orders > 0 THEN net_revenue_cents::DOUBLE / orders END AS average_order_value_cents,
CASE WHEN active_customers > 0 THEN net_revenue_cents::DOUBLE / active_customers END AS revenue_per_customer_cents,
CASE WHEN net_revenue_cents > 0 THEN gross_profit_cents::DOUBLE / net_revenue_cents END AS gross_margin

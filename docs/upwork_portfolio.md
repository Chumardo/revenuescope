# RevenueScope portfolio draft

**Portfolio title**  
RevenueScope — E-commerce Revenue Investigation Dashboard

**Project description**  
I built RevenueScope with DuckDB and Streamlit to help analysts decide which revenue gaps to investigate. Compare daily revenue with a weekday baseline, open a flagged date, and break the gap down by product, region, or channel. The default view counts refunds on the day they post. A separate view assigns them to the original order date. The demo uses 731 days of synthetic data. In the held-out test, the detector found 7 of 15 events and matched 7 of 88 alert episodes (7.95% event precision). This is a self-directed portfolio project.

**Role**  
Data analytics engineering, financial data modeling, dashboard design, and validation for a self-directed portfolio project.

**Relevant skills**  
Python, SQL, DuckDB, Streamlit, Plotly, financial analytics, anomaly screening, data visualization, automated testing.

**Business value**  
The workflow turns a flagged revenue day into a prioritized review with comparable totals, refund timing, and a traceable breakdown. A reviewer can identify where to investigate and assess the evidence before drawing conclusions.

**Recommended images**

1. [Revenue Overview](screenshots/release/01-overview.png) for the cover. Shows the scoped totals and flagged daily series.
2. [Investigation Detail](screenshots/release/03-investigation.png) to show the exact dollar gap and reconciled product evidence.
3. [Detector Evaluation](screenshots/release/04-evaluation.png) to show the held-out comparison and limitations.

The [Review Queue](screenshots/release/02-review-queue.png) is an optional fourth image showing dollar prioritization and the route into a case. All four images are actual desktop captures of the synthetic workspace.

The [published repository](https://github.com/Chumardo/revenuescope) links to the [live synthetic demo](https://revenuescope-demo.streamlit.app/). Use the repository URL as the portfolio project link.

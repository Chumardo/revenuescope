# RevenueScope case study

RevenueScope is a self-directed portfolio project built around a common e-commerce analytics problem. Daily revenue can move because of checkout volume, product mix, refunds, or planned promotions. A useful review workflow needs a consistent revenue definition, a way to prioritize deviations, and evidence that reconciles before an analyst decides what to investigate.

The demonstration uses a single synthetic merchant, one currency, and no client records. The release contains an offline snapshot and the display evidence needed to open the workspace without a private data source.

## Model and architecture

The seeded Python generator creates orders, lines, and refund events over 731 UTC days. DuckDB builds the daily facts and groups them by product, order region, and channel. The Python screening layer computes the baseline and the evidence for a flagged date. Streamlit renders the Overview, Review Queue, Investigation Detail, and Evaluation pages. A frozen synthetic snapshot avoids running the generator and benchmark each time the demo starts.

Checkout and refund events have different dates. In the **posted-day** view, recognized checkout value enters on the checkout date and refunds are subtracted on the day they post. The **retrospective order-day** view applies refunds known at the snapshot cutoff to the original purchase date. These definitions answer different questions and can produce different daily or monthly totals. Both views reconcile to **$4,461,841.22** over this full snapshot. July 2024, for example, has **$207,956.32 posted-day activity** and **$208,321.33 retrospective order-day revenue**.

Booking and refund arithmetic uses integer cents. A refund amount enters the posted-day ledger only once its synthetic posting time is known. Canceled or pending synthetic orders are excluded according to their checkout status. Production application of this model would require timestamped status changes and refund ingestion times to avoid looking ahead.

## Screening and evidence

The locked weekday trend estimates expected revenue from prior same-weekday dates using a Theil–Sen fit and median absolute deviation. It requires at least eight complete reference dates with sufficient activity and keeps unscored warmup dates separate. A review flag must clear the score, absolute-dollar, and relative-impact gates. The known synthetic promotion calendar changes training eligibility and suppresses flags on planned promotion dates. These rules were frozen before the held-out replay.

An analyst opens a flagged date, verifies the actual and expected totals, then examines the gap across regions, products, or channels. Each dimension is a separate partition of the same deviation. Expected cents are allocated using prior positive revenue mix with deterministic cent allocation. Each dimension reconciles actual, allocated expectation, and signed deviation, including any unallocated bucket. The allocation describes where the gap appears under that model. Confirming a cause requires separate evidence.

For example, the posted-day flag on **15 July 2024** shows **$11,531.62 actual** against **$6,905.85 expected**, a **+$4,625.77 deviation**. The product breakdown makes it possible to see which amounts contribute to that difference and which offset it. [Investigation screenshot](screenshots/release/03-investigation.png)

## Validation and results

The validation process independently sums source transactions by date, compares them with SQL, checks the posted and retrospective date contracts, and verifies that each investigation dimension conserves cents. The synthetic snapshot has **675 scored dates**, **56 warmup dates**, and **31 flags** on the posted-day basis. Those counts describe the dataset in the interactive workspace.

The held-out benchmark uses three separate seeds. Both policies and revenue bases use the original order-date event windows. An event is eligible when at least one day in its window was scored. An alert episode can match one eligible event if it contains a flag in the expected direction inside the window. A refund can affect posted-day revenue after that window, in which case an episode wholly outside the window earns no credit for that event. The locked posted-day policy detected **7 of 15 eligible events** and matched **7 of 88 alert episodes**. Benchmark-unmatched alert dates fell from **97 to 83** relative to the original policy. Unmatched dates can still merit investigation. [Evaluation screenshot](screenshots/release/04-evaluation.png)

The benchmark repeats five synthetic event types and measures variation across seeds rather than performance on new operational incidents. The **7.95% event precision** shows the cost of reviewing unmatched flags. Sparse segment history, the single-merchant simulation, and the absence of real-time ingestion all limit use. RevenueScope demonstrates an auditable analyst review workflow. Unattended anomaly alerts would require separate data, validation, and operational safeguards.

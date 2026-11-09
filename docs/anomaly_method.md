# Daily revenue screening and benchmark

RevenueScope compares daily revenue with a weekday baseline. The public Review Queue shows flags for overall revenue. The investigation breaks a flag's gap into segment contributions, while exploratory segment screening remains a developer workflow. A flag gives an analyst a date, a direction, an expected value, and a deviation to investigate. Confirming what caused the deviation requires separate evidence.

## Locked detector in the public workspace

The default posted-day view counts recognized booking value on checkout day and subtracts refunds on the dates they post. The retrospective order-day view applies refunds known at the snapshot cutoff to the original checkout date. A retrospective series can include information that was unavailable on the day being screened. The ledger uses UTC days and integer cents.

The locked policy uses the weekday trend fit described below. It requires eight complete same-weekday references in the prior 12 weeks, activity in at least 65% of those observations, and a historical median of at least eight orders. It excludes known synthetic promotion dates from the reference history and suppresses flags on those dates. A nonzero gap must reach four times the dispersion scale, $1,250, and 25% of the absolute expected amount. The dispersion scale is the greatest of scaled median absolute deviation, $100, and 5% of the absolute expectation.

## Original detector used for comparison

1. Use one UTC calendar day per row, with zero-activity days explicitly represented. Exclude the day being assessed and every incomplete day from training.
2. Collect observations for the same weekday from the prior 12 calendar weeks. Require at least eight complete observations. This handles weekly patterns while the rolling window limits the effect of older business conditions.
3. Require activity in at least 65% of those observations. Use nonzero order counts to measure activity when available, otherwise nonzero net revenue. When order counts are available, require a historical median of at least eight orders. The support check uses history only, so zero orders on the current day can still flag a drop in an otherwise active segment.
4. Take the median slope between pairs of historical observations to estimate the local trend. Project each historical observation to the current date using that slope, then take the median projection as expected revenue. This is a same-weekday Theil–Sen fit with the joint intercept. [Theil–Sen estimator](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.theilslopes.html)
5. Use the greatest of three scales: 1.4826 times the median absolute deviation of the projections, $100, or 5% of the absolute expectation. Divide observed minus expected revenue by this scale. Flag scores of 4 or greater in absolute value, with an up or down direction. The floors reduce noisy alerts on quiet or unusually stable segments. Normal scaling of MAD is a convention for the scale and carries no probability or confidence claim. [Median absolute deviation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.median_abs_deviation.html)

The result includes observed integer cents, model diagnostics in floating-point cents, past support counts, the score, and the flag. Incomplete, sparse, and insufficient-history rows retain their observations and an explicit status with a blank score. Thresholds are configurable through `AnomalyConfig` and are fixed before evaluation.

## Frozen held-out benchmark

The benchmark scores the overall series on three held-out seeds. It uses the same original order-date event windows and expected directions for both revenue bases. End dates are exclusive. An injected event is eligible for event recall if at least one day in its window was scored. The benchmark reports events outside coverage and events without scored dates separately.

- Date precision divides matching dates by alert dates. Date recall divides matching dates by scored dates inside the injected windows. Overlapping dates count once.
- Consecutive alerts in the same direction form an episode. An event and an episode can match at most once. A match needs an alert date in the expected direction inside the original event window. Event precision divides matches by alert episodes. Event recall divides matches by eligible events.
- Detection delay counts calendar days from the event window start to the first matched date in the assigned episode. Severity-weighted recall uses the absolute injected revenue change attributed to the original order window. Rates are blank when their denominators are zero.

A refund-driven gap may appear in posted-day revenue after the event's order-date window ends. An episode wholly outside the original window cannot count toward that event's recall, even if the timing aligns with a refund. The locked posted-day result of 7 of 15 eligible events detected and 7 of 88 episodes matched (7.95% event precision) describes this fixed-window benchmark.

## Interpretation

Changes in product mix, demand, or promotions outside the known synthetic calendar can produce flags. Sustained changes can enter the rolling baseline and reduce sensitivity. A benchmark-unmatched date can still merit investigation. The results cover five repeated synthetic event types, so they provide limited evidence about other incidents or merchants. Segment screening would also require accounting for multiple comparisons.

A separate evaluation could use posting-date event windows and posted-day counterfactual amounts. That would answer a different question and would need its own protocol and results. A production point-in-time backtest would also need timestamped order status and refund arrival data.

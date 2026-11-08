# Daily revenue anomaly screening

RevenueScope compares observed net revenue with a baseline fitted on earlier days in the same scope. A scope can be the whole business or an individual segment. A flag gives an analyst a date, a direction, an expected value, and a deviation to investigate. The statistical model cannot identify what caused a deviation.

## Baseline and alert rule

1. Use one UTC calendar day per row, with zero-activity days explicitly represented. Exclude the day being assessed and every incomplete day from training.
2. Collect observations for the same weekday from the prior 12 calendar weeks. Require at least eight complete observations. This handles weekly patterns while the rolling window limits the effect of older business conditions.
3. Require activity in at least 65% of those observations. Use nonzero order counts to measure activity when available, otherwise nonzero net revenue. When order counts are available, require a historical median of at least eight orders. The support check uses history only, so zero orders on the current day can still flag a drop in an otherwise active segment.
4. Take the median slope between pairs of historical observations to estimate the local trend. Project each historical observation to the current date using that slope, then take the median projection as expected revenue. This is a same-weekday Theil–Sen fit with the joint intercept. [Theil–Sen estimator](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.theilslopes.html)
5. Use the greatest of three scales: 1.4826 times the median absolute deviation of the projections, $100, or 5% of the absolute expectation. Divide observed minus expected revenue by this scale. Flag scores of 4 or greater in absolute value, with an up or down direction. The floors reduce noisy alerts on quiet or unusually stable segments. Normal scaling of MAD is a convention for the scale and carries no probability or confidence claim. [Median absolute deviation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.median_abs_deviation.html)

The result includes observed integer cents, model diagnostics in floating-point cents, past support counts, the score, and the flag. Incomplete, sparse, and insufficient-history rows retain their observations and an explicit status with a blank score. Thresholds are configurable through `AnomalyConfig` and are fixed before evaluation.

## Verification

The detector only consumes the revenue series. A separate evaluator compares alerts with the generator's withheld manifest using each event's date interval, direction, and scope. End dates are exclusive.

- A true-positive date has an alert in the expected direction within an event's window. A wrong-direction alert is a false positive and leaves the expected date missed. Alerts outside known windows are also false positives for the benchmark.
- Precision divides matched alerts by alert dates. Recall divides matched alert dates by the number of injected dates that had a score. Unscored injected dates are reported separately. Rates are blank when their denominators are zero.
- Each event can claim one alert date, and each alert date can credit one event. Within a scope, earliest-ending windows receive first choice of the earliest unused matching alert, with start date and event ID breaking ties. Day-level metrics count overlapping dates once. Event recall considers events with at least one scored date. Events wholly outside coverage or with no scored dates are reported separately.

## Interpretation

Holiday promotions, shifts in product mix, and abrupt changes in demand can alert even when they were part of the normal synthetic scenario. The weekly baseline does not model annual holidays, marketing calendars, or exposure to multiple comparisons across segments. Sustained changes can enter the rolling baseline and reduce sensitivity after the first few weeks. Use day-level and event-level metrics together, and treat synthetic benchmark results as a test of this dataset and policy.

The detector uses only earlier dates in its statistical fit. Because the revenue series attributes refunds known at a common reporting cutoff back to the original order date, a retrospective run can include refund information that arrived after the day being screened. For a true historical backtest, rebuild each date's revenue history using only information available at that day's cutoff.

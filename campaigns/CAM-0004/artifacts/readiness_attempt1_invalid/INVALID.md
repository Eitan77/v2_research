# Invalid readiness attempt

This artifact is preserved as a failed gate and must not be interpreted.

The run on 2026-07-29 loaded only 499 native split-adjusted daily rows for the
large-cap stock universe, leaving zero member-symbol-dates with all proxy
features. The script incorrectly emitted
`passed_for_labeled_adapted_mechanism_only` because it did not fail on complete
feature attrition. No strategy backtest ran.

The gate was tightened to require material feature coverage and is being
reproduced from scratch with cutoff-bounded Alpaca split-adjusted daily bars.

# CAM-0602 review — SSRN 3.3 Value

## Outcome

`retired_quote_execution_failure`. No candidate is promoted and the May 2026 onward holdout remains sealed.

## Evidence

- Best source-stage result at 2 bps per side: `qqq__book_to_price__long`, +470.92% fixed-base additive net.
- Selected executable adaptation: `qqq__btp__profitable__hold1__q10` at +513.24%; development-only post-2024 return +2.83%; expanding walk-forward parameter-selection return +265.20%.
- The selected quote model was 09:40 marketable daily reset: -26.23% fixed-base net, 29.32% maximum drawdown, 4/12 positive months, and 100.00% position completeness. With 2 bps extra slippage per side it returned -36.23%.

## Judgment

The result is interpreted as development evidence only. The audit separated long-only implementable sleeves from overnight or no-stop short diagnostics, tested broad parameter neighborhoods, periods, costs, contributors, and causal universes, and did not select a full-sample winning ticker basket. `retired_quote_execution_failure` is the strongest claim supported by the saved artifacts.

## Mandatory conclusion audit

- Source definition and implementation contract reconciled.
- Point-in-time universes, filing availability, sample attrition, and cutoff checks reconciled.
- Fixed-base additive accounting, no-margin gross cap, monthly/yearly path, drawdown, activity, costs, and concentration saved.
- Mechanism-consistent adaptations and development-only chronological checks completed.
- Every profitable execution-qualified best adaptation received SIP quote replay; direct-short signal-only variants were not called executable.
- Maximum loaded date is 2026-04-30 and holdout rows loaded are zero.
- Promotion remains false.

## 2026-08-10 deep-development checkpoint

Paper section 3.3, **Value**. Source contract: Book-to-price or book-to-market rank; buy top decile and short bottom decile; typical hold one to six months; current or book-contemporaneous price variants are acknowledged.

The structured survivor `qqq__value_quality__top5__trend0` earned +167.0% net at 2 bps over its available development history and +82.9% in the latest 12 months. Corrected 09:40 target-change SIP replay, with 2 bps additional adverse slippage per side, earned +82.0% with 10.4% drawdown, 10/2 positive/negative months, and 13.9% of positive P&L from the best five days.

Selection activity covered 97.2% of dates and averaged 5.00 names when active. Status: `strong_adapted_factor_concentrated_unpromoted`.

Matched-control conclusion: The winner is an adapted value-quality composite, not isolated source value; keep the label explicit and concentration caveat.

This is adapted development evidence, not untouched out-of-sample evidence. No rows on or after 2026-05-01 were loaded, and promotion remains false.

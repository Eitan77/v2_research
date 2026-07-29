# Holdout Access Log

## 2026-07-28 — metadata-only boundary incident

Before CAM-0011, a data-readiness query grouped `derived_bars_5m` for QQQ,
TQQQ, and SQQQ without an upper date predicate. Its output exposed only each
symbol's minimum date, maximum date, total row count, and count of rows on or
after May 1, 2026. No holdout prices, returns, signals, labels, strategy
results, or date-by-date observations were selected or viewed.

This was still an unauthorized access beyond the sealed boundary and is
recorded rather than hidden. It did not inform the hypothesis or any strategy
parameter. All campaign loaders remain hard-filtered through April 30, 2026.
Future readiness queries must put the discovery cutoff in the SQL `WHERE`
clause and may verify zero loaded holdout rows only on the already-filtered
in-memory frame.

## 2026-07-28 — CAM-0001 filesystem-metadata boundary incident

During CAM-0001 provenance preparation, a recursive file listing was issued
against the declared `bars_1d` source directory without first restricting its
date-partition paths through April 30, 2026. The command timed out while its
displayed output was still in April 2021, so no displayed filename was from the
sealed period. Nevertheless, the filesystem enumeration itself was not proven
to have stopped before later directory entries and is conservatively recorded
as an unauthorized metadata access.

No parquet contents, holdout prices, returns, signals, labels, strategy results,
or date-by-date holdout observations were selected or viewed. The incident did
not inform the already-frozen hypothesis, universe proposal, parameters, or
research plan. CAM-0001 market-data loaders remain hard-filtered through April
30, 2026. Further provenance uses the catalog hash and cutoff-filtered loaded
frame only; no unbounded source-directory enumeration is permitted.

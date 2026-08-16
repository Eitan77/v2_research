# QQQ Dual-MA Top-3 Current Research

This is the easy-to-find publication package for the current QQQ Dual-MA
Top-3 strategy research. It was assembled on 2026-08-16 from the canonical
local campaigns without altering their original records.

## Current locked strategy

- Point-in-time QQQ constituent universe.
- Require SMA50 > SMA200.
- Keep the top half by trailing 63-session median dollar volume.
- Rank eligible stocks by 126-session total return ending 21 sessions before
  the Friday decision.
- Hold the top three equally.
- Execute at the next session's 09:40 ET quote in the exact-fill research.
- Cash account only: no broker margin and no shorting.
- The deployment model compounds current equity, holds a 0.5% cash reserve,
  and equalizes only when ranked membership changes.

## Locked 126/21 evidence

Development through 2026-04-30, exact SIP execution plus 2 adverse bps/side:

- Fixed-base additive return: +362.83%.
- Maximum drawdown: 17.52%.
- Recent development 12-month return: +117.09%.
- Positive/negative months: 48/25.

Observed trailing-year deployment approximation, 2025-08-15 through
2026-08-14:

- Compounded return: +224.21%.
- Bar/slippage maximum account drawdown: 40.78%.
- Exact-quote Top-3 drawdown reference: 44.61%.

The observed period beginning 2026-05-01 has already influenced discussion.
It is descriptive evidence and must not be called fresh out-of-sample.

## Latest 126-session skip experiment

RUN-0079 held every other strategy component fixed and varied only the recent
session exclusion: 0, 1, 5, 10, 15, 21, and 30 sessions.

Exact SIP plus 2 adverse bps/side through 2026-04-30:

| Skip | Additive return | Max drawdown | Recent 12m |
|---:|---:|---:|---:|
| 0 | +289.55% | 32.86% | +127.42% |
| 10 | +317.69% | 20.95% | +99.45% |
| 21 | +362.83% | 17.52% | +117.09% |
| 30 | +416.31% | 15.36% | +150.91% |

126/30 is a post-hoc challenger, not the locked strategy. Its +53.48 percentage
point improvement over 126/21 was split almost evenly across the early and
late chronology halves and remained +17.77 points after removing the five
largest favorable difference days. However, those five days supplied 66.77%
of the gross improvement, and 126/21 beat it in 2023 and 2024. A frozen narrow
25/30/35 stability test is required before considering a live change.

## Recent simple defenses

Over the already-observed trailing year:

- Waiting for a flat/up morning reduced compounded return from +224.21% to
  +206.06% while improving maximum drawdown only from 40.78% to 39.09%.
- Requiring price at or below SMA200 destroyed the momentum edge.
- A maximum 75% extension above SMA200 reduced return to +106.95% and drawdown
  to 25.84%. It is a lower-risk alternative, not a superior baseline.
- Tighter 10%-50% caps removed too many large momentum winners.

## Folder map

- `campaign/`: governing CAM-0611 plan, results, review, checklist, and worklog.
- `runs/`: all preserved CAM-0611 run records.
- `src/`: all CAM-0611 research and reproduction source code.
- `results/`: compact CSV/JSON result artifacts for the focused strategy work
  beginning with aligned baseline RUN-0026. Raw quote caches and large parquet
  arrays are intentionally excluded.
- `charts/`: permanent phone-renderable charts.
- `related_overnight_CAM-0630/`: separately labeled higher-frequency overnight
  derivative research; it is not the locked weekly strategy.
- `MANIFEST.csv`: relative paths, byte sizes, and SHA-256 hashes.

## Integrity note

The canonical working campaign remains `campaigns/CAM-0611`. This publication
folder is a compact immutable-style snapshot for review and retrieval. It does
not promote 126/30, rewrite failed experiments, or convert already-observed
evidence into fresh validation.

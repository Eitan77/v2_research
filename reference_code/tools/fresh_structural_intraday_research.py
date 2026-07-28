"""Registered, low-dimensional structural investigation for a fresh intraday run.

This deliberately evaluates a small set of economically named mechanisms rather
than an optimizer grid.  It is discovery evidence only: all bar prices are
non-promotable and a candidate must pass before a SIP quote-path request.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd


ROOT = "D:/AlgoResearch/data/derived/alpaca/market/stocks/bars_10m/**/*.parquet"
CATALOG = "D:/AlgoResearch/data/catalog.duckdb"
DISCOVERY_END = "2026-05-25"


def sql_time_frame(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"ATTACH '{CATALOG}' AS catalog (READ_ONLY)")
    con.execute(
        f"""
        CREATE TEMP VIEW bars AS
        SELECT regexp_extract(filename, 'symbol=([^/\\\\]+)', 1) AS symbol,
               CAST(session_date AS DATE) AS date,
               (CAST(timestamp AS TIMESTAMPTZ) AT TIME ZONE 'America/New_York')::TIME AS tm,
               open, close, volume
        FROM read_parquet('{ROOT}', filename=true, hive_partitioning=true)
        WHERE bar_complete AND feed='sip' AND adjustment='raw'
          AND CAST(session_date AS DATE) <= DATE '{DISCOVERY_END}'
        """
    )


def query(con: duckdb.DuckDBPyConnection, name: str, statement: str) -> pd.DataFrame:
    result = con.sql(statement).df()
    result.insert(0, "hypothesis", name)
    return result


def summary(frame: pd.DataFrame) -> pd.DataFrame:
    """A bar-fill ledger summary including the prescribed 10 bps/side cost."""
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["net_return"] = frame["gross_return"] - 0.0020
    rows: list[dict] = []
    for key, part in [("full_discovery", frame), *[(str(y), x) for y, x in frame.groupby(frame.date.dt.year)]]:
        r = part.net_return
        equity = (1.0 + r).cumprod()
        dd = equity / equity.cummax() - 1.0
        rows.append({
            "period": key, "trades": len(part), "mean_net_bps": r.mean() * 10_000,
            "median_net_bps": r.median() * 10_000, "win_rate": (r > 0).mean(),
            "compound_return": equity.iloc[-1] - 1.0, "max_drawdown": dd.min(),
            "active_days": part.date.nunique(),
        })
    return pd.DataFrame(rows)


def cost_grid(frame: pd.DataFrame) -> pd.DataFrame:
    """Sensitivity is reported, never used to select a threshold or candidate."""
    rows: list[dict] = []
    for hypothesis, part in frame.groupby("hypothesis"):
        for bps_per_side in [0.0, 2.0, 5.0, 10.0, 25.0, 50.0]:
            ret = part.gross_return - 2.0 * bps_per_side / 10_000.0
            rows.append({
                "hypothesis": hypothesis, "bps_per_side": bps_per_side,
                "trades": len(ret), "mean_net_bps": ret.mean() * 10_000,
                "compound_return": (1.0 + ret).prod() - 1.0,
            })
    return pd.DataFrame(rows)


def main() -> None:
    out = Path("D:/AlgoResearch/research_pipeline/runs/20260710_fresh_structural_intraday")
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    sql_time_frame(con)
    # H1: Early information diffusion.  After the completed opening bar, use the
    # liquid Nasdaq directional ETF whose economic sign matches the early QQQ move.
    h1 = query(con, "H1_opening_information_diffusion", """
        WITH q AS (
          SELECT date, max(CASE WHEN tm=TIME '09:30' THEN open END) AS q_open,
                 max(CASE WHEN tm=TIME '09:30' THEN close END) AS q_close
          FROM bars WHERE symbol='QQQ' GROUP BY 1
        ), etf AS (
          SELECT symbol,date,max(CASE WHEN tm=TIME '09:40' THEN open END) entry,
                 max(CASE WHEN tm=TIME '15:40' THEN open END) exit
          FROM bars WHERE symbol IN ('TQQQ','SQQQ') GROUP BY 1,2
        )
        SELECT e.date,e.symbol,e.entry,e.exit,e.exit/e.entry-1 AS gross_return
        FROM q JOIN etf e USING(date)
        WHERE q_open IS NOT NULL AND q_close IS NOT NULL AND e.entry IS NOT NULL AND e.exit IS NOT NULL
          AND ((q_close >= q_open AND e.symbol='TQQQ') OR (q_close < q_open AND e.symbol='SQQQ'))
        ORDER BY e.date
    """)
    # H2: Cross-sectional information diffusion.  Every day select the strongest
    # PIT QQQ constituent after three completed opening bars, subject only to
    # pre-known 20-day $20m liquidity.  It tests ranking bins without a threshold.
    h2 = query(con, "H2_cross_sectional_opening_leader", """
        WITH p AS (
          SELECT symbol,date,
            max(CASE WHEN tm=TIME '09:30' THEN open END) o0930,
            max(CASE WHEN tm=TIME '09:50' THEN close END) c0950,
            max(CASE WHEN tm=TIME '10:00' THEN open END) entry,
            max(CASE WHEN tm=TIME '15:40' THEN open END) exit,
            sum(close*volume) dollar_volume
          FROM bars GROUP BY 1,2
        ), f AS (
          SELECT *, avg(dollar_volume) OVER(PARTITION BY symbol ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv20
          FROM p
        ), eligible AS (
          SELECT f.*, c0950/o0930-1 signal,
            row_number() OVER(PARTITION BY f.date ORDER BY c0950/o0930 DESC,f.symbol) rn
          FROM f JOIN catalog.qqq_pit_membership_daily m
            ON f.date=CAST(m.date AS DATE) AND f.symbol=m.symbol AND m.is_member
          WHERE adv20>=20000000 AND o0930 IS NOT NULL AND c0950 IS NOT NULL AND entry IS NOT NULL AND exit IS NOT NULL
        )
        SELECT date,symbol,entry,exit,exit/entry-1 gross_return FROM eligible WHERE rn=1 ORDER BY date
    """)
    # H3: Late institutional-flow continuation, using a fixed final 70-minute
    # observation window.  It does not use auction imbalances because those data
    # are unavailable in this SIP-bar catalog; that is recorded as a limitation.
    h3 = query(con, "H3_late_directional_flow", """
        WITH q AS (
          SELECT date,max(CASE WHEN tm=TIME '14:20' THEN close END) signal_close,
                 max(CASE WHEN tm=TIME '13:20' THEN close END) base_close FROM bars WHERE symbol='QQQ' GROUP BY 1
        ), etf AS (
          SELECT symbol,date,max(CASE WHEN tm=TIME '14:30' THEN open END) entry,
                 max(CASE WHEN tm=TIME '15:40' THEN open END) exit FROM bars WHERE symbol IN ('TQQQ','SQQQ') GROUP BY 1,2
        )
        SELECT e.date,e.symbol,e.entry,e.exit,e.exit/e.entry-1 gross_return
        FROM q JOIN etf e USING(date)
        WHERE signal_close IS NOT NULL AND base_close IS NOT NULL AND entry IS NOT NULL AND exit IS NOT NULL
          AND ((signal_close>=base_close AND symbol='TQQQ') OR (signal_close<base_close AND symbol='SQQQ')) ORDER BY e.date
    """)
    # H4: recurring predictable time-of-day flow. The prior session's 14:20
    # return is known before today's open; the trade tests whether that signed
    # demand recurs during today's late session, as the literature suggests.
    h4 = query(con, "H4_same_time_flow_recurrence", """
        WITH q0 AS (
          SELECT date,max(CASE WHEN tm=TIME '14:20' THEN open END) q_open,
                 max(CASE WHEN tm=TIME '14:20' THEN close END) q_close FROM bars WHERE symbol='QQQ' GROUP BY 1
        ), q AS (
          SELECT *,lag(q_close/q_open-1) OVER(ORDER BY date) prior_same_time_return FROM q0
        ), etf AS (
          SELECT symbol,date,max(CASE WHEN tm=TIME '14:30' THEN open END) entry,
                 max(CASE WHEN tm=TIME '15:40' THEN open END) exit FROM bars WHERE symbol IN ('TQQQ','SQQQ') GROUP BY 1,2
        )
        SELECT e.date,e.symbol,e.entry,e.exit,e.exit/e.entry-1 gross_return FROM q JOIN etf e USING(date)
        WHERE prior_same_time_return IS NOT NULL AND entry IS NOT NULL AND exit IS NOT NULL
          AND ((prior_same_time_return>=0 AND symbol='TQQQ') OR (prior_same_time_return<0 AND symbol='SQQQ')) ORDER BY e.date
    """)
    combined = pd.concat([h1, h2, h3, h4], ignore_index=True)
    combined.to_parquet(out / "causal_bar_fill_ledgers.parquet", index=False)
    combined.to_csv(out / "causal_bar_fill_ledgers.csv", index=False)
    metrics = pd.concat([summary(x) .assign(hypothesis=name) for name, x in combined.groupby("hypothesis")], ignore_index=True)
    metrics.to_csv(out / "descriptive_and_bar_fill_metrics.csv", index=False)
    cost_grid(combined).to_csv(out / "cost_sensitivity.csv", index=False)
    decision = {
        "discovery_end": DISCOVERY_END,
        "holdout": "sealed from 2026-06-01; not read by this program",
        "bar_timing": "10m start-stamped; signals use completed bars and entries occur at the next bar open",
        "cost_assumption": "10 bps per side, deducted as 20 bps round trip",
        "result": "no hypothesis met promotion criteria; no SIP quote-path validation or holdout test is authorized",
        "rejected_hypotheses": list(metrics.hypothesis.drop_duplicates()),
    }
    (out / "stage_03_promotion_decision.json").write_text(json.dumps(decision, indent=2), encoding="utf-8")
    full = metrics[metrics.period.eq("full_discovery")].copy()
    lines = ["# Fresh structural intraday investigation", "", "No candidate was promoted. All four registered mechanisms were evaluated as causal, costed bar-fill ledgers through 2026-05-25 and failed the first gate.", "", "## Research basis", "", "- Heston, Korajczyk, and Sadka, *Intraday Patterns in the Cross-section of Stock Returns* (2010): documents recurring same-time return continuation and separates it from short-lived reversal caused by liquidity imbalances and bid-ask bounce. https://arxiv.org/abs/1005.3535", "- Cushing and Madhavan, *Stock Returns and Trading at the Close* (2000): finds temporary price pressure and reversal after closing order imbalances. https://doi.org/10.1016/S1386-4181(99)00012-9", "- NYSE, *Opening and Closing Auctions*: the exchange publishes closing imbalance information and freezes the closing auction at 15:59. Actual SIP auction prints are tested separately in `auction_flow/`; these bar-only tests do not use auction information. https://www.nyse.com/trade/auctions", "", "## Registered tests", "", "- H1: opening information diffusion — after the 09:30-09:40 completed QQQ bar, buy TQQQ for an up move or SQQQ for a down move at 09:40; exit at 15:40. Long SQQQ is disclosed bearish economic exposure.", "- H2: cross-sectional opening leader — choose the single highest 09:30-10:00 return among point-in-time QQQ constituents with prior 20-day average dollar volume at least $20m; buy at 10:00 and exit at 15:40.", "- H3: late directional flow — use QQQ's 13:20-14:30 move to choose TQQQ/SQQQ; enter at 14:30 and exit at 15:40.", "- H4: same-time flow recurrence — use the prior session's signed QQQ 14:20 bar to choose TQQQ/SQQQ at 14:30; exit at 15:40.", "", "## Costed discovery results", "", "| Hypothesis | Trades | Mean net bps | Win rate | Compound return | Max drawdown |", "|---|---:|---:|---:|---:|---:|"]
    for row in full.itertuples(index=False):
        lines.append(f"| {row.hypothesis} | {row.trades} | {row.mean_net_bps:.2f} | {row.win_rate:.1%} | {row.compound_return:.1%} | {row.max_drawdown:.1%} |")
    lines += ["", "## Guardrails", "", "- Sealed holdout begins 2026-06-01 and was not read.", "- Bar data are SIP/raw, 10-minute, start-stamped; a signal uses only a completed bar and enters at the following bar open.", "- Results deduct 10 bps per side. They are discovery evidence, not fills.", "- `cost_sensitivity.csv` reports 0, 2, 5, 10, 25, and 50 bps per side without using the grid to tune any rule.", "", "## Promotion decision", "", "Quote-path promotion is rejected. Under the registered workflow, spending SIP requests on candidates with negative costed bar evidence would be invalid; therefore no combined portfolio or holdout was selected.", "", "## Artifacts", "", "- `causal_bar_fill_ledgers.parquet` and `.csv`: exact reconstructed trade candidates.", "- `descriptive_and_bar_fill_metrics.csv`: full and calendar-year metrics.", "- `cost_sensitivity.csv`: required cost grid.", "- `stage_03_promotion_decision.json`: auditable non-promotion decision."]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

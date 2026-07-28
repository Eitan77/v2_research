"""Causal investigation of actual SIP opening/closing-auction prints.

Both signals are deliberately rank-only: the top PIT QQQ constituent by a
predefined auction-volume ratio is selected.  No return/volume cutoff is chosen
after inspecting performance.  This writes bar-fill evidence only.
"""
from __future__ import annotations

from pathlib import Path
import duckdb
import pandas as pd


OUT = Path("D:/AlgoResearch/research_pipeline/runs/20260710_fresh_structural_intraday/auction_flow")
BAR = "D:/AlgoResearch/data/derived/alpaca/market/stocks/bars_10m/**/*.parquet"
AUC = "D:/AlgoResearch/data/raw/alpaca/market/stocks/auctions/feed=sip/**/*.parquet"


def summarize(name: str, trades: pd.DataFrame) -> pd.DataFrame:
    trades["date"] = pd.to_datetime(trades["date"])
    trades["net_return"] = trades["gross_return"] - 0.002
    rows = []
    for period, part in [("full_discovery", trades), *[(str(y), x) for y, x in trades.groupby(trades.date.dt.year)]]:
        equity = (1 + part.net_return).cumprod()
        rows.append({"hypothesis": name, "period": period, "trades": len(part), "mean_net_bps": part.net_return.mean() * 1e4,
                     "win_rate": (part.net_return > 0).mean(), "compound_return": equity.iloc[-1] - 1,
                     "max_drawdown": (equity / equity.cummax() - 1).min()})
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("ATTACH 'D:/AlgoResearch/data/catalog.duckdb' AS catalog (READ_ONLY)")
    con.execute(f"""
      CREATE TEMP TABLE p AS
      WITH bars AS (
        SELECT regexp_extract(filename,'symbol=([^/\\\\]+)',1) symbol, CAST(session_date AS DATE) date,
               (CAST(timestamp AS TIMESTAMPTZ) AT TIME ZONE 'America/New_York')::TIME tm, open, volume
        FROM read_parquet('{BAR}',filename=true,hive_partitioning=true)
        WHERE bar_complete AND feed='sip' AND adjustment='raw' AND CAST(session_date AS DATE)<=DATE '2026-05-25'
      ), p0 AS (
        SELECT symbol,date,SUM(volume) dv,
          MAX(CASE WHEN tm=TIME '09:40' THEN open END) entry_0940,
          MAX(CASE WHEN tm=TIME '10:00' THEN open END) entry_1000,
          MAX(CASE WHEN tm=TIME '15:40' THEN open END) exit_1540,
          MAX(CASE WHEN tm=TIME '15:50' THEN open END) preclose_1550
        FROM bars GROUP BY 1,2
      )
      SELECT *,AVG(dv) OVER(PARTITION BY symbol ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv20,
               LEAD(date) OVER(PARTITION BY symbol ORDER BY date) next_date
      FROM p0
    """)
    con.execute(f"""
      CREATE TEMP TABLE close_auction AS
      SELECT r.symbol,CAST(r.d AS DATE) date,SUM(CAST(json_extract(j.value,'$.s') AS DOUBLE)) auction_size,
             SUM(CAST(json_extract(j.value,'$.p') AS DOUBLE)*CAST(json_extract(j.value,'$.s') AS DOUBLE))
               / SUM(CAST(json_extract(j.value,'$.s') AS DOUBLE)) auction_price
      FROM read_parquet('{AUC}',hive_partitioning=true,union_by_name=true) r, json_each(r.c) j
      WHERE r.c IS NOT NULL GROUP BY 1,2
    """)
    con.execute(f"""
      CREATE TEMP TABLE open_auction AS
      SELECT r.symbol,CAST(r.d AS DATE) date,SUM(CAST(json_extract(j.value,'$.s') AS DOUBLE)) auction_size
      FROM read_parquet('{AUC}',hive_partitioning=true,union_by_name=true) r, json_each(r.o) j
      WHERE r.o IS NOT NULL GROUP BY 1,2
    """)
    # Closing print occurs at 16:00, so it is traded only on the next session.
    close_sql = """
      WITH ranked AS (
        SELECT p.symbol,p.next_date date,c.auction_size/p.dv auction_ratio,n.entry_1000 entry,n.exit_1540 exit,
          ROW_NUMBER() OVER(PARTITION BY p.next_date ORDER BY c.auction_size/p.dv DESC,p.symbol) rn
        FROM p JOIN close_auction c USING(symbol,date)
          JOIN p n ON p.symbol=n.symbol AND p.next_date=n.date
          JOIN catalog.qqq_pit_membership_daily m ON n.date=CAST(m.date AS DATE) AND n.symbol=m.symbol AND m.is_member
        WHERE p.dv>0 AND n.entry_1000 IS NOT NULL AND n.exit_1540 IS NOT NULL
      ) SELECT date,symbol,auction_ratio,entry,exit,exit/entry-1 gross_return FROM ranked WHERE rn=1 ORDER BY date
    """
    # Opening print is known at 09:30; 09:40 is the first permitted bar entry.
    open_sql = """
      WITH ranked AS (
        SELECT p.symbol,p.date,o.auction_size/p.adv20 auction_ratio,p.entry_0940 entry,p.exit_1540 exit,
          ROW_NUMBER() OVER(PARTITION BY p.date ORDER BY o.auction_size/p.adv20 DESC,p.symbol) rn
        FROM p JOIN open_auction o USING(symbol,date)
          JOIN catalog.qqq_pit_membership_daily m ON p.date=CAST(m.date AS DATE) AND p.symbol=m.symbol AND m.is_member
        WHERE p.adv20>0 AND p.entry_0940 IS NOT NULL AND p.exit_1540 IS NOT NULL
      ) SELECT date,symbol,auction_ratio,entry,exit,exit/entry-1 gross_return FROM ranked WHERE rn=1 ORDER BY date
    """
    # Auction price is final only after 16:00.  H7/H8 therefore use it only to
    # rank the next session, against the last completed 15:50 bar; no threshold.
    dislocation_sql = """
      WITH ranked AS (
        SELECT p.symbol,p.next_date date,c.auction_price/p.preclose_1550-1 auction_dislocation,
          n.entry_1000 entry,n.exit_1540 exit,
          ROW_NUMBER() OVER(PARTITION BY p.next_date ORDER BY c.auction_price/p.preclose_1550 ASC,p.symbol) bottom_rn,
          ROW_NUMBER() OVER(PARTITION BY p.next_date ORDER BY c.auction_price/p.preclose_1550 DESC,p.symbol) top_rn
        FROM p JOIN close_auction c USING(symbol,date)
          JOIN p n ON p.symbol=n.symbol AND p.next_date=n.date
          JOIN catalog.qqq_pit_membership_daily m ON n.date=CAST(m.date AS DATE) AND n.symbol=m.symbol AND m.is_member
        WHERE p.preclose_1550 IS NOT NULL AND n.entry_1000 IS NOT NULL AND n.exit_1540 IS NOT NULL
      )
      SELECT date,symbol,auction_dislocation,entry,exit,exit/entry-1 gross_return,'bottom' leg FROM ranked WHERE bottom_rn=1
      UNION ALL
      SELECT date,symbol,auction_dislocation,entry,exit,exit/entry-1 gross_return,'top' leg FROM ranked WHERE top_rn=1
      ORDER BY date,leg
    """
    close = con.sql(close_sql).df(); close["hypothesis"] = "H5_closing_auction_flow_spillover"
    opening = con.sql(open_sql).df(); opening["hypothesis"] = "H6_opening_auction_demand"
    dislocation = con.sql(dislocation_sql).df()
    negative = dislocation[dislocation.leg.eq("bottom")].drop(columns="leg").copy(); negative["hypothesis"] = "H7_negative_closing_auction_dislocation"
    positive = dislocation[dislocation.leg.eq("top")].drop(columns="leg").copy(); positive["hypothesis"] = "H8_positive_closing_auction_dislocation"
    ledger = pd.concat([close, opening, negative, positive], ignore_index=True)
    ledger.to_parquet(OUT / "causal_bar_fill_ledgers.parquet", index=False)
    ledger.to_csv(OUT / "causal_bar_fill_ledgers.csv", index=False)
    metrics = pd.concat([summarize(name, x.copy()) for name, x in ledger.groupby("hypothesis")], ignore_index=True)
    metrics.to_csv(OUT / "metrics.csv", index=False)
    report = ["# SIP auction-flow structural tests", "", "All four tests use actual SIP auction prints, a raw/feed=sip bar ledger, 10 bps per side, and no sealed June date.", "", "| Hypothesis | Trades | Mean net bps | Compound return | Max drawdown |", "|---|---:|---:|---:|---:|"]
    for r in metrics[metrics.period.eq("full_discovery")].itertuples(index=False):
        report.append(f"| {r.hypothesis} | {r.trades} | {r.mean_net_bps:.2f} | {r.compound_return:.1%} | {r.max_drawdown:.1%} |")
    report += ["", "Neither result clears a bar-fill promotion gate. No quote requests or holdout reading are authorized."]
    (OUT / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

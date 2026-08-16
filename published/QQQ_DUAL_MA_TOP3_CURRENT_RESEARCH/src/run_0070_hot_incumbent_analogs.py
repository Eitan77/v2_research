from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))
from run_0058_self_financing import context

OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0070"
CURRENT = np.array([0.35381658293529994, 0.163016979901919, 0.21245143316042947])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    p, schedule = context()
    equity = pd.read_parquet(ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0058" /
                             "daily_change_only_reserve0.005_2bps.parquet")
    equity["date"] = pd.to_datetime(equity.date)
    eq = equity.set_index("date").equity
    col = {str(s): i for i, s in enumerate(p.symbols)}
    previous: tuple[str, ...] = tuple()
    events = []
    for execution_i, target in schedule.items():
        signal_i = execution_i - 1
        if signal_i < 21 or execution_i + 4 >= len(p.dates):
            previous = target
            continue
        incumbents = sorted(set(target) & set(previous))
        for symbol in incumbents:
            c = col[symbol]
            close = p.adj_close[:, c]
            if not all(np.isfinite(close[k]) and close[k] > 0 for k in (signal_i, signal_i-5, signal_i-21)):
                continue
            r5 = close[signal_i] / close[signal_i-5] - 1.0
            r21 = close[signal_i] / close[signal_i-21] - 1.0
            sma20 = np.nanmean(close[signal_i-19:signal_i+1])
            d20 = close[signal_i] / sma20 - 1.0
            exec_day = pd.Timestamp(p.dates[execution_i])
            before_day = pd.Timestamp(p.dates[execution_i-1])
            end_day = pd.Timestamp(p.dates[execution_i+4])
            portfolio_return = float(eq.loc[end_day] / eq.loc[before_day] - 1.0)
            events.append({"signal_date": str(pd.Timestamp(p.dates[signal_i]).date()),
                           "execution_date": str(exec_day.date()), "symbol": symbol,
                           "ret5": float(r5), "ret21": float(r21), "above_sma20": float(d20),
                           "portfolio_next_week": portfolio_return})
        previous = target
    frame = pd.DataFrame(events)
    frame.to_csv(OUT / "incumbent_events.csv", index=False)
    summaries = []
    for threshold in (0.10, 0.15, 0.20, 0.25, 0.30, 0.35):
        z = frame[frame.ret5 >= threshold]
        weeks = z.groupby("execution_date", as_index=False).agg(
            portfolio_next_week=("portfolio_next_week", "first"),
            hottest_ret5=("ret5", "max"),
            hot_symbols=("symbol", lambda s: ",".join(sorted(set(s)))),
        )
        summaries.append({"ret5_threshold": threshold, "qualifying_name_events": int(len(z)),
                          "independent_weeks": int(len(weeks)),
                          "positive_weeks": int((weeks.portfolio_next_week > 0).sum()),
                          "win_rate": float((weeks.portfolio_next_week > 0).mean()) if len(weeks) else None,
                          "mean_next_week": float(weeks.portfolio_next_week.mean()) if len(weeks) else None,
                          "median_next_week": float(weeks.portfolio_next_week.median()) if len(weeks) else None,
                          "worst_next_week": float(weeks.portfolio_next_week.min()) if len(weeks) else None,
                          "best_next_week": float(weeks.portfolio_next_week.max()) if len(weeks) else None})
        weeks.to_csv(OUT / f"weeks_ret5_ge_{int(threshold*100):02d}.csv", index=False)
    summary = pd.DataFrame(summaries)
    summary.to_csv(OUT / "threshold_summary.csv", index=False)
    scales = np.array([0.15, 0.20, 0.15])
    frame["distance"] = np.sqrt((((frame[["ret5", "ret21", "above_sma20"]].to_numpy() - CURRENT) / scales) ** 2).sum(axis=1))
    nearest = frame.sort_values("distance").head(10)
    nearest.to_csv(OUT / "nearest_analogues.csv", index=False)
    report = {"status": "completed", "maximum_loaded_date": "2026-04-30", "holdout_rows_loaded": 0,
              "current_reference": {"symbol": "SNDK", "ret5": CURRENT[0], "ret21": CURRENT[1], "above_sma20": CURRENT[2]},
              "thresholds": summaries, "nearest": nearest.to_dict("records")}
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(summary.to_string(index=False))
    print("\nNEAREST\n", nearest.to_string(index=False))


if __name__ == "__main__":
    main()

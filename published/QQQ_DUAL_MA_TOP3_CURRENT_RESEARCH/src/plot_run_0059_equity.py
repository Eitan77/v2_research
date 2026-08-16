from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0059" / "change_only_equity_oos.jpg"

development = pd.read_parquet(
    ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0058"
    / "daily_change_only_reserve0.005_2bps.parquet"
)
holdout = pd.read_parquet(
    ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0059" / "oos_daily.parquet"
)
development["date"] = pd.to_datetime(development.date)
holdout["date"] = pd.to_datetime(holdout.date)
curve = pd.concat([
    development.loc[development.date.between("2025-04-30", "2026-04-30"), ["date", "equity"]],
    holdout[["date", "equity"]],
]).drop_duplicates("date").sort_values("date")
base = float(curve.iloc[0].equity)
curve["indexed_equity"] = 100 * curve.equity / base

cutoff = pd.Timestamp("2026-05-01")
oos = curve[curve.date >= cutoff]
peak = oos.loc[oos.equity.idxmax()]
running_peak = oos.equity.cummax()
drawdown = oos.equity / running_peak - 1
trough = oos.loc[drawdown.idxmin()]
latest = curve.iloc[-1]

plt.style.use("seaborn-v0_8-whitegrid")
fig, ax = plt.subplots(figsize=(14, 7.5), dpi=180)
ax.plot(curve.date, curve.indexed_equity, color="#1769aa", linewidth=2.5,
        label="Change-only self-financing QQQ top-3")
ax.axvline(cutoff, color="#444444", linestyle="--", linewidth=2)
ax.text(cutoff + pd.Timedelta(days=5), ax.get_ylim()[1] * 0.96, "OOS begins\nMay 1, 2026",
        ha="left", va="top", fontsize=10, color="#333333")

for row, label, color, offset in [
    (peak, "OOS peak", "#2e7d32", (8, 8)),
    (trough, "−44.6% drawdown", "#c62828", (8, -24)),
    (latest, "Aug 14: still −23.3% below peak", "#ef6c00", (-185, 12)),
]:
    ax.scatter(row.date, row.indexed_equity, s=55, color=color, zorder=4)
    ax.annotate(f"{label}\nIndex {row.indexed_equity:.1f}",
                (row.date, row.indexed_equity), xytext=offset, textcoords="offset points",
                fontsize=10, color=color, fontweight="bold")

ax.set_title("QQQ Dual-MA Top-3 — Change-Only Self-Financing Equity", fontsize=16, pad=16)
ax.set_ylabel("Portfolio equity index (Apr 30, 2025 = 100)", fontsize=11)
ax.set_xlabel("")
ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
ax.legend(loc="upper left", frameon=False)
ax.spines[["top", "right"]].set_visible(False)
ax.grid(axis="x", alpha=0.15)
ax.grid(axis="y", alpha=0.25)
fig.text(0.99, 0.012,
         "Exact SIP execution + 2 bps/side, 0.5% cash reserve, no margin. August partial through Aug 14.",
         ha="right", fontsize=8.5, color="#555555")
fig.tight_layout(rect=(0, 0.035, 1, 1))
OUT.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT, format="jpg", pil_kwargs={"quality": 94}, bbox_inches="tight")
print(OUT)

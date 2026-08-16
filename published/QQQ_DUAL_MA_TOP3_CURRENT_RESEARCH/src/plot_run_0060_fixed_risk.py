from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0060" / "fixed-risk-vs-compounding.jpg"

fixed = pd.read_parquet(ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0060" / "combined_daily.parquet")
compound_dev = pd.read_parquet(ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0058" / "daily_change_only_reserve0.005_2bps.parquet")
compound_ext = pd.read_parquet(ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0059" / "oos_daily.parquet")
for frame in (fixed, compound_dev, compound_ext):
    frame["date"] = pd.to_datetime(frame.date)
compound = pd.concat([compound_dev, compound_ext], ignore_index=True).drop_duplicates("date").sort_values("date")
start, cutoff, end = pd.Timestamp("2025-04-30"), pd.Timestamp("2026-05-01"), pd.Timestamp("2026-08-14")
fixed = fixed[fixed.date.between(start, end)].copy()
compound = compound[compound.date.between(start, end)].copy()
fixed["index"] = 100 * fixed.equity / float(fixed.iloc[0].equity)
compound["index"] = 100 * compound.equity / float(compound.iloc[0].equity)

plt.style.use("seaborn-v0_8-whitegrid")
fig, ax = plt.subplots(figsize=(14, 7.5), dpi=180)
ax.plot(compound.date, compound["index"], linewidth=2.3, color="#1769aa",
        label="Fully compounded, equalize only on membership change")
ax.plot(fixed.date, fixed["index"], linewidth=2.6, color="#2e7d32",
        label="Fixed initial risk, weekly profit sweep")
ax.axvline(cutoff, linestyle="--", linewidth=2, color="#444444")
ax.text(cutoff + pd.Timedelta(days=5), 0.965, "May 1 — observed holdout begins",
        transform=ax.get_xaxis_transform(), ha="left", va="top", fontsize=10, color="#333333")

fixed_end, compound_end = fixed.iloc[-1], compound.iloc[-1]
ax.scatter([fixed_end.date], [fixed_end["index"]], color="#2e7d32", s=55, zorder=4)
ax.scatter([compound_end.date], [compound_end["index"]], color="#1769aa", s=55, zorder=4)
ax.annotate(f"Fixed risk\nIndex {fixed_end['index']:.1f}", (fixed_end.date, fixed_end["index"]),
            xytext=(-95, -20), textcoords="offset points", fontsize=10, color="#2e7d32", fontweight="bold")
ax.annotate(f"Compounded\nIndex {compound_end['index']:.1f}", (compound_end.date, compound_end["index"]),
            xytext=(-95, 12), textcoords="offset points", fontsize=10, color="#1769aa", fontweight="bold")

summary = ("Fixed-risk post-observation path\n"
           "May  +9.33%   June  +4.86%\n"
           "July  −7.46%   Aug 1–14  +4.56%\n"
           "Combined +10.92%   Max DD −10.40%")
ax.text(0.015, 0.73, summary, transform=ax.transAxes, ha="left", va="top", fontsize=10,
        bbox={"facecolor": "white", "edgecolor": "#bbbbbb", "alpha": 0.9, "boxstyle": "round,pad=0.5"})

ax.set_title("QQQ Dual-MA Top-3 — Fixed Initial Risk vs Full Compounding", fontsize=16, pad=16)
ax.set_ylabel("Portfolio equity index (Apr 30, 2025 = 100)", fontsize=11)
ax.set_xlabel("")
ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
ax.legend(loc="upper left", frameon=False)
ax.spines[["top", "right"]].set_visible(False)
ax.grid(axis="x", alpha=0.15)
ax.grid(axis="y", alpha=0.25)
fig.text(0.99, 0.012, "Exact SIP + 2 bps/side; no margin. May–Aug fixed-risk results are post-observation diagnostics, not fresh OOS.",
         ha="right", fontsize=8.5, color="#555555")
fig.tight_layout(rect=(0, 0.035, 1, 1))
fig.savefig(OUT, format="jpg", pil_kwargs={"quality": 94}, bbox_inches="tight")
print(OUT)

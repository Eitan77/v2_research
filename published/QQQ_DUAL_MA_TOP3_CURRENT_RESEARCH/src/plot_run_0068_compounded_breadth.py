from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "campaigns" / "CAM-0611" / "artifacts" / "RUN-0068" / "compounded_breadth.csv"
OUT = Path(r"C:\Users\decla\.codex\visualizations\2026\08\07\019fd9a0-9bf8-7450-92a5-0fcc860e61bf\qqq-top-n-last-year-compounded.jpeg")

frame = pd.read_csv(DATA)
x = frame.top_n
ret = 100 * frame.compounded_return
dd = 100 * frame.maximum_drawdown
top3 = frame.loc[frame.top_n.eq(3)].iloc[0]

plt.style.use("seaborn-v0_8-whitegrid")
fig, axes = plt.subplots(2, 1, figsize=(15, 11), sharex=True)
fig.suptitle("QQQ Dual-MA 126/21 — Compounded Live-Portfolio Approximation", fontsize=22, fontweight="bold")

axes[0].plot(x, ret, color="#2774d8", marker="o", linewidth=2.4, markersize=6)
axes[0].scatter([3], [100 * top3.compounded_return], color="#ff7f0e", s=150, zorder=5)
axes[0].annotate(f"Top 3: {100 * top3.compounded_return:.2f}%",
                 xy=(3, 100 * top3.compounded_return), xytext=(4.2, 250),
                 arrowprops={"arrowstyle": "-", "color": "#ff7f0e", "lw": 1.5}, fontsize=13)
axes[0].set_title("Compounded Account Return by Number of Stocks Held", fontsize=16)
axes[0].set_ylabel("Trailing 12-month return (%)", fontsize=13)

axes[1].plot(x, dd, color="#d62728", marker="o", linewidth=2.4, markersize=6)
axes[1].scatter([3], [100 * top3.maximum_drawdown], color="#ff7f0e", s=150, zorder=5)
axes[1].annotate(f"Top 3 bar approximation: {100 * top3.maximum_drawdown:.2f}%\nExact quote-filled Top 3: 44.61%",
                 xy=(3, 100 * top3.maximum_drawdown), xytext=(4.2, 46.5),
                 arrowprops={"arrowstyle": "-", "color": "#ff7f0e", "lw": 1.5}, fontsize=12)
axes[1].set_title("True Peak-to-Trough Account Drawdown", fontsize=16)
axes[1].set_ylabel("Maximum drawdown (%)", fontsize=13)
axes[1].set_xlabel("Number of top-ranked stocks held (Top N)", fontsize=13)
axes[1].set_xticks(range(1, 21))

for ax in axes:
    ax.tick_params(labelsize=11)
    ax.set_xlim(0.5, 20.5)

fig.text(0.5, 0.015,
         "Aug 15, 2025–Aug 14, 2026 • observed OOS begins May 1 • self-financing compounding • 0.5% cash reserve • rebalance only when membership changes • bar + frozen average slippage",
         ha="center", fontsize=10.5, color="#555555")
fig.tight_layout(rect=(0, 0.045, 1, 0.95))
OUT.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT, dpi=180, format="jpeg", bbox_inches="tight")
print(OUT)

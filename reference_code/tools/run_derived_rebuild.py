from pathlib import Path

from alpaca_research.optimized import rebuild_derived_bars


if __name__ == "__main__":
    print(rebuild_derived_bars(Path("D:/AlgoResearch/data"), ["5m", "10m", "15m", "30m", "1h", "4h"], 8), flush=True)

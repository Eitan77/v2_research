from pathlib import Path

from alpaca_research.optimized import rebuild_technical_features


if __name__ == "__main__":
    print(rebuild_technical_features(Path("D:/AlgoResearch/data"), ["5m", "10m", "15m", "30m", "1h", "4h"], 16), flush=True)

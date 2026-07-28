from pathlib import Path

import pandas as pd

from ar_pipeline.marketdata.quote_lake import lake_audit, load_quote_windows, publish_run, quote_window_coverage


def test_quote_lake_publish_is_idempotent(tmp_path: Path) -> None:
    run = tmp_path / "sample_run"
    daily = run / "daily_quotes"
    daily.mkdir(parents=True)
    timestamp = pd.Timestamp("2025-05-01 14:00:00", tz="UTC")
    quotes = pd.DataFrame({
        "request_ts": [timestamp, timestamp],
        "symbol": ["AAPL", "AAPL"],
        "quote_ts": [timestamp + pd.Timedelta(milliseconds=1)] * 2,
        "bid_price": [100.0, 100.0], "ask_price": [100.01, 100.01],
        "bid_size": [10.0, 10.0], "ask_size": [12.0, 12.0],
    })
    quotes.to_parquet(daily / "quotes_2025-05-01.parquet", index=False)
    pd.DataFrame({
        "session_date": [pd.Timestamp("2025-05-01")],
        "symbol": ["AAPL"], "request_ts": [timestamp],
    }).to_parquet(run / "quote_endpoints.parquet", index=False)
    lake = tmp_path / "lake"

    publish_run(run, lake)
    publish_run(run, lake)
    audit = lake_audit(lake)

    assert audit["quote_rows"] == 1
    assert audit["windows"] == 1
    assert audit["sessions"] == 1
    assert (lake / "quote_lake.duckdb").exists()
    loaded = load_quote_windows(pd.DataFrame({
        "session_date": [pd.Timestamp("2025-05-01")],
        "symbol": ["AAPL"], "request_ts": [timestamp],
    }), lake)
    assert loaded is not None
    assert len(loaded) == 1
    coverage = quote_window_coverage(pd.DataFrame({
        "session_date": [pd.Timestamp("2025-05-01"), pd.Timestamp("2025-05-01")],
        "symbol": ["AAPL", "MSFT"], "request_ts": [timestamp, timestamp],
    }), lake)
    assert coverage.tolist() == [True, False]


def test_quote_lake_returns_none_for_uncovered_window(tmp_path: Path) -> None:
    assert load_quote_windows(pd.DataFrame({
        "session_date": [pd.Timestamp("2025-05-01")], "symbol": ["AAPL"],
        "request_ts": [pd.Timestamp("2025-05-01 14:00:00", tz="UTC")],
    }), tmp_path / "missing") is None

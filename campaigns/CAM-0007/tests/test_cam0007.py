import pandas as pd

from cam0007 import (
    allocate_equal,
    canonicalize_news_events,
    is_earnings_release_headline,
    marketable_long_return,
    map_announcement_to_session,
    max_drawdown_and_recovery,
    protected_short_return,
    session_offset,
)


def test_conservative_release_detector_rejects_previews_and_guidance_updates():
    assert is_earnings_release_headline(
        "Acme Q2 2025 Adj EPS $1.20 Beats $1.10 Estimate, Sales Beat"
    )
    assert is_earnings_release_headline(
        "Acme Q2 Earnings: Revenue Beat, EPS Miss, Guidance Raised"
    )
    assert not is_earnings_release_headline(
        "Acme Gears Up For Q2 Print Ahead Of Earnings"
    )
    assert not is_earnings_release_headline(
        "Acme Updates Q4 2024 Adj EPS To $2.10 Vs $2.30 Est."
    )


def test_news_clustering_keeps_first_explicit_release_per_36_hours():
    frame = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "created_at": [
                "2025-01-01T21:00:00Z",
                "2025-01-01T21:20:00Z",
                "2025-04-01T20:00:00Z",
            ],
            "symbol": ["ABC", "ABC", "ABC"],
            "single_symbol": [True, True, True],
            "headline": [
                "ABC Q4 2024 EPS $1 Beats $0.9 Estimate",
                "ABC Q4 Earnings: Revenue Beat, EPS Beat",
                "ABC Q1 2025 EPS $1.1 Beats $1 Estimate",
            ],
        }
    )
    result = canonicalize_news_events(frame)
    assert len(result) == 2
    assert result.iloc[0]["news_id"] == 1


def test_announcement_mapping_is_causal_and_excludes_during_session():
    sessions = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
    session, bucket = map_announcement_to_session(
        pd.Timestamp("2025-01-02 21:00:00", tz="UTC"), sessions
    )
    assert bucket == "after_close"
    assert session == pd.Timestamp("2025-01-03")
    session, bucket = map_announcement_to_session(
        pd.Timestamp("2025-01-03 12:00:00", tz="UTC"), sessions
    )
    assert bucket == "premarket"
    assert session == pd.Timestamp("2025-01-03")
    session, bucket = map_announcement_to_session(
        pd.Timestamp("2025-01-03 16:00:00", tz="UTC"), sessions
    )
    assert session is None
    assert bucket == "during_session_excluded"


def test_multiday_session_offsets_do_not_use_calendar_days():
    sessions = pd.to_datetime(
        ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]
    )
    assert session_offset(pd.Timestamp("2025-01-03"), sessions, 1) == pd.Timestamp(
        "2025-01-06"
    )
    assert session_offset(pd.Timestamp("2025-01-03"), sessions, 2) == pd.Timestamp(
        "2025-01-07"
    )
    assert session_offset(pd.Timestamp("2025-01-03"), sessions, 3) is None


def test_cost_short_stop_allocation_and_fixed_base_drawdown():
    assert abs(marketable_long_return(100, 101, 10) - 0.008) < 1e-12
    pnl, stopped, exit_price = protected_short_return(
        100, 98, [100.5, 102.1], 0.02, 10, 10
    )
    assert stopped and exit_price > 102 and pnl < -0.02
    assert abs(allocate_equal([0.02, 0.04]) - 0.03) < 1e-12
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-06"]
            ),
            "net_pnl": [0.10, -0.05, -0.05, 0.11],
        }
    )
    drawdown, days, unresolved = max_drawdown_and_recovery(daily)
    assert abs(drawdown - (0.10 / 1.10)) < 1e-12
    assert days == 3
    assert not unresolved
    initial_loss = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01", "2025-01-02"]),
            "net_pnl": [-0.10, 0.02],
        }
    )
    drawdown, days, unresolved = max_drawdown_and_recovery(initial_loss)
    assert abs(drawdown - 0.10) < 1e-12
    assert days is None
    assert unresolved

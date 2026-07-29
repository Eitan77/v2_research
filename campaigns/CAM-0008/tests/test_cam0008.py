import pandas as pd

from cam0008 import (
    equal_available_allocations,
    map_event_clock,
    marketable_long_return,
    max_drawdown_and_recovery,
    parse_action,
    protected_short_return,
)


def test_explicit_action_detector_and_exclusions():
    assert parse_action(
        "Piper Sandler Upgrades Fortinet to Overweight, Raises Price Target to $120"
    )["action_sign"] == 1
    assert parse_action(
        "Wells Fargo Downgrades T-Mobile US to Equal-Weight, Lowers Price Target to $220"
    )["action_sign"] == -1
    assert parse_action(
        "TD Cowen Initiates Coverage On Axon Enterprise with Buy Rating, Announces Price Target of $700"
    )["action_type"] == "positive_initiation"
    assert parse_action(
        "Mizuho Maintains Outperform on Western Digital, Lowers Price Target to $82"
    )["action_type"] == "target_lower"
    assert parse_action(
        "Cantor Fitzgerald Initiates Coverage On Palantir with Neutral Rating"
    ) is None
    assert parse_action(
        "This Tesla Analyst Is No Longer Bearish; Here Are Top 5 Upgrades"
    ) is None
    assert parse_action("Company Announces Software Upgrade") is None


def test_event_clock_uses_full_post_release_minutes():
    sessions = pd.to_datetime(["2025-01-03", "2025-01-06", "2025-01-07"])
    intraday = map_event_clock(
        pd.Timestamp("2025-01-03 15:02:30", tz="UTC"), sessions
    )
    assert intraday["release_bucket"] == "intraday"
    assert intraday["reaction_start_minute"] == "10:03"
    assert intraday["entry_minute"] == "10:08"
    after = map_event_clock(
        pd.Timestamp("2025-01-03 22:00:00", tz="UTC"), sessions
    )
    assert after["entry_session"] == pd.Timestamp("2025-01-06")
    assert after["entry_minute"] == "09:35"
    late = map_event_clock(
        pd.Timestamp("2025-01-03 20:50:00", tz="UTC"), sessions
    )
    assert late["mapping_status"] == "too_late_excluded"
    early_close = map_event_clock(
        pd.Timestamp("2025-01-03 18:05:00", tz="UTC"),
        sessions,
        {pd.Timestamp("2025-01-03"): "13:00"},
    )
    assert early_close["release_bucket"] == "after_close"
    assert early_close["entry_session"] == pd.Timestamp("2025-01-06")


def test_cost_stop_and_initial_capital_drawdown():
    assert abs(marketable_long_return(100, 101, 10) - 0.008) < 1e-12
    pnl, stopped, _ = protected_short_return(
        100, 99, [100.5, 102.1], 0.02, 10
    )
    assert stopped and pnl < -0.02
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01", "2025-01-02"]),
            "net_pnl": [-0.10, 0.02],
        }
    )
    drawdown, recovery, unresolved = max_drawdown_and_recovery(daily)
    assert abs(drawdown - 0.10) < 1e-12
    assert recovery is None and unresolved
    allocations = equal_available_allocations(3, 0.5, 0.2)
    assert allocations == [1 / 6] * 3
    assert sum(allocations) <= 0.5 + 1e-12

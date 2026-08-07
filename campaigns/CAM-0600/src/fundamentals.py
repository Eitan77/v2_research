from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from suite_core import CAMPAIGNS, Panel, month_end_indices


FACT_PATH = CAMPAIGNS / "CAM-0600" / "artifacts" / "shared" / "fundamental_facts.parquet"
GROUPS = {
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "cash": (
        "CashAndShortTermInvestments",
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ),
    "shares": (
        "WeightedAverageNumberOfDilutedAmericanDepositarySharesOutstanding",
        "EntityCommonStockSharesOutstanding",
        "CommonStockSharesOutstanding",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ),
}
DURATION_GROUPS = {"net_income", "revenue"}


@dataclass
class FundamentalMatrices:
    book_to_price: np.ndarray
    market_cap: np.ndarray
    chs_logit: np.ndarray
    profitability: np.ndarray
    leverage: np.ndarray
    cash_ratio: np.ndarray
    coverage: dict[str, Any]


class FundamentalStore:
    def __init__(self, path: Path = FACT_PATH):
        facts = pd.read_parquet(path)
        facts = facts[facts["symbol"].notna()].copy()
        facts["symbol"] = facts["symbol"].astype(str)
        facts["filed"] = pd.to_datetime(facts["filed"])
        facts["period_end"] = pd.to_datetime(facts["period_end"])
        facts["duration_days"] = pd.to_numeric(facts["duration_days"], errors="coerce")
        facts["value"] = pd.to_numeric(facts["value"], errors="coerce")
        facts = facts[
            facts["value"].notna()
            & facts["period_end"].notna()
            & (facts["filed"] <= pd.Timestamp("2026-04-30"))
        ]
        self.path = path
        self.facts = facts
        self.by_symbol_group: dict[tuple[str, str], tuple[np.ndarray, ...]] = {}
        for symbol, frame in facts.groupby("symbol", sort=False):
            for group, tags in GROUPS.items():
                x = frame[frame["tag"].isin(tags)].copy()
                if group in DURATION_GROUPS:
                    x = x[x["duration_days"].between(300, 400, inclusive="both")]
                elif group == "shares":
                    x = x[x["duration_days"].isna() | x["duration_days"].between(300, 400, inclusive="both")]
                else:
                    x = x[x["duration_days"].isna()]
                if x.empty:
                    continue
                priority = {tag: i for i, tag in enumerate(tags)}
                x["_priority"] = x["tag"].map(priority).fillna(999).astype(int)
                self.by_symbol_group[(str(symbol), group)] = (
                    x["filed"].to_numpy(dtype="datetime64[ns]"),
                    x["period_end"].to_numpy(dtype="datetime64[ns]"),
                    x["value"].to_numpy(float),
                    x["tag"].astype(str).to_numpy(),
                    x["_priority"].to_numpy(int),
                )

    def latest(
        self,
        symbol: str,
        signal_date: pd.Timestamp,
        group: str,
    ) -> tuple[float | None, pd.Timestamp | None, pd.Timestamp | None, str | None]:
        arrays = self.by_symbol_group.get((symbol, group))
        if arrays is None:
            return None, None, None, None
        filed, period_end, values, tags, priority = arrays
        cutoff = np.datetime64(signal_date.to_datetime64(), "ns")
        candidates = np.flatnonzero((filed < cutoff) & (period_end < cutoff))
        if len(candidates) == 0:
            return None, None, None, None
        order = np.lexsort((priority[candidates], -filed[candidates].astype("int64"), -period_end[candidates].astype("int64")))
        chosen = int(candidates[order[0]])
        value = float(values[chosen])
        if not np.isfinite(value):
            return None, None, None, None
        return value, pd.Timestamp(period_end[chosen]), pd.Timestamp(filed[chosen]), str(tags[chosen])


def _split_adjusted_shares(
    panel: Panel,
    col: int,
    signal_idx: int,
    shares: float,
    shares_period: pd.Timestamp,
) -> float:
    period_idx = int(panel.dates.searchsorted(shares_period.normalize(), side="right") - 1)
    period_idx = max(0, min(signal_idx, period_idx))
    cumulative = np.cumprod(np.where(np.isfinite(panel.split_grid[:, col]), panel.split_grid[:, col], 1.0))
    base = float(cumulative[period_idx])
    current = float(cumulative[signal_idx])
    if base <= 0 or current <= 0:
        return np.nan
    return float(shares * current / base)


def build_fundamental_matrices(panel: Panel, store: FundamentalStore) -> FundamentalMatrices:
    shape = panel.adj_close.shape
    btp = np.full(shape, np.nan)
    market_cap = np.full(shape, np.nan)
    chs = np.full(shape, np.nan)
    profitability = np.full(shape, np.nan)
    leverage = np.full(shape, np.nan)
    cash_ratio = np.full(shape, np.nan)
    signal_indices = month_end_indices(panel.dates)
    vol63 = (
        pd.DataFrame(panel.total_return_index, index=panel.dates)
        .pct_change()
        .rolling(63, min_periods=50)
        .std(ddof=1)
        .to_numpy(float)
        * np.sqrt(252.0)
    )
    ret252 = np.full(shape, np.nan)
    if len(panel.dates) > 252:
        ret252[252:] = panel.total_return_index[252:] / panel.total_return_index[:-252] - 1.0
    benchmark_col = panel.symbol_to_col.get("SPY", panel.symbol_to_col.get("QQQ", 0))
    available_cells = 0
    missing_by_field = {k: 0 for k in ("equity", "assets", "liabilities", "cash", "net_income", "shares")}
    field_tag_counts: dict[str, int] = {}
    raw_rows: list[dict[str, Any]] = []
    for i in signal_indices:
        signal_date = panel.dates[i]
        eligible_cols = np.flatnonzero(panel.member[i] & np.isfinite(panel.raw_close[i]))
        row_values: dict[int, dict[str, float]] = {}
        for col in eligible_cols:
            symbol = str(panel.symbols[col])
            selected: dict[str, tuple[float | None, pd.Timestamp | None, pd.Timestamp | None, str | None]] = {
                group: store.latest(symbol, signal_date, group)
                for group in ("equity", "assets", "liabilities", "cash", "net_income", "shares")
            }
            for group, value in selected.items():
                if value[0] is None:
                    missing_by_field[group] += 1
                elif value[3]:
                    field_tag_counts[f"{group}:{value[3]}"] = field_tag_counts.get(f"{group}:{value[3]}", 0) + 1
            equity, _, _, _ = selected["equity"]
            assets, _, _, _ = selected["assets"]
            liabilities, _, _, _ = selected["liabilities"]
            cash, _, _, _ = selected["cash"]
            net_income, _, _, _ = selected["net_income"]
            shares, shares_period, _, _ = selected["shares"]
            if (
                shares is None
                or shares_period is None
                or not np.isfinite(panel.raw_close[i, col])
                or panel.raw_close[i, col] <= 0
            ):
                continue
            shares_adj = _split_adjusted_shares(panel, col, i, shares, shares_period)
            mcap = float(panel.raw_close[i, col] * shares_adj)
            if not np.isfinite(mcap) or mcap <= 0:
                continue
            market_cap[i, col] = mcap
            if equity is not None and equity > 0:
                btp[i, col] = float(equity / mcap)
            if liabilities is None and assets is not None and equity is not None:
                liabilities = assets - equity
            if assets is None and liabilities is not None:
                assets = mcap + liabilities
            if liabilities is None or liabilities < 0:
                continue
            mta = mcap + liabilities
            if mta <= 0:
                continue
            ni_ratio = float(net_income / mta) if net_income is not None else np.nan
            lev = float(liabilities / mta)
            cash_mta = float(max(cash or 0.0, 0.0) / mta) if cash is not None else np.nan
            profitability[i, col] = ni_ratio
            leverage[i, col] = lev
            cash_ratio[i, col] = cash_mta
            row_values[col] = {
                "mcap": mcap,
                "nimta": ni_ratio,
                "tlmta": lev,
                "cashmta": cash_mta,
                "mb": float(mcap / equity) if equity is not None and equity > 0 else np.nan,
            }
        total_market_cap = float(sum(x["mcap"] for x in row_values.values()))
        benchmark_return = ret252[i, benchmark_col] if np.isfinite(ret252[i, benchmark_col]) else 0.0
        for col, values in row_values.items():
            required = (
                values["nimta"], values["tlmta"], values["cashmta"], values["mb"],
                vol63[i, col], ret252[i, col],
            )
            if not all(np.isfinite(x) for x in required) or total_market_cap <= 0:
                continue
            exret = float(ret252[i, col] - benchmark_return)
            rsize = float(np.log(values["mcap"] / total_market_cap))
            price = float(np.log(min(max(panel.raw_close[i, col], 1e-6), 15.0)))
            chs[i, col] = (
                -20.26 * values["nimta"]
                + 1.42 * values["tlmta"]
                - 7.13 * exret
                + 1.41 * vol63[i, col]
                - 0.045 * rsize
                - 2.13 * values["cashmta"]
                + 0.075 * values["mb"]
                - 0.058 * price
                - 9.16
            )
            available_cells += 1
            raw_rows.append({
                "date": str(signal_date.date()),
                "symbol": str(panel.symbols[col]),
                "book_to_price": btp[i, col],
                "market_cap": market_cap[i, col],
                "chs_logit": chs[i, col],
                "profitability": profitability[i, col],
                "leverage": leverage[i, col],
                "cash_ratio": cash_ratio[i, col],
            })
    for matrix in (btp, market_cap, chs, profitability, leverage, cash_ratio):
        frame = pd.DataFrame(matrix)
        matrix[:] = frame.ffill().to_numpy(float)
    coverage = {
        "panel": panel.name,
        "month_end_signal_dates": int(len(signal_indices)),
        "member_cells_at_signal": int(sum(panel.member[i].sum() for i in signal_indices)),
        "book_to_price_cells": int(np.isfinite(btp[signal_indices]).sum()),
        "market_cap_cells": int(np.isfinite(market_cap[signal_indices]).sum()),
        "chs_complete_cells": int(available_cells),
        "missing_by_field": missing_by_field,
        "field_tag_counts": field_tag_counts,
        "signal_row_count": int(len(raw_rows)),
        "chs_definition": "Published CHS coefficients with annual point-in-time accounting proxy; market variables use daily data. This is not an exact quarterly NIMTAAVG replication.",
    }
    return FundamentalMatrices(
        book_to_price=btp,
        market_cap=market_cap,
        chs_logit=chs,
        profitability=profitability,
        leverage=leverage,
        cash_ratio=cash_ratio,
        coverage=coverage,
    )

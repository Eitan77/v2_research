from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
CAM = ROOT / "campaigns" / "CAM-0639"
OUT = CAM / "artifacts" / "RUN-0004"
CAT = Path(r"D:\AlgoResearch\data\catalog.duckdb")
COST = 4 / 1e4
CONT = [
    "mu_day_ret", "mu_first30_ret", "mu_last30_ret", "mu_range",
    "mu_close_loc", "mu_volume_ratio", "mu_prior_overnight", "mu_ret5",
    "mu_ret20", "qqq_day_ret", "smh_day_ret", "spy_day_ret", "mu_minus_smh",
]


def load_bars() -> pd.DataFrame:
    con = duckdb.connect(str(CAT), read_only=True)
    query = """
    select date, symbol, try_cast(timestamp as timestamptz) as ts,
           arg_max(open, try_cast(ingested_at as timestamp)) as open,
           arg_max(high, try_cast(ingested_at as timestamp)) as high,
           arg_max(low, try_cast(ingested_at as timestamp)) as low,
           arg_max(close, try_cast(ingested_at as timestamp)) as close,
           arg_max(volume, try_cast(ingested_at as timestamp)) as volume
    from bars_1m
    where date between date '2021-04-01' and date '2026-04-30'
      and feed='sip' and adjustment='raw'
      and symbol in ('MU','QQQ','SMH','SPY')
      and strftime(try_cast(timestamp as timestamptz) at time zone 'America/New_York','%H:%M')
          between '09:30' and '15:59'
    group by 1,2,3 order by 1,2,3
    """
    bars = con.execute(query).fetchdf()
    con.close()
    bars["date"] = pd.to_datetime(bars["date"])
    return bars


def make_daily(bars: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (date, symbol), g in bars.groupby(["date", "symbol"], sort=True):
        g = g.sort_values("ts").reset_index(drop=True)
        if len(g) < 2:
            continue
        # The final bar supplies the entry close. All predictors stop one bar earlier.
        f = g.iloc[:-1]
        first_n = f.iloc[: min(30, len(f))]
        last_n = f.iloc[max(0, len(f) - 30) :]
        lo, hi = float(f.low.min()), float(f.high.max())
        rows.append({
            "date": date, "symbol": symbol, "bars": len(g),
            "entry_close": float(g.close.iloc[-1]),
            "session_open": float(f.open.iloc[0]), "feature_close": float(f.close.iloc[-1]),
            "day_ret": float(f.close.iloc[-1] / f.open.iloc[0] - 1),
            "first30_ret": float(first_n.close.iloc[-1] / first_n.open.iloc[0] - 1),
            "last30_ret": float(last_n.close.iloc[-1] / last_n.open.iloc[0] - 1),
            "range": float(hi / lo - 1),
            "close_loc": float((f.close.iloc[-1] - lo) / (hi - lo)) if hi > lo else 0.5,
            "volume": float(f.volume.fillna(0).sum()),
        })
    return pd.DataFrame(rows)


def make_panel(daily: pd.DataFrame) -> pd.DataFrame:
    wide = {}
    for symbol in ["MU", "QQQ", "SMH", "SPY"]:
        x = daily[daily.symbol == symbol].copy().sort_values("date").set_index("date")
        wide[symbol] = x
    mu = wide["MU"].copy()
    p = pd.DataFrame(index=mu.index)
    for col in ["day_ret", "first30_ret", "last30_ret", "range", "close_loc"]:
        p[f"mu_{col}"] = mu[col]
    p["mu_volume_ratio"] = mu.volume / mu.volume.shift(1).rolling(20).median()
    p["mu_prior_overnight"] = mu.session_open / mu.entry_close.shift(1) - 1
    p["mu_ret5"] = mu.entry_close / mu.entry_close.shift(5) - 1
    p["mu_ret20"] = mu.entry_close / mu.entry_close.shift(20) - 1
    for symbol in ["QQQ", "SMH", "SPY"]:
        p[f"{symbol.lower()}_day_ret"] = wide[symbol].day_ret.reindex(p.index)
    p["mu_minus_smh"] = p.mu_day_ret - p.smh_day_ret
    p["dow"] = p.index.dayofweek
    p["entry_close"] = mu.entry_close
    p["exit_open"] = mu.session_open.shift(-1)
    p["exit_date"] = pd.Series(mu.index, index=mu.index).shift(-1)
    p["gross_return"] = p.exit_open / p.entry_close - 1
    p["green"] = (p.gross_return > 0).astype(int)
    return p.reset_index().rename(columns={"date": "entry_date"})


def design(train: pd.DataFrame, test: pd.DataFrame):
    mean, std = train[CONT].mean(), train[CONT].std().replace(0, 1)
    def one(x):
        z = (x[CONT] - mean) / std
        dow = np.eye(5)[x.dow.astype(int).clip(0, 4)]
        return np.column_stack([np.ones(len(x)), z.to_numpy(), dow[:, 1:]])
    return one(train), one(test)


def logistic_fit(x: np.ndarray, y: np.ndarray, lam: float = 10.0) -> np.ndarray:
    beta = np.zeros(x.shape[1])
    penalty = np.eye(x.shape[1]) * lam
    penalty[0, 0] = 0
    for _ in range(60):
        eta = np.clip(x @ beta, -30, 30)
        prob = 1 / (1 + np.exp(-eta))
        w = np.clip(prob * (1 - prob), 1e-5, None)
        grad = x.T @ (prob - y) + penalty @ beta
        hess = (x.T * w) @ x + penalty
        step = np.linalg.solve(hess, grad)
        beta -= step
        if np.max(np.abs(step)) < 1e-9:
            break
    return beta


def ridge_fit(x: np.ndarray, y: np.ndarray, lam: float = 10.0) -> np.ndarray:
    penalty = np.eye(x.shape[1]) * lam
    penalty[0, 0] = 0
    return np.linalg.solve(x.T @ x + penalty, x.T @ y)


def auc(y: np.ndarray, score: np.ndarray) -> float:
    pos, neg = int(y.sum()), int(len(y) - y.sum())
    if not pos or not neg:
        return float("nan")
    ranks = pd.Series(score).rank(method="average").to_numpy()
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def metrics(x: pd.DataFrame) -> dict:
    if x.empty:
        return {"trades": 0}
    net = x.gross_return - COST
    eq = 1 + net.cumsum()
    dd = (eq.cummax() - eq) / eq.cummax()
    yearly = net.groupby(x.exit_date.dt.year).agg(["count", "sum", "mean"])
    return {
        "trades": int(len(x)), "gross_additive_return": float(x.gross_return.sum()),
        "net_additive_return_2bp_side": float(net.sum()),
        "mean_net_bp": float(net.mean() * 1e4), "green_rate_gross": float((x.gross_return > 0).mean()),
        "max_drawdown": float(dd.max()),
        "top_abs_trade_share": float(x.gross_return.abs().max() / x.gross_return.abs().sum()),
        "positive_test_years": int((yearly["sum"] > 0).sum()),
        "test_years": int(len(yearly)),
        "yearly": {str(int(k)): {"trades": int(v["count"]), "net_return": float(v["sum"]),
                                  "mean_net_bp": float(v["mean"] * 1e4)} for k, v in yearly.iterrows()},
    }


def simple_rules(x: pd.DataFrame):
    rules = {
        "all": np.ones(len(x), dtype=bool),
        "mu_day_green": x.mu_day_ret > 0, "mu_day_red": x.mu_day_ret <= 0,
        "last30_green": x.mu_last30_ret > 0, "last30_red": x.mu_last30_ret <= 0,
        "close_top_quartile": x.mu_close_loc >= .75, "close_bottom_quartile": x.mu_close_loc <= .25,
        "high_volume_1p5x": x.mu_volume_ratio >= 1.5,
        "mu_outperformed_smh": x.mu_minus_smh > 0, "mu_underperformed_smh": x.mu_minus_smh <= 0,
        "smh_green": x.smh_day_ret > 0, "smh_red": x.smh_day_ret <= 0,
        "qqq_green": x.qqq_day_ret > 0, "qqq_red": x.qqq_day_ret <= 0,
        "prior_overnight_green": x.mu_prior_overnight > 0, "prior_overnight_red": x.mu_prior_overnight <= 0,
        "red_day_last30_green": (x.mu_day_ret <= 0) & (x.mu_last30_ret > 0),
        "green_day_last30_green": (x.mu_day_ret > 0) & (x.mu_last30_ret > 0),
        "red_day_high_volume": (x.mu_day_ret <= 0) & (x.mu_volume_ratio >= 1.5),
    }
    out, details = [], {}
    for name, mask in rules.items():
        m = metrics(x.loc[np.asarray(mask)].copy())
        details[name] = m
        out.append({"rule": name, **{k: v for k, v in m.items() if k != "yearly"}})
    return pd.DataFrame(out).sort_values("mean_net_bp", ascending=False), details


def top_quartile_audit(x: pd.DataFrame) -> dict:
    top = x.mu_close_loc >= .75
    a, b = x[top].copy(), x[~top].copy()
    pa, pb = float((a.gross_return > 0).mean()), float((b.gross_return > 0).mean())
    se = np.sqrt(pa * (1-pa) / len(a) + pb * (1-pb) / len(b))
    # Resample whole calendar weeks to preserve local serial dependence.
    z = x.assign(top=top, week=x.entry_date.dt.to_period("W").astype(str))
    w = z.groupby(["week", "top"]).gross_return.agg(["sum", "count"]).unstack(fill_value=0)
    weeks = np.arange(len(w)); rng = np.random.default_rng(639); lifts = []
    for _ in range(10000):
        s = rng.choice(weeks, len(weeks), replace=True)
        am = w[("sum", True)].to_numpy()[s].sum() / w[("count", True)].to_numpy()[s].sum()
        bm = w[("sum", False)].to_numpy()[s].sum() / w[("count", False)].to_numpy()[s].sum()
        lifts.append((am-bm)*1e4)
    return {
        "top_quartile": metrics(a), "complement": metrics(b),
        "green_rate_lift_percentage_points": float((pa-pb)*100),
        "green_rate_difference_z_independent_approximation": float((pa-pb)/se),
        "mean_gross_return_lift_bp": float((a.gross_return.mean()-b.gross_return.mean())*1e4),
        "calendar_week_block_bootstrap_mean_lift_bp_95ci": [float(v) for v in np.quantile(lifts,[.025,.975])],
        "bootstrap_repetitions": 10000,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    bars = load_bars()
    daily = make_daily(bars)
    panel = make_panel(daily)
    expected = pd.read_csv(CAM / "artifacts" / "RUN-0001" / "roles.csv", parse_dates=["entry_date", "exit_date"])
    panel = panel.merge(expected[["entry_date", "exit_date", "entry_bar_close", "exit_bar_open"]],
                        on=["entry_date", "exit_date"], how="inner", validate="one_to_one")
    if not np.allclose(panel.entry_close, panel.entry_bar_close) or not np.allclose(panel.exit_open, panel.exit_bar_open):
        raise RuntimeError("Panel endpoints do not reconcile with frozen role ledger")
    before = len(panel)
    panel = panel.dropna(subset=CONT + ["gross_return"]).copy()
    if panel.entry_date.max() > pd.Timestamp("2026-04-29") or panel.exit_date.max() > pd.Timestamp("2026-04-30"):
        raise RuntimeError("Holdout boundary violation")
    predictions = []
    for year in [2023, 2024, 2025, 2026]:
        train = panel[panel.entry_date.dt.year < year].copy()
        test = panel[panel.entry_date.dt.year == year].copy()
        if test.empty:
            continue
        xtr, xte = design(train, test)
        b_log = logistic_fit(xtr, train.green.to_numpy(float))
        tr_prob = 1 / (1 + np.exp(-np.clip(xtr @ b_log, -30, 30)))
        te_prob = 1 / (1 + np.exp(-np.clip(xte @ b_log, -30, 30)))
        b_ridge = ridge_fit(xtr, train.gross_return.to_numpy(float))
        tr_ret, te_ret = xtr @ b_ridge, xte @ b_ridge
        test = test.copy()
        test["test_year"] = year
        test["logistic_score"] = te_prob
        test["logistic_selected"] = te_prob >= np.quantile(tr_prob, .75)
        test["ridge_score"] = te_ret
        test["ridge_selected"] = te_ret >= np.quantile(tr_ret, .75)
        predictions.append(test)
    oos = pd.concat(predictions, ignore_index=True)
    simple, simple_details = simple_rules(oos)
    model_metrics = {}
    for model in ["logistic", "ridge"]:
        selected = oos[oos[f"{model}_selected"]].copy()
        model_metrics[model] = metrics(selected)
    y = oos.green.to_numpy()
    classification = {
        "oos_rows": int(len(oos)), "green_base_rate": float(y.mean()),
        "logistic_auc": auc(y, oos.logistic_score.to_numpy()),
        "logistic_accuracy_at_0p5": float(((oos.logistic_score >= .5).astype(int) == y).mean()),
        "majority_class_accuracy": float(max(y.mean(), 1-y.mean())),
        "logistic_brier": float(np.mean((oos.logistic_score.to_numpy() - y) ** 2)),
        "ridge_return_rank_auc": auc(y, oos.ridge_score.to_numpy()),
    }
    report = {
        "status": "complete", "causal_entry": "predictors through penultimate regular-session bar; entry at final one-minute close",
        "source": "raw SIP one-minute bars; dividends excluded", "cost_bp_per_side": 2,
        "loaded_rows": int(len(bars)), "daily_symbol_sessions": int(len(daily)),
        "role_overnights": int(len(expected)), "merged_overnights_before_feature_attrition": int(before),
        "feature_complete_overnights": int(len(panel)), "attrition": int(len(expected)-len(panel)),
        "first_oos_entry": str(oos.entry_date.min().date()), "final_oos_exit": str(oos.exit_date.max().date()),
        "classification": classification, "all_oos": metrics(oos), "models": model_metrics,
        "simple_rules": simple.to_dict(orient="records"), "simple_rule_details": simple_details,
        "top_quartile_audit": top_quartile_audit(oos),
        "interpretation_gate": "A useful discriminator must show out-of-sample lift, positive net performance across years, and adequate activity; full-sample rule ranking is descriptive only.",
    }
    panel.to_csv(OUT / "feature_panel.csv", index=False)
    oos.to_csv(OUT / "oos_predictions.csv", index=False)
    simple.to_csv(OUT / "simple_rules.csv", index=False)
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"classification": classification, "all_oos": report["all_oos"],
                      "models": model_metrics, "top_simple_rules": simple.head(8).to_dict(orient="records"),
                      "top_quartile_audit": report["top_quartile_audit"],
                      "attrition": report["attrition"]}, indent=2))


if __name__ == "__main__":
    main()

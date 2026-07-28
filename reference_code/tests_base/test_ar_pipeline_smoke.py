from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb
import pandas as pd

from ar_pipeline.approvals import approve_quote_fill
from ar_pipeline.config import write_structured
from ar_pipeline.data import apply_session_filter
from ar_pipeline.engines.quote_fill import run_quote_fill
from ar_pipeline.engines.quote_fill import _write_summary
from ar_pipeline.engines.trade_audit import run_trade_audit
from ar_pipeline.discovery import run_discovery as run_discovery_dispatch
from ar_pipeline.execution import WorkloadInfo, resolve_execution
from ar_pipeline.manifest import RunContext
from ar_pipeline.stages import s03_promote
from ar_pipeline.stages.s04_quote_fill import _promoted_trade_ledger


class PipelineSmokeTest(unittest.TestCase):
    def test_discovery_quote_fill_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog.duckdb"
            df = self._matrix()
            con = duckdb.connect(str(catalog))
            con.register("matrix_df", df)
            con.execute("create table research_matrix as select * from matrix_df")
            con.close()

            config = {
                "schema_version": 2,
                "research": {"sealed_holdout": {"start": "2026-01-04", "end": "2026-01-05", "locked": True}},
                "data": {
                    "catalog_path": str(catalog),
                    "table": "research_matrix",
                    "feed": "sip",
                    "adjustment": "raw",
                    "bar_timestamp_label": "start",
                    "universe": {"mode": "all"},
                },
                "scan": {
                    "engine": "cross_sectional_rank",
                    "family": "smoke",
                    "timeframe": "15m",
                    "train_start": "2026-01-01",
                    "train_end": "2026-01-03",
                    "holding_bars": 1,
                    "entry_model": "next_actionable_bar_open",
                    "decision_latency_ms": 0,
                    "features": ["close_vs_sma_20", "rsi_14"],
                    "formulas": 8,
                    "batch_size": 4,
                    "top_ns": [1, 2],
                    "cost_bps_per_side_grid": [0.0, 1.0, 5.0],
                    "execution": {
                        "device": "cpu",
                        "workers": "auto",
                        "batch_size": 4,
                        "benchmark": True,
                        "fail_if_cpu_fallback": False,
                    },
                    "seed": 7,
                    "keep_trades_for_top": 3,
                },
                "quote_fill": {"mode": "source_proxy_test_only", "extra_bps_per_side": 2.0},
            }
            outputs = run_discovery_dispatch(config, root / "stage_02_discovery")
            leaderboard = pd.read_parquet(outputs["leaderboard_parquet"])
            self.assertFalse(leaderboard.empty)
            self.assertGreater(leaderboard["cost_bps_per_side"].nunique(), 1)
            self.assertTrue((root / "stage_02_discovery" / "leaderboard.parquet").exists())
            self.assertTrue((root / "stage_02_discovery" / "cost_sensitivity.csv").exists())
            self.assertTrue((root / "stage_02_discovery" / "execution_preflight.json").exists())
            quote = run_quote_fill(config, root / "stage_02_discovery" / "discovery_trades.parquet", root / "stage_04_quote_fill")
            self.assertIn("quote_return", quote.columns)
            summary = run_trade_audit(root / "stage_04_quote_fill" / "quote_filled_trades.parquet", root / "stage_07_trade_audit")
            self.assertGreater(summary["trades"], 0)

    def test_unknown_strategy_adapter_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "Unknown scan.engine"):
                run_discovery_dispatch({"scan": {"engine": "not_a_real_adapter"}}, Path(tmp))

    def test_require_accelerated_rejects_cpu_only_workload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = {"scan": {"execution": {"device": "auto", "require_accelerated": True}}}
            workload = WorkloadInfo(
                pattern="cpu_only_test",
                preferred_device="cpu",
                supports_cuda=False,
                supports_cpu=True,
                supports_batch_autotune=False,
            )
            with self.assertRaisesRegex(RuntimeError, "does not support CUDA"):
                resolve_execution(config, workload, Path(tmp))

    def test_rth_session_filter_removes_non_executable_times(self) -> None:
        df = pd.DataFrame(
            {
                "timestamp": [
                    "2026-01-02 14:30:00Z",  # valid RTH open
                    "2026-01-02 12:00:00Z",  # premarket
                    "2026-01-02 20:15:00Z",  # exit would exceed 16:00 ET
                    "2026-01-01 15:00:00Z",  # New Year's Day
                ],
                "symbol": ["A", "B", "C", "D"],
            }
        )
        out = apply_session_filter(df, {"timeframe": "15m", "horizon": 4, "session": "rth"}, horizon=4)
        self.assertEqual(out["symbol"].tolist(), ["A"])

    def test_quote_summary_uses_filled_only_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            data = pd.DataFrame(
                {
                    "candidate_id": ["c1", "c1", "c1"],
                    "source_return": [0.10, 0.10, -0.50],
                    "quote_return": [0.08, pd.NA, -0.10],
                    "source_quote_gap": [-0.02, pd.NA, 0.40],
                    "quote_entry_spread_bps": [5.0, pd.NA, 7.0],
                    "quote_exit_spread_bps": [4.0, pd.NA, 6.0],
                }
            )
            summary = _write_summary(data, output, "test")
            row = summary.iloc[0]
            self.assertEqual(row["sampled_trades"], 3)
            self.assertEqual(row["filled_trades"], 2)
            self.assertAlmostEqual(row["source_total_all_sampled"], -0.395)
            self.assertAlmostEqual(row["source_total_filled_only"], -0.45)
            self.assertAlmostEqual(row["quote_total_filled_only"], -0.028)

    def test_quote_stage_requires_fingerprinted_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog.duckdb"
            df = self._matrix()
            con = duckdb.connect(str(catalog))
            con.register("matrix_df", df)
            con.execute("create table research_matrix as select * from matrix_df")
            con.close()
            config = self._safe_config(catalog, formulas=1)
            config["scan"]["train_start"] = "2026-01-02"
            config["scan"]["train_end"] = "2026-01-03"
            config["research"]["sealed_holdout"] = {"start": "2026-01-04", "end": "2026-01-05", "locked": True}
            run_discovery_dispatch(config, root / "stage_02_discovery")
            stage3 = root / "stage_03_promotion_review"
            stage3.mkdir(parents=True)
            pd.DataFrame(
                {"base_candidate_id": ["f0_top1_h1"], "agent_decision": ["needs_agent_review"], "screening_eligibility": [True]}
            ).to_csv(stage3 / "promotion_review_queue.csv", index=False)
            write_structured(root / "scan.yaml", config)
            ctx = RunContext(run_path=root, manifest={}, config=config)
            with self.assertRaisesRegex(Exception, "approval"):
                _promoted_trade_ledger(ctx, root / "stage_04_quote_fill")
            approve_quote_fill(root, ["f0_top1_h1"], rationale="Smoke-test canonical ledger.", reviewer="test")
            path = _promoted_trade_ledger(ctx, root / "stage_04_quote_fill")
            trades = pd.read_parquet(path)
            self.assertEqual(set(trades["candidate_id"]), {"f0_top1_h1"})
            self.assertEqual(trades.groupby("timestamp").size().max(), 1)

    def test_promotion_queue_includes_tracking_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage2 = root / "stage_02_discovery"
            stage2.mkdir(parents=True)
            pd.DataFrame(
                {
                    "candidate_id": ["x0000001_c0p0", "x0000001_c2p0"],
                    "base_candidate_id": ["x0000001", "x0000001"],
                    "family": ["test", "test"],
                    "formula_id": [1, 1],
                    "top_n": [1, 1],
                    "horizon": [1, 1],
                    "cost_bps_per_side": [0.0, 2.0],
                    "spec": ["{}", "{}"],
                    "trades": [120.0, 120.0],
                    "decision_points": [120.0, 120.0],
                    "win_rate": [0.55, 0.54],
                    "avg_return": [0.003, 0.0026],
                    "log_total_return": [0.3, 0.25],
                    "total_return": [0.35, 0.28],
                    "cagr": [0.20, 0.17],
                    "max_drawdown": [-0.20, -0.22],
                }
            ).to_parquet(stage2 / "leaderboard.parquet", index=False)
            pd.DataFrame(
                {
                    "base_candidate_id": ["x0000001"],
                    "max_profitable_cost_bps_per_side": [2.0],
                    "log_return_at_lowest_cost": [0.3],
                    "log_return_at_highest_cost": [0.25],
                    "return_at_lowest_cost": [0.35],
                    "return_at_highest_cost": [0.28],
                    "cagr_at_highest_cost": [0.17],
                    "worst_drawdown_across_costs": [-0.22],
                }
            ).to_csv(stage2 / "cost_sensitivity.csv", index=False)
            ctx = _DummyContext(
                root,
                {"promotion": {"min_trades": 50, "min_cagr": 0.0, "max_drawdown": -0.50, "review_top": 10}},
            )
            s03_promote.run(ctx)
            queue = pd.read_csv(root / "stage_03_promotion_review" / "promotion_review_queue.csv")
            for column in ["promotion_rank", "cost_grid_snapshot", "promotion_flags", "agent_tracking_status", "agent_review_checklist"]:
                self.assertIn(column, queue.columns)
            self.assertIn("low_cost_survivor", queue.loc[0, "promotion_flags"])

    @staticmethod
    def _safe_config(catalog: Path, *, formulas: int = 8) -> dict:
        return {
            "schema_version": 2,
            "research": {"sealed_holdout": {"start": "2026-01-04", "end": "2026-01-05", "locked": True}},
            "data": {
                "catalog_path": str(catalog),
                "table": "research_matrix",
                "feed": "sip",
                "adjustment": "raw",
                "bar_timestamp_label": "start",
                "universe": {"mode": "all"},
            },
            "scan": {
                "engine": "cross_sectional_rank",
                "family": "smoke",
                "timeframe": "15m",
                "train_start": "2026-01-01",
                "train_end": "2026-01-03",
                "holding_bars": 1,
                "entry_model": "next_actionable_bar_open",
                "decision_latency_ms": 0,
                "features": ["close_vs_sma_20", "rsi_14"],
                "formulas": formulas,
                "batch_size": 4,
                "top_ns": [1, 2],
                "cost_bps_per_side_grid": [0.0, 1.0, 5.0],
                "execution": {"device": "cpu", "workers": "auto", "batch_size": 4, "benchmark": False, "fail_if_cpu_fallback": False},
                "seed": 7,
                "keep_trades_for_top": 3,
            },
            "quote_fill": {"mode": "source_proxy_test_only", "extra_bps_per_side": 2.0},
        }

    @staticmethod
    def _matrix() -> pd.DataFrame:
        rows = []
        stamps = pd.date_range("2026-01-02 14:30:00Z", periods=6, freq="15min")
        symbols = ["AAA", "BBB", "CCC"]
        for t_i, ts in enumerate(stamps):
            for s_i, symbol in enumerate(symbols):
                rows.append(
                    {
                        "symbol": symbol,
                        "timestamp": ts.isoformat().replace("+00:00", "Z"),
                        "timeframe": "15m",
                        "open": 100.0 + s_i,
                        "high": 101.0 + s_i,
                        "low": 99.0 + s_i,
                        "close": 100.5 + s_i,
                        "volume": 1000 + s_i,
                        "close_vs_sma_20": (s_i - 1) * 0.01 + t_i * 0.001,
                        "rsi_14": 40.0 + s_i * 5 + t_i,
                        "fwd_return_1": 0.003 * s_i - 0.001 * t_i,
                        "fwd_mfe_1": 0.005,
                        "fwd_mae_1": -0.003,
                        "is_qqq_member": True,
                        "feed": "sip",
                        "adjustment": "raw",
                    }
                )
        return pd.DataFrame(rows)


class _DummyContext:
    def __init__(self, root: Path, config: dict) -> None:
        self.root = root
        self.config = config

    def stage_dir(self, stage: int) -> Path:
        names = {
            2: "stage_02_discovery",
            3: "stage_03_promotion_review",
            4: "stage_04_quote_fill",
        }
        return self.root / names[stage]


if __name__ == "__main__":
    unittest.main()

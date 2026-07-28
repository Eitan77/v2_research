from __future__ import annotations

from pathlib import Path

import pandas as pd

from alpaca_research.ingest import pull_corporate_actions


class FakeClient:
    def paged_data_get(self, path: str, params: dict):
        assert path == "/v1/corporate-actions"
        yield {
            "corporate_actions": {
                "cash_dividends": [
                    {
                        "id": "div-1",
                        "symbol": "QQQ",
                        "ex_date": "2024-03-22",
                        "process_date": "2024-03-25",
                        "rate": 0.50,
                    }
                ],
                "reverse_splits": [
                    {
                        "id": "split-1",
                        "symbol": "SQQQ",
                        "ex_date": "2024-11-07",
                        "process_date": "2024-11-07",
                        "old_rate": 5,
                        "new_rate": 1,
                    }
                ],
            },
            "next_page_token": None,
        }


def test_corporate_action_types_keep_their_own_schema(tmp_path: Path) -> None:
    manifest = pull_corporate_actions(
        FakeClient(),
        tmp_path,
        "2024-01-01",
        "2024-12-31",
        ["QQQ", "SQQQ"],
        show_progress=False,
    )
    assert manifest["rows"] == 2
    assert len(manifest["outputs"]) == 2
    dividend_path = next(Path(x) for x in manifest["outputs"] if "action_type=cash_dividends" in x)
    split_path = next(Path(x) for x in manifest["outputs"] if "action_type=reverse_splits" in x)
    dividend = pd.read_parquet(dividend_path)
    split = pd.read_parquet(split_path)
    assert dividend.loc[0, "rate"] == 0.50
    assert split.loc[0, "old_rate"] == 5
    assert split.loc[0, "new_rate"] == 1

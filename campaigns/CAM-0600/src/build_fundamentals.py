from __future__ import annotations

import argparse
import json
import multiprocessing as mp
from pathlib import Path
from typing import Any

import pandas as pd

from suite_core import CAMPAIGNS, CUTOFF, sha256, write_json


SEED = CAMPAIGNS / "CAM-0515" / "artifacts" / "RUN-0007"
FACT_DIR = SEED / "sec_cache" / "annual_facts"
IDENTITY = SEED / "sec_identity_map.json"
TAGS = {
    "Assets", "Liabilities", "LiabilitiesCurrent", "AssetsCurrent",
    "StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "RetainedEarningsAccumulatedDeficit", "WorkingCapital",
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "CashAndShortTermInvestments",
    "NetIncomeLoss", "ProfitLoss",
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet", "SalesRevenueGoodsNet",
    "OperatingIncomeLoss", "OperatingIncomeLossFromContinuingOperations",
    "EarningsPerShareDiluted",
    "EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfDilutedAmericanDepositarySharesOutstanding",
    "LongTermDebt", "LongTermDebtCurrent", "LongTermDebtNoncurrent",
    "ShortTermBorrowings", "DebtCurrent", "DebtLongtermAndShorttermCombinedAmount",
}


def parse_file(path_text: str) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    path = Path(path_text)
    payload = json.loads(path.read_text(encoding="utf-8"))
    cik = str(payload.get("cik") or path.stem).zfill(10)
    rows: list[dict[str, Any]] = []
    after_cutoff = 0
    for fact in payload.get("facts", []):
        if str(fact.get("tag")) not in TAGS:
            continue
        filed = str(fact.get("filed") or "")
        if not filed or filed > CUTOFF.date().isoformat():
            after_cutoff += 1
            continue
        rows.append(
            {
                "cik": cik,
                "tag": str(fact.get("tag")),
                "value": fact.get("value"),
                "unit": str(fact.get("unit") or ""),
                "filed": filed,
                "form": str(fact.get("form") or ""),
                "accession": str(fact.get("accession") or ""),
                "period_start": fact.get("period_start"),
                "period_end": fact.get("period_end"),
                "duration_days": fact.get("duration_days"),
            }
        )
    return cik, rows, {
        "file": path.name,
        "sha256": sha256(path),
        "selected_rows": len(rows),
        "selected_after_cutoff_rejected": after_cutoff,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=CAMPAIGNS / "CAM-0600" / "artifacts" / "shared",
    )
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    files = sorted(FACT_DIR.glob("*.json"))
    if len(files) < 500:
        raise RuntimeError(f"unexpectedly small SEC seed: {len(files)} files")
    identity = json.loads(IDENTITY.read_text(encoding="utf-8"))
    cik_to_symbols: dict[str, list[str]] = {}
    for symbol, row in identity.items():
        cik = str(row.get("cik") or "").zfill(10)
        if cik.strip("0"):
            cik_to_symbols.setdefault(cik, []).append(str(symbol))
    with mp.get_context("spawn").Pool(processes=args.workers) as pool:
        parsed = list(pool.imap_unordered(parse_file, (str(p) for p in files), chunksize=4))
    fact_rows: list[dict[str, Any]] = []
    file_reports = []
    ciks_without_symbol = []
    for cik, rows, report in parsed:
        symbols = cik_to_symbols.get(cik, [])
        if not symbols:
            ciks_without_symbol.append(cik)
        for row in rows:
            for symbol in symbols or [None]:
                fact_rows.append({**row, "symbol": symbol})
        file_reports.append(report)
    facts = pd.DataFrame(fact_rows)
    if facts.empty:
        raise RuntimeError("no compact fundamental facts were built")
    facts["filed"] = pd.to_datetime(facts["filed"])
    facts["period_start"] = pd.to_datetime(facts["period_start"])
    facts["period_end"] = pd.to_datetime(facts["period_end"])
    if (facts["filed"] > CUTOFF).any():
        raise RuntimeError("compact fundamental cache contains holdout filing")
    facts = facts.sort_values(["symbol", "filed", "period_end", "tag", "accession"])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "fundamental_facts.parquet"
    facts.to_parquet(output, index=False)
    report = {
        "status": "passed",
        "workers": args.workers,
        "source_directory": str(FACT_DIR),
        "source_identity": str(IDENTITY),
        "source_identity_sha256": sha256(IDENTITY),
        "source_files": len(files),
        "source_bytes": sum(p.stat().st_size for p in files),
        "selected_fact_rows": int(len(facts)),
        "symbols": int(facts["symbol"].nunique()),
        "ciks": int(facts["cik"].nunique()),
        "min_filed": str(facts["filed"].min().date()),
        "max_filed": str(facts["filed"].max().date()),
        "holdout_rows_loaded": int((facts["filed"] >= pd.Timestamp("2026-05-01")).sum()),
        "ciks_without_symbol": sorted(ciks_without_symbol),
        "tag_counts": facts["tag"].value_counts().to_dict(),
        "output": str(output),
        "output_sha256": sha256(output),
        "file_reports": sorted(file_reports, key=lambda x: x["file"]),
    }
    write_json(args.output_dir / "fundamental_readiness.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "file_reports"}, indent=2))


if __name__ == "__main__":
    mp.freeze_support()
    main()

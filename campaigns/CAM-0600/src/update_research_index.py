from __future__ import annotations

from pathlib import Path

import pandas as pd


WORKSPACE = Path(__file__).resolve().parents[3]
FINAL = pd.read_csv(WORKSPACE / "campaigns" / "CAM-0600" / "artifacts" / "shared" / "final_outcomes.csv")
LEDGER = WORKSPACE / "research" / "LEDGER.md"


def main():
    text = LEDGER.read_text(encoding="utf-8")
    lines = text.splitlines()
    replacements = {}
    for row in FINAL.itertuples(index=False):
        if pd.notna(row.quote_net_return):
            evidence = (
                f"Adapted 2-bp bar net {row.adaptation_2bps_return:+.2%}; selected {row.quote_model} SIP replay "
                f"{row.quote_net_return:+.2%}, {row.quote_extra_2bps_return:+.2%} with 2 bp extra/side, "
                f"{row.quote_maximum_drawdown:.2%} DD, {int(row.quote_positive_months)}/{int(row.quote_negative_months)} positive/negative months"
            )
        else:
            adapted = "n/a" if pd.isna(row.adaptation_2bps_return) else f"{row.adaptation_2bps_return:+.2%}"
            evidence = f"Best execution-qualified adapted 2-bp net {adapted}; no executable quote-gated survivor"
        if str(row.final_decision).startswith("promising") or "profitable_but_fragile" in str(row.final_decision):
            next_step = "Preserve unchanged for frozen prospective paper confirmation; sealed holdout remains untouched"
        elif "execution_sensitive" in str(row.final_decision):
            next_step = "Preserve as a weak execution-sensitive lead; no promotion"
        else:
            next_step = "Campaign concluded under the tested expression; preserve failures and audit trail"
        replacements[row.campaign_id] = (
            f"| {row.campaign_id} | 2026-08-06 | SSRN {row.paper_section} {row.strategy} | "
            f"Cross-universe source replication and adaptation | `{row.final_decision}` | {evidence} | {next_step} |"
        )
    output = []
    seen = set()
    for line in lines:
        stripped = line.lstrip("+")
        matched = None
        for campaign_id in replacements:
            if stripped.startswith(f"| {campaign_id} |"):
                matched = campaign_id
                break
        if matched:
            output.append(replacements[matched])
            seen.add(matched)
        else:
            output.append(line)
    missing = set(replacements) - seen
    if missing:
        raise RuntimeError(f"ledger rows not found: {sorted(missing)}")
    LEDGER.write_text("\n".join(output) + "\n", encoding="utf-8")
    print({"updated_ledger_rows": len(seen)})


if __name__ == "__main__":
    main()

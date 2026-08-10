from pathlib import Path
import json
import yaml

ROOT=Path(__file__).resolve().parents[3]
for number in range(600,625):
 path=ROOT/"campaigns"/f"CAM-{number:04d}"/"runs"/"RUN-0022.yaml"
 if not path.exists(): continue
 run=yaml.safe_load(path.read_text(encoding="utf-8")); run["status"]="invalid"; run["decision"]="Do not interpret: null-quote rows were counted as matched keys in 09:40 remainder accounting. Superseded by RUN-0023."; path.write_text(yaml.safe_dump(run,sort_keys=False),encoding="utf-8")
 with (path.parents[1]/"WORKLOG.jsonl").open("a",encoding="utf-8") as f: f.write(json.dumps({"run_id":"RUN-0022","event":"invalidated","reason":"null-quote rows counted as matched keys","superseded_by":"RUN-0023","holdout_rows_loaded":0})+"\n")

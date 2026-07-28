from __future__ import annotations

import json
import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .config import ScanConfig
from .features import build_features
from .parallel_features import build_parallel_block, is_symbol_local
from .registry import feature_registry, registry_frame, target_registry
from .scanner import scan
from .bulk_scan import cuda_screen, finalize_screen
from .report import write_reports
from .table import add_targets, load_canonical_bars, validate_point_in_time


def execute(config: ScanConfig) -> Path:
    run_id=config.experiment_id
    root=Path(config.output_root)/run_id; root.mkdir(parents=True,exist_ok=True)
    sector=bool(config.sector_map_path); requested=feature_registry(config.lookbacks,sector,config.opening_windows_minutes); targets=target_registry(config.target_horizons_minutes)
    import gc
    import pandas as pd
    bars_cache=root/"canonical_bars.parquet"; bars=None
    if not bars_cache.exists():
        bars=load_canonical_bars(config); bars.to_parquet(bars_cache,index=False)
    checkpoint=root/"screen_checkpoint.csv"; journal=root/"screen_journal.csv"; results=pd.DataFrame(); built_names=set(); validation=None
    block_root=root/"blocks"; feature_root=block_root/"features"; target_root=block_root/"targets"; feature_root.mkdir(parents=True,exist_ok=True); target_root.mkdir(parents=True,exist_ok=True)
    eligible=[s for s in requested if s.status=="requested"]
    local_specs=[spec for spec in eligible if is_symbol_local(spec)]
    global_specs=[spec for spec in eligible if not is_symbol_local(spec)]
    chunks=[local_specs[i:i+config.feature_chunk_size] for i in range(0,len(local_specs),config.feature_chunk_size)]
    chunks += [global_specs[i:i+config.feature_chunk_size] for i in range(0,len(global_specs),config.feature_chunk_size)]
    raw_targets=[t for t in targets if t.classification=="raw"]
    target_batches=[]
    for start in range(0,len(raw_targets),config.target_chunk_size):
        raw_batch=raw_targets[start:start+config.target_chunk_size]; names={t.name for t in raw_batch}
        target_batches.append(raw_batch+[t for t in targets if t.classification=="benchmark_adjusted" and t.name.removesuffix("_benchmark_adjusted") in names])
    # Materialize each target block once. They are reused by every feature block.
    target_paths=[]
    for target_index,target_batch in enumerate(target_batches):
        path=target_root/f"target_{target_index:03d}.parquet"; target_paths.append(path)
        if not path.exists():
            if bars is None: bars=pd.read_parquet(bars_cache)
            target_frame=add_targets(bars[["symbol","session_date","bar_start_ts","bar_end_ts","available_at_ts","open","close"]],target_batch,config.benchmark_symbol)
            target_frame[["entry_ts",*[t.name for t in target_batch]]].to_parquet(path,index=False); del target_frame; gc.collect()
        (root/"progress.json").write_text(json.dumps({"stage":"target_cache","completed":target_index+1,"total":len(target_batches),"updated_at":datetime.now(timezone.utc).isoformat()},indent=2),encoding="utf-8")
    del bars; bars=None; gc.collect()
    # Symbol-local blocks are built by 16 independent processes. Features that
    # require the full cross-section remain full-universe calculations.
    feature_paths=[]; built_by_chunk=[]
    for feature_index,chunk in enumerate(chunks):
        digest=hashlib.sha1("\n".join(s.name for s in chunk).encode()).hexdigest()[:10]
        path=feature_root/f"feature_{feature_index:03d}_{digest}.parquet"; feature_paths.append(path)
        if path.exists():
            import pyarrow.parquet as pq
            columns=set(pq.ParquetFile(path).schema.names); built=[s for s in chunk if s.name in columns]
        elif all(is_symbol_local(spec) for spec in chunk):
            built=build_parallel_block(bars_cache,path,config,chunk,root/"progress.json")
        else:
            if bars is None:
                bars=pd.read_parquet(bars_cache)
                for column in ["open","high","low","close","vwap","volume"]:
                    if column in bars: bars[column]=pd.to_numeric(bars[column],downcast="float")
            feature_frame,built=build_features(bars,config,chunk); feature_frame["decision_ts"]=feature_frame["available_at_ts"]
            feature_frame.to_parquet(path,index=False); del feature_frame; gc.collect()
        built_by_chunk.append(built); built_names.update(s.name for s in built)
        (root/"progress.json").write_text(json.dumps({"stage":"feature_cache","completed":feature_index+1,"total":len(chunks),"updated_at":datetime.now(timezone.utc).isoformat()},indent=2),encoding="utf-8")
    del bars; gc.collect()
    resume_frames=[]
    if config.resume and checkpoint.exists(): resume_frames.append(pd.read_csv(checkpoint))
    if config.resume and journal.exists(): resume_frames.append(pd.read_csv(journal))
    if resume_frames:
        results=pd.concat(resume_frames,ignore_index=True).drop_duplicates(["feature","target"],keep="last")
    total_batches=len(chunks)*len(target_batches); completed_batches=0
    from concurrent.futures import ThreadPoolExecutor
    for feature_index,(feature_path,built) in enumerate(zip(feature_paths,built_by_chunk)):
        completed_pairs={(row.feature,row.target) for row in results.itertuples()}
        pending=[i for i,batch in enumerate(target_batches) if any((spec.name,t.name) not in completed_pairs for spec in built for t in batch)]
        completed_batches += len(target_batches)-len(pending)
        if not pending: continue
        feature_frame=pd.read_parquet(feature_path)
        with ThreadPoolExecutor(max_workers=1) as prefetch:
            future=prefetch.submit(pd.read_parquet,target_paths[pending[0]]) if pending else None
            for position,target_index in enumerate(pending):
                target_batch=target_batches[target_index]; target_frame=future.result()
                future=prefetch.submit(pd.read_parquet,target_paths[pending[position+1]]) if position+1<len(pending) else None
                if validation is None:
                    validation_frame=pd.concat([feature_frame.reset_index(drop=True),target_frame.reset_index(drop=True)],axis=1)
                    validation=validate_point_in_time(validation_frame,target_batch); validation["target_columns"]=len(targets); del validation_frame
                results=cuda_screen(feature_frame,target_frame,built,[t.name for t in target_batch],config,results,journal)
                completed_batches+=1
                (root/"progress.json").write_text(json.dumps({"stage":"cuda_screen","completed_batches":completed_batches,"total_batches":total_batches,"completed_feature_chunks":feature_index,"total_feature_chunks":len(chunks),"completed_pairs":len(results),"updated_at":datetime.now(timezone.utc).isoformat()},indent=2),encoding="utf-8")
                del target_frame; gc.collect()
        del feature_frame; gc.collect()
    results=finalize_screen(results.drop_duplicates(["feature","target"],keep="last")); results.to_csv(checkpoint,index=False)
    if journal.exists(): journal.unlink()
    registry_frame(requested).to_csv(root/"feature_registry.csv",index=False); registry_frame(targets).to_csv(root/"target_registry.csv",index=False); results.to_csv(root/"master_results.csv",index=False)
    skipped=[s.name for s in requested if s.name not in built_names]
    def skip_reason(s):
        if s.unavailable_reason: return s.unavailable_reason
        if s.family=="opening" and s.lookback is not None and s.lookback<5: return "Opening window is shorter than configured 5-minute source bars"
        return "Not constructible from configured point-in-time inputs"
    pd.DataFrame([{"feature": s.name, "status": "built" if s.name not in skipped else "skipped", "reason": "" if s.name not in skipped else skip_reason(s)} for s in requested]).to_csv(root / "scan_coverage.csv", index=False)
    queue=pd.concat([results.head(100),results.loc[results.bh_fdr_p<.10]],ignore_index=True).drop_duplicates(["feature","target"]).head(250)
    top_specs=[s for s in requested if s.name in set(queue.feature)]
    detailed=[]; qtabs={}; spec_by_name={s.name:s for s in top_specs}
    feature_path_by_name={spec.name:feature_paths[index] for index,chunk in enumerate(chunks) for spec in chunk}
    target_path_by_name={spec.name:target_paths[index] for index,batch in enumerate(target_batches) for spec in batch}
    from concurrent.futures import FIRST_COMPLETED,ProcessPoolExecutor,ThreadPoolExecutor,wait
    from .exact_parallel import exact_pair
    from .hybrid_exact import gpu_dense_pair
    cpu_rows={}; gpu_rows={}; hints={(row.feature,row.target):float(row.spearman) for row in queue.itertuples()}; screen_fdr={(row.feature,row.target):float(row.bh_fdr_p) for row in queue.itertuples()}
    cpu_done=gpu_done=0
    with ProcessPoolExecutor(max_workers=config.exact_workers) as cpu_pool, ThreadPoolExecutor(max_workers=1) as gpu_pool:
        futures={}
        for row in queue.itertuples():
            key=(row.feature,row.target); feature_path=feature_path_by_name[row.feature]; target_path=target_path_by_name[row.target]
            futures[cpu_pool.submit(exact_pair,feature_path,target_path,spec_by_name[row.feature],row.target,config,hints[key])]=("cpu",key)
            futures[gpu_pool.submit(gpu_dense_pair,feature_path,target_path,row.feature,row.target,config.min_bin_observations)]=("gpu",key)
        pending=set(futures)
        while pending:
            finished,pending=wait(pending,return_when=FIRST_COMPLETED)
            for future in finished:
                kind,key=futures[future]
                if kind=="cpu":
                    row,_=future.result(); cpu_rows[key]=row; cpu_done+=1
                else:
                    dense,table=future.result(); gpu_rows[key]=dense; qtabs[key]=pd.DataFrame(table); gpu_done+=1
            status={"stage":"exact_diagnostics_hybrid","cpu_completed":cpu_done,"gpu_completed":gpu_done,"total":len(queue),"cpu_workers":config.exact_workers,"gpu_workers":1,"updated_at":datetime.now(timezone.utc).isoformat()}
            (root/"progress.json").write_text(json.dumps(status,indent=2),encoding="utf-8"); (root/"detailed_progress.json").write_text(json.dumps(status,indent=2),encoding="utf-8")
    for key,row in cpu_rows.items():
        if row is None or key not in gpu_rows: continue
        dense=gpu_rows[key]; hint=hints[key]
        if np.sign(dense["spearman"])!=np.sign(hint):
            for column in ["year_consistency","symbol_breadth","time_stability"]:
                if pd.notna(row.get(column)): row[column]=1-row[column]
        row.update(dense); row["screen_bh_fdr_p_global"]=screen_fdr[key]; detailed.append(row)
    detailed=pd.DataFrame(detailed)
    if not detailed.empty:
        from .scanner import benjamini_hochberg
        detailed["exact_selected_bh_fdr_p"]=detailed.groupby(["feature_family","target_family"],dropna=False).raw_p.transform(benjamini_hochberg)
        detailed["bh_fdr_p"]=detailed["screen_bh_fdr_p_global"]
        detailed=_classify_detailed_candidates(detailed)
        detailed["anomaly_score"]=.25*detailed.top_bottom_spread.abs().rank(pct=True)+.25*(1-detailed.bh_fdr_p.fillna(1))+.2*detailed.monotonicity.abs().fillna(0)+.15*detailed.year_consistency.fillna(0)+.15*detailed.symbol_breadth.fillna(0)-.2*detailed.outlier_sensitivity.fillna(0)
        detailed=detailed.sort_values("anomaly_score",ascending=False); detailed.to_csv(root/"detailed_candidates.csv",index=False)
    write_reports(detailed if not detailed.empty else results.head(50), qtabs, root, None)
    from .gpu import CorrelationBackend
    manifest={"experiment_id":run_id,"executed_at":datetime.now(timezone.utc).isoformat(),"config":config.as_dict(),"validation":validation,"requested_features":len(requested),"built_features":len(built_names),"skipped_features":skipped,"targets":[t.name for t in targets],"multiple_testing":"Global Benjamini-Hochberg promotion gate; feature-family/target-family BH retained as a diagnostic","statistical_error":"one-way session-date clustered sandwich standard errors for every screened pair","correlation_backend":CorrelationBackend(config.use_cuda,config.cuda_device).name,"code_version":_git_revision()}
    (root/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    (root/"progress.json").write_text(json.dumps({"stage":"complete","screened_pairs":len(results),"exact_candidates":len(detailed),"updated_at":datetime.now(timezone.utc).isoformat()},indent=2),encoding="utf-8")
    return root


def _classify_detailed_candidates(detailed):
    """Apply the global discovery gate before assigning robust labels."""
    result=detailed.copy()
    usable=~result.status.isin(["constant_feature","insufficient_data"])
    exact=result.raw_p.lt(.05)
    global_fdr=result.screen_bh_fdr_p_global.lt(.05)
    stable=result.year_consistency.ge(.6)&result.symbol_breadth.ge(.6)
    result.loc[usable,"status"]="no_meaningful_relationship"
    result.loc[usable&exact&~stable,"status"]="interesting_but_unstable"
    result.loc[usable&exact&stable&~global_fdr,"status"]="interesting_not_global"
    result.loc[usable&global_fdr&~(exact&stable),"status"]="statistically_interesting"
    result.loc[usable&global_fdr&exact&stable,"status"]="robust_anomaly_candidate"
    return result


def _git_revision() -> str | None:
    try:return subprocess.check_output(["git","-C","D:/AlgoResearch","rev-parse","HEAD"],text=True).strip()
    except Exception:return None

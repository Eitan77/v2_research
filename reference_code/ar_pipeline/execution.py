from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import torch


@dataclass(frozen=True)
class HardwareInfo:
    torch_version: str
    cuda_available: bool
    cuda_device_count: int
    cuda_device_name: str
    cuda_total_gb: float
    cuda_multiprocessors: int
    cpu_count: int


@dataclass(frozen=True)
class WorkloadInfo:
    pattern: str
    preferred_device: str
    supports_cuda: bool
    supports_cpu: bool
    supports_batch_autotune: bool
    estimated_rows: int | None = None
    estimated_candidates: int | None = None


def inspect_hardware() -> HardwareInfo:
    cuda_available = torch.cuda.is_available()
    if cuda_available:
        props = torch.cuda.get_device_properties(0)
        return HardwareInfo(
            torch_version=torch.__version__,
            cuda_available=True,
            cuda_device_count=torch.cuda.device_count(),
            cuda_device_name=props.name,
            cuda_total_gb=round(props.total_memory / 1024**3, 3),
            cuda_multiprocessors=int(props.multi_processor_count),
            cpu_count=os.cpu_count() or 1,
        )
    return HardwareInfo(
        torch_version=torch.__version__,
        cuda_available=False,
        cuda_device_count=0,
        cuda_device_name="",
        cuda_total_gb=0.0,
        cuda_multiprocessors=0,
        cpu_count=os.cpu_count() or 1,
    )


def resolve_execution(config: dict[str, Any], workload: WorkloadInfo, output_dir: Path) -> dict[str, Any]:
    scan = config.setdefault("scan", {})
    execution = scan.setdefault("execution", {})
    hardware = inspect_hardware()
    requested_device = str(execution.get("device", scan.get("device", "auto"))).lower()
    fail_if_cpu = bool(execution.get("fail_if_cpu_fallback", False))
    require_accelerated = bool(execution.get("require_accelerated", False))
    device = _resolve_device(requested_device, workload, hardware, fail_if_cpu)
    if require_accelerated and device != "cuda":
        if not workload.supports_cuda:
            raise RuntimeError(
                f"scan.execution.require_accelerated=true, but adapter workload {workload.pattern!r} "
                "does not support CUDA. Use a CUDA-capable adapter or explicitly set "
                "require_accelerated=false for a small CPU-only diagnostic run."
            )
        raise RuntimeError(
            "scan.execution.require_accelerated=true, but execution did not resolve to CUDA. "
            "Check CUDA availability and scan.execution.device."
        )
    workers = _resolve_workers(execution.get("workers", "auto"), hardware)
    batch_size = _resolve_batch_size(execution.get("batch_size", scan.get("batch_size", "auto")), workload, hardware, device)
    scan["device"] = device
    scan["batch_size"] = batch_size
    execution["device"] = device
    execution["workers"] = workers
    execution["batch_size"] = batch_size
    preflight = {
        "hardware": asdict(hardware),
        "workload": asdict(workload),
        "execution": {
            "requested_device": requested_device,
            "resolved_device": device,
            "workers": workers,
            "batch_size": batch_size,
            "fail_if_cpu_fallback": fail_if_cpu,
            "require_accelerated": require_accelerated,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "execution_preflight.json").write_text(json.dumps(preflight, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return preflight


def _resolve_device(requested: str, workload: WorkloadInfo, hardware: HardwareInfo, fail_if_cpu: bool) -> str:
    if requested == "cuda":
        if not hardware.cuda_available:
            raise RuntimeError("scan.execution.device=cuda requested, but CUDA is not available")
        if not workload.supports_cuda:
            raise RuntimeError(f"Adapter workload {workload.pattern!r} does not support CUDA")
        return "cuda"
    if requested == "cpu":
        if not workload.supports_cpu:
            raise RuntimeError(f"Adapter workload {workload.pattern!r} does not support CPU")
        return "cpu"
    if requested != "auto":
        raise ValueError(f"Unknown execution device {requested!r}; expected auto, cuda, or cpu")
    if hardware.cuda_available and workload.supports_cuda and workload.preferred_device == "cuda":
        return "cuda"
    if fail_if_cpu and workload.preferred_device == "cuda":
        raise RuntimeError("Execution resolved to CPU while fail_if_cpu_fallback=true")
    if not workload.supports_cpu:
        raise RuntimeError(f"Adapter workload {workload.pattern!r} cannot run on CPU")
    return "cpu"


def _resolve_workers(value: Any, hardware: HardwareInfo) -> int:
    if str(value).lower() == "auto":
        return max(1, hardware.cpu_count)
    workers = int(value)
    if workers < 1:
        raise ValueError("workers must be >= 1")
    return workers


def _resolve_batch_size(value: Any, workload: WorkloadInfo, hardware: HardwareInfo, device: str) -> int:
    if str(value).lower() != "auto":
        batch = int(value)
        if batch < 1:
            raise ValueError("batch_size must be >= 1")
        return batch
    if not workload.supports_batch_autotune:
        return 1
    if device == "cuda":
        if workload.estimated_rows and workload.estimated_rows > 0:
            target_fraction = 0.82
            target_bytes = hardware.cuda_total_gb * 1024**3 * target_fraction
            # Dense rank scans materialize a rows x batch score matrix.
            raw_batch = max(1, int(target_bytes // (workload.estimated_rows * 4)))
            return max(8, min(8192, _floor_power_of_two(raw_batch)))
        if hardware.cuda_total_gb >= 10:
            return 1024
        return 256
    return 128


def _floor_power_of_two(value: int) -> int:
    if value < 1:
        return 1
    return 1 << (value.bit_length() - 1)

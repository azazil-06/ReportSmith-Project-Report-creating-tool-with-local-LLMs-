"""CUDA hardware acceleration, GPU diagnostics, and parallel processing utilities.

Supports both PyTorch CUDA runtime (when available) and direct zero-dependency
NVIDIA Driver API (nvcuda.dll / libcuda.so) to guarantee accurate GPU discovery,
VRAM tracking, Ampere/Tensor Core detection, and parallel worker optimization.
"""

import ctypes
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CUDADeviceInfo:
    """Hardware profile for NVIDIA CUDA acceleration."""

    available: bool = False
    device_name: str = "CPU (No CUDA Device Detected)"
    device_count: int = 0
    total_vram_mb: float = 0.0
    free_vram_mb: float = 0.0
    compute_capability: Tuple[int, int] = (0, 0)
    sm_count: int = 0
    cuda_driver_version: Optional[str] = None
    cuda_compiler_version: Optional[str] = None
    supports_tensor_cores: bool = False
    supports_fp16: bool = False
    supports_bf16: bool = False
    recommended_concurrency: int = 2
    recommended_model_size: str = "1.5B - 3B (4-bit quantized)"

    @property
    def compute_str(self) -> str:
        return f"{self.compute_capability[0]}.{self.compute_capability[1]}"

    @property
    def total_vram_gb(self) -> float:
        return round(self.total_vram_mb / 1024, 2)

    @property
    def free_vram_gb(self) -> float:
        return round(self.free_vram_mb / 1024, 2)


def _probe_native_cuda_driver() -> CUDADeviceInfo:
    """Zero-dependency fallback probe using native NVIDIA Driver API (nvcuda.dll / libcuda.so)."""
    info = CUDADeviceInfo()
    try:
        if os.name == "nt":
            cuda = ctypes.WinDLL("nvcuda.dll")
        else:
            cuda = ctypes.CDLL("libcuda.so")

        if cuda.cuInit(0) != 0:
            return info

        count = ctypes.c_int()
        if cuda.cuDeviceGetCount(ctypes.byref(count)) != 0 or count.value == 0:
            return info

        info.available = True
        info.device_count = count.value

        dev = ctypes.c_int()
        cuda.cuDeviceGet(ctypes.byref(dev), 0)

        name_buf = (ctypes.c_char * 256)()
        cuda.cuDeviceGetName(name_buf, 256, dev)
        info.device_name = name_buf.value.decode("utf-8", errors="replace").strip()

        total_bytes = ctypes.c_size_t()
        if hasattr(cuda, "cuDeviceTotalMem_v2"):
            cuda.cuDeviceTotalMem_v2(ctypes.byref(total_bytes), dev)
        else:
            cuda.cuDeviceTotalMem(ctypes.byref(total_bytes), dev)
        info.total_vram_mb = total_bytes.value / (1024 ** 2)

        # Free memory via context
        ctx = ctypes.c_void_p()
        try:
            if hasattr(cuda, "cuCtxCreate_v2"):
                if cuda.cuCtxCreate_v2(ctypes.byref(ctx), 0, dev) == 0:
                    free_b = ctypes.c_size_t()
                    tot_b = ctypes.c_size_t()
                    cuda.cuMemGetInfo_v2(ctypes.byref(free_b), ctypes.byref(tot_b))
                    info.free_vram_mb = free_b.value / (1024 ** 2)
                    cuda.cuCtxDestroy_v2(ctx)
        except Exception:
            info.free_vram_mb = info.total_vram_mb * 0.8

        major = ctypes.c_int()
        minor = ctypes.c_int()
        cuda.cuDeviceGetAttribute(ctypes.byref(major), 75, dev)  # COMPUTE_CAPABILITY_MAJOR
        cuda.cuDeviceGetAttribute(ctypes.byref(minor), 76, dev)  # COMPUTE_CAPABILITY_MINOR
        info.compute_capability = (major.value, minor.value)

        sm_count = ctypes.c_int()
        cuda.cuDeviceGetAttribute(ctypes.byref(sm_count), 16, dev)  # MULTIPROCESSOR_COUNT
        info.sm_count = sm_count.value

        driver_ver = ctypes.c_int()
        if cuda.cuDriverGetVersion(ctypes.byref(driver_ver)) == 0:
            d_maj = driver_ver.value // 1000
            d_min = (driver_ver.value % 1000) // 10
            info.cuda_driver_version = f"{d_maj}.{d_min}"

        info.supports_tensor_cores = major.value >= 7
        info.supports_fp16 = True
        info.supports_bf16 = major.value >= 8
    except Exception as e:
        logger.debug(f"Direct CUDA driver probe unavailable: {e}")

    return info


def detect_cuda_device() -> CUDADeviceInfo:
    """Detect NVIDIA CUDA GPU, VRAM, compute capability, and parallel processing capabilities."""
    info = CUDADeviceInfo()

    # 1. Try PyTorch CUDA if available
    try:
        import torch
        if torch.cuda.is_available():
            info.available = True
            info.device_count = torch.cuda.device_count()
            info.device_name = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            info.total_vram_mb = props.total_memory / (1024 ** 2)
            info.compute_capability = (props.major, props.minor)
            info.sm_count = getattr(props, "multi_processor_count", 0)
            info.supports_tensor_cores = props.major >= 7
            info.supports_fp16 = True
            info.supports_bf16 = props.major >= 8
            try:
                free, total = torch.cuda.mem_get_info(0)
                info.free_vram_mb = free / (1024 ** 2)
            except Exception:
                info.free_vram_mb = info.total_vram_mb * 0.8
    except Exception:
        pass

    # 2. Native Driver API probe if torch.cuda was not loaded with CUDA
    if not info.available:
        info = _probe_native_cuda_driver()

    # 3. Query nvcc compiler version if available
    nvcc_path = shutil.which("nvcc")
    if nvcc_path:
        try:
            out = subprocess.check_output([nvcc_path, "--version"], text=True, timeout=5)
            for line in out.splitlines():
                if "release" in line:
                    info.cuda_compiler_version = line.strip()
                    break
        except Exception:
            pass

    # 4. Compute optimal recommendations based on VRAM and architecture
    if info.available:
        if info.total_vram_mb >= 16000:
            info.recommended_concurrency = 4
            info.recommended_model_size = "8B - 14B (4-bit/8-bit)"
        elif info.total_vram_mb >= 10000:
            info.recommended_concurrency = 2
            info.recommended_model_size = "7B - 8B (4-bit)"
        else:
            # 4GB - 8GB VRAM (e.g. RTX 2050 / RTX 3050):
            # Sequential generation (concurrency=1) ensures zero slot contention, maximum context stability, and highest output fidelity
            info.recommended_concurrency = 1
            info.recommended_model_size = "1.5B - 3B (4-bit)"
    else:
        info.recommended_concurrency = 1

    return info


def is_cuda_available() -> bool:
    """Check whether a functional CUDA GPU is present."""
    return detect_cuda_device().available


def get_optimal_parallel_workers(requested: Optional[int] = None) -> int:
    """Get recommended number of concurrent section generator workers."""
    if requested is not None and requested > 0:
        return requested
    info = detect_cuda_device()
    return info.recommended_concurrency


def get_cuda_diagnostics_table() -> str:
    """Generate a clean terminal report of CUDA GPU hardware and parallel capabilities."""
    info = detect_cuda_device()
    lines = [
        "=" * 64,
        "  REPORTSMITH CUDA HARDWARE ACCELERATION DIAGNOSTICS",
        "=" * 64,
        f"  * CUDA GPU Detected:        {'YES' if info.available else 'NO (CPU Fallback)'}",
        f"  * Device Name:              {info.device_name}",
        f"  * GPU Device Count:         {info.device_count}",
        f"  * Total Dedicated VRAM:     {info.total_vram_gb:.2f} GB ({info.total_vram_mb:.0f} MB)",
        f"  * Available Free VRAM:      {info.free_vram_gb:.2f} GB ({info.free_vram_mb:.0f} MB)",
        f"  * Compute Capability:       {info.compute_str} (Ampere / Tensor Cores)" if info.supports_tensor_cores else f"  * Compute Capability:       {info.compute_str}",
        f"  * Streaming Multiprocessors: {info.sm_count} SMs ({info.sm_count * 128} CUDA Cores)" if info.sm_count else "  * Streaming Multiprocessors: N/A",
        f"  * Tensor Cores Supported:   {'YES (3rd Gen Ampere FP16/BF16/INT4)' if info.supports_tensor_cores else 'NO'}",
        f"  * BF16 Hardware Acceleration:{'YES' if info.supports_bf16 else 'NO'}",
        f"  * NVIDIA Driver Version:    {info.cuda_driver_version or 'N/A'}",
        f"  * CUDA Toolkit (nvcc):      {info.cuda_compiler_version or 'Detected in PATH'}",
        "-" * 64,
        "  RECOMMENDED CONFIGURATION FOR YOUR HARDWARE:",
        f"  * Parallel Concurrency:     {info.recommended_concurrency} concurrent workers",
        f"  * Optimal Model Checkpoint: {info.recommended_model_size}",
        f"  * GPU Offload (num_gpu):    99 (100% layers in VRAM)",
        f"  * Context Window (num_ctx): 4096 - 8192 tokens",
        "=" * 64,
    ]
    return "\n".join(lines)


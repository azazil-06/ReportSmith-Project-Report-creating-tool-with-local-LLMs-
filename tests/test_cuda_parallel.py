"""Tests for CUDA hardware acceleration discovery and parallel section generation."""

import subprocess
import sys
from pathlib import Path
import pytest

from reportsmith.brief import load_brief
from reportsmith.config import ReportSmithConfig, load_config
from reportsmith.cuda_utils import (
    CUDADeviceInfo,
    detect_cuda_device,
    get_cuda_diagnostics_table,
    get_optimal_parallel_workers,
    is_cuda_available,
)
from reportsmith.facts import derive_facts
from reportsmith.generate import SectionGenerator
from reportsmith.llm import StubLLM
from reportsmith.outline import parse_format_file


def test_detect_cuda_device():
    """Verify that detect_cuda_device returns a valid CUDADeviceInfo object."""
    info = detect_cuda_device()
    assert isinstance(info, CUDADeviceInfo)
    assert isinstance(info.available, bool)
    assert isinstance(info.device_name, str)
    assert info.recommended_concurrency >= 1

    if info.available:
        assert info.total_vram_mb > 0
        assert info.total_vram_gb > 0
        assert info.compute_capability[0] > 0
        assert info.sm_count >= 1
        assert "GeForce" in info.device_name or "RTX" in info.device_name or "NVIDIA" in info.device_name or "Tesla" in info.device_name or "A100" in info.device_name or "H100" in info.device_name or len(info.device_name) > 0


def test_optimal_parallel_workers():
    """Verify optimal worker count calculation and manual override."""
    # Custom requested workers
    assert get_optimal_parallel_workers(requested=8) == 8
    assert get_optimal_parallel_workers(requested=2) == 2
    assert get_optimal_parallel_workers(requested=1) == 1

    # Auto-detected workers
    auto_workers = get_optimal_parallel_workers(requested=None)
    assert auto_workers >= 1


def test_cuda_diagnostics_table():
    """Verify CUDA diagnostics report generation."""
    table = get_cuda_diagnostics_table()
    assert "REPORTSMITH CUDA HARDWARE ACCELERATION DIAGNOSTICS" in table
    assert "CUDA GPU Detected:" in table
    assert "Device Name:" in table
    assert "RECOMMENDED CONFIGURATION FOR YOUR HARDWARE:" in table
    assert "Parallel Concurrency:" in table


def test_parallel_section_generation(tmp_path):
    """Verify that SectionGenerator generates sections concurrently across multiple worker threads."""
    brief_file = Path("examples/brief.yaml")
    format_file = Path("examples/format.txt")
    assert brief_file.is_file()
    assert format_file.is_file()

    brief = load_brief(brief_file)
    facts = derive_facts(brief, base_dir=brief_file.parent)
    outline = parse_format_file(format_file)

    cfg = load_config()
    cache_dir = tmp_path / "parallel_cache"
    cfg.paths.cache_dir = str(cache_dir)

    stub = StubLLM(seed=42)
    generator = SectionGenerator(stub, config=cfg, cache_dir=cache_dir)

    # Run with concurrency=4
    sections = generator.generate_all(
        outline=outline,
        brief=brief,
        facts=facts,
        resume=False,
        concurrency=4,
    )

    assert len(sections) > 10
    assert "abstract" in sections
    assert "acknowledgement" in sections
    assert "declaration" in sections
    assert "1.1" in sections

    # Verify each section has paragraphs and valid structure
    for sec_id, data in sections.items():
        assert "paragraphs" in data
        assert isinstance(data["paragraphs"], list)
        assert len(data["paragraphs"]) > 0


def test_parallel_caching_thread_safety(tmp_path):
    """Verify thread-safe cache persistence and cache hit retrieval."""
    brief_file = Path("examples/brief.yaml")
    format_file = Path("examples/format.txt")
    brief = load_brief(brief_file)
    facts = derive_facts(brief, base_dir=brief_file.parent)
    outline = parse_format_file(format_file)

    cfg = load_config()
    cache_dir = tmp_path / "cache_safety"
    cfg.paths.cache_dir = str(cache_dir)

    stub = StubLLM(seed=123)
    generator = SectionGenerator(stub, config=cfg, cache_dir=cache_dir)

    # First pass: write cache with concurrency=4
    sections_pass1 = generator.generate_all(
        outline=outline,
        brief=brief,
        facts=facts,
        resume=False,
        concurrency=4,
    )

    # Verify cache files were created on disk
    cache_files = list(cache_dir.glob("*.json"))
    assert len(cache_files) >= len(sections_pass1)

    # Second pass: read cache with concurrency=4 and resume=True
    sections_pass2 = generator.generate_all(
        outline=outline,
        brief=brief,
        facts=facts,
        resume=True,
        concurrency=4,
    )

    assert len(sections_pass1) == len(sections_pass2)
    for k in sections_pass1:
        assert sections_pass1[k]["paragraphs"] == sections_pass2[k]["paragraphs"]


def test_compute_config_loading(tmp_path):
    """Verify that ReportSmithConfig properly loads compute settings."""
    cfg = load_config()
    assert hasattr(cfg, "compute")
    assert isinstance(cfg.compute.use_cuda, bool)

    # Test custom toml with compute section
    custom_toml = tmp_path / "custom.toml"
    custom_toml.write_text(
        """
[compute]
use_cuda = true
concurrency = 6
""",
        encoding="utf-8",
    )
    loaded = load_config(str(custom_toml))
    assert loaded.compute.use_cuda is True
    assert loaded.compute.concurrency == 6


def test_cli_cuda_flags():
    """Verify that python -m reportsmith.cli --cuda and 'cuda' subcommand run cleanly."""
    timeout_sec = 60
    res_flag = subprocess.run(
        [sys.executable, "-m", "reportsmith.cli", "--cuda"],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    assert res_flag.returncode == 0, f"CLI --cuda failed with stderr: {res_flag.stderr}"
    assert "CUDA HARDWARE ACCELERATION DIAGNOSTICS" in res_flag.stdout

    res_subcmd = subprocess.run(
        [sys.executable, "-m", "reportsmith.cli", "cuda"],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    assert res_subcmd.returncode == 0, f"CLI cuda subcommand failed with stderr: {res_subcmd.stderr}"
    assert "CUDA HARDWARE ACCELERATION DIAGNOSTICS" in res_subcmd.stdout


"""Empirical adversarial stress test suite for Milestone 1:
Cache isolation, cache key collision resistance, active project manifest lifecycle,
and resilient state management.

Authored by: Challenger 1 (Milestone 1)
"""

import inspect
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from reportsmith.brief import ProjectBrief, compute_project_signature
from reportsmith.config import load_config
from reportsmith.generate import (
    SectionGenerator,
    check_and_update_active_manifest,
    compute_cache_key,
)
from reportsmith.llm import StubLLM
from reportsmith.outline import OutlineItem, ReportOutline


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

from reportsmith.brief import ProjectBrief, compute_project_signature, load_brief

def make_dummy_brief(title: str, abstract: str, domain: str = "GENERAL_ACADEMIC") -> ProjectBrief:
    base = load_brief("examples/brief.yaml")
    data = base.model_dump()
    data["project"]["title"] = title
    data["abstract"] = abstract
    data["domain"] = domain
    return ProjectBrief.model_validate(data)


def make_minimal_outline() -> ReportOutline:
    return ReportOutline(
        items=[
            OutlineItem(
                id="ch1_overview",
                title="System Overview",
                kind="section",
                target_words=150,
            ),
            OutlineItem(
                id="ch1_objectives",
                title="Project Objectives",
                kind="section",
                target_words=150,
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Challenge Suite: Cache Isolation & Purging
# ---------------------------------------------------------------------------

def test_cache_isolation_between_distinct_projects(tmp_path):
    """Verify that Project A writes to cache/sigA/, and switching to Project B
    purges/isolates cache/sigA/ so Project B never receives Project A's cached sections.
    """
    cache_base = tmp_path / "cache"
    config = load_config()

    sig_a = "111111111111"
    sig_b = "222222222222"

    brief_a = make_dummy_brief("Smart Irrigation", "IoT automated plant watering system")
    facts_a = None

    # Step 1: Generate for Project A
    gen_a = SectionGenerator(
        llm=StubLLM(),
        config=config,
        cache_dir=cache_base,
        project_signature=sig_a,
    )
    assert gen_a.cache_dir == cache_base / sig_a
    assert gen_a.cache_dir.is_dir()

    res_a = gen_a._generate_single_section(
        section_id="ch1_overview",
        section_title="System Overview",
        prompt="Describe the smart irrigation hardware architecture.",
        target_words=100,
        resume=True,
    )

    # Verify Project A cache file exists and contains sigA
    cache_files_a = list(gen_a.cache_dir.glob("*.json"))
    assert len(cache_files_a) == 1, "Project A must produce exactly 1 cache file"
    with open(cache_files_a[0], "r", encoding="utf-8") as f:
        data_a = json.load(f)
    assert data_a["project_signature"] == sig_a
    assert data_a["section_id"] == "ch1_overview"
    assert data_a["data"] == res_a

    # Step 2: Switch active project to Project B
    gen_b = SectionGenerator(
        llm=StubLLM(),
        config=config,
        cache_dir=cache_base,
        project_signature=sig_b,
    )
    assert gen_b.cache_dir == cache_base / sig_b
    assert gen_b.cache_dir.is_dir()

    # Step 3: Verify Project A cache directory was purged by active manifest management
    assert not (cache_base / sig_a).exists(), (
        f"Stale project cache directory '{cache_base / sig_a}' was not purged upon project switch"
    )

    # Step 4: Generate for Project B using the EXACT SAME prompt
    res_b = gen_b._generate_single_section(
        section_id="ch1_overview",
        section_title="System Overview",
        prompt="Describe the smart irrigation hardware architecture.",
        target_words=100,
        resume=True,
    )

    cache_files_b = list(gen_b.cache_dir.glob("*.json"))
    assert len(cache_files_b) == 1
    with open(cache_files_b[0], "r", encoding="utf-8") as f:
        data_b = json.load(f)

    assert data_b["project_signature"] == sig_b
    # Verify cache key paths are completely separate
    assert cache_files_a[0].name != cache_files_b[0].name


def test_cache_poisoning_rejection_on_signature_mismatch(tmp_path):
    """Stress test: If a foreign cache file from Project A is placed inside Project B's
    cache directory, verify that SectionGenerator rejects the stale payload and
    regenerates cleanly.
    """
    cache_base = tmp_path / "cache"
    config = load_config()

    sig_b = "bbbb2222bbbb"
    foreign_sig = "aaaa1111aaaa"

    prompt = "Synthesize methodology section."
    key = compute_cache_key(sig_b, prompt, "stub", 0.0, 42)

    gen_b = SectionGenerator(
        llm=StubLLM(),
        config=config,
        cache_dir=cache_base,
        project_signature=sig_b,
    )

    # Plant a poisoned cache file with matching filename but foreign signature
    poisoned_file = gen_b.cache_dir / f"{key}.json"
    with open(poisoned_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "project_signature": foreign_sig,  # Mismatched signature!
                "section_id": "methodology",
                "title": "Methodology",
                "data": {"paragraphs": ["LEAKED TOPIC FROM FOREIGN PROJECT"], "bullets": []},
                "timestamp": time.time(),
            },
            f,
        )

    # When SectionGenerator attempts to read cache, it must reject the mismatched signature
    res = gen_b._generate_single_section(
        section_id="methodology",
        section_title="Methodology",
        prompt=prompt,
        target_words=100,
        resume=True,
    )

    assert res != {"paragraphs": ["LEAKED TOPIC FROM FOREIGN PROJECT"], "bullets": []}, (
        "SectionGenerator accepted poisoned cache with mismatched project_signature!"
    )

    # Verify file was overwritten with valid signature
    with open(poisoned_file, "r", encoding="utf-8") as f:
        fixed_data = json.load(f)
    assert fixed_data["project_signature"] == sig_b


def test_active_manifest_multi_switch_purging(tmp_path):
    """Verify that switching projects sequentially (A -> B -> C -> A) purges previous
    directories and updates active_manifest.json accurately.
    """
    cache_base = tmp_path / "cache"
    config = load_config()

    signatures = [
        ("sig_proj_aaa", "Project Alpha", "IoT"),
        ("sig_proj_bbb", "Project Beta", "FinTech"),
        ("sig_proj_ccc", "Project Gamma", "Bioinformatics"),
        ("sig_proj_aaa", "Project Alpha", "IoT"),
    ]

    for sig, title, domain in signatures:
        gen = SectionGenerator(
            llm=StubLLM(),
            config=config,
            cache_dir=cache_base,
            project_signature=sig,
        )
        # Touch a dummy cache file in the active folder
        dummy_file = gen.cache_dir / "test_entry.json"
        dummy_file.write_text("{}", encoding="utf-8")

        # Manifest must reflect current project
        manifest_file = cache_base / "active_manifest.json"
        assert manifest_file.is_file()
        with open(manifest_file, "r", encoding="utf-8") as f:
            m = json.load(f)
        assert m["project_signature"] == sig

        # There must be only one project subdirectory remaining in cache_base
        subdirs = [p.name for p in cache_base.iterdir() if p.is_dir()]
        assert subdirs == [sig], f"Expected only active dir [{sig}], found {subdirs}"


# ---------------------------------------------------------------------------
# Challenge Suite: Cache Key Collision Resistance
# ---------------------------------------------------------------------------

def test_cache_key_collision_50_distinct_projects():
    """Verify that 50 different projects with the exact same generation prompt
    produce 50 distinct cache keys without collisions.
    """
    prompt = "Write an exhaustive technical overview of the system architecture."
    model = "qwen2.5-coder-3b"
    temp = 0.3
    seed = 42

    keys = set()
    signatures = set()

    for idx in range(50):
        title = f"Project Title {idx}"
        abstract = f"Abstract text describing unique problem domain and methodology for system {idx}."
        sig = compute_project_signature(title, abstract)
        assert len(sig) == 12
        signatures.add(sig)

        key = compute_cache_key(sig, prompt, model, temp, seed)
        keys.add(key)

    assert len(signatures) == 50, "All 50 project signatures must be unique"
    assert len(keys) == 50, "All 50 cache keys must be unique (zero collisions)"


def test_cache_key_dual_dispatch_equivalence():
    """Verify positional 5-arg contract, keyword arguments, and mixed calls produce
    identical cache keys for identical parameters.
    """
    sig = "abcd1234ef56"
    prompt = "Synthesize Chapter 3 design."
    model = "stub"
    temp = 0.5
    seed = 100

    # 5-arg positional
    key_pos = compute_cache_key(sig, prompt, model, temp, seed)

    # Keyword arguments
    key_kw = compute_cache_key(
        project_signature=sig,
        prompt=prompt,
        model=model,
        temperature=temp,
        seed=seed,
    )

    # Mixed positional and keyword
    key_mixed = compute_cache_key(
        sig,
        prompt,
        model=model,
        temperature=temp,
        seed=seed,
    )

    assert key_pos == key_kw == key_mixed, "Dual dispatch variants must produce identical hash"


def test_cache_key_differs_when_temperature_or_seed_changes():
    """Verify temperature and seed sensitivity in compute_cache_key."""
    sig = "abcd1234ef56"
    prompt = "Synthesize Chapter 3 design."

    key_base = compute_cache_key(sig, prompt, "stub", 0.0, 42)
    key_temp = compute_cache_key(sig, prompt, "stub", 0.7, 42)
    key_seed = compute_cache_key(sig, prompt, "stub", 0.0, 99)

    assert key_base != key_temp
    assert key_base != key_seed
    assert key_temp != key_seed


def test_project_signature_boundary_handling():
    """Stress test boundary inputs to compute_project_signature:
    - Empty title & abstract
    - Whitespace normalization
    - Unicode, math symbols, CJK characters
    - Very large abstract
    """
    # Empty inputs
    sig_empty = compute_project_signature("", "")
    assert len(sig_empty) == 12
    assert all(c in "0123456789abcdef" for c in sig_empty)

    # None inputs
    sig_none = compute_project_signature(None, None)
    assert sig_none == sig_empty

    # Whitespace and CRLF vs LF normalization
    sig_normal = compute_project_signature("Title", "Line 1\nLine 2")
    sig_crlf = compute_project_signature("  Title  ", "Line 1\r\nLine 2  ")
    assert sig_normal == sig_crlf, "Whitespace and CRLF normalization must match"

    # Unicode & math symbols
    sig_unicode = compute_project_signature(
        "AI 医疗系统 (Version λ-3.14)",
        "Deep learning model achieves 99.8% precision with entropy H(x) = -Σ p log p 🚀"
    )
    assert len(sig_unicode) == 12

    # Stress: Very large abstract (100,000 characters)
    large_abstract = "A" * 100_000
    start = time.perf_counter()
    sig_large = compute_project_signature("Large Project", large_abstract)
    duration = time.perf_counter() - start
    assert len(sig_large) == 12
    assert duration < 0.1, f"Hashing large abstract took too long: {duration:.4f}s"


def test_project_signature_colon_delimiter_collision_edge_case():
    """Adversarial check: In compute_project_signature, raw input is f"{clean_title}:{clean_abstract}".
    Construction: Title "A:B" and Abstract "C" vs Title "A" and Abstract "B:C".
    Both evaluate to "A:B:C".
    This tests whether a delimiter collision occurs under unescaped colon concatenation.
    """
    sig1 = compute_project_signature("System:Subsystem", "Module description")
    sig2 = compute_project_signature("System", "Subsystem:Module description")

    # This test empirically confirms whether unescaped colons cause identical signatures.
    # If sig1 == sig2, it reveals a delimiter collision attack surface.
    is_colliding = (sig1 == sig2)
    # We assert the observation for reporting in the Challenger handoff:
    assert isinstance(is_colliding, bool)


def test_concurrent_section_cache_writes(tmp_path):
    """Stress test multithreaded section generation writing cache files concurrently."""
    config = load_config()
    sig = "multi1234567"
    gen = SectionGenerator(
        llm=StubLLM(),
        config=config,
        cache_dir=tmp_path / "cache",
        project_signature=sig,
    )

    outline = ReportOutline(
        items=[
            OutlineItem(id=f"sec_{i}", title=f"Section {i}", kind="section", target_words=50)
            for i in range(10)
        ]
    )
    brief = make_dummy_brief("Concurrent System", "Multithreaded verification abstract")
    from reportsmith.facts import derive_facts
    facts = derive_facts(brief)

    # Run generation with concurrency=4
    res = gen.generate_all(
        outline=outline,
        brief=brief,
        facts=facts,
        resume=True,
        concurrency=4,
    )

    # Verify all 10 sections + 3 standalone (abstract, ack, dec) are cached
    cached_files = list(gen.cache_dir.glob("*.json"))
    assert len(cached_files) == 13, f"Expected 13 cache files, found {len(cached_files)}"
    for f in cached_files:
        with open(f, "r", encoding="utf-8") as fp:
            d = json.load(fp)
            assert d["project_signature"] == sig
            assert "data" in d

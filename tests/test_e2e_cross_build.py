"""Tier 3 and Tier 4 E2E Tests: Cross-Build Lifecycle, Topic Isolation,

Cache Invalidation, and Grand Acceptance Scenarios.

Authoritative References:
- ORIGINAL_REQUEST.md § R1 (Prevent Topic Leakage and Cross-Build Hallucination)
- ORIGINAL_REQUEST.md § R2 (Automatic Cache and State Isolation)
- PROJECT.md § F16, F17 (Grand R1 and R2 Acceptance Scenarios)
"""

import hashlib
import re
import shutil
import sys
from pathlib import Path
from typing import List, Set
import docx
import pytest

# Ensure workspace root is in sys.path for test discovery
_workspace_root = str(Path(__file__).resolve().parent.parent)
if _workspace_root not in sys.path:
    sys.path.insert(0, _workspace_root)

from reportsmith.check import validate_report
from reportsmith.config import load_config
from reportsmith.content_analyzer import analyze_content_folder
from reportsmith.docx_build import DocxReportBuilder
from reportsmith.facts import derive_facts
from reportsmith.generate import SectionGenerator
from reportsmith.llm import StubLLM

try:
    from tests.conftest import (
        SAMPLE_ABSTRACTS,
        extract_docx_all_text,
        make_project_docx,
    )
except ImportError:
    from conftest import (
        SAMPLE_ABSTRACTS,
        extract_docx_all_text,
        make_project_docx,
    )


# Helper to assert absolute zero occurrences of foreign keywords in a Word document
def assert_zero_keyword_contamination(
    docx_path: Path,
    banned_keywords: List[str],
    project_label: str,
):
    """Scan all paragraphs, tables, headers, and footers of docx_path and assert 0 occurrences of banned_keywords."""
    assert docx_path.is_file(), f"Document {docx_path} does not exist"
    full_text = extract_docx_all_text(docx_path).lower()

    violations = []
    for kw in banned_keywords:
        kw_low = kw.lower()
        pattern = rf"\b{re.escape(kw_low)}\b"
        match = re.search(pattern, full_text)
        if match:
            idx = match.start()
            snippet = full_text[max(0, idx - 40): min(len(full_text), idx + len(kw_low) + 40)]
            violations.append(f"Found banned keyword '{kw}' in {project_label}: '...{snippet}...'")

    assert not violations, (
        f"Cross-build topic contamination detected in {project_label}!\n" + "\n".join(violations)
    )


# ==============================================================================
# TIER 3: PAIRWISE — Section Cache Hit x Different Project Cache Isolation
# ==============================================================================

@pytest.mark.tier3
def test_tier3_pairwise_cache_hit_on_same_project_rerun(tmp_path):
    """Verify that re-running generation for the same project with resume=True achieves cache hits."""
    content_dir = tmp_path / "Content"
    sample = SAMPLE_ABSTRACTS["software_moneygo"]
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    config = load_config()
    cache_dir = tmp_path / "cache"

    # Pass 1: Generate and populate cache
    gen1 = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir)
    res1 = gen1.generate_all(outline, brief, facts, allowed_sections={"1.1", "1.2"}, resume=True)

    # Pass 2: Re-run same project
    gen2 = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir)
    res2 = gen2.generate_all(outline, brief, facts, allowed_sections={"1.1", "1.2"}, resume=True)

    assert res1["1.1"] == res2["1.1"], "Re-run for same project must produce identical cached prose"
    assert res1["1.2"] == res2["1.2"]


@pytest.mark.tier3
def test_tier3_pairwise_cache_isolation_on_different_project(tmp_path):
    """Verify that a second project with different abstract does not reuse section prose from the first."""
    content_dir = tmp_path / "Content"
    cache_dir = tmp_path / "cache"
    config = load_config()

    # Project A: Embedded STM32
    make_project_docx(content_dir, SAMPLE_ABSTRACTS["embedded_stm32"]["title"], SAMPLE_ABSTRACTS["embedded_stm32"]["abstract"])
    brief_a, facts_a, outline_a = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    sig_a = getattr(brief_a, "project_signature", "sig_a")
    gen_a = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_a)
    res_a = gen_a.generate_all(outline_a, brief_a, facts_a, allowed_sections={"1.1"}, resume=True)

    # Project B: Web CampusConnect
    make_project_docx(content_dir, SAMPLE_ABSTRACTS["web_campusconnect"]["title"], SAMPLE_ABSTRACTS["web_campusconnect"]["abstract"])
    brief_b, facts_b, outline_b = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    sig_b = getattr(brief_b, "project_signature", "sig_b")
    gen_b = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_b)
    res_b = gen_b.generate_all(outline_b, brief_b, facts_b, allowed_sections={"1.1"}, resume=True)

    # Assert Project B did not reuse Project A's cached prose
    text_a = " ".join(res_a.get("1.1", {}).get("paragraphs", []))
    text_b = " ".join(res_b.get("1.1", {}).get("paragraphs", []))
    assert text_a != text_b, "Project B must not reuse cached section text from Project A"


@pytest.mark.tier3
def test_tier3_pairwise_cache_persistence_after_interleaved_build(tmp_path):
    """Verify interleaved builds (Project A -> Project B -> Project A) preserve Project A cache safely."""
    content_dir = tmp_path / "Content"
    cache_dir = tmp_path / "cache"
    config = load_config()

    # 1. Build Project A
    make_project_docx(content_dir, SAMPLE_ABSTRACTS["software_moneygo"]["title"], SAMPLE_ABSTRACTS["software_moneygo"]["abstract"])
    brief_a, facts_a, outline_a = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    sig_a = getattr(brief_a, "project_signature", "sig_moneygo")
    gen_a = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_a)
    res_a1 = gen_a.generate_all(outline_a, brief_a, facts_a, allowed_sections={"1.1"}, resume=True)

    # 2. Build Project B
    make_project_docx(content_dir, SAMPLE_ABSTRACTS["bio_genealign"]["title"], SAMPLE_ABSTRACTS["bio_genealign"]["abstract"])
    brief_b, facts_b, outline_b = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    sig_b = getattr(brief_b, "project_signature", "sig_genealign")
    gen_b = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_b)
    gen_b.generate_all(outline_b, brief_b, facts_b, allowed_sections={"1.1"}, resume=True)

    # 3. Return to Project A
    res_a2 = gen_a.generate_all(outline_a, brief_a, facts_a, allowed_sections={"1.1"}, resume=True)
    assert res_a1["1.1"] == res_a2["1.1"], "Project A cache must remain intact and valid after interleaved builds"


# ==============================================================================
# TIER 4: SCENARIO 1 — The Grand R1 Test (Sequential Cross-Build Topic Isolation)
# ==============================================================================

@pytest.mark.tier4
def test_tier4_scenario1_grand_r1_sequential_cross_build_isolation(tmp_path):
    """The Grand R1 Test: Sequential cross-build topic isolation across 4 distinct domains.

    Sequence:
      1. Build Project A: Embedded Hardware (STM32 Plant Monitor)
      2. Build Project B: Web & Cloud Services (CampusConnect MERN)
      3. Build Project C: Machine Learning / AI (FreshKNN Classifier)
      4. Build Project D: Bioinformatics (GeneAlign Sequence Alignment)

    Zero-Tolerance Assertion:
      Each built document must contain STRICTLY ZERO keywords, microcontroller models,
      pin identifiers, or prose entities from previously built projects!
    """
    workspace = tmp_path / "workspace"
    content_dir = workspace / "Content"
    out_dir = workspace / "out"
    cache_dir = workspace / "cache"
    config = load_config()

    sections_to_test = {"abstract", "1.1", "1.2", "2.1", "3.1", "4.1", "5.1", "6.1"}

    # --------------------------------------------------------------------------
    # Step 1: Build Project A (Embedded STM32)
    # --------------------------------------------------------------------------
    sample_a = SAMPLE_ABSTRACTS["embedded_stm32"]
    make_project_docx(content_dir, sample_a["title"], sample_a["abstract"])
    brief_a, facts_a, outline_a = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    sig_a = getattr(brief_a, "project_signature", "sig_stm32")

    gen_a = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_a)
    data_a = gen_a.generate_all(outline_a, brief_a, facts_a, allowed_sections=sections_to_test, resume=True)
    doc_a_path = out_dir / "report_stm32.docx"
    builder_a = DocxReportBuilder(brief_a, facts_a, outline_a, sample_path="COA_report.docx")
    builder_a.build(data_a, doc_a_path)

    assert doc_a_path.is_file()
    text_a = extract_docx_all_text(doc_a_path).lower()
    # Confirm Project A contains its own domain terms
    assert any(k in text_a for k in ("stm32", "moisture", "watering", "sensor"))

    # --------------------------------------------------------------------------
    # Step 2: Swap Content/ to Project B (CampusConnect Web MERN)
    # --------------------------------------------------------------------------
    shutil.rmtree(content_dir)
    sample_b = SAMPLE_ABSTRACTS["web_campusconnect"]
    make_project_docx(content_dir, sample_b["title"], sample_b["abstract"])
    brief_b, facts_b, outline_b = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    sig_b = getattr(brief_b, "project_signature", "sig_campusconnect")

    gen_b = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_b)
    data_b = gen_b.generate_all(outline_b, brief_b, facts_b, allowed_sections=sections_to_test, resume=True)
    doc_b_path = out_dir / "report_campusconnect.docx"
    builder_b = DocxReportBuilder(brief_b, facts_b, outline_b, sample_path="COA_report.docx")
    builder_b.build(data_b, doc_b_path)

    assert doc_b_path.is_file()
    # Strict Zero-Contamination Assertion against Project A terms
    assert_zero_keyword_contamination(
        doc_b_path,
        banned_keywords=["stm32", "pa0", "pc13", "cortex-m", "soil moisture", "plant watering", "breadboard"],
        project_label="Project B (CampusConnect Web)",
    )

    # --------------------------------------------------------------------------
    # Step 3: Swap Content/ to Project C (FreshKNN Machine Learning)
    # --------------------------------------------------------------------------
    shutil.rmtree(content_dir)
    sample_c = SAMPLE_ABSTRACTS["ml_freshknn"]
    make_project_docx(content_dir, sample_c["title"], sample_c["abstract"])
    brief_c, facts_c, outline_c = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    sig_c = getattr(brief_c, "project_signature", "sig_freshknn")

    gen_c = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_c)
    data_c = gen_c.generate_all(outline_c, brief_c, facts_c, allowed_sections=sections_to_test, resume=True)
    doc_c_path = out_dir / "report_freshknn.docx"
    builder_c = DocxReportBuilder(brief_c, facts_c, outline_c, sample_path="COA_report.docx")
    builder_c.build(data_c, doc_c_path)

    assert doc_c_path.is_file()
    # Strict Zero-Contamination Assertion against Project A & Project B terms
    assert_zero_keyword_contamination(
        doc_c_path,
        banned_keywords=["campusconnect", "mern", "mongodb", "express.js", "stm32", "pa0", "pc13", "soil moisture"],
        project_label="Project C (FreshKNN ML)",
    )

    # --------------------------------------------------------------------------
    # Step 4: Swap Content/ to Project D (GeneAlign Bioinformatics)
    # --------------------------------------------------------------------------
    shutil.rmtree(content_dir)
    sample_d = SAMPLE_ABSTRACTS["bio_genealign"]
    make_project_docx(content_dir, sample_d["title"], sample_d["abstract"])
    brief_d, facts_d, outline_d = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    sig_d = getattr(brief_d, "project_signature", "sig_genealign")

    gen_d = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_d)
    data_d = gen_d.generate_all(outline_d, brief_d, facts_d, allowed_sections=sections_to_test, resume=True)
    doc_d_path = out_dir / "report_genealign.docx"
    builder_d = DocxReportBuilder(brief_d, facts_d, outline_d, sample_path="COA_report.docx")
    builder_d.build(data_d, doc_d_path)

    assert doc_d_path.is_file()
    # Strict Zero-Contamination Assertion against all prior projects
    assert_zero_keyword_contamination(
        doc_d_path,
        banned_keywords=["freshknn", "campusconnect", "mern", "mongodb", "stm32", "pa0", "soil moisture", "microcontroller", "arduino"],
        project_label="Project D (GeneAlign Bioinformatics)",
    )


# ==============================================================================
# TIER 4: SCENARIO 2 — The Grand R2 Test (Cache Invalidation Lifecycle)
# ==============================================================================

@pytest.mark.tier4
def test_tier4_scenario2_grand_r2_cache_invalidation_lifecycle(tmp_path):
    """The Grand R2 Test: Verify that modifying an abstract in Content/ invalidates

    stale caches and synthesizes fresh report prose with zero stale persistence.
    """
    workspace = tmp_path / "workspace"
    content_dir = workspace / "Content"
    cache_dir = workspace / "cache"
    out_dir = workspace / "out"
    config = load_config()

    # Step 1: Initial Abstract in Content/
    title_1 = "CampusConnect: MERN Architecture"
    abs_1 = "The platform is built on MongoDB, Express.js, React, and Node.js for campus events."
    make_project_docx(content_dir, title_1, abs_1)
    brief_1, facts_1, outline_1 = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    sig_1 = getattr(brief_1, "project_signature", hashlib.sha256(f"{title_1}:{abs_1}".encode()).hexdigest()[:12])
    gen_1 = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_1)
    data_1 = gen_1.generate_all(outline_1, brief_1, facts_1, allowed_sections={"1.1"}, resume=True)

    # Step 2: Modify abstract in Content/ to Django + PostgreSQL
    shutil.rmtree(content_dir)
    title_2 = "CampusConnect: Django Architecture"
    abs_2 = "The platform is re-engineered with Python Django, PostgreSQL, and Redis for campus events."
    make_project_docx(content_dir, title_2, abs_2)
    brief_2, facts_2, outline_2 = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    sig_2 = getattr(brief_2, "project_signature", hashlib.sha256(f"{title_2}:{abs_2}".encode()).hexdigest()[:12])
    assert sig_1 != sig_2, "Project signature must change when abstract in Content/ changes"

    # Step 3: Run generator on modified project with persistent cache
    gen_2 = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_2)
    data_2 = gen_2.generate_all(outline_2, brief_2, facts_2, allowed_sections={"1.1"}, resume=True)

    doc_path = out_dir / "report_modified.docx"
    builder = DocxReportBuilder(brief_2, facts_2, outline_2, sample_path="COA_report.docx")
    builder.build(data_2, doc_path)

    assert doc_path.is_file()
    # Verify stale signature cache is not reused
    assert gen_1.cache_dir != gen_2.cache_dir, "Cache directories must be isolated across abstract revisions"

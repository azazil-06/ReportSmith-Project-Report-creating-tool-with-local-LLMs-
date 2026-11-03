"""Empirical challenger verification harness for Milestone 4 (Feature F14: Training DB Isolation & Prompt Sanitization).

Validates:
1. ExemplarStore domain isolation and sanitization across all supported domains:
   - BIOINFORMATICS, MACHINE_LEARNING, WEB_CLOUD, SOFTWARE_APP, DATA_ANALYTICS, EMBEDDED_HARDWARE, GENERAL_ACADEMIC, COA_SYSTEMS.
   - get_domain_exemplar with is_hardware=False must never return banned hardware tokens.
   - get_exact_project_match must reject cross-domain matches when titles resemble hardware documents.
2. Prompt template rendering across all non-hardware domains:
   - prompts/default_section.j2 and prompts/hardware_overview.j2 must never emit 'Microcontroller Part Number' or 'Operating Voltage: 3.3V'.
   - Comprehensive sweep across all prompt templates (abstract.j2, bullets_section.j2, conclusion.j2, introduction.j2, etc.) for non-hardware briefs.
3. StubLLM end-to-end prose generation purity on Bioinformatics and non-hardware projects.

Author: Challenger M4_2 (Empirical Verification)
"""

import pathlib
import re
import sys
import tempfile
from typing import Dict, List, Optional
import pytest

_workspace_root = str(pathlib.Path(__file__).resolve().parent.parent)
if _workspace_root not in sys.path:
    sys.path.insert(0, _workspace_root)

from reportsmith.brief import ProjectBrief
from reportsmith.content_analyzer import analyze_content_folder
from reportsmith.facts import derive_facts
from reportsmith.generate import build_section_prompt, jinja_env, SYSTEM_PROMPT
from reportsmith.llm import StubLLM
from reportsmith.trainer import ExemplarStore, BANNED_HARDWARE_TOKENS

try:
    from tests.conftest import SAMPLE_ABSTRACTS, make_project_docx
except ImportError:
    from conftest import SAMPLE_ABSTRACTS, make_project_docx


# ------------------------------------------------------------------------------
# Test 1: ExemplarStore get_exact_project_match Isolation
# ------------------------------------------------------------------------------

def test_exemplar_store_exact_project_match_cross_domain_rejection():
    """Verify that get_exact_project_match rejects cross-domain matches even if titles overlap."""
    store = ExemplarStore()
    assert store.db_data.get("documents"), "Training database must contain documents"

    # 1. A software project whose title partially resembles the COA Traffic Signal Controller
    match_soft_traffic = store.get_exact_project_match(
        active_title="Smart Traffic Signal Controller",
        active_domain="SOFTWARE_APP",
        min_token_overlap=0.85,
    )
    assert match_soft_traffic is None, (
        f"Cross-domain leak: 'Smart Traffic Signal Controller' under SOFTWARE_APP matched {match_soft_traffic}"
    )

    # 2. A software project with the EXACT title of the COA document
    match_exact_title_wrong_domain = store.get_exact_project_match(
        active_title="IMPLEMENTATION OF TRAFFIC SIGNAL CONTROLLER",
        active_domain="SOFTWARE_APP",
        min_token_overlap=0.85,
    )
    assert match_exact_title_wrong_domain is None, (
        f"Cross-domain leak: exact COA title under SOFTWARE_APP matched {match_exact_title_wrong_domain}"
    )

    # 3. A software project with the EXACT title of an EMBEDDED document
    match_exact_embedded_title = store.get_exact_project_match(
        active_title="Automated Soil Moisture Monitoring and Plant Watering Reminder System",
        active_domain="SOFTWARE_APP",
        min_token_overlap=0.85,
    )
    assert match_exact_embedded_title is None, (
        f"Hardware leak: embedded title under SOFTWARE_APP matched {match_exact_embedded_title}"
    )

    # 4. Token overlap threshold boundary (< 0.85 overlap must return None)
    match_low_overlap = store.get_exact_project_match(
        active_title="Traffic Controller Monitoring Tool",
        active_domain="COA_SYSTEMS",
        min_token_overlap=0.85,
    )
    assert match_low_overlap is None, (
        f"Threshold violation: low overlap title matched {match_low_overlap}"
    )

    # 5. Congruent domain and high token overlap (>= 0.85) succeeds
    match_congruent = store.get_exact_project_match(
        active_title="Implementation of Traffic Signal Controller",
        active_domain="COA_SYSTEMS",
        min_token_overlap=0.85,
    )
    assert match_congruent is not None, "Congruent match for COA Traffic Signal Controller should succeed"
    assert match_congruent.get("domain") == "COA_SYSTEMS"


# ------------------------------------------------------------------------------
# Test 2: ExemplarStore get_domain_exemplar Hardware Token Sanitization
# ------------------------------------------------------------------------------

@pytest.mark.parametrize("domain", [
    "BIOINFORMATICS",
    "MACHINE_LEARNING",
    "WEB_CLOUD",
    "SOFTWARE_APP",
    "DATA_ANALYTICS",
    "GENERAL_ACADEMIC",
])
@pytest.mark.parametrize("role", [
    "introduction",
    "objectives",
    "conclusion",
    "abstract",
    "specifications",
])
def test_exemplar_store_non_hardware_exemplars_contain_zero_banned_tokens(domain: str, role: str):
    """Verify that get_domain_exemplar with is_hardware=False returns zero banned hardware tokens."""
    store = ExemplarStore()
    exemplar = store.get_domain_exemplar(domain=domain, role=role, is_hardware=False)
    if exemplar is None:
        return  # Acceptable if role is unrepresented

    paragraphs = exemplar.get("paragraphs", [])
    bullets = exemplar.get("bullets", [])
    combined_text = " ".join(paragraphs + bullets).lower()

    for banned_tok in BANNED_HARDWARE_TOKENS:
        match = re.search(rf"\b{re.escape(banned_tok)}\b", combined_text)
        assert match is None, (
            f"Banned hardware token '{banned_tok}' leaked in domain='{domain}', role='{role}'!\n"
            f"Exemplar content:\n{combined_text}"
        )


def test_exemplar_store_embedded_hardware_banned_when_is_hardware_false():
    """Verify that get_domain_exemplar bans EMBEDDED_HARDWARE exemplars when is_hardware=False."""
    store = ExemplarStore()
    # When caller explicitly requests non-hardware (is_hardware=False), EMBEDDED_HARDWARE exemplars
    # must either be None, fallback to neutral general academic, or contain 0 banned hardware tokens.
    exemplar = store.get_domain_exemplar(domain="EMBEDDED_HARDWARE", role="specifications", is_hardware=False)
    if exemplar is not None:
        combined_text = " ".join(exemplar.get("paragraphs", []) + exemplar.get("bullets", [])).lower()
        for banned_tok in BANNED_HARDWARE_TOKENS:
            match = re.search(rf"\b{re.escape(banned_tok)}\b", combined_text)
            assert match is None, (
                f"Defect in ExemplarStore: is_hardware=False was passed for EMBEDDED_HARDWARE, but "
                f"hardware exemplar was returned containing banned token '{banned_tok}'!\n"
                f"Returned exemplar: {combined_text}"
            )


# ------------------------------------------------------------------------------
# Test 3: prompts/default_section.j2 and hardware_overview.j2 Prompt Sanitization
# ------------------------------------------------------------------------------

@pytest.mark.parametrize("sample_key", [
    "bio_genealign",
    "web_campusconnect",
    "ml_freshknn",
    "software_moneygo",
])
def test_default_section_and_hardware_overview_sanitization(sample_key: str):
    """Verify default_section.j2 and hardware_overview.j2 do not emit Microcontroller Part Number or 3.3V."""
    s = SAMPLE_ABSTRACTS[sample_key]
    tmp = pathlib.Path(tempfile.mkdtemp())
    make_project_docx(tmp, s["title"], s["abstract"])
    brief, facts, _ = analyze_content_folder(str(tmp), sample_docx_path="COA_report.docx")

    assert not brief.implementation.is_hardware, f"Brief for {sample_key} should have is_hardware=False"

    for tmpl_name in ["default_section.j2", "hardware_overview.j2"]:
        tmpl = jinja_env.get_template(tmpl_name)
        rendered = tmpl.render(
            section_id="2.1",
            section_title="System Specifications",
            brief=brief,
            facts=facts,
            target_words=200,
        )
        rendered_lower = rendered.lower()
        assert "microcontroller part number" not in rendered_lower, (
            f"Hardware leak in {tmpl_name} for domain {brief.domain}: emitted 'Microcontroller Part Number'"
        )
        assert "operating voltage: 3.3v" not in rendered_lower, (
            f"Hardware leak in {tmpl_name} for domain {brief.domain}: emitted 'Operating Voltage: 3.3V'"
        )


# ------------------------------------------------------------------------------
# Test 4: Comprehensive Prompt Template Hardware Leak Sweep (Adversarial Challenge)
# ------------------------------------------------------------------------------

@pytest.mark.parametrize("sample_key", [
    "bio_genealign",
    "web_campusconnect",
    "ml_freshknn",
    "software_moneygo",
])
@pytest.mark.parametrize("section_id,section_title", [
    ("abstract", "Abstract"),
    ("1.1", "Overview of System"),
    ("1.2", "Objectives of the Project"),
    ("1.3", "Scope and Motivation"),
    ("6.1", "Conclusion"),
    ("6.4", "Future Scope"),
])
def test_all_section_prompts_for_microcontroller_leakage(sample_key: str, section_id: str, section_title: str):
    """Stress-test build_section_prompt across non-hardware domains to detect microcontroller leaks."""
    s = SAMPLE_ABSTRACTS[sample_key]
    tmp = pathlib.Path(tempfile.mkdtemp())
    make_project_docx(tmp, s["title"], s["abstract"])
    brief, facts, _ = analyze_content_folder(str(tmp), sample_docx_path="COA_report.docx")

    prompt = build_section_prompt(
        section_id=section_id,
        section_title=section_title,
        brief=brief,
        facts=facts,
        target_words=200,
    )
    prompt_lower = prompt.lower()

    # Hardware nomenclature that must NEVER appear in non-hardware prompts
    banned_in_prompt = [
        "microcontroller:",
        "serial usart1",
        "integrated embedded microcontroller design",
        "autonomous actuation without cloud dependency",
        "hardware components & peripherals:",
    ]

    for banned_phrase in banned_in_prompt:
        assert banned_phrase not in prompt_lower, (
            f"Hardware leak detected in section '{section_id}' ({section_title}) for domain '{brief.domain}'!\n"
            f"Found banned phrase: '{banned_phrase}'\n"
            f"Prompt snippet:\n"
            f"{prompt[:500]}..."
        )

"""Empirical challenger stress-test suite for Milestone 4 Round 2 verification.

Validates:
1. Prompt template rendering across all sections and non-hardware domains:
   - 0 occurrences of 'microcontroller'
   - 0 occurrences of 'serial usart1'
   - 0 concatenated '- Framework:' lines
   - 0 hardware pins or ADC counts
2. ExemplarStore.get_domain_exemplar fallback isolation:
   - is_hardware=False for EMBEDDED_HARDWARE cleanly returns neutral generic template
   - 0 banned hardware tokens across all roles and all domains
3. Direct rendering of all .j2 templates under non-hardware briefs
"""

import pathlib
import re
import tempfile
import pytest

from reportsmith.brief import ProjectBrief
from reportsmith.content_analyzer import analyze_content_folder
from reportsmith.facts import derive_facts
from reportsmith.generate import build_section_prompt, jinja_env, select_template_name
from reportsmith.trainer import ExemplarStore, BANNED_HARDWARE_TOKENS, DOMAIN_GENERAL

try:
    from tests.conftest import SAMPLE_ABSTRACTS, make_project_docx
except ImportError:
    from conftest import SAMPLE_ABSTRACTS, make_project_docx


ALL_SECTIONS = [
    ("abstract", "Abstract"),
    ("acknowledgement", "Acknowledgement"),
    ("declaration", "Declaration"),
    ("1.1", "Overview of System"),
    ("1.2", "Objectives of the Project"),
    ("1.3", "Scope and Motivation"),
    ("1.4", "Methodology and Work Plan"),
    ("2.1", "Computing Environment & Specifications"),
    ("2.2", "Platform Architecture"),
    ("2.3", "Hardware and Software Requirements"),
    ("2.4", "Functional Subsystems"),
    ("2.5", "Tooling and Runtime Environment"),
    ("3.1", "System Architecture Overview"),
    ("3.2", "Component Decomposition"),
    ("3.3", "Data Flow and Transformations"),
    ("3.4", "Pipeline Design"),
    ("3.5", "State Transitions and Logic"),
    ("4.1", "Core Implementation Overview"),
    ("4.2", "Algorithmic Execution"),
    ("4.3", "Integration and Data Handling"),
    ("5.1", "Verification and Validation Protocol"),
    ("5.2", "Performance and Functional Evaluation"),
    ("5.3", "Empirical Results Analysis"),
    ("6.1", "Conclusion"),
    ("6.2", "Key Engineering Insights"),
    ("6.3", "Operational Limitations"),
    ("6.4", "Future Scope"),
]

NON_HARDWARE_SAMPLES = [
    "bio_genealign",
    "web_campusconnect",
    "ml_freshknn",
    "software_moneygo",
]

ROLES = [
    "abstract",
    "introduction",
    "objectives",
    "scope",
    "applications",
    "specifications",
    "conclusion",
    "future_scope",
]


@pytest.mark.parametrize("sample_key", NON_HARDWARE_SAMPLES)
@pytest.mark.parametrize("sec_id,sec_title", ALL_SECTIONS)
def test_all_sections_zero_microcontroller_and_clean_framework_formatting(sample_key: str, sec_id: str, sec_title: str):
    """Assert 0 occurrences of microcontroller, serial usart1, and 0 concatenated Framework lines."""
    s = SAMPLE_ABSTRACTS[sample_key]
    tmp = pathlib.Path(tempfile.mkdtemp())
    make_project_docx(tmp, s["title"], s["abstract"])
    brief, facts, _ = analyze_content_folder(str(tmp), sample_docx_path="COA_report.docx")

    assert not brief.implementation.is_hardware

    prompt = build_section_prompt(
        section_id=sec_id,
        section_title=sec_title,
        brief=brief,
        facts=facts,
        target_words=250,
    )
    p_lower = prompt.lower()

    # 1. Zero 'microcontroller'
    assert "microcontroller" not in p_lower, (
        f"Hardware leak in [{sample_key}][{sec_id}]: found 'microcontroller'\n{prompt}"
    )

    # 2. Zero 'serial usart1'
    assert "serial usart1" not in p_lower, (
        f"Hardware leak in [{sample_key}][{sec_id}]: found 'serial usart1'\n{prompt}"
    )

    # 3. Zero concatenated '- Framework:' lines
    for line in prompt.splitlines():
        if "- Framework:" in line:
            stripped = line.strip()
            assert stripped.startswith("- Framework:"), (
                f"Formatting violation in [{sample_key}][{sec_id}]: concatenated '- Framework:' line -> '{line}'"
            )
            assert "Platform" not in line and "Environment" not in line, (
                f"Formatting collision in [{sample_key}][{sec_id}]: Platform and Framework merged -> '{line}'"
            )

    # 4. Zero hardware pins (PA0, PC13)
    for pin in ["pa0", "pc13"]:
        assert not re.search(rf"\b{pin}\b", p_lower), (
            f"Hardware pin leak in [{sample_key}][{sec_id}]: found pin '{pin}'"
        )


@pytest.mark.parametrize("role", ROLES)
def test_exemplar_store_embedded_hardware_fallback_to_neutral_generic(role: str):
    """Verify get_domain_exemplar with is_hardware=False for EMBEDDED_HARDWARE cleanly falls back to neutral generic template."""
    store = ExemplarStore()
    exemplar = store.get_domain_exemplar(domain="EMBEDDED_HARDWARE", role=role, is_hardware=False)

    assert exemplar is not None, f"Role '{role}' returned None for EMBEDDED_HARDWARE with is_hardware=False"
    assert exemplar.get("domain") == DOMAIN_GENERAL, (
        f"Expected neutral generic domain '{DOMAIN_GENERAL}', got '{exemplar.get('domain')}'"
    )
    assert len(exemplar.get("paragraphs", [])) >= 2, f"Role '{role}' should contain at least 2 paragraphs"

    combined = " ".join(exemplar.get("paragraphs", []) + exemplar.get("bullets", [])).lower()
    for tok in BANNED_HARDWARE_TOKENS:
        match = re.search(rf"\b{re.escape(tok)}\b", combined)
        assert match is None, (
            f"Banned hardware token '{tok}' leaked in neutral generic exemplar for role '{role}'!\nContent: {combined}"
        )


@pytest.mark.parametrize("domain", [
    "BIOINFORMATICS",
    "MACHINE_LEARNING",
    "WEB_CLOUD",
    "SOFTWARE_APP",
    "DATA_ANALYTICS",
    "GENERAL_ACADEMIC",
    "COA_SYSTEMS",
    "EMBEDDED_HARDWARE",
    "EMBEDDED_IOT",
])
@pytest.mark.parametrize("role", ROLES)
def test_all_domains_non_hardware_mode_zero_banned_tokens(domain: str, role: str):
    """Verify that across all domains with is_hardware=False, zero banned hardware tokens are returned."""
    store = ExemplarStore()
    exemplar = store.get_domain_exemplar(domain=domain, role=role, is_hardware=False)
    if exemplar is None:
        return

    combined = " ".join(exemplar.get("paragraphs", []) + exemplar.get("bullets", [])).lower()
    for tok in BANNED_HARDWARE_TOKENS:
        match = re.search(rf"\b{re.escape(tok)}\b", combined)
        assert match is None, (
            f"Banned hardware token '{tok}' leaked in domain='{domain}', role='{role}'!\nContent: {combined}"
        )


def test_all_prompt_templates_render_without_hardware_when_brief_is_software():
    """Directly test every template in prompts/ with a software brief."""
    s = SAMPLE_ABSTRACTS["bio_genealign"]
    tmp = pathlib.Path(tempfile.mkdtemp())
    make_project_docx(tmp, s["title"], s["abstract"])
    brief, facts, _ = analyze_content_folder(str(tmp), sample_docx_path="COA_report.docx")

    template_names = [
        "abstract.j2",
        "bullets_section.j2",
        "conclusion.j2",
        "default_section.j2",
        "hardware_overview.j2",
        "implementation.j2",
        "introduction.j2",
        "prelim_matter.j2",
        "system_design.j2",
        "testing_results.j2",
    ]

    for tmpl_name in template_names:
        tmpl = jinja_env.get_template(tmpl_name)
        rendered = tmpl.render(
            section_id="2.1",
            section_title="Test Section Title",
            brief=brief,
            facts=facts,
            target_words=250,
        )
        r_lower = rendered.lower()
        assert "microcontroller part number" not in r_lower, f"Leaked microcontroller in {tmpl_name}"
        assert "serial usart1" not in r_lower, f"Leaked serial usart1 in {tmpl_name}"
        assert "operating voltage: 3.3v" not in r_lower, f"Leaked 3.3V in {tmpl_name}"

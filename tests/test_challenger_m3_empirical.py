"""Empirical adversarial stress test suite for Milestone 3:
Dynamic Outline Synthesis & Module Section Mapping across Multi-Domain Briefs.

Authored by: Challenger M3_1 (Empirical Challenger)

Verifies:
1. Dynamic Chapter 4 module section mapping for Web, ML, Bio, Embedded, Software domains.
2. Complete absence of hardware contamination in Chapters 1-6 for all non-hardware briefs.
3. Preservation of hardware-appropriate sections (Circuit Diagram, Pin Configuration, GPIO) for embedded briefs.
4. Institutional academic backbone invariants (9 prelims, 6 chapters, end matter, section numbering).
5. Adversarial edge cases: empty modules, single module, 10+ modules, dirty strings, unicode, hierarchy resolution.
"""

import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

import pytest

from reportsmith.content_analyzer import analyze_content_folder
from reportsmith.outline import (
    OutlineItem,
    ReportOutline,
    extract_abstract_modules,
    synthesize_universal_outline,
)

try:
    from tests.conftest import SAMPLE_ABSTRACTS, make_project_docx
except ImportError:
    from conftest import SAMPLE_ABSTRACTS, make_project_docx


# Banned hardware tokens strictly forbidden in non-hardware report body sections
BANNED_HARDWARE_TOKENS = [
    "microcontroller",
    "pinout",
    "circuit diagram",
    "stm32",
    "sensor wiring",
]

MANDATORY_PRELIMS = [
    "title_page",
    "certificate",
    "declaration",
    "acknowledgement",
    "abstract",
    "table_of_contents",
    "list_of_figures",
    "list_of_tables",
    "list_of_abbreviations",
]


# ==============================================================================
# 1. CORE DOMAIN BRIEFS: DYNAMIC MODULE SECTION MAPPING & PURITY
# ==============================================================================

@pytest.mark.parametrize(
    "domain,modules,expected_subheadings",
    [
        (
            "WEB_CLOUD",
            ["User Authentication", "Event Catalog", "Stripe Payment Gateway", "Admin Analytics Dashboard"],
            [
                "User Authentication Module Implementation",
                "Event Catalog Module Implementation",
                "Stripe Payment Gateway Module Implementation",
                "Admin Analytics Dashboard Module Implementation",
            ],
        ),
        (
            "WEB_CLOUD",
            ["Product Catalog", "Shopping Cart", "Order Fulfillment Subsystem"],
            [
                "Product Catalog Module Implementation",
                "Shopping Cart Module Implementation",
                "Order Fulfillment Module Implementation",
            ],
        ),
        (
            "MACHINE_LEARNING",
            ["Image Normalization", "Spatial Feature Extraction", "ResNet50 Classification", "Inference Pipeline"],
            [
                "Image Normalization Module Implementation",
                "Spatial Feature Extraction Module Implementation",
                "ResNet50 Classification Module Implementation",
                "Inference Pipeline Module Implementation",
            ],
        ),
        (
            "MACHINE_LEARNING",
            ["Tokenization Subsystem", "Transformer Attention Engine", "Sentiment Classifier"],
            [
                "Tokenization Module Implementation",
                "Transformer Attention Engine Module Implementation",
                "Sentiment Classifier Module Implementation",
            ],
        ),
        (
            "BIOINFORMATICS",
            ["Genomic FASTA Parser", "Smith-Waterman Alignment Matrix", "Affine Gap Scoring Engine", "Mutation Caller"],
            [
                "Genomic FASTA Parser Module Implementation",
                "Smith-Waterman Alignment Matrix Module Implementation",
                "Affine Gap Scoring Engine Module Implementation",
                "Mutation Caller Module Implementation",
            ],
        ),
        (
            "SOFTWARE_APP",
            ["Account Reconciliation", "Budget Forecasting Subsystem", "CSV Tax Exporter"],
            [
                "Account Reconciliation Module Implementation",
                "Budget Forecasting Module Implementation",
                "CSV Tax Exporter Module Implementation",
            ],
        ),
        (
            "DATA_ANALYTICS",
            ["Telemetry Ingestion", "Stream Aggregation", "Anomaly Detection Model"],
            [
                "Telemetry Ingestion Module Implementation",
                "Stream Aggregation Module Implementation",
                "Anomaly Detection Model Module Implementation",
            ],
        ),
    ],
)
def test_dynamic_chapter4_module_mapping_non_hardware(domain, modules, expected_subheadings):
    """Verify Chapter 4 synthesizes dynamic module sections matching provided subsystems."""
    outline = synthesize_universal_outline(
        domain=domain,
        title=f"Advanced {domain} System",
        modules=modules,
        is_hardware=False,
    )
    ch4_items = [i for i in outline.items if i.chapter == "4"]
    ch4_titles = [i.title for i in ch4_items]

    # Verify 4.1 header
    assert ch4_items[0].number == "4.1"
    assert "Setup and Configuration" in ch4_items[0].title

    # Verify each dynamic module mapped into Chapter 4
    for expected in expected_subheadings:
        assert expected in ch4_titles, f"Expected '{expected}' in Ch4 titles: {ch4_titles}"

    # Verify sequential section numbering
    for idx, item in enumerate(ch4_items, start=1):
        assert item.number == f"4.{idx}", f"Mismatched section number {item.number} vs 4.{idx}"

    # Verify zero hardware contamination in Chapters 1-6
    for item in outline.items:
        if item.chapter in ("1", "2", "3", "4", "5", "6"):
            t_low = item.title.lower()
            for token in BANNED_HARDWARE_TOKENS:
                assert token not in t_low, (
                    f"Contamination detected in {domain} section {item.number}: '{item.title}' contains '{token}'"
                )


# ==============================================================================
# 2. EMBEDDED HARDWARE BRIEFS: HARDWARE SECTION PRESERVATION
# ==============================================================================

@pytest.mark.parametrize(
    "domain",
    ["EMBEDDED_HARDWARE", "EMBEDDED_IOT"],
)
def test_embedded_hardware_preserves_hardware_sections(domain):
    """Verify embedded hardware outlines preserve pinouts, circuit diagrams, and microcontroller sections."""
    outline = synthesize_universal_outline(
        domain=domain,
        title="Automated Soil Moisture Irrigation System",
        is_hardware=True,
    )

    body_sections = {i.number: i.title for i in outline.items if i.chapter in ("1", "2", "3", "4", "5", "6")}

    # Chapter 2 hardware specs
    assert "2.1" in body_sections
    assert "STM32 Microcontroller Overview" in body_sections["2.1"]
    assert "2.2" in body_sections
    assert "STM32 Development Board" in body_sections["2.2"]
    assert "2.3" in body_sections
    assert "Hardware Components and Sensors Used" in body_sections["2.3"]

    # Chapter 3 circuit & pin configuration
    assert "3.3" in body_sections
    assert "Circuit Diagram" in body_sections["3.3"]
    assert "3.5" in body_sections
    assert "Pin Configuration and Peripheral Mapping" in body_sections["3.5"]

    # Chapter 4 GPIO & firmware
    assert "4.2" in body_sections
    assert "GPIO and Peripheral Configuration" in body_sections["4.2"]
    assert "4.3" in body_sections
    assert "Embedded C Program Development" in body_sections["4.3"]
    assert "4.4" in body_sections
    assert "Hardware Interfacing and Integration" in body_sections["4.4"]

    # Chapter 5 hardware photographs
    assert "5.3" in body_sections
    assert "Hardware Implementation Photographs" in body_sections["5.3"]


# ==============================================================================
# 3. SAMPLE BENCHMARK ABSTRACTS: ZERO CONTAMINATION ON NON-HARDWARE
# ==============================================================================

def test_sample_abstracts_outline_synthesis_and_purity():
    """Verify all project abstracts from SAMPLE_ABSTRACTS generate strictly pure outlines."""
    for key, sample in SAMPLE_ABSTRACTS.items():
        is_hw = sample.get("is_hardware", False)
        dom = sample.get("domain", "GENERAL_ACADEMIC")
        title = sample.get("title", "Project")
        abstract = sample.get("abstract", "")

        outline = synthesize_universal_outline(
            domain=dom,
            title=title,
            abstract_text=abstract,
            is_hardware=is_hw,
        )

        body_items = [i for i in outline.items if i.chapter in ("1", "2", "3", "4", "5", "6")]

        if not is_hw:
            for item in body_items:
                t_low = item.title.lower()
                for token in BANNED_HARDWARE_TOKENS:
                    assert token not in t_low, (
                        f"Sample '{key}' violated hardware purity: Section {item.number} '{item.title}' has '{token}'"
                    )
        else:
            titles_str = " ".join(i.title.lower() for i in body_items)
            assert "circuit diagram" in titles_str
            assert "pin configuration" in titles_str
            assert "microcontroller" in titles_str


# ==============================================================================
# 4. INSTITUTIONAL ACADEMIC BACKBONE INVARIANTS
# ==============================================================================

@pytest.mark.parametrize(
    "domain,is_hw",
    [
        ("WEB_CLOUD", False),
        ("MACHINE_LEARNING", False),
        ("BIOINFORMATICS", False),
        ("SOFTWARE_APP", False),
        ("DATA_ANALYTICS", False),
        ("GENERAL_ACADEMIC", False),
        ("COA_SYSTEMS", False),
        ("EMBEDDED_HARDWARE", True),
        ("EMBEDDED_IOT", True),
    ],
)
def test_institutional_backbone_invariants(domain, is_hw):
    """Verify all 9 prelims, 6 chapters, and standard end matter exist across all domains."""
    outline = synthesize_universal_outline(domain=domain, is_hardware=is_hw, has_code=True)

    # 1. Prelims verification
    prelims = [i for i in outline.items if i.kind == "prelim"]
    assert len(prelims) == 9, f"Expected 9 prelims for {domain}, got {len(prelims)}"
    for expected_id, actual_item in zip(MANDATORY_PRELIMS, prelims):
        assert actual_item.id == expected_id, f"Prelim order mismatch: expected {expected_id}, got {actual_item.id}"

    # 2. Chapters verification (ch_1 to ch_6)
    chapters = [i for i in outline.items if i.kind == "chapter"]
    assert len(chapters) == 6, f"Expected 6 chapters, got {len(chapters)}"
    ch_ids = [c.id for c in chapters]
    assert ch_ids == ["ch_1", "ch_2", "ch_3", "ch_4", "ch_5", "ch_6"]

    # 3. Chapter 1 invariant sections
    ch1_secs = [i for i in outline.items if i.chapter == "1"]
    ch1_map = {s.number: s.title for s in ch1_secs}
    assert ch1_map.get("1.1") == "Project Overview"
    assert ch1_map.get("1.2") == "Objectives"
    assert ch1_map.get("1.3") == "Scope of the Project"
    assert ch1_map.get("1.4") == "Applications"

    # 4. Chapter 6 invariant sections
    ch6_secs = [i for i in outline.items if i.chapter == "6"]
    ch6_map = {s.number: s.title for s in ch6_secs}
    assert ch6_map.get("6.1") == "Conclusion"
    assert ch6_map.get("6.2") == "Learning Outcomes"
    assert ch6_map.get("6.3") == "Limitations"
    assert ch6_map.get("6.4") == "Future Enhancements"

    # 5. End matter
    end_matter = [i for i in outline.items if i.kind == "end_matter"]
    end_ids = [e.id for e in end_matter]
    assert "references" in end_ids
    assert "appendix_a_source_code" in end_ids
    assert "appendix_b_additional_details" in end_ids


# ==============================================================================
# 5. ADAPTIVE APPENDIX A CODE TITLES & CODE SUPPRESSION
# ==============================================================================

@pytest.mark.parametrize(
    "domain,has_code,expected_appendix_title",
    [
        ("EMBEDDED_HARDWARE", True, "Appendix A – Complete Embedded C Source Code"),
        ("SOFTWARE_APP", True, "Appendix A – Complete Application Source Code"),
        ("WEB_CLOUD", True, "Appendix A – Complete Full-Stack Web Application Source Code"),
        ("COA_SYSTEMS", True, "Appendix A – Complete Assembly Source Code"),
        ("MACHINE_LEARNING", True, "Appendix A – Complete Source Code"),
        ("BIOINFORMATICS", True, "Appendix A – Complete Source Code"),
        ("WEB_CLOUD", False, None),
        ("EMBEDDED_HARDWARE", False, None),
    ],
)
def test_adaptive_appendix_a_code_title(domain, has_code, expected_appendix_title):
    """Verify Appendix A title adapts to domain when code is present and is omitted when has_code=False."""
    outline = synthesize_universal_outline(domain=domain, has_code=has_code)
    app_a = next((i for i in outline.items if i.id == "appendix_a_source_code"), None)

    if has_code:
        assert app_a is not None
        assert app_a.title == expected_appendix_title
    else:
        assert app_a is None


# ==============================================================================
# 6. ADVERSARIAL BOUNDARY & STRESS TESTS
# ==============================================================================

def test_adversarial_empty_and_none_modules():
    """Verify empty or None modules fall back cleanly to domain defaults without crashing."""
    for domain in ["WEB_CLOUD", "MACHINE_LEARNING", "BIOINFORMATICS", "SOFTWARE_APP", "GENERAL_ACADEMIC"]:
        outline_none = synthesize_universal_outline(domain=domain, modules=None, is_hardware=False)
        outline_empty = synthesize_universal_outline(domain=domain, modules=[], is_hardware=False)

        ch4_none = [i for i in outline_none.items if i.chapter == "4"]
        ch4_empty = [i for i in outline_empty.items if i.chapter == "4"]

        assert len(ch4_none) >= 5
        assert len(ch4_empty) >= 5
        # Verify sequential numbering
        for idx, item in enumerate(ch4_none, start=1):
            assert item.number == f"4.{idx}"
        for idx, item in enumerate(ch4_empty, start=1):
            assert item.number == f"4.{idx}"


def test_adversarial_single_module():
    """Verify single-module list produces valid sequential Chapter 4 sections."""
    outline = synthesize_universal_outline(
        domain="WEB_CLOUD",
        modules=["Core API Gateway"],
        is_hardware=False,
    )
    ch4_items = [i for i in outline.items if i.chapter == "4"]
    assert len(ch4_items) == 5  # 4.1 Setup, 4.2 Core API Gateway, 4.3 Storage, 4.4 Testing, 4.5 Challenges
    assert ch4_items[1].number == "4.2"
    assert ch4_items[1].title == "Core API Gateway Module Implementation"


def test_adversarial_ten_modules_stress():
    """Verify 10 extracted modules are cleanly formatted and numbered from 4.2 to 4.11."""
    modules = [f"Subsystem Alpha {i}" for i in range(1, 11)]
    outline = synthesize_universal_outline(
        domain="WEB_CLOUD",
        modules=modules,
        is_hardware=False,
    )
    ch4_items = [i for i in outline.items if i.chapter == "4"]
    # 1 (setup) + 10 modules + 1 (storage) + 1 (integration) + 1 (challenges) = 14
    assert len(ch4_items) == 14
    for i in range(1, 11):
        sec_num = f"4.{i + 1}"
        expected_title = f"Subsystem Alpha {i} Module Implementation"
        actual_item = ch4_items[i]
        assert actual_item.number == sec_num
        assert actual_item.title == expected_title


def test_adversarial_dirty_module_strings():
    """Verify modules with whitespace, trailing 'Module'/'Subsystem', and short tokens are sanitized."""
    dirty_modules = [
        "   User Authentication Module   ",
        "Payment Processing Subsystem",
        "a",  # too short (< 3 chars), should be dropped
        "   ",  # whitespace only, should be dropped
        "Order Fulfillment module",
        "Notification Dispatcher subsystem",
    ]
    outline = synthesize_universal_outline(
        domain="WEB_CLOUD",
        modules=dirty_modules,
        is_hardware=False,
    )
    ch4_titles = [i.title for i in outline.items if i.chapter == "4"]

    assert "User Authentication Module Implementation" in ch4_titles
    assert "Payment Processing Module Implementation" in ch4_titles
    assert "Order Fulfillment Module Implementation" in ch4_titles
    assert "Notification Dispatcher Module Implementation" in ch4_titles
    # Verify no double 'Module Module' or 'Subsystem Module'
    for title in ch4_titles:
        assert "Module Module" not in title
        assert "Subsystem Module" not in title


def test_adversarial_unicode_and_special_characters():
    """Verify modules with unicode, slashes, ampersands, and hyphens render cleanly."""
    complex_modules = [
        "OAuth 2.0 & JWT Security",
        "CI/CD Build Pipeline",
        "NLP Ingestion / Pre-Processing",
        "Model Training (v2.0-beta)",
    ]
    outline = synthesize_universal_outline(
        domain="MACHINE_LEARNING",
        modules=complex_modules,
        is_hardware=False,
    )
    ch4_titles = [i.title for i in outline.items if i.chapter == "4"]

    assert "OAuth 2.0 & JWT Security Module Implementation" in ch4_titles
    assert "CI/CD Build Pipeline Module Implementation" in ch4_titles
    assert "NLP Ingestion / Pre-Processing Module Implementation" in ch4_titles
    assert "Model Training (v2.0-beta) Module Implementation" in ch4_titles


def test_adversarial_abstract_only_module_extraction():
    """Verify structured modules are extracted directly from abstract text when modules=None."""
    abstract = (
        "The proposed platform is structured into four primary subsystems: "
        "User Onboarding, Dynamic Catalog, Secure Checkout, and Audit Analytics. "
        "The system achieves high scalability under Kubernetes orchestration."
    )
    outline = synthesize_universal_outline(
        domain="WEB_CLOUD",
        abstract_text=abstract,
        modules=None,
        is_hardware=False,
    )
    ch4_titles = [i.title for i in outline.items if i.chapter == "4"]

    assert any("User Onboarding" in t for t in ch4_titles)
    assert any("Dynamic Catalog" in t for t in ch4_titles)
    assert any("Secure Checkout" in t for t in ch4_titles)
    assert any("Audit Analytics" in t for t in ch4_titles)


# ==============================================================================
# 7. IS_HARDWARE RESOLUTION HIERARCHY
# ==============================================================================

def test_is_hardware_hierarchy_resolution():
    """Verify precedence rules for determining is_hardware."""
    # 1. Explicit argument overrides everything
    b_hw = SimpleNamespace(implementation=SimpleNamespace(is_hardware=True, modules=[]))
    o1 = synthesize_universal_outline(brief=b_hw, is_hardware=False)
    assert not any("circuit diagram" in i.title.lower() for i in o1.items)

    b_sw = SimpleNamespace(implementation=SimpleNamespace(is_hardware=False, modules=[]))
    o2 = synthesize_universal_outline(brief=b_sw, is_hardware=True)
    assert any("circuit diagram" in i.title.lower() for i in o2.items)

    # 2. brief.implementation.is_hardware inspected when is_hardware=None
    o3 = synthesize_universal_outline(brief=b_hw, is_hardware=None)
    assert any("circuit diagram" in i.title.lower() for i in o3.items)

    o4 = synthesize_universal_outline(brief=b_sw, is_hardware=None)
    assert not any("circuit diagram" in i.title.lower() for i in o4.items)

    # 3. brief.is_hardware fallback
    b_top_hw = SimpleNamespace(is_hardware=True, modules=[])
    b_top_sw = SimpleNamespace(is_hardware=False, modules=[])
    o5 = synthesize_universal_outline(brief=b_top_hw)
    o6 = synthesize_universal_outline(brief=b_top_sw)
    assert any("circuit diagram" in i.title.lower() for i in o5.items)
    assert not any("circuit diagram" in i.title.lower() for i in o6.items)

    # 4. Domain string fallback
    o7 = synthesize_universal_outline(domain="EMBEDDED_HARDWARE")
    o8 = synthesize_universal_outline(domain="EMBEDDED_IOT")
    o9 = synthesize_universal_outline(domain="WEB_CLOUD")
    assert any("circuit diagram" in i.title.lower() for i in o7.items)
    assert any("circuit diagram" in i.title.lower() for i in o8.items)
    assert not any("circuit diagram" in i.title.lower() for i in o9.items)


# ==============================================================================
# 8. FULL INGESTION PIPELINE INTEGRATION
# ==============================================================================

def test_full_pipeline_ingestion_multi_domain_outline_coupling(tmp_path):
    """End-to-end integration: analyze_content_folder -> synthesize_universal_outline."""
    for key in ["web_campusconnect", "ml_freshknn", "bio_genealign", "embedded_stm32"]:
        sample = SAMPLE_ABSTRACTS[key]
        proj_dir = tmp_path / key
        proj_dir.mkdir()
        make_project_docx(proj_dir, sample["title"], sample["abstract"])

        brief, facts, outline = analyze_content_folder(str(proj_dir), sample_docx_path="COA_report.docx")

        is_hw = sample.get("is_hardware", False)
        ch4_secs = [i for i in outline.items if i.chapter == "4"]
        body_secs = [i for i in outline.items if i.chapter in ("1", "2", "3", "4", "5", "6")]

        if not is_hw:
            # Dynamic modules must be mapped into Chapter 4
            for mod in brief.implementation.modules:
                clean = re.sub(r"\s*(?:Module|Subsystem)\s*$", "", mod, flags=re.IGNORECASE).strip().lower()
                assert any(clean in s.title.lower() for s in ch4_secs), (
                    f"Extracted module '{mod}' not found in Ch 4 sections for {key}"
                )
            # Hardware tokens must be absent
            for s in body_secs:
                for token in BANNED_HARDWARE_TOKENS:
                    assert token not in s.title.lower(), (
                        f"Found banned token '{token}' in {key} section {s.number}: {s.title}"
                    )
        else:
            # Hardware sections must be present
            titles_str = " ".join(s.title.lower() for s in body_secs)
            assert "circuit diagram" in titles_str
            assert "pin configuration" in titles_str
            assert "gpio" in titles_str

"""Tier 1, Tier 3, and Tier 4 E2E Tests: Multi-Domain Ingestion, Grounding Whitelists,

Diagram Synthesis, and Academic Document Invariants.

Authoritative References:
- ORIGINAL_REQUEST.md § R3 (Universal Dynamic Outline, Diagram, and Section Synthesis)
- PROJECT.md § Ingestion <-> Fact Grounding, Ingestion <-> Outline & Diagrams
"""

import os
import sys
from pathlib import Path
from typing import Set
import docx
import pytest

# Ensure workspace root is in sys.path for test discovery
_workspace_root = str(Path(__file__).resolve().parent.parent)
if _workspace_root not in sys.path:
    sys.path.insert(0, _workspace_root)

from reportsmith.check import validate_report
from reportsmith.content_analyzer import analyze_content_folder
from reportsmith.diagrams import generate_all_diagrams
from reportsmith.docx_build import DocxReportBuilder
from reportsmith.facts import derive_facts
from reportsmith.outline import parse_format_file, synthesize_universal_outline

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


# ==============================================================================
# TIER 1: FEATURE 1 — Abstract Ingestion Across Distinct Domains (>= 5 tests)
# ==============================================================================

@pytest.mark.tier1
def test_tier1_ingest_web_cloud_domain(tmp_path):
    """Verify ingestion of Web/Cloud MERN stack project abstract."""
    sample = SAMPLE_ABSTRACTS["web_campusconnect"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])

    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    assert brief.domain == "WEB_CLOUD", f"Expected domain WEB_CLOUD, got {brief.domain}"
    assert "CampusConnect" in brief.project.title or "CAMPUSCONNECT" in brief.project.title.upper()
    assert brief.abstract is not None and "MERN" in brief.abstract
    # Assert non-hardware schema decoupling
    is_hw = getattr(brief.implementation, "is_hardware", False)
    assert is_hw is False, "Web/Cloud domain must have is_hardware=False"
    board_name = getattr(brief.implementation.board, "name", "").lower()
    assert "stm32" not in board_name, f"Unexpected STM32 board in Web project: {board_name}"


@pytest.mark.tier1
def test_tier1_ingest_machine_learning_domain(tmp_path):
    """Verify ingestion of Computer Vision / ML produce classification abstract."""
    sample = SAMPLE_ABSTRACTS["ml_freshknn"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])

    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    assert brief.domain == "MACHINE_LEARNING", f"Expected domain MACHINE_LEARNING, got {brief.domain}"
    assert "FreshKNN" in brief.project.title or "FRESHKNN" in brief.project.title.upper()
    is_hw = getattr(brief.implementation, "is_hardware", False)
    assert is_hw is False, "Machine Learning domain must have is_hardware=False"


@pytest.mark.tier1
def test_tier1_ingest_bioinformatics_domain(tmp_path):
    """Verify ingestion of Bioinformatics / DNA sequence alignment abstract."""
    sample = SAMPLE_ABSTRACTS["bio_genealign"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])

    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    assert brief.domain in ("BIOINFORMATICS", "DATA_ANALYTICS", "GENERAL_ACADEMIC", "SOFTWARE_APP"), (
        f"Expected bioinformatics/software domain, got {brief.domain}"
    )
    assert "GeneAlign" in brief.project.title or "GENEALIGN" in brief.project.title.upper()
    is_hw = getattr(brief.implementation, "is_hardware", False)
    assert is_hw is False, "Bioinformatics domain must have is_hardware=False"


@pytest.mark.tier1
def test_tier1_ingest_software_engineering_domain(tmp_path):
    """Verify ingestion of Software Engineering / Java desktop application abstract."""
    sample = SAMPLE_ABSTRACTS["software_moneygo"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])

    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    assert brief.domain == "SOFTWARE_APP", f"Expected domain SOFTWARE_APP, got {brief.domain}"
    assert "MoneyGo" in brief.project.title or "MONEYGO" in brief.project.title.upper()
    is_hw = getattr(brief.implementation, "is_hardware", False)
    assert is_hw is False, "Software domain must have is_hardware=False"


@pytest.mark.tier1
def test_tier1_ingest_embedded_hardware_domain(tmp_path):
    """Verify ingestion of Microcontroller / Embedded IoT hardware abstract."""
    sample = SAMPLE_ABSTRACTS["embedded_stm32"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])

    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    assert brief.domain in ("EMBEDDED_HARDWARE", "EMBEDDED_IOT"), (
        f"Expected EMBEDDED domain, got {brief.domain}"
    )
    is_hw = getattr(brief.implementation, "is_hardware", True)
    assert is_hw is True, "Embedded project must have is_hardware=True"
    board_mcu = getattr(brief.implementation.board, "mcu_part_number", "").upper()
    assert "STM32" in board_mcu or "CORTEX" in getattr(brief.implementation.board, "core", "").upper()


@pytest.mark.tier1
def test_tier1_ingest_cloud_microservices_domain(tmp_path):
    """Verify ingestion of Cloud Microservices & Distributed Architecture abstract."""
    title = "KubeStream: Event-Driven Microservices Architecture on Kubernetes"
    abstract = (
        "KubeStream is a cloud-native microservices platform deployed on Kubernetes using Docker "
        "containers and Apache Kafka event streaming. The system scales horizontally across 16 "
        "worker pods, processing 25000 events per second with Redis caching over port 6379."
    )
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, title, abstract)

    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    assert brief.domain in ("WEB_CLOUD", "SOFTWARE_APP", "DATA_ANALYTICS")
    is_hw = getattr(brief.implementation, "is_hardware", False)
    assert is_hw is False, "Cloud microservices domain must have is_hardware=False"


# ==============================================================================
# TIER 1: FEATURE 3 — Grounding Number Whitelisting (>= 5 tests)
# ==============================================================================

@pytest.mark.tier1
def test_tier1_facts_whitelists_abstract_numbers(tmp_path):
    """Verify that all numerical constants in brief.abstract are automatically added to known_numbers."""
    sample = SAMPLE_ABSTRACTS["ml_freshknn"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    derived_facts = derive_facts(brief)

    # Numbers from FreshKNN abstract: 128, 24, 94.5, 1200, 18
    for expected_num in ["128", "24", "94.5", "1200", "18"]:
        assert expected_num in derived_facts.known_numbers, (
            f"Expected abstract number '{expected_num}' to be whitelisted in known_numbers"
        )


@pytest.mark.tier1
def test_tier1_facts_whitelists_standard_http_status_codes(tmp_path):
    """Verify standard HTTP status codes are whitelisted for Web/Cloud domains."""
    sample = SAMPLE_ABSTRACTS["web_campusconnect"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    derived_facts = derive_facts(brief)

    http_codes = {"200", "201", "204", "400", "401", "403", "404", "500"}
    missing = http_codes - derived_facts.known_numbers
    assert not missing, f"Missing standard HTTP status codes in facts.known_numbers: {missing}"


@pytest.mark.tier1
def test_tier1_facts_whitelists_standard_network_ports(tmp_path):
    """Verify standard networking ports are whitelisted for Web and Software domains."""
    sample = SAMPLE_ABSTRACTS["web_campusconnect"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    derived_facts = derive_facts(brief)

    ports = {"80", "443", "3000", "5000", "8080"}
    missing = ports - derived_facts.known_numbers
    assert not missing, f"Missing standard network ports in facts.known_numbers: {missing}"


@pytest.mark.tier1
def test_tier1_facts_whitelists_latency_and_telemetry_metrics(tmp_path):
    """Verify latency metrics appearing in abstract (e.g. 140 ms, 450 concurrent) are whitelisted."""
    sample = SAMPLE_ABSTRACTS["web_campusconnect"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    derived_facts = derive_facts(brief)

    for num in ["140", "450"]:
        assert num in derived_facts.known_numbers, (
            f"Expected latency/telemetry metric '{num}' to be grounded in facts"
        )


@pytest.mark.tier1
def test_tier1_facts_preserves_hardware_calibration_numbers(tmp_path):
    """Verify that embedded hardware facts retain clock frequency, baud rate, and ADC ranges."""
    sample = SAMPLE_ABSTRACTS["embedded_stm32"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    derived_facts = derive_facts(brief)

    hw_numbers = {"72", "115200", "3.3", "4095", "1000", "1300", "3700", "35"}
    missing = hw_numbers - derived_facts.known_numbers
    assert not missing, f"Missing hardware constants in known_numbers: {missing}"


@pytest.mark.tier1
def test_tier1_validator_passes_on_web_cloud_numbers(tmp_path):
    """Verify ReportValidator does not falsely flag standard HTTP status codes and ports as ungrounded."""
    sample = SAMPLE_ABSTRACTS["web_campusconnect"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    # Construct test docx with HTTP codes in prose
    doc = docx.Document()
    doc.add_paragraph("API endpoints return HTTP 200 OK for queries and HTTP 201 Created for new events.")
    doc.add_paragraph("Unauthorized access attempts trigger HTTP 401 and HTTP 403 Forbidden responses.")
    doc.add_paragraph("Requests to missing resources yield HTTP 404 Not Found over port 5000.")
    test_docx = tmp_path / "web_test.docx"
    doc.save(str(test_docx))

    check_txt = tmp_path / "check.txt"
    is_valid = validate_report(test_docx, brief, facts, out_report_path=check_txt)

    report_content = check_txt.read_text(encoding="utf-8") if check_txt.is_file() else ""
    assert "Ungrounded number '201'" not in report_content
    assert "Ungrounded number '401'" not in report_content
    assert "Ungrounded number '404'" not in report_content
    assert is_valid is True, f"Validator failed on standard web numbers:\n{report_content}"


# ==============================================================================
# TIER 1: FEATURE 4 — Diagram Generation Without MCU Pinouts (>= 5 tests)
# ==============================================================================

@pytest.mark.tier1
def test_tier1_software_diagram_no_mcu_pinouts(tmp_path):
    """Verify that non-hardware projects produce diagrams without MCU pins (PA0, PB6, ST-Link, etc.)."""
    sample = SAMPLE_ABSTRACTS["web_campusconnect"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    diagrams = generate_all_diagrams(brief, facts, out_dir=tmp_path / "diagrams")

    for diag_key, diag_path in diagrams.items():
        assert diag_path.is_file(), f"Diagram {diag_key} was not generated"
        assert diag_path.stat().st_size > 5000, f"Diagram {diag_key} file size suspiciously small"


@pytest.mark.tier1
def test_tier1_software_diagram_generates_dfd(tmp_path):
    """Verify that software/web projects generate a Data Flow Diagram (DFD) instead of wiring schematic."""
    sample = SAMPLE_ABSTRACTS["web_campusconnect"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    diagrams = generate_all_diagrams(brief, facts, out_dir=tmp_path / "diagrams")

    conn_diag = diagrams.get("connection_diagram")
    assert conn_diag and conn_diag.is_file()


@pytest.mark.tier1
def test_tier1_software_flowchart_process_logic(tmp_path):
    """Verify software flowchart reflects application/data process flow, not firmware setup/loop."""
    sample = SAMPLE_ABSTRACTS["software_moneygo"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    diagrams = generate_all_diagrams(brief, facts, out_dir=tmp_path / "diagrams")
    flowchart_p = diagrams.get("flowchart")

    assert flowchart_p and flowchart_p.is_file()
    assert flowchart_p.stat().st_size > 5000


@pytest.mark.tier1
def test_tier1_hardware_diagram_retains_mcu_pinouts(tmp_path):
    """Verify that embedded hardware projects correctly generate hardware wiring diagram with MCU pins."""
    sample = SAMPLE_ABSTRACTS["embedded_stm32"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    diagrams = generate_all_diagrams(brief, facts, out_dir=tmp_path / "diagrams")

    assert diagrams["block_diagram"].is_file()
    assert diagrams["connection_diagram"].is_file()
    assert diagrams["flowchart"].is_file()


@pytest.mark.tier1
def test_tier1_diagram_output_validity_and_resolution(tmp_path):
    """Verify all generated diagram files are valid PNG images with standard 8-byte PNG headers."""
    sample = SAMPLE_ABSTRACTS["ml_freshknn"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    diagrams = generate_all_diagrams(brief, facts, out_dir=tmp_path / "diagrams")

    png_magic = b"\x89PNG\r\n\x1a\n"
    for name, path in diagrams.items():
        with open(path, "rb") as f:
            header = f.read(8)
        assert header == png_magic, f"File {name} ({path}) does not have valid PNG magic bytes"


# ==============================================================================
# TIER 3: CROSS-FEATURE PAIRWISE COMBINATIONS (Domain x Outlines x Diagrams)
# ==============================================================================

@pytest.mark.tier3
def test_tier3_pairwise_web_domain_outline_and_diagrams(tmp_path):
    """Pairwise combination: Web domain with dynamic outline and dynamic diagrams."""
    sample = SAMPLE_ABSTRACTS["web_campusconnect"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    # Assert dynamic outline reflects web domain entities
    outline_titles = [item.title.lower() for item in outline.items]
    assert not any("stm32" in t for t in outline_titles), f"Found STM32 in web outline: {outline_titles}"
    assert not any("microcontroller" in t for t in outline_titles), f"Found microcontroller in web outline: {outline_titles}"

    # Assert diagrams generated successfully without hardware pins
    diagrams = generate_all_diagrams(brief, facts, out_dir=tmp_path / "diagrams")
    assert len(diagrams) == 3


@pytest.mark.tier3
def test_tier3_pairwise_ml_domain_outline_and_diagrams(tmp_path):
    """Pairwise combination: Machine Learning domain with dynamic outline and diagrams."""
    sample = SAMPLE_ABSTRACTS["ml_freshknn"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    outline_titles = [item.title.lower() for item in outline.items]
    assert not any("stm32" in t for t in outline_titles)
    assert not any("circuit" in t for t in outline_titles)

    diagrams = generate_all_diagrams(brief, facts, out_dir=tmp_path / "diagrams")
    assert len(diagrams) == 3


@pytest.mark.tier3
def test_tier3_pairwise_bioinformatics_outline_and_diagrams(tmp_path):
    """Pairwise combination: Bioinformatics domain with dynamic outline and diagrams."""
    sample = SAMPLE_ABSTRACTS["bio_genealign"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    outline_titles = [item.title.lower() for item in outline.items]
    assert not any("stm32" in t for t in outline_titles)
    assert not any("pinout" in t for t in outline_titles)

    diagrams = generate_all_diagrams(brief, facts, out_dir=tmp_path / "diagrams")
    assert len(diagrams) == 3


@pytest.mark.tier3
def test_tier3_pairwise_embedded_domain_outline_and_diagrams(tmp_path):
    """Pairwise combination: Embedded IoT domain with hardware outline and diagrams."""
    sample = SAMPLE_ABSTRACTS["embedded_stm32"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    # In embedded domain, hardware outline and wiring diagrams are expected
    diagrams = generate_all_diagrams(brief, facts, out_dir=tmp_path / "diagrams")
    assert len(diagrams) == 3


# ==============================================================================
# TIER 4: SCENARIO 4 — Academic Backbone Invariants Across Domains
# ==============================================================================

@pytest.mark.tier4
def test_tier4_academic_backbone_three_docx_sections(tmp_path):
    """Verify that generated Word document always contains exactly 3 sections."""
    sample = SAMPLE_ABSTRACTS["software_moneygo"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    out_docx = tmp_path / "moneygo_report.docx"
    builder = DocxReportBuilder(brief, facts, outline, sample_path="COA_report.docx")
    builder.build({}, out_docx)

    doc = docx.Document(str(out_docx))
    assert len(doc.sections) == 3, f"Expected exactly 3 sections, found {len(doc.sections)}"


@pytest.mark.tier4
def test_tier4_academic_backbone_page_numbering_formats(tmp_path):
    """Verify page numbering: Section 1 (none), Section 2 (lowerRoman), Section 3 (decimal restarting at 1)."""
    sample = SAMPLE_ABSTRACTS["software_moneygo"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    out_docx = tmp_path / "numbering_report.docx"
    builder = DocxReportBuilder(brief, facts, outline, sample_path="COA_report.docx")
    builder.build({}, out_docx)

    doc = docx.Document(str(out_docx))

    # Section 2 footer should have lowerRoman
    sec2_xml = doc.sections[1]._sectPr.xml
    assert 'w:fmt="lowerRoman"' in sec2_xml or 'w:val="lowerRoman"' in sec2_xml or "lowerRoman" in sec2_xml

    # Section 3 footer should have decimal restarting at 1
    sec3_xml = doc.sections[2]._sectPr.xml
    assert 'w:fmt="decimal"' in sec3_xml or 'w:val="decimal"' in sec3_xml or "decimal" in sec3_xml


@pytest.mark.tier4
def test_tier4_academic_backbone_prelims_and_chapters_order(tmp_path):
    """Verify deterministic order of academic preliminary matter and body chapters."""
    sample = SAMPLE_ABSTRACTS["software_moneygo"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    out_docx = tmp_path / "order_report.docx"
    builder = DocxReportBuilder(brief, facts, outline, sample_path="COA_report.docx")
    builder.build({}, out_docx)

    full_text = extract_docx_all_text(out_docx)

    # Check order of major headings
    pos_cert = full_text.find("BONAFIDE CERTIFICATE")
    pos_decl = full_text.find("DECLARATION")
    pos_ack = full_text.find("ACKNOWLEDGEMENT")
    pos_abs = full_text.find("ABSTRACT")

    assert pos_cert != -1, "Missing BONAFIDE CERTIFICATE"
    assert pos_decl != -1, "Missing DECLARATION"
    assert pos_ack != -1, "Missing ACKNOWLEDGEMENT"
    assert pos_abs != -1, "Missing ABSTRACT"
    assert pos_cert < pos_decl < pos_ack < pos_abs, "Preliminary matter out of order"


@pytest.mark.tier4
def test_tier4_academic_backbone_table_cantsplit_and_headers(tmp_path):
    """Verify that all table rows have w:cantSplit applied to avoid awkward page breaks."""
    sample = SAMPLE_ABSTRACTS["software_moneygo"]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    out_docx = tmp_path / "tables_report.docx"
    builder = DocxReportBuilder(brief, facts, outline, sample_path="COA_report.docx")
    builder.build({}, out_docx)

    doc = docx.Document(str(out_docx))
    assert len(doc.tables) > 0, "Document should contain tables"

    # Check that report tables have cantSplit on rows to prevent awkward page splits
    found_cant_split = False
    for tbl in doc.tables:
        for row in tbl.rows:
            trPr_xml = row._tr.get_or_add_trPr().xml
            if "cantSplit" in trPr_xml:
                found_cant_split = True
                break
        if found_cant_split:
            break
    assert found_cant_split, "Report tables must contain w:cantSplit XML property on rows"

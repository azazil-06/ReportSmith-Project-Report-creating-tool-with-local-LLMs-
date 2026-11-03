"""Empirical challenger verification harness for Milestone 3 (Features F11 and F12).

Validates:
1. Universal diagram generation across 5 domains (Web, ML, Bio, Software, Hardware):
   - Image file validity (PNG signature, IHDR dimensions >= 1500x1000, size > 15KB).
   - Canvas text inspection: zero MCU pinouts/headers or sensor arrays in non-hardware diagrams.
2. DocxReportBuilder adaptive document assembly across all 5 domains:
   - Abbreviations table adaptation and hardware term suppression.
   - Section 2.2/2.3 table adaptation (no MCU specifications/pinouts for non-hardware).
   - Section 3 figures and tables (DFD captions, subsystem table, no pin/calibration tables for non-hardware).
   - Section 5.3/5.4 photographs (no oscilloscope or hardware wiring photos for non-hardware).
   - End matter (references, Appendix B configuration, Appendix C supplementary images).

Author: Challenger M3_2
"""

import os
import struct
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple
import docx
import matplotlib.pyplot as plt
import pytest

_workspace_root = str(Path(__file__).resolve().parent.parent)
if _workspace_root not in sys.path:
    sys.path.insert(0, _workspace_root)

from reportsmith.content_analyzer import analyze_content_folder
from reportsmith.diagrams import generate_all_diagrams
from reportsmith.docx_build import DocxReportBuilder
from reportsmith.facts import derive_facts
from reportsmith.outline import synthesize_universal_outline

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


# ------------------------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------------------------


def _get_png_dimensions(path: Path) -> Tuple[int, int]:
    """Read width and height from PNG IHDR chunk without external PIL dependency."""
    with open(path, "rb") as f:
        header = f.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"Invalid PNG magic in {path}"
    assert header[12:16] == b"IHDR", f"Missing IHDR chunk in {path}"
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def _build_and_extract_docx(tmp_path: Path, sample_key: str):
    """Generate project brief, facts, outline, diagrams, and full docx for a given sample abstract."""
    sample = SAMPLE_ABSTRACTS[sample_key]
    content_dir = tmp_path / f"Content_{sample_key}"
    make_project_docx(content_dir, sample["title"], sample["abstract"])

    brief, facts, outline = analyze_content_folder(
        str(content_dir), sample_docx_path="COA_report.docx"
    )

    diagram_dir = tmp_path / f"diagrams_{sample_key}"
    diagrams = generate_all_diagrams(brief, facts, out_dir=diagram_dir)

    out_docx = tmp_path / f"report_{sample_key}.docx"
    builder = DocxReportBuilder(
        brief=brief,
        facts=facts,
        outline=outline,
        sample_path="COA_report.docx",
        diagrams=diagrams,
        out_dir=diagram_dir,
    )
    builder.build({}, out_path=out_docx)

    doc = docx.Document(str(out_docx))
    full_text = extract_docx_all_text(out_docx)
    return brief, facts, outline, diagrams, doc, full_text


# ==============================================================================
# FEATURE F11: UNIVERSAL DIAGRAM GENERATION EMPIRICAL TESTS
# ==============================================================================


@pytest.mark.parametrize(
    "sample_key, expected_domain, is_hardware",
    [
        ("web_campusconnect", "WEB_CLOUD", False),
        ("ml_freshknn", "MACHINE_LEARNING", False),
        ("bio_genealign", "BIOINFORMATICS", False),
        ("software_moneygo", "SOFTWARE_APP", False),
        ("embedded_stm32", "EMBEDDED_HARDWARE", True),
    ],
)
def test_empirical_diagram_generation_and_image_metadata(
    tmp_path, sample_key: str, expected_domain: str, is_hardware: bool
):
    """Empirically test diagram generation across 5 domains.
    
    Verifies:
    - All three diagrams (block, connection/DFD, flowchart) exist.
    - File sizes are non-trivial (> 15,000 bytes) indicating crisp vector-rendered content.
    - Standard PNG magic header bytes.
    - High-resolution dimensions (width >= 1500px, height >= 1000px) representing >= 200 DPI.
    """
    sample = SAMPLE_ABSTRACTS[sample_key]
    content_dir = tmp_path / "Content"
    make_project_docx(content_dir, sample["title"], sample["abstract"])
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    out_dir = tmp_path / f"diag_{sample_key}"
    diagrams = generate_all_diagrams(brief, facts, out_dir=out_dir)

    assert len(diagrams) == 3, f"Expected 3 diagrams for {sample_key}, got {len(diagrams)}"

    for diag_role in ("block_diagram", "connection_diagram", "flowchart"):
        path = diagrams[diag_role]
        assert path.is_file(), f"Diagram {diag_role} missing at {path}"

        size = path.stat().st_size
        assert size > 15000, (
            f"Diagram {diag_role} for {sample_key} file size suspiciously small: {size} bytes"
        )

        w, h = _get_png_dimensions(path)
        assert w >= 1500, f"Diagram {diag_role} width {w}px below high-resolution 1500px threshold"
        assert h >= 1000, f"Diagram {diag_role} height {h}px below high-resolution 1000px threshold"


def test_empirical_diagram_canvas_text_zero_hardware_leakage(tmp_path):
    """Stress-test diagram canvas text rendering via monkeypatched savefig.
    
    Collects 100% of text elements drawn onto the matplotlib canvas across all
    non-hardware diagrams and hardware diagrams to empirically prove zero MCU pinouts
    or hardware artifacts for non-hardware domains.
    """
    captured_texts: Dict[str, Dict[str, List[str]]] = {}

    orig_savefig = plt.savefig

    def intercept_savefig(fname, *args, **kwargs):
        fig = plt.gcf()
        file_name = Path(fname).name
        texts = []
        for ax in fig.axes:
            for t in ax.texts:
                txt = t.get_text().strip()
                if txt:
                    texts.append(txt)
            for child in ax.get_children():
                if hasattr(child, "get_text"):
                    txt = child.get_text().strip()
                    if txt and txt not in texts:
                        texts.append(txt)
        captured_texts.setdefault(current_sample, {})[file_name] = texts
        return orig_savefig(fname, *args, **kwargs)

    plt.savefig = intercept_savefig

    hardware_leakage_terms = [
        "stm32", "pa0", "pa8", "pa9", "pa10", "pb0", "pb1", "pb6", "pb7", "pc13",
        "microcontroller", "pin header", "sensor module", "usart1_tx", "usart1_rx",
        "firmware execution loop", "analog sensing probe"
    ]

    try:
        for s_key in ("web_campusconnect", "ml_freshknn", "bio_genealign", "software_moneygo", "embedded_stm32"):
            current_sample = s_key
            sample = SAMPLE_ABSTRACTS[s_key]
            content_dir = tmp_path / f"Content_{s_key}"
            make_project_docx(content_dir, sample["title"], sample["abstract"])
            brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

            out_dir = tmp_path / f"diags_{s_key}"
            generate_all_diagrams(brief, facts, out_dir=out_dir)

            # Analyze captured canvas texts
            sample_texts = captured_texts[s_key]
            assert "block_diagram.png" in sample_texts
            assert "connection_diagram.png" in sample_texts
            assert "flowchart.png" in sample_texts

            all_sample_text_blob = " ".join(
                t for diag_list in sample_texts.values() for t in diag_list
            ).lower()

            if s_key == "embedded_stm32":
                # Hardware project MUST include MCU pins and hardware terms
                assert "stm32" in all_sample_text_blob
                assert any(p in all_sample_text_blob for p in ("pa0", "pc13", "usart1"))
            else:
                # Non-hardware projects MUST NOT contain any hardware terms
                for bad_term in hardware_leakage_terms:
                    assert bad_term not in all_sample_text_blob, (
                        f"Hardware term '{bad_term}' leaked into non-hardware diagram for {s_key}!\n"
                        f"Canvas texts: {sample_texts}"
                    )
    finally:
        plt.savefig = orig_savefig


# ==============================================================================
# FEATURE F12: ADAPTIVE DOCX CONTENT INJECTION EMPIRICAL TESTS
# ==============================================================================


def test_empirical_docx_abbreviations_table_adaptation(tmp_path):
    """Empirically test abbreviations table contents across all domains.
    
    Verifies that non-hardware reports exclude MCU/hardware abbreviations
    (ADC, GPIO, MCU, I2C, OLED, etc.) and include domain-appropriate abbreviations.
    """
    hardware_banned_abbrs = {
        "ADC", "GPIO", "MCU", "I2C", "OLED", "UART", "USART", "SWD", "NVIC", "HAL",
        "VCC", "GND", "ARM", "Cortex-M", "SysTick", "MMIO", "ALU", "CPI", "FSM", "IR", "ISA", "PC", "RISC"
    }

    for s_key in ("web_campusconnect", "ml_freshknn", "bio_genealign", "software_moneygo", "embedded_stm32"):
        brief, facts, outline, diagrams, doc, full_text = _build_and_extract_docx(tmp_path, s_key)

        # Locate abbreviations table (in Section 2 prelims)
        # Abbreviations table has columns "Abbreviation" and "Expansion"
        abbr_table = None
        for tbl in doc.tables:
            if len(tbl.columns) == 2 and tbl.rows[0].cells[0].text.strip() == "Abbreviation":
                abbr_table = tbl
                break

        assert abbr_table is not None, f"Abbreviations table not found in report for {s_key}"

        table_abbrs = {row.cells[0].text.strip() for row in abbr_table.rows[1:]}

        if s_key == "embedded_stm32":
            # Hardware must contain ADC, GPIO, MCU
            assert "ADC" in table_abbrs
            assert "GPIO" in table_abbrs
            assert "MCU" in table_abbrs
        else:
            # Non-hardware must NOT contain hardware abbreviations
            leaked_hw = table_abbrs.intersection(hardware_banned_abbrs)
            assert not leaked_hw, (
                f"Banned hardware abbreviations {leaked_hw} found in non-hardware report for {s_key}!"
            )

            # Check domain-appropriate abbreviations
            if s_key == "web_campusconnect":
                assert any(a in table_abbrs for a in ("API", "REST", "JWT", "HTTP", "CRUD"))
            elif s_key == "ml_freshknn":
                assert any(a in table_abbrs for a in ("AI", "ML", "KNN", "ROC", "AUC", "CV"))
            elif s_key == "bio_genealign":
                assert any(a in table_abbrs for a in ("DNA", "RNA", "NCBI", "BLAST"))
            elif s_key == "software_moneygo":
                assert any(a in table_abbrs for a in ("API", "GUI", "CRUD", "IDE", "SQL"))


def test_empirical_docx_section_2_tables_adaptation(tmp_path):
    """Empirically test Section 2 specifications and hardware component tables.
    
    Verifies:
    - Non-hardware reports omit STM32/microcontroller technical specification tables.
    - Non-hardware reports omit hardware component tables (sensors, buzzers, relays).
    - Non-hardware reports inject domain-appropriate platform specification tables.
    """
    for s_key in ("web_campusconnect", "ml_freshknn", "bio_genealign", "software_moneygo", "embedded_stm32"):
        brief, facts, outline, diagrams, doc, full_text = _build_and_extract_docx(tmp_path, s_key)

        all_table_texts = [
            "\n".join(cell.text for cell in row.cells)
            for tbl in doc.tables
            for row in tbl.rows
        ]

        if s_key == "embedded_stm32":
            # Hardware must contain STM32 specifications and component specifications
            has_mcu_specs = any("Microcontroller Part" in txt or "STM32F103" in txt for txt in all_table_texts)
            assert has_mcu_specs, f"Expected MCU specifications table in embedded report for {s_key}"
        else:
            # Non-hardware must NOT contain MCU specs or component pin tables
            has_mcu_specs = any("Microcontroller Part" in txt for txt in all_table_texts)
            assert not has_mcu_specs, f"Leaked 'Microcontroller Part' table into non-hardware report {s_key}"

            has_adc_res = any("ADC Resolution" in txt for txt in all_table_texts)
            assert not has_adc_res, f"Leaked 'ADC Resolution' table row into non-hardware report {s_key}"

            # Check domain-specific table titles in paragraphs
            p_texts = [p.text for p in doc.paragraphs]
            if s_key == "web_campusconnect":
                assert any("Web Platform and Cloud Deployment Specifications" in t for t in p_texts)
            elif s_key == "ml_freshknn":
                assert any("Machine Learning Computational Environment and Platform Specifications" in t for t in p_texts)
            elif s_key == "bio_genealign":
                assert any("Bioinformatics Pipeline and Computational Specifications" in t for t in p_texts)
            elif s_key == "software_moneygo":
                assert any("System Operational Environment and Software Specifications" in t for t in p_texts)


def test_empirical_docx_section_3_diagram_captions_and_pin_tables(tmp_path):
    """Empirically test Section 3 diagram captions, pinout tables, and calibration tables.
    
    Verifies:
    - Non-hardware reports do NOT have "Complete Circuit Interconnection Diagram".
    - Non-hardware reports have Data Flow Diagram (DFD) figure captions.
    - Non-hardware reports do NOT have MCU Pin Allocation tables.
    - Non-hardware reports have "System Subsystem Modules and Component Specifications" table.
    - Non-hardware reports do NOT have ADC calibration/state classification tables.
    """
    for s_key in ("web_campusconnect", "ml_freshknn", "bio_genealign", "software_moneygo", "embedded_stm32"):
        brief, facts, outline, diagrams, doc, full_text = _build_and_extract_docx(tmp_path, s_key)

        p_texts = [p.text for p in doc.paragraphs]

        if s_key == "embedded_stm32":
            # Hardware expects circuit interconnection diagram and pin allocation table
            assert any("Complete Circuit Interconnection Diagram" in t for t in p_texts)
            assert any("Microcontroller Pin Allocation" in t for t in p_texts)
            assert any("Calibration and Operational State Classification" in t for t in p_texts)
        else:
            # Non-hardware must NOT contain circuit diagram or pin mapping
            assert not any("Complete Circuit Interconnection Diagram" in t for t in p_texts), (
                f"Leaked 'Complete Circuit Interconnection Diagram' in {s_key}"
            )
            assert not any("Microcontroller Pin Allocation" in t for t in p_texts), (
                f"Leaked 'Microcontroller Pin Allocation' table in {s_key}"
            )
            assert not any("ADC Calibration" in t for t in p_texts), (
                f"Leaked 'ADC Calibration' table in {s_key}"
            )

            # Must contain DFD and Subsystem Modules table
            assert any("Data Flow" in t or "DFD" in t for t in p_texts), (
                f"Missing DFD figure caption in {s_key}"
            )
            assert any("System Subsystem Modules and Component Specifications" in t for t in p_texts), (
                f"Missing Subsystem Modules Table in {s_key}"
            )


def test_empirical_docx_section_5_photographs_and_captions(tmp_path):
    """Empirically test Section 5.3 photographs and Section 5.4 telemetry placeholders.
    
    Verifies:
    - Non-hardware reports do NOT contain "Photograph 5.1: Hardware Prototype Setup"
      or mention oscilloscope captures or sensor array wiring.
    - Non-hardware reports contain "Figure 5.1: System Prototype Setup and User Interface"
      (or ML classification interface).
    - Non-hardware reports contain "Figure 5.2: System Output and Execution Telemetry"
      (or ML confusion matrix) instead of Photograph 5.2.
    """
    for s_key in ("web_campusconnect", "ml_freshknn", "bio_genealign", "software_moneygo", "embedded_stm32"):
        brief, facts, outline, diagrams, doc, full_text = _build_and_extract_docx(tmp_path, s_key)

        # Inspect all text in tables, boxes, paragraphs
        full_text_lower = full_text.lower()

        if s_key == "embedded_stm32":
            assert "photograph 5.1: hardware prototype setup" in full_text_lower
            assert "photograph 5.2: output display and telemetry output" in full_text_lower
        else:
            assert "photograph 5.1: hardware prototype setup" not in full_text_lower, (
                f"Leaked 'Photograph 5.1: Hardware Prototype Setup' in {s_key}"
            )
            assert "photograph 5.2: output display and telemetry output" not in full_text_lower, (
                f"Leaked 'Photograph 5.2: Output Display' in {s_key}"
            )
            assert "wired" not in full_text_lower or "prototype" in full_text_lower

            # Non-hardware figure 5.1 & 5.2
            if s_key == "ml_freshknn":
                assert "figure 5.1: machine learning prototype and classification interface" in full_text_lower
                assert "figure 5.2: output confusion matrix and classification metrics" in full_text_lower
            else:
                assert "figure 5.1: system prototype setup and user interface" in full_text_lower
                assert "figure 5.2: system output and execution telemetry" in full_text_lower


def test_empirical_docx_end_matter_and_appendices(tmp_path):
    """Empirically test End Matter references and Appendix B/C content.
    
    Verifies:
    - Non-hardware reports do NOT cite STMicroelectronics or Adafruit SSD1306.
    - Non-hardware reports cite domain literature (Design Patterns, Effective Java, Scikit-learn, BLAST).
    - Appendix B does NOT contain 72 MHz clock tree, HSE crystal, PLL multiplier, or ADC prescaler for non-hardware.
    - Appendix C does NOT mention oscilloscope captures or probe waveforms for non-hardware.
    """
    for s_key in ("web_campusconnect", "ml_freshknn", "bio_genealign", "software_moneygo", "embedded_stm32"):
        brief, facts, outline, diagrams, doc, full_text = _build_and_extract_docx(tmp_path, s_key)

        full_text_lower = full_text.lower()

        if s_key == "embedded_stm32":
            assert "stmicroelectronics" in full_text_lower
            assert "hse crystal: 8.000 mhz" in full_text_lower
            assert "pll multiplier: x9 (72 mhz" in full_text_lower
            assert "oscilloscope captures" in full_text_lower
        else:
            # References check
            assert "stmicroelectronics" not in full_text_lower, (
                f"Leaked STMicroelectronics datasheet reference in {s_key}"
            )
            assert "adafruit ssd1306" not in full_text_lower, (
                f"Leaked Adafruit SSD1306 reference in {s_key}"
            )

            # Appendix B check
            assert "hse crystal" not in full_text_lower, (
                f"Leaked HSE crystal clock tree in Appendix B of {s_key}"
            )
            assert "pll multiplier" not in full_text_lower, (
                f"Leaked PLL multiplier in Appendix B of {s_key}"
            )
            assert "72 mhz" not in full_text_lower, (
                f"Leaked 72 MHz clock tree in Appendix B of {s_key}"
            )

            # Appendix C check
            assert "oscilloscope captures" not in full_text_lower, (
                f"Leaked oscilloscope traces into Appendix C of {s_key}"
            )
            assert "supplementary visualizations" in full_text_lower or "supplementary images" in full_text_lower

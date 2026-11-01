"""Test suite for Content folder analysis and COA_report.docx styling pipeline."""

from pathlib import Path
import docx
import pytest

from reportsmith.check import validate_report
from reportsmith.config import load_config
from reportsmith.content_analyzer import analyze_content_folder
from reportsmith.docx_build import DocxReportBuilder
from reportsmith.generate import SectionGenerator
from reportsmith.llm import StubLLM


def test_content_folder_analysis():
    brief, facts, outline = analyze_content_folder("Content", "COA_report.docx")

    t_low = brief.project.title.lower()
    assert any(k in t_low for k in ("water", "plant", "freshknn", "knn", "parkease", "parking", "moneygo", "resumate"))
    assert brief.domain in ("MACHINE_LEARNING", "SOFTWARE_APP", "EMBEDDED_HARDWARE", "WEB_CLOUD", "DATA_ANALYTICS")
    assert len(brief.people.team) >= 1
    assert brief.people.hod.name == "Dr. S Suresh Babu"
    assert brief.people.coordinator is None
    assert len(outline.items) >= 20


def test_end_to_end_content_build_and_validation(tmp_path):
    brief, facts, outline = analyze_content_folder("Content", "COA_report.docx")
    config = load_config()

    # Generate sections using deterministic stub LLM
    generator = SectionGenerator(StubLLM(), config=config, cache_dir=tmp_path / "cache")
    gen_data = generator.generate_all(outline, brief, facts)

    out_docx = tmp_path / "test_report.docx"
    builder = DocxReportBuilder(brief, facts, outline, sample_path="COA_report.docx")
    builder.build(gen_data, out_docx)

    assert out_docx.is_file()
    assert out_docx.stat().st_size > 50000  # At least 50 KB

    # Inspect generated docx
    doc = docx.Document(str(out_docx))
    assert len(doc.sections) == 3

    # Check Title Page
    title_p = doc.paragraphs[0]
    assert len(title_p.text) > 0
    assert title_p.runs[0].font.size.pt == 26.0

    # Check Team Table (Table 0)
    team_tbl = doc.tables[0]
    assert len(team_tbl.rows) == len(brief.people.team)
    assert len(team_tbl.rows[0].cells[0].text.strip()) > 0

    # Check Certificate
    cert_headings = [p.text for p in doc.paragraphs if "BONAFIDE CERTIFICATE" in p.text]
    assert len(cert_headings) >= 1

    # Check Contents Banner Table
    banner_tables = [t for t in doc.tables if len(t.rows) == 1 and len(t.columns) == 1 and "CONTENTS" in t.rows[0].cells[0].text]
    assert len(banner_tables) >= 1

    # Validate with grounding checker
    check_report = tmp_path / "check.txt"
    is_valid = validate_report(out_docx, brief, facts, out_report_path=check_report)
    assert is_valid is True
    assert "Status: PASSED" in check_report.read_text(encoding="utf-8")


def test_universal_outline_synthesis_without_rules_file(tmp_path):
    """Verify that when no rules file is provided in Content/, the engine analyzes the abstract,
    detects the domain, invents appropriate subheadings, and enforces the exact mandatory backbone order.
    """
    import shutil
    content_dir = tmp_path / "Content"
    content_dir.mkdir(parents=True)

    # Place Java project abstract (named Project.pdf) without any rules file
    shutil.copy("training/java project abstract.pdf", content_dir / "Project.pdf")

    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    # 1. Subject & Domain Identification
    assert brief.domain == "SOFTWARE_APP"
    assert "Moneygo" in brief.project.title or "MONEYGO" in brief.project.title.upper()
    assert len(brief.people.team) == 6
    names = [m.name for m in brief.people.team]
    assert any("Kashinath" in n for n in names)
    assert any("Kannan" in n for n in names)
    assert any("Jeevan" in n for n in names)
    assert any("Arjun" in n for n in names)
    assert any("Devananda" in n for n in names)
    assert any("Neehara" in n for n in names)

    assert "Computer Science" in brief.project.department
    assert brief.project.course_name == "Software Engineering & Application Development"

    # 2. Strict Structural Invariant Check across all subjects
    # Front page, Bonafide certificate, Declaration, Acknowledgement, Abstract, Content (TOC),
    # List of figures, List of tables, List of abbreviations, Introduction, Requirements,
    # System design, Implementation, Testing and results, Conclusion, References
    mandatory_sequence = [
        "title_page",
        "certificate",
        "declaration",
        "acknowledgement",
        "abstract",
        "table_of_contents",
        "list_of_figures",
        "list_of_tables",
        "list_of_abbreviations",
        "ch_1",
        "ch_2",
        "ch_3",
        "ch_4",
        "ch_5",
        "ch_6",
        "references",
    ]

    outline_ids = [item.id for item in outline.items]
    for mandatory_id in mandatory_sequence:
        assert mandatory_id in outline_ids, f"Mandatory section '{mandatory_id}' missing from outline!"

    # Verify strictly monotonic ordering
    indices = [outline_ids.index(mid) for mid in mandatory_sequence]
    assert indices == sorted(indices), "Mandatory sections do not preserve strictly required order!"

    # 3. Dynamic Subheading Invention from Abstract
    ch4_titles = [item.title for item in outline.items if item.chapter == "4"]
    assert any("Customer Terminal" in t for t in ch4_titles), "Customer Terminal module not invented in Chapter 4!"
    assert any("Banking Interface" in t for t in ch4_titles), "Banking Interface module not invented in Chapter 4!"
    assert any("Data Persistence" in t for t in ch4_titles), "Data Persistence handling not invented in Chapter 4!"

    # 4. Absence of ungrounded microcontroller pins
    assert len(facts.pin_table) == 0, "Non-microcontroller software project should not have MCU pins!"


def test_universal_software_report_build(tmp_path):
    """Verify that a non-microcontroller software project builds into publication-grade .docx
    with zero hallucinated pins, authentic software tables, diagrams, and domain references.
    """
    import shutil
    content_dir = tmp_path / "Content"
    content_dir.mkdir(parents=True)
    shutil.copy("training/java project abstract.pdf", content_dir / "Project.pdf")

    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    config = load_config()

    generator = SectionGenerator(StubLLM(), config=config, cache_dir=tmp_path / "cache")
    gen_data = generator.generate_all(outline, brief, facts)

    out_docx = tmp_path / "moneygo_report.docx"
    builder = DocxReportBuilder(brief, facts, outline, sample_path="COA_report.docx")
    builder.build(gen_data, out_docx)

    assert out_docx.is_file()
    assert out_docx.stat().st_size > 40000

    doc = docx.Document(str(out_docx))
    assert len(doc.sections) == 3

    # Check Title Page
    title_p = doc.paragraphs[0]
    assert "MONEYGO" in title_p.text.upper()
    assert title_p.runs[0].font.size.pt == 26.0

    # Check Team Table (Table 0) has 6 rows
    team_tbl = doc.tables[0]
    assert len(team_tbl.rows) == 6

    # Check Bonafide Certificate
    cert_headings = [p.text for p in doc.paragraphs if "BONAFIDE CERTIFICATE" in p.text]
    assert len(cert_headings) >= 1

    # Check Declaration contains project title and department without plant monitor text
    dec_paras = [p.text for p in doc.paragraphs if "hereby declare" in p.text.lower()]
    assert len(dec_paras) >= 1
    assert "plant" not in dec_paras[0].lower()
    assert "moneygo" in dec_paras[0].lower()

    # Check List of Abbreviations has software terms and no ADC
    abbr_cells = [c.text for t in doc.tables for r in t.rows for c in r.cells]
    assert any("API" in c or "GUI" in c or "JVM" in c or "UI" in c for c in abbr_cells)
    assert not any(c.strip() == "ADC" for c in abbr_cells)

    # Check Chapter 2 Software Specifications table
    table_texts = [" ".join(c.text for c in r.cells) for t in doc.tables for r in t.rows]
    assert any("Target Platform" in t or "Runtime Framework" in t or "Execution Environment" in t for t in table_texts)

    # Check Chapter 5 Software Test Cases table
    assert any("TC_01" in t for t in table_texts)
    assert any("Standard system input parameters" in t for t in table_texts)

    # Check References contains Java / Software engineering literature
    ref_paras = [p.text for p in doc.paragraphs if "[" in p.text and "]" in p.text and ("Java" in p.text or "Code" in p.text or "Design Patterns" in p.text)]
    assert len(ref_paras) >= 1


def test_universal_report_with_source_code(tmp_path):
    """Verify that when a source code file is included in Content/, Appendix A is generated
    with the correct domain heading and verbatim code content.
    """
    import shutil
    content_dir = tmp_path / "Content"
    content_dir.mkdir(parents=True)
    shutil.copy("training/java project abstract.pdf", content_dir / "Project.pdf")

    # Add Java source code file
    java_file = content_dir / "MoneyGo.java"
    java_file.write_text(
        "package com.moneygo;\n\n"
        "public class MoneyGo {\n"
        "    public static void main(String[] args) {\n"
        "        System.out.println(\"Welcome to MoneyGo Banking System\");\n"
        "    }\n"
        "}\n",
        encoding="utf-8"
    )

    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    assert outline.items[-2].id == "appendix_a_source_code"
    assert "Complete Application Source Code" in outline.items[-2].title

    config = load_config()
    generator = SectionGenerator(StubLLM(), config=config, cache_dir=tmp_path / "cache")
    gen_data = generator.generate_all(outline, brief, facts)

    out_docx = tmp_path / "moneygo_with_code.docx"
    builder = DocxReportBuilder(brief, facts, outline, sample_path="COA_report.docx")
    builder.build(gen_data, out_docx)

    doc = docx.Document(str(out_docx))
    code_boxes = [c.text for t in doc.tables for r in t.rows for c in r.cells if "public class MoneyGo" in c.text]
    assert len(code_boxes) >= 1


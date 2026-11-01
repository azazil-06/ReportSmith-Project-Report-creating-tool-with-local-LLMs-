"""Unit tests for Word (.docx) Post-Processing & Error Correction Engine."""

from pathlib import Path
import docx
import pytest

from reportsmith.brief import ProjectBrief, load_brief
from reportsmith.docx_postprocess import (
    DocxSectionItem,
    SectionAuditResult,
    apply_section_correction,
    extract_docx_sections,
    run_docx_postprocessing,
)
from reportsmith.facts import derive_facts
from reportsmith.llm import StubLLM


@pytest.fixture
def sample_docx(tmp_path: Path) -> Path:
    """Create a minimal mock .docx file with structured sections."""
    doc = docx.Document()
    doc.add_heading("DECLARATION", level=1)
    doc.add_paragraph("We hereby declare that this report is authentic.")
    doc.add_heading("ACKNOWLEDGEMENT", level=1)
    doc.add_paragraph("We thank our faculty guide and HOD for guidance.")
    doc.add_paragraph("1.1 Project Overview")
    doc.add_paragraph("The project is a machine learning fruit classifier.")
    doc.add_paragraph("It uses KNN algorithm to evaluate produce freshness.")
    doc.add_paragraph("1.2 Objectives")
    doc.add_paragraph("To build an accurate image classification pipeline.")
    
    out_file = tmp_path / "test_report.docx"
    doc.save(str(out_file))
    return out_file


def test_extract_docx_sections(sample_docx: Path):
    doc = docx.Document(str(sample_docx))
    sections = extract_docx_sections(doc)
    assert len(sections) >= 3
    sec_ids = [s.section_id for s in sections]
    assert "declaration" in sec_ids
    assert "acknowledgement" in sec_ids
    assert "1.1" in sec_ids
    assert "1.2" in sec_ids

    sec_1_1 = next(s for s in sections if s.section_id == "1.1")
    assert sec_1_1.title == "Project Overview"
    assert len(sec_1_1.paragraphs) == 2
    assert "machine learning" in sec_1_1.paragraphs[0]


def test_apply_section_correction(sample_docx: Path):
    doc = docx.Document(str(sample_docx))
    sections = extract_docx_sections(doc)
    sec_1_1 = next(s for s in sections if s.section_id == "1.1")

    res = SectionAuditResult(
        section_id="1.1",
        title="Project Overview",
        confidence_score=75,
        domain_score=80,
        verdict="CORRECTED",
        critique="Polished prose for publication standard.",
        corrected_paragraphs=[
            "The FreshKNN framework provides automated visual quality grading.",
            "It deploys an instance-based nearest neighbor voting mechanism.",
        ],
        original_paragraphs=sec_1_1.paragraphs,
    )

    apply_section_correction(doc, sec_1_1, res)

    # Check updated text in document
    p0 = doc.paragraphs[sec_1_1.paragraph_indices[0]].text
    p1 = doc.paragraphs[sec_1_1.paragraph_indices[1]].text
    assert "FreshKNN framework" in p0
    assert "nearest neighbor voting" in p1


def test_run_docx_postprocessing_end_to_end(sample_docx: Path, tmp_path: Path):
    brief_file = Path("out/analyzed_brief.yaml")
    if not brief_file.is_file():
        brief_file = Path("examples/brief.yaml")
    brief = load_brief(brief_file)
    facts = derive_facts(brief, base_dir=brief_file.parent)

    stub_llm = StubLLM(seed=42)
    out_refined = tmp_path / "refined_output.docx"

    report = run_docx_postprocessing(
        docx_path=sample_docx,
        brief=brief,
        facts=facts,
        llm=stub_llm,
        out_path=out_refined,
    )

    assert report.total_sections >= 3
    assert report.average_confidence > 0
    assert out_refined.is_file()


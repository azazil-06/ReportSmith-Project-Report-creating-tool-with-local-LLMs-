"""Unit tests for outline parsing from format files."""

from pathlib import Path
import pytest

from reportsmith.outline import parse_format_file, parse_format_text, ReportOutline


def test_parse_example_format_file():
    format_path = Path("examples/format.txt")
    assert format_path.is_file()

    outline = parse_format_file(format_path)
    assert isinstance(outline, ReportOutline)
    assert len(outline.items) > 30

    # Check preliminary pages
    prelims = [i for i in outline.items if i.kind == "prelim"]
    assert len(prelims) >= 8
    prelim_ids = [p.id for p in prelims]
    assert "title_page" in prelim_ids
    assert "certificate" in prelim_ids
    assert "abstract" in prelim_ids
    assert "table_of_contents" in prelim_ids

    # Check chapters
    chapters = [i for i in outline.items if i.kind == "chapter"]
    assert len(chapters) == 6
    ch_titles = [c.title for c in chapters]
    assert "Introduction" in ch_titles
    assert "Hardware and Software Requirements" in ch_titles
    assert "Conclusion and Future Scope" in ch_titles

    # Check sections
    sections = [i for i in outline.items if i.kind == "section"]
    sec_ids = [s.id for s in sections]
    assert "1.1" in sec_ids
    assert "2.1" in sec_ids
    assert "3.7" in sec_ids
    assert "4.4" in sec_ids
    assert "5.5" in sec_ids
    assert "6.4" in sec_ids

    # Check end matter
    end_matter = [i for i in outline.items if i.kind == "end_matter"]
    assert len(end_matter) >= 4
    end_titles = [e.title for e in end_matter]
    assert any("References" in t for t in end_titles)
    assert any("Appendix A" in t for t in end_titles)


def test_optional_flag_detection():
    sample_format = """
    Preliminary pages:
    - Declaration (if required)
    - List of Abbreviations (if applicable)

    Chapter 1 Test
    1.1 Mandatory Section
    1.2 Optional Sensor Details (if applicable)

    End matter:
    - Appendix B Circuit Details (if applicable)
    """
    outline = parse_format_text(sample_format)
    items_by_title = {i.title: i for i in outline.items}

    assert items_by_title["Declaration"].optional is True
    assert items_by_title["List of Abbreviations"].optional is True
    assert items_by_title["Mandatory Section"].optional is False
    assert items_by_title["Optional Sensor Details"].optional is True
    assert items_by_title["Appendix B Circuit Details"].optional is True

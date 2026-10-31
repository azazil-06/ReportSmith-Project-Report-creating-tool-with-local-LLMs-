"""Document generator: builds Word (.docx) report using sample.docx template or standard college style."""

import copy
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import docx
from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches, Pt, RGBColor

from reportsmith.brief import ProjectBrief
from reportsmith.facts import ProjectFacts
from reportsmith.outline import ReportOutline

logger = logging.getLogger(__name__)


def sanitize_paragraph_prose(text: str) -> List[str]:
    """Defensively clean any leaked code fences, JSON delimiters, or raw arrays before Word insertion."""
    if not text:
        return []

    text_s = text.strip()
    # Check if string contains raw JSON syntax or markdown fences
    if text_s.startswith("```") or text_s.startswith("{") or '"paragraphs":' in text_s or '"bullets":' in text_s:
        # 1. Try to parse as JSON
        clean_cand = re.sub(r"^```(?:json)?\s*", "", text_s)
        clean_cand = re.sub(r"\s*```$", "", clean_cand).strip()
        try:
            parsed = json.loads(clean_cand)
            extracted: List[str] = []
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if k.lower() in ("title", "section", "status", "id"):
                        continue
                    if isinstance(v, list):
                        extracted.extend([str(x).strip() for x in v if str(x).strip()])
                    elif isinstance(v, str) and v.strip():
                        extracted.append(v.strip())
            elif isinstance(parsed, list):
                extracted.extend([str(x).strip() for x in parsed if str(x).strip()])
            if extracted:
                return extracted
        except Exception:
            pass

        # 2. Extract string literals from array or object
        items = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', clean_cand)
        valid_items = [
            it.replace('\\"', '"').replace('\\n', ' ').strip()
            for it in items
            if len(it.strip()) > 15 and it.lower() not in ("paragraphs", "bullets", "title", "section", "content")
        ]
        if valid_items:
            return valid_items

        # 3. Line-by-line fallback cleaning
        clean_lines: List[str] = []
        for line in clean_cand.splitlines():
            line_str = line.strip()
            if not line_str or line_str in ("{", "}", "[", "]") or line_str.startswith("```"):
                continue
            if re.match(r'^"(?:paragraphs|bullets|title|section)"\s*:\s*', line_str):
                continue
            line_str = re.sub(r'^["\']|["\'],?$', '', line_str).strip()
            if len(line_str) > 10 and not line_str.startswith("{"):
                clean_lines.append(line_str)
        if clean_lines:
            return clean_lines

    return [text]


def add_markdown_formatted_runs(
    paragraph,
    text: str,
    font_name: Optional[str] = "Times New Roman",
    font_size: Optional[Pt] = Pt(12),
):
    """Add formatted runs to a Word paragraph, parsing inline markdown (bold **, italic *, code `)
    into native Word runs and stripping stray markdown syntax and asterisk artifacts.
    Guarantees zero literal asterisks or backticks remain in academic report prose.
    """
    if not text:
        return

    # Normalize newlines and clean multi-line sub-bullets
    lines = text.splitlines()
    for line_idx, line in enumerate(lines):
        if line_idx > 0:
            paragraph.add_run("\n")

        s_line = line.strip()
        indent_prefix = ""
        # Detect and clean sub-bullet prefixes
        if s_line.startswith(("* ", "- ", "+ ")):
            indent_prefix = "   •  "
            s_line = s_line[2:].strip()
        elif s_line.startswith("• "):
            indent_prefix = "   •  "
            s_line = s_line[2:].strip()
        elif re.match(r'^\*\s+', s_line):
            indent_prefix = "   •  "
            s_line = re.sub(r'^\*\s+', '', s_line).strip()

        if indent_prefix:
            run_indent = paragraph.add_run(indent_prefix)
            if font_name:
                run_indent.font.name = font_name
            if font_size:
                run_indent.font.size = font_size

        # Tokenize markdown: **bold**, `code`, *italic*
        tokens = re.split(r'(\*\*.*?\*\*|`.*?`|\*(?!\s).*?(?<!\s)\*)', s_line)
        for token in tokens:
            if not token:
                continue
            if token.startswith("**") and token.endswith("**") and len(token) >= 4:
                inner = token[2:-2]
                r = paragraph.add_run(inner)
                r.bold = True
            elif token.startswith("`") and token.endswith("`") and len(token) >= 2:
                inner = token[1:-1]
                r = paragraph.add_run(inner)
                r.font.name = "Consolas"
                r.font.size = Pt(9.5) if font_size else Pt(9.5)
            elif token.startswith("*") and token.endswith("*") and len(token) >= 2:
                inner = token[1:-1]
                r = paragraph.add_run(inner)
                r.italic = True
            else:
                # Strip any stray markdown asterisks like *word or **
                clean_tok = token.replace("**", "")
                clean_tok = re.sub(r'^\*\s*', '', clean_tok)
                clean_tok = re.sub(r'\s*\*\s*$', '', clean_tok)
                r = paragraph.add_run(clean_tok)

            if font_name and r.font.name != "Consolas":
                r.font.name = font_name
            if font_size and r.font.name != "Consolas":
                r.font.size = font_size


# Standard embedded systems acronym dictionary for abbreviations table
KNOWN_ABBREVIATIONS = {
    "ADC": "Analog-to-Digital Converter",
    "ARM": "Advanced RISC Machines",
    "Cortex-M": "ARM Cortex Microcontroller Core",
    "DMA": "Direct Memory Access",
    "Flash": "Electrically Erasable Programmable Flash Memory",
    "GPIO": "General Purpose Input/Output",
    "HAL": "Hardware Abstraction Layer",
    "HSE": "High-Speed External Clock Oscillator",
    "I2C": "Inter-Integrated Circuit Bus",
    "IDE": "Integrated Development Environment",
    "LED": "Light Emitting Diode",
    "MCU": "Microcontroller Unit",
    "NVIC": "Nested Vectored Interrupt Controller",
    "OLED": "Organic Light Emitting Diode",
    "PCB": "Printed Circuit Board",
    "PLL": "Phase-Locked Loop",
    "PWM": "Pulse Width Modulation",
    "RAM": "Random Access Memory",
    "RISC": "Reduced Instruction Set Computer",
    "RTC": "Real-Time Clock",
    "SDA": "Serial Data Line (I2C)",
    "SCL": "Serial Clock Line (I2C)",
    "SPI": "Serial Peripheral Interface",
    "SRAM": "Static Random Access Memory",
    "SWD": "Serial Wire Debug",
    "UART": "Universal Asynchronous Receiver-Transmitter",
    "USART": "Universal Synchronous/Asynchronous Receiver-Transmitter",
    "USB": "Universal Serial Bus",
    "VCC": "Voltage Common Collector (Power Supply)",
    "GND": "Ground Reference",
}


def set_cell_margins(cell, top=100, bottom=100, left=150, right=150):
    """Set inner padding for table cell in dxa (1 pt = 20 dxa)."""
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement("w:tcMar")
    for margin_name, val in [
        ("top", top),
        ("bottom", bottom),
        ("left", left),
        ("right", right),
    ]:
        node = OxmlElement(f"w:{margin_name}")
        node.set(qn("w:w"), str(val))
        node.set(qn("w:type"), "dxa")
        tcMar.append(node)
    tcPr.append(tcMar)


def set_cell_shading(cell, color_hex: str):
    """Set background color for table cell."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color_hex}"/>')
    tcPr.append(shd)


def prevent_row_split(table):
    """Ensure rows do not split across pages and header repeats."""
    for i, row in enumerate(table.rows):
        trPr = row._tr.get_or_add_trPr()
        trPr.append(OxmlElement("w:cantSplit"))
        if i == 0:
            trPr.append(OxmlElement("w:tblHeader"))


def add_page_number_to_footer(footer, alignment=WD_ALIGN_PARAGRAPH.CENTER):
    """Add a native Word page number field to a footer paragraph."""
    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.alignment = alignment
    p.text = ""
    run = p.add_run()
    fldChar1 = parse_xml(r'<w:fldChar %s w:fldCharType="begin"/>' % nsdecls("w"))
    instrText = parse_xml(
        r'<w:instrText %s xml:space="preserve"> PAGE </w:instrText>' % nsdecls("w")
    )
    fldChar2 = parse_xml(r'<w:fldChar %s w:fldCharType="separate"/>' % nsdecls("w"))
    fldChar3 = parse_xml(r'<w:fldChar %s w:fldCharType="end"/>' % nsdecls("w"))
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)
    run._r.append(fldChar3)


def set_section_page_numbering(section, fmt: str, start: int = 1):
    """Set section page numbering format and start number cleanly."""
    sectPr = section._sectPr
    for existing in sectPr.xpath(".//w:pgNumType"):
        sectPr.remove(existing)
    pgNumType = parse_xml(f'<w:pgNumType {nsdecls("w")} w:fmt="{fmt}" w:start="{start}"/>')
    sectPr.append(pgNumType)


def add_placeholder_box(doc: Document, text: str, instruction: str):
    """Insert a standard grey placeholder box with dashed border for student assets."""
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.columns[0].width = Inches(6.0)

    cell = table.cell(0, 0)
    set_cell_margins(cell, top=240, bottom=240, left=240, right=240)
    set_cell_shading(cell, "F4F5F7")

    # Set border to dashed
    tcPr = cell._tc.get_or_add_tcPr()
    borders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'<w:top w:val="dashed" w:sz="12" w:space="0" w:color="888888"/>'
        f'<w:left w:val="dashed" w:sz="12" w:space="0" w:color="888888"/>'
        f'<w:bottom w:val="dashed" w:sz="12" w:space="0" w:color="888888"/>'
        f'<w:right w:val="dashed" w:sz="12" w:space="0" w:color="888888"/>'
        f"</w:tcBorders>"
    )
    tcPr.append(borders)

    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)

    run_title = p.add_run(f"[{text.upper()}]\n")
    run_title.bold = True
    run_title.font.name = "Times New Roman"
    run_title.font.size = Pt(11)
    run_title.font.color.rgb = RGBColor(70, 70, 70)

    run_desc = p.add_run(f"({instruction})")
    run_desc.italic = True
    run_desc.font.name = "Times New Roman"
    run_desc.font.size = Pt(10)
    run_desc.font.color.rgb = RGBColor(110, 110, 110)

    # Empty paragraph after box for spacing
    after_p = doc.add_paragraph()
    after_p.paragraph_format.space_after = Pt(6)


def add_code_block(doc: Document, code_text: str):
    """Add a shaded monospaced code block with clean formatting."""
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.columns[0].width = Inches(6.2)

    cell = table.cell(0, 0)
    set_cell_margins(cell, top=140, bottom=140, left=180, right=180)
    set_cell_shading(cell, "F8F9FA")

    # Light solid border
    tcPr = cell._tc.get_or_add_tcPr()
    borders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'<w:top w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        f'<w:left w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        f'<w:bottom w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        f'<w:right w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        f"</w:tcBorders>"
    )
    tcPr.append(borders)

    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.0

    run = p.add_run(code_text)
    run.font.name = "Consolas"
    run.font.size = Pt(8.5)
    run.font.color.rgb = RGBColor(40, 40, 40)

    after_p = doc.add_paragraph()
    after_p.paragraph_format.space_after = Pt(6)


def format_table(table, col_widths: List[float], col_alignments: List[WD_ALIGN_PARAGRAPH]):
    """Format table with column widths, borders, header shading and alignment."""
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    prevent_row_split(table)

    # Apply widths
    for row in table.rows:
        for idx, width in enumerate(col_widths):
            row.cells[idx].width = Inches(width)

    # Style header row
    header_row = table.rows[0]
    for idx, cell in enumerate(header_row.cells):
        set_cell_shading(cell, "EAEAEA")
        set_cell_margins(cell, top=120, bottom=120, left=140, right=140)
        p = cell.paragraphs[0]
        p.alignment = col_alignments[idx]
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        for run in p.runs:
            run.bold = True
            run.font.name = "Times New Roman"
            run.font.size = Pt(10.5)

    # Style data rows
    for row in table.rows[1:]:
        for idx, cell in enumerate(row.cells):
            set_cell_margins(cell, top=100, bottom=100, left=140, right=140)
            p = cell.paragraphs[0]
            p.alignment = col_alignments[idx]
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            for run in p.runs:
                run.font.name = "Times New Roman"
                run.font.size = Pt(10)

    # Thin borders for all cells
    for row in table.rows:
        for cell in row.cells:
            tcPr = cell._tc.get_or_add_tcPr()
            b = parse_xml(
                f'<w:tcBorders {nsdecls("w")}>'
                f'<w:top w:val="single" w:sz="4" w:space="0" w:color="D0D0D0"/>'
                f'<w:left w:val="single" w:sz="4" w:space="0" w:color="D0D0D0"/>'
                f'<w:bottom w:val="single" w:sz="4" w:space="0" w:color="D0D0D0"/>'
                f'<w:right w:val="single" w:sz="4" w:space="0" w:color="D0D0D0"/>'
                f"</w:tcBorders>"
            )
            tcPr.append(b)


class DocxReportBuilder:
    """Constructs Word (.docx) report using template or defaults."""

    def __init__(
        self,
        brief: ProjectBrief,
        facts: ProjectFacts,
        outline: ReportOutline,
        sample_path: Optional[Union[str, Path]] = None,
    ):
        self.brief = brief
        self.facts = facts
        self.outline = outline
        self.sample_path = Path(sample_path) if sample_path else None

        self.figure_entries: List[Tuple[str, str, str]] = []  # (fig_num, caption, page_placeholder)
        self.table_entries: List[Tuple[str, str, str]] = []  # (tbl_num, caption, page_placeholder)
        self.toc_entries: List[Tuple[str, str, str]] = []  # (title, level, page_placeholder)

        self.figure_count = 0
        self.table_count = 0

    def init_document(self) -> Document:
        """Initialize document from sample or scratch with A4 & 1 inch margins."""
        if self.sample_path and self.sample_path.is_file():
            try:
                doc = Document(str(self.sample_path))
                # Clear existing body content while retaining styles, headers, media
                doc._body.clear_content()
                logger.info(f"Initialized document reusing template styles from {self.sample_path}")
            except Exception as e:
                logger.warning(f"Could not load sample docx {self.sample_path}: {e}. Creating fresh.")
                doc = Document()
        else:
            doc = Document()

        # Set Normal style font
        style = doc.styles["Normal"]
        font = style.font
        font.name = "Times New Roman"
        font.size = Pt(12)
        font.color.rgb = RGBColor(0, 0, 0)

        # Ensure first section is A4 with 1 inch margins
        s1 = doc.sections[0]
        s1.page_width = Inches(8.27)  # A4
        s1.page_height = Inches(11.69)
        s1.top_margin = Inches(1.0)
        s1.bottom_margin = Inches(1.0)
        s1.left_margin = Inches(1.0)
        s1.right_margin = Inches(1.0)

        return doc

    def build(self, generated_sections: Dict[str, Dict[str, Any]], out_path: Union[str, Path]) -> Document:
        """Build the full Word document."""
        doc = self.init_document()

        # ==========================================
        # SECTION 1: TITLE PAGE (No headers/footers)
        # ==========================================
        sec1 = doc.sections[0]
        self._build_title_page(doc)

        # ==========================================
        # SECTION 2: PRELIMINARY PAGES (Roman numerals)
        # ==========================================
        sec2 = doc.add_section(WD_SECTION_START.NEW_PAGE)
        sec2.page_width = Inches(8.27)
        sec2.page_height = Inches(11.69)
        sec2.top_margin = Inches(1.0)
        sec2.bottom_margin = Inches(1.0)
        sec2.left_margin = Inches(1.0)
        sec2.right_margin = Inches(1.0)

        # Unlink footer and add lower roman page numbers
        sec2.footer.is_linked_to_previous = False
        add_page_number_to_footer(sec2.footer)
        set_section_page_numbering(sec2, "lowerRoman", 1)

        # Preliminary pages
        self._build_certificate(doc)
        self._build_declaration(doc, generated_sections.get("declaration", {}))
        self._build_acknowledgement(doc, generated_sections.get("acknowledgement", {}))
        self._build_abstract(doc, generated_sections.get("abstract", {}))

        # ToC, List of Figures, List of Tables placeholders
        toc_header_p = self._build_toc_page(doc)
        lof_header_p = self._build_list_of_figures_page(doc)
        lot_header_p = self._build_list_of_tables_page(doc)
        self._build_list_of_abbreviations_page(doc)

        # ==========================================
        # SECTION 3: BODY (Arabic numerals restart at 1)
        # ==========================================
        sec3 = doc.add_section(WD_SECTION_START.NEW_PAGE)
        sec3.page_width = Inches(8.27)
        sec3.page_height = Inches(11.69)
        sec3.top_margin = Inches(1.0)
        sec3.bottom_margin = Inches(1.0)
        sec3.left_margin = Inches(1.0)
        sec3.right_margin = Inches(1.0)

        sec3.footer.is_linked_to_previous = False
        add_page_number_to_footer(sec3.footer)
        set_section_page_numbering(sec3, "decimal", 1)

        # Render Chapters and Sections
        self._build_body_chapters(doc, generated_sections)

        # Render End Matter (References, Appendices)
        self._build_end_matter(doc)

        # Populate ToC, LoF, LoT tables
        self._populate_toc_table(doc)
        self._populate_lof_table(doc)
        self._populate_lot_table(doc)

        # Save output atomically
        out_p = Path(out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        temp_p = out_p.with_suffix(".tmp.docx")
        doc.save(str(temp_p))
        try:
            if out_p.exists():
                out_p.unlink()
            temp_p.rename(out_p)
            logger.info(f"Saved complete docx report to {out_p}")
        except PermissionError:
            fallback_p = out_p.with_name(f"{out_p.stem}_updated{out_p.suffix}")
            if fallback_p.exists():
                try:
                    fallback_p.unlink()
                except Exception:
                    pass
            temp_p.rename(fallback_p)
            logger.warning(
                f"\n[WARNING] '{out_p}' is currently open in Microsoft Word or another application.\n"
                f"Saved newly generated report to '{fallback_p}' instead!\n"
            )
            print(
                f"\n[NOTE] '{out_p}' is currently open in Microsoft Word or another application.\n"
                f"Saved newly generated report to '{fallback_p}' instead!\n"
                f"To overwrite '{out_p}', close Microsoft Word and re-run build."
            )
        return doc

    def _build_title_page(self, doc: Document):
        """Construct Title Page strictly matching COA_report.docx."""
        # 1. Project Title (26pt bold, centered)
        p_title = doc.add_paragraph()
        p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_title.paragraph_format.space_before = Pt(8)
        p_title.paragraph_format.space_after = Pt(14)
        run_title = p_title.add_run(self.brief.project.title.upper())
        run_title.bold = True
        run_title.font.name = "Times New Roman"
        run_title.font.size = Pt(26)

        # 2. PROJECT REPORT label
        p_report = doc.add_paragraph()
        p_report.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_report.paragraph_format.space_before = Pt(4)
        p_report.paragraph_format.space_after = Pt(8)
        run_report = p_report.add_run("PROJECT REPORT")
        run_report.bold = True
        run_report.font.name = "Times New Roman"
        run_report.font.size = Pt(12)

        # 3. submitted by
        p_sub = doc.add_paragraph()
        p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_sub.paragraph_format.space_before = Pt(4)
        p_sub.paragraph_format.space_after = Pt(8)
        run_sub = p_sub.add_run("submitted by")
        run_sub.bold = True
        run_sub.font.name = "Times New Roman"
        run_sub.font.size = Pt(12)

        # 4. Borderless Team table (2 columns: Name on left, (Roll No) on right)
        team = self.brief.people.team
        tbl = doc.add_table(rows=len(team), cols=2)
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        tbl.autofit = False
        tbl.columns[0].width = Inches(3.2)
        tbl.columns[1].width = Inches(2.2)

        for i, member in enumerate(team):
            c0 = tbl.rows[i].cells[0]
            c1 = tbl.rows[i].cells[1]
            p0 = c0.paragraphs[0]
            p1 = c1.paragraphs[0]
            p0.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p1.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            p0.paragraph_format.space_before = Pt(1)
            p0.paragraph_format.space_after = Pt(1)
            p1.paragraph_format.space_before = Pt(1)
            p1.paragraph_format.space_after = Pt(1)

            r0 = p0.add_run(member.name)
            r0.bold = True
            r0.font.name = "Times New Roman"
            r0.font.size = Pt(12)

            roll_str = member.roll_no.strip()
            if not roll_str.startswith("("):
                roll_str = f"({roll_str})"
            r1 = p1.add_run(roll_str)
            r1.bold = True
            r1.font.name = "Times New Roman"
            r1.font.size = Pt(12)

        # 5. Course line
        p_course = doc.add_paragraph()
        p_course.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_course.paragraph_format.space_before = Pt(12)
        p_course.paragraph_format.space_after = Pt(4)
        run_course = p_course.add_run(f"As part of project based learning course {self.brief.project.course_code}")
        run_course.bold = True
        run_course.font.name = "Times New Roman"
        run_course.font.size = Pt(12)

        # 6. Degree details
        p_deg = doc.add_paragraph()
        p_deg.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_deg.paragraph_format.space_before = Pt(2)
        p_deg.paragraph_format.space_after = Pt(10)
        p_deg.paragraph_format.line_spacing = 1.15
        r_sem = p_deg.add_run(f"{self.brief.project.semester.upper()}\n")
        r_sem.bold = True
        r_sem.font.name = "Times New Roman"
        r_sem.font.size = Pt(12)
        r_deg = p_deg.add_run("BACHELOR OF TECHNOLOGY\nIN\nCOMPUTER SCIENCE AND ENGINEERING")
        r_deg.bold = True
        r_deg.font.name = "Times New Roman"
        r_deg.font.size = Pt(12)

        # 7. College Logo
        logo_path = Path("out/extracted_logos/college_logo.png")
        if not logo_path.is_file() and self.sample_path and self.sample_path.is_file():
            try:
                s_doc = Document(str(self.sample_path))
                for rel in s_doc.part.rels.values():
                    if "image1." in rel.target_ref:
                        logo_path.parent.mkdir(parents=True, exist_ok=True)
                        logo_path.write_bytes(rel.target_part.blob)
                        break
            except Exception:
                pass

        p_logo = doc.add_paragraph()
        p_logo.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_logo.paragraph_format.space_before = Pt(6)
        p_logo.paragraph_format.space_after = Pt(8)
        if logo_path.is_file():
            p_logo.add_run().add_picture(str(logo_path), width=Inches(2.54), height=Inches(2.40))
        else:
            r_l = p_logo.add_run("[ COLLEGE LOGO ]")
            r_l.font.color.rgb = RGBColor(120, 120, 120)

        # 8. Department & College footer
        p_foot = doc.add_paragraph()
        p_foot.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_foot.paragraph_format.space_before = Pt(4)
        p_foot.paragraph_format.space_after = Pt(0)
        p_foot.paragraph_format.line_spacing = 1.15

        dept_str = self.brief.project.department.upper().rstrip(",")
        college_str = self.brief.project.college.upper()
        if "AUTONOMOUS" not in college_str:
            college_str += " (AUTONOMOUS)"
        addr_str = self.brief.project.address.upper()
        date_str = self.brief.project.month_year.upper()

        r_foot = p_foot.add_run(
            f"{dept_str}\n"
            f"{college_str}\n"
            f"{addr_str}\n"
            f"{date_str}"
        )
        r_foot.font.name = "Times New Roman"
        r_foot.font.size = Pt(11)
        r_foot.bold = True

    def _build_certificate(self, doc: Document):
        """Build Bonafide Certificate page strictly matching COA_report.docx."""
        p_head = doc.add_paragraph()
        p_head.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_head.paragraph_format.space_before = Pt(8)
        p_head.paragraph_format.space_after = Pt(6)
        p_head.paragraph_format.line_spacing = 1.15

        dept_line = self.brief.project.department.upper().rstrip(",") + ",\n"
        college_line = self.brief.project.college.upper()
        if "AUTONOMOUS" not in college_line:
            college_line += "\n(AUTONOMOUS)"
        else:
            college_line = college_line.replace("(AUTONOMOUS)", "\n(AUTONOMOUS)")
        addr_line = "\n" + self.brief.project.address.upper().replace("- 690 529", "").strip()

        r_h = p_head.add_run(f"{dept_line}{college_line}{addr_line}")
        r_h.bold = True
        r_h.font.name = "Times New Roman"
        r_h.font.size = Pt(14)

        # Emblem Logo
        emblem_path = Path("out/extracted_logos/emblem_logo.png")
        if not emblem_path.is_file() and self.sample_path and self.sample_path.is_file():
            try:
                s_doc = Document(str(self.sample_path))
                for rel in s_doc.part.rels.values():
                    if "image2." in rel.target_ref:
                        emblem_path.parent.mkdir(parents=True, exist_ok=True)
                        emblem_path.write_bytes(rel.target_part.blob)
                        break
            except Exception:
                pass

        p_logo = doc.add_paragraph()
        p_logo.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_logo.paragraph_format.space_before = Pt(8)
        p_logo.paragraph_format.space_after = Pt(10)
        if emblem_path.is_file():
            p_logo.add_run().add_picture(str(emblem_path), width=Inches(1.10), height=Inches(1.03))

        # BONAFIDE CERTIFICATE
        p_title = doc.add_paragraph()
        p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_title.paragraph_format.space_before = Pt(8)
        p_title.paragraph_format.space_after = Pt(16)
        run_t = p_title.add_run("BONAFIDE CERTIFICATE")
        run_t.bold = True
        run_t.font.name = "Times New Roman"
        run_t.font.size = Pt(16)

        # Body
        team_str = ", ".join(f"{m.name} ({m.roll_no.strip('()')})" for m in self.brief.people.team)
        cert_text = (
            f"This is to certify that the project report entitled \"{self.brief.project.title.upper()}\" "
            f"submitted by {team_str}, in partial fulfillment of the requirements for the award of the "
            f"Degree of Bachelor of Technology in Computer Science and Engineering is a bonafide record "
            f"of the work carried out under my guidance and supervision at college."
        )

        p_body = doc.add_paragraph()
        p_body.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_body.paragraph_format.line_spacing = 1.25
        p_body.paragraph_format.space_after = Pt(50)
        r_b = p_body.add_run(cert_text)
        r_b.font.name = "Times New Roman"
        r_b.font.size = Pt(12)

        # Dual signature blocks table (borderless)
        sig_tbl = doc.add_table(rows=1, cols=2)
        sig_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        sig_tbl.columns[0].width = Inches(3.2)
        sig_tbl.columns[1].width = Inches(3.2)

        p_l = sig_tbl.cell(0, 0).paragraphs[0]
        p_l.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_l.paragraph_format.line_spacing = 1.15
        p_l.add_run(
            f"__________________________\n"
            f"SIGNATURE\n\n"
            f"{self.brief.people.guide.name}\n"
            f"{self.brief.people.guide.designation or 'Course Coordinator / Guide'}\n"
            f"{self.brief.project.department.rstrip(',')}"
        )

        p_r = sig_tbl.cell(0, 1).paragraphs[0]
        p_r.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p_r.paragraph_format.line_spacing = 1.15
        p_r.add_run(
            f"__________________________\n"
            f"SIGNATURE\n\n"
            f"{self.brief.people.hod.name}\n"
            f"{self.brief.people.hod.designation or 'Head of Department'}\n"
            f"{self.brief.project.department.rstrip(',')}"
        )

        doc.add_page_break()

    def _build_declaration(self, doc: Document, gen_data: Dict[str, Any]):
        """Build Declaration page."""
        p_t = doc.add_paragraph()
        p_t.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_t.paragraph_format.space_before = Pt(14)
        p_t.paragraph_format.space_after = Pt(20)
        run_t = p_t.add_run("DECLARATION")
        run_t.bold = True
        run_t.font.size = Pt(15)

        paras = gen_data.get("paragraphs") or [
            f"We hereby declare that the micro-project report entitled '{self.brief.project.title}' "
            f"submitted to {self.brief.project.college} is a record of original work done by us "
            f"under the supervision of {self.brief.people.guide.name}.",
            "We further declare that this report has not previously formed the basis for the award of any "
            "degree, diploma, or other similar title of any university or institution.",
        ]

        for p_text in paras:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p.paragraph_format.line_spacing = 1.2
            p.paragraph_format.space_after = Pt(10)
            add_markdown_formatted_runs(p, p_text)

        p_date = doc.add_paragraph()
        p_date.paragraph_format.space_before = Pt(30)
        p_date.paragraph_format.space_after = Pt(20)
        p_date.add_run(f"Place: {self.brief.project.address.split(',')[-1].strip()}\nDate: {self.brief.project.month_year}")

        # Student signatures
        p_sig = doc.add_paragraph()
        p_sig.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        for m in self.brief.people.team:
            p_sig.add_run(f"{m.name} ({m.roll_no})\n")

        doc.add_page_break()

    def _build_acknowledgement(self, doc: Document, gen_data: Dict[str, Any]):
        """Build Acknowledgement page."""
        p_t = doc.add_paragraph()
        p_t.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_t.paragraph_format.space_before = Pt(14)
        p_t.paragraph_format.space_after = Pt(20)
        run_t = p_t.add_run("ACKNOWLEDGEMENT")
        run_t.bold = True
        run_t.font.size = Pt(15)

        paras = gen_data.get("paragraphs") or [
            f"We express our sincere gratitude to our guide {self.brief.people.guide.name}, "
            f"{self.brief.people.guide.designation or 'Faculty Guide'}, for continuous encouragement "
            f"and invaluable guidance throughout the course of this project.",
            f"We are deeply thankful to {self.brief.people.hod.name}, Head of the Department of "
            f"{self.brief.project.department}, for providing the necessary laboratory infrastructure "
            f"and technical support.",
            "Finally, we wish to thank our parents and fellow classmates for their moral support "
            "and constructive feedback during this endeavor.",
        ]

        for raw_p in paras:
            for p_text in sanitize_paragraph_prose(raw_p):
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                p.paragraph_format.line_spacing = 1.2
                p.paragraph_format.space_after = Pt(10)
                add_markdown_formatted_runs(p, p_text)

        p_names = doc.add_paragraph()
        p_names.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p_names.paragraph_format.space_before = Pt(30)
        for m in self.brief.people.team:
            p_names.add_run(f"{m.name} ({m.roll_no})\n")

        doc.add_page_break()

    def _build_abstract(self, doc: Document, gen_data: Dict[str, Any]):
        """Build Abstract page."""
        p_t = doc.add_paragraph()
        p_t.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_t.paragraph_format.space_before = Pt(14)
        p_t.paragraph_format.space_after = Pt(20)
        run_t = p_t.add_run("ABSTRACT")
        run_t.bold = True
        run_t.font.size = Pt(15)

        if self.brief.abstract:
            clean_abs = self.brief.abstract
            clean_abs = re.sub(r"16[^\x20-\x7E]?2\s*LCD", "16x2 LCD", clean_abs)
            clean_abs = re.sub(r"\bpro\s+vide\b", "provide", clean_abs)
            clean_abs = re.sub(r"\bwastag\s+e\b", "wastage", clean_abs)
            paras = [clean_abs]
        else:
            paras = gen_data.get("paragraphs") or [
                f"This micro-project report describes the design and implementation of '{self.brief.project.title}' "
                f"utilizing the {self.brief.implementation.board.name} microcontroller."
            ]

        for raw_p in paras:
            for p_text in sanitize_paragraph_prose(raw_p):
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                p.paragraph_format.line_spacing = 1.2
                p.paragraph_format.space_after = Pt(10)
                add_markdown_formatted_runs(p, p_text)

        doc.add_page_break()

    def _build_toc_page(self, doc: Document):
        # Banner table matching COA_report.docx Table 1 (1 row, 1 col, centered 'CONTENTS')
        banner = doc.add_table(rows=1, cols=1)
        banner.alignment = WD_TABLE_ALIGNMENT.CENTER
        banner.autofit = False
        banner.columns[0].width = Inches(6.2)
        c = banner.cell(0, 0)
        p_banner = c.paragraphs[0]
        p_banner.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_banner.paragraph_format.space_before = Pt(4)
        p_banner.paragraph_format.space_after = Pt(4)
        r_ban = p_banner.add_run("CONTENTS")
        r_ban.bold = True
        r_ban.font.name = "Times New Roman"
        r_ban.font.size = Pt(14)

        p_space = doc.add_paragraph()
        p_space.paragraph_format.space_after = Pt(12)

        self.toc_table = doc.add_table(rows=1, cols=3)
        self.toc_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        self.toc_table.columns[0].width = Inches(1.2)
        self.toc_table.columns[1].width = Inches(4.2)
        self.toc_table.columns[2].width = Inches(0.8)

        self.toc_table.rows[0].cells[0].paragraphs[0].text = "Item"
        self.toc_table.rows[0].cells[1].paragraphs[0].text = "Title"
        self.toc_table.rows[0].cells[2].paragraphs[0].text = "Page"

        doc.add_page_break()
        return p_banner

    def _build_list_of_figures_page(self, doc: Document):
        p_t = doc.add_paragraph()
        p_t.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_t.paragraph_format.space_before = Pt(14)
        p_t.paragraph_format.space_after = Pt(16)
        run_t = p_t.add_run("LIST OF FIGURES")
        run_t.bold = True
        run_t.font.name = "Times New Roman"
        run_t.font.size = Pt(15)

        self.lof_table = doc.add_table(rows=1, cols=3)
        self.lof_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        self.lof_table.columns[0].width = Inches(1.5)
        self.lof_table.columns[1].width = Inches(3.9)
        self.lof_table.columns[2].width = Inches(0.8)

        self.lof_table.rows[0].cells[0].paragraphs[0].text = "Figure"
        self.lof_table.rows[0].cells[1].paragraphs[0].text = "Description"
        self.lof_table.rows[0].cells[2].paragraphs[0].text = "Page No."

        doc.add_page_break()
        return p_t

    def _build_list_of_tables_page(self, doc: Document):
        p_t = doc.add_paragraph()
        p_t.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_t.paragraph_format.space_before = Pt(14)
        p_t.paragraph_format.space_after = Pt(16)
        run_t = p_t.add_run("LIST OF TABLES")
        run_t.bold = True
        run_t.font.name = "Times New Roman"
        run_t.font.size = Pt(15)

        self.lot_table = doc.add_table(rows=1, cols=3)
        self.lot_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        self.lot_table.columns[0].width = Inches(1.5)
        self.lot_table.columns[1].width = Inches(3.9)
        self.lot_table.columns[2].width = Inches(0.8)

        self.lot_table.rows[0].cells[0].paragraphs[0].text = "Table No."
        self.lot_table.rows[0].cells[1].paragraphs[0].text = "Description"
        self.lot_table.rows[0].cells[2].paragraphs[0].text = "Page No."

        doc.add_page_break()
        return p_t

    def _build_list_of_abbreviations_page(self, doc: Document):
        p_t = doc.add_paragraph()
        p_t.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_t.paragraph_format.space_before = Pt(14)
        p_t.paragraph_format.space_after = Pt(16)
        run_t = p_t.add_run("LIST OF ABBREVIATIONS")
        run_t.bold = True
        run_t.font.size = Pt(15)

        # Collect abbreviations that actually match the brief and facts
        active_abbreviations = []
        full_text = (
            f"{self.brief.project.title} {self.brief.implementation.board.name} "
            f"{self.brief.implementation.board.core} {self.brief.implementation.framework} "
            f"{' '.join(p.peripheral for p in self.brief.implementation.pins)} "
            f"{' '.join(c.interface for c in self.brief.implementation.components)}"
        )
        for abbr, desc in sorted(KNOWN_ABBREVIATIONS.items()):
            if abbr in full_text or abbr in ["ADC", "GPIO", "I2C", "MCU", "OLED", "UART"]:
                active_abbreviations.append((abbr, desc))

        tbl = doc.add_table(rows=len(active_abbreviations) + 1, cols=2)
        tbl.rows[0].cells[0].paragraphs[0].text = "Abbreviation"
        tbl.rows[0].cells[1].paragraphs[0].text = "Expansion"

        for idx, (abbr, desc) in enumerate(active_abbreviations, start=1):
            tbl.rows[idx].cells[0].paragraphs[0].text = abbr
            tbl.rows[idx].cells[1].paragraphs[0].text = desc

        format_table(
            tbl,
            col_widths=[1.8, 4.4],
            col_alignments=[WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT],
        )

        doc.add_page_break()

    def _build_body_chapters(self, doc: Document, generated_sections: Dict[str, Dict[str, Any]]):
        """Render all chapters, sections, tables, figures, and code blocks."""
        current_chapter: Optional[str] = None

        for item in self.outline.items:
            if item.kind == "chapter":
                if current_chapter is not None:
                    doc.add_page_break()
                current_chapter = item.number

                # Chapter Header: Centred, Bold Caps
                p_ch = doc.add_paragraph()
                p_ch.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p_ch.paragraph_format.space_before = Pt(20)
                p_ch.paragraph_format.space_after = Pt(4)
                run_num = p_ch.add_run(f"CHAPTER {item.number}\n")
                run_num.bold = True
                run_num.font.size = Pt(14)

                p_t = doc.add_paragraph()
                p_t.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p_t.paragraph_format.space_before = Pt(0)
                p_t.paragraph_format.space_after = Pt(18)
                run_t = p_t.add_run(item.title.upper())
                run_t.bold = True
                run_t.font.size = Pt(14)

                self.toc_entries.append((f"CHAPTER {item.number}: {item.title}", "chapter", f"ch_{item.number}"))
                continue

            if item.kind == "section":
                sec_id = item.id
                gen_data = generated_sections.get(sec_id, {})

                # Section Heading
                p_sec = doc.add_paragraph()
                p_sec.paragraph_format.space_before = Pt(12)
                p_sec.paragraph_format.space_after = Pt(6)
                p_sec.paragraph_format.keep_with_next = True
                run_sec = p_sec.add_run(f"{item.number} {item.title}")
                run_sec.bold = True
                run_sec.font.size = Pt(12)

                self.toc_entries.append((f"{item.number} {item.title}", "section", sec_id))

                # Render prose paragraphs
                for raw_p in gen_data.get("paragraphs", []):
                    for p_text in sanitize_paragraph_prose(raw_p):
                        p = doc.add_paragraph()
                        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                        p.paragraph_format.line_spacing = 1.15
                        p.paragraph_format.space_after = Pt(6)
                        add_markdown_formatted_runs(p, p_text)

                # Render bullets if any
                for raw_b in gen_data.get("bullets", []):
                    for b_text in sanitize_paragraph_prose(raw_b):
                        b_clean = re.sub(r"^[\*\-•\u2022\s]+", "", b_text).strip()
                        if "List Bullet" in doc.styles:
                            p_b = doc.add_paragraph(style="List Bullet")
                            add_markdown_formatted_runs(p_b, b_clean)
                        else:
                            p_b = doc.add_paragraph()
                            p_b.paragraph_format.left_indent = Inches(0.25)
                            r_sym = p_b.add_run("•   ")
                            r_sym.font.name = "Times New Roman"
                            r_sym.font.size = Pt(12)
                            add_markdown_formatted_runs(p_b, b_clean)
                        p_b.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                        p_b.paragraph_format.line_spacing = 1.15
                        p_b.paragraph_format.space_after = Pt(3)

                # Inject deterministic tables & figures for specific sections
                self._inject_section_special_content(doc, sec_id, item)

    def _inject_section_special_content(self, doc: Document, sec_id: str, item: Any):
        """Deterministically insert tables, diagrams, or placeholder boxes."""
        # Section 2.2: Board Specifications Table
        if sec_id == "2.2":
            self.table_count += 1
            tbl_num = f"Table 2.{self.table_count}"
            tbl_title = "STM32F103C8T6 (Blue Pill) Technical Specifications"
            self._add_table_caption(doc, tbl_num, tbl_title)

            b = self.brief.implementation.board
            specs = [
                ("Microcontroller Part", b.mcu_part_number),
                ("Processor Architecture", b.core),
                ("CPU Clock Frequency", b.clock),
                ("Flash Memory Capacity", b.flash),
                ("SRAM Capacity", b.sram),
                ("Operating Voltage", b.voltage),
                ("ADC Resolution", "12-bit (up to 16 channels)"),
                ("Communication Peripherals", "I2C, SPI, USART, USB 2.0 FS"),
            ]
            t = doc.add_table(rows=len(specs) + 1, cols=2)
            t.rows[0].cells[0].paragraphs[0].text = "Specification Parameter"
            t.rows[0].cells[1].paragraphs[0].text = "Value / Details"
            for idx, (param, val) in enumerate(specs, start=1):
                t.rows[idx].cells[0].paragraphs[0].text = param
                t.rows[idx].cells[1].paragraphs[0].text = val

            format_table(
                t,
                col_widths=[2.8, 3.4],
                col_alignments=[WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT],
            )
            self.table_entries.append((tbl_num, tbl_title, "pg"))

        # Section 2.3: Hardware Components Table
        elif sec_id == "2.3":
            self.table_count += 1
            tbl_num = f"Table 2.{self.table_count}"
            tbl_title = "System Hardware Components and Specifications"
            self._add_table_caption(doc, tbl_num, tbl_title)

            comps = self.facts.component_table
            t = doc.add_table(rows=len(comps) + 1, cols=3)
            t.rows[0].cells[0].paragraphs[0].text = "Component Name"
            t.rows[0].cells[1].paragraphs[0].text = "Role in System"
            t.rows[0].cells[2].paragraphs[0].text = "Interface / Details"
            for idx, c in enumerate(comps, start=1):
                t.rows[idx].cells[0].paragraphs[0].text = c["name"]
                t.rows[idx].cells[1].paragraphs[0].text = c["role"]
                t.rows[idx].cells[2].paragraphs[0].text = f"{c['interface']} - {c['notes']}"

            format_table(
                t,
                col_widths=[2.2, 2.0, 2.0],
                col_alignments=[WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT],
            )
            self.table_entries.append((tbl_num, tbl_title, "pg"))

        # Section 3.2: Block Diagram
        elif sec_id == "3.2":
            self.figure_count += 1
            fig_num = f"Figure 3.{self.figure_count}"
            fig_title = "Overall System Architecture and Hardware Block Diagram"

            # Check if diagram image exists
            diag_path = Path("out/block_diagram.png")
            if diag_path.is_file():
                p_img = doc.add_paragraph()
                p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p_img.paragraph_format.keep_with_next = True
                p_img.add_run().add_picture(str(diag_path), width=Inches(5.5))
            else:
                add_placeholder_box(
                    doc,
                    "Figure 3.1 Block Diagram",
                    "Block diagram showing sensor inputs, STM32 MCU, OLED display, and LED output",
                )

            self._add_figure_caption(doc, fig_num, fig_title)
            self.figure_entries.append((fig_num, fig_title, "pg"))

        # Section 3.3: Circuit Diagram
        elif sec_id == "3.3":
            self.figure_count += 1
            fig_num = f"Figure 3.{self.figure_count}"
            fig_title = "Complete Circuit Interconnection Diagram"

            conn_path = Path("out/connection_diagram.png")
            if conn_path.is_file():
                p_img = doc.add_paragraph()
                p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p_img.paragraph_format.keep_with_next = True
                p_img.add_run().add_picture(str(conn_path), width=Inches(5.5))
            else:
                add_placeholder_box(
                    doc,
                    "Figure 3.2 Circuit Diagram",
                    "Wiring diagram of STM32 Blue Pill, soil moisture sensor, SSD1306 OLED, and ST-Link",
                )

            self._add_figure_caption(doc, fig_num, fig_title)
            self.figure_entries.append((fig_num, fig_title, "pg"))

        # Section 3.5: Pin Mapping Table
        elif sec_id == "3.5":
            self.table_count += 1
            tbl_num = f"Table 3.{self.table_count}"
            tbl_title = "Microcontroller Pin Allocation and Peripheral Mapping"
            self._add_table_caption(doc, tbl_num, tbl_title)

            pins = self.facts.pin_table
            t = doc.add_table(rows=len(pins) + 1, cols=4)
            t.rows[0].cells[0].paragraphs[0].text = "MCU Pin"
            t.rows[0].cells[1].paragraphs[0].text = "Peripheral"
            t.rows[0].cells[2].paragraphs[0].text = "Connected Device Pin"
            t.rows[0].cells[3].paragraphs[0].text = "Function Description"
            for idx, p in enumerate(pins, start=1):
                t.rows[idx].cells[0].paragraphs[0].text = p["pin"]
                t.rows[idx].cells[1].paragraphs[0].text = p["peripheral"]
                t.rows[idx].cells[2].paragraphs[0].text = p["connected_to"]
                t.rows[idx].cells[3].paragraphs[0].text = p["function"]

            format_table(
                t,
                col_widths=[1.2, 1.4, 1.8, 1.8],
                col_alignments=[
                    WD_ALIGN_PARAGRAPH.CENTER,
                    WD_ALIGN_PARAGRAPH.CENTER,
                    WD_ALIGN_PARAGRAPH.LEFT,
                    WD_ALIGN_PARAGRAPH.LEFT,
                ],
            )
            self.table_entries.append((tbl_num, tbl_title, "pg"))

        # Section 3.6: Algorithm and Flowchart
        elif sec_id == "3.6":
            self.figure_count += 1
            fig_num = f"Figure 3.{self.figure_count}"
            fig_title = "Firmware Execution and Sampling Flowchart"

            flow_path = Path("out/flowchart.png")
            if flow_path.is_file():
                p_img = doc.add_paragraph()
                p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p_img.paragraph_format.keep_with_next = True
                p_img.add_run().add_picture(str(flow_path), width=Inches(4.5))
            else:
                add_placeholder_box(
                    doc,
                    "Figure 3.3 System Flowchart",
                    "Flowchart showing initialization, ADC reading, averaging, map() calculation, and state evaluation",
                )

            self._add_figure_caption(doc, fig_num, fig_title)
            self.figure_entries.append((fig_num, fig_title, "pg"))

        # Section 3.7: Calibration & State Table
        elif sec_id == "3.7":
            self.table_count += 1
            tbl_num = f"Table 3.{self.table_count}"
            is_night_lamp = any("DARK" in sr.state_name for sr in self.facts.state_ranges)
            if is_night_lamp:
                tbl_title = "Ambient Light ADC Thresholds and Hysteresis State Classification"
                col2_name = "Illumination Level"
            else:
                tbl_title = "ADC Calibration and Soil Moisture State Classification"
                col2_name = "Moisture %"
            self._add_table_caption(doc, tbl_num, tbl_title)

            sr_list = self.facts.state_ranges
            t = doc.add_table(rows=len(sr_list) + 1, cols=4)
            t.rows[0].cells[0].paragraphs[0].text = "Operating State"
            t.rows[0].cells[1].paragraphs[0].text = "Condition"
            t.rows[0].cells[2].paragraphs[0].text = col2_name
            t.rows[0].cells[3].paragraphs[0].text = "Raw ADC Range (12-bit)"
            for idx, sr in enumerate(sr_list, start=1):
                t.rows[idx].cells[0].paragraphs[0].text = sr.state_name
                t.rows[idx].cells[1].paragraphs[0].text = sr.condition_desc
                t.rows[idx].cells[2].paragraphs[0].text = sr.percent_range
                t.rows[idx].cells[3].paragraphs[0].text = sr.raw_range

            format_table(
                t,
                col_widths=[1.8, 1.6, 1.4, 1.4],
                col_alignments=[
                    WD_ALIGN_PARAGRAPH.LEFT,
                    WD_ALIGN_PARAGRAPH.LEFT,
                    WD_ALIGN_PARAGRAPH.CENTER,
                    WD_ALIGN_PARAGRAPH.CENTER,
                ],
            )
            self.table_entries.append((tbl_num, tbl_title, "pg"))

        # Section 5.2: Test Cases Table
        elif sec_id == "5.2":
            self.table_count += 1
            tbl_num = f"Table 5.{self.table_count}"
            tbl_title = "System Test Cases and Expected Operational Responses"
            self._add_table_caption(doc, tbl_num, tbl_title)

            tests = self.facts.test_table
            t = doc.add_table(rows=len(tests) + 1, cols=4)
            t.rows[0].cells[0].paragraphs[0].text = "Test ID"
            t.rows[0].cells[1].paragraphs[0].text = "Test Condition"
            t.rows[0].cells[2].paragraphs[0].text = "Expected Behavior"
            t.rows[0].cells[3].paragraphs[0].text = "Observed Result"
            for idx, test in enumerate(tests, start=1):
                t.rows[idx].cells[0].paragraphs[0].text = test["id"]
                t.rows[idx].cells[1].paragraphs[0].text = test["condition"]
                t.rows[idx].cells[2].paragraphs[0].text = test["expected"]
                t.rows[idx].cells[3].paragraphs[0].text = test["observed"]

            format_table(
                t,
                col_widths=[1.0, 1.8, 2.0, 1.4],
                col_alignments=[
                    WD_ALIGN_PARAGRAPH.CENTER,
                    WD_ALIGN_PARAGRAPH.LEFT,
                    WD_ALIGN_PARAGRAPH.LEFT,
                    WD_ALIGN_PARAGRAPH.LEFT,
                ],
            )
            self.table_entries.append((tbl_num, tbl_title, "pg"))

        # Section 5.3: Hardware Implementation Photographs
        elif sec_id == "5.3":
            add_placeholder_box(
                doc,
                "Photograph 5.1: Hardware Prototype Setup",
                "Please insert high-resolution photograph of the wired STM32 Blue Pill, soil moisture sensor, and OLED display prototype.",
            )

        # Section 5.4: Output Screenshots
        elif sec_id == "5.4":
            add_placeholder_box(
                doc,
                "Photograph 5.2: OLED Display and Telemetry Output",
                "Please insert photograph or screenshot of the SSD1306 OLED display screen and serial terminal monitor.",
            )

    def _add_figure_caption(self, doc: Document, fig_num: str, caption_text: str):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(12)
        run_lbl = p.add_run(f"{fig_num}: ")
        run_lbl.bold = True
        run_lbl.font.size = Pt(10.5)
        run_cap = p.add_run(caption_text)
        run_cap.italic = True
        run_cap.font.size = Pt(10.5)

    def _add_table_caption(self, doc: Document, tbl_num: str, caption_text: str):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.keep_with_next = True
        run_lbl = p.add_run(f"{tbl_num}: ")
        run_lbl.bold = True
        run_lbl.font.size = Pt(10.5)
        run_cap = p.add_run(caption_text)
        run_cap.italic = True
        run_cap.font.size = Pt(10.5)

    def _build_end_matter(self, doc: Document):
        """Render References and Code Appendices."""
        # References
        doc.add_page_break()
        p_ref = doc.add_paragraph()
        p_ref.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_ref.paragraph_format.space_before = Pt(16)
        p_ref.paragraph_format.space_after = Pt(16)
        run_ref = p_ref.add_run("REFERENCES")
        run_ref.bold = True
        run_ref.font.size = Pt(14)

        self.toc_entries.append(("REFERENCES", "chapter", "references"))

        refs = self.brief.references or [
            "STMicroelectronics, 'STM32F103x8 STM32F103xB Medium-density performance line ARM-based 32-bit MCU Datasheet', DocID 13587 Rev 17, 2020.",
            "STMicroelectronics, 'RM0008 Reference Manual: STM32F101xx, STM32F102xx, STM32F103xx advanced ARM-based 32-bit MCUs', Rev 21, 2021.",
            "STM32duino, 'Arduino Core for STM32 based boards', Official STMicroelectronics GitHub Repository, 2024.",
            "Adafruit Industries, 'Adafruit SSD1306 OLED Driver Library Documentation', 2023.",
        ]

        for idx, ref_text in enumerate(refs, start=1):
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p.paragraph_format.line_spacing = 1.15
            p.paragraph_format.space_after = Pt(6)
            run_idx = p.add_run(f"[{idx}] ")
            run_idx.bold = True
            ref_clean = re.sub(r'^\*+\s*', '', ref_text).strip()
            add_markdown_formatted_runs(p, ref_clean)

        # Appendix A: Complete Embedded C Source Code
        doc.add_page_break()
        p_app_a = doc.add_paragraph()
        p_app_a.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_app_a.paragraph_format.space_before = Pt(16)
        p_app_a.paragraph_format.space_after = Pt(14)
        run_app_a = p_app_a.add_run("APPENDIX A\nCOMPLETE EMBEDDED C SOURCE CODE")
        run_app_a.bold = True
        run_app_a.font.size = Pt(14)

        self.toc_entries.append(("APPENDIX A: Complete Source Code", "chapter", "appendix_a"))

        for code_rel in self.brief.code_files_list:
            code_path = Path(code_rel)
            if code_path.is_file():
                code_text = code_path.read_text(encoding="utf-8", errors="replace")
                p_fn = doc.add_paragraph()
                p_fn.paragraph_format.space_before = Pt(6)
                p_fn.paragraph_format.space_after = Pt(4)
                p_fn.add_run(f"Source File: {code_path.name} ({len(code_text.splitlines())} lines)").bold = True
                add_code_block(doc, code_text)

        # Appendix B: Additional Circuit Diagrams and Configuration Details
        doc.add_page_break()
        p_app_b = doc.add_paragraph()
        p_app_b.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_app_b.paragraph_format.space_before = Pt(16)
        p_app_b.paragraph_format.space_after = Pt(14)
        run_app_b = p_app_b.add_run("APPENDIX B\nADDITIONAL CONFIGURATION DETAILS")
        run_app_b.bold = True
        run_app_b.font.size = Pt(14)

        self.toc_entries.append(("APPENDIX B: Additional Configuration Details", "chapter", "appendix_b"))

        p_cfg = doc.add_paragraph()
        p_cfg.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p_cfg.paragraph_format.line_spacing = 1.15
        p_cfg.add_run(
            "Clock Tree and Power Consumption Details:\n"
            "- HSE Crystal: 8.000 MHz\n"
            "- PLL Multiplier: x9 (72 MHz System Core Clock)\n"
            "- AHB Prescaler: /1 (72 MHz HCLK)\n"
            "- APB1 Prescaler: /2 (36 MHz PCLK1 - Maximum allowable frequency)\n"
            "- APB2 Prescaler: /1 (72 MHz PCLK2)\n"
            "- ADC Prescaler: /6 (12 MHz ADCCLK - within 14 MHz specification limit)\n"
            "- I2C Speed: Standard Mode 100 kHz\n"
            "- Active Run Current: ~32 mA at 72 MHz\n"
        )

        # Appendix C: Additional Output Images
        doc.add_page_break()
        p_app_c = doc.add_paragraph()
        p_app_c.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_app_c.paragraph_format.space_before = Pt(16)
        p_app_c.paragraph_format.space_after = Pt(14)
        run_app_c = p_app_c.add_run("APPENDIX C\nADDITIONAL OUTPUT IMAGES")
        run_app_c.bold = True
        run_app_c.font.size = Pt(14)

        self.toc_entries.append(("APPENDIX C: Additional Output Images", "chapter", "appendix_c"))

        add_placeholder_box(
            doc,
            "Appendix C Supplementary Images",
            "Optional oscilloscope captures, serial plotter waveforms, or simulation scope traces.",
        )

    def _populate_toc_table(self, doc: Document):
        """Populate Table of Contents entries."""
        for title, level, anchor in self.toc_entries:
            row = self.toc_table.add_row()
            if level == "chapter":
                row.cells[0].paragraphs[0].text = ""
                row.cells[1].paragraphs[0].text = title
                row.cells[1].paragraphs[0].runs[0].bold = True
                row.cells[2].paragraphs[0].text = ""  # Populated during pagenums pass
            else:
                row.cells[0].paragraphs[0].text = ""
                row.cells[1].paragraphs[0].text = f"    {title}"
                row.cells[2].paragraphs[0].text = ""

        format_table(
            self.toc_table,
            col_widths=[0.8, 4.6, 0.8],
            col_alignments=[WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.RIGHT],
        )

    def _populate_lof_table(self, doc: Document):
        """Populate List of Figures entries."""
        for num, title, anchor in self.figure_entries:
            row = self.lof_table.add_row()
            row.cells[0].paragraphs[0].text = num
            row.cells[1].paragraphs[0].text = title
            row.cells[2].paragraphs[0].text = ""  # Populated during pagenums pass

        format_table(
            self.lof_table,
            col_widths=[1.2, 4.2, 0.8],
            col_alignments=[WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.RIGHT],
        )

    def _populate_lot_table(self, doc: Document):
        """Populate List of Tables entries."""
        for num, title, anchor in self.table_entries:
            row = self.lot_table.add_row()
            row.cells[0].paragraphs[0].text = num
            row.cells[1].paragraphs[0].text = title
            row.cells[2].paragraphs[0].text = ""  # Populated during pagenums pass

        format_table(
            self.lot_table,
            col_widths=[1.2, 4.2, 0.8],
            col_alignments=[WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.RIGHT],
        )

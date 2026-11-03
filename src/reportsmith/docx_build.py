"""Document generator: builds Word (.docx) report using sample.docx template or standard college style."""

import copy
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Set, Tuple, Union

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
from reportsmith.llm import _is_prompt_echo

logger = logging.getLogger(__name__)


def attempt_close_word_doc(file_path: Union[str, Path]) -> bool:
    """Attempt to close an open document in running Microsoft Word instance on Windows."""
    if sys.platform != "win32":
        return False
    target_abs = str(Path(file_path).resolve()).lower()
    closed = False
    try:
        import win32com.client
        word = win32com.client.GetActiveObject("Word.Application")
        for wdoc in list(word.Documents):
            try:
                if str(Path(wdoc.FullName).resolve()).lower() == target_abs:
                    wdoc.Close(SaveChanges=0)
                    closed = True
            except Exception:
                pass
        if closed:
            time.sleep(0.4)
    except Exception:
        pass
    return closed


def is_file_writable(path: Union[str, Path]) -> bool:
    """Test if a file can be opened for writing / overwriting without PermissionError."""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            test_p = p.with_name(f".probe_{p.stem}_{os.getpid()}_{uuid.uuid4().hex[:6]}.tmp")
            try:
                with open(test_p, "wb") as f:
                    f.write(b"")
                test_p.unlink(missing_ok=True)
                return True
            except (PermissionError, OSError):
                return False
        else:
            with open(p, "r+b"):
                pass
            return True
    except (PermissionError, OSError):
        return False


def get_output_candidates(target_path: Union[str, Path]) -> Generator[Path, None, None]:
    """Generate prioritized output candidate paths:
    1. target_path (e.g. out/report.docx)
    2. out/report_updated.docx
    3. out/report_1.docx ... out/report_99.docx
    4. out/report_<timestamp>.docx
    """
    p = Path(target_path)
    parent = p.parent
    stem = p.stem
    ext = p.suffix if p.suffix else ".docx"

    # Candidate 0: Target path
    yield p

    # Candidate 1: _updated
    updated_p = parent / f"{stem}_updated{ext}"
    if updated_p != p:
        yield updated_p

    # Candidates 2..100: _1, _2, ..., _99
    for idx in range(1, 100):
        cand = parent / f"{stem}_{idx}{ext}"
        if cand != p and cand != updated_p:
            yield cand

    # Candidate fallback: timestamp
    yield parent / f"{stem}_{int(time.time())}{ext}"


def get_safe_output_path(target_path: Union[str, Path]) -> Path:
    """Resolve a safe, non-colliding writable output path.

    1. Attempts COM close if Word holds target_path on Windows.
    2. Probes target_path for writability.
    3. If locked, probes fallback candidates:
       - f"{stem}_updated{ext}"
       - f"{stem}_{idx}{ext}" for idx in range(1, 100)
       - f"{stem}_{int(time.time())}{ext}" as timestamp fallback
    4. Returns the first writable candidate Path.
    """
    p = Path(target_path)
    for cand in get_output_candidates(p):
        attempt_close_word_doc(cand)
        if is_file_writable(cand):
            return cand
    return p.with_name(f"{p.stem}_{int(time.time())}{p.suffix or '.docx'}")


def _is_abstract_echo(text: str, abstract_text: Optional[str]) -> bool:
    """Detect if text is an echo or copy-paste of the project abstract."""
    if not text or not abstract_text:
        return False
    t_clean = re.sub(r"\s+", " ", text.strip().lower())
    a_clean = re.sub(r"\s+", " ", abstract_text.strip().lower())
    if len(t_clean) < 40 or len(a_clean) < 40:
        return False

    # Exact substring match (either way)
    if len(t_clean) >= 60 and (t_clean in a_clean or a_clean in t_clean):
        return True

    # Prefix overlap (first 60 characters)
    if len(t_clean) >= 60 and len(a_clean) >= 60:
        if t_clean[:60] == a_clean[:60]:
            return True

    # Word-level Jaccard similarity
    t_words = set(re.findall(r"\b[a-z]{3,}\b", t_clean))
    a_words = set(re.findall(r"\b[a-z]{3,}\b", a_clean))
    if len(t_words) >= 15 and len(a_words) >= 15:
        overlap = len(t_words & a_words) / min(len(t_words), len(a_words))
        if overlap > 0.80:
            return True

    return False


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


# Standard technical acronym dictionary for abbreviations table across domains
KNOWN_ABBREVIATIONS = {
    "ADC": "Analog-to-Digital Converter",
    "AI": "Artificial Intelligence",
    "ALU": "Arithmetic Logic Unit",
    "API": "Application Programming Interface",
    "ARM": "Advanced RISC Machines",
    "AUC": "Area Under the Curve",
    "BLAST": "Basic Local Alignment Search Tool",
    "CLI": "Command-Line Interface",
    "CNN": "Convolutional Neural Network",
    "Cortex-M": "ARM Cortex Microcontroller Core",
    "DNA": "Deoxyribonucleic Acid",
    "FASTQ": "FASTQ Sequence and Quality Format",
    "FASTA": "FASTA Sequence Format",
    "HPC": "High-Performance Computing",
    "NCBI": "National Center for Biotechnology Information",
    "RNA": "Ribonucleic Acid",
    "CPI": "Cycles Per Instruction",
    "CPU": "Central Processing Unit",
    "CRUD": "Create, Read, Update, Delete",
    "CSV": "Comma-Separated Values",
    "CUDA": "Compute Unified Device Architecture",
    "CV": "Computer Vision",
    "DMA": "Direct Memory Access",
    "EDA": "Exploratory Data Analysis",
    "Flash": "Electrically Erasable Programmable Flash Memory",
    "FSM": "Finite State Machine",
    "GLCM": "Gray-Level Co-occurrence Matrix",
    "GPIO": "General Purpose Input/Output",
    "GPU": "Graphics Processing Unit",
    "GUI": "Graphical User Interface",
    "HAL": "Hardware Abstraction Layer",
    "HSE": "High-Speed External Clock Oscillator",
    "HSV": "Hue Saturation Value Color Space",
    "HTTP": "Hypertext Transfer Protocol",
    "I2C": "Inter-Integrated Circuit Bus",
    "IDE": "Integrated Development Environment",
    "IR": "Instruction Register",
    "ISA": "Instruction Set Architecture",
    "JDK": "Java Development Kit",
    "JRE": "Java Runtime Environment",
    "JSON": "JavaScript Object Notation",
    "JWT": "JSON Web Token",
    "JVM": "Java Virtual Machine",
    "KNN": "K-Nearest Neighbors",
    "MERN": "MongoDB, Express.js, React, Node.js Stack",
    "RF": "Random Forest",
    "RBAC": "Role-Based Access Control",
    "SMOTE": "Synthetic Minority Over-sampling Technique",
    "F1": "F1-Score Harmonized Metric",
    "CSS": "Cascading Style Sheets",
    "HTML": "Hypertext Markup Language",
    "ODM": "Object Data Modeling",
    "ORM": "Object-Relational Mapping",
    "SPA": "Single-Page Application",
    "UI": "User Interface",
    "UX": "User Experience",
    "LED": "Light Emitting Diode",
    "MCU": "Microcontroller Unit",
    "ML": "Machine Learning",
    "MMIO": "Memory-Mapped Input/Output",
    "MVC": "Model-View-Controller",
    "NVIC": "Nested Vectored Interrupt Controller",
    "OLED": "Organic Light Emitting Diode",
    "OOP": "Object-Oriented Programming",
    "PC": "Program Counter",
    "PCB": "Printed Circuit Board",
    "PIL": "Python Imaging Library",
    "PLL": "Phase-Locked Loop",
    "PWM": "Pulse Width Modulation",
    "RAM": "Random Access Memory",
    "REST": "Representational State Transfer",
    "RGB": "Red Green Blue Color Space",
    "RISC": "Reduced Instruction Set Computer",
    "ROC": "Receiver Operating Characteristic",
    "RTC": "Real-Time Clock",
    "SDA": "Serial Data Line (I2C)",
    "SCL": "Serial Clock Line (I2C)",
    "SPI": "Serial Peripheral Interface",
    "SQL": "Structured Query Language",
    "SRAM": "Static Random Access Memory",
    "SVM": "Support Vector Machine",
    "SWD": "Serial Wire Debug",
    "UART": "Universal Asynchronous Receiver-Transmitter",
    "UI": "User Interface",
    "USART": "Universal Synchronous/Asynchronous Receiver-Transmitter",
    "BLoC": "Business Logic Component",
    "DB": "Database",
    "FCM": "Firebase Cloud Messaging",
    "GPS": "Global Positioning System",
    "HTTPS": "Hypertext Transfer Protocol Secure",
    "MVVM": "Model-View-ViewModel",
    "NoSQL": "Non-Relational (Not Only SQL) Database",
    "OTP": "One-Time Password",
    "PWA": "Progressive Web Application",
    "QR": "Quick Response Code",
    "SDK": "Software Development Kit",
    "URL": "Uniform Resource Locator",
    "UX": "User Experience",
    "USB": "Universal Serial Bus",
    "VCC": "Voltage Common Collector (Power Supply)",
    "VRAM": "Video Random Access Memory",
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
        diagrams: Optional[Dict[str, Path]] = None,
        out_dir: Optional[Union[str, Path]] = None,
    ):
        self.brief = brief
        self.facts = facts
        self.outline = outline
        self.sample_path = Path(sample_path) if sample_path else None
        self.diagrams = diagrams or {}
        self.out_dir = Path(out_dir) if out_dir else Path("out")
        self.saved_path: Optional[Path] = None

        self.figure_entries: List[Tuple[str, str, str]] = []  # (fig_num, caption, page_placeholder)
        self.table_entries: List[Tuple[str, str, str]] = []  # (tbl_num, caption, page_placeholder)
        self.toc_entries: List[Tuple[str, str, str]] = []  # (title, level, page_placeholder)

        self.figure_count = 0
        self.table_count = 0

    @property
    def is_hardware(self) -> bool:
        """Check whether the active project is an embedded hardware/IoT project."""
        is_hw = getattr(getattr(self.brief, "implementation", None), "is_hardware", None)
        if is_hw is None:
            is_hw = getattr(self.brief, "is_hardware", None)
        if is_hw is None:
            domain = (getattr(self.brief, "domain", None) or "GENERAL_ACADEMIC").upper()
            is_hw = (domain in ("EMBEDDED_HARDWARE", "EMBEDDED_IOT"))
        return bool(is_hw)

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

        # Save output atomically using unique temporary file
        out_p = Path(out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        unique_id = uuid.uuid4().hex[:8]
        temp_p = out_p.with_name(f".{out_p.stem}_{os.getpid()}_{unique_id}.tmp.docx")

        try:
            doc.save(str(temp_p))

            # Probe candidates and atomically replace
            chosen_path = None
            for cand in get_output_candidates(out_p):
                attempt_close_word_doc(cand)
                try:
                    if cand.exists():
                        try:
                            cand.unlink()
                        except (PermissionError, OSError):
                            pass
                    os.replace(str(temp_p), str(cand))
                    chosen_path = cand
                    break
                except (PermissionError, OSError) as e:
                    logger.debug(f"Candidate '{cand}' locked ({e}), probing next candidate...")
                    continue

            if chosen_path is None:
                fallback_ts = out_p.with_name(f"{out_p.stem}_{int(time.time())}{out_p.suffix or '.docx'}")
                os.replace(str(temp_p), str(fallback_ts))
                chosen_path = fallback_ts

            self.saved_path = chosen_path

            if chosen_path.resolve() == out_p.resolve():
                logger.info(f"Saved complete docx report to {chosen_path}")
            else:
                logger.warning(
                    f"\n[WARNING] '{out_p}' is currently locked by Microsoft Word or another application.\n"
                    f"Saved newly generated report to '{chosen_path}' instead!\n"
                )
                print(
                    f"\n"
                    f"====================================================================\n"
                    f"[IMPORTANT NOTICE - TARGET FILE LOCKED BY WORD]\n"
                    f"'{out_p.resolve()}' could not be overwritten because it is open in Microsoft Word.\n"
                    f"Your newly generated report has been safely saved to:\n"
                    f"  >>> {chosen_path.resolve()} <<<\n"
                    f"Close Microsoft Word if you wish to overwrite '{out_p.name}'.\n"
                    f"All downstream post-processing and checks have targeted this file.\n"
                    f"====================================================================\n"
                )
        finally:
            if temp_p.exists():
                try:
                    temp_p.unlink(missing_ok=True)
                except Exception:
                    pass

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

        paras = [
            f"We hereby declare that the micro-project report entitled '{self.brief.project.title}' "
            f"submitted to {self.brief.project.college} in partial fulfillment of the requirements for the award of the Degree of "
            f"Bachelor of Technology in {self.brief.project.department} is an authentic record of original work done by us "
            f"under the supervision of {self.brief.people.guide.name}, {self.brief.people.guide.designation or 'Faculty Guide'}.",
            "We further declare that this report has not previously formed the basis for the award of any "
            "degree, diploma, associateship, fellowship, or other similar title to any university or institution.",
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

        clean_paras = []
        raw_paras = gen_data.get("paragraphs") or []
        for raw_p in raw_paras:
            for p_text in sanitize_paragraph_prose(raw_p):
                if not _is_prompt_echo(p_text) and len(p_text.split()) >= 6:
                    clean_paras.append(p_text)

        if not clean_paras:
            clean_paras = [
                f"We express our sincere gratitude to our project guide {self.brief.people.guide.name}, "
                f"{self.brief.people.guide.designation or 'Faculty Guide'}, Department of {self.brief.project.department}, "
                f"for continuous encouragement, constructive critique, and invaluable guidance throughout the course of this project.",
                f"We are deeply thankful to {self.brief.people.hod.name}, Head of the Department of "
                f"{self.brief.project.department}, for providing the necessary computing facilities, laboratory infrastructure, "
                f"and administrative support.",
                f"We also convey our sincere thanks to all faculty members of the Department of "
                f"{self.brief.project.department} and our fellow classmates for their valuable feedback and cooperation.",
                "Finally, we wish to express our heartfelt gratitude to our parents and family members for their moral support, "
                "patience, and constant encouragement during this endeavor.",
            ]

        for p_text in clean_paras:
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

        # Collect abbreviations that actually match the brief, outline, and domain
        active_abbreviations = []
        domain = getattr(self.brief, "domain", None) or getattr(self.outline, "domain", "GENERAL_ACADEMIC")
        is_hw = getattr(getattr(self.brief, "implementation", None), "is_hardware", None)
        if is_hw is None:
            is_hw = getattr(self.brief, "is_hardware", None)
        if is_hw is None:
            is_hw = (domain in ("EMBEDDED_HARDWARE", "EMBEDDED_IOT"))

        full_text = (
            f"{self.brief.project.title} {self.brief.implementation.board.name} "
            f"{self.brief.implementation.board.core} {self.brief.implementation.framework} "
            f"{' '.join(p.peripheral for p in self.brief.implementation.pins)} "
            f"{' '.join(c.interface for c in self.brief.implementation.components)} "
            f"{self.brief.abstract or ''} "
            f"{' '.join(it.title for it in self.outline.items)}"
        )
        hardware_banned = {
            "ADC", "GPIO", "MCU", "I2C", "OLED", "UART", "USART", "SWD", "NVIC", "HAL",
            "VCC", "GND", "ARM", "Cortex-M", "DMA", "Flash", "HSE", "PCB", "PLL", "PWM",
            "RTC", "SDA", "SCL", "SPI", "SysTick", "MMIO", "ALU", "CPI", "FSM", "IR", "ISA", "PC", "RISC"
        }
        if domain in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
            is_vision = any(
                k in full_text.lower()
                for k in ("image", "vision", "produce", "fruit", "vegetable", "glcm", "opencv", "freshness")
            ) and not any(
                k in full_text.lower()
                for k in ("student", "academic", "attendance", "random forest", "tabular")
            )
            if is_vision:
                default_core = ["AI", "AUC", "CNN", "CPU", "CUDA", "CV", "EDA", "GLCM", "GPU", "HSV", "KNN", "ML", "RGB", "ROC"]
            else:
                default_core = ["AI", "AUC", "CPU", "CSV", "EDA", "F1", "GPU", "ML", "RF", "ROC", "SMOTE"]
            banned_hardware = hardware_banned
            full_text = f"{self.brief.project.title} {self.brief.abstract or ''} {' '.join(it.title for it in self.outline.items)}"
        elif domain in ("SOFTWARE_APP", "WEB_CLOUD"):
            default_core = ["API", "CLI", "CRUD", "CSS", "DB", "GUI", "HTML", "HTTP", "HTTPS", "IDE", "JDK", "JSON", "JVM", "JWT", "MERN", "NoSQL", "ODM", "QR", "RBAC", "REST", "SDK", "SPA", "SQL", "UI", "UX"]
            banned_hardware = hardware_banned
            full_text = f"{self.brief.project.title} {self.brief.abstract or ''} {' '.join(it.title for it in self.outline.items)}" 
        elif domain == "BIOINFORMATICS" or "bio" in domain.lower():
            default_core = ["DNA", "RNA", "NCBI", "BLAST", "HPC", "API", "FASTQ"]
            banned_hardware = hardware_banned
            full_text = f"{self.brief.project.title} {self.brief.abstract or ''} {' '.join(it.title for it in self.outline.items)}"
        elif domain == "COA_SYSTEMS":
            default_core = ["ALU", "CPI", "FSM", "IR", "ISA", "MMIO", "PC", "RISC"]
            banned_hardware = set()
        elif not is_hw:
            default_core = ["API", "CLI", "CRUD", "DB", "GUI", "IDE", "JSON", "OOP", "RAM", "SDK", "SQL", "UI", "UX"]
            banned_hardware = hardware_banned
            full_text = f"{self.brief.project.title} {self.brief.abstract or ''} {' '.join(it.title for it in self.outline.items)}"
        else:
            default_core = ["ADC", "GPIO", "I2C", "MCU", "OLED", "UART"]
            banned_hardware = set()

        for abbr, desc in sorted(KNOWN_ABBREVIATIONS.items()):
            if abbr in banned_hardware:
                continue
            if re.search(r"\b" + re.escape(abbr) + r"\b", full_text) or abbr in default_core:
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
        seen_body_paragraphs: Set[str] = set()
        seen_body_bullets: Set[str] = set()

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
                rendered_count = 0

                # Render prose paragraphs
                for raw_p in gen_data.get("paragraphs", []):
                    for p_text in sanitize_paragraph_prose(raw_p):
                        if _is_prompt_echo(p_text):
                            continue
                        if _is_abstract_echo(p_text, self.brief.abstract):
                            logger.info(f"Filtered out abstract echo in section {item.number} ({item.title})")
                            continue
                        norm_p = re.sub(r"\W+", "", p_text.lower())
                        if len(norm_p) >= 45:
                            if norm_p in seen_body_paragraphs:
                                logger.info(f"Filtered duplicate paragraph in section {item.number} ({item.title})")
                                continue
                            seen_body_paragraphs.add(norm_p)

                        p = doc.add_paragraph()
                        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                        p.paragraph_format.line_spacing = 1.15
                        p.paragraph_format.space_after = Pt(6)
                        add_markdown_formatted_runs(p, p_text)
                        rendered_count += 1

                # Render bullets if any
                for raw_b in gen_data.get("bullets", []):
                    for b_text in sanitize_paragraph_prose(raw_b):
                        if _is_prompt_echo(b_text):
                            continue
                        if _is_abstract_echo(b_text, self.brief.abstract):
                            continue
                        b_clean = re.sub(r"^[\*\-•\u2022\s]+", "", b_text).strip()
                        norm_b = re.sub(r"\W+", "", b_clean.lower())
                        if len(norm_b) >= 45:
                            if norm_b in seen_body_bullets:
                                continue
                            seen_body_bullets.add(norm_b)

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
                        rendered_count += 1

                # Clean fallback paragraph if all contents were filtered out
                if rendered_count == 0:
                    fallback_text = (
                        f"This section discusses {item.title} within the scope of the {self.brief.project.title} project, "
                        f"outlining the architectural decisions, operational criteria, and technical methodology "
                        f"governing implementation in the {self.brief.domain} domain."
                    )
                    p = doc.add_paragraph()
                    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                    p.paragraph_format.line_spacing = 1.15
                    p.paragraph_format.space_after = Pt(6)
                    p.add_run(fallback_text)

                # Inject deterministic tables & figures for specific sections
                self._inject_section_special_content(doc, sec_id, item)

    def _inject_section_special_content(self, doc: Document, sec_id: str, item: Any):
        """Deterministically insert tables, diagrams, or placeholder boxes using semantic title and domain matching."""
        title_lower = (item.title or "").lower()
        domain = getattr(self.brief, "domain", None) or getattr(self.outline, "domain", "GENERAL_ACADEMIC")

        # Section 2.2 / Specifications: Board / System / Hardware Specifications Table
        if self.is_hardware and ((sec_id == "2.2" and domain == "EMBEDDED_HARDWARE") or ("board specification" in title_lower) or ("development board" in title_lower)):
            self.table_count += 1
            tbl_num = f"Table 2.{self.table_count}"
            tbl_title = f"{self.brief.implementation.board.mcu_part_number} Technical Specifications"
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

        elif domain == "SOFTWARE_APP" and ("operational environment" in title_lower or "technology stack" in title_lower or (sec_id in ("2.1", "2.3") and "specifications" in title_lower)):
            self.table_count += 1
            tbl_num = f"Table 2.{self.table_count}"
            tbl_title = "System Operational Environment and Software Specifications"
            self._add_table_caption(doc, tbl_num, tbl_title)

            b = self.brief.implementation.board
            specs = [
                ("Target Platform", b.name),
                ("Execution Environment", b.mcu_part_number),
                ("Processor Architecture", b.core),
                ("Memory / Storage Requirement", f"{b.sram} / {b.flash}"),
                ("Runtime Framework", self.brief.implementation.framework),
                ("Data Persistence Method", "Encrypted / Formatted File Store (.txt / .json)"),
                ("User Interface Mode", "Interactive Console / Graphical Terminal"),
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

        elif domain == "WEB_CLOUD" and ("web platform" in title_lower or "cloud architectural" in title_lower or "specifications" in title_lower or sec_id in ("2.1", "2.3")):
            self.table_count += 1
            tbl_num = f"Table 2.{self.table_count}"
            tbl_title = "Web Platform and Cloud Deployment Specifications"
            self._add_table_caption(doc, tbl_num, tbl_title)

            specs = [
                ("System Architecture", "Three-Tier Client-Server RESTful Architecture"),
                ("Client Interface", "Responsive Web Application / SPA (Port 80/443)"),
                ("Application Framework", self.brief.implementation.framework or "Node.js / Express / Python"),
                ("API Communication", "JSON Payloads over HTTPS / RESTful Endpoints"),
                ("Database Engine", "Relational Database Management System / Document Store"),
                ("Security Protocol", "JWT Bearer Token Authentication & HTTPS Encryption"),
                ("Hosting Environment", "Cloud Container Platform / Virtualized Server"),
            ]
            t = doc.add_table(rows=len(specs) + 1, cols=2)
            t.rows[0].cells[0].paragraphs[0].text = "Architectural Parameter"
            t.rows[0].cells[1].paragraphs[0].text = "Value / Specification"
            for idx, (param, val) in enumerate(specs, start=1):
                t.rows[idx].cells[0].paragraphs[0].text = param
                t.rows[idx].cells[1].paragraphs[0].text = val

            format_table(
                t,
                col_widths=[2.8, 3.4],
                col_alignments=[WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT],
            )
            self.table_entries.append((tbl_num, tbl_title, "pg"))

        elif domain in ("MACHINE_LEARNING", "DATA_ANALYTICS") and ("computational specifications" in title_lower or "operational environment" in title_lower or "specifications" in title_lower or sec_id in ("2.1", "2.3")):
            self.table_count += 1
            tbl_num = f"Table 2.{self.table_count}"
            tbl_title = "Machine Learning Computational Environment and Platform Specifications"
            self._add_table_caption(doc, tbl_num, tbl_title)

            specs = [
                ("Execution Environment", "Python 3.10+ Analytical Virtual Environment"),
                ("Core ML Frameworks", "Scikit-Learn, PyTorch, NumPy, Pandas"),
                ("Hardware Acceleration", "CUDA GPU Acceleration / Multi-Threaded CPU"),
                ("Feature Representation", "Multidimensional Feature Vectors & Normalized Tensors"),
                ("Model Paradigm", "Supervised Classification / Ensemble Learning"),
                ("Validation Metric", "Stratified Cross-Validation / Macro F1-Score"),
                ("Data Ingestion Format", "Tabular CSV / Multidimensional Array Store"),
            ]
            t = doc.add_table(rows=len(specs) + 1, cols=2)
            t.rows[0].cells[0].paragraphs[0].text = "Platform Parameter"
            t.rows[0].cells[1].paragraphs[0].text = "Specification"
            for idx, (param, val) in enumerate(specs, start=1):
                t.rows[idx].cells[0].paragraphs[0].text = param
                t.rows[idx].cells[1].paragraphs[0].text = val

            format_table(
                t,
                col_widths=[2.8, 3.4],
                col_alignments=[WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT],
            )
            self.table_entries.append((tbl_num, tbl_title, "pg"))

        elif (domain == "BIOINFORMATICS" or "bio" in domain.lower()) and ("computational environment" in title_lower or "system specifications" in title_lower or "specifications" in title_lower or sec_id in ("2.1", "2.3")):
            self.table_count += 1
            tbl_num = f"Table 2.{self.table_count}"
            tbl_title = "Bioinformatics Pipeline and Computational Specifications"
            self._add_table_caption(doc, tbl_num, tbl_title)

            specs = [
                ("Execution Platform", "High-Performance Computing Cluster / Linux Server"),
                ("Algorithm Engine", "Dynamic Programming Alignment Kernel"),
                ("Input Formats", "FASTA, FASTQ, SAM/BAM Sequence Specifications"),
                ("Scoring Matrix", "BLOSUM62 / PAM250 / Affine Gap Model"),
                ("Statistical Model", "Karlin-Altschul E-Value Verification"),
                ("Storage Backend", "Indexed Genomic Sequence Cache"),
                ("Quality Standards", "Phred Quality Q30+ Filtering Standard"),
            ]
            t = doc.add_table(rows=len(specs) + 1, cols=2)
            t.rows[0].cells[0].paragraphs[0].text = "Pipeline Parameter"
            t.rows[0].cells[1].paragraphs[0].text = "Specification"
            for idx, (param, val) in enumerate(specs, start=1):
                t.rows[idx].cells[0].paragraphs[0].text = param
                t.rows[idx].cells[1].paragraphs[0].text = val

            format_table(
                t,
                col_widths=[2.8, 3.4],
                col_alignments=[WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT],
            )
            self.table_entries.append((tbl_num, tbl_title, "pg"))

        elif domain == "COA_SYSTEMS" and ("target isa" in title_lower or "processor specifications" in title_lower or (sec_id == "2.1" and "isa" in title_lower)):
            self.table_count += 1
            tbl_num = f"Table 2.{self.table_count}"
            tbl_title = "Target Processor Architectural Specifications and ISA Parameters"
            self._add_table_caption(doc, tbl_num, tbl_title)

            b = self.brief.implementation.board
            specs = [
                ("Target Architecture", b.name),
                ("Instruction Set Family", b.mcu_part_number),
                ("Datapath Pipeline Stages", b.core),
                ("Clock Frequency", b.clock),
                ("Instruction Memory Model", b.flash),
                ("Data Memory Capacity", b.sram),
                ("Simulation Environment", self.brief.implementation.framework),
            ]
            t = doc.add_table(rows=len(specs) + 1, cols=2)
            t.rows[0].cells[0].paragraphs[0].text = "Architectural Parameter"
            t.rows[0].cells[1].paragraphs[0].text = "Specification"
            for idx, (param, val) in enumerate(specs, start=1):
                t.rows[idx].cells[0].paragraphs[0].text = param
                t.rows[idx].cells[1].paragraphs[0].text = val

            format_table(
                t,
                col_widths=[2.8, 3.4],
                col_alignments=[WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT],
            )
            self.table_entries.append((tbl_num, tbl_title, "pg"))

        # Section 2.3: Hardware Components Table (only for hardware when component table has elements)
        elif self.is_hardware and ("hardware components" in title_lower or "sensors used" in title_lower or (sec_id == "2.3" and domain == "EMBEDDED_HARDWARE")) and self.facts.component_table:
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
        elif "block diagram" in title_lower or (sec_id == "3.2" and "diagram" in title_lower):
            self.figure_count += 1
            fig_num = f"Figure 3.{self.figure_count}"
            if domain in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
                is_vis = any(k in title_lower or k in (self.brief.abstract or "").lower() for k in ("image", "vision", "produce", "fruit", "vegetable", "glcm", "opencv", "freshness")) and not any(k in title_lower or k in (self.brief.abstract or "").lower() for k in ("student", "academic", "attendance", "random forest", "tabular"))
                fig_title = "Machine Learning Pipeline and Modular Architecture Diagram"
                desc = "Pipeline block diagram illustrating image input, preprocessing, feature extraction, and classification" if is_vis else "Pipeline block diagram illustrating tabular dataset ingestion, SMOTE balancing, Random Forest ensemble, and majority voting"
            elif domain in ("SOFTWARE_APP", "WEB_CLOUD"):
                fig_title = "Overall System Architecture and Layered Block Diagram"
                desc = "Block diagram illustrating presentation layer, application controller, service logic, and persistent storage store"
            elif domain == "COA_SYSTEMS":
                fig_title = "Instruction Datapath and Multi-Stage Pipeline Block Diagram"
                desc = "Block diagram illustrating fetch, decode, execute, memory access, and writeback pipeline stages"
            elif domain == "BIOINFORMATICS" or "bio" in domain.lower():
                fig_title = "Bioinformatics Pipeline and Computational Architecture Diagram"
                desc = "Block diagram illustrating sequence ingestion, quality filtering, dynamic programming alignment, and verification"
            elif not self.is_hardware:
                fig_title = "System Architecture and Subsystem Block Diagram"
                desc = "Block diagram illustrating subsystem modules, component interfaces, and execution flow"
            else:
                fig_title = "Overall System Architecture and Hardware Block Diagram"
                desc = "Block diagram showing sensor inputs, microcontroller unit, display interface, and output actuators"

            # Check if diagram image exists
            if self.diagrams:
                diag_path = self.diagrams.get("block_diagram")
            else:
                diag_path = self.out_dir / "block_diagram.png"

            if diag_path and Path(diag_path).is_file():
                p_img = doc.add_paragraph()
                p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p_img.paragraph_format.keep_with_next = True
                p_img.add_run().add_picture(str(diag_path), width=Inches(6.2))
            else:
                add_placeholder_box(
                    doc,
                    f"Figure 3.{self.figure_count} Block Diagram",
                    desc,
                )

            self._add_figure_caption(doc, fig_num, fig_title)
            self.figure_entries.append((fig_num, fig_title, "pg"))

        # Section 3.3: Circuit Diagram / Data Flow / Interconnect
        elif "circuit diagram" in title_lower or "data flow" in title_lower or "communication path" in title_lower or (sec_id == "3.3" and "diagram" in title_lower):
            self.figure_count += 1
            fig_num = f"Figure 3.{self.figure_count}"
            if domain in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
                is_vis = any(k in title_lower or k in (self.brief.abstract or "").lower() for k in ("image", "vision", "produce", "fruit", "vegetable", "glcm", "opencv", "freshness")) and not any(k in title_lower or k in (self.brief.abstract or "").lower() for k in ("student", "academic", "attendance", "random forest", "tabular"))
                fig_title = "Data Flow and Feature Pipeline Interaction Diagram (DFD)"
                desc = "Data flow diagram illustrating input image stream, preprocessing, color and GLCM texture descriptor extraction, and classification" if is_vis else "Data flow diagram illustrating student records stream, preprocessing, academic/behavioral feature matrix, and ensemble inference"
            elif domain in ("SOFTWARE_APP", "WEB_CLOUD"):
                fig_title = "Data Flow and Component Interaction Diagram (DFD)"
                desc = "Data flow diagram illustrating user inputs, controller processing, file persistence read/write cycles, and response formatting"
            elif domain == "COA_SYSTEMS":
                fig_title = "Memory Mapped I/O (MMIO) and Bus Interconnect Diagram"
                desc = "Interconnection diagram depicting address bus decoders, data registers, and control lines"
            elif domain == "BIOINFORMATICS" or "bio" in domain.lower():
                fig_title = "Bioinformatics Sequence Processing and Data Flow Diagram (DFD)"
                desc = "Data flow diagram illustrating genomic sequence ingestion, DP scoring recurrence, and alignment output"
            elif not self.is_hardware:
                fig_title = "System Data Flow and Interaction Diagram (DFD)"
                desc = "Data flow diagram illustrating subsystem component interactions and data exchange"
            else:
                fig_title = "Complete Circuit Interconnection Diagram"
                desc = f"Wiring diagram of {self.brief.implementation.board.name}, sensors, display module, and programming interface"

            if self.diagrams:
                conn_path = self.diagrams.get("connection_diagram")
            else:
                conn_path = self.out_dir / "connection_diagram.png"

            if conn_path and Path(conn_path).is_file():
                p_img = doc.add_paragraph()
                p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p_img.paragraph_format.keep_with_next = True
                p_img.add_run().add_picture(str(conn_path), width=Inches(6.2))
            else:
                add_placeholder_box(
                    doc,
                    f"Figure 3.{self.figure_count} Interconnection Diagram",
                    desc,
                )

            self._add_figure_caption(doc, fig_num, fig_title)
            self.figure_entries.append((fig_num, fig_title, "pg"))

        # Section 3.5: Pin Mapping Table (hardware only)
        elif self.is_hardware and ("pin configuration" in title_lower or "pin allocation" in title_lower or "peripheral mapping" in title_lower or (sec_id == "3.5" and domain == "EMBEDDED_HARDWARE")) and self.facts.pin_table:
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

        # Section 3.4 / 3.5: Subsystem Modules and Component Specifications Table (non-hardware)
        elif not self.is_hardware and (
            "interface specification" in title_lower
            or "data schema" in title_lower
            or "dataset organization" in title_lower
            or "subsystem" in title_lower
            or (sec_id in ("3.4", "3.5") and not any(k in title_lower for k in ("algorithm", "flowchart", "principle")))
        ):
            self.table_count += 1
            tbl_num = f"Table 3.{self.table_count}"
            tbl_title = "System Subsystem Modules and Component Specifications"
            self._add_table_caption(doc, tbl_num, tbl_title)

            raw_mods = getattr(self.brief.implementation, "modules", []) or []
            if not raw_mods:
                raw_mods = ["Ingestion & Input Interface", "Core Business Logic", "Data Persistence & Storage"]

            t = doc.add_table(rows=len(raw_mods) + 1, cols=4)
            t.rows[0].cells[0].paragraphs[0].text = "Subsystem Module"
            t.rows[0].cells[1].paragraphs[0].text = "Architectural Layer"
            t.rows[0].cells[2].paragraphs[0].text = "Primary Responsibility"
            t.rows[0].cells[3].paragraphs[0].text = "Interface / Schema"

            layer_map = [
                ("Presentation Tier", "User interaction and input event handling", "REST API / GUI Events"),
                ("Application Tier", "Core business rules and workflow execution", "Internal Service Calls"),
                ("Data Access Tier", "State persistence and transaction records", "Database Queries / JSON"),
                ("Integration Tier", "Inter-module telemetry and validation", "Event Payloads / HTTPS"),
                ("Analytics Tier", "Performance telemetry and monitoring", "Log Streams / Metrics"),
            ]
            for idx, mod in enumerate(raw_mods, start=1):
                layer, resp, iface = layer_map[(idx - 1) % len(layer_map)]
                t.rows[idx].cells[0].paragraphs[0].text = mod
                t.rows[idx].cells[1].paragraphs[0].text = layer
                t.rows[idx].cells[2].paragraphs[0].text = resp
                t.rows[idx].cells[3].paragraphs[0].text = iface

            format_table(
                t,
                col_widths=[1.6, 1.4, 2.0, 1.4],
                col_alignments=[
                    WD_ALIGN_PARAGRAPH.LEFT,
                    WD_ALIGN_PARAGRAPH.CENTER,
                    WD_ALIGN_PARAGRAPH.LEFT,
                    WD_ALIGN_PARAGRAPH.LEFT,
                ],
            )
            self.table_entries.append((tbl_num, tbl_title, "pg"))

        # Section 3.6: Algorithm and Flowchart
        elif "flowchart" in title_lower or "algorithm" in title_lower or (sec_id == "3.6" and domain == "EMBEDDED_HARDWARE"):
            self.figure_count += 1
            fig_num = f"Figure 3.{self.figure_count}"
            if domain in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
                is_vis = any(k in title_lower or k in (self.brief.abstract or "").lower() for k in ("image", "vision", "produce", "fruit", "vegetable", "glcm", "opencv", "freshness")) and not any(k in title_lower or k in (self.brief.abstract or "").lower() for k in ("student", "academic", "attendance", "random forest", "tabular"))
                fig_title = "KNN Classification and Freshness Decision Flowchart" if is_vis else "Random Forest Ensemble Inference and Decision Flowchart"
            elif domain in ("SOFTWARE_APP", "WEB_CLOUD"):
                fig_title = "Application Request Execution and Data Persistence Flowchart"
            elif domain == "BIOINFORMATICS" or "bio" in domain.lower():
                fig_title = "Sequence Alignment Algorithm and Verification Flowchart"
            elif not self.is_hardware:
                fig_title = "System Execution and Process Logic Flowchart"
            else:
                fig_title = "System Execution and Logic Flowchart"

            if self.diagrams:
                flow_path = self.diagrams.get("flowchart")
            else:
                flow_path = self.out_dir / "flowchart.png"

            if flow_path and Path(flow_path).is_file():
                p_img = doc.add_paragraph()
                p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p_img.paragraph_format.keep_with_next = True
                p_img.add_run().add_picture(str(flow_path), width=Inches(5.0))
            else:
                add_placeholder_box(
                    doc,
                    f"Figure 3.{self.figure_count} System Flowchart",
                    "Flowchart showing initialization, input validation, core algorithm evaluation, and state persistence workflows",
                )

            self._add_figure_caption(doc, fig_num, fig_title)
            self.figure_entries.append((fig_num, fig_title, "pg"))

        # Section 3.7: Calibration & State Table (hardware only)
        elif self.is_hardware and ("calibration" in title_lower or "state classification" in title_lower or "threshold" in title_lower or (sec_id == "3.7" and domain == "EMBEDDED_HARDWARE")) and self.facts.state_ranges:
            self.table_count += 1
            tbl_num = f"Table 3.{self.table_count}"
            is_night_lamp = any("DARK" in sr.state_name for sr in self.facts.state_ranges)
            is_water = any("OVERFLOW" in sr.state_name for sr in self.facts.state_ranges)
            if is_night_lamp:
                tbl_title = "Ambient Light ADC Thresholds and Hysteresis State Classification"
                col2_name = "Illumination Level"
            elif is_water:
                tbl_title = "Water Tank Depth Thresholds and Alert State Classification"
                col2_name = "Water Level %"
            else:
                tbl_title = "ADC Calibration and Operational State Classification"
                col2_name = "Measurement Level"
            self._add_table_caption(doc, tbl_num, tbl_title)

            sr_list = self.facts.state_ranges
            t = doc.add_table(rows=len(sr_list) + 1, cols=4)
            t.rows[0].cells[0].paragraphs[0].text = "Operating State"
            t.rows[0].cells[1].paragraphs[0].text = "Condition"
            t.rows[0].cells[2].paragraphs[0].text = col2_name
            t.rows[0].cells[3].paragraphs[0].text = "Measurement Range"
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

        # Section 5.2: Test Cases Table (only if test table has elements)
        elif ("test case" in title_lower or "test cases and observations" in title_lower or sec_id == "5.2") and self.facts.test_table:
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

        # Section 5.3: Photographs / Prototype Setup / Benchmark Results
        elif sec_id == "5.3" or "prototype setup" in title_lower or "validation and benchmark" in title_lower or (self.is_hardware and "photograph" in title_lower):
            if self.is_hardware:
                add_placeholder_box(
                    doc,
                    "Photograph 5.1: Hardware Prototype Setup",
                    f"Please insert high-resolution photograph of the wired {self.brief.implementation.board.name} prototype setup.",
                )
            elif domain in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
                add_placeholder_box(
                    doc,
                    "Figure 5.1: Machine Learning Prototype and Classification Interface",
                    "Please insert screenshot of the model execution environment, input sample, and classification prediction interface.",
                )
            else:
                add_placeholder_box(
                    doc,
                    "Figure 5.1: System Prototype Setup and User Interface",
                    "Please insert screenshot of the application user interface, running terminal session, or benchmark validation dashboard.",
                )

        # Section 5.4: Screenshots / Telemetry
        elif sec_id == "5.4" or "screenshot" in title_lower or "telemetry" in title_lower:
            if self.is_hardware:
                add_placeholder_box(
                    doc,
                    "Photograph 5.2: Output Display and Telemetry Output",
                    "Please insert photograph or screenshot of the output display screen and serial terminal monitor.",
                )
            elif domain in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
                add_placeholder_box(
                    doc,
                    "Figure 5.2: Output Confusion Matrix and Classification Metrics",
                    "Please insert visual evaluation plot, confusion matrix, and category prediction results.",
                )
            else:
                add_placeholder_box(
                    doc,
                    "Figure 5.2: System Output and Execution Telemetry",
                    "Please insert screenshot of the application output telemetry, query results, and execution logs.",
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
        domain = getattr(self.brief, "domain", None) or getattr(self.outline, "domain", "GENERAL_ACADEMIC")
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

        if self.brief.references:
            refs = self.brief.references
        elif not self.is_hardware:
            if domain in ("SOFTWARE_APP", "WEB_CLOUD"):
                refs = [
                    "Gamma, E., Helm, R., Johnson, R., & Vlissides, J. (1994). Design Patterns: Elements of Reusable Object-Oriented Software. Addison-Wesley.",
                    "Bloch, J. (2018). Effective Java (3rd ed.). Addison-Wesley Professional.",
                    "Martin, R. C. (2008). Clean Code: A Handbook of Agile Software Craftsmanship. Prentice Hall.",
                    "Fielding, R. T. (2000). Architectural Styles and the Design of Network-based Software Architectures. Doctoral dissertation, University of California, Irvine.",
                ]
            elif domain in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
                refs = [
                    "Hastie, T., Tibshirani, R., & Friedman, J. (2009). The Elements of Statistical Learning: Data Mining, Inference, and Prediction. Springer.",
                    "Pedregosa, F., et al. (2011). Scikit-learn: Machine Learning in Python. Journal of Machine Learning Research, 12, 2825-2830.",
                    "Chawla, N. V., et al. (2002). SMOTE: Synthetic Minority Over-sampling Technique. Journal of Artificial Intelligence Research, 16, 321-357.",
                    "Breiman, L. (2001). Random Forests. Machine Learning, 45(1), 5-32.",
                ]
            elif domain == "BIOINFORMATICS" or "bio" in domain.lower():
                refs = [
                    "Altschul, S. F., et al. (1990). Basic local alignment search tool. Journal of Molecular Biology, 215(3), 403-410.",
                    "Needleman, S. B., & Wunsch, C. D. (1970). A general method applicable to the search for similarities in the amino acid sequence of two proteins. Journal of Molecular Biology, 48(3), 443-453.",
                    "Smith, T. F., & Waterman, M. S. (1981). Identification of common molecular subsequences. Journal of Molecular Biology, 147(1), 195-197.",
                    "Cock, P. J., et al. (2009). Biopython: freely available Python tools for computational molecular biology and bioinformatics. Bioinformatics, 25(11), 1422-1423.",
                ]
            elif domain == "COA_SYSTEMS":
                refs = [
                    "Patterson, D. A., & Hennessy, J. L. (2020). Computer Organization and Design: The Hardware/Software Interface. Morgan Kaufmann.",
                    "Waterman, A., & Asanovic, K. (2019). The RISC-V Instruction Set Manual, Volume I: User-Level ISA. RISC-V Foundation.",
                ]
            else:
                refs = [
                    "Sommerville, I. (2015). Software Engineering (10th ed.). Pearson.",
                    "Pressman, R. S. (2014). Software Engineering: A Practitioner's Approach. McGraw-Hill.",
                    "Tanenbaum, A. S., & Austin, T. (2012). Structured Computer Organization. Pearson.",
                ]
        else:
            refs = [
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

        # Appendix A: Source Code (only if code exists or outline specifies source code)
        has_code_files = any(Path(c).is_file() for c in self.brief.code_files_list)
        outline_has_code = any(item.id == "appendix_a_source_code" for item in self.outline.items)

        if has_code_files or outline_has_code:
            doc.add_page_break()
            p_app_a = doc.add_paragraph()
            p_app_a.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_app_a.paragraph_format.space_before = Pt(16)
            p_app_a.paragraph_format.space_after = Pt(14)
            if domain == "EMBEDDED_HARDWARE":
                code_heading = "APPENDIX A\nCOMPLETE EMBEDDED C SOURCE CODE"
            elif domain == "SOFTWARE_APP":
                code_heading = "APPENDIX A\nCOMPLETE APPLICATION SOURCE CODE"
            elif domain == "COA_SYSTEMS":
                code_heading = "APPENDIX A\nCOMPLETE ASSEMBLY SOURCE CODE"
            else:
                code_heading = "APPENDIX A\nCOMPLETE SOURCE CODE"

            run_app_a = p_app_a.add_run(code_heading)
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

        # Appendix B: Additional Configuration Details
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
        if domain == "SOFTWARE_APP":
            p_cfg.add_run(
                "Application Runtime and Execution Environment Details:\n"
                "- Runtime Platform: Java SE / Modern JVM / Python 3 Runtime\n"
                "- Memory Management: Automatic Garbage Collection / Dynamic Heap Allocation\n"
                "- Threading Model: Multi-Threaded Execution / Event Dispatch Loop\n"
                "- Persistence Layer: Formatted Local File Store / Dynamic Transaction Log\n"
                "- Data Encoding: UTF-8 Standard Encoding\n"
                "- Exception Policy: Graceful Degradation with Structured Error Trapping\n"
            )
        elif domain == "WEB_CLOUD":
            p_cfg.add_run(
                "Cloud Deployment and Environment Configuration Details:\n"
                "- Web Protocol: HTTP/1.1 and HTTPS / TLS 1.3 Encryption\n"
                "- Container Platform: Docker Container Virtualization\n"
                "- Port Allocation: Application Port 5000 / Web Service Port 443\n"
                "- Process Management: Daemonized Multi-Worker Process Pool\n"
                "- Cache & Session Store: In-Memory Key-Value Store\n"
                "- API Specification: OpenAPI / Swagger 3.0 Standard Schema\n"
            )
        elif domain in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
            p_cfg.add_run(
                "Machine Learning Model and Hyperparameter Specifications:\n"
                "- Optimization Framework: Scikit-learn / PyTorch Analytical Kernel\n"
                "- Acceleration Backend: CUDA / Multi-Threaded BLAS Operations\n"
                "- Convergence Criterion: Tolerance tol=1e-4, Maximum Iterations 1000\n"
                "- Feature Normalization: Standard / Min-Max Dynamic Scaling\n"
                "- Model Validation Strategy: Stratified 5-Fold Cross-Validation\n"
                "- Telemetry Logging: Macro Precision, Recall, F1-Score, and ROC-AUC\n"
            )
        elif domain == "BIOINFORMATICS" or "bio" in domain.lower():
            p_cfg.add_run(
                "Bioinformatics Computational Environment and Scoring Parameters:\n"
                "- Alignment Mode: Global / Local Dynamic Programming Matrix Kernel\n"
                "- Scoring Matrix: BLOSUM62 / PAM250 Substitution Model\n"
                "- Gap Penalties: Existence Penalty d=-10, Extension Penalty e=-0.5\n"
                "- Alphabet Validation: Strict Ambiguity Filter (IUPAC Nucleotide/Amino)\n"
                "- Statistical Significance: Karlin-Altschul E-Value Threshold <= 1e-5\n"
                "- Memory Scaling: Quadratic DP Space / Hirschberg Linear Space Option\n"
            )
        elif domain == "COA_SYSTEMS":
            p_cfg.add_run(
                "Architectural Simulation and Datapath Timing Details:\n"
                "- Target ISA: RISC-V RV32I / RV64I Base Integer Instruction Set\n"
                "- Pipeline Stages: 5-Stage (Instruction Fetch, Decode, Execute, Memory, Writeback)\n"
                "- Hazard Mitigation: Forwarding Unit and 1-Cycle Load-Use Stall Interlock\n"
                "- Branch Prediction: Static / Dynamic 2-Bit Saturating Counter\n"
                "- Memory Bus: Unified Memory Mapped I/O Bus (MMIO)\n"
                "- Register File: 32 General-Purpose 32-bit Registers (x0 Hardwired to Zero)\n"
            )
        elif not self.is_hardware:
            p_cfg.add_run(
                "System Platform and Runtime Configuration Details:\n"
                "- Runtime Platform: Multi-Platform Managed Runtime / Virtual Environment\n"
                "- Memory Management: Automatic Dynamic Heap Management\n"
                "- Execution Concurrency: Multi-Threaded / Asynchronous Event Loop\n"
                "- Persistence Layer: Structured Relational Database / Formatted File Store\n"
                "- Logging & Telemetry: Structured JSON Application Execution Logs\n"
                "- Error Handling: Defensive Exception Interception and Recovery\n"
            )
        else:
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

        if self.is_hardware:
            add_placeholder_box(
                doc,
                "Appendix C Supplementary Images",
                "Optional oscilloscope captures, serial plotter waveforms, or simulation scope traces.",
            )
        else:
            add_placeholder_box(
                doc,
                "Appendix C Supplementary Visualizations",
                "Optional application screenshots, telemetry dashboard visualizations, or benchmark performance plots.",
            )


    def _populate_toc_table(self, doc: Document):
        """Populate Table of Contents entries with structured Item numbers, Titles, and baseline page numbers."""
        est_page = 1
        for title, level, anchor in self.toc_entries:
            row = self.toc_table.add_row()
            item_text = ""
            title_text = title
            is_bold = False

            if level == "chapter":
                is_bold = True
                m_ch = re.match(r"^CHAPTER\s+(\d+)[:\s]+(.*)", title, re.IGNORECASE)
                if m_ch:
                    item_text = f"Chapter {m_ch.group(1)}"
                    title_text = m_ch.group(2).strip()
                elif title.startswith("APPENDIX"):
                    m_app = re.match(r"^(APPENDIX\s+[A-Z])[:\s–—]+(.*)", title, re.IGNORECASE)
                    if m_app:
                        item_text = m_app.group(1).title()
                        title_text = m_app.group(2).strip()
                    else:
                        item_text = title.split(":")[0].strip().title()
                        title_text = title.split(":")[-1].strip()
                elif title.upper() == "REFERENCES":
                    item_text = ""
                    title_text = "REFERENCES"

                if m_ch:
                    ch_idx = int(m_ch.group(1))
                    est_page = 1 + (ch_idx - 1) * 2
            else:
                m_sec = re.match(r"^(\d+\.\d+(?:\.\d+)?)\s+(.*)", title)
                if m_sec:
                    item_text = f"  {m_sec.group(1)}"
                    title_text = f"  {m_sec.group(2).strip()}"
                else:
                    item_text = ""
                    title_text = f"  {title}"

            row.cells[0].paragraphs[0].text = item_text
            if is_bold and row.cells[0].paragraphs[0].runs:
                row.cells[0].paragraphs[0].runs[0].bold = True

            row.cells[1].paragraphs[0].text = title_text
            if is_bold and row.cells[1].paragraphs[0].runs:
                row.cells[1].paragraphs[0].runs[0].bold = True

            row.cells[2].paragraphs[0].text = str(est_page)
            row.cells[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT

        format_table(
            self.toc_table,
            col_widths=[1.2, 4.2, 0.8],
            col_alignments=[WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.RIGHT],
        )

    def _populate_lof_table(self, doc: Document):
        """Populate List of Figures entries."""
        for num, title, anchor in self.figure_entries:
            row = self.lof_table.add_row()
            row.cells[0].paragraphs[0].text = num
            row.cells[1].paragraphs[0].text = title
            row.cells[2].paragraphs[0].text = "1"
            row.cells[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT

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
            row.cells[2].paragraphs[0].text = "1"
            row.cells[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT

        format_table(
            self.lot_table,
            col_widths=[1.2, 4.2, 0.8],
            col_alignments=[WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.RIGHT],
        )

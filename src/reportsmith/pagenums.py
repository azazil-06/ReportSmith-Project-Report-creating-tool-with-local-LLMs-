"""Page number resolver using native Word COM (on Windows), LibreOffice (on Linux/Mac), and pypdf."""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from docx.enum.text import WD_ALIGN_PARAGRAPH
from pypdf import PdfReader

logger = logging.getLogger(__name__)

COMMON_SOFFICE_PATHS = [
    Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
    Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
]


def find_libreoffice() -> Optional[Path]:
    """Find LibreOffice soffice executable on system."""
    which_path = shutil.which("soffice")
    if which_path:
        return Path(which_path)

    for p in COMMON_SOFFICE_PATHS:
        if p.is_file():
            return p
    return None


def int_to_roman(n: int) -> str:
    """Convert integer to lowercase roman numeral."""
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    syb = ["m", "cm", "d", "cd", "c", "xc", "l", "xl", "x", "ix", "v", "iv", "i"]
    roman_num = ""
    i = 0
    while n > 0:
        for _ in range(n // val[i]):
            roman_num += syb[i]
            n -= val[i]
        i += 1
    return roman_num


def convert_docx_to_pdf(docx_path: Path, pdf_path: Path) -> bool:
    """Convert docx to PDF using either Microsoft Word (on Windows) or LibreOffice."""
    # 1. Native Microsoft Word COM on Windows (highest fidelity)
    if os.name == "nt":
        try:
            import win32com.client
            word = win32com.client.Dispatch("Word.Application")
            word.Visible = False
            word.DisplayAlerts = False
            try:
                doc = word.Documents.Open(str(docx_path.resolve()))
                doc.SaveAs(str(pdf_path.resolve()), FileFormat=17)  # wdFormatPDF = 17
                doc.Close(False)
                if pdf_path.is_file() and pdf_path.stat().st_size > 1000:
                    logger.info("Exported PDF via native Microsoft Word COM.")
                    return True
            finally:
                word.Quit()
        except Exception as e:
            logger.debug(f"Word COM conversion failed: {e}")

        # Try PowerShell COM fallback if pywin32 is not installed
        try:
            ps_cmd = (
                f"$w = New-Object -ComObject Word.Application; $w.Visible = $false; $w.DisplayAlerts = $false; "
                f"$d = $w.Documents.Open('{docx_path.resolve()}'); "
                f"$d.SaveAs('{pdf_path.resolve()}', 17); "
                f"$d.Close($false); $w.Quit()"
            )
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, timeout=60)
            if res.returncode == 0 and pdf_path.is_file() and pdf_path.stat().st_size > 1000:
                logger.info("Exported PDF via PowerShell Word COM.")
                return True
        except Exception as e:
            logger.debug(f"PowerShell Word COM conversion failed: {e}")

    # 2. LibreOffice headless fallback
    soffice_exe = find_libreoffice()
    if soffice_exe:
        cmd = [
            str(soffice_exe),
            "--headless",
            "--convert-to",
            "pdf",
            str(docx_path),
            "--outdir",
            str(pdf_path.parent),
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
            if res.returncode == 0 and pdf_path.is_file():
                logger.info(f"Exported PDF via LibreOffice at: {soffice_exe}")
                return True
        except Exception as e:
            logger.debug(f"LibreOffice execution failed: {e}")

    return False


def run_pagenums_pass(docx_path: Union[str, Path], builder: Any = None, max_passes: int = 2) -> bool:
    """Resolve exact page numbers by rendering via Microsoft Word or LibreOffice, extracting text per page with pypdf,
    and updating Table of Contents, List of Figures, and List of Tables.
    """
    docx_file = Path(docx_path).resolve()
    if not docx_file.is_file():
        logger.warning(f"Docx file not found: {docx_file}")
        return False

    pdf_file = docx_file.with_suffix(".pdf")
    
    converted = convert_docx_to_pdf(docx_file, pdf_file)
    if not converted or not pdf_file.is_file():
        logger.info("PDF conversion tool not available; baseline estimated page numbers retained.")
        return False

    try:
        reader = PdfReader(str(pdf_file))
        total_pages = len(reader.pages)
        logger.info(f"Rendered PDF has {total_pages} pages.")

        page_texts: List[str] = [(p.extract_text() or "") for p in reader.pages]

        # Identify boundary: where Chapter 1 starts (strictly after preliminary pages)
        past_prelims = False
        body_start_pdf_page = 1
        for idx, text in enumerate(page_texts, start=1):
            if any(k in text.upper() for k in ("LIST OF ABBREVIATIONS", "CONTENTS", "TABLE OF CONTENTS")):
                past_prelims = True
            elif past_prelims and "CHAPTER 1" in text.upper():
                body_start_pdf_page = idx
                break

        if body_start_pdf_page == 1:
            for idx, text in enumerate(page_texts, start=1):
                if "CHAPTER 1" in text.upper() and not any(k in text.upper() for k in ("ITEM", "PAGE NO", "TITLE")):
                    body_start_pdf_page = idx
                    break

        logger.info(f"Body pages (Chapter 1) start at physical PDF page {body_start_pdf_page}")

        def get_display_page(pdf_page_num: int) -> str:
            if pdf_page_num < body_start_pdf_page:
                roman_idx = pdf_page_num - 1
                return int_to_roman(roman_idx) if roman_idx >= 1 else ""
            else:
                arabic_num = pdf_page_num - body_start_pdf_page + 1
                return str(arabic_num)

        import docx
        doc = docx.Document(str(docx_file))

        for table in doc.tables:
            first_row_text = [c.text.strip() for c in table.rows[0].cells]
            if len(first_row_text) == 3 and any(w in first_row_text[2] for w in ("Page", "Page No", "Page No.")):
                # 1. Table of Contents
                if "Item" in first_row_text[0] or "Title" in first_row_text[1]:
                    last_p = body_start_pdf_page
                    for row in table.rows[1:]:
                        t_item = row.cells[0].text.strip()
                        t_title = row.cells[1].text.strip()
                        full_str = f"{t_item} {t_title}".strip()
                        matched_page = None

                        m_ch = re.search(r"Chapter\s+(\d+)", t_item, re.I) or re.search(r"CHAPTER\s+(\d+)", t_title, re.I)
                        m_sec = re.search(r"(\d+\.\d+)", t_item) or re.search(r"(\d+\.\d+)", t_title)

                        for p_idx in range(last_p, total_pages + 1):
                            txt = page_texts[p_idx - 1]
                            if m_ch:
                                ch_num = m_ch.group(1)
                                if re.search(r"\bCHAPTER\s+" + ch_num + r"\b[\s\n]+[A-Z]", txt, re.I):
                                    matched_page = p_idx
                                    break
                            elif m_sec:
                                num = m_sec.group(1)
                                if re.search(r"\b" + re.escape(num) + r"[\s\t\n]+[A-Z]", txt):
                                    matched_page = p_idx
                                    break
                            elif "REFERENCES" in full_str.upper():
                                if re.search(r"\bREFERENCES\b[\s\n]+\[", txt):
                                    matched_page = p_idx
                                    break
                            elif "APPENDIX" in full_str.upper():
                                m_app = re.search(r"APPENDIX\s+([A-Z])", full_str, re.I)
                                if m_app and re.search(r"\bAPPENDIX\s+" + m_app.group(1) + r"\b", txt, re.I):
                                    matched_page = p_idx
                                    break

                        if matched_page:
                            last_p = matched_page
                            row.cells[2].paragraphs[0].text = get_display_page(matched_page)
                            row.cells[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT

                # 2. List of Figures
                elif any(w in first_row_text[0] for w in ("Figure", "Figure No", "Figure No.")):
                    last_p = body_start_pdf_page
                    for row in table.rows[1:]:
                        fig_str = row.cells[0].text.strip()
                        clean_num = fig_str.replace("Figure", "").strip()
                        matched_page = None
                        for p_idx in range(last_p, total_pages + 1):
                            txt = page_texts[p_idx - 1]
                            if re.search(r"Figure\s+" + re.escape(clean_num) + r"\b", txt, re.I):
                                matched_page = p_idx
                                break
                        if matched_page:
                            last_p = matched_page
                            row.cells[2].paragraphs[0].text = get_display_page(matched_page)
                            row.cells[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT

                # 3. List of Tables
                elif any(w in first_row_text[0] for w in ("Table", "Table No", "Table No.")):
                    last_p = body_start_pdf_page
                    for row in table.rows[1:]:
                        tbl_str = row.cells[0].text.strip()
                        clean_num = tbl_str.replace("Table", "").strip()
                        matched_page = None
                        for p_idx in range(last_p, total_pages + 1):
                            txt = page_texts[p_idx - 1]
                            if re.search(r"Table\s+" + re.escape(clean_num) + r"\b", txt, re.I):
                                matched_page = p_idx
                                break
                        if matched_page:
                            last_p = matched_page
                            row.cells[2].paragraphs[0].text = get_display_page(matched_page)
                            row.cells[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT

        from reportsmith.docx_build import attempt_close_word_doc, get_safe_output_path
        attempt_close_word_doc(docx_file)
        saved_docx = docx_file
        try:
            doc.save(str(docx_file))
        except PermissionError:
            saved_docx = get_safe_output_path(docx_file)
            doc.save(str(saved_docx))
            if builder:
                builder.saved_path = saved_docx
            logger.warning(f"Target '{docx_file}' locked during page number pass. Saved to '{saved_docx}'.")

        # Re-convert final DOCX with populated page numbers to PDF
        convert_docx_to_pdf(saved_docx, pdf_file)
        print(f"\n[PAGE NUMBERS] Successfully synchronized page numbers in Table of Contents, List of Figures, and List of Tables!")
        print(f"               Updated Word report: {saved_docx}")
        print(f"               Exported PDF report: {pdf_file}")
        return True

    except Exception as e:
        logger.warning(f"Error during page number resolution: {e}")
        return False

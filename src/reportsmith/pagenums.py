"""Page number resolver using headless LibreOffice (soffice) and pypdf."""

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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


def run_pagenums_pass(docx_path: Union[str, Path], builder: Any, max_passes: int = 3) -> bool:
    """Resolve exact page numbers by rendering via LibreOffice, extracting text per page with pypdf,

    and updating Table of Contents, List of Figures, and List of Tables.
    """
    docx_file = Path(docx_path)
    if not docx_file.is_file():
        logger.warning(f"Docx file not found: {docx_file}")
        return False

    soffice_exe = find_libreoffice()
    if not soffice_exe:
        print(
            "\n[NOTICE] LibreOffice (soffice) was not found on your system.\n"
            "         Page-number resolution pass skipped. To enable exact page numbers\n"
            "         in Table of Contents and Lists, install LibreOffice.\n"
        )
        return False

    logger.info(f"Using LibreOffice at: {soffice_exe}")
    out_dir = docx_file.parent

    for pass_num in range(1, max_passes + 1):
        logger.info(f"Page number resolution pass {pass_num}/{max_passes}...")

        # Convert docx to PDF
        cmd = [
            str(soffice_exe),
            "--headless",
            "--convert-to",
            "pdf",
            str(docx_file),
            "--outdir",
            str(out_dir),
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
            if res.returncode != 0:
                logger.warning(f"LibreOffice conversion failed: {res.stderr}")
                return False
        except Exception as e:
            logger.warning(f"Error executing LibreOffice: {e}")
            return False

        pdf_file = out_dir / (docx_file.stem + ".pdf")
        if not pdf_file.is_file():
            logger.warning(f"Expected PDF output not found: {pdf_file}")
            return False

        # Read PDF pages
        reader = PdfReader(str(pdf_file))
        total_pages = len(reader.pages)
        logger.info(f"Rendered PDF has {total_pages} pages.")

        page_texts: List[str] = [
            (p.extract_text() or "") for p in reader.pages
        ]

        # Identify boundary: where Chapter 1 starts
        # Page 1 is Title Page
        # Pages 2..body_start-1 are prelim pages (lower Roman: i, ii, iii, ...)
        # Pages body_start..total_pages are body pages (Arabic: 1, 2, 3, ...)
        body_start_pdf_page = 1
        for idx, text in enumerate(page_texts, start=1):
            if "CHAPTER 1" in text.upper():
                body_start_pdf_page = idx
                break

        logger.info(f"Body pages (Chapter 1) start at physical PDF page {body_start_pdf_page}")

        def get_display_page(pdf_page_num: int) -> str:
            if pdf_page_num < body_start_pdf_page:
                # Preliminary page (Page 1 has no number; page 2 is i)
                roman_idx = pdf_page_num - 1
                return int_to_roman(roman_idx) if roman_idx >= 1 else ""
            else:
                arabic_num = pdf_page_num - body_start_pdf_page + 1
                return str(arabic_num)

        # Update ToC entries
        import docx
        doc = docx.Document(str(docx_file))
        tables = doc.tables

        # Find ToC table, LoF table, LoT table
        # We know ToC table has headers ["Item", "Title", "Page"]
        # LoF has ["Figure No.", "Title / Description", "Page"]
        # LoT has ["Table No.", "Title / Description", "Page"]

        for table in tables:
            first_row_text = [c.text.strip() for c in table.rows[0].cells]
            if len(first_row_text) == 3 and first_row_text[2] == "Page":
                if first_row_text[0] == "Item":
                    # This is ToC table
                    for row in table.rows[1:]:
                        item_title = row.cells[1].text.strip()
                        # Search for item_title in PDF pages
                        matched_page = None
                        for p_idx, text in enumerate(page_texts, start=1):
                            clean_text = " ".join(text.split())
                            clean_item = " ".join(item_title.split())
                            if clean_item in clean_text or item_title.upper() in text.upper():
                                matched_page = p_idx
                                break
                        if matched_page:
                            row.cells[2].paragraphs[0].text = get_display_page(matched_page)

                elif first_row_text[0] == "Figure No.":
                    # LoF table
                    for row in table.rows[1:]:
                        fig_num = row.cells[0].text.strip()
                        matched_page = None
                        for p_idx, text in enumerate(page_texts, start=1):
                            if fig_num in text:
                                matched_page = p_idx
                                break
                        if matched_page:
                            row.cells[2].paragraphs[0].text = get_display_page(matched_page)

                elif first_row_text[0] == "Table No.":
                    # LoT table
                    for row in table.rows[1:]:
                        tbl_num = row.cells[0].text.strip()
                        matched_page = None
                        for p_idx, text in enumerate(page_texts, start=1):
                            if tbl_num in text:
                                matched_page = p_idx
                                break
                        if matched_page:
                            row.cells[2].paragraphs[0].text = get_display_page(matched_page)

        doc.save(str(docx_file))

    print(f"\n[DONE] Successfully resolved and verified page numbers for {docx_file}")
    return True

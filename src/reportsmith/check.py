"""Report Validator: verifies factual grounding, outline completeness, code appendix fidelity,

and checks for hallucinated numbers or banned phrases.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import docx

from reportsmith.brief import ProjectBrief
from reportsmith.facts import ProjectFacts

logger = logging.getLogger(__name__)

BANNED_PHRASES = [
    "as an ai",
    "as a language model",
    "i cannot",
    "i am an ai",
    "in this essay",
    "revolutionary breakthrough",
    "groundbreaking technology",
    "market-leading",
    "world-class",
    "cutting-edge state of the art",
    "99.9% accuracy",
]

TEMPLATE_MARKERS = [
    "{{",
    "}}",
    "{%",
    "%}",
    "TODO",
    "FIXME",
    "[INSERT",
    "<TODO>",
    "__BLANK__",
    "UNDEFINED",
]


def extract_numbers_from_prose(text: str) -> Set[str]:
    """Extract numeric tokens from prose, ignoring section, chapter, figure, and table references and years."""
    # Ignore "section 3.2", "chapter 1", "figure 3.1", "table 2.1", "page 12"
    cleaned = re.sub(
        r"\b(?:section|chapter|figure|table|page|appendix)\s+[A-Za-z0-9]+(?:\.[0-9]+)?\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    tokens = re.findall(r"\b\d+(?:\.\d+)?\b", cleaned)
    filtered = set()
    for t in tokens:
        # Ignore academic years (e.g. 2024..2028)
        if t in ("2024", "2025", "2026", "2027", "2028"):
            continue
        filtered.add(t)
    return filtered


class ReportValidator:
    """Validates generated report (.docx) against project facts and grounding rules."""

    def __init__(self, docx_path: Union[str, Path], brief: ProjectBrief, facts: ProjectFacts):
        self.docx_path = Path(docx_path)
        self.brief = brief
        self.facts = facts
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate(self, out_report_path: Optional[Union[str, Path]] = None) -> bool:
        if not self.docx_path.is_file():
            self.errors.append(f"Report file not found: {self.docx_path}")
            self._write_report(out_report_path)
            return False

        doc = docx.Document(str(self.docx_path))

        # Collect text and headings
        paragraphs_text: List[str] = []
        headings: List[str] = []
        full_text = ""

        for p in doc.paragraphs:
            txt = p.text.strip()
            if not txt:
                continue
            paragraphs_text.append(txt)
            full_text += txt + "\n"

            # Check if paragraph is heading
            if p.style.name.startswith("Heading") or re.match(r"^\d+\.\d+", txt) or txt.startswith("CHAPTER"):
                headings.append(txt)

        # Collect text from tables
        tables_text = ""
        for tbl in doc.tables:
            for row in tbl.rows:
                row_str = " | ".join(c.text.strip() for c in row.cells)
                tables_text += row_str + "\n"
        full_doc_text = full_text + tables_text

        # 1. Check template markers
        self._check_template_markers(full_doc_text)

        # 2. Check banned phrases
        self._check_banned_phrases(full_doc_text)

        # 3. Check outline section presence
        self._check_sections_presence(headings)

        # 4. Check figures and tables references
        self._check_figure_and_table_refs(full_doc_text)

        # 5. Check code appendix fidelity
        self._check_code_appendix(full_doc_text)

        # 6. Check for ungrounded numbers in prose
        self._check_prose_numbers(paragraphs_text)

        is_valid = len(self.errors) == 0
        self._write_report(out_report_path, is_valid)
        return is_valid

    def _check_template_markers(self, text: str):
        for marker in TEMPLATE_MARKERS:
            if marker in text:
                self.errors.append(f"Leftover template marker found in report: '{marker}'")

    def _check_banned_phrases(self, text: str):
        lower = text.lower()
        for phrase in BANNED_PHRASES:
            if phrase in lower:
                self.errors.append(f"Banned / hallucinated AI phrase detected: '{phrase}'")

    def _check_sections_presence(self, headings: List[str]):
        # Required chapters
        for ch_num in range(1, 7):
            if not any(f"CHAPTER {ch_num}" in h for h in headings):
                self.errors.append(f"Missing mandatory chapter header: CHAPTER {ch_num}")

        # Check key sections
        mandatory_secs = ["1.1", "1.2", "2.1", "2.2", "3.1", "3.7", "4.4", "5.1", "6.1"]
        for sec in mandatory_secs:
            if not any(h.startswith(sec) for h in headings):
                self.errors.append(f"Missing mandatory section header: {sec}")

    def _check_figure_and_table_refs(self, text: str):
        fig_refs = set(re.findall(r"Figure\s+(\d+\.\d+)", text))
        for ref in fig_refs:
            if f"Figure {ref}:" not in text:
                self.warnings.append(
                    f"Figure {ref} is referenced in text but figure caption 'Figure {ref}:' was not found"
                )

        tbl_refs = set(re.findall(r"Table\s+(\d+\.\d+)", text))
        for ref in tbl_refs:
            if f"Table {ref}:" not in text:
                self.warnings.append(
                    f"Table {ref} is referenced in text but table caption 'Table {ref}:' was not found"
                )

    def _check_code_appendix(self, full_text: str):
        for code_rel in self.brief.code_files_list:
            code_path = Path(code_rel)
            if not code_path.is_file():
                self.warnings.append(f"Code file {code_path} not found on disk to verify verbatim appendix.")
                continue

            lines = [l.strip() for l in code_path.read_text(encoding="utf-8").splitlines() if l.strip()]
            # Verify representative sample of lines
            missing_count = 0
            for l in lines:
                if len(l) > 10 and l not in full_text:
                    missing_count += 1

            if missing_count > len(lines) * 0.1:
                self.errors.append(
                    f"Appendix code fidelity check failed: {missing_count} source lines from {code_path.name} were not found verbatim."
                )

    def _check_prose_numbers(self, paragraphs: List[str]):
        # Check that numbers in prose are grounded in facts
        # Ignore headings and caption lines
        for p in paragraphs:
            if (
                p.startswith("CHAPTER")
                or re.match(r"^\d+\.\d+", p)
                or p.startswith("Figure")
                or p.startswith("Table")
                or p.startswith("[")
            ):
                continue

            nums = extract_numbers_from_prose(p)
            for num in nums:
                # Check whitelist
                if num not in self.facts.known_numbers:
                    # Allow single-digit small counts 1-9
                    try:
                        f_val = float(num)
                        if f_val.is_integer() and 1 <= int(f_val) <= 9:
                            continue
                    except ValueError:
                        pass

                    self.errors.append(
                        f"Ungrounded number '{num}' detected in prose: \"{p[:80]}...\""
                    )

    def _write_report(self, report_path: Optional[Union[str, Path]], is_valid: bool = False):
        out_p = Path(report_path or "check_report.txt")
        lines = [
            "=" * 60,
            "REPORTSMITH VERIFICATION REPORT",
            "=" * 60,
            f"Target Document: {self.docx_path}",
            f"Project: {self.brief.project.title}",
            f"Status: {'PASSED' if is_valid else 'FAILED'}",
            "",
            f"Total Errors: {len(self.errors)}",
            f"Total Warnings: {len(self.warnings)}",
            "",
        ]

        if self.errors:
            lines.append("ERRORS:")
            for e in self.errors:
                lines.append(f"  [X] {e}")
            lines.append("")

        if self.warnings:
            lines.append("WARNINGS:")
            for w in self.warnings:
                lines.append(f"  [!] {w}")
            lines.append("")

        if is_valid:
            lines.append("SUCCESS: All verification checks passed without errors.")

        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Wrote check report to {out_p}")


def validate_report(
    docx_path: Union[str, Path],
    brief: ProjectBrief,
    facts: ProjectFacts,
    out_report_path: Optional[Union[str, Path]] = None,
) -> bool:
    """Run full validation suite and write check_report.txt."""
    validator = ReportValidator(docx_path, brief, facts)
    return validator.validate(out_report_path)

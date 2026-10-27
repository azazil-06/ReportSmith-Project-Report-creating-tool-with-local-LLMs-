"""Format parser and structured report outline generator."""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class OutlineItem(BaseModel):
    kind: Literal["prelim", "chapter", "section", "end_matter"] = Field(
        ..., description="Type of outline element"
    )
    id: str = Field(..., description="Unique section/element identifier")
    title: str = Field(..., description="Section title")
    number: Optional[str] = Field(None, description="Numbering (e.g. 1.1, Chapter 1)")
    chapter: Optional[str] = Field(None, description="Parent chapter number if applicable")
    optional: bool = Field(False, description="Whether section is optional")
    description: Optional[str] = Field(None, description="Guidance or student instructions")


class ReportOutline(BaseModel):
    title: Optional[str] = None
    items: List[OutlineItem] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    def save_json(self, path: Union[str, Path]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.model_dump(), f, indent=2)

    @classmethod
    def load_json(cls, path: Union[str, Path]) -> "ReportOutline":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)


def parse_format_text(text: str) -> ReportOutline:
    """Deterministically parse format text file into structured ReportOutline."""
    lines = [line.strip() for line in text.splitlines()]
    items: List[OutlineItem] = []

    current_mode: Optional[str] = None
    current_chapter_num: Optional[str] = None

    # Patterns
    ch_pattern = re.compile(r"^(?:Chapter|CHAPTER)\s+(\d+)[:\s]+(.*)$", re.IGNORECASE)
    sec_pattern = re.compile(r"^(\d+\.\d+(?:\.\d+)?)\s+(.*)$")
    bullet_pattern = re.compile(r"^(?:[-*•]|\d+\.)\s+(.*)$")

    for line in lines:
        if not line:
            continue

        lower = line.lower()

        # Check section boundaries
        if "preliminary pages" in lower:
            current_mode = "prelim"
            continue
        elif "end matter" in lower or "end-matter" in lower:
            current_mode = "end_matter"
            continue
        elif "instructions to students" in lower or "instructions" in lower:
            current_mode = "instructions"
            continue

        if current_mode == "instructions":
            continue

        # Check Chapter header
        ch_match = ch_pattern.match(line)
        if ch_match:
            current_mode = "chapter"
            ch_num = ch_match.group(1).strip()
            ch_title = ch_match.group(2).strip()
            ch_title = re.sub(r"^[\s\-–—:•\*\u2022\ufffd]+", "", ch_title).strip()
            current_chapter_num = ch_num
            items.append(
                OutlineItem(
                    kind="chapter",
                    id=f"ch_{ch_num}",
                    title=ch_title,
                    number=ch_num,
                    optional=False,
                )
            )
            continue

        # Check numbered section
        sec_match = sec_pattern.match(line)
        if sec_match:
            sec_num = sec_match.group(1).strip()
            sec_title = sec_match.group(2).strip()
            is_opt = "(if applicable)" in sec_title.lower() or "(if required)" in sec_title.lower()
            clean_title = re.sub(
                r"\s*\((?:if applicable|if required)\)", "", sec_title, flags=re.IGNORECASE
            ).strip()

            parent_ch = sec_num.split(".")[0]
            items.append(
                OutlineItem(
                    kind="section",
                    id=sec_num,
                    title=clean_title,
                    number=sec_num,
                    chapter=parent_ch,
                    optional=is_opt,
                )
            )
            continue

        # Check prelim items
        if current_mode == "prelim":
            bullet_match = bullet_pattern.match(line)
            raw_title = bullet_match.group(1).strip() if bullet_match else line
            is_opt = "(if applicable)" in raw_title.lower() or "(if required)" in raw_title.lower()
            clean_title = re.sub(
                r"\s*\((?:if applicable|if required)\)", "", raw_title, flags=re.IGNORECASE
            ).strip()

            slug = re.sub(r"[^a-z0-9]+", "_", clean_title.lower()).strip("_")
            items.append(
                OutlineItem(
                    kind="prelim",
                    id=slug,
                    title=clean_title,
                    optional=is_opt,
                )
            )
            continue

        # Check end matter items
        if current_mode == "end_matter":
            bullet_match = bullet_pattern.match(line)
            raw_title = bullet_match.group(1).strip() if bullet_match else line
            is_opt = "(if applicable)" in raw_title.lower() or "(if required)" in raw_title.lower()
            clean_title = re.sub(
                r"\s*\((?:if applicable|if required)\)", "", raw_title, flags=re.IGNORECASE
            ).strip()

            slug = re.sub(r"[^a-z0-9]+", "_", clean_title.lower()).strip("_")
            items.append(
                OutlineItem(
                    kind="end_matter",
                    id=slug,
                    title=clean_title,
                    optional=is_opt,
                )
            )
            continue

    # If no items were parsed, raise error
    if not items:
        raise ValueError("Could not parse any outline items from format content")

    return ReportOutline(items=items)


def parse_format_file(file_path: Union[str, Path]) -> ReportOutline:
    """Parse format specification from text or PDF file."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Format file not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n".join([page.extract_text() or "" for page in reader.pages])
    else:
        text = path.read_text(encoding="utf-8", errors="replace")

    return parse_format_text(text)

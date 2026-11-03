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


def extract_abstract_modules(abstract_text: str) -> List[str]:
    """Extract named components, modules, or subsystems mentioned in an abstract."""
    if not abstract_text:
        return []

    modules: List[str] = []
    # Pattern 1: structured into / consists of / divided into / comprises ... components / modules:
    m = re.search(
        r"(?:structured into|consists of|divided into|comprises|organized into)\s+"
        r"(?:two|three|four|five|\d+)?\s*(?:primary\s+|core\s+|key\s+)?(?:components|modules|subsystems|parts|sections)[:\s]+([^.]+)",
        abstract_text,
        re.IGNORECASE,
    )
    if m:
        raw_list = m.group(1)
        parts = re.split(r"\s*(?:,|and|\&)\s*", raw_list)
        for p in parts:
            p_clean = re.sub(r"^(?:a|an|the)\s+", "", p.strip(), flags=re.IGNORECASE)
            p_clean = re.sub(r"\s+(?:component|module|subsystem)$", "", p_clean, flags=re.IGNORECASE)
            if len(p_clean) >= 3 and len(p_clean) <= 40:
                modules.append(p_clean.strip().title())

    # Pattern 2: Explicit module names mentioned with 'Module' or 'Terminal' or 'Interface'
    named_matches = re.findall(
        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:Module|Terminal|Subsystem|Interface)\b",
        abstract_text,
    )
    for nm in named_matches:
        nm_title = nm.strip().title()
        if nm_title not in modules and len(nm_title) > 3 and nm_title not in ("The", "This", "An", "Each", "Our"):
            modules.append(nm_title)

    # Deduplicate while preserving order
    seen = set()
    unique_modules = []
    for m_name in modules:
        m_lower = m_name.lower()
        if m_lower not in seen and m_lower not in ("system", "project", "software", "hardware"):
            seen.add(m_lower)
            unique_modules.append(m_name)

    return unique_modules


def synthesize_universal_outline(
    domain: str = "GENERAL_ACADEMIC",
    title: str = "Technical Project Report",
    abstract_text: Optional[str] = None,
    modules: Optional[List[str]] = None,
    has_code: bool = True,
    is_hardware: Optional[bool] = None,
    brief: Optional[Any] = None,
) -> ReportOutline:
    """Synthesize complete, publication-grade academic outline for any subject when no rules file is provided.

    Guarantees that mandatory pages stay present in exact institutional order:
    1. Front page (Title Page)
    2. Bonafide certificate
    3. Declaration
    4. Acknowledgement
    5. Abstract
    6. Content (Table of Contents)
    7. List of figures
    8. List of tables
    9. List of abbreviations
    10. Chapter 1 - Introduction
    11. Chapter 2 - System Specifications & Requirements
    12. Chapter 3 - System Design & Architecture
    13. Chapter 4 - Implementation
    14. Chapter 5 - Testing and Results
    15. Chapter 6 - Conclusion and Future Scope
    16. References
    17. Appendix A - Complete Source Code (if code present)

    Dynamically invents appropriate subheadings and topics based on the abstract, title, and domain,
    conditioned strictly on is_hardware.
    """
    if is_hardware is None:
        if brief is not None:
            is_hardware = getattr(getattr(brief, "implementation", None), "is_hardware", None)
            if is_hardware is None:
                is_hardware = getattr(brief, "is_hardware", None)
    if is_hardware is None:
        domain_clean = (domain or "GENERAL_ACADEMIC").upper()
        is_hardware = (domain_clean in ("EMBEDDED_HARDWARE", "EMBEDDED_IOT"))

    domain_clean = (domain or "GENERAL_ACADEMIC").upper()
    if brief is not None:
        brief_dom = getattr(brief, "domain", None)
        if brief_dom and domain == "GENERAL_ACADEMIC":
            domain_clean = brief_dom.upper()

    items: List[OutlineItem] = [
        OutlineItem(kind="prelim", id="title_page", title="Title Page"),
        OutlineItem(kind="prelim", id="certificate", title="Bonafide Certificate"),
        OutlineItem(kind="prelim", id="declaration", title="Declaration", optional=False),
        OutlineItem(kind="prelim", id="acknowledgement", title="Acknowledgement"),
        OutlineItem(kind="prelim", id="abstract", title="Abstract"),
        OutlineItem(kind="prelim", id="table_of_contents", title="Table of Contents"),
        OutlineItem(kind="prelim", id="list_of_figures", title="List of Figures"),
        OutlineItem(kind="prelim", id="list_of_tables", title="List of Tables", optional=False),
        OutlineItem(kind="prelim", id="list_of_abbreviations", title="List of Abbreviations", optional=False),
    ]

    # Chapter 1: Introduction
    items.append(OutlineItem(kind="chapter", id="ch_1", title="Introduction", number="1"))
    items.append(OutlineItem(kind="section", id="1.1", title="Project Overview", number="1.1", chapter="1"))
    items.append(OutlineItem(kind="section", id="1.2", title="Objectives", number="1.2", chapter="1"))
    items.append(OutlineItem(kind="section", id="1.3", title="Scope of the Project", number="1.3", chapter="1"))
    items.append(OutlineItem(kind="section", id="1.4", title="Applications", number="1.4", chapter="1"))

    # Chapter 2: System Specifications / Requirements
    if is_hardware:
        ch2_title = "Hardware and Software Requirements"
        sec2 = [
            ("2.1", "STM32 Microcontroller Overview"),
            ("2.2", "STM32 Development Board and Specifications"),
            ("2.3", "Hardware Components and Sensors Used"),
            ("2.4", "Software Requirements"),
            ("2.5", "STM32CubeIDE and STM32 HAL Library"),
        ]
    elif domain_clean == "BIOINFORMATICS" or "BIO" in domain_clean:
        ch2_title = "System Specifications and Platform Requirements"
        sec2 = [
            ("2.1", "Computational Environment and System Specifications"),
            ("2.2", "Algorithmic Framework and Software Dependencies"),
            ("2.3", "High-Performance Computing and Infrastructure Requirements"),
            ("2.4", "Genomic Dataset Specifications and Input Constraints"),
            ("2.5", "System Design Constraints and Quality Standards"),
        ]
    elif domain_clean in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
        ch2_title = "Computational Platform and Software Requirements"
        sec2 = [
            ("2.1", "Operational Environment and Computational Specifications"),
            ("2.2", "Machine Learning Framework and Software Requirements"),
            ("2.3", "Computational Infrastructure and GPU Acceleration Requirements"),
            ("2.4", "Dataset Specifications and Input Constraints"),
            ("2.5", "Design Constraints and Quality Standards"),
        ]
    elif domain_clean == "SOFTWARE_APP":
        ch2_title = "Software and System Requirements"
        sec2 = [
            ("2.1", "Operational Environment and System Specifications"),
            ("2.2", "Functional and Feature Requirements"),
            ("2.3", "Software and Technology Stack Requirements"),
            ("2.4", "Computational Platform and Infrastructure Requirements"),
            ("2.5", "Design Constraints and Quality Standards"),
        ]
    elif domain_clean == "WEB_CLOUD":
        ch2_title = "System Specifications and Platform Requirements"
        sec2 = [
            ("2.1", "Web Platform and Cloud Architectural Specifications"),
            ("2.2", "Functional and User Role Requirements"),
            ("2.3", "Full-Stack Technology Stack and Framework Specifications"),
            ("2.4", "Cloud Infrastructure and Hosting Requirements"),
            ("2.5", "Security, Authentication, and Interface Standards"),
        ]
    elif domain_clean == "COA_SYSTEMS":
        ch2_title = "Architectural and System Requirements"
        sec2 = [
            ("2.1", "Target ISA and Processor Specifications"),
            ("2.2", "Simulation Platform and Software Requirements"),
            ("2.3", "Hardware-Software Co-Design Requirements"),
            ("2.4", "System Design Constraints and Standards"),
        ]
    else:
        ch2_title = "System Architecture, Technology Stack, and Prerequisites"
        sec2 = [
            ("2.1", "Operational Environment and System Prerequisites"),
            ("2.2", "Technology Stack and Framework Specifications"),
            ("2.3", "Computational Infrastructure and Platform Specifications"),
            ("2.4", "System Design Constraints and Quality Standards"),
        ]

    items.append(OutlineItem(kind="chapter", id="ch_2", title=ch2_title, number="2"))
    for sid, stitle in sec2:
        items.append(OutlineItem(kind="section", id=sid, title=stitle, number=sid, chapter="2"))

    # Chapter 3: System Design and Architecture
    items.append(OutlineItem(kind="chapter", id="ch_3", title="System Design and Architecture", number="3"))
    if is_hardware:
        sec3 = [
            ("3.1", "System Architecture"),
            ("3.2", "Block Diagram"),
            ("3.3", "Circuit Diagram"),
            ("3.4", "Hardware Component Description"),
            ("3.5", "Pin Configuration and Peripheral Mapping"),
            ("3.6", "Algorithm and Flowchart"),
            ("3.7", "Working Principle"),
        ]
    elif domain_clean == "BIOINFORMATICS" or "BIO" in domain_clean:
        sec3 = [
            ("3.1", "Subsystem Architecture and Pipeline Decomposition"),
            ("3.2", "Bioinformatics Pipeline and Block Diagram"),
            ("3.3", "Subsystem Data Flow and Sequence Processing Diagram (DFD)"),
            ("3.4", "Interface Specifications and Data Schema"),
            ("3.5", "Alignment Algorithm and Operational Flowchart"),
            ("3.6", "Working Principle and Processing Logic"),
        ]
    elif domain_clean in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
        sec3 = [
            ("3.1", "Overall System Architecture"),
            ("3.2", "Machine Learning Pipeline and Block Diagram"),
            ("3.3", "Subsystem Data Flow and Feature Pipeline Interaction Diagram (DFD)"),
            ("3.4", "Dataset Organization and Feature Representation"),
            ("3.5", "Classification Algorithm and Operational Flowchart"),
            ("3.6", "Working Principle and Performance Evaluation Metrics"),
        ]
    elif domain_clean == "SOFTWARE_APP":
        sec3 = [
            ("3.1", "System Architecture and Subsystem Decomposition"),
            ("3.2", "Block Diagram and Architectural Layers"),
            ("3.3", "Subsystem Data Flow and Component Interaction Diagram (DFD)"),
            ("3.4", "Data Storage and Database / File Schema"),
            ("3.5", "Application Algorithm and Operational Flowchart"),
            ("3.6", "Working Principle and Processing Logic"),
        ]
    elif domain_clean == "WEB_CLOUD":
        sec3 = [
            ("3.1", "Three-Tier Client-Server Architecture"),
            ("3.2", "Full-Stack System Block Diagram"),
            ("3.3", "End-to-End Data Flow and REST API Interaction Diagram (DFD)"),
            ("3.4", "Database Schema Design and Entity Relationship"),
            ("3.5", "Operational Flowchart and Request-Response Lifecycle"),
            ("3.6", "Working Principle, Authentication, and Security Architecture"),
        ]
    elif domain_clean == "COA_SYSTEMS":
        sec3 = [
            ("3.1", "Core System Architecture"),
            ("3.2", "Instruction Datapath and Pipeline Block Diagram"),
            ("3.3", "Memory Mapped I/O (MMIO) Communication Path"),
            ("3.4", "Finite State Machine (FSM) State Transition Diagram"),
            ("3.5", "Control Logic and Execution Flowchart"),
        ]
    else:
        sec3 = [
            ("3.1", "Subsystem Architecture and Decomposition"),
            ("3.2", "Subsystem Block Architecture Diagram"),
            ("3.3", "Subsystem Data Flow and Interaction Diagram (DFD)"),
            ("3.4", "Interface Specifications and Data Schema"),
            ("3.5", "Operational Flowchart and Execution Logic"),
            ("3.6", "Working Principle and Processing Logic"),
        ]

    for sid, stitle in sec3:
        items.append(OutlineItem(kind="section", id=sid, title=stitle, number=sid, chapter="3"))

    # Chapter 4: Implementation
    items.append(OutlineItem(kind="chapter", id="ch_4", title="Implementation", number="4"))
    if is_hardware:
        sec4 = [
            ("4.1", "Development Environment Setup"),
            ("4.2", "GPIO and Peripheral Configuration"),
            ("4.3", "Embedded C Program Development"),
            ("4.4", "Hardware Interfacing and Integration"),
            ("4.5", "Program Execution and Debugging"),
            ("4.6", "Implementation Challenges and Solutions"),
        ]
    else:
        extracted_mods = []
        if modules:
            extracted_mods = [m for m in modules if m and len(m.strip()) > 2]
        if not extracted_mods and brief is not None:
            brief_impl = getattr(brief, "implementation", None)
            if brief_impl is not None:
                b_mods = getattr(brief_impl, "modules", []) or []
                extracted_mods = [m for m in b_mods if m and len(m.strip()) > 2]
            if not extracted_mods:
                b_mods = getattr(brief, "modules", []) or []
                extracted_mods = [m for m in b_mods if m and len(m.strip()) > 2]
        if not extracted_mods and abstract_text:
            extracted_mods = extract_abstract_modules(abstract_text)
        if not extracted_mods:
            if domain_clean == "BIOINFORMATICS" or "BIO" in domain_clean:
                extracted_mods = ["Sequence Ingestion and Preprocessing", "Alignment Matrix Computation", "Scoring and Statistical Verification"]
            elif domain_clean in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
                extracted_mods = ["Dataset Ingestion and Preprocessing", "Feature Engineering Subsystem", "Classification and Inference Engine"]
            elif domain_clean == "WEB_CLOUD":
                extracted_mods = ["Responsive Client Presentation", "RESTful API Controller", "Authentication and Role Security"]
            elif domain_clean == "SOFTWARE_APP":
                extracted_mods = ["User Interface and Client Interaction", "Core Business Logic Processing", "Data Persistence and Storage Handling"]
            else:
                extracted_mods = ["Data Ingestion Pipeline", "Core Processing Engine", "Evaluation and Output Interface"]

        sec4 = [("4.1", "Development Environment Setup and Configuration")]
        idx = 2
        for mod in extracted_mods:
            mod_clean = re.sub(r"\s*(?:Module|Subsystem)\s*$", "", mod, flags=re.IGNORECASE).strip()
            sec4.append((f"4.{idx}", f"{mod_clean} Module Implementation"))
            idx += 1

        # Add domain-appropriate closing sections
        if not any("persistence" in m.lower() or "storage" in m.lower() for m in extracted_mods):
            if domain_clean in ("SOFTWARE_APP", "WEB_CLOUD", "GENERAL_ACADEMIC"):
                sec4.append((f"4.{idx}", "Data Persistence and Storage Handling"))
                idx += 1
            elif domain_clean in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
                sec4.append((f"4.{idx}", "Model Training and Pipeline Optimization"))
                idx += 1
            elif domain_clean == "BIOINFORMATICS" or "BIO" in domain_clean:
                sec4.append((f"4.{idx}", "Algorithmic Optimization and Matrix Computation"))
                idx += 1

        sec4.append((f"4.{idx}", "System Integration and Program Execution Testing"))
        idx += 1
        sec4.append((f"4.{idx}", "Implementation Challenges and Solutions"))

    for sid, stitle in sec4:
        items.append(OutlineItem(kind="section", id=sid, title=stitle, number=sid, chapter="4"))

    # Chapter 5: Testing and Results
    items.append(OutlineItem(kind="chapter", id="ch_5", title="Testing and Results", number="5"))
    if is_hardware:
        sec5 = [
            ("5.1", "Testing Methodology"),
            ("5.2", "Test Cases and Observations"),
            ("5.3", "Hardware Implementation Photographs"),
            ("5.4", "Output Screenshots"),
            ("5.5", "Result Analysis"),
        ]
    else:
        sec5 = [
            ("5.1", "Testing Methodology and Validation Strategy"),
            ("5.2", "Test Cases and Experimental Observations"),
            ("5.3", "System Validation and Benchmark Results"),
            ("5.4", "Output Screenshots and Telemetry"),
            ("5.5", "Result Analysis and Performance Evaluation"),
        ]
    for sid, stitle in sec5:
        items.append(OutlineItem(kind="section", id=sid, title=stitle, number=sid, chapter="5"))

    # Chapter 6: Conclusion and Future Scope
    items.append(OutlineItem(kind="chapter", id="ch_6", title="Conclusion and Future Scope", number="6"))
    sec6 = [
        ("6.1", "Conclusion"),
        ("6.2", "Learning Outcomes"),
        ("6.3", "Limitations"),
        ("6.4", "Future Enhancements"),
    ]
    for sid, stitle in sec6:
        items.append(OutlineItem(kind="section", id=sid, title=stitle, number=sid, chapter="6"))

    # End Matter
    items.append(OutlineItem(kind="end_matter", id="references", title="References"))
    if has_code:
        code_title = "Appendix A – Complete Source Code"
        if domain_clean == "EMBEDDED_HARDWARE":
            code_title = "Appendix A – Complete Embedded C Source Code"
        elif domain_clean == "SOFTWARE_APP":
            code_title = "Appendix A – Complete Application Source Code"
        elif domain_clean == "WEB_CLOUD":
            code_title = "Appendix A – Complete Full-Stack Web Application Source Code"
        elif domain_clean == "COA_SYSTEMS":
            code_title = "Appendix A – Complete Assembly Source Code"
        items.append(OutlineItem(kind="end_matter", id="appendix_a_source_code", title=code_title))

    items.append(
        OutlineItem(
            kind="end_matter",
            id="appendix_b_additional_details",
            title="Appendix B – Additional Diagrams and Configuration Details",
            optional=True,
        )
    )

    # Definitive Non-Hardware Sanitizer Guard
    if not is_hardware:
        banned_phrases = {
            "stm32 microcontroller overview": "Operational Environment and System Specifications",
            "stm32 development board and specifications": "Technology Stack and Framework Specifications",
            "hardware components and sensors used": "System Components and Dependencies",
            "stm32cubeide and stm32 hal library": "Development Tools and Runtime Libraries",
            "circuit diagram": "Subsystem Data Flow Diagram (DFD)",
            "hardware component description": "Subsystem Module Description",
            "pin configuration and peripheral mapping": "Subsystem Architecture and Interface Mapping",
            "gpio and peripheral configuration": "Subsystem Configuration and Setup",
            "embedded c program development": "Core Application Logic Development",
            "hardware interfacing and integration": "System Integration and Module Interfacing",
            "hardware implementation photographs": "System Validation and Benchmark Results",
        }
        for item in items:
            t_low = item.title.strip().lower()
            for banned, clean in banned_phrases.items():
                if banned in t_low:
                    item.title = clean

    return ReportOutline(title=f"{title} Outline", items=items)


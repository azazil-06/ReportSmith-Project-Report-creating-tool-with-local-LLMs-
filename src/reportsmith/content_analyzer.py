"""Content folder analyzer: inspects files in Content/ (code, rules PDF/text)

and sample COA_report.docx to synthesize ProjectBrief, ProjectFacts, and ReportOutline.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import docx
from pypdf import PdfReader

from reportsmith.brief import (
    AssetsInfo,
    BehaviourInfo,
    BoardInfo,
    ComponentItem,
    ImplementationInfo,
    PeopleInfo,
    PinMapping,
    ProjectBrief,
    ProjectInfo,
    StaffMember,
    SystemPlatformInfo,
    TeamMember,
    TestCase,
)
from reportsmith.facts import derive_facts, ProjectFacts
from reportsmith.outline import (
    extract_abstract_modules,
    parse_format_file,
    synthesize_universal_outline,
    ReportOutline,
)

logger = logging.getLogger(__name__)

KNOWN_ACRONYMS = {
    "DNA", "RNA", "KNN", "CNN", "RNN", "LSTM", "SVM", "AI", "ML", "NLP",
    "REST", "API", "HTTP", "HTTPS", "JWT", "MERN", "CRUD", "UI", "GUI",
    "URL", "JSON", "XML", "SQL", "NOSQL", "STM32", "ARM", "ADC", "DAC",
    "GPIO", "PWM", "I2C", "SPI", "UART", "USART", "USB", "BLE", "IOT",
    "CPU", "GPU", "FASTA", "FASTQ", "BLAST", "NCBI", "MVC", "SPA", "RBAC",
}

LOWERCASE_WORDS = {
    "a", "an", "the", "and", "but", "or", "for", "nor", "on", "at", "to",
    "from", "by", "with", "in", "of", "via", "using",
}


def normalize_title(raw_title: str, abstract_text: str = "") -> str:
    """Normalize project title: preserve acronyms, recover CamelCase, enforce scholarly title case."""
    if not raw_title or len(raw_title.strip()) < 3:
        return "Technical Project Report"

    clean = re.sub(r"\s+", " ", raw_title.strip())
    # Recover CamelCase tokens from abstract or title (e.g. CampusConnect, GeneAlign, FreshKNN, MoneyGo, ParkEase)
    camel_tokens = set(re.findall(r"\b[A-Z][a-z]+[A-Z][A-Za-z0-9]*\b", clean + " " + (abstract_text or "")))

    words = clean.split()
    norm_words = []
    for i, w in enumerate(words):
        m = re.match(r"^([^a-zA-Z0-9]*)(.*?)([^a-zA-Z0-9]*)$", w)
        if not m:
            norm_words.append(w)
            continue
        pre, core, post = m.groups()
        core_upper = core.upper()

        if core_upper in KNOWN_ACRONYMS:
            c = core_upper
        else:
            matched_camel = next((ct for ct in camel_tokens if ct.lower() == core.lower()), None)
            if matched_camel:
                c = matched_camel
            elif i > 0 and i < len(words) - 1 and core.lower() in LOWERCASE_WORDS and not pre.startswith(":"):
                c = core.lower()
            elif "-" in core:
                subparts = core.split("-")
                c = "-".join(sp.upper() if sp.upper() in KNOWN_ACRONYMS else sp.capitalize() for sp in subparts)
            else:
                c = core.capitalize()
        norm_words.append(pre + c + post)

    return " ".join(norm_words)


def _get_domain_default_subsystems(domain: str) -> List[str]:
    d = (domain or "").upper()
    if d == "BIOINFORMATICS":
        return [
            "Genomic Dataset Ingestion Pipeline",
            "Sequence Alignment Engine",
            "Dynamic Programming Computation Matrix",
            "Benchmark and Performance Evaluation",
        ]
    elif d in ("WEB_CLOUD", "SOFTWARE_APP"):
        return [
            "Client Interface and Presentation Layer",
            "RESTful API Controller and Routing",
            "Authentication and Security Subsystem",
            "Database and Persistence Layer",
        ]
    elif d in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
        return [
            "Data Ingestion and Preprocessing Pipeline",
            "Feature Engineering and Extraction Engine",
            "Model Training and Inference Subsystem",
            "Performance Evaluation and Metrics Module",
        ]
    elif d in ("EMBEDDED_HARDWARE", "EMBEDDED_IOT"):
        return [
            "Sensor Acquisition Subsystem",
            "Signal Calibration and Decision Logic",
            "Output Actuation and Alert Controller",
            "Firmware Execution Loop",
        ]
    elif d == "COA_SYSTEMS":
        return [
            "Instruction Fetch and Decode Subsystem",
            "ALU and Execution Datapath",
            "Memory-Mapped I/O Interface",
            "Hazard Detection and Control Unit",
        ]
    return [
        "Data Ingestion and Validation Layer",
        "Core Algorithmic Processing Engine",
        "State Management and Persistence",
        "System Verification and Reporting",
    ]


def _extract_domain_entities(abstract_text: str, domain: str) -> List[str]:
    t_low = (abstract_text or "").lower()
    d = (domain or "").upper()
    entities: List[str] = []

    if d == "BIOINFORMATICS":
        if any(w in t_low for w in ("smith-waterman", "blast", "alignment", "aligning")):
            entities.append("Sequence Alignment Engine")
        if any(w in t_low for w in ("fasta", "nucleotide", "genomic", "dataset", "sequence")):
            entities.append("Genomic Dataset Ingestion Pipeline")
        if any(w in t_low for w in ("dynamic programming", "matrix", "matrices", "gap")):
            entities.append("Dynamic Programming Matrix Module")
        if any(w in t_low for w in ("benchmark", "speedup", "sensitivity", "evaluation")):
            entities.append("Sensitivity and Benchmark Evaluation Subsystem")

    elif d in ("WEB_CLOUD", "SOFTWARE_APP"):
        if any(w in t_low for w in ("react", "angular", "vue", "frontend", "client", "ui", "gui", "swing", "interface")):
            entities.append("User Interface and Presentation Layer")
        if any(w in t_low for w in ("restful", "api", "express", "node", "controller", "endpoint", "endpoints")):
            entities.append("RESTful API Routing and Controller Engine")
        if any(w in t_low for w in ("jwt", "auth", "token", "bcrypt", "security", "role")):
            entities.append("Authentication and Security Subsystem")
        if any(w in t_low for w in ("mongodb", "sqlite", "sql", "database", "datastore", "persistence")):
            entities.append("Data Storage and Persistence Layer")
        if any(w in t_low for w in ("qr", "ticket", "event", "transaction", "registration", "export", "csv")):
            entities.append("Transaction Processing and Service Logic")

    elif d in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
        if any(w in t_low for w in ("image", "vision", "color", "histogram", "pixel", "preprocess")):
            entities.append("Visual Data Preprocessing Pipeline")
        if any(w in t_low for w in ("feature", "descriptor", "glcm", "extraction", "embedding")):
            entities.append("Feature Extraction and Representation Engine")
        if any(w in t_low for w in ("knn", "classifier", "classification", "random forest", "inference", "model")):
            entities.append("Model Inference and Classification Subsystem")
        if any(w in t_low for w in ("accuracy", "evaluation", "metrics", "test", "benchmark")):
            entities.append("Performance Evaluation and Metrics Module")

    elif d in ("EMBEDDED_HARDWARE", "EMBEDDED_IOT"):
        if any(w in t_low for w in ("sensor", "adc", "capacitive", "ultrasonic", "moisture", "water", "analog")):
            entities.append("Sensor Data Acquisition Subsystem")
        if any(w in t_low for w in ("calibration", "filtering", "threshold", "decision")):
            entities.append("Signal Calibration and Threshold Detection Module")
        if any(w in t_low for w in ("led", "buzzer", "alert", "gpio", "actuat")):
            entities.append("Output Actuation and Alerting Controller")
        if any(w in t_low for w in ("firmware", "loop", "telemetry", "uart", "serial")):
            entities.append("Firmware Executive and Telemetry Loop")

    return entities


def extract_dynamic_subsystems(abstract_text: str, domain: str = "GENERAL_ACADEMIC", title: str = "") -> List[str]:
    """Extract 3 to 5 domain-grounded functional subsystems/modules from arbitrary technical abstracts."""
    if not abstract_text or len(abstract_text.strip()) < 20:
        return _get_domain_default_subsystems(domain)

    candidates: List[str] = []

    # 1. Structured lists ("structured into / consists of / divided into / comprises / features include:")
    m_struct = re.search(
        r"(?:structured into|consists of|divided into|comprises|organized into|features include|key features include|modules include)[:\s]+([^.]+)",
        abstract_text,
        re.IGNORECASE,
    )
    if m_struct:
        for part in re.split(r",\s*(?:and\s+)?|\s+and\s+|;\s*", m_struct.group(1)):
            p_clean = re.sub(r"^(?:a|an|the)\s+", "", part.strip(), flags=re.IGNORECASE)
            p_clean = re.sub(r"\s+(?:component|module|subsystem)$", "", p_clean, flags=re.IGNORECASE)
            if 3 <= len(p_clean) <= 45 and not any(w in p_clean.lower() for w in ("following", "project", "system")):
                candidates.append(normalize_title(p_clean, abstract_text))

    # 2. Explicit capitalized module names
    named_mods = re.findall(
        r"\b([A-Z][A-Za-z0-9_\-\s]{2,35})\s+(?:Module|Subsystem|Pipeline|Layer|Engine|Interface|Terminal)\b",
        abstract_text,
    )
    for nm in named_mods:
        nm_clean = normalize_title(nm.strip(), abstract_text)
        if nm_clean not in candidates and len(nm_clean) > 3 and nm_clean.lower() not in ("the", "this", "our", "each", "system"):
            candidates.append(nm_clean)

    # 3. Domain entity parsing if candidates < 3
    if len(candidates) < 3:
        for ent in _extract_domain_entities(abstract_text, domain):
            if ent not in candidates:
                candidates.append(ent)

    # 4. Universal fallback if still < 3
    if len(candidates) < 3:
        for fb in _get_domain_default_subsystems(domain):
            if fb not in candidates:
                candidates.append(fb)

    # 5. Deduplicate and bound to [3, 5]
    seen = set()
    final_modules = []
    for c in candidates:
        c_low = c.lower()
        if c_low not in seen and c_low not in ("system", "software", "hardware", "project", "application"):
            seen.add(c_low)
            final_modules.append(c)
        if len(final_modules) == 5:
            break

    # Guarantee at least 3
    if len(final_modules) < 3:
        for fallback in _get_domain_default_subsystems(domain):
            if fallback.lower() not in seen:
                seen.add(fallback.lower())
                final_modules.append(fallback)
            if len(final_modules) >= 3:
                break

    return final_modules[:5]


def extract_template_metadata(sample_docx_path: Path) -> Dict[str, Any]:
    """Extract college, department, address, and logos from sample docx (e.g. COA_report.docx).
    Names are never extracted from sample docx.
    """
    meta: Dict[str, Any] = {
        "college": "Sree Buddha College of Engineering (Autonomous)",
        "department": "Department of Computer Science & Engineering",
        "university_line": "An Autonomous Institution Affiliated to State Technological University",
        "address": "Pattoor P. O., Alappuzha - 690 529",
        "month_year": "October 2026",
        "academic_year": "2026-2027",
        "team": [],
        "logo_cover": None,
        "logo_cert": None,
    }

    if not sample_docx_path or not sample_docx_path.is_file():
        return meta

    try:
        doc = docx.Document(str(sample_docx_path))

        # Extract cover paragraphs
        paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for p in paras:
            if "COLLEGE OF ENGINEERING" in p.upper():
                meta["college"] = p.title()
            elif "DEPARTMENT OF" in p.upper():
                meta["department"] = p.title().rstrip(",")
            elif "ALAPPUZHA" in p.upper() or "PATTOOR" in p.upper():
                meta["address"] = p

        # Extract logos from docx media
        out_logo_dir = Path("out/extracted_logos")
        out_logo_dir.mkdir(parents=True, exist_ok=True)
        for rel in doc.part.rels.values():
            if "image1." in rel.target_ref:
                p1 = out_logo_dir / "college_logo.png"
                p1.write_bytes(rel.target_part.blob)
                meta["logo_cover"] = p1
            elif "image2." in rel.target_ref:
                p2 = out_logo_dir / "emblem_logo.png"
                p2.write_bytes(rel.target_part.blob)
                meta["logo_cert"] = p2

    except Exception as e:
        logger.warning(f"Failed extracting metadata from {sample_docx_path}: {e}")

    return meta


def _is_valid_student_name(name: str) -> bool:
    name_clean = name.strip()
    if len(name_clean) < 3 or len(name_clean) > 40:
        return False
    if not re.match(r"^[A-Za-z\s\.\'\-]+$", name_clean):
        return False
    lower = name_clean.lower()
    banned = [
        "system", "sensor", "controls", "indicator", "ultrasonic", "circuit",
        "diagram", "table", "figure", "chapter", "abstract", "contents",
        "predefined", "configured", "threshold", "powered", "supply", "battery",
        "demonstrate", "application", "instead", "designed", "microcontroller",
        "watering", "reminder", "moisture", "night", "automatic", "lamp",
    ]
    if any(b in lower for b in banned):
        return False
    return True


def _is_valid_roll_no(roll: str) -> bool:
    roll_clean = roll.strip().upper()
    if len(roll_clean) < 5 or len(roll_clean) > 16:
        return False
    if not (re.search(r"\d", roll_clean) and re.search(r"[A-Z]", roll_clean)):
        return False
    lower = roll_clean.lower()
    if any(w in lower for w in ["sr04", "hc-sr", "stm32", "lm2596", "lcd", "led", "16x2", "162", "oled"]):
        return False
    if len(roll_clean.split()) > 2:
        return False
    return True


def _parse_member_line(line: str) -> Optional[TeamMember]:
    """Parse a single line into a TeamMember(name, roll_no), or None if not matching."""
    clean = re.sub(r"^\s*(?:\d+[\.\)]|\*|-)\s*", "", line).strip()
    if not clean:
        return None

    # 1. Name (Roll) or Roll (Name)
    m = re.match(r"^([A-Za-z\s\.\']+?)\s*\(\s*([A-Za-z0-9\s\-_/]+)\s*\)$", clean)
    if m:
        p1, p2 = m.group(1).strip(), m.group(2).strip()
        if _is_valid_roll_no(p2) and _is_valid_student_name(p1):
            return TeamMember(name=p1.title(), roll_no=p2.upper())
        elif _is_valid_roll_no(p1) and _is_valid_student_name(p2):
            return TeamMember(name=p2.title(), roll_no=p1.upper())

    # 2. Match Name with optional serial/class roll number followed by university roll number
    # e.g. "Kashinath R Krishna 45 SBC24CS134" or "Arjun Prasad SBC24CS073"
    m_serial = re.match(r"^([A-Za-z\s\.\']+?)\s+(?:\d{1,3}\s+)?([A-Za-z]{2,5}\d{2}[A-Za-z]{1,4}\d{2,4}|[A-Za-z0-9\-_/]{6,16})$", clean)
    if m_serial:
        p1, p2 = m_serial.group(1).strip(), m_serial.group(2).strip()
        if _is_valid_roll_no(p2) and _is_valid_student_name(p1):
            return TeamMember(name=p1.title(), roll_no=p2.upper())

    # 3. Separated by delimiter [-–—:,|\t] or 2+ spaces
    parts = re.split(r"\s*(?:[-–—:,|\t]|\s{2,})\s*", clean, maxsplit=1)
    if len(parts) == 2:
        p1, p2 = parts[0].strip(), parts[1].strip()
        if _is_valid_roll_no(p2) and _is_valid_student_name(p1):
            return TeamMember(name=p1.title(), roll_no=p2.upper())
        elif _is_valid_roll_no(p1) and _is_valid_student_name(p2):
            return TeamMember(name=p2.title(), roll_no=p1.upper())

    return None


def _parse_table_row_member(cells: List[str]) -> Optional[TeamMember]:
    """Parse table cells into a TeamMember if they contain student name and roll number."""
    clean = [c.strip() for c in cells if c.strip()]
    if len(clean) < 2:
        return None
    # Skip headers
    if any(h in clean[0].lower() for h in ["name", "sl", "no", "roll", "reg", "student"]):
        if any(h in clean[1].lower() for h in ["name", "roll", "reg", "number", "id"]):
            return None
    if len(clean) >= 3 and clean[0].isdigit():
        c1, c2 = clean[1], clean[2]
    else:
        c1, c2 = clean[0], clean[1]

    if _is_valid_roll_no(c2) and _is_valid_student_name(c1):
        return TeamMember(name=c1.title(), roll_no=c2.strip("()").upper())
    elif _is_valid_roll_no(c1) and _is_valid_student_name(c2):
        return TeamMember(name=c2.title(), roll_no=c1.strip("()").upper())
    return None


def parse_project_doc(doc_path: Path) -> Dict[str, Any]:
    """Extract project title, team members, guide, course code, and abstract from a project document (PDF/DOCX/text)."""
    res: Dict[str, Any] = {
        "title": None,
        "team": [],
        "guide": None,
        "course_code": None,
        "abstract": None,
    }
    if not doc_path.is_file():
        return res

    text_all = ""
    text_p0 = ""
    text_p1 = ""
    docx_team_candidates: List[TeamMember] = []

    try:
        suffix = doc_path.suffix.lower()
        if suffix == ".pdf":
            reader = PdfReader(str(doc_path))
            pages = [p.extract_text() or "" for p in reader.pages]
            text_p0 = pages[0] if len(pages) > 0 else ""
            text_p1 = pages[1] if len(pages) > 1 else ""
            text_all = "\n".join(pages)
        elif suffix == ".docx":
            d = docx.Document(str(doc_path))
            text_all = "\n".join(p.text for p in d.paragraphs if p.text.strip())
            text_p0 = text_all
            for tbl in d.tables:
                for row in tbl.rows:
                    cells = [c.text.strip() for c in row.cells]
                    m = _parse_table_row_member(cells)
                    if m:
                        docx_team_candidates.append(m)
        else:
            text_all = doc_path.read_text(encoding="utf-8", errors="replace")
            text_p0 = text_all
    except Exception as e:
        logger.warning(f"Failed extracting text from {doc_path}: {e}")
        return res

    # 1. Roll numbers
    rolls = re.findall(r"[A-Z]{3}\d{2}[A-Z]{2}\d{3}", text_all or text_p0)

    # 2. Extract Team Members
    if docx_team_candidates:
        res["team"] = docx_team_candidates

    # Extract team members from Cover Page (text_p0)
    if not res["team"] and text_p0:
        lines_p0 = [l.strip() for l in text_p0.splitlines() if l.strip()]
        in_sub_by = False
        cover_team: List[TeamMember] = []
        for l in lines_p0:
            if re.search(r"submitted\s+by", l, re.IGNORECASE):
                in_sub_by = True
                continue
            if in_sub_by:
                if re.search(r"(?:submitted\s+to|guided\s+by|guide|faculty|department|college|microcontrollers)", l, re.IGNORECASE):
                    break
                mem = _parse_member_line(l)
                if mem and mem not in cover_team:
                    cover_team.append(mem)
        if cover_team:
            res["team"] = cover_team

    # Check for two-column layout on cover page if team still empty
    if not res["team"] and text_p0:
        lines = [l for l in text_p0.splitlines() if l.strip()]
        left_items = []
        right_items = []
        has_two_col = False
        for l in lines:
            parts = re.split(r"\s{3,}", l.strip())
            if len(parts) >= 2:
                has_two_col = True
                left_items.append(parts[0])
                right_items.append(parts[1])
            elif len(parts) == 1:
                leading = len(l) - len(l.lstrip())
                if leading > 15 and has_two_col:
                    right_items.append(parts[0])
                else:
                    left_items.append(parts[0])

        if has_two_col:
            for i, item in enumerate(left_items):
                if re.search(r"submitted\s+to", item, re.IGNORECASE):
                    if i + 1 < len(left_items):
                        cand = left_items[i + 1].strip()
                        cand_clean = re.sub(r"^(?:ms\.|mr\.|dr\.|prof\.)\s*", "", cand, flags=re.IGNORECASE)
                        if cand_clean:
                            res["guide"] = cand
                            break

            col_team = []
            for item in right_items:
                mem = _parse_member_line(item)
                if mem:
                    col_team.append(mem)
            if col_team:
                res["team"] = col_team

    # If still no team, check for "Submitted by" block in full text before ABSTRACT
    if not res["team"]:
        in_team_block = False
        block_team = []
        for l in text_all.splitlines():
            l_str = l.strip()
            if not l_str:
                continue
            if re.search(r"(?:submitted\s+by|team\s+members?|students?|prepared\s+by|group\s+members?)", l_str, re.IGNORECASE):
                in_team_block = True
                continue
            if in_team_block and re.search(r"(?:submitted\s+to|guided\s+by|guide|abstract|chapter|contents|table)", l_str, re.IGNORECASE):
                in_team_block = False
            if in_team_block:
                mem = _parse_member_line(l_str)
                if mem and mem not in block_team:
                    block_team.append(mem)
        if block_team:
            res["team"] = block_team

    # If still no team, try to find plain name-only lists after "Group Members" or similar
    if not res["team"]:
        in_names_block = False
        name_only_team = []
        for l in text_all.splitlines():
            l_str = l.strip()
            if not l_str:
                continue
            if re.search(r"(?:group\s+members?|team\s+members?|submitted\s+by|prepared\s+by)", l_str, re.IGNORECASE):
                in_names_block = True
                continue
            if in_names_block:
                # Stop if we hit a section header or non-name content
                if re.search(r"(?:abstract|introduction|chapter|contents|table|guide|department|college|references)", l_str, re.IGNORECASE):
                    break
                # Check if line looks like a plain human name (2-4 words, all alpha)
                name_cand = l_str.strip()
                if _is_valid_student_name(name_cand) and 2 <= len(name_cand.split()) <= 5:
                    name_only_team.append(TeamMember(name=name_cand.title(), roll_no="N/A"))
        if name_only_team:
            res["team"] = name_only_team

    # 3. Guide (if not already extracted)
    if not res["guide"]:
        g_match = re.search(r"(?:SUBMITTED\s+TO|GUIDED\s+BY|FACULTY\s+GUIDE|PROJECT\s+GUIDE|GUIDE)\s*[:\-]?\s*([^\n]+)", text_p0 or text_all, re.IGNORECASE)
        if g_match:
            cand = g_match.group(1).strip()
            cand = re.split(r"\s{3,}", cand)[0].strip()
            cand = cand.split(":")[0].strip()
            if cand and not re.search(r"(?:submitted\s+by|team|roll)", cand, re.IGNORECASE):
                prefix_m = re.match(r"^(Ms|Mr|Dr|Prof)\.?\s*(.*)", cand, re.IGNORECASE)
                if prefix_m:
                    res["guide"] = f"{prefix_m.group(1).title()}. {prefix_m.group(2).strip().title()}"
                else:
                    res["guide"] = cand.title()

    # 4. Course code and Department
    res["department"] = None
    m_dept = re.search(r"DEPARTMENT\s+OF\s+([A-Za-z\s\&\,]+?)(?:\n|\r|\Z|SREE|COLLEGE)", text_all or text_p0, re.IGNORECASE)
    if m_dept:
        dept_name = m_dept.group(1).strip()
        dept_name = re.sub(r"\s+", " ", dept_name)
        res["department"] = f"Department of {dept_name.title()}"

    c_match = re.search(r"\((P[A-Z0-9]+)\)", text_p0)
    if not c_match:
        c_match = re.search(r"\b(P[A-Z0-9]{5,8})\b", text_p0)
    if c_match:
        res["course_code"] = c_match.group(1).strip()

    # 5. Title from cover lines
    lines_p0 = [l.strip() for l in text_p0.splitlines() if l.strip()]
    idx_course = -1
    idx_sub = len(lines_p0)
    for i, l in enumerate(lines_p0):
        if re.search(r"\((?:P[A-Z0-9]+)\)", l) or re.search(r"microcontrollers|microprocessors|embedded", l, re.IGNORECASE):
            idx_course = i
        if re.search(r"submitted\s+(?:to|by)", l, re.IGNORECASE):
            idx_sub = min(idx_sub, i)

    if idx_course != -1 and idx_sub > idx_course:
        cand_lines = lines_p0[idx_course + 1 : idx_sub]
        cand_lines = [
            l for l in cand_lines
            if not re.search(r"(?:project\s+report|mini\s+project|micro\s+project)", l, re.IGNORECASE)
        ]
        if cand_lines:
            cand_title = " ".join(cand_lines).strip()
            cand_title = re.sub(r"\bSTEM32\b", "STM32", cand_title, flags=re.IGNORECASE)
            res["title"] = normalize_title(cand_title, text_p1 or text_all)

    # Universal title: first non-trivial line before "Abstract" or "submitted by"
    if not res["title"] and lines_p0:
        stop_keywords = ["abstract", "submitted", "group member", "team member",
                         "department", "college", "project report", "table of contents"]
        for l in lines_p0:
            l_lower = l.lower().strip()
            if any(kw in l_lower for kw in stop_keywords):
                break
            # Skip very short lines, page numbers, dates
            if len(l.strip()) < 5 or l.strip().isdigit():
                continue
            # Skip lines that are just labels
            if re.match(r"^(?:a\s+)?(?:micro\s+project|mini\s+project|project\s+report|report|internship)\s*$", l, re.IGNORECASE):
                continue
            # This looks like a title line
            cand_title = l.strip()
            if 5 < len(cand_title) < 200:
                res["title"] = normalize_title(cand_title, text_p1 or text_all)
                break

    if not res["title"] and lines_p0:
        # Check lines before 'submitted by' or 'submitted to'
        pre_sub_lines = []
        for l in lines_p0:
            if re.search(r"submitted\s+(?:to|by)", l, re.IGNORECASE):
                break
            if not re.search(r"^(?:a\s+)?(?:micro\s+project|mini\s+project|project\s+report|report|internship)\b", l, re.IGNORECASE):
                pre_sub_lines.append(l)
        if pre_sub_lines:
            cand_title = " ".join(pre_sub_lines).strip()
            if cand_title and len(cand_title) < 150:
                res["title"] = normalize_title(cand_title, text_p1 or text_all)

    if not res["title"] and text_p1:
        m_abs_title = re.search(r"ABSTRACT\s*\n\s*(?:The\s+)?([A-Za-z0-9\s\-]+?)\s+(?:is\s+designed\s+to|is\s+a[n]?\s+)", text_p1, re.IGNORECASE)
        if m_abs_title:
            res["title"] = normalize_title(m_abs_title.group(1).strip(), text_p1 or text_all)

    if not res["title"]:
        # Extract title from abstract opening: "The <ProjectName> project is..." or "<Name> is a..."
        abs_text = res.get("abstract") or text_all
        m_intro = re.search(r"\b(?:The\s+)?([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*)\s+(?:project\s+)?is\s+(?:a[n]?\s+)", abs_text)
        if m_intro:
            res["title"] = normalize_title(m_intro.group(1).strip(), text_p1 or text_all)

    # 6. Abstract
    if text_p1:
        abs_m = re.search(r"ABSTRACT\s*\n\s*(.*)", text_p1, re.DOTALL | re.IGNORECASE)
        if abs_m:
            raw_abs = abs_m.group(1)
            raw_abs = re.sub(r"\s+", " ", raw_abs).strip()
            raw_abs = re.sub(r"16[^\x20-\x7E]?2\s*LCD", "16x2 LCD", raw_abs)
            raw_abs = re.sub(r"\bpro\s+vide\b", "provide", raw_abs)
            raw_abs = re.sub(r"\bwastag\s+e\b", "wastage", raw_abs)
            res["abstract"] = raw_abs
    elif text_all:
        abs_m = re.search(r"ABSTRACT\s*\n\s*(.*?)(?:\n\s*\n\s*[A-Z0-9]|\Z)", text_all, re.DOTALL | re.IGNORECASE)
        if abs_m:
            raw_abs = abs_m.group(1)
            raw_abs = re.sub(r"\s+", " ", raw_abs).strip()
            raw_abs = re.sub(r"16[^\x20-\x7E]?2\s*LCD", "16x2 LCD", raw_abs)
            raw_abs = re.sub(r"\bpro\s+vide\b", "provide", raw_abs)
            raw_abs = re.sub(r"\bwastag\s+e\b", "wastage", raw_abs)
            res["abstract"] = raw_abs

    return res


def analyze_code_content(code_path: Path) -> Dict[str, Any]:
    """Analyze embedded source code file (e.g. water_level_overflow_indicator.ino or plant_monitor.ino) to extract
    project title, pins, components, calibration, thresholds, and operational logic.
    """
    if not code_path.is_file():
        code_text = ""
    else:
        code_text = code_path.read_text(encoding="utf-8", errors="replace")

    # 1. Project Title
    is_water = any(w in code_text.lower() for w in ["water", "overflow", "ultrasonic", "sr04", "trig_pin", "echo_pin", "tank"])
    is_plant = ("plant" in code_text.lower() or "moisture" in code_text.lower()) and not is_water

    if is_water:
        title = "Water Level Detection using STM32 and Ultrasonic Sensor"
    elif is_plant:
        title = "Plant Watering Reminder using STM32 and Soil Moisture Sensor"
    else:
        title = "Automatic Night Lamp using STM32 and LDR"

    title_match = re.search(r"PROJECT\s*\d*[:\-]?\s*([A-Za-z0-9_\s]+)", code_text, re.IGNORECASE)
    if title_match:
        cand = title_match.group(1).split("\n")[0].strip()
        if len(cand) > 5 and len(cand) < 80:
            title = normalize_title(cand, code_text[:2000])

    # 2. Extract Pins
    pins: List[PinMapping] = []
    pin_defs = re.findall(r"#define\s+([A-Za-z0-9_]+)\s+([P][A-E]\d+)", code_text)
    pin_comments = {
        name: comment
        for name, comment in re.findall(
            r"#define\s+([A-Za-z0-9_]+)\s+[P][A-E]\d+\s*//\s*(.*)", code_text
        )
    }

    if pin_defs:
        for name, pin_str in pin_defs:
            desc = pin_comments.get(name, f"{name} control pin")
            periph = "GPIO"
            if "TRIG" in name or (pin_str == "PB0" and is_water):
                periph = "GPIO"
                conn = "HC-SR04 Ultrasonic Trigger Pin"
                func = "10us ultrasonic pulse generation trigger"
            elif "ECHO" in name or (pin_str == "PB1" and is_water):
                periph = "GPIO/Timer"
                conn = "HC-SR04 Ultrasonic Echo Pin"
                func = "High-speed ultrasonic pulse duration echo measurement"
            elif "LED_RED" in name or (pin_str == "PA2" and is_water):
                periph = "GPIO"
                conn = "Red Alert LED"
                func = "Visual indicator for critical high water level and overflow"
            elif "LED_GREEN" in name or (pin_str == "PA3" and is_water):
                periph = "GPIO"
                conn = "Green Status LED"
                func = "Visual indicator for safe and normal water level"
            elif "SCL" in name or (pin_str == "PB6" and is_water):
                periph = "I2C1_SCL"
                conn = "16x2 LCD I2C SCL"
                func = "I2C clock line for 16x2 alphanumeric LCD display"
            elif "SDA" in name or (pin_str == "PB7" and is_water):
                periph = "I2C1_SDA"
                conn = "16x2 LCD I2C SDA"
                func = "I2C data line for 16x2 alphanumeric LCD display"
            elif "SENSOR" in name or ("MOISTURE" in name) or (pin_str == "PA0" and is_plant):
                periph = "ADC1_IN0"
                conn = "Soil Moisture Sensor Analog Out"
                func = "Analog soil moisture voltage measurement (12-bit ADC)"
            elif "LDR" in name or (pin_str == "PA1" and not is_plant and not is_water):
                periph = "ADC1_IN1"
                conn = "LDR Voltage Divider Output"
                func = "Analog ambient light voltage measurement (12-bit ADC)"
            elif "BUZZER" in name or (pin_str == "PA1" and (is_plant or is_water)):
                periph = "GPIO"
                conn = "Active Buzzer Alert Driver"
                func = "Audible alarm trigger on critical alert condition"
            elif "LAMP" in name or pin_str == "PA8":
                periph = "TIM1_CH1"
                conn = "Night Lamp / Relay Module / LED Driver"
                func = "PWM brightness control or digital relay switching"
            elif "LED" in name or pin_str == "PC13":
                periph = "GPIOC"
                conn = "Onboard Status LED"
                func = "Active-low status alert indicator (lights when action needed)"
            else:
                conn = name
                func = desc

            pins.append(
                PinMapping(
                    pin=pin_str,
                    peripheral=periph,
                    connected_to=conn,
                    function=func,
                )
            )

    # If serial is initialized in setup, add USART pins
    if "Serial.begin" in code_text or not pins:
        pins.append(
            PinMapping(
                pin="PA9",
                peripheral="USART1_TX",
                connected_to="USB-to-UART Adapter RX",
                function="Serial telemetry and debug monitoring at 115200 baud",
            )
        )
        pins.append(
            PinMapping(
                pin="PA10",
                peripheral="USART1_RX",
                connected_to="USB-to-UART Adapter TX",
                function="Serial receiver interface",
            )
        )

    # 3. Extract Constants & Thresholds
    constants: Dict[str, Any] = {}
    for match in re.finditer(
        r"(?:const\s+[A-Za-z0-9_]+\s+([A-Za-z0-9_]+)\s*=\s*([^;]+);|#define\s+([A-Za-z0-9_]+)\s+([0-9\.]+))",
        code_text,
    ):
        name = match.group(1) or match.group(3)
        val_str = (match.group(2) or match.group(4)).split("//")[0].strip()
        try:
            val = int(val_str, 0)
        except ValueError:
            try:
                val = float(val_str)
            except ValueError:
                val = val_str
        constants[name] = val

    # 4. Default components for detected sensors
    if is_water:
        components: List[ComponentItem] = [
            ComponentItem(
                name="STM32F103C8T6 Development Board (Blue Pill)",
                role="Central Processing Unit",
                interface="On-board / 72 MHz ARM Cortex-M3",
                notes="64 KB Flash, 20 KB SRAM, 12-bit ADC, Timer PWM",
            ),
            ComponentItem(
                name="HC-SR04 Ultrasonic Distance Sensor Module",
                role="Non-Contact Water Level Measurement",
                interface="Digital Trigger (PB0) & Echo (PB1)",
                notes="Acoustic time-of-flight ranging from 2 cm to 400 cm with 3mm precision",
            ),
            ComponentItem(
                name="16x2 Alphanumeric LCD Display (with I2C PCF8574)",
                role="Local Telemetry & Level Display",
                interface="I2C Bus (PB6 SCL, PB7 SDA at 0x27)",
                notes="Displays real-time distance in cm and system status warnings",
            ),
            ComponentItem(
                name="Active Buzzer Alert Module",
                role="Audible Overflow Warning",
                interface="Digital GPIO (PA1)",
                notes="Active high sounder activates when water level reaches overflow threshold",
            ),
            ComponentItem(
                name="Red & Green Status Indicator LEDs",
                role="Visual Level State Annunciation",
                interface="Digital GPIOs (PA2 Red, PA3 Green)",
                notes="Red indicates overflow alarm; Green indicates normal safe water level",
            ),
            ComponentItem(
                name="LM2596 DC-DC Buck Converter Step-Down Module",
                role="Regulated Power Supply",
                interface="Input 9V-12V Battery, Regulated 5V DC Output",
                notes="High-efficiency step-down switching regulator supplying stable 5V rail",
            ),
            ComponentItem(
                name="Onboard Indicator LED (PC13)",
                role="Visual Diagnostic Indicator",
                interface="GPIO (Active-Low)",
                notes="Illuminates automatically during overflow alert states",
            ),
            ComponentItem(
                name="ST-Link V2 In-Circuit Programmer",
                role="Firmware Flashing & Debugging",
                interface="SWD (SWDIO, SWCLK)",
                notes="Serial Wire Debug interface for STM32 programming",
            ),
        ]
        overflow_cm = constants.get("OVERFLOW_THRESHOLD_CM", 15)
        warning_cm = constants.get("WARNING_THRESHOLD_CM", 30)
        tests = [
            TestCase(
                id="TC_01",
                name="High Water Level / Critical Overflow Alert Test",
                condition=f"Measured Distance <= {overflow_cm} cm (Tank > 85% full)",
                expected="Red LED ON, Active Buzzer sounding, Green LED OFF, LCD shows 'OVERFLOW ALERT!'",
                observed=None,
            ),
            TestCase(
                id="TC_02",
                name="Approaching High Level Warning Test",
                condition=f"{overflow_cm + 1} cm <= Measured Distance <= {warning_cm} cm (70% to 85% full)",
                expected="Red LED ON, Green LED ON, Buzzer OFF, LCD shows 'LEVEL HIGH'",
                observed=None,
            ),
            TestCase(
                id="TC_03",
                name="Normal / Safe Water Level Verification Test",
                condition=f"Measured Distance > {warning_cm} cm (Tank < 70% full)",
                expected="Green LED ON, Red LED OFF, Buzzer OFF, LCD shows 'STATUS: NORMAL'",
                observed=None,
            ),
        ]
    elif is_plant:
        components: List[ComponentItem] = [
            ComponentItem(
                name="STM32F103C8T6 Development Board (Blue Pill)",
                role="Central Processing Unit",
                interface="On-board / 72 MHz ARM Cortex-M3",
                notes="64 KB Flash, 20 KB SRAM, 12-bit ADC, Timer PWM",
            ),
            ComponentItem(
                name="Soil Moisture Sensor Module",
                role="Soil Moisture Measurement",
                interface="Analog Voltage Output (PA0)",
                notes="Capacitive / Resistive soil moisture probe reading 1300 to 3700 raw ADC",
            ),
            ComponentItem(
                name="Active Buzzer Alert Module",
                role="Audible Notification",
                interface="Digital GPIO (PA1)",
                notes="Active high sounder activates when moisture is below threshold",
            ),
            ComponentItem(
                name="Onboard Indicator LED (PC13)",
                role="Visual State Indicator",
                interface="GPIO (Active-Low)",
                notes="Illuminates automatically when plant needs watering",
            ),
            ComponentItem(
                name="ST-Link V2 In-Circuit Programmer",
                role="Firmware Flashing & Debugging",
                interface="SWD (SWDIO, SWCLK)",
                notes="Serial Wire Debug interface for STM32 programming",
            ),
        ]
        tests = [
            TestCase(
                id="TC_01",
                name="Dry Soil Alert Test",
                condition="Raw ADC >= 2981 (Moisture < 30%)",
                expected="Buzzer ACTIVE (HIGH), Status LED PC13 ON (LOW)",
                observed=None,
            ),
            TestCase(
                id="TC_02",
                name="Optimal Moisture Inactive Test",
                condition="1757 <= Raw ADC <= 2980 (Moisture 30% to 80%)",
                expected="Buzzer INACTIVE (LOW), Status LED PC13 OFF (HIGH)",
                observed=None,
            ),
            TestCase(
                id="TC_03",
                name="Excess Moisture / Saturated Test",
                condition="Raw ADC <= 1756 (Moisture > 80%)",
                expected="Buzzer INACTIVE (LOW), Status LED PC13 OFF (HIGH)",
                observed=None,
            ),
        ]
    else:
        components = [
            ComponentItem(
                name="STM32F103C8T6 Development Board (Blue Pill)",
                role="Central Processing Unit",
                interface="On-board / 72 MHz ARM Cortex-M3",
                notes="64 KB Flash, 20 KB SRAM, 12-bit ADC, Timer PWM",
            ),
            ComponentItem(
                name="Light Dependent Resistor (LDR) & 10kΩ Resistor",
                role="Ambient Light Sensing",
                interface="Analog Voltage Divider (PA1)",
                notes="Resistance decreases with illumination, output ~300 (dark) to ~3500 (light)",
            ),
            ComponentItem(
                name="Night Lamp Output Module / High-Power LED",
                role="Illumination Actuator",
                interface="PWM / Digital Output (PA8)",
                notes="Hardware Timer TIM1 PWM (0-255 duty cycle) or 5V relay drive",
            ),
            ComponentItem(
                name="Onboard Indicator LED (PC13)",
                role="Visual State Indicator",
                interface="GPIO (Active-Low)",
                notes="Illuminates automatically when the night lamp is engaged",
            ),
            ComponentItem(
                name="ST-Link V2 In-Circuit Programmer",
                role="Firmware Flashing & Debugging",
                interface="SWD (SWDIO, SWCLK)",
                notes="Serial Wire Debug interface for STM32 programming",
            ),
        ]
        dark_on = constants.get("THRESHOLD_DARK_ON", 1400)
        light_off = constants.get("THRESHOLD_LIGHT_OFF", 1800)
        tests = [
            TestCase(
                id="TC_01",
                name="Daylight Inactivation Test",
                condition=f"Ambient light high (LDR ADC > {light_off})",
                expected="Lamp OFF, Status LED PC13 OFF (HIGH), PWM duty 0",
                observed=None,
            ),
            TestCase(
                id="TC_02",
                name="Hysteresis Dusk/Twilight Stability Test",
                condition=f"Ambient light fluctuating within hysteresis band ({dark_on} to {light_off})",
                expected="System latches existing state without high-frequency toggling or flickering",
                observed=None,
            ),
            TestCase(
                id="TC_03",
                name="Night Darkness Activation Test",
                condition=f"Ambient light drops below threshold (LDR ADC < {dark_on})",
                expected="Lamp engaged, Status LED PC13 ON (LOW), PWM duty scaled from 60 to 255",
                observed=None,
            ),
        ]

    return {
        "title": title,
        "pins": pins,
        "components": components,
        "constants": constants,
        "tests": tests,
    }


def llm_extract_project_metadata(text: str, llm: Optional[Any] = None) -> Dict[str, Any]:
    """Run abstract or project document through LLM to extract key project entities:
    title, domain, course, students, guide, HOD, framework, components, categories, tests, references.
    """
    if not text or len(text.strip()) < 20:
        return {}

    prompt = f"""Read the project document/abstract below and extract key project entities into a JSON object.

PROJECT DOCUMENT:
{text[:3000]}

Respond ONLY with a valid JSON object matching this exact schema:

{{
  "title": "Exact Title of the project in title case",
  "domain": "One of: MACHINE_LEARNING, EMBEDDED_IOT, EMBEDDED_HARDWARE, SOFTWARE_APP, WEB_CLOUD, DATA_ANALYTICS, COA_SYSTEMS, GENERAL_ACADEMIC",
  "course_name": "Suggested course name",
  "course_code": "Course code if found or PBCST504",
  "students": [
    {{"name": "Full Student Name", "roll_no": "Roll number or N/A"}}
  ],
  "guide": {{"name": "Faculty guide name if found", "designation": "Assistant Professor"}},
  "hod": {{"name": "Dr. S Suresh Babu", "designation": "Professor and Head"}},
  "framework": "Primary technology or framework (e.g. Python / scikit-learn / OpenCV)",
  "platform": "Runtime or hardware (e.g. Python ML Environment)",
  "modules": [
    "Module 1",
    "Module 2",
    "Module 3"
  ],
  "components": [
    {{"name": "Component Name", "role": "Role in system", "interface": "Interface/Protocol", "notes": "Notes"}}
  ],
  "categories": ["Category 1", "Category 2", "Category 3"],
  "tests": [
    {{"id": "TC_01", "name": "Test Name", "condition": "Condition", "expected": "Expected result"}}
  ],
  "references": [
    "Academic Reference 1",
    "Academic Reference 2"
  ]
}}


"""


    content = None
    import requests
    from reportsmith.llm import clean_reasoning_harmony
    import json

    # 1. Try provided LLMClient instance
    def _is_service_online(url: str, timeout: float = 0.5) -> bool:
        try:
            import urllib.parse, socket
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname or "localhost"
            port = parsed.port or (80 if parsed.scheme == "http" else 443)
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False

    if llm and hasattr(llm, "base_url") and _is_service_online(llm.base_url):
        try:
            url = f"{llm.base_url}/chat/completions"
            payload = {
                "model": llm.model,
                "messages": [
                    {"role": "system", "content": "You are a specialized JSON extraction API. Output ONLY valid JSON starting with '{'. Never repeat or echo the input text, and do not include markdown backticks or commentary."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 1200,
            }
            effective_timeout = max(180, int(getattr(llm, "timeout", 300) or 300)) if llm else 180
            resp = requests.post(url, json=payload, timeout=effective_timeout)
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"Direct LLM metadata extraction failed: {e}")

    # 2. Try default local endpoints if no content yet
    if not content:
        for ep in ["http://localhost:1234/v1", "http://localhost:11434/v1"]:
            if not _is_service_online(ep):
                continue
            try:
                resp = requests.post(
                    f"{ep}/chat/completions",
                    json={
                        "model": "qwen2.5-coder-3b" if "1234" in ep else "gpt-oss:20b",
                        "messages": [
                            {"role": "system", "content": "You are a specialized JSON extraction API. Output ONLY valid JSON starting with '{'. Never repeat or echo the input text, and do not include markdown backticks or commentary."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.0,
                        "max_tokens": 1200,
                    },
                    timeout=3,
                )
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    break
            except Exception:
                continue

    if not content:
        return {}

    content_clean = clean_reasoning_harmony(content)
    m_json = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content_clean, re.DOTALL)
    if m_json:
        content_clean = m_json.group(1)
    else:
        m_brace = None
    idx = content_clean.find("{")
    while idx != -1:
        try:
            data, _ = json.JSONDecoder().raw_decode(content_clean[idx:])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        idx = content_clean.find("{", idx + 1)



    try:
        data = json.loads(content_clean)
        if isinstance(data, dict):
            return data
    except Exception:
        try:
            cleaned = re.sub(r",\s*([}\]])", r"\1", content_clean)
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not parse LLM metadata JSON: {e}")

    return {}


def analyze_content_folder(
    content_dir: Union[str, Path] = "Content",
    sample_docx_path: Optional[Union[str, Path]] = "COA_report.docx",
    llm: Optional[Any] = None,
) -> Tuple[ProjectBrief, ProjectFacts, ReportOutline]:
    """Comprehensively analyze files in Content/ folder and sample docx
    to produce a deterministic ProjectBrief, ProjectFacts, and ReportOutline.
    Dynamically adapts to any subject/domain (Machine Learning, Embedded IoT, Software, etc.).
    """
    c_dir = Path(content_dir)
    if not c_dir.is_dir():
        raise FileNotFoundError(f"Content directory not found: {c_dir}")

    # 1. Locate format/rules file in Content/
    rules_file = None
    for f in c_dir.iterdir():
        name_l = f.name.lower()
        if f.suffix.lower() in (".pdf", ".txt", ".md", ".docx") and any(
            k in name_l for k in ("rule", "format", "syllabus", "guideline")
        ):
            rules_file = f
            break

    # 2. Locate project specification document in Content/ (e.g. Project.docx, Project.pdf, abstract.pdf)
    project_doc = None
    for f in c_dir.iterdir():
        name_l = f.name.lower()
        if f.suffix.lower() in (".pdf", ".docx", ".txt", ".md") and f != rules_file:
            if any(k in name_l for k in ("project", "abstract", "brief", "spec")):
                project_doc = f
                break
    if not project_doc:
        for f in c_dir.iterdir():
            if f.suffix.lower() in (".pdf", ".docx", ".txt", ".md") and f != rules_file:
                project_doc = f
                break

    doc_meta = parse_project_doc(project_doc) if project_doc else {}

    # Extract raw text from project doc for LLM pre-analysis
    project_text = ""
    if project_doc and project_doc.is_file():
        try:
            if project_doc.suffix.lower() == ".docx":
                d_tmp = docx.Document(str(project_doc))
                project_text = "\n".join(p.text.strip() for p in d_tmp.paragraphs if p.text.strip())
            elif project_doc.suffix.lower() == ".pdf":
                r_tmp = PdfReader(str(project_doc))
                project_text = "\n".join(p.extract_text() or "" for p in r_tmp.pages)
            else:
                project_text = project_doc.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Failed extracting raw text from {project_doc}: {e}")

    # Run LLM pre-analysis to extract title, students, coordinators, components
    llm_meta = {}
    if project_text:
        timeout_disp = getattr(llm, "timeout", 300) if llm else 300
        logger.info(f"Running document text through LLM pre-analysis (timeout: {timeout_disp}s)...")
        llm_meta = llm_extract_project_metadata(project_text, llm=llm)
        if llm_meta:
            logger.info(f"LLM extracted title='{llm_meta.get('title')}', domain='{llm_meta.get('domain')}', students={len(llm_meta.get('students', []))}")

    # 3. Locate source code file in Content/
    code_file = None
    for f in c_dir.iterdir():
        if f.suffix.lower() in (".ino", ".c", ".cpp", ".h", ".asm", ".s", ".java", ".py", ".cs", ".js", ".ts", ".rs", ".go"):
            code_file = f
            break

    if not code_file:
        for cand_name in [
            "Water_Level_Overflow_Indicator.ino",
            "water_level_overflow_indicator.ino",
            "water_level_indicator.ino",
            "plant_watering_reminder.ino",
        ]:
            cand = c_dir / cand_name
            if cand.is_file():
                code_file = cand
                break

    if code_file and code_file.suffix.lower() in (".ino", ".c", ".cpp", ".h"):
        logger.info(f"Using source code file: {code_file}")
        code_info = analyze_code_content(code_file)
    else:
        code_info = {
            "title": doc_meta.get("title") or "Technical Project Report",
            "pins": [],
            "components": [],
            "constants": {},
            "tests": [
                TestCase(
                    id="TC_01",
                    name="Core Functional Verification Test",
                    condition="Standard system input parameters and valid authentication credentials",
                    expected="System processes input, updates state accurately, and displays confirmed status output",
                    observed=None,
                ),
                TestCase(
                    id="TC_02",
                    name="Boundary Condition and Input Validation Test",
                    condition="Input parameters at boundary thresholds or invalid formatting",
                    expected="System rejects invalid stimuli gracefully with appropriate error notifications",
                    observed=None,
                ),
                TestCase(
                    id="TC_03",
                    name="System Recovery and State Consistency Test",
                    condition="Simulated interruption or sudden operational boundary change",
                    expected="System maintains data integrity without corruption or unhandled exception",
                    observed=None,
                ),
            ],
        }

    # Determine Title
    final_title = None
    if llm_meta.get("title") and len(llm_meta["title"].strip()) > 5:
        cand = llm_meta["title"].strip()
        if not cand.lower().startswith("exact title"):
            final_title = cand
    if not final_title:
        final_title = doc_meta.get("title") or code_info["title"] or "Technical Project Report"

    final_abstract = doc_meta.get("abstract") or project_text or ""
    final_title = normalize_title(final_title, final_abstract)

    # Classify domain
    from reportsmith.trainer import (
        classify_text_domain,
        DOMAIN_ML,
        DOMAIN_COA,
        DOMAIN_WEB,
        DOMAIN_DATA,
        DOMAIN_SOFTWARE,
        DOMAIN_EMBEDDED,
        DOMAIN_BIOINFORMATICS,
    )
    combined_corpus = final_title + " " + final_abstract
    if code_file and code_file.is_file():
        try:
            combined_corpus += " " + code_file.read_text(encoding="utf-8", errors="replace")[:2000]
        except Exception:
            pass

    domain = None
    kw_domain = classify_text_domain(combined_corpus)
    if kw_domain != "GENERAL_ACADEMIC":
        domain = kw_domain
    elif llm_meta.get("domain") in (DOMAIN_BIOINFORMATICS, DOMAIN_ML, DOMAIN_COA, DOMAIN_WEB, DOMAIN_DATA, DOMAIN_SOFTWARE, DOMAIN_EMBEDDED):
        domain = llm_meta["domain"]
    else:
        domain = kw_domain

    # 4. Synthesize or parse outline
    raw_modules = llm_meta.get("modules") or []
    modules = [
        m for m in raw_modules
        if len(m.strip()) > 3 and not re.match(r"^module\s*\d+$", m.strip(), re.IGNORECASE) and m.strip() not in ("1", "2", "3")
    ]
    if not modules:
        modules = extract_dynamic_subsystems(final_abstract, domain=domain, title=final_title)

    if rules_file:
        logger.info(f"Using rules/format file found in Content: {rules_file}")
        outline = parse_format_file(rules_file)
    else:
        logger.info(
            f"No rules file found in Content folder. Synthesizing universal outline for domain '{domain}'..."
        )
        outline = synthesize_universal_outline(
            domain=domain,
            title=final_title,
            abstract_text=final_abstract,
            modules=modules,
            has_code=bool(code_file),
        )

    # 5. Extract template styling & metadata from sample docx
    sample_p = Path(sample_docx_path) if sample_docx_path else (
        Path("required.docx") if Path("required.docx").is_file() else Path("COA_report.docx")
    )
    template_meta = extract_template_metadata(sample_p)

    is_water = domain in (DOMAIN_EMBEDDED, "EMBEDDED_IOT") and ("water" in final_title.lower() or "overflow" in final_title.lower() or "ultrasonic" in final_title.lower() or (final_abstract and "water" in final_abstract.lower()))
    is_plant = domain in (DOMAIN_EMBEDDED, "EMBEDDED_IOT") and ("plant" in final_title.lower() or "moisture" in final_title.lower()) and not is_water

    if is_water and not ("water" in code_info["title"].lower() or "overflow" in code_info["title"].lower()):
        water_ino = c_dir / "Water_Level_Overflow_Indicator.ino"
        if water_ino.is_file():
            code_file = water_ino
            code_info = analyze_code_content(code_file)

    # Team Members
    final_team: List[TeamMember] = []
    if llm_meta.get("students"):
        for s in llm_meta["students"]:
            if isinstance(s, dict) and s.get("name"):
                s_name = s["name"].strip()
                if _is_valid_student_name(s_name) and not s_name.lower().startswith("full student"):
                    r_no = s.get("roll_no") or "N/A"
                    if "roll number" in r_no.lower():
                        r_no = "N/A"
                    final_team.append(TeamMember(name=s_name.title(), roll_no=r_no))

    if not final_team:
        final_team = doc_meta.get("team") or []

    if not final_team:
        for f in c_dir.iterdir():
            if f == project_doc or f == rules_file:
                continue
            if f.suffix.lower() in (".pdf", ".docx", ".txt", ".md"):
                sub_meta = parse_project_doc(f)
                if sub_meta.get("team"):
                    final_team = sub_meta["team"]
                    break
            elif f.suffix.lower() in (".ino", ".c", ".cpp", ".h"):
                try:
                    c_text = f.read_text(encoding="utf-8", errors="replace")
                    code_members = []
                    for l in c_text.splitlines():
                        mem = _parse_member_line(l.strip().lstrip("/*# "))
                        if mem and mem not in code_members:
                            code_members.append(mem)
                    if code_members:
                        final_team = code_members
                        break
                except Exception:
                    pass

    if not final_team:
        logger.warning("No team members found in Content folder. Using placeholder.")
        final_team = [TeamMember(name="Student Name", roll_no="Roll Number")]

    # Faculty Guide
    final_guide = None
    if isinstance(llm_meta.get("guide"), dict) and llm_meta["guide"].get("name"):
        g_name = llm_meta["guide"]["name"].strip()
        if not g_name.lower().startswith("faculty guide"):
            final_guide = g_name
    if not final_guide:
        final_guide = doc_meta.get("guide")
    if not final_guide:
        for f in c_dir.iterdir():
            if f == project_doc or f == rules_file:
                continue
            if f.suffix.lower() in (".pdf", ".docx", ".txt", ".md"):
                sub_meta = parse_project_doc(f)
                if sub_meta.get("guide"):
                    final_guide = sub_meta["guide"]
                    break
    if not final_guide:
        final_guide = "Ms. Aryalekshmi A"

    final_course_code = "PBCST504"
    raw_code = llm_meta.get("course_code") or doc_meta.get("course_code")
    if raw_code and isinstance(raw_code, str):
        c_str = raw_code.strip()
        if "if found" not in c_str.lower() and "placeholder" not in c_str.lower() and len(c_str.split()) <= 2:
            final_course_code = c_str

    # Department
    detected_dept = doc_meta.get("department")
    if not detected_dept:
        if "computer science" in combined_corpus.lower():
            detected_dept = "Department of Computer Science & Engineering"
        else:
            detected_dept = template_meta["department"]

    # Course name based on domain
    domain_course_map = {
        DOMAIN_SOFTWARE: "Software Engineering & Application Development",
        DOMAIN_ML: "Machine Learning & Artificial Intelligence",
        DOMAIN_WEB: "Cloud Computing & Web Services",
        DOMAIN_DATA: "Big Data Analytics & Business Intelligence",
        DOMAIN_BIOINFORMATICS: "Bioinformatics & Computational Biology",
        DOMAIN_EMBEDDED: "Microcontrollers",
        "EMBEDDED_IOT": "Microcontrollers",
        DOMAIN_COA: "Computer Organization & Architecture",
    }
    course_name = domain_course_map.get(domain, "Software Engineering & Application Development")

    project_info = ProjectInfo(
        title=final_title,
        course_code=final_course_code,
        course_name=course_name,
        semester="V Semester B.Tech",
        month_year=template_meta["month_year"],
        academic_year=template_meta["academic_year"],
        college=template_meta["college"],
        department=detected_dept,
        university_or_autonomy_line=template_meta["university_line"],
        address=template_meta["address"],
    )

    # HOD is Dr. S Suresh Babu, coordinator is None (Prof S Mukherjee removed as per user instruction)
    people_info = PeopleInfo(
        team=final_team,
        guide=StaffMember(name=final_guide, designation="Assistant Professor", department=detected_dept),
        hod=StaffMember(name="Dr. S Suresh Babu", designation="Professor and Head", department=detected_dept),
        coordinator=None,
    )

    # Synthesize implementation, behaviour, and references
    impl_info, behaviour_info, project_refs = _synthesize_implementation_from_abstract(
        domain=domain,
        title=final_title,
        abstract_text=final_abstract,
        code_file=code_file,
        code_info=code_info,
        combined_corpus=combined_corpus,
        llm_meta=llm_meta,
    )

    brief = ProjectBrief(
        project=project_info,
        people=people_info,
        implementation=impl_info,
        behaviour=behaviour_info,
        code_file=str(code_file) if code_file else None,
        abstract=final_abstract,
        tests=code_info["tests"] if (code_file and code_info.get("pins")) else impl_info_tests(domain, llm_meta, combined_corpus),
        references=project_refs,
        domain=domain,
        extra_notes=f"{final_title} project report.",
    )

    facts = derive_facts(brief, base_dir=Path("."))

    return brief, facts, outline


def impl_info_tests(domain: str, llm_meta: Dict[str, Any], combined_corpus: str = "") -> List[TestCase]:
    """Generate domain-relevant test cases."""
    d = (domain or "").upper()

    if d == "BIOINFORMATICS":
        return [
            TestCase(
                id="TC_01",
                name="FASTA Sequence Parsing & Validation Test",
                condition="Input synthetic FASTA file with standard nucleotide sequences (A, C, G, T)",
                expected="Ingestion pipeline successfully parses sequence records without data corruption or unhandled exception",
                observed=None,
            ),
            TestCase(
                id="TC_02",
                name="Sequence Alignment & Scoring Matrix Verification Test",
                condition="Pairwise alignment of homologous sequences with known match/mismatch scores and gap penalties",
                expected="Alignment engine computes optimal alignment score matching theoretical dynamic programming matrix",
                observed=None,
            ),
            TestCase(
                id="TC_03",
                name="Degenerate Sequence & Boundary Handling Test",
                condition="Input sequence containing ambiguous IUPAC codes or empty sequence buffers",
                expected="System handles boundary input gracefully with appropriate validation warning or rejection",
                observed=None,
            ),
            TestCase(
                id="TC_04",
                name="Sensitivity & Speedup Benchmarking Test",
                condition="High-throughput batch comparison against baseline sequence datasets",
                expected="System achieves expected alignment sensitivity (>= 99%) and execution speedup over standard tools",
                observed=None,
            ),
        ]
    elif d in ("WEB_CLOUD", "SOFTWARE_APP"):
        return [
            TestCase(
                id="TC_01",
                name="User Authentication and Session Verification Test",
                condition="Client submits valid user login credentials to authentication endpoint",
                expected="System verifies credentials, issues session/token (JWT/bcrypt), and grants access to protected routes",
                observed=None,
            ),
            TestCase(
                id="TC_02",
                name="Transaction Processing and State Consistency Test",
                condition="Client dispatches data mutation or entity registration request through API interface",
                expected="Atomic database transaction updates record state and returns HTTP 200/201 confirmation",
                observed=None,
            ),
            TestCase(
                id="TC_03",
                name="Role-Based Access Control & Unauthorized Access Test",
                condition="Unauthorized client attempt to invoke administrative or restricted endpoint",
                expected="Security middleware intercepts request, returning HTTP 401 or 403 status code gracefully",
                observed=None,
            ),
            TestCase(
                id="TC_04",
                name="Boundary Condition & High Concurrency Load Test",
                condition="Concurrent simulated client requests near peak system capacity",
                expected="System maintains average latency below SLA threshold without data loss or memory leaks",
                observed=None,
            ),
        ]
    elif d in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
        return [
            TestCase(
                id="TC_01",
                name="Dataset Ingestion and Preprocessing Verification Test",
                condition="Input raw feature matrices or sample images with standard dimensions",
                expected="Pipeline applies normalization and feature extraction producing clean tensor/vector representations",
                observed=None,
            ),
            TestCase(
                id="TC_02",
                name="Inference and Decision Boundary Test",
                condition="Input validation vectors across distinct representative target categories",
                expected="Classification model assigns correct category labels with confidence score >= 85%",
                observed=None,
            ),
            TestCase(
                id="TC_03",
                name="Outlier and Degraded Input Robustness Test",
                condition="Input samples with artificial noise, extreme values, or corrupted features",
                expected="System flags uncertain inputs or assigns reject status without pipeline termination",
                observed=None,
            ),
            TestCase(
                id="TC_04",
                name="Performance Metric and Generalization Evaluation Test",
                condition="Evaluation over held-out test partition",
                expected="Achieves targeted benchmark accuracy, precision, and recall metrics matching specifications",
                observed=None,
            ),
        ]
    elif d in ("EMBEDDED_HARDWARE", "EMBEDDED_IOT"):
        return [
            TestCase(
                id="TC_01",
                name="Sensor Acquisition and ADC Conversion Test",
                condition="Stimulus applied to analog sensor input pins (e.g. PA0)",
                expected="ADC registers read and convert raw counts within expected electrical operating range",
                observed=None,
            ),
            TestCase(
                id="TC_02",
                name="Threshold Detection and Alert Actuation Test",
                condition="Measured physical quantity crosses predefined critical setpoint threshold",
                expected="Firmware logic triggers output GPIO (e.g. status LED on PC13 or buzzer) within latency limit",
                observed=None,
            ),
            TestCase(
                id="TC_03",
                name="Sensor Disconnection and Fault Safety Test",
                condition="Disconnected or saturated sensor signal line",
                expected="Firmware detects out-of-bounds ADC reading and asserts safety fallback state",
                observed=None,
            ),
        ]
    return [
        TestCase(
            id="TC_01",
            name="Core Functional Verification Test",
            condition="Standard system input parameters and valid authentication credentials",
            expected="System processes input, updates state accurately, and displays confirmed status output",
            observed=None,
        ),
        TestCase(
            id="TC_02",
            name="Boundary Condition and Input Validation Test",
            condition="Input parameters at boundary thresholds or invalid formatting",
            expected="System rejects invalid stimuli gracefully with appropriate error notifications",
            observed=None,
        ),
        TestCase(
            id="TC_03",
            name="System Recovery and State Consistency Test",
            condition="Simulated interruption or sudden operational boundary change",
            expected="System maintains data integrity without corruption or unhandled exception",
            observed=None,
        ),
    ]


def _synthesize_implementation_from_abstract(
    domain: str,
    title: str,
    abstract_text: str,
    code_file: Optional[Path],
    code_info: Dict[str, Any],
    combined_corpus: str,
    llm_meta: Dict[str, Any],
) -> tuple:
    """Dynamically derive implementation details, behaviour, and references from abstract and domain."""
    abs_lower = (abstract_text or "").lower()
    title_lower = title.lower()

    # 1. Determine hardware mode
    has_code_pins = bool(code_file and len(code_info.get("pins", [])) > 0)
    is_hw = (domain in ("EMBEDDED_HARDWARE", "EMBEDDED_IOT")) or has_code_pins

    # 2. Extract dynamic modules (3-5 functional subsystems)
    raw_modules = llm_meta.get("modules") or []
    modules = [
        m for m in raw_modules
        if len(m.strip()) > 3 and not re.match(r"^module\s*\d+$", m.strip(), re.IGNORECASE) and m.strip() not in ("1", "2", "3")
    ]
    if not modules:
        modules = extract_dynamic_subsystems(abstract_text, domain=domain, title=title)

    # 3. Detect categories/states from abstract or llm_meta
    detected_cats = []
    cat_match = re.search(
        r"(?:such as|category|categories|classes|tiers|predict(?:ing)?\s+the)[:\s]+([^.]+?)(?:\s+(?:is|are|decided|using|with|to|\.))",
        abstract_text or "",
        re.IGNORECASE,
    )
    if cat_match:
        parts = re.split(r",\s*(?:or\s+)?|\s+or\s+", cat_match.group(1).strip())
        for p in parts:
            p_clean = p.strip().strip("'\"").upper()
            if 2 <= len(p_clean) <= 30 and not any(w in p_clean.lower() for w in ("algorithm", "model", "tree", "forest", "system", "project")):
                detected_cats.append(p_clean)

    raw_cats = llm_meta.get("categories") or detected_cats
    cleaned_cats = [
        c for c in raw_cats
        if len(c.strip()) > 2 and not re.match(r"^category\s*\d+$", c.strip(), re.IGNORECASE)
    ]

    # 4. Synthesize Platform & Implementation
    if is_hw:
        mcu_name = "STM32 Blue Pill"
        mcu_part = "STM32F103C8T6"
        core = "ARM Cortex-M3"
        clock = "72 MHz"
        flash = "64 KB"
        sram = "20 KB"
        voltage = "3.3V"

        if "esp32" in combined_corpus.lower():
            mcu_name = "ESP32 DevKit"
            mcu_part = "ESP32-WROOM-32"
            core = "Xtensa Dual-Core 32-bit LX6"
            clock = "240 MHz"
            flash = "4 MB"
            sram = "520 KB"
        elif "atmega" in combined_corpus.lower() or "arduino uno" in combined_corpus.lower():
            mcu_name = "Arduino Uno"
            mcu_part = "ATmega328P"
            core = "AVR 8-bit RISC"
            clock = "16 MHz"
            flash = "32 KB"
            sram = "2 KB"
            voltage = "5V"

        board_info = SystemPlatformInfo(
            name=mcu_name,
            mcu_part_number=mcu_part,
            core=core,
            clock=clock,
            flash=flash,
            sram=sram,
            voltage=voltage,
        )

        pins = code_info.get("pins", [])
        if not pins:
            if any(re.search(rf"\b{re.escape(w)}s?\b", abs_lower) for w in ("moisture", "capacitive", "soil", "water", "analog", "sensor")):
                pins.append(PinMapping(pin="PA0", peripheral="ADC1_IN0", connected_to="Analog Sensor Signal", function="Analog Acquisition"))
            if any(re.search(rf"\b{re.escape(w)}s?\b", abs_lower) for w in ("led", "alert", "reminder", "indicator")):
                pins.append(PinMapping(pin="PC13", peripheral="GPIO_Output", connected_to="Status Alert LED", function="Digital Alert Indicator"))

        components = []
        if code_info.get("components"):
            for comp_data in code_info["components"]:
                if isinstance(comp_data, ComponentItem):
                    components.append(comp_data)
                elif isinstance(comp_data, dict):
                    components.append(ComponentItem(**comp_data))
        if not components:
            for mod in modules:
                components.append(
                    ComponentItem(
                        name=mod,
                        role=f"Hardware/firmware functional block for {mod.lower()}",
                        interface="Internal Bus / GPIO / Peripheral",
                        notes=f"Implements {mod} operations on {mcu_name}",
                    )
                )

        impl_info = ImplementationInfo(
            mode="hardware",
            board=board_info,
            framework="arduino_stm32core",
            components=components,
            pins=pins,
            peripherals=["GPIO", "ADC", "Timer", "UART"],
            libraries=["Wire", "LiquidCrystal_I2C", "STM32LowPower"],
            programmer="ST-Link V2",
            serial_baud=code_info.get("serial_baud", 115200),
            is_hardware=True,
            modules=modules,
        )

        state_names = cleaned_cats or ["NORMAL ACQUISITION", "ACTIVE TRANSDUCTION", "STANDBY / POWER SAVE"]
        behaviour_info = BehaviourInfo(
            calibration_constants=code_info.get("constants", {}),
            thresholds={},
            timing_values={},
            state_names=state_names,
            led_active_low=True,
        )

        project_refs = [
            "Joseph Yiu, 'The Definitive Guide to ARM Cortex-M3 and Cortex-M4 Processors', Newnes (Elsevier), 2013.",
            "STMicroelectronics, 'STM32F103x8 STM32F103xB Datasheet: Medium-density performance line ARM-based 32-bit MCU', DocID13587 Rev 17, 2015.",
            "STMicroelectronics, 'RM0008 Reference Manual: STM32F101xx, STM32F102xx, STM32F103xx advanced ARM-based 32-bit MCUs', 2021.",
            "Jack Ganssle, 'The Art of Designing Embedded Systems', Newnes, 2008.",
        ]
        return impl_info, behaviour_info, project_refs

    # Non-hardware domains
    if domain == "MACHINE_LEARNING":
        board_info = SystemPlatformInfo(
            name="Python Machine Learning Workstation",
            mcu_part_number="Python 3.10+ / CUDA Toolkit",
            core="x86_64 / NVIDIA CUDA Tensor Cores",
            clock="Host CPU / GPU Acceleration",
            flash="Storage (NVMe SSD / Local Disk)",
            sram="16 GB Host RAM / 4 GB GDDR6 VRAM",
            voltage="N/A (Host Software / CUDA)",
        )
        components = [
            ComponentItem(
                name=m,
                role=f"Machine learning pipeline module for {m.lower()}",
                interface="Python API / scikit-learn / PyTorch",
                notes=f"Executes {m} within model pipeline",
            )
            for m in modules
        ]
        impl_info = ImplementationInfo(
            mode="simulation",
            board=board_info,
            framework="Python / scikit-learn / PyTorch",
            components=components,
            pins=[],
            peripherals=["Data Ingestion", "CPU / CUDA GPU", "File System", "Visualization Engine"],
            libraries=["scikit-learn", "numpy", "pandas", "matplotlib", "scikit-image"],
            programmer="pip / conda",
            serial_baud=115200,
            is_hardware=False,
            modules=modules,
        )
        state_names = cleaned_cats or ["HIGH CONFIDENCE CLASSIFICATION", "INTERMEDIATE PREDICTION", "UNCERTAIN / REJECT BAND"]
        behaviour_info = BehaviourInfo(
            calibration_constants={},
            thresholds={},
            timing_values={},
            state_names=state_names,
            led_active_low=False,
        )
        project_refs = [
            "T. Cover, P. Hart, 'Nearest Neighbor Pattern Classification', IEEE Transactions on Information Theory, Vol. 13, No. 1, pp. 21-27, 1967.",
            "Robert M. Haralick, K. Shanmugam, Its'hak Dinstein, 'Textural Features for Image Classification', IEEE Transactions on Systems, Man, and Cybernetics, Vol. SMC-3, No. 6, pp. 610-621, 1973.",
            "Fabian Pedregosa et al., 'Scikit-learn: Machine Learning in Python', Journal of Machine Learning Research, Vol. 12, pp. 2825-2830, 2011.",
            "Christopher M. Bishop, 'Pattern Recognition and Machine Learning', Springer, 2006.",
            "Leo Breiman, 'Random Forests', Machine Learning, Vol. 45, No. 1, pp. 5-32, 2001.",
        ]
        return impl_info, behaviour_info, project_refs

    elif domain == "BIOINFORMATICS":
        board_info = SystemPlatformInfo(
            name="Bioinformatics High-Performance Workstation",
            mcu_part_number="Python 3.10+ / Biopython / C++ Extension",
            core="x86_64 Multi-Core / AVX-512 SIMD",
            clock="Host CPU / Multi-Threaded Parallel Execution",
            flash="High-Speed NVMe Storage / Sequence Datasets",
            sram="32 GB High-Bandwidth RAM",
            voltage="N/A (Compute Workstation)",
        )
        components = [
            ComponentItem(
                name=m,
                role=f"Bioinformatics processing subsystem for {m.lower()}",
                interface="Biopython / NumPy / C++ Extensions",
                notes=f"Performs {m} on genomic sequences",
            )
            for m in modules
        ]
        impl_info = ImplementationInfo(
            mode="simulation",
            board=board_info,
            framework="Biopython / NumPy / Parallel C++",
            components=components,
            pins=[],
            peripherals=["FASTA Ingestion", "Parallel CPU Cores", "Matrix Memory Buffer", "Export Engine"],
            libraries=["biopython", "numpy", "scipy", "matplotlib"],
            programmer="pip / conda",
            serial_baud=115200,
            is_hardware=False,
            modules=modules,
        )
        state_names = cleaned_cats or ["OPTIMAL SEQUENCE ALIGNMENT", "HOMOLOGOUS MATCH", "LOW SENSITIVITY / DIVERGENT"]
        behaviour_info = BehaviourInfo(
            calibration_constants={},
            thresholds={},
            timing_values={},
            state_names=state_names,
            led_active_low=False,
        )
        project_refs = [
            "T. F. Smith, M. S. Waterman, 'Identification of Common Molecular Subsequences', Journal of Molecular Biology, Vol. 147, No. 1, pp. 195-197, 1981.",
            "S. F. Altschul, W. Gish, W. Miller, E. W. Myers, D. J. Lipman, 'Basic Local Alignment Search Tool', Journal of Molecular Biology, Vol. 215, No. 3, pp. 403-410, 1990.",
            "P. J. A. Cock et al., 'Biopython: Freely Available Python Tools for Computational Molecular Biology and Bioinformatics', Bioinformatics, Vol. 25, No. 11, pp. 1422-1423, 2009.",
            "R. Durbin, S. R. Eddy, A. Krogh, G. Mitchison, 'Biological Sequence Analysis: Probabilistic Models of Proteins and Nucleic Acids', Cambridge University Press, 1998.",
        ]
        return impl_info, behaviour_info, project_refs

    elif domain == "WEB_CLOUD":
        board_info = SystemPlatformInfo(
            name="Cloud-Native Application Runtime Environment",
            mcu_part_number="Node.js 20+ LTS / V8 JavaScript Engine",
            core="x86_64 / Containerized Execution (Docker/K8s)",
            clock="Multi-Core Event Loop / Asynchronous I/O",
            flash="Cloud Storage / MongoDB / Redis Cache",
            sram="8 GB Dedicated Host RAM",
            voltage="N/A (Cloud Virtual Machine / PaaS)",
        )
        components = [
            ComponentItem(
                name=m,
                role=f"Web application subsystem for {m.lower()}",
                interface="RESTful API / JSON / React Component",
                notes=f"Handles {m} within full-stack architecture",
            )
            for m in modules
        ]
        impl_info = ImplementationInfo(
            mode="simulation",
            board=board_info,
            framework="MERN Stack (MongoDB, Express, React, Node.js)",
            components=components,
            pins=[],
            peripherals=["HTTP/HTTPS Network Interface", "RESTful API Endpoints", "Database Socket", "JWT Authentication Engine"],
            libraries=["express", "react", "mongoose", "jsonwebtoken", "bcryptjs"],
            programmer="npm / yarn / docker",
            serial_baud=115200,
            is_hardware=False,
            modules=modules,
        )
        state_names = cleaned_cats or ["READY / IDLE", "PROCESSING REQUEST", "COMPLETED / PERSISTED"]
        behaviour_info = BehaviourInfo(
            calibration_constants={},
            thresholds={},
            timing_values={},
            state_names=state_names,
            led_active_low=False,
        )
        project_refs = [
            "Roy Thomas Fielding, 'Architectural Styles and the Design of Network-based Software Architectures', Doctoral Dissertation, University of California, Irvine, 2000.",
            "Martin Fowler, 'Patterns of Enterprise Application Architecture', Addison-Wesley Professional, 2002.",
            "Alex Banks, Eve Porcello, 'Learning React: Modern Patterns for Developing React Apps', O'Reilly Media, 2020.",
            "Mario Casciaro, Luciano Mammino, 'Node.js Design Patterns: Design and implement production-grade Node.js applications', Packt Publishing, 2020.",
            "National Institute of Standards and Technology (NIST), 'Secure Hash Standard (SHS)', FIPS PUB 180-4, 2015.",
        ]
        return impl_info, behaviour_info, project_refs

    elif domain == "SOFTWARE_APP":
        board_info = SystemPlatformInfo(
            name="Enterprise Software Runtime Workstation",
            mcu_part_number="Java SE 17+ / OpenJDK HotSpot JVM",
            core="x86_64 Multi-Core Architecture",
            clock="Host CPU (2.8 GHz - 4.5 GHz Boost)",
            flash="Local Storage (NVMe SSD / SQLite Database)",
            sram="16 GB DDR4/DDR5 System RAM",
            voltage="N/A (Host Desktop / Server OS)",
        )
        components = [
            ComponentItem(
                name=m,
                role=f"Software application subsystem for {m.lower()}",
                interface="Java Class / Swing / JDBC",
                notes=f"Executes {m} logic within application structure",
            )
            for m in modules
        ]
        impl_info = ImplementationInfo(
            mode="simulation",
            board=board_info,
            framework="Java / Swing GUI / SQLite Database",
            components=components,
            pins=[],
            peripherals=["GUI Desktop Display", "File System I/O", "SQLite Database Engine", "CSV Export Subsystem"],
            libraries=["java.swing", "sqlite-jdbc", "java.awt", "junit"],
            programmer="Maven / Gradle / javac",
            serial_baud=115200,
            is_hardware=False,
            modules=modules,
        )
        state_names = cleaned_cats or ["READY / IDLE", "PROCESSING REQUEST", "COMPLETED / PERSISTED"]
        behaviour_info = BehaviourInfo(
            calibration_constants={},
            thresholds={},
            timing_values={},
            state_names=state_names,
            led_active_low=False,
        )
        project_refs = [
            "Erich Gamma, Richard Helm, Ralph Johnson, John Vlissides, 'Design Patterns: Elements of Reusable Object-Oriented Software', Addison-Wesley, 1994.",
            "Joshua Bloch, 'Effective Java', 3rd Edition, Addison-Wesley Professional, 2017.",
            "Robert C. Martin, 'Clean Architecture: A Craftsman's Guide to Software Structure and Design', Prentice Hall, 2017.",
            "James Gosling, Bill Joy, Guy Steele, Gilad Bracha, Alex Buckley, 'The Java Language Specification, Java SE 17 Edition', Oracle America, Inc., 2021.",
            "National Institute of Standards and Technology (NIST), 'Secure Hash Standard (SHS)', FIPS PUB 180-4, 2015.",
        ]
        return impl_info, behaviour_info, project_refs

    elif domain == "COA_SYSTEMS":
        board_info = SystemPlatformInfo(
            name="Computer Architecture Simulation Platform",
            mcu_part_number="Ripes RISC-V 5-Stage Pipelined Simulator",
            core="RISC-V (RV32I / RV32IM User & Privilege Architecture)",
            clock="Cycle-Accurate Instruction Execution / 1-10 MHz Virtual Clock",
            flash="Simulation Instruction Memory (ROM / Flash Buffer)",
            sram="Simulation Data Memory (RAM / Register File)",
            voltage="N/A (Simulation Workstation)",
        )
        components = [
            ComponentItem(
                name=m,
                role=f"Computer architecture simulated subsystem for {m.lower()}",
                interface="MMIO / Bus Interconnect / Register Wire",
                notes=f"Simulates {m} in RISC-V environment",
            )
            for m in modules
        ]
        impl_info = ImplementationInfo(
            mode="simulation",
            board=board_info,
            framework="RISC-V Assembly / Ripes Simulator",
            components=components,
            pins=[],
            peripherals=["Memory-Mapped I/O (MMIO)", "Register File", "ALU", "Hazard Detection Unit"],
            libraries=["ripes-sim", "riscv-toolchain", "gcc-riscv32"],
            programmer="Ripes GUI / riscv32-unknown-elf-gcc",
            serial_baud=115200,
            is_hardware=False,
            modules=modules,
        )
        state_names = cleaned_cats or ["INSTRUCTION FETCH & DECODE", "EXECUTE & MEMORY ACCESS", "WRITE-BACK & COMMIT"]
        behaviour_info = BehaviourInfo(
            calibration_constants={},
            thresholds={},
            timing_values={},
            state_names=state_names,
            led_active_low=False,
        )
        project_refs = [
            "David A. Patterson, John L. Hennessy, 'Computer Organization and Design RISC-V Edition: The Hardware Software Interface', Morgan Kaufmann, 2017.",
            "Morten Borup Petersen, 'Ripes: A Visual Computer Architecture Simulator and Assembly Code Editor for the RISC-V Instruction Set Architecture', GitHub Repository, 2021.",
            "Andrew S. Tanenbaum, Todd Austin, 'Structured Computer Organization', 6th Edition, Pearson, 2012.",
        ]
        return impl_info, behaviour_info, project_refs

    else:
        # Default / GENERAL_ACADEMIC / DATA_ANALYTICS
        board_info = SystemPlatformInfo(
            name="Universal Academic Software Workstation",
            mcu_part_number="Standard Execution Environment",
            core="x86_64 Multi-Core Architecture",
            clock="Host CPU",
            flash="Storage (SSD / Local Disk)",
            sram="16 GB Host RAM",
            voltage="N/A (Host Software)",
        )
        components = [
            ComponentItem(
                name=m,
                role=f"Software module responsible for {m.lower()}",
                interface="Modular Software API",
                notes=f"Supports {title} system pipeline",
            )
            for m in modules
        ]
        impl_info = ImplementationInfo(
            mode="simulation",
            board=board_info,
            framework="Universal Application Framework",
            components=components,
            pins=[],
            peripherals=["File I/O", "User Interface", "Execution Engine"],
            libraries=["standard-lib", "testing-framework"],
            programmer="System Package Manager",
            serial_baud=115200,
            is_hardware=False,
            modules=modules,
        )
        state_names = cleaned_cats or ["INITIALIZATION", "OPERATION", "DATA PERSISTENCE"]
        behaviour_info = BehaviourInfo(
            calibration_constants={},
            thresholds={},
            timing_values={},
            state_names=state_names,
            led_active_low=False,
        )
        project_refs = [
            "Donald E. Knuth, 'The Art of Computer Programming, Volume 1: Fundamental Algorithms', Addison-Wesley, 1997.",
            "Thomas H. Cormen, Charles E. Leiserson, Ronald L. Rivest, Clifford Stein, 'Introduction to Algorithms', 3rd Edition, MIT Press, 2009.",
            "Ian Sommerville, 'Software Engineering', Pearson, 10th Edition, 2015.",
        ]
        return impl_info, behaviour_info, project_refs


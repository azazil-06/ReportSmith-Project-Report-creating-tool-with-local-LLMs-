"""Shared fixtures, test markers, and helpers for ReportSmith E2E test suite."""

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional
import docx
import pytest

from reportsmith.brief import ProjectBrief


# Pytest custom markers configuration
def pytest_configure(config):
    config.addinivalue_line("markers", "tier1: Tier 1 Feature Coverage test")
    config.addinivalue_line("markers", "tier2: Tier 2 Boundary & Corner Case test")
    config.addinivalue_line("markers", "tier3: Tier 3 Cross-Feature Combination test")
    config.addinivalue_line("markers", "tier4: Tier 4 Real-World Application Scenario test")


# Standard benchmark abstracts across diverse domains
SAMPLE_ABSTRACTS = {
    "embedded_stm32": {
        "title": "Automated Soil Moisture Monitoring and Plant Watering Reminder System",
        "domain": "EMBEDDED_HARDWARE",
        "is_hardware": True,
        "abstract": (
            "This project presents the design and realization of an automated soil moisture "
            "monitoring and plant watering alert system based on the STM32F103C8T6 ARM Cortex-M3 "
            "microcontroller operating at 72 MHz. A capacitive soil moisture sensor is interfaced "
            "via analog-to-digital converter ADC pin PA0, and an onboard alert LED is driven via "
            "GPIO pin PC13. The firmware executes an acquisition loop every 1000 ms, calibrating raw "
            "ADC counts from 1300 (full water saturation) to 3700 (dry air). When measured soil "
            "moisture drops below the 35% threshold, an automated reminder alert is asserted."
        ),
        "banned_for_other_topics": ["stm32", "pa0", "pc13", "cortex", "moisture", "watering", "soil"],
    },
    "web_campusconnect": {
        "title": "CampusConnect: A MERN-Based Campus Event Management and Registration Platform",
        "domain": "WEB_CLOUD",
        "is_hardware": False,
        "abstract": (
            "CampusConnect is a full-stack web application designed to streamline collegiate event "
            "registration, scheduling, and ticketing. Developed using MongoDB, Express.js, React, "
            "and Node.js (MERN architecture), the platform implements RESTful API endpoints secured "
            "by JSON Web Tokens (JWT) and bcrypt hashing. Client traffic communicates over port 3000 "
            "with backend services on port 5000. In load testing, the platform handled 450 concurrent "
            "registrations with average API latency below 140 ms, returning standard HTTP 200 and 201 "
            "status codes across all transactions."
        ),
        "banned_for_other_topics": ["campusconnect", "mern", "mongodb", "react", "express", "jwt"],
    },
    "ml_freshknn": {
        "title": "FreshKNN: Automated Agricultural Produce Freshness Classification Using Computer Vision",
        "domain": "MACHINE_LEARNING",
        "is_hardware": False,
        "abstract": (
            "FreshKNN presents an automated computer vision pipeline for classifying agricultural "
            "produce freshness based on RGB color distributions and textural features. Images "
            "captured under controlled illumination are resized to 128x128 pixels, and 24-dimensional "
            "color histogram descriptors are extracted. A K-Nearest Neighbors (KNN) classifier with "
            "Euclidean distance metric achieves 94.5% classification accuracy across 1200 test samples. "
            "The inferencing pipeline processes each frame in 18 ms on commodity CPU hardware."
        ),
        "banned_for_other_topics": ["freshknn", "knn", "freshness", "color histogram", "euclidean distance"],
    },
    "bio_genealign": {
        "title": "GeneAlign: Scalable DNA Sequence Alignment Using Parallel Dynamic Programming",
        "domain": "BIOINFORMATICS",
        "is_hardware": False,
        "abstract": (
            "GeneAlign is a bioinformatics tool implementing an optimized Smith-Waterman local "
            "sequence alignment algorithm for genomic nucleotide datasets. The system accelerates "
            "pairwise sequence alignment using dynamic programming matrices with affine gap penalties. "
            "Benchmarked against NCBI BLAST on a dataset of 50000 base pairs, GeneAlign achieved a 3.8x "
            "speedup while maintaining 99.2% alignment sensitivity across synthetic FASTA sequences."
        ),
        "banned_for_other_topics": ["genealign", "smith-waterman", "bioinformatics", "nucleotide", "fasta"],
    },
    "software_moneygo": {
        "title": "MoneyGo: A Personal Finance Management and Budget Tracking Application",
        "domain": "SOFTWARE_APP",
        "is_hardware": False,
        "abstract": (
            "MoneyGo is an object-oriented personal finance management application developed in Java "
            "using the Swing GUI framework and SQLite database. The application employs the "
            "Model-View-Controller (MVC) architectural pattern to decouple user interface interactions "
            "from financial transaction logic. Features include multi-account balance reconciliation, "
            "monthly expenditure forecasting, and automated CSV export for tax auditing."
        ),
        "banned_for_other_topics": ["moneygo", "budget tracking", "sqlite", "personal finance", "reconciliation"],
    },
}


def make_project_docx(
    target_dir: Path,
    title: str,
    abstract: str,
    filename: str = "Project.docx",
    team_members: Optional[List[Dict[str, str]]] = None,
) -> Path:
    """Helper to synthesize a realistic student Project.docx file in target_dir."""
    target_dir.mkdir(parents=True, exist_ok=True)
    doc_path = target_dir / filename
    doc = docx.Document()

    # Title paragraph
    p_title = doc.add_paragraph()
    run_title = p_title.add_run(title.upper())
    run_title.bold = True

    # Team members
    members = team_members or [
        {"name": "Arjun Sharma", "roll": "SBC22CS010"},
        {"name": "Devananda R", "roll": "SBC22CS025"},
    ]
    p_team = doc.add_paragraph()
    for m in members:
        p_team.add_run(f"{m['name']} ({m['roll']})\n")

    # Abstract header & body
    p_head = doc.add_paragraph()
    p_head.add_run("ABSTRACT").bold = True

    doc.add_paragraph(abstract)

    doc.save(str(doc_path))
    return doc_path


def extract_docx_all_text(docx_file: Path) -> str:
    """Extract all text content from paragraphs, tables, headers, and footers of a Word docx."""
    if not Path(docx_file).is_file():
        return ""
    doc = docx.Document(str(docx_file))
    lines: List[str] = []

    # Paragraphs
    for p in doc.paragraphs:
        t = p.text.strip()
        if t:
            lines.append(t)

    # Tables
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                t = cell.text.strip()
                if t:
                    lines.append(t)

    # Headers & Footers
    for sec in doc.sections:
        for p in sec.header.paragraphs:
            t = p.text.strip()
            if t:
                lines.append(t)
        for p in sec.footer.paragraphs:
            t = p.text.strip()
            if t:
                lines.append(t)

    return "\n".join(lines)


@contextmanager
def simulate_file_lock(file_path: Path):
    """Context manager that simulates an external process (like Microsoft Word) holding an exclusive file lock."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(b"PK\x03\x04Initial locked file placeholder content")

    handle = None
    file_obj = None

    if sys.platform == "win32":
        try:
            import win32file
            # Open with FILE_SHARE_READ only (no write sharing), preventing external modifications
            handle = win32file.CreateFile(
                str(path.resolve()),
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                win32file.FILE_SHARE_READ,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
        except Exception:
            # Fallback if pywin32 has issue
            file_obj = open(path, "r+b")
            try:
                import msvcrt
                msvcrt.locking(file_obj.fileno(), msvcrt.LK_NBLCK, 1)
            except Exception:
                pass
    else:
        file_obj = open(path, "r+b")
        try:
            import fcntl
            fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            pass

    try:
        yield path
    finally:
        if handle is not None:
            try:
                import win32file
                win32file.CloseHandle(handle)
            except Exception:
                pass
        if file_obj is not None:
            try:
                file_obj.close()
            except Exception:
                pass

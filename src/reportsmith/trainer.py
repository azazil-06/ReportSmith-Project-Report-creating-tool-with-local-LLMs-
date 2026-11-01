"""Trainer module: ingests training documents (PDF/DOCX) in training/ folder,

extracts outlines, metadata, tables, and academic prose, and compiles a trained
knowledge database, fine-tuning dataset, and Ollama Modelfile for model retraining.
"""

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import docx
import pdfplumber
import pypdf

logger = logging.getLogger(__name__)

# Standard domain categories
DOMAIN_SOFTWARE = "SOFTWARE_APP"
DOMAIN_COA = "COA_SYSTEMS"
DOMAIN_EMBEDDED = "EMBEDDED_HARDWARE"
DOMAIN_ML = "MACHINE_LEARNING"
DOMAIN_WEB = "WEB_CLOUD"
DOMAIN_DATA = "DATA_ANALYTICS"
DOMAIN_GENERAL = "GENERAL_ACADEMIC"

DOMAIN_KEYWORDS = {
    DOMAIN_ML: [
        "machine learning", "deep learning", "neural network", "knn",
        "k-nearest", "classification", "regression", "clustering",
        "training set", "testing set", "feature extraction", "feature vector",
        "supervised", "unsupervised", "accuracy", "precision", "recall",
        "f1-score", "confusion matrix", "overfitting", "underfitting",
        "epoch", "gradient descent", "backpropagation", "cnn",
        "convolutional", "recurrent", "rnn", "lstm", "transformer",
        "attention mechanism", "image classification", "object detection",
        "natural language processing", "nlp", "tokenization",
        "random forest", "decision tree", "svm", "support vector",
        "logistic regression", "naive bayes", "ensemble", "cross-validation",
        "hyperparameter", "model training", "inference", "prediction",
        "tensorflow", "pytorch", "keras", "scikit-learn", "sklearn",
        "opencv", "computer vision", "image processing", "dataset",
        "euclidean distance", "majority voting", "freshness",
        "histogram", "glcm", "texture features", "color histogram",
        "feature engineering", "preprocessing", "normalization",
    ],
    DOMAIN_COA: [
        "ripes", "risc-v", "assembly", "datapath", "pipeline", "mmio",
        "instruction", "register offset", "traffic signal controller", "alu",
        "memory mapped", "five-stage", "hazard unit", "program counter",
    ],
    DOMAIN_WEB: [
        "react", "angular", "vue", "html", "css", "javascript", "typescript",
        "node.js", "express", "django", "flask", "rest api", "graphql",
        "microservices", "docker", "kubernetes", "cloud", "aws", "azure",
        "gcp", "deployment", "ci/cd", "devops", "serverless",
        "responsive design", "single page application", "spa",
        "full stack", "frontend framework", "backend framework",
    ],
    DOMAIN_DATA: [
        "big data", "data analytics", "data mining", "data warehouse",
        "hadoop", "spark", "mapreduce", "etl", "data pipeline",
        "business intelligence", "dashboard", "visualization",
        "pandas", "numpy", "matplotlib", "seaborn", "tableau",
        "power bi", "sql", "nosql", "mongodb", "data lake",
        "statistical analysis", "time series", "predictive analytics",
    ],
    DOMAIN_SOFTWARE: [
        "java", "resume", "banking", "customer terminal", "gui",
        "database", "login", "portal", "oop",
        "employer", "template", "software", "api", "backend",
        "frontend", "account", "transaction", "hashing", "validation",
        "file i/o", "console", "crud", "inventory", "management system",
    ],
    DOMAIN_EMBEDDED: [
        "stm32", "microcontroller", "sensor", "gpio", "adc", "ultrasonic",
        "circuit", "blue pill", "led", "buzzer", "i2c", "baud", "pinout",
        "firmware", "st-link", "cortex-m", "hal", "threshold", "calibration",
    ],
}


def classify_text_domain(text: str) -> str:
    """Classify text into a domain based on keyword frequency analysis.

    Supported domains: MACHINE_LEARNING, COA_SYSTEMS, WEB_CLOUD, DATA_ANALYTICS,
    SOFTWARE_APP, EMBEDDED_HARDWARE, GENERAL_ACADEMIC.
    """
    t_lower = text.lower()
    scores = {}
    for domain, kws in DOMAIN_KEYWORDS.items():
        score = sum(t_lower.count(kw) for kw in kws)
        scores[domain] = score

    # Pick the domain with the highest score; break ties by specificity order
    priority_order = [
        DOMAIN_ML, DOMAIN_COA, DOMAIN_WEB, DOMAIN_DATA,
        DOMAIN_EMBEDDED, DOMAIN_SOFTWARE,
    ]
    best_domain = DOMAIN_GENERAL
    best_score = 0
    for d in priority_order:
        s = scores.get(d, 0)
        if s > best_score:
            best_score = s
            best_domain = d

    if best_score == 0:
        return DOMAIN_GENERAL
    return best_domain


def map_section_role(title: str, section_id: str = "") -> str:
    """Map raw section title/id to semantic role."""
    t_low = title.lower()
    s_low = section_id.lower()

    if "conclusion" in t_low or s_low == "6.1":
        return "conclusion"
    if "future" in t_low or s_low == "6.4":
        return "future_scope"
    if "abstract" in t_low or s_low == "abstract":
        return "abstract"
    if "acknowledgement" in t_low or s_low == "acknowledgement":
        return "acknowledgement"
    if "declaration" in t_low or s_low == "declaration":
        return "declaration"
    if "objective" in t_low or s_low == "1.2":
        return "objectives"
    if "problem" in t_low or "motivation" in t_low:
        return "problem_statement"
    if "scope" in t_low or s_low == "1.3":
        return "scope"
    if "application" in t_low or s_low == "1.4":
        return "applications"
    if "overview" in t_low or s_low == "1.1":
        return "introduction"
    if "introduction" in t_low:
        return "introduction"
    if "block diagram" in t_low:
        return "block_diagram"
    if "circuit" in t_low or "wiring" in t_low or "connection" in t_low:
        return "circuit_diagram"
    if "flowchart" in t_low or "algorithm" in t_low:
        return "flowchart"
    if "architecture" in t_low:
        return "architecture"
    if "hardware" in t_low and "requirement" in t_low:
        return "hardware_requirements"
    if "software" in t_low and "requirement" in t_low:
        return "software_requirements"
    if "specification" in t_low:
        return "specifications"
    if "pin" in t_low or "peripheral" in t_low:
        return "pin_mapping"
    if "module" in t_low:
        return "modules"
    if "implementation" in t_low:
        return "implementation"
    if "test" in t_low or "observation" in t_low:
        return "testing_results"
    if "simulation" in t_low or "result" in t_low:
        return "testing_results"
    if "limitation" in t_low or s_low == "6.3":
        return "limitations"
    if "future" in t_low or s_low == "6.4":
        return "future_scope"
    if "learning" in t_low or s_low == "6.2":
        return "learning_outcomes"
    if "conclusion" in t_low or s_low == "6.1":
        return "conclusion"
    if "reference" in t_low:
        return "references"
    return "general_content"


class ReportTrainer:
    """Trains and compiles knowledge base, fine-tuning dataset, and model artifacts

    from documents stored in the training folder.
    """

    def __init__(
        self,
        training_dir: Union[str, Path] = "training",
        out_dir: Union[str, Path] = "out",
    ):
        self.training_dir = Path(training_dir)
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def parse_docx_document(self, fpath: Path) -> Dict[str, Any]:
        """Extract structured sections, metadata, and tables from a DOCX file."""
        doc = docx.Document(str(fpath))
        raw_paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        full_text = " ".join(raw_paras)
        domain = classify_text_domain(full_text)

        # 1. Metadata from cover page
        title = None
        for p in raw_paras[:20]:
            m_cert = re.search(r'(?:entitled|titled)\s*[\'\"“\u201c\u2018]([^\'\"”\u201d\u2019]+)[\'\"”\u201d\u2019]', p, re.IGNORECASE)
            if m_cert:
                cand = m_cert.group(1).strip().replace("\n", " ")
                if len(cand) > 3 and not cand.lower().startswith("section"):
                    title = cand
                    break
        if not title:
            for p in raw_paras[:10]:
                if p.upper() not in ('PROJECT REPORT', 'A MICRO PROJECT REPORT', 'SUBMITTED BY', 'SUBMITTED TO') and len(p) > 3:
                    title = p
                    break
        if not title:
            title = fpath.stem
        course_code = None
        semester = None
        college = "Sree Buddha College of Engineering (Autonomous)"
        department = "Department of Computer Science & Engineering"

        for p in raw_paras[:25]:
            p_clean = p.replace("  ", " ").strip()
            if "course" in p_clean.lower() or re.search(r"\b(P[A-Z0-9]{6,8})\b", p_clean):
                m_c = re.search(r"\b(P[A-Z0-9]{6,8})\b", p_clean)
                if m_c:
                    course_code = m_c.group(1)
            if "semester" in p_clean.lower():
                semester = p_clean
            if "COLLEGE OF ENGINEERING" in p.upper():
                college = p.title()
            if "DEPARTMENT OF" in p.upper():
                department = p.title().rstrip(",")

        # 2. Extract sections
        sections: Dict[str, Dict[str, Any]] = {}
        current_id = "prelim"
        current_title = "Title & Preliminaries"
        current_role = "prelim"
        sections[current_id] = {
            "title": current_title,
            "role": current_role,
            "paragraphs": [],
            "bullets": [],
        }

        for p in doc.paragraphs:
            txt = p.text.strip()
            if not txt:
                continue

            # Check heading markers
            m_ch = re.match(r"^(?:CHAPTER\s+(\d+)[:\s]*(.*))", txt, re.IGNORECASE)
            m_sec = re.match(r"^(\d+\.\d+)\s*(.*)", txt)
            is_standalone = txt.upper() in (
                "ABSTRACT", "ACKNOWLEDGEMENT", "DECLARATION", "REFERENCES", "CONCLUSION",
            )

            if is_standalone and len(txt) < 30:
                current_id = txt.lower()
                current_title = txt.title()
                current_role = map_section_role(current_title, current_id)
                if current_id not in sections:
                    sections[current_id] = {
                        "title": current_title,
                        "role": current_role,
                        "paragraphs": [],
                        "bullets": [],
                    }
                continue

            if m_ch:
                ch_num = m_ch.group(1)
                ch_title = m_ch.group(2).split(".")[0].strip() or f"Chapter {ch_num}"
                current_id = f"Chapter_{ch_num}"
                current_title = ch_title
                current_role = map_section_role(ch_title, current_id)
                if current_id not in sections:
                    sections[current_id] = {
                        "title": current_title,
                        "role": current_role,
                        "paragraphs": [],
                        "bullets": [],
                    }
                continue

            if m_sec:
                sec_num = m_sec.group(1)
                sec_title = m_sec.group(2).split(".")[0].strip()
                current_id = sec_num
                current_title = sec_title
                current_role = map_section_role(sec_title, current_id)
                if current_id not in sections:
                    sections[current_id] = {
                        "title": current_title,
                        "role": current_role,
                        "paragraphs": [],
                        "bullets": [],
                    }
                continue

            # Content paragraphs
            if txt.startswith("•") or txt.startswith("-") or txt.startswith("*"):
                clean_bullet = re.sub(r"^[•\-\*]\s*", "", txt).strip()
                if clean_bullet:
                    sections[current_id]["bullets"].append(clean_bullet)
            else:
                # Discard TOC dot leaders (e.g. '..........12' or 'ABSTRACT........ iii')
                if not re.search(r"\.{4,}", txt):
                    sections[current_id]["paragraphs"].append(txt)

        # 3. Extract tables
        tables_data: List[Dict[str, Any]] = []
        for tbl in doc.tables:
            tbl_rows: List[List[str]] = []
            for r in tbl.rows:
                cells = [c.text.strip().replace("\n", " ") for c in r.cells]
                tbl_rows.append(cells)
            if tbl_rows and len(tbl_rows) > 1:
                tables_data.append({
                    "headers": tbl_rows[0],
                    "rows": tbl_rows[1:],
                    "num_rows": len(tbl_rows),
                })

        return {
            "filename": fpath.name,
            "type": "docx",
            "title": title,
            "domain": domain,
            "course_code": course_code or "PBCST304",
            "semester": semester or "Semester 3",
            "college": college,
            "department": department,
            "sections": sections,
            "tables": tables_data,
        }

    def parse_pdf_document(self, fpath: Path) -> Dict[str, Any]:
        """Extract structured sections, metadata, and tables from a PDF file."""
        reader = pypdf.PdfReader(str(fpath))
        num_pages = len(reader.pages)
        page_texts = [p.extract_text() or "" for p in reader.pages]
        full_text = " ".join(page_texts)
        domain = classify_text_domain(full_text)

        # 1. Metadata
        p0 = page_texts[0] if num_pages > 0 else ""
        lines_p0 = [l.strip() for l in p0.splitlines() if l.strip()]
        title = None
        for page in reader.pages[:3]:
            txt = page.extract_text() or ""
            m_cert = re.search(r'(?:entitled|titled)\s*[\'\"“\u201c\u2018\ufffd]([^\'\"”\u201d\u2019\ufffd]+)[\'\"”\u201d\u2019\ufffd]', txt, re.IGNORECASE)
            if m_cert:
                cand = m_cert.group(1).strip().replace("\n", " ")
                if len(cand) > 4 and not cand.startswith("(") and not cand.lower().startswith("section"):
                    title = cand
                    break
        if not title:
            for l in lines_p0:
                if l in ('1', 'i', 'ii', 'iii') or len(l) <= 2:
                    continue
                if any(b in l.upper() for b in ['PROJECT REPORT', 'A MICRO PROJECT REPORT', 'SUBMITTED BY', 'SUBMITTED TO', 'DEPARTMENT OF', 'COLLEGE OF']):
                    continue
                if re.search(r'\(SBC\d{2}[A-Z]{2}\d{3}\)', l) or re.search(r'SBC\d{2}[A-Z]{2}\d{3}', l):
                    continue
                if len(l) > 3:
                    title = l
                    break
        if not title:
            title = fpath.stem
        course_code = None
        semester = None
        college = "Sree Buddha College of Engineering (Autonomous)"
        department = "Department of Computer Science & Engineering"

        for l in lines_p0[:20]:
            if re.search(r"\b(P[A-Z0-9]{6,8})\b", l):
                course_code = re.search(r"\b(P[A-Z0-9]{6,8})\b", l).group(1)
            if "semester" in l.lower():
                semester = l
            if "COLLEGE OF ENGINEERING" in l.upper():
                college = l.title()
            if "DEPARTMENT OF" in l.upper():
                department = l.title().rstrip(",")

        # 2. Extract sections by analyzing line headers
        sections: Dict[str, Dict[str, Any]] = {}
        current_id = "prelim"
        current_title = "Title & Preliminaries"
        current_role = "prelim"
        sections[current_id] = {
            "title": current_title,
            "role": current_role,
            "paragraphs": [],
            "bullets": [],
        }

        for text in page_texts:
            for line in text.splitlines():
                txt = line.strip()
                if not txt:
                    continue

                # Section & Chapter headers
                m_ch = re.match(r"^(?:CHAPTER\s+(\d+)[:\s]*(.*))", txt, re.IGNORECASE)
                m_sec = re.match(r"^(\d+\.\d+)\s+([A-Za-z].*)", txt)
                is_standalone = txt.upper() in (
                    "ABSTRACT", "ACKNOWLEDGEMENT", "DECLARATION", "REFERENCES", "CONCLUSION",
                )

                if m_ch and len(txt) < 80:
                    ch_num = m_ch.group(1)
                    ch_title = m_ch.group(2).strip() or f"Chapter {ch_num}"
                    current_id = f"Chapter_{ch_num}"
                    current_title = ch_title
                    current_role = map_section_role(ch_title, current_id)
                    if current_id not in sections:
                        sections[current_id] = {
                            "title": current_title,
                            "role": current_role,
                            "paragraphs": [],
                            "bullets": [],
                        }
                    continue

                if m_sec and len(txt) < 80:
                    sec_num = m_sec.group(1)
                    sec_title = m_sec.group(2).strip()
                    current_id = sec_num
                    current_title = sec_title
                    current_role = map_section_role(sec_title, current_id)
                    if current_id not in sections:
                        sections[current_id] = {
                            "title": current_title,
                            "role": current_role,
                            "paragraphs": [],
                            "bullets": [],
                        }
                    continue

                if is_standalone and len(txt) < 30:
                    current_id = txt.lower()
                    current_title = txt.title()
                    current_role = map_section_role(current_title, current_id)
                    if current_id not in sections:
                        sections[current_id] = {
                            "title": current_title,
                            "role": current_role,
                            "paragraphs": [],
                            "bullets": [],
                        }
                    continue

                # Collect content lines
                if txt.startswith("•") or txt.startswith("-"):
                    clean_b = re.sub(r"^[•\-]\s*", "", txt).strip()
                    if clean_b:
                        sections[current_id]["bullets"].append(clean_b)
                elif not re.search(r"\.{4,}", txt) and len(txt) > 20:
                    sections[current_id]["paragraphs"].append(txt)

        # 3. Extract tables using pdfplumber
        tables_data: List[Dict[str, Any]] = []
        try:
            with pdfplumber.open(str(fpath)) as pdf:
                for p_idx, page in enumerate(pdf.pages):
                    page_tbls = page.extract_tables() or []
                    for t in page_tbls:
                        if t and len(t) > 1:
                            clean_t = [[str(cell or "").strip().replace("\n", " ") for cell in row] for row in t]
                            tables_data.append({
                                "page": p_idx + 1,
                                "headers": clean_t[0],
                                "rows": clean_t[1:],
                                "num_rows": len(clean_t),
                            })
        except Exception as e:
            logger.warning(f"pdfplumber table extraction skipped for {fpath.name}: {e}")

        return {
            "filename": fpath.name,
            "type": "pdf",
            "title": title,
            "domain": domain,
            "course_code": course_code or "PBCST504",
            "semester": semester or "Semester 5",
            "college": college,
            "department": department,
            "sections": sections,
            "tables": tables_data,
        }

    def train(self) -> Dict[str, Any]:
        """Execute full training pipeline:

        Ingests files in training_dir, indexes corpus, generates training_database.json,
        fine-tuning train_dataset.jsonl, and Ollama Modelfile.
        """
        if not self.training_dir.is_dir():
            raise FileNotFoundError(f"Training directory not found: {self.training_dir}")

        files = sorted(self.training_dir.iterdir())
        documents: List[Dict[str, Any]] = []
        domain_counts: Dict[str, int] = {}
        total_sections = 0
        total_paras = 0

        # Curated exemplars store: domain -> role -> list of {paragraphs, bullets, title}
        exemplars: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
            DOMAIN_SOFTWARE: {},
            DOMAIN_COA: {},
            DOMAIN_EMBEDDED: {},
            DOMAIN_GENERAL: {},
        }

        print(f"Scanning '{self.training_dir}' ({len(files)} files discovered)...")

        for f in files:
            suffix = f.suffix.lower()
            if suffix not in (".docx", ".pdf", ".txt", ".md"):
                continue

            print(f"  Ingesting [{suffix.upper().lstrip('.')}] {f.name}...")
            doc_data = None
            try:
                if suffix == ".docx":
                    doc_data = self.parse_docx_document(f)
                elif suffix == ".pdf":
                    doc_data = self.parse_pdf_document(f)
            except Exception as e:
                logger.error(f"Failed parsing {f.name}: {e}")
                continue

            if not doc_data:
                continue

            documents.append(doc_data)
            domain = doc_data["domain"]
            domain_counts[domain] = domain_counts.get(domain, 0) + 1

            # Index sections into exemplars
            for sec_id, sec_data in doc_data["sections"].items():
                role = sec_data["role"]
                paras = sec_data["paragraphs"]
                bullets = sec_data["bullets"]

                if not paras and not bullets:
                    continue

                total_sections += 1
                total_paras += len(paras)

                if domain not in exemplars:
                    exemplars[domain] = {}
                if role not in exemplars[domain]:
                    exemplars[domain][role] = []

                exemplars[domain][role].append({
                    "doc_title": doc_data["title"],
                    "section_id": sec_id,
                    "title": sec_data["title"],
                    "paragraphs": paras[:5],  # store primary paragraphs
                    "bullets": bullets[:8],
                })

        # 1. Compile Unified Training Database
        db_payload = {
            "created_at": datetime.now().isoformat(),
            "version": "2.0.0",
            "training_dir": str(self.training_dir),
            "documents_count": len(documents),
            "total_sections": total_sections,
            "total_paragraphs": total_paras,
            "domains": sorted(list(domain_counts.keys())),
            "domain_distribution": domain_counts,
            "documents": documents,
            "exemplars": exemplars,
        }

        db_path = self.out_dir / "training_database.json"
        with open(db_path, "w", encoding="utf-8") as f_db:
            json.dump(db_payload, f_db, indent=2, ensure_ascii=False)

        # 2. Compile Fine-Tuning Dataset (JSONL)
        jsonl_path = self.out_dir / "train_dataset.jsonl"
        with open(jsonl_path, "w", encoding="utf-8") as f_jl:
            for doc in documents:
                for sec_id, sec in doc["sections"].items():
                    if not sec["paragraphs"] and not sec["bullets"]:
                        continue
                    system_msg = (
                        f"You are ReportSmith, an expert academic report generator for {doc['college']}. "
                        f"Write formal, rigorous technical prose in Indian/UK English for the domain '{doc['domain']}'."
                    )
                    user_msg = (
                        f"Generate section '{sec['title']}' (Section ID: {sec_id}) for the project entitled "
                        f"'{doc['title']}' under course {doc['course_code']} ({doc['semester']})."
                    )
                    assistant_msg = json.dumps({
                        "paragraphs": sec["paragraphs"][:4],
                        "bullets": sec["bullets"][:6],
                    }, ensure_ascii=False)

                    line = json.dumps({
                        "messages": [
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": user_msg},
                            {"role": "assistant", "content": assistant_msg},
                        ]
                    }, ensure_ascii=False)
                    f_jl.write(line + "\n")

        # 3. Generate Ollama Modelfile
        modelfile_path = self.out_dir / "Modelfile"
        modelfile_content = f"""# ReportSmith Trained Local Modelfile
# Compiled automatically from training database on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

FROM qwen2.5-coder:1.5b

PARAMETER temperature 0.25
PARAMETER top_p 0.90
PARAMETER repeat_penalty 1.15
PARAMETER stop "<|im_end|>"
PARAMETER stop "<|endoftext|>"

SYSTEM \"\"\"
You are ReportSmith, an offline deterministic technical report generator trained on official college project reports.
You format prose exclusively into strict JSON: {{"paragraphs": ["..."], "bullets": ["..."]}}
Domains learned: {', '.join(sorted(list(domain_counts.keys())))}.
Tone: Academic, formal Indian/UK English, active/passive third-person, zero AI conversational filler.
Strict factual grounding: NEVER hallucinate unprovided components, pin assignments, or metrics.
\"\"\"
"""
        modelfile_path.write_text(modelfile_content, encoding="utf-8")

        # 4. Generate Training Summary
        summary_path = self.out_dir / "training_summary.txt"
        summary_lines = [
            "=" * 70,
            "REPORTSMITH MODEL RETRAINING SUMMARY",
            "=" * 70,
            f"Timestamp: {datetime.now().isoformat()}",
            f"Training Directory: {self.training_dir.resolve()}",
            f"Documents Processed: {len(documents)}",
            f"Total Sections Indexed: {total_sections}",
            f"Total Paragraphs Cataloged: {total_paras}",
            "",
            "Domain Distribution:",
        ]
        for dom, cnt in sorted(domain_counts.items()):
            summary_lines.append(f"  - {dom}: {cnt} documents")
        summary_lines.extend([
            "",
            "Artifacts Generated:",
            f"  1. Database: {db_path.resolve()}",
            f"  2. Fine-Tuning JSONL: {jsonl_path.resolve()}",
            f"  3. Ollama Modelfile: {modelfile_path.resolve()}",
            "=" * 70,
        ])
        summary_path.write_text("\n".join(summary_lines), encoding="utf-8")

        return {
            "status": "success",
            "documents_count": len(documents),
            "domains": sorted(list(domain_counts.keys())),
            "sections_count": total_sections,
            "paragraphs_count": total_paras,
            "database_path": str(db_path),
            "dataset_path": str(jsonl_path),
            "modelfile_path": str(modelfile_path),
            "summary_path": str(summary_path),
        }

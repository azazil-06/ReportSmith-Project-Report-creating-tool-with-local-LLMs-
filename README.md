# ReportSmith 📄⚡

> **Autonomous, publication-grade academic report builder powered by local LLMs (LM Studio / Ollama) and a deterministic, anti-hallucination factual synthesis engine.**

---

## ⚡ Quick Overview & Simple Setup Guide

ReportSmith transforms raw project materials, codebases, abstracts, and institutional syllabi into fully formatted, 6-chapter Microsoft Word (`.docx`) and PDF (`.pdf`) reports following strict university formatting standards—complete with Roman-numeral preliminaries, dynamic system architecture diagrams, styled specification tables, and synchronized page-numbered tables of contents.

### 1. Installation

```bash
# Clone or open repository root
cd "Report Builder"

# Install dependencies and package in editable development mode
pip install -r requirements.txt
pip install -e .
```
*(Prerequisites: Python 3.10+, local LLM server such as LM Studio or Ollama, and Microsoft Word or LibreOffice for native PDF export & TOC pagination).*

### 2. Connect Your Local Model

Start LM Studio or Ollama with an instruction-tuned model (e.g., `qwen2.5-coder-3b`, `qwen2.5-7b`, `llama-3.2-3b`):
- **LM Studio**: Start local server on port `1234` (`http://localhost:1234/v1`)
- **Ollama**: Run `ollama run qwen2.5-coder:3b` on port `11434` (`http://localhost:11434/v1`)

### 3. Core Daily Commands

Drop your project files (`Project.docx`, `abstract.pdf`, or source code) into the `Content/` folder, then run:

```bash
# Check GPU acceleration status & optimal worker recommendations
python -m reportsmith.cli --cuda

# Run with custom parallel concurrency (e.g. 4 concurrent worker threads)
python -m reportsmith.cli build --content Content --concurrency 4

# Validate generated report against facts and specifications
python -m reportsmith.cli check out/report.docx

# Retrain model knowledge base whenever new reference docs are added to training/
python -m reportsmith.cli --train

# Or retrain with custom source and output directories
python -m reportsmith.cli train --dir training --out-dir out
```

---

## 🧠 System Architecture: The Hybrid Bifurcated Engine

ReportSmith solves the fundamental problem of LLM document generation: **large language models write eloquent prose but hallucinate numerical facts, mangle document formatting, and fail on strict institutional structural invariants.**

ReportSmith enforces a **strict architectural bifurcation**:

```
                                  [Content/ Folder]
                       (Abstract, Source Code, Syllabus PDF)
                                         │
                                         ▼
                        ┌─────────────────────────────────┐
                        │   Content & Abstract Analyzer   │
                        │   • Domain & Title Classification│
                        │   • Subsystem Module Extraction │
                        │   • Factual Number Harvesting   │
                        └────────────────┬────────────────┘
                                         │
                    ┌────────────────────┴────────────────────┐
                    ▼                                         ▼
  ┌──────────────────────────────────┐      ┌──────────────────────────────────┐
  │      DETERMINISTIC ENGINE        │      │       LOCAL LLM PROSE ENGINE     │
  │ • Vector Matplotlib Diagrams     │      │ • Modular Section Prompting      │
  │ • Specification & State Tables   │      │ • Word-Budgeted Academic Prose   │
  │ • Mathematical Formulations      │      │ • Literature Review & Theory     │
  │ • Verbatim Source Code Appendix  │      │ • Strict Abstract-Bound Grounding│
  │ • Roman/Decimal Section Layout   │      │ • Anti-Leakage Post-Sanitizer    │
  └─────────────────┬────────────────┘      └─────────────────┬────────────────┘
                    │                                         │
                    └────────────────────┬────────────────────┘
                                         ▼
                        ┌─────────────────────────────────┐
                        │      Document Assembly Engine   │
                        │   • python-docx Academic Styles │
                        │   • Page Break & Table cantSplit│
                        │   • Synchronized TOC via COM    │
                        └────────────────┬────────────────┘
                                         ▼
                            out/report.docx & out/report.pdf
```

1. **The Deterministic Synthesis Engine**: Generates system architecture block diagrams, DFDs, logic flowcharts, data tables, component interfaces, mathematical formulas, and verbatim code appendices directly from verified source files. The language model is never permitted to invent specifications, pin mappings, or numbers.
2. **The Local LLM Scholarly Engine**: Generates formal academic prose, theoretical background, domain methodology, and chapter narratives modularly using curated, domain-specific context prompts.
3. **The Assembly & Postprocessing Pipeline**: Compiles assets into a native `.docx` container with institutional headers/footers, Roman numeral preliminary pagination, table-of-contents dot leaders, and Word COM PDF synchronization.

---

## 🛠️ Ingenious Solutions to Complex Engineering Challenges

During the evolution of ReportSmith into a production-grade universal report builder, multiple complex edge-cases and architectural hurdles were solved through purpose-built systems:

### 1. Dynamic Domain Ingestion & Schema Decoupling
* **The Challenge:** Traditional report generators are hardcoded to a single domain (e.g. microcontroller/IoT hardware with microcontrollers and pinouts). When fed software, machine learning, cloud platforms, or bioinformatics abstracts, they either crashed or hallucinated circuit diagrams and GPIO pins.
* **The Solution:** 
  - Created a decoupled `SystemPlatformInfo` schema in `brief.py` where hardware attributes (`mcu`, `sensors`, `pin_config`) are strictly optional and `is_hardware=False` for non-hardware domains.
  - Engineered an autonomous NLP subsystem extractor in `content_analyzer.py` that parses raw abstracts across domains (`WEB_CLOUD`, `MACHINE_LEARNING`, `SOFTWARE_APP`, `BIOINFORMATICS`, `EMBEDDED_IOT`, `COA_SYSTEMS`).
  - Automatically synthesizes 3 to 5 core functional modules (e.g., *Presentation Tier*, *RESTful Controller*, *Data Persistence Tier* for MERN; *Dataset Preprocessing*, *Model Architecture*, *Evaluation* for ML) and structures Chapters 2, 3, 4, and 5 dynamically around them.

### 2. Cryptographic Project Fingerprinting & Cache Isolation
* **The Challenge:** When users swapped abstracts in `Content/`, the builder frequently suffered from "cross-build hallucination"—stale cached paragraphs from previous builds (e.g., an STM32 irrigation project) bled into a new full-stack MERN or ML report.
* **The Solution:** 
  - Implemented deterministic SHA-256 project signature fingerprinting:
    $$\text{Project Signature} = \text{SHA256}(\text{title} + \text{":\!"} + \text{abstract})[:12]$$
  - Partitioned the disk cache into isolated directories (`cache/<project_sig>/`).
  - Added an atomic `active_manifest.json` tracker that detects when the target project changes, instantly isolating previous outputs and purging orphaned cache records so zero cross-contamination can ever occur.

### 3. Elimination of Hardcoded Fallbacks & Regex Word-Boundary Exemplars
* **The Challenge:** Stub fallback engines often contain legacy default texts. In addition, exemplar training databases matched substrings (e.g., the word `"access"` in `"role-based access control"` falsely triggered an STM32 RFID door lock exemplar).
* **The Solution:**
  - Completely purged static hardware fallback templates from `llm.py`; replaced with dynamic brief-driven scholastic synthesis that respects the active project domain.
  - Implemented strict word-boundary regular expression matching (`\bkeyword\b`) across all training classification indexes in `trainer.py`.
  - Added project-signature filtering to exemplar retrieval so only exemplars relevant to the active project domain are cited.

### 4. Resilient Multi-Candidate Windows Word Lock Recovery
* **The Challenge:** On Windows, if a user has `out/report.docx` open in Microsoft Word, any attempt to overwrite the file triggers an unhandled `PermissionError: [Errno 13] Permission denied`, crashing the generation pipeline.
* **The Solution:**
  - Implemented `_attempt_close_word_doc` using Windows COM (`win32com.client.GetActiveObject("Word.Application")`) to gracefully save and release locks.
  - Built an atomic multi-candidate path resolver (`get_safe_output_path`) that smoothly falls back to `out/report_updated.docx`, `out/report_1.docx`..`_99.docx`, or timestamped variants if Word remains locked.
  - Synchronously propagated the resolved `saved_path` to CLI output handlers, verification checkers, and PDF exporters.

### 5. Domain-Adaptive Vector Diagram Engine
* **The Challenge:** Fixed diagram routines output identical hardware pinout block diagrams regardless of topic.
* **The Solution:** Upgraded `diagrams.py` with domain-adaptive rendering logic:
  - **Web & Cloud:** Generates multi-tier full-stack diagrams (Client SPA $\to$ API Gateway / Express $\to$ Services $\to$ Cloud DB/MongoDB).
  - **Machine Learning:** Generates pipeline block diagrams (Data Ingestion $\to$ Feature Extraction $\to$ Model Inference $\to$ Validation).
  - **Embedded / Hardware:** Generates pin-accurate microcontroller schematics and sensor bus interconnects.
  - **Data Flow Diagrams (DFDs) & Flowcharts:** Rendered as clean, high-DPI vector graphics via Matplotlib without external Graphviz dependencies.

### 6. Automated Native Word COM Page Number Synchronization
* **The Challenge:** Word table of contents (`TOC`), list of figures (`LOF`), and list of tables (`LOT`) cannot calculate exact page numbers without Word's layout rendering engine.
* **The Solution:**
  - Created a dual-pass layout synchronizer (`pagenums.py`).
  - Leverages native Word COM automation on Windows (or headless LibreOffice on Linux/macOS) to update all document fields, render the PDF, read physical page breaks, and synchronize TOC dot-leader page numbers with 100% mathematical accuracy.

---

## 🛡️ Anti-Hallucination & Factual Grounding Framework

ReportSmith applies multi-layered defensive verification to guarantee academic integrity:

```
    ┌────────────────────────────────────────────────────────┐
    │           INPUT SOURCE HARVESTING & WHITELIST          │
    │  • Numerical values & units regex-extracted            │
    │  • Software versions & HTTP codes whitelisted          │
    │  • Technical entity cataloging                         │
    └───────────────────────────┬────────────────────────────┘
                                │
                                ▼
    ┌────────────────────────────────────────────────────────┐
    │           LLM OUTPUT SANITIZATION FILTER               │
    │  • Strips ```markdown fences & thinking tokens         │
    │  • Filters raw prompt / abstract echoes                │
    │  • Removes meta-commentary ("Here is the section...")  │
    └───────────────────────────┬────────────────────────────┘
                                │
                                ▼
    ┌────────────────────────────────────────────────────────┐
    │           AUTOMATED COMPLIANCE CHECKER (`check.py`)    │
    │  • Validates facts against brief specifications        │
    │  • Scans for banned generic filler phrases             │
    │  • Audits table rows, citations & figure references    │
    └────────────────────────────────────────────────────────┘
```

1. **Dynamic Numerical Whitelisting (`facts.py`):**
   Extracts all authentic numbers, technical metrics, and data values directly from the input abstract. Dynamically whitelists HTTP status codes (`200`, `404`, `500`), standard software ports (`3000`, `5000`, `8080`, `27017`), and performance benchmarks, eliminating false-positive checker errors while intercepting fabricated numbers.
2. **Output Sanitization & Anti-Leakage:**
   A multi-pass response parser strips triple-backtick markdown blocks, unescaped HTML entities, model CoT reasoning tokens, and introductory chatter.
3. **Automated Document Auditor (`reportsmith check`):**
   Performs AST analysis on `out/report.docx`, inspecting all 200+ paragraphs and tables for:
   - Factual grounding against `out/analyzed_brief.yaml`
   - Zero occurrences of cross-domain leakage
   - Strict adherence to academic tone and word-count targets

---

## 📊 Performance Matrix & Hardware Benchmarks

ReportSmith is optimized for consumer hardware, running completely offline with local quantization.

### Hardware Performance Matrix

| Hardware Platform | GPU / VRAM | Quantization | Parallel Workers | Generation Throughput | Total 36-Section Build Time |
| :--- | :--- | :--- | :---: | :---: | :---: |
| **NVIDIA RTX 2050 (Laptop)** | 4.0 GB GDDR6 | 4-bit (`qwen2.5-3b`) | **4 workers** | **18 - 25 tok/s** | **~5 - 6 minutes** |
| **NVIDIA RTX 3060 / 4060** | 6 - 8 GB GDDR6 | 4-bit / 8-bit | **4 - 6 workers** | **35 - 55 tok/s** | **~3 - 4 minutes** |
| **NVIDIA RTX 4090 / A100** | 24 - 80 GB | 16-bit / FP8 | **8 workers** | **85 - 120 tok/s** | **~1.5 minutes** |
| **Apple Silicon (M2 / M3 Pro)** | Unified Memory | 4-bit (Metal) | **4 workers** | **25 - 40 tok/s** | **~4 minutes** |
| **Modern x86 CPU (AVX-512)** | System RAM (DDR5)| 4-bit (llama.cpp) | **2 workers** | **8 - 14 tok/s** | **~12 - 15 minutes** |
| **Cached Re-Run (Any)** | Disk / SSD | N/A | **N/A** | **Instant** | **< 3 seconds** |

### Benchmark Highlights
- **Parallel CUDA Acceleration:** ReportSmith batches section generation across independent worker threads, maximizing GPU Tensor Core saturation while preventing VRAM exhaustion.
- **Granular Token Budgeting:** Each section is assigned an explicit word budget (typically 200–350 words), preventing local LLM context drift and ensuring fast, predictable generation times.
- **Zero-Latency Resumption:** The project-isolated cache (`cache/<project_sig>/`) saves each section as an individual JSON artifact upon completion. Interrupted builds resume instantly.

---

## 📐 Academic Document Standard (6-Chapter Invariant)

Every report generated by ReportSmith strictly adheres to the standard institutional engineering format:

```
├── PRELIMINARY PAGES (Numbered with Lowercase Roman Numerals: i, ii, iii...)
│   ├── Title Page (Institution Header, Title, Contributor Details, Date)
│   ├── Bonafide Certificate (Supervisor & Department Head Signatures)
│   ├── Declaration & Acknowledgements
│   ├── Executive Abstract
│   ├── Table of Contents (With Dot Leaders & Page Numbers)
│   ├── List of Figures & List of Tables
│   └── List of Abbreviations & Nomenclature
│
├── MAIN BODY CHAPTERS (Numbered with Arabic Decimals: 1, 2, 3...)
│   ├── Chapter 1: Introduction (Overview, Objectives, Problem Statement, Scope)
│   ├── Chapter 2: Literature Survey & Requirements Analysis (Functional/Non-Functional Specs)
│   ├── Chapter 3: System Design & Architecture (Block Diagram, Data Flow, Schematics)
│   ├── Chapter 4: Implementation Methodology (Modular Subsystem Chapters & Algorithms)
│   ├── Chapter 5: Testing, Validation & Performance Evaluation (Test Matrices & Benchmarks)
│   └── Chapter 6: Conclusion & Future Scope (Outcomes, Limitations, Enhancements)
│
└── END MATTER
    ├── References (IEEE Sequentially Numbered: [1], [2], ...)
    └── Appendices (Appendix A: Verbatim Source Code, Appendix B: System Telemetry)
```

### Typography & Formatting Rules
- **Font Family:** Times New Roman across all headings, body text, and tables.
- **Font Hierarchy:** Chapter Titles (16pt Bold), Section Headings (14pt Bold), Subheadings (12pt Bold), Body Text (12pt Regular, 1.15x Line Spacing, Justified).
- **Table Formatting:** Styled headers (`#EAEAEA` background), centered borders, and `<w:cantSplit/>` XML tags to prevent awkward page splits across pages.
- **Figure Formatting:** Centered high-resolution 300 DPI vector graphics with standard academic captions (`Figure X.Y: Caption`).

---

## ⚙️ Configuration Reference (`reportsmith.toml`)

Project defaults can be permanently configured in `reportsmith.toml`:

```toml
[llm]
model = "qwen2.5-coder-3b"
base_url = "http://localhost:1234/v1"  # Or http://localhost:11434/v1 for Ollama
timeout = 600
temperature = 0.2
seed = 42

[paths]
cache_dir = "cache"
out_dir = "out"
content_dir = "Content"
training_dir = "training"

[compute]
use_cuda = true
concurrency = 4

[word_targets]
abstract = 250
"1.1" = 250
"1.2" = 150
"4.1" = 300
default = 220
```

---

## 🧪 Comprehensive Test Suite

ReportSmith includes a test suite covering unit tests, dynamic outline synthesis, CUDA parallelization, multi-domain decoupling, and adversarial edge cases:

```bash
# Run complete test suite (350+ tests)
pytest

# Run fast canonical E2E suite
pytest tests/test_e2e_cross_build.py tests/test_e2e_resilience.py tests/test_e2e_multi_domain.py -v
```

---

## 📄 License & Verification

ReportSmith is engineered for privacy-conscious developers, researchers, and students who demand **complete offline autonomy**, **strict factual correctness**, and **publication-ready aesthetics**.

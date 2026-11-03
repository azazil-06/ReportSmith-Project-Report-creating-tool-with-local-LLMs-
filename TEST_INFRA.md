# ReportSmith E2E Test Infrastructure Specification

**Document Version**: 1.0.0  
**Target Project**: ReportSmith (Deterministic Word Report Generator)  
**Author**: E2E Test Writer (Parallel Testing Track)  
**Authoritative References**: `ORIGINAL_REQUEST.md`, `PROJECT.md`  

---

## 1. Test Architecture Overview

The ReportSmith E2E Test Suite provides an opaque-box, 4-tier verification harness designed to enforce the core production requirements defined in `ORIGINAL_REQUEST.md`:
- **R1**: Prevent Topic Leakage and Cross-Build Hallucination.
- **R2**: Automatic Cache and State Isolation across projects.
- **R3**: Universal Dynamic Outline, Diagram, and Section Synthesis without microcontroller assumptions.

### Test Directory Layout

```
tests/
├── conftest.py                   # Pytest markers, multi-domain fixtures, docx text parser, Windows lock simulator
├── test_e2e_cross_build.py       # Tier 3 (Cache hits vs isolation) & Tier 4 (Grand R1 & R2 lifecycle scenarios)
├── test_e2e_multi_domain.py      # Tier 1 (Ingestion, grounding, diagrams without MCU), Tier 3 (Pairwise), Tier 4 (Academic invariants)
├── test_e2e_resilience.py        # Tier 1 (Cache scoping & safe path), Tier 2 (Boundary cases), Tier 4 (File lock resilience)
└── ... (existing baseline unit/integration tests)
```

---

## 2. 4-Tier Test Matrix & Coverage

### Tier 1: Feature Coverage (>=5 test cases per feature)
| Feature | Target Module | Scope & Invariants Tested | File | Count |
|---|---|---|---|---|
| **F1: Domain Ingestion** | `content_analyzer.py` | Ingestion across Web/Cloud, ML/CV, Bioinformatics, Software App, Embedded IoT, and Cloud Microservices; validates domain classification, metadata extraction, and schema decoupling (`is_hardware=False` for software). | `test_e2e_multi_domain.py` | 6 |
| **F2: Cache Scoping** | `generate.py` | Project signature fingerprinting `sha256(title:abstract)[:12]`, distinct signatures for diffs, cache key computation incorporating signatures, namespaced cache dirs (`cache/<sig>/`). | `test_e2e_resilience.py` | 5 |
| **F3: Grounding Whitelists** | `facts.py`, `check.py` | Dynamic whitelisting of numbers in `brief.abstract`, standard HTTP status codes (`200`, `201`, `204`, `400`, `401`, `403`, `404`, `500`), standard network ports (`80`, `443`, `3000`, `5000`, `8080`), web telemetry metrics, and embedded hardware constants. | `test_e2e_multi_domain.py` | 6 |
| **F4: Diagrams Without MCU** | `diagrams.py` | Software block diagram without MCU pins (PA0, PB6, ST-Link, VCC, GND), Data Flow Diagram (DFD) synthesis instead of circuit wiring, software process flowchart instead of firmware setup/loop, valid PNG headers and resolution. | `test_e2e_multi_domain.py` | 5 |
| **F5: Safe Path Resolution** | `docx_build.py`, `cli.py` | Unlocked path passthrough, single file collision incrementation (`report_1.docx` or `report_updated.docx`), multi-candidate collision resolution (`report_2.docx`), auto-creation of missing directories, `DocxReportBuilder.saved_path` reflection. | `test_e2e_resilience.py` | 5 |

### Tier 2: Boundary & Corner Cases (>=5 test cases per boundary)
| Boundary Area | Target Subsystem | Boundary Conditions Exercised | File | Count |
|---|---|---|---|---|
| **B1: Minimal Abstracts** | `content_analyzer.py` | Single-sentence abstract (<15 words), empty abstract string `""`, Unicode special symbols (`λ, α, β, ∑, 🚀, café`), irregular internal tabs and multiple whitespace, preservation of the mandatory 6-chapter backbone. | `test_e2e_resilience.py` | 5 |
| **B2: Complex Abstracts** | `content_analyzer.py`, `diagrams.py` | Abstract with 8+ distinct subsystems (extracts bounded 3-5 modules), 1000+ words abstract buffer stress, academic depth bounding (strictly 6 chapters), multi-subsystem diagram synthesis, high number density grounding (15+ numbers). | `test_e2e_resilience.py` | 5 |
| **B3: Pseudo-Hardware Terms** | `content_analyzer.py`, `diagrams.py` | Software abstracts with "Kafka message bus" / "event bus", "circuit breaker resilience pattern", "UI wireframes" / "PIN authentication", "Docker port 8080" / "WebSocket", comparative citations ("unlike STM32 embedded systems..."). Strictly verifies `is_hardware=False` and zero hardware fallbacks. | `test_e2e_resilience.py` | 5 |
| **B4: File Collisions** | `docx_build.py` | Exclusive Windows file locking (`win32file.FILE_SHARE_READ`), preservation of original locked file bytes without corruption, validator targeting actual saved file, deep collision chains (0, 1, 2 locked -> candidate 3), non-Word process locks. | `test_e2e_resilience.py` | 5 |
| **B5: Cache Invalidation** | `generate.py` | Single sentence added to abstract, single architecture word changed (MERN -> Django), unrelated project cache directory isolation, deterministic whitespace normalization, fresh prose synthesis upon abstract modification. | `test_e2e_resilience.py` | 5 |

### Tier 3: Cross-Feature Combinations (Pairwise)
| Combination Matrix | Description | Expected Invariant | File |
|---|---|---|---|
| **Domain x Outline x Diagrams** | Evaluates Web/Cloud, ML, Bioinformatics, and Embedded domains across outline synthesis and diagram generation. | Chapter titles and diagrams adapt dynamically to domain entities; zero microcontroller chapters or wiring schematics in non-hardware reports. | `test_e2e_multi_domain.py` (4 tests) |
| **Section Cache Hit x Project Isolation** | Evaluates same-project rerun with `resume=True`, project swap (A -> B), and interleaved builds (A -> B -> A). | 100% cache hit for unchanged project; 0% cache reuse across differing projects; cache persistence after interleaved runs. | `test_e2e_cross_build.py` (3 tests) |

### Tier 4: Real-World Acceptance Scenarios
| Scenario | Scenario Name | Test Procedure | Acceptance Invariant | File |
|---|---|---|---|---|
| **Scenario 1** | **The Grand R1 Test (Cross-Build Topic Isolation)** | Sequential execution in same workspace: Build Project A (Embedded STM32) -> Build Project B (CampusConnect Web) -> Build Project C (FreshKNN ML) -> Build Project D (GeneAlign Bioinformatics). | Generated docx for each project contains **STRICTLY ZERO** foreign keywords or entities from prior builds (e.g. 0 occurrences of STM32, PA0, moisture in Web/ML/Bio reports). | `test_e2e_cross_build.py` |
| **Scenario 2** | **The Grand R2 Test (Cache Invalidation Lifecycle)** | Build report for Abstract 1 (CampusConnect MERN); modify abstract in `Content/` to Django + PostgreSQL; rebuild with persistent cache (`resume=True`). | Stale MERN prose is purged/isolated; newly generated report reflects Django/PostgreSQL; signatures and cache directories are completely separated. | `test_e2e_cross_build.py` |
| **Scenario 3** | **File Lock Resilience (Acceptance)** | Simulates active external lock on `out/report.docx`; runs full build pipeline. | Document is saved cleanly to non-colliding fallback path (e.g. `report_1.docx` or `report_updated.docx`); `validate_report()` succeeds on actual saved file; zero data corruption. | `test_e2e_resilience.py` |
| **Scenario 4** | **Academic Backbone Invariants** | Evaluates generated Word reports across diverse domains. | Document contains exactly 3 sections: Section 1 (Title, no numbering), Section 2 (Prelims, `lowerRoman`), Section 3 (Body chapters 1-6 + end matter, `decimal` starting at 1); all table rows have `w:cantSplit` XML property. | `test_e2e_multi_domain.py` (4 tests) |

---

## 3. Test Fixtures & Authoritative Data

Defined in `tests/conftest.py`:
1. **`SAMPLE_ABSTRACTS`**:
   - `embedded_stm32`: STM32F103C8T6 ARM Cortex-M3 capacitive soil moisture monitoring.
   - `web_campusconnect`: MERN stack event registration platform with JWT, ports 3000/5000, HTTP 200/201.
   - `ml_freshknn`: FreshKNN produce freshness classifier with 128x128 images, KNN, Euclidean distance, 94.5% accuracy.
   - `bio_genealign`: GeneAlign Smith-Waterman DNA sequence alignment benchmarked against NCBI BLAST.
   - `software_moneygo`: MoneyGo personal finance MVC desktop application with SQLite.
2. **`make_project_docx(target_dir, title, abstract, filename, team_members)`**:
   Synthesizes realistic student project Word documents in `Content/Project.docx`.
3. **`extract_docx_all_text(docx_file)`**:
   Exhaustively extracts text from all paragraphs, table cells, headers, and footers.
4. **`simulate_file_lock(file_path)`**:
   Native Windows file locking context manager utilizing `win32file.CreateFile(..., FILE_SHARE_READ)` to accurately simulate an open Microsoft Word process holding an exclusive write lock.

---

## 4. Test Execution Instructions

### Running the Complete E2E Suite
```powershell
python -m pytest tests/test_e2e_cross_build.py tests/test_e2e_multi_domain.py tests/test_e2e_resilience.py -v
```

### Running by Tier Markers
```powershell
# Tier 1: Feature Coverage (Ingestion, Scoping, Grounding, Diagrams, Safe Path)
python -m pytest -m tier1 -v

# Tier 2: Boundary & Corner Cases (Minimal, Complex, Pseudo-Hardware, Locks, Invalidation)
python -m pytest -m tier2 -v

# Tier 3: Pairwise Combinations
python -m pytest -m tier3 -v

# Tier 4: Real-World Acceptance Scenarios (Grand R1, Grand R2, Lock Resilience, Backbone Invariants)
python -m pytest -m tier4 -v
```

### Running Individual Test Suites
```powershell
# Cross-Build Isolation & Lifecycle
python -m pytest tests/test_e2e_cross_build.py -v

# Resilience & Boundary Testing
python -m pytest tests/test_e2e_resilience.py -v

# Multi-Domain & Academic Invariants
python -m pytest tests/test_e2e_multi_domain.py -v
```

---

## 5. Current Baseline Test Results & Defect Catalog

### Summary
- **Total Test Cases**: 66 items
- **Passed**: 57 items (86.4%)
- **Failed**: 9 items (13.6%)

### Baseline Defect Mapping to Milestones
| Test | Failure Summary | Root Cause | Target Milestone |
|---|---|---|---|
| `test_tier1_ingest_web_cloud_domain` | Expected domain `WEB_CLOUD`, got `SOFTWARE_APP` | Hardcoded topic branching in `content_analyzer.py` lacks general web decoupling | Milestone M2 (F7, F8) |
| `test_tier1_ingest_bioinformatics_domain` | Classified as `EMBEDDED_HARDWARE` | Missing domain classifier fallback for non-hardware scientific disciplines | Milestone M2 (F7, F8) |
| `test_tier1_facts_whitelists_abstract_numbers` | Abstract numbers not in `facts.known_numbers` | `derive_facts()` does not extract numerical tokens from `brief.abstract` | Milestone M2 (F9) |
| `test_tier1_facts_whitelists_standard_http_status_codes` | HTTP codes `{200, 201, 204, 400, 401, 403, 404, 500}` missing | `derive_facts()` static whitelist lacks standard HTTP codes | Milestone M2 (F9) |
| `test_tier1_facts_whitelists_standard_network_ports` | Network ports `{80, 443, 3000, 5000, 8080}` missing | `derive_facts()` static whitelist lacks networking ports | Milestone M2 (F9) |
| `test_tier1_facts_whitelists_latency_and_telemetry_metrics` | Metrics `140`, `450` missing | Missing telemetry number parsing from input abstract | Milestone M2 (F9) |
| `test_tier1_facts_preserves_hardware_calibration_numbers` | ADC calibration bounds missing | Hardcoded constant assumptions | Milestone M2 (F9) |
| `test_tier1_validator_passes_on_web_cloud_numbers` | Rejects valid web docx with 6 ungrounded errors | Validator rejects numbers not in `facts.known_numbers` | Milestone M2 (F9) |
| `test_tier2_boundary_complex_abstract_high_number_density_grounding` | Number `99.9` missing from `known_numbers` | Abstract numbers not whitelisted | Milestone M2 (F9) |

*All 9 failing tests are strictly intentional, non-facade verifications that precisely expose the features to be delivered in Milestone M2.*

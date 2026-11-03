"""Empirical adversarial stress test suite for Milestone 4:
Universal StubLLM Synthesis, Cross-Build Topic Isolation, and Training DB Isolation.

Authored by: Challenger M4_1 (Empirical Challenger)

Verifies:
1. Grand R1 sequential cross-build topic isolation across diverse unseen domains.
2. Complete absence of banned hardware tokens in non-hardware reports:
   ['stm32', 'microcontroller', 'pinout', 'soil moisture', 'arduino', 'pa0', 'pc13', 'ldr', 'ultrasonic'].
3. Active project entity reflection (each report reflects only its own entities).
4. Full document 6-chapter build integrity for novel unseen domains.
5. ExemplarStore isolation against cross-topic / cross-domain leakage.
6. Cache scoping and invalidation across project abstract changes.
"""

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Dict, List, Set
import docx
import pytest

from reportsmith.brief import ProjectBrief
from reportsmith.config import load_config
from reportsmith.content_analyzer import analyze_content_folder
from reportsmith.docx_build import DocxReportBuilder
from reportsmith.generate import SectionGenerator
from reportsmith.llm import StubLLM, _extract_prompt_context, _synthesize_scholarly_prose
from reportsmith.trainer import ExemplarStore, BANNED_HARDWARE_TOKENS

try:
    from tests.conftest import SAMPLE_ABSTRACTS, extract_docx_all_text, make_project_docx
except ImportError:
    from conftest import SAMPLE_ABSTRACTS, extract_docx_all_text, make_project_docx


# Banned hardware tokens strictly forbidden in non-hardware reports
STRICT_BANNED_HARDWARE_TOKENS = [
    "stm32",
    "microcontroller",
    "pinout",
    "soil moisture",
    "arduino",
    "pa0",
    "pc13",
    "ldr",
    "ultrasonic",
    "cortex-m",
    "breadboard",
    "sensor wiring",
]


def assert_zero_keywords(docx_path: Path, banned_tokens: List[str], label: str):
    """Scan all paragraphs, tables, headers, and footers of docx_path and assert 0 occurrences."""
    assert docx_path.is_file(), f"Document {docx_path} does not exist"
    full_text = extract_docx_all_text(docx_path).lower()

    violations = []
    for tok in banned_tokens:
        tok_low = tok.lower()
        pattern = rf"\b{re.escape(tok_low)}\b"
        match = re.search(pattern, full_text)
        if match:
            idx = match.start()
            snippet = full_text[max(0, idx - 40) : min(len(full_text), idx + len(tok_low) + 40)]
            violations.append(f"Found banned token '{tok}' in {label}: '...{snippet}...'")

    assert not violations, f"Topic contamination detected in {label}!\n" + "\n".join(violations)


def assert_contains_active_entities(docx_path: Path, expected_entities: List[str], label: str):
    """Assert that the document contains characteristic entities of the active project."""
    assert docx_path.is_file(), f"Document {docx_path} does not exist"
    full_text = extract_docx_all_text(docx_path).lower()

    missing = []
    for ent in expected_entities:
        ent_low = ent.lower()
        pattern = rf"\b{re.escape(ent_low)}\b"
        if not re.search(pattern, full_text):
            missing.append(ent)

    assert not missing, f"Active entities missing in {label}!\nMissing: {missing}"


# ==============================================================================
# 1. SEQUENTIAL CROSS-BUILD ISOLATION ACROSS UNSEEN NOVEL DOMAINS
# ==============================================================================

@pytest.mark.tier4
def test_empirical_sequential_cross_build_with_unseen_novel_domains(tmp_path):
    """Sequential cross-build topic isolation across 5 diverse domains including unseen domains.

    Sequence:
      1. Embedded Hardware (STM32 Plant Monitor)
      2. Cybersecurity Intrusion Detection (CyberGuard Deep Packet Inspection) [UNSEEN]
      3. Quantum Cryptography (QuantumKey BB84 Protocol Simulation) [UNSEEN]
      4. Web Cloud Services (CampusConnect MERN)
      5. Bioinformatics (GeneAlign Dynamic Programming)
    """
    workspace = tmp_path / "workspace"
    content_dir = workspace / "Content"
    out_dir = workspace / "out"
    cache_dir = workspace / "cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    config = load_config()

    sections_to_test = {"abstract", "1.1", "1.2", "2.1", "3.1", "4.1", "5.1", "6.1"}

    # --- Step 1: Build Hardware Project A (STM32 Plant Monitor) ---
    sample_a = SAMPLE_ABSTRACTS["embedded_stm32"]
    make_project_docx(content_dir, sample_a["title"], sample_a["abstract"])
    brief_a, facts_a, outline_a = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    sig_a = getattr(brief_a, "project_signature", "sig_a")

    gen_a = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_a)
    data_a = gen_a.generate_all(outline_a, brief_a, facts_a, allowed_sections=sections_to_test, resume=True)
    doc_a = out_dir / "report_stm32.docx"
    DocxReportBuilder(brief_a, facts_a, outline_a, sample_path="COA_report.docx").build(data_a, doc_a)
    assert doc_a.is_file()

    # --- Step 2: Build Unseen Domain Project B (CyberGuard: Intrusion Detection) ---
    shutil.rmtree(content_dir)
    title_b = "CyberGuard: Deep Packet Inspection and Anomaly-Based Network Intrusion Detection System"
    abstract_b = (
        "CyberGuard is a high-performance network intrusion detection framework utilizing deep packet "
        "inspection and statistical anomaly detection. Developed in C++ and Python using libpcap packet "
        "capture libraries, the system monitors ingress TCP/UDP streams across promiscuous network interfaces. "
        "The classification engine applies signature rules and entropy metrics to identify zero-day port scans, "
        "SYN flood attacks, and malicious payloads. In 10 Gbps network evaluations, CyberGuard achieved 99.4% "
        "threat detection accuracy with an average packet processing latency of 12 microseconds."
    )
    make_project_docx(content_dir, title_b, abstract_b)
    brief_b, facts_b, outline_b = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    sig_b = getattr(brief_b, "project_signature", "sig_b")

    gen_b = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_b)
    data_b = gen_b.generate_all(outline_b, brief_b, facts_b, allowed_sections=sections_to_test, resume=True)
    doc_b = out_dir / "report_cyberguard.docx"
    DocxReportBuilder(brief_b, facts_b, outline_b, sample_path="COA_report.docx").build(data_b, doc_b)
    assert doc_b.is_file()

    # Assert 0 hardware keywords in CyberGuard
    assert_zero_keywords(doc_b, STRICT_BANNED_HARDWARE_TOKENS, "Project B (CyberGuard)")
    # Assert 0 Project A keywords in CyberGuard
    assert_zero_keywords(doc_b, ["soil moisture", "plant watering", "stm32", "pa0", "pc13"], "Project B (CyberGuard)")
    # Assert active entities present
    assert_contains_active_entities(doc_b, ["cyberguard", "deep packet inspection", "intrusion detection"], "Project B (CyberGuard)")

    # --- Step 3: Build Unseen Domain Project C (QuantumKey: BB84 Quantum Distribution) ---
    shutil.rmtree(content_dir)
    title_c = "QuantumKey: Simulation of BB84 Quantum Key Distribution Protocol and Decoy-State Analysis"
    abstract_c = (
        "QuantumKey is a computational simulation platform for quantum key distribution (QKD) modeling the "
        "BB84 protocol with decoy-state modulation over optical fiber channels. Built using Python, NumPy, "
        "and Qiskit, the system models single-photon polarization states across rectilinear and diagonal bases. "
        "The software calculates Quantum Bit Error Rate (QBER), performs cascade error correction, and executes "
        "privacy amplification to extract unconditionally secure cryptographic keys under photon number splitting attacks. "
        "Simulations over a 100 km simulated fiber demonstrated secure key generation rates of 1.8 kbps with QBER under 2.5%."
    )
    make_project_docx(content_dir, title_c, abstract_c)
    brief_c, facts_c, outline_c = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    sig_c = getattr(brief_c, "project_signature", "sig_c")

    gen_c = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_c)
    data_c = gen_c.generate_all(outline_c, brief_c, facts_c, allowed_sections=sections_to_test, resume=True)
    doc_c = out_dir / "report_quantumkey.docx"
    DocxReportBuilder(brief_c, facts_c, outline_c, sample_path="COA_report.docx").build(data_c, doc_c)
    assert doc_c.is_file()

    # Assert 0 hardware keywords in QuantumKey
    assert_zero_keywords(doc_c, STRICT_BANNED_HARDWARE_TOKENS, "Project C (QuantumKey)")
    # Assert 0 Project A and Project B keywords in QuantumKey
    assert_zero_keywords(
        doc_c,
        ["stm32", "soil moisture", "pa0", "cyberguard", "libpcap", "packet inspection", "syn flood"],
        "Project C (QuantumKey)",
    )
    # Assert active entities present
    assert_contains_active_entities(doc_c, ["quantumkey", "bb84", "quantum key distribution"], "Project C (QuantumKey)")

    # --- Step 4: Build Web Cloud Project D (CampusConnect) ---
    shutil.rmtree(content_dir)
    sample_d = SAMPLE_ABSTRACTS["web_campusconnect"]
    make_project_docx(content_dir, sample_d["title"], sample_d["abstract"])
    brief_d, facts_d, outline_d = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    sig_d = getattr(brief_d, "project_signature", "sig_d")

    gen_d = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_d)
    data_d = gen_d.generate_all(outline_d, brief_d, facts_d, allowed_sections=sections_to_test, resume=True)
    doc_d = out_dir / "report_campusconnect.docx"
    DocxReportBuilder(brief_d, facts_d, outline_d, sample_path="COA_report.docx").build(data_d, doc_d)
    assert doc_d.is_file()

    # Assert 0 hardware keywords in CampusConnect
    assert_zero_keywords(doc_d, STRICT_BANNED_HARDWARE_TOKENS, "Project D (CampusConnect)")
    # Assert 0 prior project keywords (STM32, CyberGuard, QuantumKey)
    assert_zero_keywords(
        doc_d,
        ["stm32", "soil moisture", "cyberguard", "libpcap", "quantumkey", "bb84", "qber"],
        "Project D (CampusConnect)",
    )
    assert_contains_active_entities(doc_d, ["campusconnect", "mern", "mongodb"], "Project D (CampusConnect)")

    # --- Step 5: Build Bioinformatics Project E (GeneAlign) ---
    shutil.rmtree(content_dir)
    sample_e = SAMPLE_ABSTRACTS["bio_genealign"]
    make_project_docx(content_dir, sample_e["title"], sample_e["abstract"])
    brief_e, facts_e, outline_e = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")
    sig_e = getattr(brief_e, "project_signature", "sig_e")

    gen_e = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig_e)
    data_e = gen_e.generate_all(outline_e, brief_e, facts_e, allowed_sections=sections_to_test, resume=True)
    doc_e = out_dir / "report_genealign.docx"
    DocxReportBuilder(brief_e, facts_e, outline_e, sample_path="COA_report.docx").build(data_e, doc_e)
    assert doc_e.is_file()

    # Assert 0 hardware keywords in GeneAlign
    assert_zero_keywords(doc_e, STRICT_BANNED_HARDWARE_TOKENS, "Project E (GeneAlign)")
    # Assert 0 prior project keywords (STM32, CyberGuard, QuantumKey, CampusConnect)
    assert_zero_keywords(
        doc_e,
        ["stm32", "soil moisture", "cyberguard", "quantumkey", "campusconnect", "mern", "mongodb"],
        "Project E (GeneAlign)",
    )
    assert_contains_active_entities(doc_e, ["genealign", "smith-waterman", "sequence alignment"], "Project E (GeneAlign)")


# ==============================================================================
# 2. FULL DOCUMENT BUILD INTEGRITY FOR NOVEL UNSEEN DOMAIN
# ==============================================================================

@pytest.mark.tier4
def test_empirical_full_document_build_for_novel_unseen_domain(tmp_path):
    """Empirically verify a full 6-chapter end-to-end build for a completely novel domain.

    Ensures that prelims, Chapters 1-6, and end-matter assemble without scoping bugs
    or hardware tables, and that strict zero hardware tokens leak across the entire document.
    """
    workspace = tmp_path / "workspace_full"
    content_dir = workspace / "Content"
    out_dir = workspace / "out"
    cache_dir = workspace / "cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    config = load_config()

    title = "CyberGuard: Deep Packet Inspection and Anomaly-Based Network Intrusion Detection System"
    abstract = (
        "CyberGuard is a high-performance network intrusion detection framework utilizing deep packet "
        "inspection and statistical anomaly detection. Developed in C++ and Python using libpcap packet "
        "capture libraries, the system monitors ingress TCP/UDP streams across promiscuous network interfaces. "
        "The classification engine applies signature rules and entropy metrics to identify zero-day port scans, "
        "SYN flood attacks, and malicious payloads. In 10 Gbps network evaluations, CyberGuard achieved 99.4% "
        "threat detection accuracy with an average packet processing latency of 12 microseconds."
    )
    make_project_docx(content_dir, title, abstract)
    brief, facts, outline = analyze_content_folder(str(content_dir), sample_docx_path="COA_report.docx")

    assert brief.implementation.is_hardware is False

    # Generate ALL sections across all 6 chapters and prelims
    all_sec_ids = {item.id for item in outline.items}
    sig = getattr(brief, "project_signature", "sig_cyberguard")
    gen = SectionGenerator(StubLLM(), config=config, cache_dir=cache_dir / sig)
    data = gen.generate_all(outline, brief, facts, allowed_sections=all_sec_ids, resume=True)

    doc_path = out_dir / "report_cyberguard_full.docx"
    builder = DocxReportBuilder(brief, facts, outline, sample_path="COA_report.docx")
    builder.build(data, doc_path)

    assert doc_path.is_file(), "Report document must be created"
    doc = docx.Document(str(doc_path))

    # Assert 0 banned hardware tokens across entire document
    assert_zero_keywords(doc_path, STRICT_BANNED_HARDWARE_TOKENS, "CyberGuard Full Document")

    # Assert active entities present in document text
    assert_contains_active_entities(doc_path, ["cyberguard", "deep packet inspection", "intrusion detection"], "CyberGuard Full Document")

    # Verify no hardware pin tables were generated
    table_texts = [cell.text.lower() for tbl in doc.tables for row in tbl.rows for cell in row.cells]
    assert not any("pin configuration" in t for t in table_texts)
    assert not any("pa0" in t for t in table_texts)
    assert not any("stm32" in t for t in table_texts)


# ==============================================================================
# 3. ADVERSARIAL VULNERABILITY DEMONSTRATION: SUBSTRING KEYWORD COLLISION
# ==============================================================================

def test_empirical_vulnerability_domain_keyword_substring_collision():
    """Adversarial stress test: Verify that word-boundary regex matching in classify_text_domain
    prevents false-positive EMBEDDED_HARDWARE classification for software projects containing
    common English words ending in 'led' (e.g. 'knowledge', 'handled', 'scheduled').
    """
    from reportsmith.trainer import classify_text_domain

    # A purely theoretical software project mentioning "zero-knowledge proofs"
    # 'knowledge' contains 'led' as a substring, but word-boundary matching isolates it.
    text_with_knowledge = (
        "This project presents an authentication framework using zero-knowledge proofs. "
        "The cryptographic protocols verify client knowledge without revealing sensitive secrets."
    )
    classified = classify_text_domain(text_with_knowledge)
    assert classified != "EMBEDDED_HARDWARE", (
        "Vulnerability fixed: 'knowledge' must NOT trigger 'led' keyword in DOMAIN_EMBEDDED!"
    )
    assert classified == "GENERAL_ACADEMIC", (
        f"Expected GENERAL_ACADEMIC for novel theoretical domain, got {classified}"
    )


def test_empirical_zero_knowledge_abstract_no_hardware_pins():
    """End-to-end verification: an abstract with zero-knowledge and distributed ledger terms
    must yield is_hardware=False and 0 synthesized GPIO pins.
    """
    from reportsmith.content_analyzer import _synthesize_implementation_from_abstract

    abs_text = (
        "A decentralized zero-knowledge authentication scheme implemented for distributed ledger networks. "
        "The system handles off-chain proof generation and scheduled state updates."
    )
    impl, behaviour, refs = _synthesize_implementation_from_abstract(
        domain="GENERAL_ACADEMIC",
        title="Zero-Knowledge Ledger Framework",
        abstract_text=abs_text,
        code_file=None,
        code_info={},
        combined_corpus=abs_text,
        llm_meta={},
    )
    assert impl.is_hardware is False, "Theoretical software project must not be flagged as hardware"
    assert len(impl.pins) == 0, f"Expected 0 synthesized pins, got {impl.pins}"


# ==============================================================================
# 3. EXEMPLAR STORE ISOLATION AND HARDWARE SANITIZATION
# ==============================================================================

def test_empirical_exemplar_store_isolation_under_adversarial_queries():
    """Verify that ExemplarStore enforces strict domain isolation and sanitizes hardware tokens."""
    # Mock exemplar database with both hardware and non-hardware exemplars
    mock_db = {
        "documents": [
            {
                "title": "Automated Soil Moisture Monitoring and Plant Watering Reminder System",
                "domain": "EMBEDDED_HARDWARE",
                "sections": {
                    "1.1": {"title": "Overview", "paragraphs": ["STM32 microcontroller manages PA0 ADC channel."]},
                },
            },
            {
                "title": "CampusConnect: MERN Architecture Event Platform",
                "domain": "WEB_CLOUD",
                "sections": {
                    "1.1": {"title": "Overview", "paragraphs": ["CampusConnect uses React and Express."]},
                },
            },
        ],
        "exemplars": {
            "EMBEDDED_HARDWARE": {
                "introduction": [
                    {"paragraphs": ["The STM32F103 microcontroller controls the sensor via pinout PC13."]}
                ]
            },
            "SOFTWARE_APP": {
                "introduction": [
                    {"paragraphs": ["This software system manages user transactions."]}
                ]
            },
        },
    }

    store = ExemplarStore(db_data=mock_db)

    # 1. Query with unseen domain: CYBER_SECURITY
    match = store.get_exact_project_match(
        active_title="CyberGuard: Deep Packet Inspection System",
        active_domain="CYBER_SECURITY",
        min_token_overlap=0.85,
    )
    assert match is None, "Novel unseen domain must return None for exact project match"

    # 2. Query with near title match but domain mismatch: Embedded vs Web
    match_mismatch = store.get_exact_project_match(
        active_title="Automated Soil Moisture Monitoring System",
        active_domain="WEB_CLOUD",  # Domain is non-hardware, target is hardware!
        min_token_overlap=0.85,
    )
    assert match_mismatch is None, "Cross-domain match must be rejected despite title similarity"

    # 3. Query domain exemplar for non-hardware unseen domain: QUANTUM_COMPUTING
    ex = store.get_domain_exemplar(domain="QUANTUM_COMPUTING", role="introduction", is_hardware=False)
    assert ex is not None, "ExemplarStore should return neutral generic academic template"
    text = " ".join(ex.get("paragraphs", [])).lower()
    for tok in STRICT_BANNED_HARDWARE_TOKENS:
        assert tok not in text, f"Hardware token '{tok}' leaked into non-hardware exemplar"


# ==============================================================================
# 4. STUBLLM PROSE SYNTHESIS FIDELITY AND HARDWARE PURITY
# ==============================================================================

def test_empirical_stubllm_prose_synthesis_entity_fidelity():
    """Verify that StubLLM dynamically conditions prose on prompt entities with zero hardware leaks."""
    stub = StubLLM(seed=42)

    prompt = (
        "TASK: Write Section 1.1 Overview.\n"
        "Title: NeuroVision: Brain-Computer Interface for EEG Motor Imagery Classification\n"
        "PROJECT DOMAIN: MACHINE_LEARNING\n"
        "Computing Environment: High-Performance GPU Computing Cluster / CUDA Runtime\n"
        "Framework: PyTorch / MNE-Python EEG Toolkit\n"
        "Architecture Subsystems:\n"
        "- Signal Preprocessing Subsystem\n"
        "- Feature Extraction Subsystem\n"
        "- Classification Engine Subsystem\n"
        "Operational States & Classes:\n"
        "- Rest State, Left Hand Imagery, Right Hand Imagery\n"
        "ABSTRACT: NeuroVision captures 64-channel EEG signals for real-time motor imagery classification.\n"
    )

    res = stub.generate(
        prompt=prompt,
        system_prompt="You are an academic report author.",
        section_id="1.1",
        target_words=250,
    )

    assert "paragraphs" in res
    paras = res["paragraphs"]
    assert len(paras) >= 2, "Must generate substantial paragraphs"
    full_text = " ".join(paras).lower()

    # Verify zero banned hardware tokens
    for tok in STRICT_BANNED_HARDWARE_TOKENS:
        assert tok not in full_text, f"StubLLM generated banned hardware token '{tok}' in ML project"

    # Verify active entities present
    assert "neurovision" in full_text
    assert "signal preprocessing" in full_text or "eeg" in full_text
    assert "pytorch" in full_text or "gpu" in full_text


# ==============================================================================
# 5. CACHE DIRECTORY AND KEY ISOLATION ACROSS ABSTRACT REVISIONS
# ==============================================================================

def test_empirical_cache_directory_and_key_isolation(tmp_path):
    """Verify that SectionGenerator creates isolated cache directories per project signature."""
    cache_root = tmp_path / "cache"
    config = load_config()

    # Abstract 1: Cryptography
    title1 = "CryptoShield: Post-Quantum Lattice Encryption"
    abs1 = "Implementation of Kyber-768 lattice-based key encapsulation mechanism."
    sig1 = hashlib.sha256(f"{title1}:{abs1}".encode()).hexdigest()[:12]

    # Abstract 2: Cloud
    title2 = "CloudMesh: Service Mesh Observability"
    abs2 = "Distributed telemetry collection using Envoy proxies and OpenTelemetry."
    sig2 = hashlib.sha256(f"{title2}:{abs2}".encode()).hexdigest()[:12]

    assert sig1 != sig2, "Different abstracts must have distinct signatures"

    gen1 = SectionGenerator(StubLLM(), config=config, cache_dir=cache_root / sig1)
    gen2 = SectionGenerator(StubLLM(), config=config, cache_dir=cache_root / sig2)

    assert gen1.cache_dir != gen2.cache_dir
    assert str(sig1) in str(gen1.cache_dir)
    assert str(sig2) in str(gen2.cache_dir)

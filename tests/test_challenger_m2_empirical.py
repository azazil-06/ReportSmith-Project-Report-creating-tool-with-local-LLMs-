"""Empirical adversarial stress test suite for Milestone 2:
Multi-domain ingestion, schema decoupling, dynamic subsystem extraction,
and grounding number derivation & whitelisting.

Authored by: Challenger 1 (Milestone 2)
"""

from pathlib import Path
from typing import List, Set
import pytest

from reportsmith.brief import (
    BoardInfo,
    ComponentItem,
    ImplementationInfo,
    PinMapping,
    ProjectBrief,
    SystemPlatformInfo,
    load_brief,
)
from reportsmith.check import ReportValidator, extract_numbers_from_prose
from reportsmith.content_analyzer import (
    _get_domain_default_subsystems,
    extract_dynamic_subsystems,
    normalize_title,
)
from reportsmith.facts import derive_facts
from reportsmith.trainer import (
    DOMAIN_BIOINFORMATICS,
    DOMAIN_EMBEDDED,
    DOMAIN_GENERAL,
    DOMAIN_ML,
    DOMAIN_SOFTWARE,
    DOMAIN_WEB,
    classify_text_domain,
)


def _make_brief(
    title: str = "Test Project",
    domain: str = "GENERAL_ACADEMIC",
    abstract: str = "Default test abstract.",
    is_hardware: bool = False,
    mode: str = "simulation",
    pins: list = None,
    components: list = None,
) -> ProjectBrief:
    base = load_brief("examples/brief.yaml")
    data = base.model_dump()
    data["project"]["title"] = title
    data["domain"] = domain
    data["abstract"] = abstract
    data["implementation"]["mode"] = mode
    data["implementation"]["is_hardware"] = is_hardware
    data["implementation"]["pins"] = pins or []
    data["implementation"]["components"] = components or []
    if not is_hardware:
        data["code_file"] = None
        data["behaviour"]["calibration_constants"] = {}
        data["behaviour"]["thresholds"] = {}
        data["behaviour"]["state_names"] = []
    return ProjectBrief.model_validate(data)



# ==============================================================================
# 1. ADVERSARIAL SUBSYSTEM EXTRACTION TESTS (F8)
# ==============================================================================


@pytest.mark.parametrize(
    "abstract, domain, desc",
    [
        ("", "GENERAL_ACADEMIC", "Empty abstract string"),
        ("   \n\t  \r\n   ", "WEB_CLOUD", "Whitespace and newlines only"),
        ("Short", "BIOINFORMATICS", "Ultra-short 5-char abstract"),
        ("A" * 19, "MACHINE_LEARNING", "Boundary 19 chars (below threshold)"),
        ("A" * 20, "MACHINE_LEARNING", "Boundary exact 20 chars"),
        ("A" * 21, "DATA_ANALYTICS", "Boundary 21 chars"),
        ("No structured keywords here just random text words without punctuation", "GENERAL_ACADEMIC", "Unstructured prose"),
        ("12345 67890 12345 67890 12345", "SOFTWARE_APP", "Numeric tokens only"),
        ("🚀🔥💻 DNA Sequence Alignment and Analysis 🧬🔬", "BIOINFORMATICS", "Emojis and special unicode"),
        ("本项目包含数据采集分析与展示模块用于学术研究系统", "GENERAL_ACADEMIC", "Non-Latin characters"),
    ],
)
def test_extract_dynamic_subsystems_adversarial_boundaries(abstract: str, domain: str, desc: str):
    """Stress test boundary condition inputs: extract_dynamic_subsystems must NEVER fail

    and MUST ALWAYS return between 3 and 5 valid, non-empty, distinct, capitalized modules.
    """
    modules = extract_dynamic_subsystems(abstract, domain=domain)

    # 1. Length invariant: 3 to 5
    assert 3 <= len(modules) <= 5, f"[{desc}] Expected 3-5 modules, got {len(modules)}: {modules}"

    # 2. Non-empty string invariant
    for m in modules:
        assert isinstance(m, str) and len(m.strip()) > 0, f"[{desc}] Invalid module string: '{m}'"

    # 3. Distinct invariant (case-insensitive)
    lower_set = {m.lower() for m in modules}
    assert len(lower_set) == len(modules), f"[{desc}] Duplicate modules detected: {modules}"

    # 4. Capitalization invariant (scholarly title case: first char uppercase)
    for m in modules:
        assert m[0].isupper(), f"[{desc}] Module not capitalized: '{m}'"


@pytest.mark.parametrize(
    "prefix",
    [
        "structured into",
        "consists of",
        "divided into",
        "comprises",
        "organized into",
        "features include",
        "modules include",
    ],
)
def test_extract_dynamic_subsystems_structural_markers(prefix: str):
    """Test all grammatical marker triggers with structured candidate lists."""
    abstract = (
        f"The proposed architecture is {prefix}: Client Interface, "
        "RESTful API Routing, Authentication Controller, and Persistence Datastore. "
        "It achieves high throughput and sub-second response times."
    )
    modules = extract_dynamic_subsystems(abstract, domain="WEB_CLOUD")

    assert 3 <= len(modules) <= 5
    assert len(set(m.lower() for m in modules)) == len(modules)
    # Check that client interface and api were extracted
    assert any("client" in m.lower() for m in modules)
    assert any("api" in m.lower() for m in modules)


def test_extract_dynamic_subsystems_excessive_candidates_capped_at_five():
    """Verify that abstracts listing >5 modules are cleanly capped at exactly 5 distinct items."""
    abstract = (
        "The software comprises: Data Ingestion, Noise Filtering, Feature Extraction, "
        "Dimensionality Reduction, Model Inference, Metric Evaluation, Alert Dispatcher, "
        "and Audit Logging subsystem."
    )
    modules = extract_dynamic_subsystems(abstract, domain="MACHINE_LEARNING")
    assert len(modules) == 5
    assert len(set(m.lower() for m in modules)) == 5


def test_extract_dynamic_subsystems_repetitive_candidates_fallback():
    """Verify that duplicate candidate lists are deduplicated and supplemented by domain fallbacks to >= 3."""
    abstract = "The system consists of Alpha Module, Alpha Module, Alpha Module, and Alpha Module."
    modules = extract_dynamic_subsystems(abstract, domain="GENERAL_ACADEMIC")

    assert 3 <= len(modules) <= 5
    assert len(set(m.lower() for m in modules)) == len(modules)
    assert any("alpha" in m.lower() for m in modules)


@pytest.mark.parametrize(
    "domain",
    [
        "BIOINFORMATICS",
        "WEB_CLOUD",
        "SOFTWARE_APP",
        "MACHINE_LEARNING",
        "DATA_ANALYTICS",
        "EMBEDDED_HARDWARE",
        "EMBEDDED_IOT",
        "COA_SYSTEMS",
        "GENERAL_ACADEMIC",
        "NON_EXISTENT_DOMAIN",
        "",
        None,
    ],
)
def test_extract_dynamic_subsystems_all_domain_defaults(domain):
    """Verify that every domain returns 3-5 valid default modules when abstract contains no structured data."""
    abstract = "This report discusses experimental results without any explicit architectural breakdown."
    modules = extract_dynamic_subsystems(abstract, domain=domain)

    assert 3 <= len(modules) <= 5
    assert len(set(m.lower() for m in modules)) == len(modules)
    for m in modules:
        assert len(m.strip()) > 3
        assert m[0].isupper()


# ==============================================================================
# 2. TITLE NORMALIZATION & DOMAIN CLASSIFICATION TESTS (F8)
# ==============================================================================


@pytest.mark.parametrize(
    "raw_title, abstract, expected_tokens",
    [
        ("CAMPUSCONNECT: A CAMPUS SOCIAL PLATFORM", "CampusConnect is a modern MERN app.", ["CampusConnect", "Platform"]),
        ("GENEALIGN: SEQUENCE ALIGNMENT USING SMITH-WATERMAN", "GeneAlign tool.", ["GeneAlign", "Smith-Waterman"]),
        ("FRESHKNN: AUTOMATED PRODUCE FRESHNESS DETECTION", "FreshKNN classifier.", ["FreshKNN", "Freshness"]),
        ("MONEYGO: PERSONAL EXPENSE TRACKING SYSTEM", "MoneyGo is a desktop app.", ["MoneyGo", "Expense"]),
        ("PARKEASE: IOT-ENABLED PARKING RESERVATION", "ParkEase parking.", ["ParkEase", "IOT-Enabled"]),
        ("dna and rna sequence alignment via rest api", "", ["DNA", "RNA", "REST", "API"]),
        ("stm32-based soil moisture controller with ble and adc", "", ["STM32-Based", "BLE", "ADC"]),
        ("a study of knn algorithms in machine learning", "", ["A", "Study", "KNN"]),
        ("", "", ["Technical", "Project", "Report"]),
        ("ab", "", ["Technical", "Project", "Report"]),
    ],
)
def test_normalize_title_camelcase_and_acronyms(raw_title: str, abstract: str, expected_tokens: List[str]):
    """Empirically verify title normalization recovers CamelCase and preserves technical acronyms."""
    norm = normalize_title(raw_title, abstract)
    for token in expected_tokens:
        assert token in norm, f"Expected '{token}' in normalized title '{norm}'"


def test_classify_text_domain_bioinformatics_priority():
    """Verify that bioinformatics terms trigger DOMAIN_BIOINFORMATICS accurately over general ML."""
    bio_text = (
        "GeneAlign performs optimal pairwise sequence alignment using the Smith-Waterman "
        "dynamic programming algorithm with affine gap penalty on nucleotide FASTA datasets."
    )
    domain = classify_text_domain(bio_text)
    assert domain == DOMAIN_BIOINFORMATICS


def test_classify_text_domain_web_and_software():
    """Verify web cloud and software app classification accuracy."""
    web_text = "CampusConnect is a MERN stack web application with React, Node.js, Express, and JWT auth."
    assert classify_text_domain(web_text) == DOMAIN_WEB

    sw_text = "ResumeBuilder is a Java Swing application with SQLite database persistence and PDF export."
    assert classify_text_domain(sw_text) == DOMAIN_SOFTWARE


# ==============================================================================
# 3. SCHEMA DECOUPLING & HARDWARE INDEPENDENCE TESTS (F7)
# ==============================================================================


def test_system_platform_info_software_defaults():
    """Verify SystemPlatformInfo can be instantiated without hardware fields and defaults to safe strings."""
    platform = SystemPlatformInfo(name="Node.js 20 LTS")
    assert platform.name == "Node.js 20 LTS"
    assert platform.mcu_part_number == ""
    assert platform.core == ""
    assert platform.clock == ""
    assert platform.flash == ""
    assert platform.sram == ""
    assert platform.voltage == ""

    # Backwards compatibility alias
    assert BoardInfo is SystemPlatformInfo
    board = BoardInfo(name="Python ML Environment")
    assert isinstance(board, SystemPlatformInfo)


def test_implementation_info_hardware_flag_resolution():
    """Verify ImplementationInfo correctly auto-infers is_hardware from mode and pins."""
    # Simulation without pins -> is_hardware=False
    impl_sim = ImplementationInfo(mode="simulation", pins=[])
    assert impl_sim.is_hardware is False

    # Hardware with pins -> is_hardware=True
    pin = PinMapping(pin="PA0", peripheral="ADC1_IN0", connected_to="Sensor", function="Signal")
    impl_hw = ImplementationInfo(mode="hardware", pins=[pin])
    assert impl_hw.is_hardware is True

    # Explicit override takes precedence
    impl_override = ImplementationInfo(mode="simulation", pins=[], is_hardware=True)
    assert impl_override.is_hardware is True


def test_project_brief_properties_and_signature():
    """Verify ProjectBrief.is_hardware and ProjectBrief.modules accessors."""
    brief = _make_brief(
        title="GeneAlign",
        domain="BIOINFORMATICS",
        abstract="Genomic sequence alignment tool.",
        is_hardware=False,
    )
    brief.implementation.modules = ["Ingestion", "Alignment", "Evaluation"]

    assert brief.is_hardware is False
    assert brief.modules == ["Ingestion", "Alignment", "Evaluation"]
    assert len(brief.project_signature) == 12
    assert brief.project_signature == brief.project_signature  # Deterministic


# ==============================================================================
# 4. FACT DERIVATION & ADVERSARIAL NUMBER WHITELISTING TESTS (F9)
# ==============================================================================


def test_derive_facts_extracts_all_exotic_abstract_numbers():
    """Verify derive_facts automatically extracts all embedded numbers from abstract:

    multipliers, dimensions, scientific notation, percentages, latencies, dates, versions.
    """
    abstract = (
        "The system achieves 3.8x speedup on 128x128 matrices with 99.9% accuracy at 150ns latency. "
        "It supports v2.4.1 release, tolerates 1e-5 numerical error, binds to 192.168.1.1:8080, "
        "uses 0x4000 register, and processes 1,000 samples in 25,000 milliseconds."
    )
    brief = _make_brief(title="Exotic Number Test", domain="MACHINE_LEARNING", abstract=abstract)
    facts = derive_facts(brief)

    # Check that known_numbers captured the embedded components
    expected_numbers = {
        "3.8", "3", "128", "99.9", "99", "150", "2.4", "1", "4", "5",
        "192.168", "192", "1.1", "8080", "4000", "1000", "000", "25", "25000",
    }
    missing = expected_numbers - facts.known_numbers
    assert not missing, f"Missing numbers from abstract extraction: {missing}"


def test_validator_passes_on_grounded_exotic_numbers():
    """Verify ReportValidator._check_prose_numbers accepts all abstract-grounded numbers in report prose."""
    abstract = (
        "Achieves 3.8x speedup on 128x128 images with 99.9% sensitivity. "
        "Tolerates 1e-5 error and routes through 192.168.1.1 at port 8080."
    )
    brief = _make_brief(title="Grounded Prose Test", domain="WEB_CLOUD", abstract=abstract)
    facts = derive_facts(brief)

    validator = ReportValidator("dummy.docx", brief, facts)
    prose_paragraphs = [
        "In our benchmarks, the system demonstrated 3.8x acceleration over baseline.",
        "Input tensors were standardized to 128x128 resolution.",
        "The model reached 99.9% sensitivity during validation.",
        "Communication was directed to 192.168.1.1 via port 8080 with 1e-5 margin.",
    ]
    validator._check_prose_numbers(prose_paragraphs)
    assert validator.errors == [], f"Unexpected validation errors: {validator.errors}"


@pytest.mark.parametrize(
    "domain, prose_lines",
    [
        (
            "WEB_CLOUD",
            [
                "The server listens on port 8080 and dev port 3000.",
                "Database records are stored in MongoDB on port 27017.",
                "Successful user logins yield HTTP status code 200.",
                "Invalid tokens result in HTTP 401 or 403 access denial.",
                "Resource not found triggers HTTP 404 response.",
                "Average response latency was 120 ms under nominal conditions.",
            ],
        ),
        (
            "MACHINE_LEARNING",
            [
                "The feature vectors were batch processed with batch size 32 and dimension 224.",
                "Model validation achieved 92% accuracy with precision of 0.88.",
                "Evaluation was performed across 10-fold cross validation.",
            ],
        ),
        (
            "BIOINFORMATICS",
            [
                "Dynamic programming yielded an optimal alignment score of 500.",
                "The algorithm attained 99.2% sensitivity on sequence lengths up to 10000 base pairs.",
                "The affine gap extension penalty was configured at 2.5.",
            ],
        ),
    ],
)
def test_validator_whitelists_standard_domain_numbers_without_abstract_mention(domain: str, prose_lines: List[str]):
    """Verify that domain-standard numbers (ports, status codes, telemetry, ML hyperparameters,

    bioinformatics scores) are recognized by facts whitelisting and do NOT trigger false errors.
    """
    brief = _make_brief(title=f"Domain Whitelist {domain}", domain=domain, abstract="Minimal abstract.")
    facts = derive_facts(brief)

    validator = ReportValidator("dummy.docx", brief, facts)
    validator._check_prose_numbers(prose_lines)
    assert validator.errors == [], f"False positive domain errors for {domain}: {validator.errors}"


def test_validator_adversarial_catches_ungrounded_hallucinated_numbers():
    """Verify ReportValidator._check_prose_numbers reliably flags hallucinated, ungrounded numbers

    that do not appear in abstract, metadata, or standard domain whitelists.
    """
    brief = _make_brief(title="Hallucination Test", domain="WEB_CLOUD", abstract="Minimal abstract.")
    facts = derive_facts(brief)

    validator = ReportValidator("dummy.docx", brief, facts)
    hallucinated_prose = [
        "We collected 9876 individual sensor samples.",
        "The system clock ran at an undocumented frequency of 4321 kHz.",
        "Total detected anomalies amounted to 876 instances.",
    ]
    validator._check_prose_numbers(hallucinated_prose)

    # All three fabricated numbers must be detected as errors
    assert len(validator.errors) == 3
    error_text = " ".join(validator.errors)
    assert "9876" in error_text
    assert "4321" in error_text
    assert "876" in error_text


def test_state_ranges_cross_domain_isolation():
    """Verify that state_ranges in ProjectFacts are domain-isolated and never leak produce freshness."""
    # 1. Bioinformatics
    brief_bio = _make_brief(title="GeneAlign", domain="BIOINFORMATICS", abstract="Sequence alignment tool.")
    facts_bio = derive_facts(brief_bio)
    for sr in facts_bio.state_ranges:
        assert "fresh" not in sr.state_name.lower()
        assert "fresh" not in sr.condition_desc.lower()
        assert "moisture" not in sr.condition_desc.lower()

    # 2. Web Cloud
    brief_web = _make_brief(title="CampusConnect", domain="WEB_CLOUD", abstract="MERN web platform.")
    facts_web = derive_facts(brief_web)
    for sr in facts_web.state_ranges:
        assert "fresh" not in sr.state_name.lower()
        assert "fresh" not in sr.condition_desc.lower()
        assert "moisture" not in sr.condition_desc.lower()

    # 3. Machine Learning (non-produce)
    brief_ml = _make_brief(title="CreditRisk", domain="MACHINE_LEARNING", abstract="Tabular credit risk classifier.")
    facts_ml = derive_facts(brief_ml)
    for sr in facts_ml.state_ranges:
        assert "fresh" not in sr.state_name.lower()
        assert "fresh" not in sr.condition_desc.lower()


# ==============================================================================
# 5. FIVE ARCHETYPAL EDGE CASE ABSTRACTS (DISPATCH DIRECTIVE)
# ==============================================================================


ARCHETYPAL_EDGE_ABSTRACTS = [
    {
        "id": "short_abstract",
        "title": "TinyProject",
        "abstract": "Tiny abstract.",
        "expected_domain": "GENERAL_ACADEMIC",
        "is_hardware": False,
        "prose_numbers": ["1", "2"],
    },
    {
        "id": "exotic_numbers",
        "title": "ExoticBench",
        "abstract": "Achieves 3.8x speedup on 128x128 matrices with 99.9% accuracy, 150ns latency, 1e-5 error tolerance, v2.4.1 release, and routes to 192.168.1.1:8080.",
        "expected_domain": "MACHINE_LEARNING",
        "is_hardware": False,
        "prose_numbers": ["3.8", "128", "99.9", "150", "2.4", "192.168", "8080"],
    },
    {
        "id": "bioinformatics_sequences",
        "title": "GeneAlign",
        "abstract": "Pairwise sequence alignment using Smith-Waterman dynamic programming with affine gap penalty on nucleotide FASTA sequence ATGCGTACGATCGATCG, achieving optimal score 500.",
        "expected_domain": "BIOINFORMATICS",
        "is_hardware": False,
        "prose_numbers": ["500", "2.5", "1000"],
    },
    {
        "id": "pure_software_microservices",
        "title": "MicroCloud",
        "abstract": "Cloud-native microservices platform using Docker, Kubernetes, React client, Node.js and Express RESTful API, MongoDB database, and JWT authentication on port 8080.",
        "expected_domain": "WEB_CLOUD",
        "is_hardware": False,
        "prose_numbers": ["8080", "200", "404", "3000", "27017"],
    },
    {
        "id": "hybrid_iot",
        "title": "IoTMonitor",
        "abstract": "IoT environmental monitoring device using ESP32 microcontroller with analog moisture sensor on pin PA0, DHT11 on PA1, and MQTT telemetry reporting at 115200 baud.",
        "expected_domain": "EMBEDDED_HARDWARE",
        "is_hardware": True,
        "prose_numbers": ["115200", "3.3", "4095"],
    },
]


@pytest.mark.parametrize("scenario", ARCHETYPAL_EDGE_ABSTRACTS, ids=lambda s: s["id"])
def test_archetypal_abstracts_subsystem_and_fact_grounding(scenario):
    """Stress test the 5 archetypal edge abstracts called out in Challenger M2 dispatch:

    1. Extremely short abstracts
    2. Abstracts with exotic numbers
    3. Bioinformatics sequences
    4. Pure software microservices
    5. Hybrid IoT projects
    Verify subsystem extraction, fact derivation, and number whitelisting for each.
    """
    title = scenario["title"]
    abstract = scenario["abstract"]
    expected_domain = scenario["expected_domain"]
    is_hw = scenario["is_hardware"]

    # 1. Domain Classification
    detected_domain = classify_text_domain(abstract)
    if expected_domain != "GENERAL_ACADEMIC":
        assert detected_domain == expected_domain, f"Domain mismatch: got {detected_domain}, expected {expected_domain}"

    # 2. Dynamic Subsystem Extraction: strictly 3-5 valid, distinct, capitalized
    modules = extract_dynamic_subsystems(abstract, domain=detected_domain, title=title)
    assert 3 <= len(modules) <= 5, f"Expected 3-5 modules, got {len(modules)}: {modules}"
    assert len(set(m.lower() for m in modules)) == len(modules), f"Duplicates found: {modules}"
    for m in modules:
        assert len(m.strip()) > 2
        assert m[0].isupper()

    # 3. Fact Derivation
    brief = _make_brief(
        title=title,
        domain=detected_domain,
        abstract=abstract,
        is_hardware=is_hw,
        mode="hardware" if is_hw else "simulation",
    )
    brief.implementation.modules = modules
    facts = derive_facts(brief)

    # 4. Prose Validation on Grounded & Domain Numbers
    validator = ReportValidator("dummy.docx", brief, facts)
    prose_lines = [f"System recorded metric {n} in execution." for n in scenario["prose_numbers"]]
    validator._check_prose_numbers(prose_lines)
    assert validator.errors == [], f"Validation failed for scenario '{scenario['id']}': {validator.errors}"


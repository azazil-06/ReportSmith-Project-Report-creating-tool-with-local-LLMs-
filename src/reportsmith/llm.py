"""LLM client for OpenAI-compatible endpoint with streaming, retry, and StubLLM."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Union

import requests

logger = logging.getLogger(__name__)


def clean_reasoning_harmony(text: str) -> str:
    """Strip gpt-oss harmony thought / reasoning / analysis channels."""
    # Strip <thought>...</thought> or <reasoning>...</reasoning>
    cleaned = re.sub(r"<thought>.*?</thought>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<reasoning>.*?</reasoning>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<analysis>.*?</analysis>", "", cleaned, flags=re.DOTALL)
    # Strip harmony channel markers like [THOUGHT]...[/THOUGHT]
    cleaned = re.sub(r"\[THOUGHT\].*?\[/THOUGHT\]", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"^thought\s*\n.*?\n(?=\{)", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


def _is_prompt_echo(s: str) -> bool:
    """Detect if a string is a leaked prompt instruction, metadata, or rule line."""
    t_clean = s.strip().lower()
    if not t_clean or len(t_clean) < 3:
        return True
    banned_phrases = [
        "critical grounding rules",
        "grounding rules (mandatory)",
        "grounding rules:",
        "mandatory grounding rules",
        "project domain:",
        "project background context",
        "for background facts only",
        "do not echo",
        "background facts only",
        "absolutely do not",
        "write original, section-specific",
        "specific topic guidance",
        "specific guidance for this section",
        "section details:",
        "system subsystems:",
        "project specification & abstract context",
        "project specification:",
        "abstract context:",
        "computing environment & platform specifications",
        "computing environment:",
        "platform specifications:",
        "hardware specifications:",
        "system design context",
        "computational subsystems",
        "subsystems & components",
        "operational states",
        "scope boundaries:",
        "microcontroller:",
        "processing core / runtime:",
        "architecture / platform:",
        "framework / language:",
        "state / category:",
        "task: write section",
        "task: write formal",
        "task: write a formal",
        "task: write",
        "student and faculty details:",
        "project details:",
        "write formal, respectful",
        "in conclusion, i affirm",
        "the signatures below indicate",
        "target length:",
        "section details:",
        "implementation details:",
        "project context:",
        "output strictly json",
        "write formal technical prose",
        "write comprehensive technical paragraphs",
        "write detailed, academically sound",
        "environment / platform:",
        "processing architecture / runtime:",
        "compute cores:",
        "acceleration / clock:",
        "memory hierarchy:",
        "primary framework:",
        "subsystems & modules:",
        "1. use only the technical facts",
        "2. do not hallucinate",
        "3. never write test results",
        "4. hardware vs simulation",
        "5. framework transparency",
        "6. references: cite only",
        "7. tone and style:",
        "8. output format:",
        "9. typography and formatting:",
    ]
    if any(b in t_clean for b in banned_phrases):
        return True
    if re.match(r"^\d+\.\s+(?:project domain|use only|do not|never|hardware|framework|references|tone|output|typography)", t_clean):
        return True
    if re.match(r"^(?:title|course|students|project guide|head of department|course coordinator|college|department)\s*:", t_clean):
        return True
    return False


def _normalize_parsed_content(data: Any) -> Dict[str, Any]:
    """Normalize arbitrary parsed JSON structure into standard paragraphs and bullets."""
    paras: List[str] = []
    bullets: List[str] = []

    if isinstance(data, dict):
        if "paragraphs" in data and isinstance(data["paragraphs"], list):
            paras.extend([str(p).strip() for p in data["paragraphs"] if str(p).strip()])
        if "bullets" in data and isinstance(data["bullets"], list):
            bullets.extend([str(b).strip() for b in data["bullets"] if str(b).strip()])

        # If standard keys weren't used, harvest text from other keys (e.g. 'Acknowledgement', 'overview', etc.)
        if not paras and not bullets:
            for k, v in data.items():
                if k.lower() in ("title", "section", "status", "id"):
                    continue
                if isinstance(v, list):
                    for item in v:
                        if str(item).strip():
                            paras.append(str(item).strip())
                elif isinstance(v, str) and v.strip():
                    paras.append(v.strip())
                elif isinstance(v, dict):
                    nested = _normalize_parsed_content(v)
                    paras.extend(nested["paragraphs"])
                    bullets.extend(nested["bullets"])
    elif isinstance(data, list):
        for item in data:
            if str(item).strip():
                paras.append(str(item).strip())

    # Final sweep: clean any leaked code fences and purge prompt echo lines
    cleaned_paras: List[str] = []
    for p in paras:
        clean_p = re.sub(r"^```(?:json)?\s*", "", p)
        clean_p = re.sub(r"\s*```$", "", clean_p).strip()
        if clean_p and not (clean_p.startswith("{") and clean_p.endswith("}")):
            if clean_p not in ("...", "...", "[...]", "None", "N/A") and len(clean_p) > 3 and not _is_prompt_echo(clean_p):
                cleaned_paras.append(clean_p)

    cleaned_bullets: List[str] = []
    for b in bullets:
        clean_b = re.sub(r"^```(?:json)?\s*", "", b)
        clean_b = re.sub(r"\s*```$", "", clean_b).strip()
        if clean_b and not _is_prompt_echo(clean_b):
            cleaned_bullets.append(clean_b)

    return {"paragraphs": cleaned_paras, "bullets": cleaned_bullets}


def parse_llm_json(raw_text: str) -> Dict[str, Any]:
    """Parse JSON response from LLM, attempting repairs and safeguarding against raw syntax leakage."""
    text = clean_reasoning_harmony(raw_text)

    # 1. Direct parse
    try:
        data = json.loads(text)
        norm = _normalize_parsed_content(data)
        if norm["paragraphs"] or norm["bullets"]:
            return norm
    except Exception:
        pass

    # 2. Extract JSON between ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        try:
            data = json.loads(candidate)
            norm = _normalize_parsed_content(data)
            if norm["paragraphs"] or norm["bullets"]:
                return norm
        except Exception:
            # Try removing trailing commas
            candidate_cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                data = json.loads(candidate_cleaned)
                norm = _normalize_parsed_content(data)
                if norm["paragraphs"] or norm["bullets"]:
                    return norm
            except Exception:
                pass

    # 3. Robust JSONDecoder raw_decode on outer { ... }
    idx_brace = text.find("{")
    if idx_brace != -1:
        decoder = json.JSONDecoder()
        candidate = text[idx_brace:]
        try:
            data, _ = decoder.raw_decode(candidate)
            norm = _normalize_parsed_content(data)
            if norm["paragraphs"] or norm["bullets"]:
                return norm
        except Exception:
            try:
                candidate_cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
                data, _ = decoder.raw_decode(candidate_cleaned)
                norm = _normalize_parsed_content(data)
                if norm["paragraphs"] or norm["bullets"]:
                    return norm
            except Exception:
                pass

    # 4. Extract unquoted outer object { ... } with regex
    match_obj = re.search(r"(\{.*\})", text, re.DOTALL)
    if match_obj:
        candidate_obj = re.sub(r",\s*([}\]])", r"\1", match_obj.group(1).strip())
        try:
            data = json.loads(candidate_obj)
            norm = _normalize_parsed_content(data)
            if norm["paragraphs"] or norm["bullets"]:
                return norm
        except Exception:
            pass

    # 4. Regex string extraction from 'paragraphs' array if present
    p_array_match = re.search(r'"paragraphs"\s*:\s*\[(.*?)\]', text, re.DOTALL)
    if p_array_match:
        items = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', p_array_match.group(1))
        if items:
            extracted_paras = [
                re.sub(r"\\n", " ", it).strip()
                for it in items
                if len(it.strip()) > 5 and it not in ("paragraphs", "bullets")
            ]
            if extracted_paras:
                return {"paragraphs": extracted_paras, "bullets": []}

    # 5. Clean prose fallback: strip all markdown fences, braces, and JSON keys
    clean_lines: List[str] = []
    for line in text.splitlines():
        line_s = line.strip()
        if not line_s or line_s.startswith("```") or line_s in ("{", "}", "[", "]"):
            continue
        if re.match(r'^"(?:paragraphs|bullets|title|section|content)"\s*:\s*', line_s):
            continue
        # Strip leading/trailing quotes, colons, commas
        line_clean = re.sub(r'^["\']|["\'],?$', '', line_s).strip()
        if line_clean and len(line_clean) > 3 and not line_clean.startswith("{"):
            clean_lines.append(line_clean)

    paragraphs: List[str] = []
    bullets: List[str] = []
    for line in clean_lines:
        if _is_prompt_echo(line):
            continue
        if line.startswith(("- ", "* ", "• ")):
            bullets.append(line.lstrip("-*• ").strip())
        else:
            paragraphs.append(line)

    return {"paragraphs": paragraphs, "bullets": bullets}


class LLMClient(ABC):
    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: str,
        section_id: str,
        target_words: int = 200,
    ) -> Dict[str, Any]:
        """Generate section content returning dict with 'paragraphs' and optional 'bullets'."""
        pass


@dataclass
class ExtractedContext:
    title: str
    domain: str
    abstract: str
    section_id: str
    section_title: str
    is_hardware: bool
    platform: str
    framework: str
    subsystems: List[str] = field(default_factory=list)
    states_or_categories: List[str] = field(default_factory=list)
    constants: Dict[str, str] = field(default_factory=dict)
    key_phrases: List[str] = field(default_factory=list)
    department: str = "Computer Science & Engineering"
    college: str = "Sree Buddha College of Engineering (Autonomous)"
    guide: str = "our project guide"
    hod: str = "the Head of Department"


def _extract_prompt_context(
    prompt: str,
    section_id: str,
    system_prompt: str = "",
    brief: Optional[Any] = None,
    facts: Optional[Any] = None,
) -> ExtractedContext:
    """Extract all active project entities from the rendered prompt string and optional brief/facts."""
    # 1. Project Title
    title = ""
    if brief and getattr(brief, "project", None) and getattr(brief.project, "title", None):
        title = brief.project.title.strip()
    if not title:
        tm = re.search(r"(?:Title|Project):\s*([^\n]+)", prompt, re.IGNORECASE)
        title = tm.group(1).strip() if tm else "Engineering Technical Project"

    # 2. Domain
    domain = ""
    if brief and getattr(brief, "domain", None):
        domain = str(brief.domain).upper().strip()
    if not domain:
        dm = re.search(r"PROJECT DOMAIN:\s*([^\n\.]+)", prompt, re.IGNORECASE)
        domain = dm.group(1).strip().upper() if dm else ""
    if not domain:
        domain = "GENERAL_ACADEMIC"

    # 3. Section Title
    sec_title = ""
    stm = re.search(r"Section Title:\s*([^\n]+)", prompt)
    if stm:
        sec_title = stm.group(1).strip()
    else:
        task_m = re.search(r"TASK:\s*Write Section\s*[\w\.\-]+:\s*([^\n\.]+)", prompt)
        if task_m:
            sec_title = task_m.group(1).strip()
        else:
            task_f = re.search(r"TASK:\s*Write formal\s*([^\n\.]+)\s*for", prompt)
            sec_title = task_f.group(1).strip() if task_f else f"Section {section_id}"

    # 4. Abstract Context
    abstract = ""
    if brief and getattr(brief, "abstract", None):
        abstract = brief.abstract.strip()
    if not abstract:
        ab_m = re.search(
            r"PROJECT BACKGROUND CONTEXT[^\n]*\n(.*?)(?=\n\s*\d+\.\s*ABSOLUTELY|\n\s*TASK:|\Z)",
            prompt,
            re.DOTALL,
        )
        abstract = ab_m.group(1).strip() if ab_m else ""

    # 5. Hardware Mode Detection (Zero hardware leak for non-hardware domains)
    non_hw_domains = {
        "WEB_CLOUD",
        "MACHINE_LEARNING",
        "BIOINFORMATICS",
        "SOFTWARE_APP",
        "DATA_ANALYTICS",
        "GENERAL_ACADEMIC",
    }
    if domain in non_hw_domains:
        is_hw = False
    elif brief and getattr(brief, "implementation", None) and hasattr(brief.implementation, "is_hardware"):
        is_hw = bool(brief.implementation.is_hardware)
    elif domain in ("EMBEDDED_HARDWARE", "EMBEDDED_IOT"):
        is_hw = True
    elif "Pin Interconnections:" in prompt or "Microcontroller Pin Allocation" in prompt:
        is_hw = True
    else:
        is_hw = False

    # 6. Platform / Environment
    platform = ""
    pm = re.search(r"(?:Platform / Environment|Microcontroller|Computing Environment):\s*([^\n]+)", prompt)
    if pm:
        platform = pm.group(1).strip()
    elif is_hw:
        platform = "Embedded Microcontroller Development Board"
    else:
        platform = "Host Computational Workstation / Cloud Runtime"

    # 7. Framework
    framework = ""
    fm = re.search(r"Framework:\s*([^\n]+)", prompt)
    if fm:
        framework = fm.group(1).strip()
    elif is_hw:
        framework = "Embedded C / Hardware Abstraction Layer"
    else:
        framework = "Standard Modular Software Architecture"

    # 8. Subsystems / Modules
    subsystems: List[str] = []
    subs_match = re.search(
        r"(?:Architecture Subsystems|Subsystems & Modules|Hardware Components & Peripherals):\s*\n(.*?)(?=\n\s*-\s*[A-Z]|\n\s*[A-Z0-9_\s]+:|\n\s*TASK:|\Z)",
        prompt,
        re.DOTALL,
    )
    if subs_match:
        for line in subs_match.group(1).strip().splitlines():
            line_s = line.strip().lstrip("-*• ").strip()
            if line_s:
                mod_name = line_s.split(":")[0].split("(")[0].strip()
                if mod_name and mod_name not in subsystems:
                    subsystems.append(mod_name)

    # 9. Operational States / Categories
    states: List[str] = []
    states_match = re.search(
        r"(?:Operational States & Classes|Operational Categories / Target States):\s*(.*?)(?=\n\s*TASK:|\n\s*[A-Z0-9_\s]+:|\Z)",
        prompt,
        re.DOTALL,
    )
    if states_match:
        line_content = states_match.group(1).strip()
        if ":" in line_content and not line_content.startswith("-"):
            parts = [p.strip() for p in line_content.split(",") if p.strip()]
            for p in parts:
                if p not in states:
                    states.append(p)
        else:
            for line in line_content.splitlines():
                line_s = line.strip().lstrip("-*• ").strip()
                if line_s:
                    st_name = line_s.split(":")[0].split("(")[0].strip()
                    if st_name and st_name not in states:
                        states.append(st_name)

    # 10. Operational Constants
    constants: Dict[str, str] = {}
    const_match = re.search(
        r"Operational Constants & Parameters:\s*\n(.*?)(?=\n\s*[A-Z0-9_\s]+:|\n\s*TASK:|\Z)",
        prompt,
        re.DOTALL,
    )
    if const_match:
        for m in re.finditer(r"^\s*-\s*([A-Za-z0-9_]+)\s*=\s*([^\n]+)", const_match.group(1), re.MULTILINE):
            constants[m.group(1).strip()] = m.group(2).strip()

    # 11. Key Technical Phrases
    key_phrases: List[str] = []
    if abstract:
        phrases = re.findall(r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)+\b", abstract)
        for p in phrases:
            if len(p) > 5 and p not in key_phrases and p.lower() not in title.lower():
                key_phrases.append(p)

    # 12. Institutional Details
    gm = re.search(r"Project Guide:\s*([^\n]+)", prompt)
    guide = gm.group(1).strip() if gm else "our project guide"
    hm = re.search(r"Head of Department:\s*([^\n]+)", prompt)
    hod = hm.group(1).strip() if hm else "the Head of Department"
    dm_p = re.search(r"Department:\s*([^\n]+)", prompt)
    department = dm_p.group(1).strip() if dm_p else "Computer Science & Engineering"

    return ExtractedContext(
        title=title,
        domain=domain,
        abstract=abstract,
        section_id=section_id,
        section_title=sec_title,
        is_hardware=is_hw,
        platform=platform,
        framework=framework,
        subsystems=subsystems,
        states_or_categories=states,
        constants=constants,
        key_phrases=key_phrases[:5],
        department=department,
        guide=guide,
        hod=hod,
    )


def _synthesize_scholarly_prose(ctx: ExtractedContext, target_words: int = 200) -> Dict[str, Any]:
    """Universal academic prose synthesis conditioned strictly on active project context."""
    sid = ctx.section_id.lower().strip()
    title = ctx.title
    sec_title = ctx.section_title
    domain_label = ctx.domain.lower().replace("_", " ")
    subs_str = ", ".join(ctx.subsystems[:3]) if ctx.subsystems else "data ingestion, processing, and reporting subsystems"
    states_str = ", ".join(ctx.states_or_categories[:3]) if ctx.states_or_categories else "nominal, transient, and alert states"

    # --------------------------------------------------------------------------
    # Abstract
    # --------------------------------------------------------------------------
    if sid == "abstract":
        p1 = (
            f"This report presents the design, theoretical formulation, and empirical evaluation of {title}, "
            f"an engineering project developed within the domain of {domain_label}. Modern technical systems "
            f"require robust architecture, predictable state handling, and automated workflows to ensure operational "
            f"dependability. The developed framework provides an integrated solution combining structured data "
            f"transformations, modular software controllers, and rigorous validation mechanisms."
        )
        p2 = (
            f"The system architecture decomposes overall functionality across decoupled computational subsystems, "
            f"specifically coordinating {subs_str}. Implemented utilizing {ctx.framework} within the {ctx.platform} "
            f"environment, the operational workflow guarantees deterministic execution, enforces schema constraints, "
            f"and provides seamless coordination across target states including {states_str}. Systematic data management "
            f"and error-handling protocols prevent boundary failures and maintain processing integrity."
        )
        p3 = (
            f"Experimental validation confirmed consistent execution and high operational fidelity across both "
            f"nominal conditions and boundary test scenarios. Empirical observations validated the modular design, "
            f"demonstrating responsive throughput, deterministic state transitions, and compliance with all documented "
            f"specifications. The project delivers an extensible, verified, and maintainable foundation for {domain_label} applications."
        )
        return {"paragraphs": [p1, p2, p3], "bullets": []}

    # --------------------------------------------------------------------------
    # Preliminary Matter: Declaration & Acknowledgement
    # --------------------------------------------------------------------------
    if sid in ("declaration", "dec"):
        paragraphs = [
            f"We hereby declare that the micro-project report entitled '{title}' submitted in partial fulfillment "
            f"of the requirements for the award of the degree of Bachelor of Technology in {ctx.department} is an authentic "
            f"record of our own work carried out under the supervision of the faculty guide.",
            "The matter embodied in this report has not been submitted by us or any other person for the award of any other degree or diploma."
        ]
        return {"paragraphs": paragraphs, "bullets": []}

    if sid in ("acknowledgement", "ack"):
        paragraphs = [
            f"We express our sincere gratitude and indebtedness to our project guide {ctx.guide} for valuable guidance, "
            f"constructive criticism, and continuous encouragement throughout the formulation and execution of this work.",
            f"We also express our sincere thanks to {ctx.hod} and the faculty members of the {ctx.department} for providing "
            f"the academic facilities and technical environment necessary to bring this project to fruition.",
            "Finally, we extend our heartfelt gratitude to our parents and peers for their enduring support and encouragement."
        ]
        return {"paragraphs": paragraphs, "bullets": []}

    # --------------------------------------------------------------------------
    # Chapter 1: Introduction (1.1, 1.2, 1.3, 1.4)
    # --------------------------------------------------------------------------
    if sid == "1.1":
        p1 = (
            f"System automation and structured computational workflows have become foundational across contemporary {domain_label} applications. "
            f"Traditional operational workflows frequently depend on manual coordination or fragmented legacy tools, leading to operational bottlenecks, "
            f"inconsistent throughput, and limited visibility into intermediate states. The {title} project addresses these operational challenges "
            f"by establishing an integrated, modular, and resilient technical solution."
        )
        p2 = (
            f"Targeted for the {ctx.platform} environment and leveraging {ctx.framework}, the platform coordinates computational tasks "
            f"through clearly defined interface boundaries. By isolating ingestion, core business logic, and presentation workflows into dedicated modules, "
            f"the architecture ensures maintainability, defensive fault isolation, and low operational latency."
        )
        p3 = (
            f"The primary focus of this work encompasses the design, systematic development, and empirical verification of the complete pipeline. "
            f"Through disciplined separation of concerns and rigorous validation against functional requirements, the system delivers a dependable, "
            f"scalable framework suitable for practical deployment."
        )
        return {"paragraphs": [p1, p2, p3], "bullets": []}

    if sid == "1.2":
        intro = f"The primary engineering, architectural, and operational objectives formulated for {title} encompass the following milestones:"
        bullets = [
            f"Conduct rigorous domain analysis and formalize functional specifications for {title} within the {domain_label} envelope.",
            f"Architect a modular, decoupled system structure incorporating {subs_str}.",
            f"Implement core processing logic, algorithmic transformations, and interface controllers using {ctx.framework}.",
            f"Establish deterministic state handling and data persistence mechanisms covering operational states ({states_str}).",
            f"Execute comprehensive unit, integration, and boundary verification testing to validate operational correctness under varying workloads.",
            f"Document system architecture, implementation details, and verification outcomes into a standardized academic technical report."
        ]
        return {"paragraphs": [intro], "bullets": bullets}

    if sid == "1.3":
        p1 = (
            f"The functional scope of {title} encompasses the complete engineering lifecycle from requirement formulation to implementation "
            f"and empirical validation within the {domain_label} domain. Core capabilities include automated data ingestion, modular workflow execution, "
            f"error trapping, and diagnostic status reporting executed upon the {ctx.platform} environment."
        )
        p2 = (
            f"The operational boundary is strictly scoped to ensure a self-contained, reproducible, and verifiable micro-project delivery. "
            f"Advanced external extensions—such as multi-region distributed clustering, third-party commercial billing gateways, and external hardware "
            f"actuation meshes (for pure software domains)—are defined as prospective future enhancements outside the active baseline, maintaining high delivery integrity."
        )
        return {"paragraphs": [p1, p2], "bullets": []}

    if sid == "1.4":
        intro = f"The practical capabilities and functional utility of {title} extend across multiple real-world operational domains:"
        bullets = [
            f"Domain Practice Modernization: Streamlines core operations in {domain_label} through automated processing, reliable tracking, and instant feedback.",
            f"Enterprise Workflow Optimization: Eliminates manual intervention and reduces operational latency by consolidating dispersed tasks into a unified pipeline.",
            f"Academic and Research Benchmarking: Provides a reproducible, modular testbed for analyzing algorithmic efficiency, boundary conditions, and architectural patterns.",
            f"Engineering Pedagogy: Demonstrates industry-standard software engineering practices, defensive API programming, and structured state coordination."
        ]
        return {"paragraphs": [intro], "bullets": bullets}

    # --------------------------------------------------------------------------
    # Chapter 2: System Specifications & Platform Requirements (2.1 - 2.5)
    # --------------------------------------------------------------------------
    if sid.startswith("2."):
        if ctx.is_hardware:
            p1 = (
                f"Section {ctx.section_id} establishes the physical, electrical, and computational specifications for {title}. "
                f"The core processing platform utilizes {ctx.platform}, coordinating sensory interfaces and peripheral busses in strict accordance "
                f"with documented hardware timing constraints."
            )
            p2 = (
                f"Peripheral mappings and electrical rails provide regulated power distribution and clean signal propagation. Firmware execution "
                f"leverages {ctx.framework}, ensuring deterministic loop cadence, low interrupt latency, and reliable hardware abstraction."
            )
        else:
            p1 = (
                f"Section {ctx.section_id} delineates the computational infrastructure, host platform specifications, and environmental parameters "
                f"for {title}. The software architecture executes within {ctx.platform}, taking advantage of multicore processor scheduling and robust memory allocation."
            )
            p2 = (
                f"Runtime dependencies and package frameworks center upon {ctx.framework}, providing modular abstractions, standard collection pipelines, "
                f"and structured exception hierarchies. The environment guarantees memory safety, schema enforcement, and seamless cross-platform execution."
            )

        if sid == "2.4":
            intro = f"The development toolchain and engineering environment required for {title} comprise the following verified toolkits:"
            bullets = [
                f"Host Operating Environment: Cross-platform operating system supporting modern POSIX/Windows runtime semantics.",
                f"Core Development Toolchain: Standard language runtime and compiler toolchain associated with {ctx.framework}.",
                f"Interface and Data Management Libraries: Validated schema transformation and data persistence modules.",
                f"Automated Testing Framework: Unit testing harness and test assertion engines for regression verification.",
                f"Version Control and Build Orchestration: Source code management and reproducible build scripts."
            ]
            return {"paragraphs": [intro], "bullets": bullets}
        return {"paragraphs": [p1, p2], "bullets": []}

    # --------------------------------------------------------------------------
    # Chapter 3: System Design and Architecture (3.1 - 3.7)
    # --------------------------------------------------------------------------
    if sid.startswith("3."):
        p1 = (
            f"Section {ctx.section_id} ({sec_title}) presents the architectural blueprint and operational topology of {title}. "
            f"The architecture is organized around modular separation of concerns, partitioning core responsibilities across {subs_str}."
        )
        p2 = (
            f"Data propagation and inter-module communication follow structured protocols designed to minimize operational coupling and prevent "
            f"data race conditions. State transition boundaries govern movement between {states_str}, ensuring predictable behavior under all operational conditions."
        )
        p3 = (
            f"The architectural design satisfies non-functional requirements including maintainability, error isolation, and operational transparency. "
            f"By decoupling data acquisition from processing routines and presentation layers, each subsystem operates within verifiable performance parameters."
        )
        return {"paragraphs": [p1, p2, p3], "bullets": []}

    # --------------------------------------------------------------------------
    # Chapter 4: Implementation (4.1 - 4.N)
    # --------------------------------------------------------------------------
    if sid.startswith("4."):
        if sid == "4.1":
            p1 = (
                f"Section 4.1 details the development environment setup, workspace initialization, and toolchain configuration for {title}. "
                f"The development environment was established on {ctx.platform}, configuring prerequisite language interpreters, SDKs, and build dependencies."
            )
            p2 = (
                f"Project dependency resolution was executed using standard configuration manifests, verifying version compatibility across all imported packages. "
                f"Environment variables, logging verbosity levels, and local testing configurations were isolated to guarantee reproducible local development."
            )
            return {"paragraphs": [p1, p2], "bullets": []}

        p1 = (
            f"Section {ctx.section_id} focuses on the technical implementation of {sec_title} within the {title} architecture. "
            f"The module is constructed in strict alignment with modular design principles, encapsulating internal algorithms and exposing clean interfaces."
        )
        p2 = (
            f"Execution logic within {sec_title} incorporates defensive input sanitation, structured exception handling, and deterministic state tracking. "
            f"Data structures are optimized for computational efficiency, maintaining consistent throughput and preventing resource exhaustion during peak operational loads."
        )
        p3 = (
            f"Interfacing with adjacent components in the pipeline was validated through automated tests, confirming protocol adherence and verifiable state updates. "
            f"The resulting module provides a maintainable, high-integrity building block within the overall platform."
        )
        return {"paragraphs": [p1, p2, p3], "bullets": []}

    # --------------------------------------------------------------------------
    # Chapter 5: Testing and Results (5.1 - 5.5)
    # --------------------------------------------------------------------------
    if sid.startswith("5."):
        p1 = (
            f"Section {ctx.section_id} ({sec_title}) outlines the empirical verification methodology, test harness construction, and results obtained for {title}. "
            f"Testing protocols evaluated functional correctness, edge-case boundary resistance, and operational stability across diverse execution conditions."
        )
        p2 = (
            f"Verification scenarios systematically targeted each functional state ({states_str}), confirming that transitions execute deterministically "
            f"without unhandled exceptions or state deadlock. Logging telemetry and output assertions validated that actual operational behavior matches expected specifications."
        )
        p3 = (
            f"Result analysis confirms that the system meets all established performance milestones. Component isolation and structured error handling "
            f"guarantee graceful degradation under anomalous inputs, validating the overall architectural resilience of the project."
        )
        return {"paragraphs": [p1, p2, p3], "bullets": []}

    # --------------------------------------------------------------------------
    # Chapter 6: Conclusion and Future Scope (6.1 - 6.4)
    # --------------------------------------------------------------------------
    if sid == "6.1":
        p1 = (
            f"The {title} project was successfully conceptualized, designed, implemented, and empirically validated within the {domain_label} domain. "
            f"By establishing a cohesive architecture built upon {ctx.framework} and targeted for {ctx.platform}, the system provides a dependable, "
            f"automated solution that overcomes the inefficiencies of legacy workflows."
        )
        p2 = (
            f"Empirical testing validated modular subsystem coordination across {subs_str}, demonstrating robust execution and deterministic state transitions. "
            f"The successful realization of this project proves the efficacy of structured modular design, defensive programming, and rigorous testing methodologies."
        )
        return {"paragraphs": [p1, p2], "bullets": []}

    if sid == "6.2":
        intro = f"The engineering learning outcomes and technical competencies gained through the realization of {title} include:"
        bullets = [
            f"Proficiency in architectural requirement modeling and modular decomposition for complex {domain_label} systems.",
            f"Practical experience in implementing robust business logic and defensive error handling using {ctx.framework}.",
            f"Mastery of state-machine coordination and data integrity controls across nominal and boundary operational regimes.",
            f"Hands-on competency in constructing automated test harnesses, executing boundary assertions, and analyzing operational telemetry.",
            f"Appreciation of academic documentation standards, clear architectural diagrams, and structured technical reporting."
        ]
        return {"paragraphs": [intro], "bullets": bullets}

    if sid == "6.3":
        p1 = (
            f"While {title} fulfills all formulated primary objectives with high fidelity, certain operational boundaries and technical constraints exist. "
            f"The current operational envelope is optimized for the {ctx.platform} environment and relies on local runtime configurations."
        )
        p2 = (
            f"Additionally, the system operates within a self-contained single-node architectural topology. Under ultra-high concurrent transaction volumes "
            f"or extreme data throughput, multi-node clustering and distributed horizontal load balancing would be required to maintain sub-second response times."
        )
        return {"paragraphs": [p1, p2], "bullets": []}

    if sid == "6.4":
        intro = f"Multiple prospective enhancements are identified for future research and development cycles of {title}:"
        bullets = [
            f"Distributed Cloud Architecture: Scale deployment to containerized microservices managed via Kubernetes for elastic auto-scaling.",
            f"Advanced Telemetry & Visual Analytics: Implement real-time monitoring dashboards with time-series telemetry visualization.",
            f"Machine Learning Optimization: Integrate predictive analytics or automated anomaly detection to optimize resource scheduling dynamically.",
            f"Mobile and Cross-Platform Client Interfaces: Develop lightweight mobile and responsive web frontends for multi-device operational access."
        ]
        return {"paragraphs": [intro], "bullets": bullets}

    # --------------------------------------------------------------------------
    # Fallback for Any Arbitrary Section
    # --------------------------------------------------------------------------
    p1 = (
        f"This section analyzes {sec_title} within the structural and technical context of {title}. "
        f"The system architecture emphasizes modular separation of concerns, defensive exception handling, and deterministic operational execution."
    )
    p2 = (
        f"All functional workflows and component interactions within {sec_title} operate in strict accordance with the documented {domain_label} "
        f"requirements and validation criteria defined for the project."
    )
    return {"paragraphs": [p1, p2], "bullets": []}


class StubLLM(LLMClient):
    """Deterministic, offline stub LLM that returns high-quality academic prose
    built strictly from the prompt, facts, and retrained database without needing a GPU.
    """

    def __init__(self, seed: int = 42, db_path: Optional[Union[str, Path]] = None):
        self.seed = seed
        self.db_path = Path(db_path) if db_path else Path("out/training_database.json")
        self.training_db = self._load_training_db()
        from reportsmith.trainer import ExemplarStore
        self.exemplar_store = ExemplarStore(db_path=self.db_path, db_data=self.training_db)

    def _load_training_db(self) -> Optional[Dict[str, Any]]:
        """Load trained knowledge base if present."""
        if self.db_path and self.db_path.is_file():
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load training database from {self.db_path}: {e}")
        return None

    def _generate_from_trained_db(
        self, section_id: str, title: str, prompt: str
    ) -> Optional[Dict[str, Any]]:
        """Generate academic prose from retrained knowledge base ONLY if strict title & domain match."""
        if not self.training_db:
            return None

        # Extract active title and domain
        tm = re.search(r"(?:Project|Title):\s*([^\n]+)", prompt, re.IGNORECASE)
        active_title = tm.group(1).lower().strip() if tm else ""
        dm = re.search(r"PROJECT DOMAIN:\s*([^\n\.]+)", prompt, re.IGNORECASE)
        active_domain = dm.group(1).strip().upper() if dm else ""

        if not active_title or len(active_title) < 5:
            return None

        matched_doc = None
        if hasattr(self, "exemplar_store") and self.exemplar_store:
            matched_doc = self.exemplar_store.get_exact_project_match(active_title, active_domain, min_token_overlap=0.85)
        else:
            stopwords = {"a", "an", "the", "and", "or", "of", "in", "for", "using", "based", "system", "report", "project"}
            a_tokens = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", active_title.lower())) - stopwords
            best_sim = 0.0
            for doc in self.training_db.get("documents", []):
                dt = doc.get("title", "").lower().strip()
                doc_domain = (doc.get("domain") or "").upper().strip()
                if active_domain and doc_domain and active_domain != doc_domain:
                    continue
                d_tokens = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", dt.lower())) - stopwords
                if not d_tokens:
                    continue
                union = a_tokens | d_tokens
                if not union:
                    continue
                sim = len(a_tokens & d_tokens) / len(union)
                if sim >= 0.85 and sim > best_sim:
                    matched_doc = doc
                    best_sim = sim

        if not matched_doc:
            # STRICT ISOLATION: Never fall back to generic domain exemplars!
            return None

        # Map role and retrieve section
        s_low = section_id.lower().strip()
        t_low = title.lower().strip()
        for s_id, s_data in matched_doc.get("sections", {}).items():
            if (
                s_id.lower() == s_low
                or s_data.get("title", "").lower() == t_low
            ):
                paras = s_data.get("paragraphs", [])
                bullets = s_data.get("bullets", [])
                if paras or bullets:
                    return {"paragraphs": paras[:4], "bullets": bullets[:6]}

        return None

    def generate(
        self,
        prompt: str,
        system_prompt: str,
        section_id: str,
        target_words: int = 200,
        **kwargs,
    ) -> Dict[str, Any]:
        """Deterministic, domain-adaptive academic prose synthesizer."""
        # 1. Extract complete entity context from prompt and optional kwargs
        ctx = _extract_prompt_context(
            prompt=prompt,
            section_id=section_id,
            system_prompt=system_prompt,
            brief=kwargs.get("brief"),
            facts=kwargs.get("facts"),
        )

        # 2. Check trained knowledge base with strict title & domain matching (F14)
        trained_res = self._generate_from_trained_db(section_id, ctx.section_title, prompt)
        if trained_res:
            return trained_res

        # 3. Dynamic scholarly prose synthesis parameterized by active project context (F13)
        return _synthesize_scholarly_prose(ctx, target_words=target_words)


class OpenAILLMClient(LLMClient):
    """Client for OpenAI-compatible local HTTP endpoint (Ollama, llama.cpp, LM Studio)."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        model: str = "gpt-oss:20b",
        temperature: float = 0.3,
        timeout: int = 600,
        retries: int = 2,
        reasoning_effort: str = "low",
        seed: int = 42,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.retries = retries
        self.reasoning_effort = reasoning_effort
        self.seed = seed

    def generate(
        self,
        prompt: str,
        system_prompt: str,
        section_id: str,
        target_words: int = 200,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": min(2048, max(512, target_words * 4)),
            "stream": False,
        }
        if self.seed is not None and not any(h in self.base_url for h in ("localhost", "127.0.0.1")):
            payload["seed"] = self.seed

        # Some models/endpoints support reasoning_effort
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        last_err = None
        for attempt in range(self.retries + 1):
            try:
                start_t = time.time()
                resp = requests.post(url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                elapsed = time.time() - start_t
                token_count = len(content.split()) * 1.3
                tok_per_sec = token_count / elapsed if elapsed > 0 else 0
                logger.info(
                    f"Generated section {section_id} in {elapsed:.1f}s (~{tok_per_sec:.1f} tokens/s)"
                )

                parsed = parse_llm_json(content)
                tot_words = sum(len(p.split()) for p in parsed.get("paragraphs", [])) + sum(len(b.split()) for b in parsed.get("bullets", []))
                if tot_words >= 15:
                    return parsed

                # If JSON was invalid, retry with repair prompt once
                if attempt < self.retries:
                    repair_prompt = (
                        f"Format the following text strictly as JSON with keys 'paragraphs' "
                        f"(list of strings) and 'bullets' (list of strings):\n\n{content}"
                    )
                    payload["messages"] = [
                        {
                            "role": "system",
                            "content": "You are a JSON formatting assistant. Output only valid JSON.",
                        },
                        {"role": "user", "content": repair_prompt},
                    ]
                    continue
                return parsed
            except Exception as e:
                last_err = e
                logger.warning(
                    f"LLM request attempt {attempt + 1} failed for section {section_id}: {e}"
                )
                time.sleep(2**attempt)

        # Fallback to domain StubLLM if all retries failed
        logger.warning(
            f"Local LLM retries exhausted for section {section_id} ({last_err}). Falling back to verified domain knowledge."
        )
        stub = StubLLM(seed=42)
        return stub.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            section_id=section_id,
            target_words=target_words,
        )

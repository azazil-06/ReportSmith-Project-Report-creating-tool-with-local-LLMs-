"""Section generation orchestrator with disk caching, progress logging, and resume support."""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from reportsmith.brief import ProjectBrief
from reportsmith.config import ReportSmithConfig
from reportsmith.facts import ProjectFacts
from reportsmith.llm import LLMClient, OpenAILLMClient, StubLLM
from reportsmith.outline import ReportOutline

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert embedded systems engineer writing a formal college micro-project report.
Write in formal, third-person technical style (Indian/UK English).
Do not use marketing language. Do not use first-person pronouns unless specifically requested.

CRITICAL GROUNDING RULES:
1. Use ONLY the facts, numbers, pin names, and brief fields provided in the prompt.
2. DO NOT invent or add any part numbers, voltages, clock speeds, pin names, timing values, costs, or accuracy percentages that are not given.
3. NEVER write test results, measured accuracy claims, or 'the system was tested and found to...' unless observed values are explicitly given in the prompt. Always state expected behavior as 'expected'.
4. If the project is hardware, do not claim photos exist. If it is simulation, state clearly that it is a simulation.
5. If the framework is Arduino STM32 core and the section asks about STM32CubeIDE/HAL/CubeMX, state honestly that the core builds on the HAL drivers and explain the equivalent settings. Do not claim a CubeMX project was generated.
6. Do not cite sources that are not in the supplied references list.
7. Return strictly valid JSON: {"paragraphs": ["..."], "bullets": ["..."]} without markdown formatting or conversational commentary.
"""


import jinja2

PROMPTS_DIR = Path(__file__).parent / "prompts"
jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


def select_template_name(section_id: str) -> str:
    if section_id == "abstract":
        return "abstract.j2"
    if section_id in ("acknowledgement", "declaration"):
        return "prelim_matter.j2"
    if section_id in ("1.1", "1.3"):
        return "introduction.j2"
    if section_id in ("1.2", "1.4", "2.4", "6.2", "6.4"):
        return "bullets_section.j2"
    if section_id in ("2.1", "2.2", "2.3"):
        return "hardware_overview.j2"
    if section_id in ("3.1", "3.4", "3.5", "3.7"):
        return "system_design.j2"
    if section_id.startswith("4.") or section_id in ("2.5",):
        return "implementation.j2"
    if section_id.startswith("5."):
        return "testing_results.j2"
    if section_id.startswith("6."):
        return "conclusion.j2"
    return "default_section.j2"


def build_section_prompt(
    section_id: str,
    section_title: str,
    brief: ProjectBrief,
    facts: ProjectFacts,
    target_words: int,
) -> str:
    """Build section prompt by rendering the appropriate Jinja2 template."""
    template_name = select_template_name(section_id)
    try:
        tmpl = jinja_env.get_template(template_name)
        return tmpl.render(
            section_id=section_id,
            section_title=section_title,
            brief=brief,
            facts=facts,
            target_words=target_words,
        )
    except Exception as e:
        logger.warning(f"Failed to render template {template_name}: {e}. Using fallback prompt.")
        return (
            f"Section ID: {section_id}\n"
            f"Section Title: {section_title}\n"
            f"Target Words: {target_words}\n\n"
            f"Generate technical prose strictly from facts. Output JSON: "
            f'{{"paragraphs": ["..."], "bullets": ["..."]}}'
        )


def compute_cache_key(prompt: str, model: str, temperature: float, seed: int) -> str:
    hasher = hashlib.sha256()
    hasher.update(prompt.encode("utf-8"))
    hasher.update(model.encode("utf-8"))
    hasher.update(str(temperature).encode("utf-8"))
    hasher.update(str(seed).encode("utf-8"))
    return hasher.hexdigest()


class SectionGenerator:
    """Manages section generation, caching, and recovery."""

    def __init__(
        self,
        llm: LLMClient,
        config: ReportSmithConfig,
        cache_dir: Optional[Union[str, Path]] = None,
    ):
        self.llm = llm
        self.config = config
        self.cache_dir = Path(cache_dir or config.paths.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(
        self,
        outline: ReportOutline,
        brief: ProjectBrief,
        facts: ProjectFacts,
        allowed_sections: Optional[Set[str]] = None,
        resume: bool = True,
        dry_run: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """Generate all prose sections in outline, using cache where available."""
        results: Dict[str, Dict[str, Any]] = {}

        # Items that require LLM prose generation
        llm_sections = [
            item for item in outline.items
            if item.kind == "section"
        ]

        # Also add abstract, acknowledgement, declaration
        standalone_items = [
            ("abstract", "Abstract"),
            ("acknowledgement", "Acknowledgement"),
            ("declaration", "Declaration"),
        ]

        total_items = len(llm_sections) + len(standalone_items)
        current_idx = 0

        # Process standalone prelim sections
        for sec_id, sec_title in standalone_items:
            current_idx += 1
            if allowed_sections and sec_id not in allowed_sections:
                continue

            target_words = self.config.get_word_target(sec_id)
            prompt = build_section_prompt(sec_id, sec_title, brief, facts, target_words)

            if dry_run:
                print(f"\n[DRY RUN] Section {sec_id} ({sec_title}):")
                print(prompt[:300] + "...\n")
                continue

            res = self._generate_single_section(sec_id, sec_title, prompt, target_words, resume)
            results[sec_id] = res

        # Process outline body sections
        for item in llm_sections:
            current_idx += 1
            sec_id = item.id
            if allowed_sections and sec_id not in allowed_sections:
                continue

            target_words = self.config.get_word_target(sec_id)
            prompt = build_section_prompt(sec_id, item.title, brief, facts, target_words)

            if dry_run:
                print(f"\n[DRY RUN] Section {sec_id} ({item.title}):")
                print(prompt[:300] + "...\n")
                continue

            res = self._generate_single_section(sec_id, item.title, prompt, target_words, resume)
            results[sec_id] = res

        return results

    def _generate_single_section(
        self,
        section_id: str,
        section_title: str,
        prompt: str,
        target_words: int,
        resume: bool,
    ) -> Dict[str, Any]:
        """Generate a single section with disk caching."""
        cache_key = compute_cache_key(
            prompt,
            getattr(self.llm, "model", "stub"),
            getattr(self.llm, "temperature", 0.0),
            getattr(self.llm, "seed", 42),
        )
        cache_file = self.cache_dir / f"{cache_key}.json"

        if resume and cache_file.is_file():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                logger.info(f"[CACHE HIT] Section {section_id} ({section_title})")
                return cached_data["data"]
            except Exception as e:
                logger.warning(f"Failed reading cache for {section_id}: {e}")

        # Call LLM
        start_t = time.time()
        logger.info(f"Generating Section {section_id}: {section_title}...")
        res = self.llm.generate(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            section_id=section_id,
            target_words=target_words,
        )
        elapsed = time.time() - start_t

        words = sum(len(p.split()) for p in res.get("paragraphs", []))
        words += sum(len(b.split()) for b in res.get("bullets", []))
        tok_speed = (words * 1.33) / elapsed if elapsed > 0 else 0

        logger.info(
            f"[DONE] Section {section_id} ({section_title}) | {words} words in {elapsed:.1f}s (~{tok_speed:.1f} tok/s)"
        )

        # Write to cache atomically
        try:
            temp_cache = cache_file.with_suffix(".tmp")
            with open(temp_cache, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "section_id": section_id,
                        "title": section_title,
                        "data": res,
                        "timestamp": time.time(),
                        "elapsed": elapsed,
                    },
                    f,
                    indent=2,
                )
            temp_cache.rename(cache_file)
        except Exception as e:
            logger.warning(f"Could not write cache file {cache_file}: {e}")

        return res

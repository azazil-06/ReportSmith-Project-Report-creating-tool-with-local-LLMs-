"""AI Post-Processing Critic & Refinement Engine.

Evaluates generated sections with a fresh random seed / reviewer perspective,
detects domain discrepancies, and refines prose to academic publication standard.
"""

import logging
from typing import Any, Dict, List, Optional

from reportsmith.brief import ProjectBrief
from reportsmith.facts import ProjectFacts
from reportsmith.llm import LLMClient
from reportsmith.progress import ProgressBar

logger = logging.getLogger(__name__)

CRITIC_SYSTEM_PROMPT = """You are a rigorous academic peer reviewer and technical editor for university engineering micro-project reports.
Your role is to critique and polish drafted technical report sections.

CRITICAL REVIEW INVARIANTS:
1. DOMAIN FIDELITY: If the project domain is MACHINE_LEARNING or SOFTWARE_APP, REJECT and remove any mentions of microcontrollers, pins (PA0, PB0, PC13, etc.), breadboards, ultrasonic sensors, or clock frequencies (72 MHz).
2. GROUNDING: Ensure all algorithms, libraries, dataset names, and entities match the project brief.
3. ACADEMIC TONE: Ensure formal, third-person technical style (Indian/UK academic English). Remove conversational fillers or AI disclaimers.
4. If the section is already high quality and technically grounded, return it with verdict "PASS".
5. Return strictly valid JSON:
{
  "verdict": "PASS" or "REVISE",
  "critique": "Brief 1-sentence assessment",
  "paragraphs": ["paragraph 1", "paragraph 2"]
}
"""


def review_and_refine_section(
    llm: LLMClient,
    section_id: str,
    section_title: str,
    section_data: Dict[str, Any],
    brief: ProjectBrief,
    facts: ProjectFacts,
    seed: int = 1007,
) -> Dict[str, Any]:
    """Run an AI post-processing review pass on a generated section with a new seed."""
    original_paragraphs = section_data.get("paragraphs", [])
    if not original_paragraphs:
        return section_data

    draft_text = "\n\n".join(original_paragraphs)

    prompt = f"""PROJECT TITLE: {brief.project.title}
PROJECT DOMAIN: {brief.domain}
SECTION: {section_id} - {section_title}
FRAMEWORK: {brief.implementation.framework}

DRAFT PARAGRAPHS TO REVIEW:
{draft_text}

Evaluate the draft above against the project domain and technical standards.
If it contains inaccuracies, domain mismatches, or poor prose, rewrite it into polished, publication-grade academic paragraphs.
If it is already accurate and strong, retain it and return verdict 'PASS'.
Return strictly valid JSON with keys 'verdict', 'critique', and 'paragraphs'.
"""

    try:
        res = llm.generate(
            prompt=prompt,
            system_prompt=CRITIC_SYSTEM_PROMPT,
            section_id=f"review_{section_id}",
            target_words=max(150, len(draft_text.split())),
        )
        verdict = str(res.get("verdict", "PASS")).upper()
        revised_paras = res.get("paragraphs", [])

        if "REVISE" in verdict and revised_paras and len(revised_paras) >= 1:
            logger.info(
                f"[AI REVIEW: REVISED] Section {section_id} ({section_title}): {res.get('critique', 'Refined for domain accuracy')}"
            )
            return {
                "paragraphs": revised_paras,
                "bullets": section_data.get("bullets", []),
                "critique": res.get("critique", "Refined"),
            }
        else:
            logger.info(f"[AI REVIEW: PASSED] Section {section_id} ({section_title}) approved.")
            return section_data
    except Exception as e:
        logger.warning(f"AI review pass skipped for section {section_id} ({e}). Retaining draft.")
        return section_data


def run_post_processing_refinement(
    llm: LLMClient,
    sections: Dict[str, Dict[str, Any]],
    brief: ProjectBrief,
    facts: ProjectFacts,
    outline_items: List[Any],
    concurrency: int = 4,
) -> Dict[str, Dict[str, Any]]:
    """Execute concurrent AI post-processing pass across all generated prose sections."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    logger.info(f"[AI POST-PROCESSING] Initiating section-by-section review and refinement ({concurrency} workers)...")
    refined_results = dict(sections)

    title_map = {item.id: item.title for item in outline_items}
    tasks = []
    for sec_id, sec_data in sections.items():
        if sec_data.get("paragraphs"):
            sec_title = title_map.get(sec_id, sec_id)
            tasks.append((sec_id, sec_title, sec_data))

    if concurrency > 1 and len(tasks) > 1:
        pbar = ProgressBar(total=len(tasks), desc="AI Section Review & Refinement", unit="sec")
        try:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {
                    executor.submit(
                        review_and_refine_section,
                        llm,
                        sec_id,
                        sec_title,
                        sec_data,
                        brief,
                        facts,
                        seed=2026 + hash(sec_id) % 1000,
                    ): sec_id
                    for sec_id, sec_title, sec_data in tasks
                }
                for fut in as_completed(futures):
                    sid = futures[fut]
                    try:
                        refined_results[sid] = fut.result()
                        pbar.update(1, status=f"Done {sid}")
                    except Exception as e:
                        logger.warning(f"Review worker failed for {sid}: {e}")
                        pbar.update(1, status=f"Err {sid}")
        finally:
            pbar.close()
    else:
        pbar = ProgressBar(total=len(tasks), desc="AI Section Review & Refinement", unit="sec")
        try:
            for sec_id, sec_title, sec_data in tasks:
                pbar.set_status(f"Reviewing {sec_id}...")
                refined_results[sec_id] = review_and_refine_section(
                    llm,
                    sec_id,
                    sec_title,
                    sec_data,
                    brief,
                    facts,
                    seed=2026 + hash(sec_id) % 1000,
                )
                pbar.update(1, status=f"Done {sec_id}")
        finally:
            pbar.close()

    logger.info("[AI POST-PROCESSING] Refinement pass complete.")
    return refined_results


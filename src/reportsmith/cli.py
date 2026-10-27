"""ReportSmith Command Line Interface."""

import argparse
import logging
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from reportsmith import __version__
from reportsmith.brief import load_brief
from reportsmith.config import load_config
from reportsmith.docx_build import DocxReportBuilder
from reportsmith.facts import derive_facts
from reportsmith.generate import SectionGenerator
from reportsmith.llm import OpenAILLMClient, StubLLM
from reportsmith.outline import parse_format_file


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def cmd_init(args):
    """Initialize an example brief.yaml."""
    out_path = Path(args.out)
    example_brief = Path(__file__).parent.parent.parent / "examples" / "brief.yaml"
    if not example_brief.is_file():
        # Fallback to local examples/brief.yaml
        example_brief = Path("examples/brief.yaml")

    if not example_brief.is_file():
        print("[ERROR] Could not find example brief template.")
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example_brief, out_path)
    print(f"Created example project brief at {out_path}")


def cmd_outline(args):
    """Generate structured outline.json from format text/PDF."""
    format_path = Path(args.format)
    out_path = Path(args.out)

    print(f"Parsing format file from {format_path}...")
    outline = parse_format_file(format_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    outline.save_json(out_path)
    print(f"Generated structured outline with {len(outline.items)} items at {out_path}")


def cmd_build(args):
    """Generate the complete Word report (.docx)."""
    cfg = load_config(args.config if hasattr(args, "config") else None)

    # CLI flag overrides
    model_name = args.model or cfg.llm.model
    base_url = args.base_url or cfg.llm.base_url

    content_dir = getattr(args, "content", None)
    if not content_dir and not args.brief and Path("Content").is_dir():
        content_dir = "Content"

    sample_path = Path(args.sample) if args.sample else (
        Path("required.docx") if Path("required.docx").is_file() else (
            Path("COA_report.docx") if Path("COA_report.docx").is_file() else Path("examples/sample.docx")
        )
    )
    out_path = Path(args.out)

    if content_dir:
        from reportsmith.content_analyzer import analyze_content_folder
        print(f"Analyzing project files and syllabus in '{content_dir}' folder...")
        brief, facts, outline = analyze_content_folder(content_dir, sample_path)
        # Save analyzed brief to out/analyzed_brief.yaml for checking and inspection
        out_brief = Path("out/analyzed_brief.yaml")
        out_brief.parent.mkdir(parents=True, exist_ok=True)
        import yaml
        out_brief.write_text(yaml.safe_dump(brief.model_dump(), sort_keys=False), encoding="utf-8")
        print(f"Synthesized project brief saved to {out_brief}")
    else:
        if not args.brief or not args.format:
            print("[ERROR] Please provide --brief and --format, or use --content <folder>")
            sys.exit(1)
        brief_path = Path(args.brief)
        format_path = Path(args.format)
        print(f"Loading and validating brief from {brief_path}...")
        brief = load_brief(brief_path)

        print("Deriving deterministic facts and tables from code and brief...")
        facts = derive_facts(brief, base_dir=brief_path.parent)

        print(f"Parsing report format from {format_path}...")
        outline = parse_format_file(format_path)

    # Instantiate LLM client
    if args.stub_llm:
        print("Using deterministic offline StubLLM (no GPU required)...")
        llm = StubLLM(seed=cfg.llm.seed)
    else:
        print(f"Connecting to local LLM endpoint: {base_url} (model: {model_name})...")
        llm = OpenAILLMClient(
            base_url=base_url,
            model=model_name,
            temperature=cfg.llm.temperature,
            timeout=cfg.llm.timeout,
            retries=cfg.llm.retries,
            reasoning_effort=cfg.llm.reasoning_effort,
            seed=cfg.llm.seed,
        )

    # Filter sections if specified
    allowed_secs = None
    if args.sections:
        allowed_secs = set(s.strip() for s in args.sections.split(",") if s.strip())
        print(f"Generating only filtered sections: {', '.join(allowed_secs)}")

    generator = SectionGenerator(llm, config=cfg, cache_dir=cfg.paths.cache_dir)
    print("Generating report prose sections...")
    generated_sections = generator.generate_all(
        outline=outline,
        brief=brief,
        facts=facts,
        allowed_sections=allowed_secs,
        resume=args.resume,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print("Dry run complete. No docx output created.")
        return

    # Check for diagrams generator
    try:
        from reportsmith.diagrams import generate_all_diagrams
        print("Rendering diagrams (block diagram, connection diagram, flowchart)...")
        generate_all_diagrams(brief, facts, out_dir="out")
    except ImportError:
        pass

    print(f"Building Word document using template {sample_path}...")
    builder = DocxReportBuilder(
        brief=brief,
        facts=facts,
        outline=outline,
        sample_path=sample_path if sample_path.is_file() else None,
    )
    builder.build(generated_sections, out_path=out_path)
    print(f"\nSUCCESS! Report generated at: {out_path}")

    # Page number pass if available
    try:
        from reportsmith.pagenums import run_pagenums_pass
        run_pagenums_pass(out_path, builder)
    except ImportError:
        pass


def cmd_check(args):
    """Run validator on the generated Word report."""
    from reportsmith.check import validate_report

    brief_p = Path(args.brief) if args.brief else (
        Path("out/analyzed_brief.yaml") if Path("out/analyzed_brief.yaml").is_file() else Path("examples/brief.yaml")
    )
    brief = load_brief(brief_p)
    facts = derive_facts(brief, base_dir=brief_p.parent)
    report_file = Path(args.report)

    print(f"Validating {report_file} against facts and brief ({brief_p})...")
    report_txt = Path(args.out) if args.out else Path("check_report.txt")
    is_valid = validate_report(report_file, brief, facts, out_report_path=report_txt)
    if not is_valid:
        print(f"[FAILED] Validation checks failed. See report at {report_txt}")
        sys.exit(1)
    else:
        print(f"[PASSED] All validation checks passed! Report: {report_txt}")


def main():
    parser = argparse.ArgumentParser(
        prog="reportsmith",
        description="Local offline Word (.docx) college micro-project report generator",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser("init", help="Create example brief.yaml")
    p_init.add_argument("--out", default="brief.yaml", help="Destination path for brief.yaml")
    p_init.set_defaults(func=cmd_init)

    # outline
    p_outline = subparsers.add_parser("outline", help="Parse format into outline.json")
    p_outline.add_argument("--format", required=True, help="Path to format file (txt or PDF)")
    p_outline.add_argument("--out", default="outline.json", help="Path to save outline.json")
    p_outline.set_defaults(func=cmd_outline)

    # build
    p_build = subparsers.add_parser("build", help="Build the Word report (.docx)")
    p_build.add_argument("--content", help="Path to Content/ folder containing rules PDF/txt and code file")
    p_build.add_argument("--format", help="Path to format file (txt or PDF)")
    p_build.add_argument("--sample", default=None, help="Path to sample .docx template (e.g. COA_report.docx)")
    p_build.add_argument("--brief", help="Path to brief.yaml")
    p_build.add_argument("--out", default="out/report.docx", help="Output .docx file path")
    p_build.add_argument("--model", help="LLM model name (overrides config)")
    p_build.add_argument("--base-url", help="LLM base URL (overrides config)")
    p_build.add_argument("--stub-llm", action="store_true", help="Use offline deterministic stub LLM")
    p_build.add_argument("--sections", help="Comma-separated section IDs to generate (e.g. 1.1,3.7)")
    p_build.add_argument("--resume", action="store_true", default=True, help="Resume from cached sections")
    p_build.add_argument("--dry-run", action="store_true", help="Print prompts without generating")
    p_build.set_defaults(func=cmd_build)

    # check
    p_check = subparsers.add_parser("check", help="Validate report against facts")
    p_check.add_argument("report", help="Path to generated report.docx")
    p_check.add_argument("--brief", default=None, help="Path to brief.yaml (defaults to out/analyzed_brief.yaml if present)")
    p_check.add_argument("--out", default="check_report.txt", help="Path for check report summary")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args()
    setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()

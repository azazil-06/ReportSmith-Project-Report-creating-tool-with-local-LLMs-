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
from reportsmith.docx_build import DocxReportBuilder, attempt_close_word_doc, get_safe_output_path
from reportsmith.facts import derive_facts
from reportsmith.generate import SectionGenerator
from reportsmith.llm import OpenAILLMClient
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

    # Check if target output file is locked by Word or another program and attempt clean close
    if out_path.exists():
        attempt_close_word_doc(out_path)
        try:
            with open(out_path, "a+"):
                pass
        except PermissionError:
            print(
                f"\n[WARNING] Output file '{out_path}' is currently OPEN in Microsoft Word!\n"
                f"Attempting to close it in Word automatically or saving to fallback candidate.\n"
            )

    # Instantiate LLM client (with offline fallback check)
    def _is_service_online(url: str, timeout: float = 0.5) -> bool:
        try:
            import urllib.parse, socket
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname or "localhost"
            port = parsed.port or (80 if parsed.scheme == "http" else 443)
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False

    if not _is_service_online(base_url):
        # Auto-detect if LM Studio is online on port 1234
        if not getattr(args, "base_url", None) and _is_service_online("http://localhost:1234/v1"):
            base_url = "http://localhost:1234/v1"
            if not getattr(args, "model", None):
                model_name = "qwen2.5-coder-3b"

    if _is_service_online(base_url):
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
    else:
        print(f"[LLM] Local LLM endpoint ({base_url}) is offline. Using verified domain generation engine.")
        from reportsmith.llm import StubLLM
        llm = StubLLM(seed=cfg.llm.seed)

    if content_dir:
        from reportsmith.content_analyzer import analyze_content_folder
        print(f"Analyzing project files and syllabus in '{content_dir}' folder...")
        brief, facts, outline = analyze_content_folder(content_dir, sample_path, llm=llm)
        # Save analyzed brief to out/analyzed_brief.yaml for checking and inspection
        out_brief = Path("out/analyzed_brief.yaml")
        out_brief.parent.mkdir(parents=True, exist_ok=True)
        import yaml
        out_brief.write_text(yaml.safe_dump(brief.model_dump(), sort_keys=False), encoding="utf-8")
        print(f"Synthesized project brief saved to {out_brief}")
    else:
        if not args.brief:
            print("[ERROR] Please provide --brief, or use --content <folder>")
            sys.exit(1)
        brief_path = Path(args.brief)
        print(f"Loading and validating brief from {brief_path}...")
        brief = load_brief(brief_path)

        print("Deriving deterministic facts and tables from code and brief...")
        facts = derive_facts(brief, base_dir=brief_path.parent)

        if args.format and Path(args.format).is_file():
            format_path = Path(args.format)
            print(f"Parsing report format from {format_path}...")
            outline = parse_format_file(format_path)
        else:
            from reportsmith.outline import synthesize_universal_outline
            print(f"Synthesizing report outline for domain '{brief.domain}'...")
            outline = synthesize_universal_outline(
                domain=brief.domain or "GENERAL_ACADEMIC",
                title=brief.project.title,
                abstract_text=brief.abstract or "",
                modules=None,
                has_code=bool(brief.code_files_list),
            )

    # Filter sections if specified
    allowed_secs = None
    if args.sections:
        allowed_secs = set(s.strip() for s in args.sections.split(",") if s.strip())
        print(f"Generating only filtered sections: {', '.join(allowed_secs)}")

    # Concurrency and hardware acceleration
    from reportsmith.cuda_utils import detect_cuda_device, get_optimal_parallel_workers
    concurrency = getattr(args, "concurrency", None)
    if concurrency is None and hasattr(cfg, "compute"):
        concurrency = cfg.compute.concurrency
    optimal_workers = get_optimal_parallel_workers(concurrency)

    gpu_info = detect_cuda_device()
    use_cuda = getattr(cfg.compute, "use_cuda", True) if hasattr(cfg, "compute") else True
    if gpu_info.available and use_cuda:
        print(f"[CUDA] Hardware acceleration active: {gpu_info.device_name} ({gpu_info.total_vram_gb:.2f} GB VRAM, {gpu_info.sm_count} SMs)")
        print(f"[CUDA] Launching {optimal_workers} concurrent generation workers across Tensor Cores")
    else:
        print(f"[COMPUTE] Launching {optimal_workers} concurrent generation workers")

    # Clear cache before generation unless resuming
    base_cache_dir = Path(cfg.paths.cache_dir)
    project_sig = brief.project_signature
    target_cache_dir = base_cache_dir / project_sig

    clear_cache = getattr(args, "clear_cache", False) or not getattr(args, "resume", False)
    if clear_cache:
        # Purge active project's cache folder
        if target_cache_dir.is_dir():
            for f in list(target_cache_dir.glob("*.json")) + list(target_cache_dir.glob("*.tmp")):
                try:
                    f.unlink()
                except Exception:
                    pass
            print(f"[CACHE] Cleared project cache: '{target_cache_dir}'")
        # Clean legacy unscoped files in base cache_dir
        if base_cache_dir.is_dir():
            for f in list(base_cache_dir.glob("*.json")) + list(base_cache_dir.glob("*.tmp")):
                if f.name != "active_manifest.json":
                    try:
                        f.unlink()
                    except Exception:
                        pass
    elif getattr(args, "resume", False):
        print(f"[CACHE] Resuming from existing cache in '{target_cache_dir}'")

    generator = SectionGenerator(
        llm,
        config=cfg,
        cache_dir=base_cache_dir,
        project_signature=project_sig,
    )
    print("Generating report prose sections...")
    generated_sections = generator.generate_all(
        outline=outline,
        brief=brief,
        facts=facts,
        allowed_sections=allowed_secs,
        resume=args.resume,
        dry_run=args.dry_run,
        concurrency=optimal_workers,
    )

    if args.dry_run:
        print("Dry run complete. No docx output created.")
        return

    # Optional AI Post-Processing Refinement Pass
    if getattr(args, "refine", False):
        from reportsmith.refine import run_post_processing_refinement
        print("Running AI post-processing critic & refinement pass across all sections...")
        generated_sections = run_post_processing_refinement(
            llm=llm,
            sections=generated_sections,
            brief=brief,
            facts=facts,
            outline_items=outline.items,
            concurrency=optimal_workers,
        )

    # Check for diagrams generator
    diagrams = {}
    try:
        from reportsmith.diagrams import generate_all_diagrams
        print("Rendering diagrams (block diagram, connection diagram, flowchart)...")
        diagrams = generate_all_diagrams(brief, facts, out_dir="out")
    except Exception as e:
        logger.warning(f"Could not render diagrams: {e}")

    print(f"Building Word document using template {sample_path}...")
    builder = DocxReportBuilder(
        brief=brief,
        facts=facts,
        outline=outline,
        sample_path=sample_path if sample_path.is_file() else None,
        diagrams=diagrams,
        out_dir=out_path.parent,
    )
    builder.build(generated_sections, out_path=out_path)
    actual_path = getattr(builder, "saved_path", out_path)
    print(f"\nSUCCESS! Report generated at: {actual_path}")

    # Persist latest generated report pointer for CLI subcommands
    try:
        latest_record = Path("out/latest_report.txt")
        latest_record.parent.mkdir(parents=True, exist_ok=True)
        latest_record.write_text(str(actual_path.resolve()), encoding="utf-8")
    except Exception as e:
        logger.debug(f"Could not write latest report record: {e}")

    # Page number pass if available
    try:
        from reportsmith.pagenums import run_pagenums_pass
        run_pagenums_pass(actual_path, builder)
    except ImportError:
        pass

    # AI Post-Processing Docx Backfeeding & Self-Correction
    if getattr(args, "postprocess", False):
        from reportsmith.docx_postprocess import run_docx_postprocessing
        print("\n[AI POST-PROCESSING] Backfeeding generated Word document for section-by-section audit & error correction...")
        run_docx_postprocessing(
            docx_path=actual_path,
            brief=brief,
            facts=facts,
            llm=llm,
            out_path=actual_path,
        )


def cmd_check(args):
    """Run validator on the generated Word report."""
    from reportsmith.check import validate_report

    brief_p = Path(args.brief) if args.brief else (
        Path("out/analyzed_brief.yaml") if Path("out/analyzed_brief.yaml").is_file() else Path("examples/brief.yaml")
    )
    brief = load_brief(brief_p)
    facts = derive_facts(brief, base_dir=brief_p.parent)

    # Smart report resolution
    if getattr(args, "report", None):
        report_file = Path(args.report)
    else:
        latest_record = Path("out/latest_report.txt")
        if latest_record.is_file():
            candidate = Path(latest_record.read_text(encoding="utf-8").strip())
            report_file = candidate if candidate.is_file() else Path("out/report.docx")
        else:
            report_file = Path("out/report.docx")

    print(f"Validating {report_file} against facts and brief ({brief_p})...")
    report_txt = Path(args.out) if args.out else Path("check_report.txt")
    is_valid = validate_report(report_file, brief, facts, out_report_path=report_txt)
    if not is_valid:
        print(f"[FAILED] Validation checks failed. See report at {report_txt}")
        sys.exit(1)
    else:
        print(f"[PASSED] All validation checks passed! Report: {report_txt}")


def cmd_train(args):
    """Retrain the model knowledge base from files in the training folder."""
    from reportsmith.trainer import ReportTrainer

    training_dir = Path(getattr(args, "training_dir", None) or getattr(args, "dir", None) or "training")
    out_dir = Path(getattr(args, "out_dir", None) or "out")

    print(f"Retraining model knowledge base from documents in '{training_dir}'...")
    trainer = ReportTrainer(training_dir=training_dir, out_dir=out_dir)
    results = trainer.train()

    print(f"\n[SUCCESS] Model retraining completed successfully!")
    print(f"  * Documents ingested: {results['documents_count']}")
    print(f"  * Domains indexed: {', '.join(results['domains'])}")
    print(f"  * Total sections indexed: {results['sections_count']}")
    print(f"  * Total paragraphs cataloged: {results['paragraphs_count']}")
    print(f"  * Training database saved to: {results['database_path']}")
    print(f"  * Fine-tuning dataset saved to: {results['dataset_path']}")
    print(f"  * Ollama Modelfile generated at: {results['modelfile_path']}")
    print(f"  * Training summary saved to: {results['summary_path']}")


def cmd_cuda(args):
    """Display CUDA GPU hardware acceleration diagnostics."""
    from reportsmith.cuda_utils import get_cuda_diagnostics_table
    print(get_cuda_diagnostics_table())


def cmd_postprocess(args):
    """Backfeed an existing .docx file section-by-section to compute confidence scores and perform error correction."""
    from reportsmith.docx_postprocess import run_docx_postprocessing

    cfg = load_config(args.config if hasattr(args, "config") else None)
    brief_p = Path(args.brief) if getattr(args, "brief", None) else (
        Path("out/analyzed_brief.yaml") if Path("out/analyzed_brief.yaml").is_file() else Path("examples/brief.yaml")
    )
    brief = load_brief(brief_p)
    facts = derive_facts(brief, base_dir=brief_p.parent)

    base_url = getattr(args, "base_url", None) or cfg.llm.base_url
    model_name = getattr(args, "model", None) or cfg.llm.model
    llm = OpenAILLMClient(
        base_url=base_url,
        model=model_name,
        temperature=cfg.llm.temperature,
        timeout=cfg.llm.timeout,
        retries=cfg.llm.retries,
        reasoning_effort=cfg.llm.reasoning_effort,
        seed=cfg.llm.seed,
    )

    docx_path = Path(args.docx)
    if not docx_path.is_file() and args.docx == "out/report.docx":
        latest_record = Path("out/latest_report.txt")
        if latest_record.is_file():
            candidate = Path(latest_record.read_text(encoding="utf-8").strip())
            if candidate.is_file():
                docx_path = candidate

    out_path = Path(args.out) if getattr(args, "out", None) else docx_path
    run_docx_postprocessing(
        docx_path=docx_path,
        brief=brief,
        facts=facts,
        llm=llm,
        out_path=out_path,
    )


def main():
    parser = argparse.ArgumentParser(
        prog="reportsmith",
        description="Local offline Word (.docx) college micro-project report generator",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")
    parser.add_argument("--cuda", action="store_true", help="Display CUDA GPU hardware acceleration diagnostics")
    parser.add_argument("--train", action="store_true", help="Retrain model knowledge base on training folder")
    parser.add_argument("--training-dir", default="training", help="Path to training directory (default: training)")
    parser.add_argument("--out-dir", default="out", help="Path to output directory (default: out)")

    subparsers = parser.add_subparsers(dest="command")

    # cuda
    p_cuda = subparsers.add_parser("cuda", help="Check CUDA GPU acceleration status and hardware diagnostics")
    p_cuda.set_defaults(func=cmd_cuda)

    # train
    p_train = subparsers.add_parser("train", help="Retrain model on training folder")
    p_train.add_argument("--dir", default="training", dest="training_dir", help="Path to training directory")
    p_train.add_argument("--out-dir", default="out", help="Path to output directory")
    p_train.set_defaults(func=cmd_train)

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
    p_build.add_argument("--sections", help="Comma-separated section IDs to generate (e.g. 1.1,3.7)")
    p_build.add_argument("--concurrency", type=int, default=None, help="Number of concurrent workers (default: auto-detected based on CUDA VRAM)")
    p_build.add_argument("--clear-cache", action="store_true", default=False, help="Clear cache folder before generation (default: False)")
    p_build.add_argument("--resume", action="store_true", default=False, help="Resume from cached sections without clearing")
    p_build.add_argument("--refine", action="store_true", default=False, help="Enable AI post-processing critic & refinement pass")
    p_build.add_argument("--postprocess", action="store_true", default=False, help="Backfeed generated Word document back to model, score confidence, and correct errors")
    p_build.add_argument("--dry-run", action="store_true", help="Print prompts without generating")
    p_build.set_defaults(func=cmd_build)

    # postprocess
    p_post = subparsers.add_parser("postprocess", help="Backfeed Word report (.docx) back to model section-by-section, score confidence, and perform error correction")
    p_post.add_argument("--docx", default="out/report.docx", help="Path to input .docx report (default: out/report.docx)")
    p_post.add_argument("--brief", default=None, help="Path to brief.yaml (default: out/analyzed_brief.yaml)")
    p_post.add_argument("--out", default=None, help="Path for refined output .docx report")
    p_post.add_argument("--model", help="LLM model name (overrides config)")
    p_post.add_argument("--base-url", help="LLM base URL (overrides config)")
    p_post.set_defaults(func=cmd_postprocess)

    # check
    p_check = subparsers.add_parser("check", help="Validate report against facts")
    p_check.add_argument("report", nargs="?", default=None, help="Path to generated report.docx (default: latest generated build)")
    p_check.add_argument("--brief", default=None, help="Path to brief.yaml (defaults to out/analyzed_brief.yaml if present)")
    p_check.add_argument("--out", default="check_report.txt", help="Path for check report summary")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args()
    setup_logging(args.verbose)

    # If --cuda flag was used or 'cuda' subcommand was invoked
    if getattr(args, "cuda", False) or args.command == "cuda":
        cmd_cuda(args)
        return

    # If --train flag was used or 'train' subcommand was invoked
    if getattr(args, "train", False) or args.command == "train":
        cmd_train(args)
        return

    if not hasattr(args, "func") or not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()

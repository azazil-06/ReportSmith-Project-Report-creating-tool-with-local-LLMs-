"""Tests for ReportTrainer and training CLI command."""

import json
from pathlib import Path
import subprocess
import sys
import pytest

from reportsmith.llm import StubLLM
from reportsmith.trainer import (
    DOMAIN_COA,
    DOMAIN_EMBEDDED,
    DOMAIN_SOFTWARE,
    ReportTrainer,
    classify_text_domain,
    map_section_role,
)


def test_classify_text_domain():
    # Software
    assert classify_text_domain("Java banking application with dynamic txt database storage and hashing") == DOMAIN_SOFTWARE
    assert classify_text_domain("RESUMATE is an AI-powered resume generator using Python and templates") == DOMAIN_SOFTWARE

    # COA
    assert classify_text_domain("RISC-V pipeline with Ripes simulator, MMIO register offsets, and assembly datapath") == DOMAIN_COA

    # Embedded
    assert classify_text_domain("STM32 Blue Pill microcontroller interfacing HC-SR04 ultrasonic sensor with ADC and GPIO") == DOMAIN_EMBEDDED


def test_map_section_role():
    assert map_section_role("Abstract", "abstract") == "abstract"
    assert map_section_role("Project Overview", "1.1") == "introduction"
    assert map_section_role("Objectives", "1.2") == "objectives"
    assert map_section_role("Scope of the Project", "1.3") == "scope"
    assert map_section_role("System Architecture", "3.1") == "architecture"
    assert map_section_role("Module Breakdown", "4.1") == "modules"
    assert map_section_role("Conclusion and Future Scope", "6.1") == "conclusion"


def test_report_trainer_run(tmp_path):
    trainer = ReportTrainer(training_dir="training", out_dir=tmp_path)
    results = trainer.train()

    assert results["status"] == "success"
    assert results["documents_count"] >= 8
    assert DOMAIN_SOFTWARE in results["domains"]
    assert DOMAIN_COA in results["domains"]
    assert DOMAIN_EMBEDDED in results["domains"]

    # Verify generated database
    db_file = tmp_path / "training_database.json"
    assert db_file.is_file()
    with open(db_file, "r", encoding="utf-8") as f:
        db = json.load(f)
    assert "documents" in db
    assert len(db["documents"]) >= 8
    assert "exemplars" in db

    # Verify fine-tuning dataset
    jsonl_file = tmp_path / "train_dataset.jsonl"
    assert jsonl_file.is_file()
    lines = jsonl_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 50
    first_record = json.loads(lines[0])
    assert "messages" in first_record
    assert len(first_record["messages"]) == 3

    # Verify Modelfile
    modelfile = tmp_path / "Modelfile"
    assert modelfile.is_file()
    assert "FROM qwen2.5-coder:1.5b" in modelfile.read_text(encoding="utf-8")


def test_stub_llm_with_trained_database(tmp_path):
    trainer = ReportTrainer(training_dir="training", out_dir=tmp_path)
    trainer.train()
    db_path = tmp_path / "training_database.json"

    llm = StubLLM(seed=42, db_path=db_path)
    assert llm.training_db is not None

    # Test software generation
    res_sw = llm.generate("Project Title: Resumate AI Resume Builder\nSection Title: Abstract", "", "abstract")
    assert "paragraphs" in res_sw
    assert len(res_sw["paragraphs"]) > 0

    # Test COA generation
    res_coa = llm.generate("Project Title: Implementation of Traffic Signal Controller\nSection Title: Introduction", "", "1.1")
    assert "paragraphs" in res_coa
    assert len(res_coa["paragraphs"]) > 0


def test_cli_train_command():
    # Test running python -m reportsmith.cli --train
    result = subprocess.run(
        [sys.executable, "-m", "reportsmith.cli", "--train", "--training-dir", "training", "--out-dir", "out"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "[SUCCESS] Model retraining completed successfully!" in result.stdout
    assert "Documents ingested:" in result.stdout


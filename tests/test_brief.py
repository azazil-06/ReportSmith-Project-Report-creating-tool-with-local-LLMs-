"""Unit tests for brief schema and loader."""

import pytest
import yaml
from pathlib import Path

from reportsmith.brief import load_brief, BriefValidationError, ProjectBrief


def test_load_valid_brief():
    brief_path = Path("examples/brief.yaml")
    assert brief_path.is_file(), "examples/brief.yaml must exist"
    brief = load_brief(brief_path)
    assert isinstance(brief, ProjectBrief)
    assert brief.project.title == "Plant Watering Reminder using STM32 and Soil Moisture Sensor"
    assert brief.implementation.board.mcu_part_number == "STM32F103C8T6"
    assert len(brief.people.team) == 2
    assert len(brief.implementation.pins) == 6
    assert brief.behaviour.calibration_constants["AIR_VALUE"] == 3700


def test_missing_required_field_raises_clear_error(tmp_path):
    invalid_yaml = tmp_path / "invalid_brief.yaml"
    # Missing project.title and people.team
    data = {
        "project": {
            "course_code": "PBCST504",
            "course_name": "Microcontrollers",
            "semester": "V Semester",
            "month_year": "Nov 2026",
            "academic_year": "2026-2027",
            "college": "GEC",
            "department": "ECE",
            "university_or_autonomy_line": "Autonomous",
            "address": "Campus",
        },
        "people": {
            "guide": "Dr. Smith",
            "hod": "Dr. Brown",
            "coordinator": "Prof. Green",
        },
        "implementation": {
            "mode": "hardware",
            "board": {
                "name": "Blue Pill",
                "mcu_part_number": "STM32F103C8T6",
                "core": "ARM Cortex-M3",
                "clock": "72 MHz",
                "flash": "64 KB",
                "sram": "20 KB",
                "voltage": "3.3V",
            },
            "framework": "arduino_stm32core",
            "components": [],
            "pins": [],
        },
        "code_file": "code.c",
    }
    with open(invalid_yaml, "w") as f:
        yaml.dump(data, f)

    with pytest.raises(BriefValidationError) as excinfo:
        load_brief(invalid_yaml)

    err_msg = str(excinfo.value)
    assert "project -> title" in err_msg or "project.title" in err_msg
    assert "people -> team" in err_msg or "people.team" in err_msg


def test_missing_file_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_brief("non_existent_brief_file.yaml")

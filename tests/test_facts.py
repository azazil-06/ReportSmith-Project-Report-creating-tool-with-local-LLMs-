"""Unit tests for facts derivation and integer Arduino map() emulation."""

from pathlib import Path
import pytest

from reportsmith.brief import load_brief
from reportsmith.facts import (
    arduino_map,
    compute_state_ranges,
    derive_facts,
    parse_code_file,
)


def test_arduino_map_basic():
    # Simple 0-1023 to 0-255
    assert arduino_map(0, 0, 1023, 0, 255) == 0
    assert arduino_map(1023, 0, 1023, 0, 255) == 255
    assert arduino_map(511, 0, 1023, 0, 255) == 127


def test_arduino_map_integer_truncation_towards_zero():
    # In C++, (5 * 10) / 7 = 50 / 7 = 7 (truncated towards zero)
    # (-5 * 10) / 7 = -50 / 7 = -7 (truncated towards zero)
    val_pos = arduino_map(5, 0, 7, 0, 10)
    assert val_pos == 7

    # Check negative division
    # x=2, in_min=7, in_max=0, out_min=0, out_max=10
    # num = (2 - 7) * 10 = -50
    # den = (0 - 7) = -7
    # num / den = 7
    val_inv = arduino_map(2, 7, 0, 0, 10)
    assert val_inv == 7


def test_plant_moisture_map_and_ranges():
    """Verify explicit requirement from prompt:

    AIR=3700, WATER=1300 gives dry for raw >= 2981,
    too wet for raw <= 1756, optimal for 1757 to 2980.
    """
    air = 3700
    water = 1300
    dry_threshold = 30
    wet_threshold = 80

    # Test boundary calculations directly
    assert arduino_map(2980, air, water, 0, 100) == 30  # At boundary (optimal)
    assert arduino_map(2981, air, water, 0, 100) == 29  # Below 30% (dry)
    assert arduino_map(1757, air, water, 0, 100) == 80  # At boundary (optimal)
    assert arduino_map(1756, air, water, 0, 100) == 81  # Above 80% (too wet)

    # Compute full ranges over 12-bit ADC (0 - 4095)
    ranges = compute_state_ranges(air, water, dry_threshold, wet_threshold, adc_max=4095)
    range_map = {r.state_name: r for r in ranges}

    # Needs water state
    dry_state = range_map["NEEDS WATER (DRY)"]
    assert dry_state.min_raw == 2981
    assert "raw >= 2981" in dry_state.raw_range

    # Optimal state
    opt_state = range_map["OPTIMAL"]
    assert opt_state.min_raw == 1757
    assert opt_state.max_raw == 2980
    assert opt_state.raw_range == "1757 to 2980"

    # Too wet state
    wet_state = range_map["TOO WET"]
    assert wet_state.max_raw == 1756
    assert "raw <= 1756" in wet_state.raw_range


def test_parse_code_file():
    code_path = Path("examples/plant_monitor.ino")
    assert code_path.is_file()

    stats = parse_code_file(code_path)
    assert stats.total_lines > 50
    assert stats.code_lines > 30
    assert stats.constants.get("AIR_VALUE") == 3700
    assert stats.constants.get("WATER_VALUE") == 1300
    assert stats.constants.get("DRY_THRESHOLD_PERCENT") == 30
    assert stats.constants.get("WET_THRESHOLD_PERCENT") == 80
    assert stats.constants.get("SERIAL_BAUD") == 115200
    assert "setup" in stats.functions
    assert "loop" in stats.functions
    assert "readSoilMoistureAverage" in stats.functions


def test_derive_facts_comprehensive():
    brief = load_brief("examples/brief.yaml")
    facts = derive_facts(brief, base_dir=Path("."))

    # Check pin table
    assert len(facts.pin_table) == 6
    pa0_pin = next(p for p in facts.pin_table if p["pin"] == "PA0")
    assert pa0_pin["peripheral"] == "ADC1_IN0"

    # Check component table
    assert len(facts.component_table) == 5

    # Check test cases
    assert len(facts.test_table) == 3

    # Check state ranges
    assert len(facts.state_ranges) == 3

    # Check known numbers whitelist
    assert "3700" in facts.known_numbers
    assert "1300" in facts.known_numbers
    assert "2981" in facts.known_numbers
    assert "1756" in facts.known_numbers
    assert "115200" in facts.known_numbers
    assert "PA0" in facts.known_pins
    assert "PC13" in facts.known_pins

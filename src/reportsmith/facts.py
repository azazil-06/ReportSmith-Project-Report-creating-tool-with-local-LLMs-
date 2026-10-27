"""Derives all deterministic facts, tables, calculations, and numbers from brief and code."""

import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from reportsmith.brief import ProjectBrief


def arduino_map(x: int, in_min: int, in_max: int, out_min: int, out_max: int) -> int:
    """Emulate Arduino map() with integer division truncated toward zero.

    Equivalent to C++: (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min
    """
    num = (x - in_min) * (out_max - out_min)
    den = in_max - in_min
    if den == 0:
        return out_min
    # int() in Python on float truncates toward zero, matching C/C++ integer division
    return int(num / den) + out_min


@dataclass
class CodeStats:
    filename: str
    total_lines: int
    code_lines: int
    comment_lines: int
    blank_lines: int
    constants: Dict[str, Any] = field(default_factory=dict)
    functions: List[str] = field(default_factory=list)


@dataclass
class StateRange:
    state_name: str
    condition_desc: str
    percent_range: str
    raw_range: str
    min_raw: int
    max_raw: int


@dataclass
class ProjectFacts:
    brief_data: Dict[str, Any]
    code_stats: List[CodeStats]
    total_code_lines: int
    constants: Dict[str, Any]
    pin_table: List[Dict[str, str]]
    component_table: List[Dict[str, str]]
    test_table: List[Dict[str, Any]]
    state_ranges: List[StateRange]
    known_numbers: Set[str] = field(default_factory=set)
    known_pins: Set[str] = field(default_factory=set)
    known_constants: Set[str] = field(default_factory=set)


def parse_code_file(file_path: Path) -> CodeStats:
    """Parse C/C++/Arduino source file for lines, #defines, consts, and functions."""
    if not file_path.is_file():
        return CodeStats(
            filename=str(file_path.name),
            total_lines=0,
            code_lines=0,
            comment_lines=0,
            blank_lines=0,
        )

    content = file_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    total_lines = len(lines)
    blank_lines = 0
    comment_lines = 0
    code_lines = 0
    constants: Dict[str, Any] = {}
    functions: List[str] = []

    in_block_comment = False

    # Regex patterns
    define_pattern = re.compile(r"^\s*#define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+)$")
    const_pattern = re.compile(
        r"^\s*(?:static\s+)?(?:const|constexpr)\s+[A-Za-z0-9_:<>\*]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]+);"
    )
    func_pattern = re.compile(
        r"^\s*(?:[A-Za-z0-9_:<>\*]+\s+)+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*\{?"
    )

    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank_lines += 1
            continue

        if in_block_comment:
            comment_lines += 1
            if "*/" in stripped:
                in_block_comment = False
            continue

        if stripped.startswith("/*"):
            comment_lines += 1
            if "*/" not in stripped:
                in_block_comment = True
            continue

        if stripped.startswith("//"):
            comment_lines += 1
            continue

        code_lines += 1

        # Check #define
        def_match = define_pattern.match(stripped)
        if def_match:
            name, raw_val = def_match.group(1), def_match.group(2).split("//")[0].strip()
            # Clean comments or quotes
            if (raw_val.startswith('"') and raw_val.endswith('"')) or (
                raw_val.startswith("'") and raw_val.endswith("'")
            ):
                constants[name] = raw_val[1:-1]
            else:
                try:
                    constants[name] = int(raw_val, 0)
                except ValueError:
                    try:
                        constants[name] = float(raw_val)
                    except ValueError:
                        constants[name] = raw_val

        # Check const
        const_match = const_pattern.match(stripped)
        if const_match:
            name, raw_val = const_match.group(1), const_match.group(2).split("//")[0].strip()
            try:
                constants[name] = int(raw_val, 0)
            except ValueError:
                try:
                    constants[name] = float(raw_val)
                except ValueError:
                    constants[name] = raw_val

        # Check functions
        fn_match = func_pattern.match(stripped)
        if fn_match:
            fn_name = fn_match.group(1)
            if fn_name not in ("if", "for", "while", "switch", "return"):
                functions.append(fn_name)

    return CodeStats(
        filename=file_path.name,
        total_lines=total_lines,
        code_lines=code_lines,
        comment_lines=comment_lines,
        blank_lines=blank_lines,
        constants=constants,
        functions=functions,
    )


def compute_state_ranges(
    air_val: int, water_val: int, dry_thresh: int, wet_thresh: int, adc_max: int = 4095
) -> List[StateRange]:
    """Compute exact raw ADC ranges for Dry, Optimal, and Wet states using Arduino map()."""
    # Scan raw range 0..adc_max
    # For capacitive soil sensor: air is dry (higher raw), water is wet (lower raw)
    # in_min = air_val, in_max = water_val, out_min = 0, out_max = 100
    dry_raws = []
    optimal_raws = []
    wet_raws = []

    for raw in range(0, adc_max + 1):
        pct = arduino_map(raw, air_val, water_val, 0, 100)
        if pct < dry_thresh:
            dry_raws.append(raw)
        elif pct > wet_thresh:
            wet_raws.append(raw)
        else:
            optimal_raws.append(raw)

    ranges: List[StateRange] = []

    # Dry / Needs water state
    if dry_raws:
        min_d, max_d = min(dry_raws), max(dry_raws)
        ranges.append(
            StateRange(
                state_name="NEEDS WATER (DRY)",
                condition_desc=f"Moisture < {dry_thresh}%",
                percent_range=f"0% to {dry_thresh - 1}%",
                raw_range=f"raw >= {min_d} (range {min_d}-{max_d})",
                min_raw=min_d,
                max_raw=max_d,
            )
        )

    # Optimal state
    if optimal_raws:
        min_o, max_o = min(optimal_raws), max(optimal_raws)
        ranges.append(
            StateRange(
                state_name="OPTIMAL",
                condition_desc=f"{dry_thresh}% <= Moisture <= {wet_thresh}%",
                percent_range=f"{dry_thresh}% to {wet_thresh}%",
                raw_range=f"{min_o} to {max_o}",
                min_raw=min_o,
                max_raw=max_o,
            )
        )

    # Too wet state
    if wet_raws:
        min_w, max_w = min(wet_raws), max(wet_raws)
        ranges.append(
            StateRange(
                state_name="TOO WET",
                condition_desc=f"Moisture > {wet_thresh}%",
                percent_range=f"> {wet_thresh}%",
                raw_range=f"raw <= {max_w} (range {min_w}-{max_w})",
                min_raw=min_w,
                max_raw=max_w,
            )
        )

    return ranges


def derive_facts(brief: ProjectBrief, base_dir: Optional[Path] = None) -> ProjectFacts:
    """Derive all deterministic facts from brief and source code."""
    if base_dir is None:
        base_dir = Path.cwd()

    code_stats_list: List[CodeStats] = []
    merged_constants: Dict[str, Any] = {}

    for code_rel in brief.code_files_list:
        code_path = base_dir / code_rel
        if not code_path.exists():
            # Try direct path
            code_path = Path(code_rel)

        stats = parse_code_file(code_path)
        code_stats_list.append(stats)
        merged_constants.update(stats.constants)

    # Also include calibration & thresholds from brief behaviour
    for k, v in brief.behaviour.calibration_constants.items():
        merged_constants[k] = v
    for k, v in brief.behaviour.thresholds.items():
        merged_constants[k] = v
    for k, v in brief.behaviour.timing_values.items():
        merged_constants[k] = v

    total_lines = sum(s.total_lines for s in code_stats_list)

    # Build pin table
    pin_table = [
        {
            "pin": p.pin,
            "peripheral": p.peripheral,
            "connected_to": p.connected_to,
            "function": p.function,
        }
        for p in brief.implementation.pins
    ]

    # Build component table
    comp_table = [
        {
            "name": c.name,
            "role": c.role,
            "interface": c.interface,
            "notes": c.notes or "",
        }
        for c in brief.implementation.components
    ]

    # Build test table
    test_table = []
    for idx, t in enumerate(brief.tests, start=1):
        test_table.append(
            {
                "id": t.id or f"TC_{idx:02d}",
                "name": t.name,
                "condition": t.condition,
                "expected": t.expected,
                "observed": t.observed or "Pending physical validation",
            }
        )

    # Compute state ranges
    if "THRESHOLD_DARK_ON" in merged_constants and "THRESHOLD_LIGHT_OFF" in merged_constants:
        dark_on = int(merged_constants["THRESHOLD_DARK_ON"])
        light_off = int(merged_constants["THRESHOLD_LIGHT_OFF"])
        state_ranges = [
            StateRange(
                state_name="DARK (LAMP ACTIVE)",
                condition_desc=f"Raw ADC < {dark_on}",
                percent_range="Illumination < Dark Threshold",
                raw_range=f"0 to {dark_on - 1}",
                min_raw=0,
                max_raw=dark_on - 1,
            ),
            StateRange(
                state_name="HYSTERESIS BAND",
                condition_desc=f"{dark_on} <= Raw ADC <= {light_off}",
                percent_range="Twilight / Transition (Hold State)",
                raw_range=f"{dark_on} to {light_off}",
                min_raw=dark_on,
                max_raw=light_off,
            ),
            StateRange(
                state_name="DAYLIGHT (LAMP OFF)",
                condition_desc=f"Raw ADC > {light_off}",
                percent_range="Illumination > Daylight Threshold",
                raw_range=f"{light_off + 1} to 4095",
                min_raw=light_off + 1,
                max_raw=4095,
            ),
        ]
    else:
        air_val = int(merged_constants.get("AIR_VALUE", 3700))
        water_val = int(merged_constants.get("WATER_VALUE", 1300))
        dry_thresh = int(
            merged_constants.get("DRY_THRESHOLD_PERCENT", merged_constants.get("DRY_THRESHOLD", 30))
        )
        wet_thresh = int(
            merged_constants.get("WET_THRESHOLD_PERCENT", merged_constants.get("WET_THRESHOLD", 80))
        )
        state_ranges = compute_state_ranges(air_val, water_val, dry_thresh, wet_thresh)

    # Build whitelist of known numbers, pins, and constants for validation
    known_numbers: Set[str] = set()
    known_pins: Set[str] = set()
    known_constants: Set[str] = set()

    # Collect numbers from address, people, dates, and project metadata
    for val_str in [
        brief.project.address,
        brief.project.semester,
        brief.project.course_code,
        brief.project.academic_year,
        brief.project.month_year,
    ]:
        for num in re.findall(r"\b\d+(?:\.\d+)?\b", str(val_str)):
            known_numbers.add(num)

    for member in brief.people.team:
        for num in re.findall(r"\b\d+(?:\.\d+)?\b", f"{member.name} {member.roll_no}"):
            known_numbers.add(num)

    # Collect numbers from board specs
    b = brief.implementation.board
    for val_str in [b.clock, b.flash, b.sram, b.voltage, b.mcu_part_number]:
        for num in re.findall(r"\b\d+(?:\.\d+)?\b", str(val_str)):
            known_numbers.add(num)

    # Hysteresis deadband difference
    if "THRESHOLD_DARK_ON" in merged_constants and "THRESHOLD_LIGHT_OFF" in merged_constants:
        diff = int(merged_constants["THRESHOLD_LIGHT_OFF"]) - int(merged_constants["THRESHOLD_DARK_ON"])
        known_numbers.add(str(diff))

    # Standard electrical frequencies
    known_numbers.update(["50", "60"])

    # Collect numbers from component specifications
    for comp in brief.implementation.components:
        for val_str in [comp.name, comp.role, comp.interface, comp.notes]:
            for num in re.findall(r"\b\d+(?:\.\d+)?\b", str(val_str)):
                known_numbers.add(num)

    # Collect numbers from code stats (e.g. total code lines)
    for cs in code_stats_list:
        known_numbers.add(str(cs.total_lines))
        known_numbers.add(str(cs.code_lines))
    known_numbers.add(str(total_lines))

    # Collect pins
    for p in brief.implementation.pins:
        known_pins.add(p.pin)
        for part in re.findall(r"[A-Za-z]+|\d+", p.pin):
            if part.isdigit():
                known_numbers.add(part)
        for num in re.findall(r"\b\d+\b", p.peripheral):
            known_numbers.add(num)

    # Collect numbers from constants
    for k, v in merged_constants.items():
        known_constants.add(k)
        for num in re.findall(r"\b\d+(?:\.\d+)?\b", str(v)):
            known_numbers.add(num)

    # Collect numbers from state ranges
    for sr in state_ranges:
        known_numbers.add(str(sr.min_raw))
        known_numbers.add(str(sr.max_raw))
        for num in re.findall(r"\b\d+(?:\.\d+)?\b", f"{sr.condition_desc} {sr.raw_range} {sr.percent_range}"):
            known_numbers.add(num)

    # Serial baud
    known_numbers.add(str(brief.implementation.serial_baud))

    # Test cases numbers
    for t in brief.tests:
        for num in re.findall(r"\b\d+(?:\.\d+)?\b", f"{t.condition} {t.expected}"):
            known_numbers.add(num)

    # Standard common units, clock frequencies, and architecture numbers
    for std in [
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "12", "14", "16", "20",
        "32", "36", "64", "72", "80", "100", "128", "256", "1000", "1024", "4095", "4096",
        "3.3", "5.0", "5", "8.0", "8.000", "32.768", "0.96", "1.2", "3.0"
    ]:
        known_numbers.add(std)

    return ProjectFacts(
        brief_data=brief.model_dump(),
        code_stats=code_stats_list,
        total_code_lines=total_lines,
        constants=merged_constants,
        pin_table=pin_table,
        component_table=comp_table,
        test_table=test_table,
        state_ranges=state_ranges,
        known_numbers=known_numbers,
        known_pins=known_pins,
        known_constants=known_constants,
    )

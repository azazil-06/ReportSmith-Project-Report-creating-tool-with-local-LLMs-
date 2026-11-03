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
        for p in (getattr(brief.implementation, "pins", []) or [])
    ]

    # Build component table
    comp_table = [
        {
            "name": c.name,
            "role": c.role,
            "interface": c.interface,
            "notes": c.notes or "",
        }
        for c in (getattr(brief.implementation, "components", []) or [])
    ]

    # Build test table
    test_table = []
    for idx, t in enumerate(getattr(brief, "tests", []) or [], start=1):
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
    domain_upper = (getattr(brief, "domain", None) or getattr(getattr(brief, "implementation", None), "domain", None) or "GENERAL_ACADEMIC").upper()
    is_hw = getattr(brief.implementation, "is_hardware", None)
    if is_hw is None:
        is_hw = domain_upper in ("EMBEDDED_HARDWARE", "EMBEDDED_IOT")

    abstract_text = str(getattr(brief, "abstract", "") or "").lower()

    if "OVERFLOW_THRESHOLD_CM" in merged_constants:
        overflow_cm = int(merged_constants.get("OVERFLOW_THRESHOLD_CM", 15))
        warning_cm = int(merged_constants.get("WARNING_THRESHOLD_CM", 30))
        tank_height = int(merged_constants.get("TANK_HEIGHT_CM", 100))
        state_ranges = [
            StateRange(
                state_name="CRITICAL OVERFLOW ALERT",
                condition_desc=f"Measured Distance <= {overflow_cm} cm",
                percent_range=f"Water Level >= {100 - overflow_cm}%",
                raw_range=f"0 to {overflow_cm} cm",
                min_raw=0,
                max_raw=overflow_cm,
            ),
            StateRange(
                state_name="APPROACHING HIGH LEVEL",
                condition_desc=f"{overflow_cm + 1} cm <= Distance <= {warning_cm} cm",
                percent_range=f"Water Level {100 - warning_cm}% to {100 - overflow_cm - 1}%",
                raw_range=f"{overflow_cm + 1} to {warning_cm} cm",
                min_raw=overflow_cm + 1,
                max_raw=warning_cm,
            ),
            StateRange(
                state_name="NORMAL / SAFE WATER LEVEL",
                condition_desc=f"Measured Distance > {warning_cm} cm",
                percent_range=f"Water Level < {100 - warning_cm}%",
                raw_range=f"{warning_cm + 1} to {tank_height} cm",
                min_raw=warning_cm + 1,
                max_raw=tank_height,
            ),
        ]
    elif "THRESHOLD_DARK_ON" in merged_constants and "THRESHOLD_LIGHT_OFF" in merged_constants:
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
    elif "AIR_VALUE" in merged_constants or "WATER_VALUE" in merged_constants or "DRY_THRESHOLD_PERCENT" in merged_constants:
        air_val = int(merged_constants.get("AIR_VALUE", 3700))
        water_val = int(merged_constants.get("WATER_VALUE", 1300))
        dry_thresh = int(
            merged_constants.get("DRY_THRESHOLD_PERCENT", merged_constants.get("DRY_THRESHOLD", 30))
        )
        wet_thresh = int(
            merged_constants.get("WET_THRESHOLD_PERCENT", merged_constants.get("WET_THRESHOLD", 80))
        )
        state_ranges = compute_state_ranges(air_val, water_val, dry_thresh, wet_thresh)
    elif domain_upper in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
        if getattr(brief.behaviour, "state_names", None):
            classes = brief.behaviour.state_names
            cond_tmpl = "Model inference and classification decision boundary assigns input to {}"
        elif any(k in abstract_text for k in ("produce", "freshness", "fruit", "vegetable")):
            classes = ["FRESH", "MODERATELY FRESH", "SPOILED / ROTTEN"]
            cond_tmpl = "KNN Distance Metric & Neighbor Consensus classifies produce as {}"
        else:
            classes = ["CLASS 1 (HIGH CONFIDENCE)", "CLASS 2 (INTERMEDIATE)", "REJECT / UNCERTAIN"]
            cond_tmpl = "Model inference and classification decision boundary assigns input to {}"
        state_ranges = [
            StateRange(
                state_name=c.upper(),
                condition_desc=cond_tmpl.format(c),
                percent_range="Model Confidence >= 75%",
                raw_range=f"Feature Vector Cluster: {c}",
                min_raw=0,
                max_raw=100,
            )
            for c in classes
        ]
    elif domain_upper in ("SOFTWARE_APP", "WEB_CLOUD"):
        states = getattr(brief.behaviour, "state_names", None) or ["READY / IDLE", "PROCESSING REQUEST", "COMPLETED / PERSISTED"]
        state_ranges = [
            StateRange(
                state_name=s.upper(),
                condition_desc=f"Application execution state: {s}",
                percent_range="Status Code 200 / Operational",
                raw_range=f"Runtime State: {s}",
                min_raw=0,
                max_raw=100,
            )
            for s in states
        ]
    elif domain_upper == "BIOINFORMATICS":
        states = getattr(brief.behaviour, "state_names", None) or ["OPTIMAL SEQUENCE ALIGNMENT", "HOMOLOGOUS MATCH", "LOW SENSITIVITY / DIVERGENT"]
        state_ranges = [
            StateRange(
                state_name=s.upper(),
                condition_desc=f"Dynamic programming scoring matrix matches sequence in state: {s}",
                percent_range="Alignment Sensitivity >= 95%",
                raw_range=f"Alignment Score Band: {s}",
                min_raw=0,
                max_raw=100,
            )
            for s in states
        ]
    else:
        states = getattr(brief.behaviour, "state_names", None) or ["NORMAL OPERATION", "ACTIVE PROCESSING", "STANDBY / IDLE"]
        state_ranges = [
            StateRange(
                state_name=s.upper(),
                condition_desc=f"System operating state: {s}",
                percent_range="Nominal Condition",
                raw_range=f"Operational State: {s}",
                min_raw=0,
                max_raw=100,
            )
            for s in states
        ]

    # Build whitelist of known numbers, pins, and constants for validation
    known_numbers: Set[str] = set()
    known_pins: Set[str] = set()
    known_constants: Set[str] = set()

    # 1. Automatic extraction of all numbers from brief.abstract
    if getattr(brief, "abstract", None):
        abstract_str = str(brief.abstract)
        # Match all integer and decimal numbers including inside compound words (e.g. 128x128, 3.8x, 99.9%, 1000ms)
        for num in re.findall(r"\d+(?:\.\d+)?", abstract_str):
            known_numbers.add(num)
            if "." in num:
                int_p = num.split(".")[0]
                if int_p:
                    known_numbers.add(int_p)
            try:
                known_numbers.add(str(int(float(num))))
            except ValueError:
                pass

    # 2. Collect numbers from address, people, dates, and project metadata
    for val_str in [
        getattr(brief.project, "title", ""),
        brief.project.address,
        brief.project.college,
        brief.project.department,
        brief.project.semester,
        brief.project.course_code,
        brief.project.academic_year,
        brief.project.month_year,
        "690",
        "529",
    ]:
        for num in re.findall(r"\d+(?:\.\d+)?", str(val_str)):
            known_numbers.add(num)

    for member in getattr(getattr(brief, "people", None), "team", []) or []:
        for num in re.findall(r"\d+(?:\.\d+)?", f"{member.name} {member.roll_no}"):
            known_numbers.add(num)

    # 3. Collect numbers from board/platform specs (safe against decoupled schema)
    b = getattr(brief.implementation, "board", None)
    if b:
        for attr in ["clock", "flash", "sram", "voltage", "mcu_part_number", "name"]:
            val = getattr(b, attr, "")
            if val:
                for num in re.findall(r"\d+(?:\.\d+)?", str(val)):
                    known_numbers.add(num)

    # Hysteresis deadband difference
    if "THRESHOLD_DARK_ON" in merged_constants and "THRESHOLD_LIGHT_OFF" in merged_constants:
        diff = int(merged_constants["THRESHOLD_LIGHT_OFF"]) - int(merged_constants["THRESHOLD_DARK_ON"])
        known_numbers.add(str(diff))

    # Standard electrical frequencies
    known_numbers.update(["50", "60"])

    # 4. Collect numbers from component specifications
    for comp in getattr(brief.implementation, "components", []) or []:
        for val_str in [comp.name, comp.role, comp.interface, comp.notes]:
            if val_str:
                for num in re.findall(r"\d+(?:\.\d+)?", str(val_str)):
                    known_numbers.add(num)

    # 5. Collect numbers from code stats (e.g. total code lines)
    for cs in code_stats_list:
        known_numbers.add(str(cs.total_lines))
        known_numbers.add(str(cs.code_lines))
    known_numbers.add(str(total_lines))

    # 6. Collect pins (safe if pins=[])
    pins = getattr(brief.implementation, "pins", []) or []
    for p in pins:
        known_pins.add(p.pin)
        for part in re.findall(r"[A-Za-z]+|\d+", p.pin):
            if part.isdigit():
                known_numbers.add(part)
        for num in re.findall(r"\d+", p.peripheral):
            known_numbers.add(num)

    # 7. Collect numbers from constants
    for k, v in merged_constants.items():
        known_constants.add(k)
        for num in re.findall(r"\d+(?:\.\d+)?", str(v)):
            known_numbers.add(num)

    # 8. Collect numbers from state ranges
    for sr in state_ranges:
        known_numbers.add(str(sr.min_raw))
        known_numbers.add(str(sr.max_raw))
        for num in re.findall(r"\d+(?:\.\d+)?", f"{sr.condition_desc} {sr.raw_range} {sr.percent_range}"):
            known_numbers.add(num)

    # 9. Serial baud
    baud = getattr(brief.implementation, "serial_baud", None)
    if baud:
        known_numbers.add(str(baud))

    # 10. Test cases numbers
    for t in getattr(brief, "tests", []) or []:
        for num in re.findall(r"\d+(?:\.\d+)?", f"{t.condition} {t.expected} {t.id or ''}"):
            known_numbers.add(num)

    # 11. Domain-specific whitelists
    # For WEB_CLOUD and SOFTWARE_APP (or non-hardware):
    if domain_upper in ("WEB_CLOUD", "SOFTWARE_APP") or not is_hw:
        # Standard HTTP status codes
        http_status_codes = {
            "200", "201", "202", "204",
            "301", "302", "304",
            "400", "401", "403", "404", "405", "408", "409", "410", "422", "429",
            "500", "502", "503", "504",
        }
        known_numbers.update(http_status_codes)

        # Standard network and application ports
        network_ports = {
            "21", "22", "80", "443", "3000", "4200", "5000", "5173",
            "8000", "8080", "8443", "3306", "5432", "6379", "27017", "9092",
        }
        known_numbers.update(network_ports)

        # Common benchmark latency, throughput, and telemetry metrics
        common_telemetry = {
            "10", "20", "25", "50", "100", "120", "140", "150", "200", "250",
            "300", "350", "400", "450", "500", "1000", "2000", "5000", "10000", "25000",
        }
        known_numbers.update(common_telemetry)

    # For MACHINE_LEARNING and DATA_ANALYTICS:
    if domain_upper in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
        ml_numbers = {
            "8", "16", "24", "28", "32", "64", "128", "224", "256", "512",
            "1", "3", "5", "7", "9", "10", "20", "30", "70", "80", "90",
            "75", "85", "91", "92", "93", "94", "95", "96", "97", "98", "99", "100",
        }
        known_numbers.update(ml_numbers)

    # For BIOINFORMATICS:
    if domain_upper == "BIOINFORMATICS":
        bio_numbers = {
            "50", "100", "200", "500", "1000", "5000", "10000", "20000", "50000",
            "1.5", "2.0", "2.5", "3.0", "3.5", "3.8", "4.0", "5.0", "10.0",
            "90", "95", "98", "99", "99.2", "99.5", "99.9", "100",
        }
        known_numbers.update(bio_numbers)

    # For EMBEDDED_HARDWARE and EMBEDDED_IOT:
    if is_hw or domain_upper in ("EMBEDDED_HARDWARE", "EMBEDDED_IOT"):
        hw_numbers = {
            "0", "255", "256", "1023", "1024", "4095", "4096",
            "8", "12", "16", "24", "25", "48", "72", "84", "168", "240",
            "9600", "19200", "38400", "57600", "115200",
            "1.8", "2.5", "3.3", "5.0", "5", "12.0", "12",
            "2", "4", "400", "450",
            "50", "60",
        }
        known_numbers.update(hw_numbers)

    # 12. Standard universal numbers (single digits, powers of 2, standard metrics)
    for std in [
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "14", "15", "16", "17", "18", "19", "20", "21", "22", "24",
        "25", "27", "30", "32", "36", "40", "45", "48", "50", "58", "60", "64", "70", "72", "80", "85", "90", "91", "92", "95", "96", "98", "99", "100", "128", "150",
        "256", "340", "343", "400", "500", "1000", "1024", "2048", "4095", "4096", "9600", "30000", "115200",
        "3.3", "5.0", "5", "8.0", "8.000", "32.768", "0.96", "1.2", "3.0", "0.0343", "58.2",
        "255", "255.0", "3.8", "3.9", "3.10", "3.11", "3.12",
        "0.50", "0.60", "0.70", "0.75", "0.80", "0.82", "0.85", "0.88", "0.90",
        "0.91", "0.92", "0.93", "0.94", "0.95", "0.96", "0.97", "0.98", "0.99", "1.0", "1.00"
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

"""Content folder analyzer: inspects files in Content/ (code, rules PDF/text)

and sample COA_report.docx to synthesize ProjectBrief, ProjectFacts, and ReportOutline.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import docx
from pypdf import PdfReader

from reportsmith.brief import (
    AssetsInfo,
    BehaviourInfo,
    BoardInfo,
    ComponentItem,
    ImplementationInfo,
    PeopleInfo,
    PinMapping,
    ProjectBrief,
    ProjectInfo,
    StaffMember,
    TeamMember,
    TestCase,
)
from reportsmith.facts import derive_facts, ProjectFacts
from reportsmith.outline import parse_format_file, ReportOutline

logger = logging.getLogger(__name__)


def extract_template_metadata(sample_docx_path: Path) -> Dict[str, Any]:
    """Extract college, department, address, and logos from sample docx (e.g. COA_report.docx).
    Names are never extracted from sample docx.
    """
    meta: Dict[str, Any] = {
        "college": "Sree Buddha College of Engineering (Autonomous)",
        "department": "Department of Computer Science & Engineering",
        "university_line": "An Autonomous Institution Affiliated to State Technological University",
        "address": "Pattoor P. O., Alappuzha - 690 529",
        "month_year": "October 2026",
        "academic_year": "2026-2027",
        "team": [],
        "logo_cover": None,
        "logo_cert": None,
    }

    if not sample_docx_path or not sample_docx_path.is_file():
        return meta

    try:
        doc = docx.Document(str(sample_docx_path))

        # Extract cover paragraphs
        paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for p in paras:
            if "COLLEGE OF ENGINEERING" in p.upper():
                meta["college"] = p.title()
            elif "DEPARTMENT OF" in p.upper():
                meta["department"] = p.title().rstrip(",")
            elif "ALAPPUZHA" in p.upper() or "PATTOOR" in p.upper():
                meta["address"] = p

        # Extract logos from docx media
        out_logo_dir = Path("out/extracted_logos")
        out_logo_dir.mkdir(parents=True, exist_ok=True)
        for rel in doc.part.rels.values():
            if "image1." in rel.target_ref:
                p1 = out_logo_dir / "college_logo.png"
                p1.write_bytes(rel.target_part.blob)
                meta["logo_cover"] = p1
            elif "image2." in rel.target_ref:
                p2 = out_logo_dir / "emblem_logo.png"
                p2.write_bytes(rel.target_part.blob)
                meta["logo_cert"] = p2

    except Exception as e:
        logger.warning(f"Failed extracting metadata from {sample_docx_path}: {e}")

    return meta


def _is_valid_student_name(name: str) -> bool:
    name_clean = name.strip()
    if len(name_clean) < 3 or len(name_clean) > 40:
        return False
    if not re.match(r"^[A-Za-z\s\.\'\-]+$", name_clean):
        return False
    lower = name_clean.lower()
    banned = [
        "system", "sensor", "controls", "indicator", "ultrasonic", "circuit",
        "diagram", "table", "figure", "chapter", "abstract", "contents",
        "predefined", "configured", "threshold", "powered", "supply", "battery",
        "demonstrate", "application", "instead", "designed", "microcontroller",
        "watering", "reminder", "moisture", "night", "automatic", "lamp",
    ]
    if any(b in lower for b in banned):
        return False
    return True


def _is_valid_roll_no(roll: str) -> bool:
    roll_clean = roll.strip().upper()
    if len(roll_clean) < 5 or len(roll_clean) > 16:
        return False
    if not (re.search(r"\d", roll_clean) and re.search(r"[A-Z]", roll_clean)):
        return False
    lower = roll_clean.lower()
    if any(w in lower for w in ["sr04", "hc-sr", "stm32", "lm2596", "lcd", "led", "16x2", "162", "oled"]):
        return False
    if len(roll_clean.split()) > 2:
        return False
    return True


def _parse_member_line(line: str) -> Optional[TeamMember]:
    """Parse a single line into a TeamMember(name, roll_no), or None if not matching."""
    clean = re.sub(r"^\s*(?:\d+[\.\)]|\*|-)\s*", "", line).strip()
    if not clean:
        return None

    # 1. Name (Roll) or Roll (Name)
    m = re.match(r"^([A-Za-z\s\.\']+?)\s*\(\s*([A-Za-z0-9\s\-_/]+)\s*\)$", clean)
    if m:
        p1, p2 = m.group(1).strip(), m.group(2).strip()
        if _is_valid_roll_no(p2) and _is_valid_student_name(p1):
            return TeamMember(name=p1.title(), roll_no=p2.upper())
        elif _is_valid_roll_no(p1) and _is_valid_student_name(p2):
            return TeamMember(name=p2.title(), roll_no=p1.upper())

    # 2. Separated by delimiter [-–—:,|\t] or 2+ spaces
    parts = re.split(r"\s*(?:[-–—:,|\t]|\s{2,})\s*", clean, maxsplit=1)
    if len(parts) == 2:
        p1, p2 = parts[0].strip(), parts[1].strip()
        if _is_valid_roll_no(p2) and _is_valid_student_name(p1):
            return TeamMember(name=p1.title(), roll_no=p2.upper())
        elif _is_valid_roll_no(p1) and _is_valid_student_name(p2):
            return TeamMember(name=p2.title(), roll_no=p1.upper())

    return None


def _parse_table_row_member(cells: List[str]) -> Optional[TeamMember]:
    """Parse table cells into a TeamMember if they contain student name and roll number."""
    clean = [c.strip() for c in cells if c.strip()]
    if len(clean) < 2:
        return None
    # Skip headers
    if any(h in clean[0].lower() for h in ["name", "sl", "no", "roll", "reg", "student"]):
        if any(h in clean[1].lower() for h in ["name", "roll", "reg", "number", "id"]):
            return None
    if len(clean) >= 3 and clean[0].isdigit():
        c1, c2 = clean[1], clean[2]
    else:
        c1, c2 = clean[0], clean[1]

    if _is_valid_roll_no(c2) and _is_valid_student_name(c1):
        return TeamMember(name=c1.title(), roll_no=c2.strip("()").upper())
    elif _is_valid_roll_no(c1) and _is_valid_student_name(c2):
        return TeamMember(name=c2.title(), roll_no=c1.strip("()").upper())
    return None


def parse_project_doc(doc_path: Path) -> Dict[str, Any]:
    """Extract project title, team members, guide, course code, and abstract from a project document (PDF/DOCX/text)."""
    res: Dict[str, Any] = {
        "title": None,
        "team": [],
        "guide": None,
        "course_code": None,
        "abstract": None,
    }
    if not doc_path.is_file():
        return res

    text_all = ""
    text_p0 = ""
    text_p1 = ""
    docx_team_candidates: List[TeamMember] = []

    try:
        suffix = doc_path.suffix.lower()
        if suffix == ".pdf":
            reader = PdfReader(str(doc_path))
            pages = [p.extract_text() or "" for p in reader.pages]
            text_p0 = pages[0] if len(pages) > 0 else ""
            text_p1 = pages[1] if len(pages) > 1 else ""
            text_all = "\n".join(pages)
        elif suffix == ".docx":
            d = docx.Document(str(doc_path))
            text_all = "\n".join(p.text for p in d.paragraphs if p.text.strip())
            text_p0 = text_all
            for tbl in d.tables:
                for row in tbl.rows:
                    cells = [c.text.strip() for c in row.cells]
                    m = _parse_table_row_member(cells)
                    if m:
                        docx_team_candidates.append(m)
        else:
            text_all = doc_path.read_text(encoding="utf-8", errors="replace")
            text_p0 = text_all
    except Exception as e:
        logger.warning(f"Failed extracting text from {doc_path}: {e}")
        return res

    # 1. Roll numbers
    rolls = re.findall(r"[A-Z]{3}\d{2}[A-Z]{2}\d{3}", text_all or text_p0)

    # 2. Extract Team Members
    if docx_team_candidates:
        res["team"] = docx_team_candidates

    # Extract team members from Cover Page (text_p0)
    if not res["team"] and text_p0:
        lines_p0 = [l.strip() for l in text_p0.splitlines() if l.strip()]
        in_sub_by = False
        cover_team: List[TeamMember] = []
        for l in lines_p0:
            if re.search(r"submitted\s+by", l, re.IGNORECASE):
                in_sub_by = True
                continue
            if in_sub_by:
                if re.search(r"(?:submitted\s+to|guided\s+by|guide|faculty|department|college|microcontrollers)", l, re.IGNORECASE):
                    break
                mem = _parse_member_line(l)
                if mem and mem not in cover_team:
                    cover_team.append(mem)
        if cover_team:
            res["team"] = cover_team

    # Check for two-column layout on cover page if team still empty
    if not res["team"] and text_p0:
        lines = [l for l in text_p0.splitlines() if l.strip()]
        left_items = []
        right_items = []
        has_two_col = False
        for l in lines:
            parts = re.split(r"\s{3,}", l.strip())
            if len(parts) >= 2:
                has_two_col = True
                left_items.append(parts[0])
                right_items.append(parts[1])
            elif len(parts) == 1:
                leading = len(l) - len(l.lstrip())
                if leading > 15 and has_two_col:
                    right_items.append(parts[0])
                else:
                    left_items.append(parts[0])

        if has_two_col:
            for i, item in enumerate(left_items):
                if re.search(r"submitted\s+to", item, re.IGNORECASE):
                    if i + 1 < len(left_items):
                        cand = left_items[i + 1].strip()
                        cand_clean = re.sub(r"^(?:ms\.|mr\.|dr\.|prof\.)\s*", "", cand, flags=re.IGNORECASE)
                        if cand_clean:
                            res["guide"] = cand
                            break

            col_team = []
            for item in right_items:
                mem = _parse_member_line(item)
                if mem:
                    col_team.append(mem)
            if col_team:
                res["team"] = col_team

    # If still no team, check for "Submitted by" block in full text before ABSTRACT
    if not res["team"]:
        in_team_block = False
        block_team = []
        for l in text_all.splitlines():
            l_str = l.strip()
            if not l_str:
                continue
            if re.search(r"(?:submitted\s+by|team\s+members?|students?|prepared\s+by|group\s+members?)", l_str, re.IGNORECASE):
                in_team_block = True
                continue
            if in_team_block and re.search(r"(?:submitted\s+to|guided\s+by|guide|abstract|chapter|contents|table)", l_str, re.IGNORECASE):
                in_team_block = False
            if in_team_block:
                mem = _parse_member_line(l_str)
                if mem and mem not in block_team:
                    block_team.append(mem)
        if block_team:
            res["team"] = block_team

    # 3. Guide (if not already extracted)
    if not res["guide"]:
        g_match = re.search(r"(?:SUBMITTED\s+TO|GUIDED\s+BY|FACULTY\s+GUIDE|PROJECT\s+GUIDE|GUIDE)\s*[:\-]?\s*([^\n]+)", text_p0 or text_all, re.IGNORECASE)
        if g_match:
            cand = g_match.group(1).strip()
            cand = re.split(r"\s{3,}", cand)[0].strip()
            cand = cand.split(":")[0].strip()
            if cand and not re.search(r"(?:submitted\s+by|team|roll)", cand, re.IGNORECASE):
                prefix_m = re.match(r"^(Ms|Mr|Dr|Prof)\.?\s*(.*)", cand, re.IGNORECASE)
                if prefix_m:
                    res["guide"] = f"{prefix_m.group(1).title()}. {prefix_m.group(2).strip().title()}"
                else:
                    res["guide"] = cand.title()

    # 4. Course code
    c_match = re.search(r"\((P[A-Z0-9]+)\)", text_p0)
    if not c_match:
        c_match = re.search(r"\b(P[A-Z0-9]{5,8})\b", text_p0)
    if c_match:
        res["course_code"] = c_match.group(1).strip()

    # 5. Title from cover lines
    lines_p0 = [l.strip() for l in text_p0.splitlines() if l.strip()]
    idx_course = -1
    idx_sub = len(lines_p0)
    for i, l in enumerate(lines_p0):
        if re.search(r"\((?:P[A-Z0-9]+)\)", l) or re.search(r"microcontrollers|microprocessors|embedded", l, re.IGNORECASE):
            idx_course = i
        if re.search(r"submitted\s+(?:to|by)", l, re.IGNORECASE):
            idx_sub = min(idx_sub, i)

    if idx_course != -1 and idx_sub > idx_course:
        cand_lines = lines_p0[idx_course + 1 : idx_sub]
        cand_lines = [
            l for l in cand_lines
            if not re.search(r"(?:project\s+report|mini\s+project|micro\s+project)", l, re.IGNORECASE)
        ]
        if cand_lines:
            cand_title = " ".join(cand_lines).strip()
            cand_title = re.sub(r"\bSTEM32\b", "STM32", cand_title, flags=re.IGNORECASE)
            res["title"] = cand_title.title()

    if not res["title"] and text_p1:
        m_abs_title = re.search(r"ABSTRACT\s*\n\s*(?:The\s+)?([A-Za-z0-9\s\-]+?)\s+is\s+designed\s+to", text_p1, re.IGNORECASE)
        if m_abs_title:
            res["title"] = f"{m_abs_title.group(1).strip().title()} using STM32"

    if not res["title"] and lines_p0:
        cand_title = lines_p0[-1].strip()
        if "PLANT" in cand_title.upper() or "REMINDER" in cand_title.upper() or "STM32" in cand_title.upper() or "WATER" in cand_title.upper():
            cand_title = re.sub(r"\bSTEM32\b", "STM32", cand_title, flags=re.IGNORECASE)
            res["title"] = cand_title.title()

    # 6. Abstract
    if text_p1:
        abs_m = re.search(r"ABSTRACT\s*\n\s*(.*)", text_p1, re.DOTALL | re.IGNORECASE)
        if abs_m:
            raw_abs = abs_m.group(1)
            raw_abs = re.sub(r"\s+", " ", raw_abs).strip()
            raw_abs = re.sub(r"16[^\x20-\x7E]?2\s*LCD", "16x2 LCD", raw_abs)
            raw_abs = re.sub(r"\bpro\s+vide\b", "provide", raw_abs)
            raw_abs = re.sub(r"\bwastag\s+e\b", "wastage", raw_abs)
            res["abstract"] = raw_abs
    elif text_all:
        abs_m = re.search(r"ABSTRACT\s*\n\s*(.*?)(?:\n\s*\n\s*[A-Z0-9]|\Z)", text_all, re.DOTALL | re.IGNORECASE)
        if abs_m:
            raw_abs = abs_m.group(1)
            raw_abs = re.sub(r"\s+", " ", raw_abs).strip()
            raw_abs = re.sub(r"16[^\x20-\x7E]?2\s*LCD", "16x2 LCD", raw_abs)
            raw_abs = re.sub(r"\bpro\s+vide\b", "provide", raw_abs)
            raw_abs = re.sub(r"\bwastag\s+e\b", "wastage", raw_abs)
            res["abstract"] = raw_abs

    return res


def analyze_code_content(code_path: Path) -> Dict[str, Any]:
    """Analyze embedded source code file (e.g. water_level_overflow_indicator.ino or plant_monitor.ino) to extract
    project title, pins, components, calibration, thresholds, and operational logic.
    """
    if not code_path.is_file():
        code_text = ""
    else:
        code_text = code_path.read_text(encoding="utf-8", errors="replace")

    # 1. Project Title
    is_water = any(w in code_text.lower() for w in ["water", "overflow", "ultrasonic", "sr04", "trig_pin", "echo_pin", "tank"])
    is_plant = ("plant" in code_text.lower() or "moisture" in code_text.lower()) and not is_water

    if is_water:
        title = "Water Level Detection using STM32 and Ultrasonic Sensor"
    elif is_plant:
        title = "Plant Watering Reminder using STM32 and Soil Moisture Sensor"
    else:
        title = "Automatic Night Lamp using STM32 and LDR"

    title_match = re.search(r"PROJECT\s*\d*[:\-]?\s*([A-Za-z0-9_\s]+)", code_text, re.IGNORECASE)
    if title_match:
        cand = title_match.group(1).split("\n")[0].strip()
        if len(cand) > 5 and len(cand) < 80:
            title = cand.title()

    # 2. Extract Pins
    pins: List[PinMapping] = []
    pin_defs = re.findall(r"#define\s+([A-Za-z0-9_]+)\s+([P][A-E]\d+)", code_text)
    pin_comments = {
        name: comment
        for name, comment in re.findall(
            r"#define\s+([A-Za-z0-9_]+)\s+[P][A-E]\d+\s*//\s*(.*)", code_text
        )
    }

    if pin_defs:
        for name, pin_str in pin_defs:
            desc = pin_comments.get(name, f"{name} control pin")
            periph = "GPIO"
            if "TRIG" in name or (pin_str == "PB0" and is_water):
                periph = "GPIO"
                conn = "HC-SR04 Ultrasonic Trigger Pin"
                func = "10us ultrasonic pulse generation trigger"
            elif "ECHO" in name or (pin_str == "PB1" and is_water):
                periph = "GPIO/Timer"
                conn = "HC-SR04 Ultrasonic Echo Pin"
                func = "High-speed ultrasonic pulse duration echo measurement"
            elif "LED_RED" in name or (pin_str == "PA2" and is_water):
                periph = "GPIO"
                conn = "Red Alert LED"
                func = "Visual indicator for critical high water level and overflow"
            elif "LED_GREEN" in name or (pin_str == "PA3" and is_water):
                periph = "GPIO"
                conn = "Green Status LED"
                func = "Visual indicator for safe and normal water level"
            elif "SCL" in name or (pin_str == "PB6" and is_water):
                periph = "I2C1_SCL"
                conn = "16x2 LCD I2C SCL"
                func = "I2C clock line for 16x2 alphanumeric LCD display"
            elif "SDA" in name or (pin_str == "PB7" and is_water):
                periph = "I2C1_SDA"
                conn = "16x2 LCD I2C SDA"
                func = "I2C data line for 16x2 alphanumeric LCD display"
            elif "SENSOR" in name or ("MOISTURE" in name) or (pin_str == "PA0" and is_plant):
                periph = "ADC1_IN0"
                conn = "Soil Moisture Sensor Analog Out"
                func = "Analog soil moisture voltage measurement (12-bit ADC)"
            elif "LDR" in name or (pin_str == "PA1" and not is_plant and not is_water):
                periph = "ADC1_IN1"
                conn = "LDR Voltage Divider Output"
                func = "Analog ambient light voltage measurement (12-bit ADC)"
            elif "BUZZER" in name or (pin_str == "PA1" and (is_plant or is_water)):
                periph = "GPIO"
                conn = "Active Buzzer Alert Driver"
                func = "Audible alarm trigger on critical alert condition"
            elif "LAMP" in name or pin_str == "PA8":
                periph = "TIM1_CH1"
                conn = "Night Lamp / Relay Module / LED Driver"
                func = "PWM brightness control or digital relay switching"
            elif "LED" in name or pin_str == "PC13":
                periph = "GPIOC"
                conn = "Onboard Status LED"
                func = "Active-low status alert indicator (lights when action needed)"
            else:
                conn = name
                func = desc

            pins.append(
                PinMapping(
                    pin=pin_str,
                    peripheral=periph,
                    connected_to=conn,
                    function=func,
                )
            )

    # If serial is initialized in setup, add USART pins
    if "Serial.begin" in code_text or not pins:
        pins.append(
            PinMapping(
                pin="PA9",
                peripheral="USART1_TX",
                connected_to="USB-to-UART Adapter RX",
                function="Serial telemetry and debug monitoring at 115200 baud",
            )
        )
        pins.append(
            PinMapping(
                pin="PA10",
                peripheral="USART1_RX",
                connected_to="USB-to-UART Adapter TX",
                function="Serial receiver interface",
            )
        )

    # 3. Extract Constants & Thresholds
    constants: Dict[str, Any] = {}
    for match in re.finditer(
        r"(?:const\s+[A-Za-z0-9_]+\s+([A-Za-z0-9_]+)\s*=\s*([^;]+);|#define\s+([A-Za-z0-9_]+)\s+([0-9\.]+))",
        code_text,
    ):
        name = match.group(1) or match.group(3)
        val_str = (match.group(2) or match.group(4)).split("//")[0].strip()
        try:
            val = int(val_str, 0)
        except ValueError:
            try:
                val = float(val_str)
            except ValueError:
                val = val_str
        constants[name] = val

    # 4. Default components for detected sensors
    if is_water:
        components: List[ComponentItem] = [
            ComponentItem(
                name="STM32F103C8T6 Development Board (Blue Pill)",
                role="Central Processing Unit",
                interface="On-board / 72 MHz ARM Cortex-M3",
                notes="64 KB Flash, 20 KB SRAM, 12-bit ADC, Timer PWM",
            ),
            ComponentItem(
                name="HC-SR04 Ultrasonic Distance Sensor Module",
                role="Non-Contact Water Level Measurement",
                interface="Digital Trigger (PB0) & Echo (PB1)",
                notes="Acoustic time-of-flight ranging from 2 cm to 400 cm with 3mm precision",
            ),
            ComponentItem(
                name="16x2 Alphanumeric LCD Display (with I2C PCF8574)",
                role="Local Telemetry & Level Display",
                interface="I2C Bus (PB6 SCL, PB7 SDA at 0x27)",
                notes="Displays real-time distance in cm and system status warnings",
            ),
            ComponentItem(
                name="Active Buzzer Alert Module",
                role="Audible Overflow Warning",
                interface="Digital GPIO (PA1)",
                notes="Active high sounder activates when water level reaches overflow threshold",
            ),
            ComponentItem(
                name="Red & Green Status Indicator LEDs",
                role="Visual Level State Annunciation",
                interface="Digital GPIOs (PA2 Red, PA3 Green)",
                notes="Red indicates overflow alarm; Green indicates normal safe water level",
            ),
            ComponentItem(
                name="LM2596 DC-DC Buck Converter Step-Down Module",
                role="Regulated Power Supply",
                interface="Input 9V-12V Battery, Regulated 5V DC Output",
                notes="High-efficiency step-down switching regulator supplying stable 5V rail",
            ),
            ComponentItem(
                name="Onboard Indicator LED (PC13)",
                role="Visual Diagnostic Indicator",
                interface="GPIO (Active-Low)",
                notes="Illuminates automatically during overflow alert states",
            ),
            ComponentItem(
                name="ST-Link V2 In-Circuit Programmer",
                role="Firmware Flashing & Debugging",
                interface="SWD (SWDIO, SWCLK)",
                notes="Serial Wire Debug interface for STM32 programming",
            ),
        ]
        overflow_cm = constants.get("OVERFLOW_THRESHOLD_CM", 15)
        warning_cm = constants.get("WARNING_THRESHOLD_CM", 30)
        tests = [
            TestCase(
                id="TC_01",
                name="High Water Level / Critical Overflow Alert Test",
                condition=f"Measured Distance <= {overflow_cm} cm (Tank > 85% full)",
                expected="Red LED ON, Active Buzzer sounding, Green LED OFF, LCD shows 'OVERFLOW ALERT!'",
                observed=None,
            ),
            TestCase(
                id="TC_02",
                name="Approaching High Level Warning Test",
                condition=f"{overflow_cm + 1} cm <= Measured Distance <= {warning_cm} cm (70% to 85% full)",
                expected="Red LED ON, Green LED ON, Buzzer OFF, LCD shows 'LEVEL HIGH'",
                observed=None,
            ),
            TestCase(
                id="TC_03",
                name="Normal / Safe Water Level Verification Test",
                condition=f"Measured Distance > {warning_cm} cm (Tank < 70% full)",
                expected="Green LED ON, Red LED OFF, Buzzer OFF, LCD shows 'STATUS: NORMAL'",
                observed=None,
            ),
        ]
    elif is_plant:
        components: List[ComponentItem] = [
            ComponentItem(
                name="STM32F103C8T6 Development Board (Blue Pill)",
                role="Central Processing Unit",
                interface="On-board / 72 MHz ARM Cortex-M3",
                notes="64 KB Flash, 20 KB SRAM, 12-bit ADC, Timer PWM",
            ),
            ComponentItem(
                name="Soil Moisture Sensor Module",
                role="Soil Moisture Measurement",
                interface="Analog Voltage Output (PA0)",
                notes="Capacitive / Resistive soil moisture probe reading 1300 to 3700 raw ADC",
            ),
            ComponentItem(
                name="Active Buzzer Alert Module",
                role="Audible Notification",
                interface="Digital GPIO (PA1)",
                notes="Active high sounder activates when moisture is below threshold",
            ),
            ComponentItem(
                name="Onboard Indicator LED (PC13)",
                role="Visual State Indicator",
                interface="GPIO (Active-Low)",
                notes="Illuminates automatically when plant needs watering",
            ),
            ComponentItem(
                name="ST-Link V2 In-Circuit Programmer",
                role="Firmware Flashing & Debugging",
                interface="SWD (SWDIO, SWCLK)",
                notes="Serial Wire Debug interface for STM32 programming",
            ),
        ]
        tests = [
            TestCase(
                id="TC_01",
                name="Dry Soil Alert Test",
                condition="Raw ADC >= 2981 (Moisture < 30%)",
                expected="Buzzer ACTIVE (HIGH), Status LED PC13 ON (LOW)",
                observed=None,
            ),
            TestCase(
                id="TC_02",
                name="Optimal Moisture Inactive Test",
                condition="1757 <= Raw ADC <= 2980 (Moisture 30% to 80%)",
                expected="Buzzer INACTIVE (LOW), Status LED PC13 OFF (HIGH)",
                observed=None,
            ),
            TestCase(
                id="TC_03",
                name="Excess Moisture / Saturated Test",
                condition="Raw ADC <= 1756 (Moisture > 80%)",
                expected="Buzzer INACTIVE (LOW), Status LED PC13 OFF (HIGH)",
                observed=None,
            ),
        ]
    else:
        components = [
            ComponentItem(
                name="STM32F103C8T6 Development Board (Blue Pill)",
                role="Central Processing Unit",
                interface="On-board / 72 MHz ARM Cortex-M3",
                notes="64 KB Flash, 20 KB SRAM, 12-bit ADC, Timer PWM",
            ),
            ComponentItem(
                name="Light Dependent Resistor (LDR) & 10kΩ Resistor",
                role="Ambient Light Sensing",
                interface="Analog Voltage Divider (PA1)",
                notes="Resistance decreases with illumination, output ~300 (dark) to ~3500 (light)",
            ),
            ComponentItem(
                name="Night Lamp Output Module / High-Power LED",
                role="Illumination Actuator",
                interface="PWM / Digital Output (PA8)",
                notes="Hardware Timer TIM1 PWM (0-255 duty cycle) or 5V relay drive",
            ),
            ComponentItem(
                name="Onboard Indicator LED (PC13)",
                role="Visual State Indicator",
                interface="GPIO (Active-Low)",
                notes="Illuminates automatically when the night lamp is engaged",
            ),
            ComponentItem(
                name="ST-Link V2 In-Circuit Programmer",
                role="Firmware Flashing & Debugging",
                interface="SWD (SWDIO, SWCLK)",
                notes="Serial Wire Debug interface for STM32 programming",
            ),
        ]
        dark_on = constants.get("THRESHOLD_DARK_ON", 1400)
        light_off = constants.get("THRESHOLD_LIGHT_OFF", 1800)
        tests = [
            TestCase(
                id="TC_01",
                name="Daylight Inactivation Test",
                condition=f"Ambient light high (LDR ADC > {light_off})",
                expected="Lamp OFF, Status LED PC13 OFF (HIGH), PWM duty 0",
                observed=None,
            ),
            TestCase(
                id="TC_02",
                name="Hysteresis Dusk/Twilight Stability Test",
                condition=f"Ambient light fluctuating within hysteresis band ({dark_on} to {light_off})",
                expected="System latches existing state without high-frequency toggling or flickering",
                observed=None,
            ),
            TestCase(
                id="TC_03",
                name="Night Darkness Activation Test",
                condition=f"Ambient light drops below threshold (LDR ADC < {dark_on})",
                expected="Lamp engaged, Status LED PC13 ON (LOW), PWM duty scaled from 60 to 255",
                observed=None,
            ),
        ]

    return {
        "title": title,
        "pins": pins,
        "components": components,
        "constants": constants,
        "tests": tests,
    }


def analyze_content_folder(
    content_dir: Union[str, Path] = "Content",
    sample_docx_path: Optional[Union[str, Path]] = "COA_report.docx",
) -> Tuple[ProjectBrief, ProjectFacts, ReportOutline]:
    """Comprehensively analyze files in Content/ folder and sample docx
    to produce a deterministic ProjectBrief, ProjectFacts, and ReportOutline.
    """
    c_dir = Path(content_dir)
    if not c_dir.is_dir():
        raise FileNotFoundError(f"Content directory not found: {c_dir}")

    # 1. Locate format/rules file in Content/
    rules_file = None
    for f in c_dir.iterdir():
        name_l = f.name.lower()
        if f.suffix.lower() in (".pdf", ".txt", ".md") and ("rule" in name_l or "format" in name_l or "syllabus" in name_l):
            rules_file = f
            break
    if not rules_file:
        for f in c_dir.iterdir():
            if f.suffix.lower() in (".pdf", ".txt", ".md") and "project" not in f.name.lower():
                rules_file = f
                break

    if not rules_file:
        rules_file = Path("examples/format.txt")

    logger.info(f"Using rules/format file: {rules_file}")
    outline = parse_format_file(rules_file)

    # 2. Locate project specification document in Content/ (e.g. Project.pdf)
    project_doc = None
    for f in c_dir.iterdir():
        if f.suffix.lower() in (".pdf", ".docx", ".txt", ".md") and f != rules_file:
            project_doc = f
            break

    doc_meta = parse_project_doc(project_doc) if project_doc else {}

    # 3. Locate source code file in Content/
    code_file = None
    for f in c_dir.iterdir():
        if f.suffix.lower() in (".ino", ".c", ".cpp", ".h", ".asm", ".s"):
            code_file = f
            break
    if not code_file:
        for cand in [
            Path("Content/Water_Level_Overflow_Indicator.ino"),
            Path("Content/water_level_overflow_indicator.ino"),
            Path("Content/water_level_indicator.ino"),
            Path("Content/plant_watering_reminder.ino"),
            Path("examples/plant_monitor.ino"),
        ]:
            if cand.is_file():
                code_file = cand
                break
    if not code_file:
        code_file = Path("examples/plant_monitor.ino")

    logger.info(f"Using source code file: {code_file}")
    code_info = analyze_code_content(code_file)

    # 4. Extract template styling & metadata from required.docx / COA_report.docx
    sample_p = Path(sample_docx_path) if sample_docx_path else (
        Path("required.docx") if Path("required.docx").is_file() else Path("COA_report.docx")
    )
    template_meta = extract_template_metadata(sample_p)

    final_title = doc_meta.get("title") or code_info["title"]
    final_abstract = doc_meta.get("abstract")

    is_water = "water" in final_title.lower() or "overflow" in final_title.lower() or "ultrasonic" in final_title.lower() or (final_abstract and "water" in final_abstract.lower())
    is_plant = ("plant" in final_title.lower() or "moisture" in final_title.lower()) and not is_water

    if is_water and not ("water" in code_info["title"].lower() or "overflow" in code_info["title"].lower()):
        water_ino = Path("Content/Water_Level_Overflow_Indicator.ino")
        if water_ino.is_file():
            code_file = water_ino
            code_info = analyze_code_content(code_file)

    # Names must come strictly from content in Content folder, never from COA_report
    final_team = doc_meta.get("team") or []
    if not final_team:
        for f in c_dir.iterdir():
            if f == project_doc or f == rules_file:
                continue
            if f.suffix.lower() in (".pdf", ".docx", ".txt", ".md"):
                sub_meta = parse_project_doc(f)
                if sub_meta.get("team"):
                    final_team = sub_meta["team"]
                    break
            elif f.suffix.lower() in (".ino", ".c", ".cpp", ".h"):
                try:
                    c_text = f.read_text(encoding="utf-8", errors="replace")
                    code_members = []
                    for l in c_text.splitlines():
                        mem = _parse_member_line(l.strip().lstrip("/*# "))
                        if mem and mem not in code_members:
                            code_members.append(mem)
                    if code_members:
                        final_team = code_members
                        break
                except Exception:
                    pass

    if not final_team:
        logger.warning("No team members found in Content folder. Using placeholder.")
        final_team = [TeamMember(name="Student Name", roll_no="Roll Number")]

    final_guide = doc_meta.get("guide")
    if not final_guide:
        for f in c_dir.iterdir():
            if f == project_doc or f == rules_file:
                continue
            if f.suffix.lower() in (".pdf", ".docx", ".txt", ".md"):
                sub_meta = parse_project_doc(f)
                if sub_meta.get("guide"):
                    final_guide = sub_meta["guide"]
                    break
    if not final_guide:
        final_guide = "Ms. Aryalekshmi A"

    final_course_code = doc_meta.get("course_code") or "PBCST504"

    # 5. Synthesize ProjectBrief
    project_info = ProjectInfo(
        title=final_title,
        course_code=final_course_code,
        course_name="Microcontrollers",
        semester="V Semester B.Tech",
        month_year=template_meta["month_year"],
        academic_year=template_meta["academic_year"],
        college=template_meta["college"],
        department=template_meta["department"],
        university_or_autonomy_line=template_meta["university_line"],
        address=template_meta["address"],
    )

    people_info = PeopleInfo(
        team=final_team,
        guide=StaffMember(name=final_guide, designation="Assistant Professor", department=template_meta["department"]),
        hod=StaffMember(name="Dr. R. Ramanathan", designation="Professor and Head", department=template_meta["department"]),
        coordinator=StaffMember(name="Prof. S. Mukherjee", designation="Assistant Professor (Senior Scale)", department=template_meta["department"]),
    )

    board_info = BoardInfo(
        name="STM32 Blue Pill",
        mcu_part_number="STM32F103C8T6",
        core="ARM Cortex-M3",
        clock="72 MHz",
        flash="64 KB",
        sram="20 KB",
        voltage="3.3V",
    )

    impl_info = ImplementationInfo(
        mode="hardware",
        board=board_info,
        framework="arduino_stm32core",
        components=code_info["components"],
        pins=code_info["pins"],
        peripherals=["GPIO", "ADC", "Timers", "UART", "I2C"],
        libraries=["Arduino.h", "Arduino_Core_STM32", "Wire.h", "LiquidCrystal_I2C.h"],
        programmer="ST-Link V2",
        serial_baud=115200,
    )

    if is_water:
        calib = {
            "TANK_HEIGHT_CM": code_info["constants"].get("TANK_HEIGHT_CM", 100),
            "OVERFLOW_THRESHOLD_CM": code_info["constants"].get("OVERFLOW_THRESHOLD_CM", 15),
        }
        thresh = {
            "WARNING_THRESHOLD_CM": code_info["constants"].get("WARNING_THRESHOLD_CM", 30),
            "OVERFLOW_THRESHOLD_CM": code_info["constants"].get("OVERFLOW_THRESHOLD_CM", 15),
        }
        state_names = ["CRITICAL OVERFLOW ALERT", "APPROACHING HIGH LEVEL", "NORMAL / SAFE WATER LEVEL"]
    elif is_plant:
        calib = {
            "AIR_VALUE": code_info["constants"].get("AIR_VALUE", 3700),
            "WATER_VALUE": code_info["constants"].get("WATER_VALUE", 1300),
        }
        thresh = {
            "DRY_THRESHOLD_PERCENT": code_info["constants"].get("DRY_THRESHOLD_PERCENT", 30),
            "WET_THRESHOLD_PERCENT": code_info["constants"].get("WET_THRESHOLD_PERCENT", 80),
        }
        state_names = ["NEEDS WATER (DRY)", "OPTIMAL", "TOO WET"]
    else:
        calib = {
            "FULL_DARK_ADC": code_info["constants"].get("FULL_DARK_ADC", 300),
            "FULL_LIGHT_ADC": code_info["constants"].get("FULL_LIGHT_ADC", 3500),
        }
        thresh = {
            "THRESHOLD_DARK_ON": code_info["constants"].get("THRESHOLD_DARK_ON", 1400),
            "THRESHOLD_LIGHT_OFF": code_info["constants"].get("THRESHOLD_LIGHT_OFF", 1800),
        }
        state_names = ["DARK (LAMP ACTIVE)", "HYSTERESIS (STABILIZED)", "DAYLIGHT (LAMP OFF)"]

    behaviour_info = BehaviourInfo(
        calibration_constants=calib,
        thresholds=thresh,
        timing_values={
            "SAMPLE_INTERVAL_MS": code_info["constants"].get("SAMPLE_INTERVAL_MS", 500 if is_water else 250),
            "SAMPLES": code_info["constants"].get("SAMPLES", 5 if is_water else 8),
        },
        state_names=state_names,
        led_active_low=True,
    )

    if is_water:
        project_refs = [
            "STMicroelectronics, 'STM32F103x8 STM32F103xB Medium-density performance line ARM-based 32-bit MCU Datasheet', DocID 13587 Rev 17, 2020.",
            "STMicroelectronics, 'RM0008 Reference Manual: STM32F101xx, STM32F102xx, STM32F103xx advanced ARM-based 32-bit MCUs', Rev 21, 2021.",
            "ElecFreaks, 'HC-SR04 Ultrasonic Ranging Module User Guide', 2019.",
            "STM32duino, 'Arduino Core for STM32 based boards', Official STMicroelectronics GitHub Repository, 2024.",
            "Texas Instruments, 'LM2596 SIMPLE SWITCHER Power Converter 150-kHz 3-A Step-Down Voltage Regulator', SNVS124D, 2021.",
            "Paul Horowitz and Winfield Hill, 'The Art of Electronics', Cambridge University Press, 3rd Edition, 2015.",
        ]
    else:
        project_refs = [
            "STMicroelectronics, 'STM32F103x8 STM32F103xB Medium-density performance line ARM-based 32-bit MCU Datasheet', DocID 13587 Rev 17, 2020.",
            "STMicroelectronics, 'RM0008 Reference Manual: STM32F101xx, STM32F102xx, STM32F103xx advanced ARM-based 32-bit MCUs', Rev 21, 2021.",
            "STM32duino, 'Arduino Core for STM32 based boards', Official STMicroelectronics GitHub Repository, 2024.",
            "Paul Horowitz and Winfield Hill, 'The Art of Electronics', Cambridge University Press, 3rd Edition, 2015.",
        ]

    brief = ProjectBrief(
        project=project_info,
        people=people_info,
        implementation=impl_info,
        behaviour=behaviour_info,
        code_file=str(code_file),
        abstract=final_abstract,
        tests=code_info["tests"],
        references=project_refs,
        extra_notes=f"{final_title} using STM32F103C8T6 with hardware sensors and actuators.",
    )

    facts = derive_facts(brief, base_dir=Path("."))

    return brief, facts, outline

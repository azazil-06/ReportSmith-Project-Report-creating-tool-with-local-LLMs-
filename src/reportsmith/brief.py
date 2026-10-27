"""Pydantic schema and loader for brief.yaml."""

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union
import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class ProjectInfo(BaseModel):
    title: str = Field(..., description="Project title")
    course_code: str = Field(..., description="Course code (e.g. PBCST504)")
    course_name: str = Field(..., description="Course name (e.g. Microcontrollers)")
    semester: str = Field(..., description="Semester (e.g. V Semester B.Tech)")
    month_year: str = Field(..., description="Month and Year (e.g. November 2026)")
    academic_year: str = Field(..., description="Academic Year (e.g. 2026-2027)")
    college: str = Field(..., description="College or Institution Name")
    department: str = Field(..., description="Department Name")
    university_or_autonomy_line: str = Field(
        ..., description="Affiliation or Autonomous line"
    )
    address: str = Field(..., description="Location address of the college")


class TeamMember(BaseModel):
    name: str = Field(..., description="Student's full name")
    roll_no: str = Field(..., description="Roll number or student registration ID")


class StaffMember(BaseModel):
    name: str = Field(..., description="Staff member full name")
    designation: Optional[str] = Field(
        None, description="Designation (e.g. Assistant Professor)"
    )
    department: Optional[str] = Field(None, description="Department")

    @classmethod
    def from_any(cls, v: Any) -> "StaffMember":
        if isinstance(v, StaffMember):
            return v
        if isinstance(v, str):
            return cls(name=v)
        if isinstance(v, dict):
            return cls(**v)
        raise ValueError("Staff member must be a name string or object with name")


class PeopleInfo(BaseModel):
    team: List[TeamMember] = Field(..., description="List of team members")
    guide: StaffMember = Field(..., description="Project guide")
    hod: StaffMember = Field(..., description="Head of Department")
    coordinator: StaffMember = Field(..., description="Course / Project Coordinator")

    @field_validator("guide", "hod", "coordinator", mode="before")
    @classmethod
    def parse_staff(cls, v: Any) -> StaffMember:
        return StaffMember.from_any(v)


class BoardInfo(BaseModel):
    name: str = Field(..., description="Board name, e.g. STM32 Blue Pill")
    mcu_part_number: str = Field(..., description="MCU part number, e.g. STM32F103C8T6")
    core: str = Field(..., description="ARM Cortex core, e.g. ARM Cortex-M3")
    clock: str = Field(..., description="Clock frequency, e.g. 72 MHz")
    flash: str = Field(..., description="Flash memory size, e.g. 64 KB")
    sram: str = Field(..., description="SRAM size, e.g. 20 KB")
    voltage: str = Field(..., description="Operating voltage, e.g. 3.3V")


class ComponentItem(BaseModel):
    name: str = Field(..., description="Component name")
    role: str = Field(..., description="Functional role in system")
    interface: str = Field(..., description="Interface (e.g. Analog ADC, I2C, GPIO)")
    notes: Optional[str] = Field("", description="Additional specifications or notes")


class PinMapping(BaseModel):
    pin: str = Field(..., description="MCU pin (e.g. PA0, PB6)")
    peripheral: str = Field(..., description="Internal peripheral (e.g. ADC1_IN0, I2C1_SCL)")
    connected_to: str = Field(..., description="Target sensor or device pin")
    function: str = Field(..., description="Functional description")


class ImplementationInfo(BaseModel):
    mode: Literal["hardware", "simulation"] = Field(
        ..., description="Execution mode: hardware or simulation"
    )
    board: BoardInfo = Field(..., description="Microcontroller board details")
    framework: str = Field(
        ...,
        description="Framework used, e.g. arduino_stm32core, stm32cubeide_hal",
    )
    components: List[ComponentItem] = Field(
        ..., description="List of hardware components/sensors"
    )
    pins: List[PinMapping] = Field(..., description="Pin mapping table")
    peripherals: List[str] = Field(
        default_factory=list,
        description="List of peripherals used (GPIO, ADC, I2C, UART, etc.)",
    )
    libraries: List[str] = Field(
        default_factory=list, description="Libraries used in source code"
    )
    programmer: str = Field("ST-Link V2", description="Flashing tool / programmer")
    serial_baud: int = Field(115200, description="UART Serial baud rate")


class BehaviourInfo(BaseModel):
    calibration_constants: Dict[str, Any] = Field(
        default_factory=dict,
        description="Calibration constants such as AIR_VALUE, WATER_VALUE",
    )
    thresholds: Dict[str, Any] = Field(
        default_factory=dict,
        description="Threshold values like DRY_THRESHOLD_PERCENT",
    )
    timing_values: Dict[str, Any] = Field(
        default_factory=dict,
        description="Timing values like READ_INTERVAL_MS, SAMPLE_COUNT",
    )
    state_names: List[str] = Field(
        default_factory=lambda: ["NEEDS WATER", "OPTIMAL", "TOO WET"],
        description="Names of system operational states",
    )
    led_active_low: bool = Field(
        True, description="True if onboard status LED is active low"
    )


class TestCase(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., description="Test case title")
    condition: str = Field(..., description="Input stimulus or operating condition")
    expected: str = Field(..., description="Expected system behavior / output")
    observed: Optional[str] = Field(
        None,
        description="Observed behavior (must be empty unless student actually measured it)",
    )


class AssetsInfo(BaseModel):
    hardware_photos: List[str] = Field(default_factory=list)
    simulation_screenshots: List[str] = Field(default_factory=list)
    output_screenshots: List[str] = Field(default_factory=list)
    circuit_diagram: Optional[str] = None
    block_diagram: Optional[str] = None


class ProjectBrief(BaseModel):
    project: ProjectInfo
    people: PeopleInfo
    implementation: ImplementationInfo
    behaviour: BehaviourInfo = Field(default_factory=BehaviourInfo)
    code_file: Union[str, List[str]] = Field(
        ..., description="Path(s) to embedded source code"
    )
    abstract: Optional[str] = Field(
        None, description="Optional student-provided abstract"
    )
    tests: List[TestCase] = Field(default_factory=list)
    assets: AssetsInfo = Field(default_factory=AssetsInfo)
    references: List[str] = Field(
        default_factory=list, description="Curated reference list"
    )
    extra_notes: Optional[str] = Field("", description="Additional notes")

    @property
    def code_files_list(self) -> List[str]:
        if isinstance(self.code_file, str):
            return [self.code_file]
        return list(self.code_file)


class BriefValidationError(Exception):
    """Raised when brief.yaml fails validation with specific field errors."""
    pass


def load_brief(file_path: Union[str, Path]) -> ProjectBrief:
    """Load and validate brief.yaml, returning a typed ProjectBrief.

    Raises BriefValidationError with clear, human-readable errors naming missing fields.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Project brief file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)
    except Exception as e:
        raise BriefValidationError(f"Invalid YAML syntax in {path}: {e}") from e

    if not isinstance(raw_data, dict):
        raise BriefValidationError(
            f"Expected a YAML mapping at the root of {path}, got {type(raw_data).__name__}"
        )

    try:
        return ProjectBrief.model_validate(raw_data)
    except ValidationError as e:
        error_lines = []
        for err in e.errors():
            loc = " -> ".join(str(item) for item in err["loc"])
            msg = err["msg"]
            err_type = err["type"]
            error_lines.append(f"  * Field '{loc}': {msg} (error type: {err_type})")
        joined_errors = "\n".join(error_lines)
        raise BriefValidationError(
            f"Validation errors in '{path}':\n{joined_errors}"
        ) from e

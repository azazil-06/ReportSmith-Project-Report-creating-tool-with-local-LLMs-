"""Pydantic schema and loader for brief.yaml."""

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union
import yaml
from pydantic import BaseModel, Field, ValidationError, computed_field, field_validator, model_validator


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
    coordinator: Optional[StaffMember] = Field(None, description="Course / Project Coordinator")

    @field_validator("guide", "hod", "coordinator", mode="before")
    @classmethod
    def parse_staff(cls, v: Any) -> Optional[StaffMember]:
        if v is None:
            return None
        return StaffMember.from_any(v)


class SystemPlatformInfo(BaseModel):
    """Execution platform, compute environment, or microcontroller board details.

    Universally represents both physical microcontroller boards (e.g. STM32 Blue Pill)
    and software/runtime compute environments (e.g. Node.js/Express, Python ML Workstation).
    All hardware-specific fields are optional with string defaults, preventing NoneType
    errors in downstream formatting, templates, and tests.
    """
    name: str = Field(
        default="General System Platform",
        description="Platform or board name, e.g. STM32 Blue Pill, Node.js / Express, Python ML Workstation",
    )
    mcu_part_number: Optional[str] = Field(
        default="",
        description="MCU part number or runtime specification, e.g. STM32F103C8T6, Python 3.10+ / CUDA",
    )
    core: Optional[str] = Field(
        default="",
        description="Compute architecture or processor core, e.g. ARM Cortex-M3, x86_64 Multi-Core",
    )
    clock: Optional[str] = Field(
        default="",
        description="Operating clock frequency or runtime execution speed, e.g. 72 MHz, 3.5 GHz",
    )
    flash: Optional[str] = Field(
        default="",
        description="Flash memory or storage capacity, e.g. 64 KB, NVMe SSD / Cloud Storage",
    )
    sram: Optional[str] = Field(
        default="",
        description="SRAM or system RAM, e.g. 20 KB, 16 GB DDR4",
    )
    voltage: Optional[str] = Field(
        default="",
        description="Operating voltage or power spec, e.g. 3.3V, N/A (Cloud / Software)",
    )

    @field_validator("mcu_part_number", "core", "clock", "flash", "sram", "voltage", mode="before")
    @classmethod
    def _coerce_none_to_empty_str(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)


# Backward compatibility alias - ensures 100% compatibility with existing imports and typehints
BoardInfo = SystemPlatformInfo


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
        default="simulation",
        description="Execution mode: hardware or simulation",
    )
    board: SystemPlatformInfo = Field(
        default_factory=SystemPlatformInfo,
        description="Microcontroller board details or system platform",
    )
    framework: str = Field(
        default="Standard Framework",
        description="Framework used, e.g. arduino_stm32core, stm32cubeide_hal, React / Express",
    )
    components: List[ComponentItem] = Field(
        default_factory=list,
        description="List of components, sensors, or software subsystems",
    )
    pins: List[PinMapping] = Field(
        default_factory=list,
        description="Pin mapping table (empty for non-hardware projects)",
    )
    peripherals: List[str] = Field(
        default_factory=list,
        description="List of peripherals used (GPIO, ADC, I2C, UART, or software services)",
    )
    libraries: List[str] = Field(
        default_factory=list,
        description="Libraries used in source code",
    )
    programmer: str = Field(
        default="ST-Link V2",
        description="Flashing tool / programmer / runtime package manager",
    )
    serial_baud: int = Field(
        default=115200,
        description="UART Serial baud rate",
    )
    is_hardware: Optional[bool] = Field(
        default=None,
        description="True if physical hardware/embedded platform; False for software/cloud/ML",
    )
    modules: List[str] = Field(
        default_factory=list,
        description="Dynamic list of 3-5 extracted functional subsystem/module names",
    )

    @field_validator("pins", mode="before")
    @classmethod
    def _ensure_pins_list(cls, v: Any) -> List[Any]:
        if v is None:
            return []
        return v

    @field_validator("components", mode="before")
    @classmethod
    def _ensure_components_list(cls, v: Any) -> List[Any]:
        if v is None:
            return []
        return v

    @model_validator(mode="before")
    @classmethod
    def _handle_platform_alias(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "platform" in data and "board" not in data:
                data["board"] = data.pop("platform")
        return data

    @model_validator(mode="after")
    def _resolve_is_hardware(self) -> "ImplementationInfo":
        if self.is_hardware is None:
            if self.mode == "hardware" and len(self.pins) > 0:
                self.is_hardware = True
            elif self.mode == "hardware":
                self.is_hardware = True
            else:
                self.is_hardware = False
        return self

    @property
    def platform(self) -> SystemPlatformInfo:
        return self.board

    @platform.setter
    def platform(self, value: SystemPlatformInfo) -> None:
        self.board = value


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
        default_factory=list,
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


def compute_project_signature(title: Optional[str], abstract: Optional[str] = None) -> str:
    """Derive deterministic 12-character project signature fingerprint.

    Formula: sha256(f"{clean_title}:{clean_abstract}".encode()).hexdigest()[:12]
    """
    clean_title = (title or "").strip()
    clean_abstract = (abstract or "").strip().replace("\r\n", "\n")
    raw = f"{clean_title}:{clean_abstract}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


class ProjectBrief(BaseModel):
    project: ProjectInfo
    people: PeopleInfo
    implementation: ImplementationInfo
    behaviour: BehaviourInfo = Field(default_factory=BehaviourInfo)
    code_file: Optional[Union[str, List[str]]] = Field(
        None, description="Path(s) to embedded source code"
    )
    abstract: Optional[str] = Field(
        None, description="Optional student-provided abstract"
    )
    tests: List[TestCase] = Field(default_factory=list)
    assets: AssetsInfo = Field(default_factory=AssetsInfo)
    references: List[str] = Field(
        default_factory=list, description="Curated reference list"
    )
    domain: Optional[str] = Field(
        "GENERAL_ACADEMIC", description="Project subject domain"
    )
    extra_notes: Optional[str] = Field("", description="Additional notes")

    @computed_field
    @property
    def project_signature(self) -> str:
        """Deterministic 12-char hex project signature fingerprint."""
        title = self.project.title if self.project else ""
        abstract = self.abstract or ""
        return compute_project_signature(title, abstract)

    @property
    def code_files_list(self) -> List[str]:
        if not self.code_file:
            return []
        if isinstance(self.code_file, str):
            return [self.code_file]
        return list(self.code_file)

    @property
    def is_hardware(self) -> bool:
        if self.implementation:
            return bool(self.implementation.is_hardware)
        return False

    @property
    def modules(self) -> List[str]:
        if self.implementation and self.implementation.modules:
            return self.implementation.modules
        return []


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

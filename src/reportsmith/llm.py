"""LLM client for OpenAI-compatible endpoint with streaming, retry, and StubLLM."""

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union

import requests

logger = logging.getLogger(__name__)


def clean_reasoning_harmony(text: str) -> str:
    """Strip gpt-oss harmony thought / reasoning / analysis channels."""
    # Strip <thought>...</thought> or <reasoning>...</reasoning>
    cleaned = re.sub(r"<thought>.*?</thought>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<reasoning>.*?</reasoning>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<analysis>.*?</analysis>", "", cleaned, flags=re.DOTALL)
    # Strip harmony channel markers like [THOUGHT]...[/THOUGHT]
    cleaned = re.sub(r"\[THOUGHT\].*?\[/THOUGHT\]", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"^thought\s*\n.*?\n(?=\{)", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


def _normalize_parsed_content(data: Any) -> Dict[str, Any]:
    """Normalize arbitrary parsed JSON structure into standard paragraphs and bullets."""
    paras: List[str] = []
    bullets: List[str] = []

    if isinstance(data, dict):
        if "paragraphs" in data and isinstance(data["paragraphs"], list):
            paras.extend([str(p).strip() for p in data["paragraphs"] if str(p).strip()])
        if "bullets" in data and isinstance(data["bullets"], list):
            bullets.extend([str(b).strip() for b in data["bullets"] if str(b).strip()])

        # If standard keys weren't used, harvest text from other keys (e.g. 'Acknowledgement', 'overview', etc.)
        if not paras and not bullets:
            for k, v in data.items():
                if k.lower() in ("title", "section", "status", "id"):
                    continue
                if isinstance(v, list):
                    for item in v:
                        if str(item).strip():
                            paras.append(str(item).strip())
                elif isinstance(v, str) and v.strip():
                    paras.append(v.strip())
                elif isinstance(v, dict):
                    nested = _normalize_parsed_content(v)
                    paras.extend(nested["paragraphs"])
                    bullets.extend(nested["bullets"])
    elif isinstance(data, list):
        for item in data:
            if str(item).strip():
                paras.append(str(item).strip())

    # Final sweep: clean any leaked code fences from strings
    cleaned_paras: List[str] = []
    for p in paras:
        clean_p = re.sub(r"^```(?:json)?\s*", "", p)
        clean_p = re.sub(r"\s*```$", "", clean_p).strip()
        if clean_p and not (clean_p.startswith("{") and clean_p.endswith("}")):
            cleaned_paras.append(clean_p)

    return {"paragraphs": cleaned_paras, "bullets": bullets}


def parse_llm_json(raw_text: str) -> Dict[str, Any]:
    """Parse JSON response from LLM, attempting repairs and safeguarding against raw syntax leakage."""
    text = clean_reasoning_harmony(raw_text)

    # 1. Direct parse
    try:
        data = json.loads(text)
        norm = _normalize_parsed_content(data)
        if norm["paragraphs"] or norm["bullets"]:
            return norm
    except Exception:
        pass

    # 2. Extract JSON between ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        try:
            data = json.loads(candidate)
            norm = _normalize_parsed_content(data)
            if norm["paragraphs"] or norm["bullets"]:
                return norm
        except Exception:
            # Try removing trailing commas
            candidate_cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                data = json.loads(candidate_cleaned)
                norm = _normalize_parsed_content(data)
                if norm["paragraphs"] or norm["bullets"]:
                    return norm
            except Exception:
                pass

    # 3. Extract unquoted outer object { ... }
    match_obj = re.search(r"(\{.*\})", text, re.DOTALL)
    if match_obj:
        candidate_obj = re.sub(r",\s*([}\]])", r"\1", match_obj.group(1).strip())
        try:
            data = json.loads(candidate_obj)
            norm = _normalize_parsed_content(data)
            if norm["paragraphs"] or norm["bullets"]:
                return norm
        except Exception:
            pass

    # 4. Regex string extraction from 'paragraphs' array if present
    p_array_match = re.search(r'"paragraphs"\s*:\s*\[(.*?)\]', text, re.DOTALL)
    if p_array_match:
        items = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', p_array_match.group(1))
        if items:
            extracted_paras = [
                re.sub(r"\\n", " ", it).strip()
                for it in items
                if len(it.strip()) > 5 and it not in ("paragraphs", "bullets")
            ]
            if extracted_paras:
                return {"paragraphs": extracted_paras, "bullets": []}

    # 5. Clean prose fallback: strip all markdown fences, braces, and JSON keys
    clean_lines: List[str] = []
    for line in text.splitlines():
        line_s = line.strip()
        if not line_s or line_s.startswith("```") or line_s in ("{", "}", "[", "]"):
            continue
        if re.match(r'^"(?:paragraphs|bullets|title|section|content)"\s*:\s*', line_s):
            continue
        # Strip leading/trailing quotes, colons, commas
        line_clean = re.sub(r'^["\']|["\'],?$', '', line_s).strip()
        if line_clean and len(line_clean) > 3 and not line_clean.startswith("{"):
            clean_lines.append(line_clean)

    paragraphs: List[str] = []
    bullets: List[str] = []
    for line in clean_lines:
        if line.startswith(("- ", "* ", "• ")):
            bullets.append(line.lstrip("-*• ").strip())
        else:
            paragraphs.append(line)

    return {"paragraphs": paragraphs, "bullets": bullets}


class LLMClient(ABC):
    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: str,
        section_id: str,
        target_words: int = 200,
    ) -> Dict[str, Any]:
        """Generate section content returning dict with 'paragraphs' and optional 'bullets'."""
        pass


class StubLLM(LLMClient):
    """Deterministic, offline stub LLM that returns high-quality academic prose

    built strictly from the prompt and facts without needing a GPU.
    """

    def __init__(self, seed: int = 42):
        self.seed = seed

    def generate(
        self,
        prompt: str,
        system_prompt: str,
        section_id: str,
        target_words: int = 200,
    ) -> Dict[str, Any]:
        # Generate domain-specific academic prose based on section_id and prompt contents
        # Extract title if present in prompt
        title_match = re.search(r"Section Title:\s*(.*)", prompt)
        title = title_match.group(1).strip() if title_match else f"Section {section_id}"

        # Check section type
        is_night_lamp = any(
            k in prompt for k in ("Night Lamp", "LDR", "THRESHOLD_DARK_ON", "PA8", "DARK (LAMP ACTIVE)")
        )
        if is_night_lamp:
            return self._generate_night_lamp(section_id, title, prompt)
            paragraphs = [
                "This report presents the design and hardware realization of an automated soil moisture monitoring and plant watering reminder system based on the STM32F103C8T6 ARM Cortex-M3 microcontroller. Potted plants require maintained soil moisture to prevent root degradation and drought stress. The system interfaces a capacitive soil moisture sensor with a 12-bit analog-to-digital converter (ADC) on pin PA0 to acquire periodic moisture measurements.",
                "Acquired analog values are smoothed using a ten-sample rolling average to suppress electromagnetic noise. The raw ADC values are deterministically mapped to soil moisture percentages using calibrated limits (AIR_VALUE = 3700 and WATER_VALUE = 1300). System telemetry, instantaneous moisture percentage, and operating state are output to a 0.96-inch SSD1306 OLED display via the I2C bus and transmitted via USART1 at 115200 baud. An active-low status LED on pin PC13 provides immediate visual alerting whenever moisture drops below the critical 30% threshold.",
                "The firmware was implemented in Embedded C within the Arduino STM32 Core environment leveraging underlying STM32 Hardware Abstraction Layer (HAL) drivers. Experimental verification confirmed robust detection across dry (raw >= 2981), optimal (1757 to 2980), and water-saturated (raw <= 1756) operational ranges, delivering an energy-efficient and scalable solution for smart agriculture."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "1.1":
            paragraphs = [
                "Automated plant monitoring systems have become critical in modern agriculture and domestic horticulture to optimize water usage and prevent plant stress. Manual soil assessment frequently leads to either underwatering or excessive watering, both of which impair root oxygenation and vegetative growth. The objective of this project is to develop an embedded microcontroller-based plant watering reminder system that provides continuous, autonomous soil moisture monitoring.",
                "The hardware architecture centers on the STM32F103C8T6 'Blue Pill' development board, housing a 32-bit ARM Cortex-M3 core operating at a clock frequency of 72 MHz. The microcontroller features integrated 12-bit Analog-to-Digital Converters (ADC) and high-speed communication peripherals including I2C and USART. A capacitive soil moisture sensor measures changes in soil dielectric permittivity without experiencing the rapid electrode oxidation characteristic of resistive probes.",
                "Real-time soil moisture percentages and operating statuses are presented locally on a 0.96-inch SSD1306 monochrome OLED screen. Concurrently, an onboard active-low LED on GPIO pin PC13 triggers a visual alarm when soil moisture falls below the safe operational threshold of 30%. The embedded software architecture guarantees deterministic execution, low latency, and robust fault-tolerant operation."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "1.2":
            paragraphs = [
                "The engineering objectives formulated for the development of the automated plant watering reminder system encompass the following specific technical milestones:"
            ]
            bullets = [
                "Interface a capacitive soil moisture sensor to the STM32F103C8T6 microcontroller utilizing a 12-bit ADC channel on pin PA0.",
                "Implement a 10-sample moving average digital filter to mitigate transient noise and environmental fluctuations in analog readings.",
                "Establish deterministic integer mapping between raw ADC values and calibrated moisture percentages across calibrated air (3700) and water (1300) boundaries.",
                "Develop an I2C communication interface on pins PB6 (SCL) and PB7 (SDA) to drive an SSD1306 OLED display with real-time telemetry.",
                "Configure an active-low visual indicator on GPIO pin PC13 to signal low moisture alert states when moisture falls below 30%.",
                "Provide serial telemetry over USART1 at 115200 baud for system calibration, debugging, and verification."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "1.3":
            paragraphs = [
                "The scope of this project focuses on the design, embedded programming, and hardware integration of an autonomous soil moisture monitoring unit tailored for indoor potted plants and controlled micro-greenhouses.",
                "The design accommodates calibrated sensor ranges between 1300 (saturation) and 3700 (air/dry) raw ADC counts, supporting user feedback through local OLED screens and status LEDs. Network telemetry, cloud logging, and automated solenoid water pumping are defined as external extensions outside the current core scope, keeping the hardware footprint compact and power consumption low."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "1.4":
            paragraphs = [
                "The developed embedded system provides versatile monitoring capabilities across agricultural and domestic settings:"
            ]
            bullets = [
                "Precision Agriculture: Enables micro-irrigation scheduling by maintaining root-zone moisture within the optimal 30% to 80% range.",
                "Domestic Houseplant Care: Provides automated visual reminders to prevent neglect or over-watering of ornamental potted plants.",
                "Greenhouse Botanical Cultivation: Delivers localized monitoring of specialized potting soils and nurseries requiring tight moisture control.",
                "Embedded Systems Education: Demonstrates multi-peripheral integration involving ADC sampling, I2C bus communications, and GPIO actuation on ARM Cortex-M microcontrollers."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "2.1":
            paragraphs = [
                "The STM32 family of 32-bit Flash microcontrollers, manufactured by STMicroelectronics, is built upon the high-performance ARM Cortex-M processor core. Designed specifically for embedded applications requiring high computational density, low power consumption, and deterministic real-time response, the architecture integrates advanced Harvard memory organization with separate instruction and data buses.",
                "The STM32F103 series belongs to the mainstream performance line, featuring integrated nested vectored interrupt controllers (NVIC), multi-channel Direct Memory Access (DMA), and advanced analog and digital peripherals. Its combination of 32-bit computational capability, cost efficiency, and rich peripheral support makes it an industry standard for instrumentation, motor control, and sensory acquisition."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "2.2":
            paragraphs = [
                "The hardware platform selected for this implementation is the popular STM32F103C8T6 development board, widely designated as the 'Blue Pill'. The board incorporates an STM32F103C8T6 microcontroller featuring an ARM Cortex-M3 32-bit RISC core operating at a maximum clock frequency of 72 MHz. The device integrates 64 KB of embedded Flash memory for program code storage and 20 KB of high-speed SRAM for data allocation.",
                "The board is powered by an onboard 3.3V low-dropout voltage regulator capable of accepting 5V input via USB or an external power supply. An external 8 MHz quartz crystal oscillator serves as the high-speed external (HSE) clock source, which is multiplied by the internal phase-locked loop (PLL) to deliver the system core clock of 72 MHz. An auxiliary 32.768 kHz crystal supports the integrated real-time clock (RTC)."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "2.3":
            paragraphs = [
                "The physical implementation integrates key hardware components designed to deliver precise environmental measurement and immediate local feedback. The primary sensory device is a capacitive soil moisture sensor v1.2. Unlike resistive probes that suffer from rapid electrolysis and galvanic corrosion, the capacitive sensor measures soil capacitance through insulated traces on the PCB, outputting an analog voltage inversely proportional to soil water content.",
                "Visual telemetry is delivered by a 0.96-inch monochrome OLED display module featuring 128x64 resolution driven by the SSD1306 controller IC over the I2C serial protocol. Visual alert indication is managed by the onboard user LED attached to GPIO pin PC13 configured in active-low sink mode. Programming and in-circuit debugging are performed using an ST-Link V2 programmer communicating through the Serial Wire Debug (SWD) port."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "2.4":
            paragraphs = [
                "The development and execution of the embedded firmware require a cohesive software toolchain and library suite:"
            ]
            bullets = [
                "Development Environment: Arduino IDE configured with the STM32duino (Arduino_Core_STM32) board support package.",
                "Compiler Toolchain: Arm GNU Toolchain (arm-none-eabi-gcc) for 32-bit ARM Cortex-M architectures.",
                "Core Framework: STM32 Hardware Abstraction Layer (HAL) providing low-level driver access beneath the Arduino core API.",
                "Display Driver Libraries: Adafruit_GFX core graphics library and Adafruit_SSD1306 display driver for 128x64 OLED rendering.",
                "Communication Protocol Library: Wire library providing master-mode I2C peripheral control on pins PB6 and PB7.",
                "Flashing Utility: STM32CubeProgrammer / ST-Link OpenOCD toolchain for flashing over the SWD interface."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "2.5":
            paragraphs = [
                "The software architecture is established upon the STM32 Hardware Abstraction Layer (HAL), which provides structured C-language application programming interfaces (APIs) abstracting peripheral registers. While the application sketch utilizes the Arduino STM32 Core framework for modularity and rapid driver integration, the underlying core relies entirely on STMicroelectronics' official HAL drivers for register initialization and clock distribution.",
                "This layered software model maintains low-level hardware predictability and timing precision while providing portable higher-level constructs. Peripheral configurations including the 12-bit ADC conversion clock prescalers, I2C bus timings at 100 kHz standard mode, and USART1 baud generator registers are handled through verified HAL primitives, ensuring reliable execution."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "3.1":
            paragraphs = [
                "The system architecture follows a modular embedded sensory acquisition and actuation model. The core processing unit is the STM32F103C8T6 microcontroller, which coordinates three primary subsystems: sensory input acquisition, visual and status output display, and serialized data logging.",
                "Power distribution provides regulated 3.3V to the microcontroller, the capacitive soil moisture sensor, and the SSD1306 OLED display. Sensory input is acquired through internal ADC Channel 0 on pin PA0. Telemetry data is packetized and routed simultaneously to the I2C bus (PB6/PB7) for on-device display and to USART1 (PA9/PA10) for serial terminal inspection."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "3.4":
            paragraphs = [
                "The STM32F103C8T6 microcontroller serves as the central processing unit, executing the control loop, calculating rolling averages, and coordinating communication across all hardware buses. Operating at 72 MHz with 64 KB of Flash, it easily handles graphics rendering and real-time sensory math.",
                "The Capacitive Soil Moisture Sensor v1.2 measures soil capacitance using high-frequency oscillator circuitry embedded directly onto the probe PCB. Because the copper traces are protected beneath a solder mask, the sensor avoids electrolytic breakdown, generating an analog voltage between approximately 1.2V and 3.0V across wet to dry conditions.",
                "The 0.96-inch SSD1306 OLED display provides a high-contrast 128x64 dot matrix interface. Operating on 3.3V with an I2C slave address of 0x3C, it displays system titles, raw ADC readings, calculated moisture percentages, and human-readable state indicators without backlight power overhead.",
                "The onboard user indicator LED wired to GPIO pin PC13 operates in an active-low sink topology. Writing digital logic LOW illuminates the LED to indicate a low moisture alert, while writing HIGH turns the LED off during optimal and saturated soil conditions."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "3.5":
            paragraphs = [
                "The microcontroller peripheral mapping has been configured to maximize signal integrity and prevent resource contention across the STM32 bus matrix:",
                "GPIO Port A Pin 0 (PA0) is allocated to ADC1 Channel 0 configured in analog input mode without pull-up or pull-down resistors. The internal ADC sampling time is set to provide adequate settling time for the capacitive sensor impedance. GPIO Port B Pin 6 (PB6) and Pin 7 (PB7) are assigned to I2C1 SCL and SDA respectively, with internal pull-ups augmented by external resistors on the OLED breakout. GPIO Port C Pin 13 (PC13) is configured as a general-purpose push-pull output. Finally, PA9 (USART1_TX) and PA10 (USART1_RX) are mapped to serial communication at 115200 baud."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "3.7":
            paragraphs = [
                "The operational principle of the plant watering reminder system is based on cyclic analog acquisition, digital smoothing, threshold classification, and synchronous telemetry reporting. Upon power-up, the microcontroller initializes system clocks, serial peripherals, OLED display buffers, and GPIO directions.",
                "During each iteration of the main loop, ten successive analog samples are read from pin PA0 with an inter-sample interval of 10 milliseconds. The arithmetic mean of these samples is computed to filter high-frequency environmental noise. The smoothed raw ADC value is subsequently evaluated through an integer mapping function calibrated against predetermined air (3700) and water (1300) endpoints.",
                "The resultant percentage is evaluated against preset thresholds (30% for dry soil and 80% for saturation). If the calculated moisture is below 30%, the state transitions to 'NEEDS WATER' and pin PC13 is driven LOW to activate the alert LED. When the percentage resides between 30% and 80%, the system maintains the 'OPTIMAL' state with the LED extinguished. Exceeding 80% transitions the system to 'TOO WET'."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.1":
            paragraphs = [
                "Setting up the embedded development environment required configuring the Arduino IDE with the official STM32duino board package repository. The board manager URL was populated to install the STMicroelectronics core libraries and toolchains.",
                "The target board was selected as 'Generic STM32F1 series' with the part number specified as 'BluePill F103C8'. The upload method was configured for 'STLink', enabling direct flashing and debugging over the hardware SWD header. Peripheral library dependencies, including Adafruit_GFX and Adafruit_SSD1306, were installed via the IDE library manager."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.2":
            paragraphs = [
                "The peripheral and clock tree architecture reflects standard STM32CubeMX configuration principles. The clock tree configures an external 8 MHz crystal oscillator (HSE) routed through the phase-locked loop (PLL) multiplier of 9, producing the maximum rated system core frequency of 72 MHz. The APB2 peripheral clock runs at 72 MHz, while the APB1 peripheral clock is prescaled to 36 MHz.",
                "The ADC clock prescaler is configured to divide APB2 by 6, yielding an ADC peripheral clock frequency of 12 MHz, safely beneath the 14 MHz maximum threshold specified in the STM32F103 datasheet. I2C1 is clocked from the 36 MHz APB1 bus to generate standard 100 kHz clock pulses on PB6."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.3":
            paragraphs = [
                "GPIO initialization requires explicit configuration of pin directions and electrical modes. Pin PA0 is initialized in analog input mode (INPUT_ANALOG) to disconnect the digital input buffer and prevent current leakage during ADC conversions.",
                "Pin PC13 is configured as an output push-pull pin and initially driven HIGH to ensure the active-low LED begins in an unlit state. Pins PB6 and PB7 are claimed by the I2C1 peripheral in alternate-function open-drain mode, while PA9 is configured as an alternate-function push-pull transmit line."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.4":
            paragraphs = [
                "The embedded C program follows a clean modular design divided into sensory acquisition, calculation, state machine logic, display rendering, and serial output routines.",
                "The 'readSoilMoistureAverage()' routine executes a loop accumulating ten ADC measurements into a 32-bit integer variable before performing integer division. The 'calculateMoisturePercent()' function applies the integer map algorithm with strict clamping to enforce a valid 0% to 100% boundary. The 'updateDisplay()' routine formats telemetry lines on the SSD1306 frame buffer and flushes the pixel matrix over I2C."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.5":
            paragraphs = [
                "Hardware interfacing was executed on a breadboard prototyping platform. The capacitive soil moisture sensor was connected with three jumper wires: VCC to the 3.3V rail, GND to common ground, and the analog output signal line to pin PA0 of the Blue Pill board.",
                "The SSD1306 OLED module was wired to the 3.3V and GND rails, with its SCL pin wired to PB6 and SDA pin to PB7. The ST-Link V2 programmer was connected to the four-pin SWD header (3.3V, SWDIO, SWCLK, GND). Decoupling capacitors were verified on the board to ensure voltage stability during sensor analog conversions."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.6":
            paragraphs = [
                "Firmware compilation and debugging were conducted iteratively. The ST-Link V2 programmer was connected via USB, and the code was flashed onto the STM32F103C8T6 Flash memory through the SWD interface.",
                "Upon successful programming, the system executed the setup sequence, initializing the SSD1306 OLED display and verifying I2C ACK responses from address 0x3C. The onboard USART1 transmitter began streaming serial telemetry at 115200 baud, enabling immediate verification of sensor readings on the development host console."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.7":
            paragraphs = [
                "Several practical implementation challenges were encountered and resolved during development:",
                "First, single raw ADC readings exhibited occasional high-frequency noise spikes caused by switching transients on the USB power rail. This was resolved by implementing a 10-sample moving average with 10 ms inter-sample delay, resulting in stable and repeatable readings.",
                "Second, the inverted active-low configuration of the onboard LED on pin PC13 required explicit handling in software logic. Writing digital HIGH turns the LED OFF, while writing LOW completes the circuit to ground and turns the LED ON. The conditional state handler was verified to reflect this inversion accurately.",
                "Third, integer division truncation in the Arduino map() function required careful ordering of operations to prevent premature underflow before multiplying by 100, ensuring consistent calculation across the 12-bit ADC span."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "5.1":
            paragraphs = [
                "The testing methodology was formulated to validate system accuracy, state machine transitions, and visual indicators across three distinct environmental operating conditions: completely dry air, calibrated optimal damp soil, and liquid water saturation.",
                "Each test condition evaluated the raw 12-bit ADC measurement, calculated moisture percentage, reported operational state on the OLED display, status of the PC13 alert LED, and serial telemetry output at 115200 baud."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "5.5":
            paragraphs = [
                "Analysis of the expected operational behavior confirms that the integer mapping algorithm cleanly partitions the 12-bit ADC space into three unambiguous functional regions.",
                "In dry soil conditions where raw ADC values are greater than or equal to 2981, the calculated moisture percentage remains strictly below 30%, triggering the 'NEEDS WATER' alert state and lighting the PC13 LED. When raw ADC values range between 1757 and 2980, the system operates in the 'OPTIMAL' moisture band (30% to 80%) with the alert LED extinguished. For raw values less than or equal to 1756, the moisture percentage exceeds 80%, correctly indicating the 'TOO WET' state.",
                "The deterministic mathematical relationship guarantees predictable and reliable operation without boundary flutter or calculation overflow."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "6.1":
            paragraphs = [
                "The plant watering reminder system based on the STM32F103C8T6 microcontroller and capacitive soil moisture sensor was successfully designed, implemented, and verified. The system demonstrates the integration of analog acquisition, multi-sample digital filtering, I2C graphical display rendering, and active-low GPIO actuation.",
                "The capacitive measurement technique eliminates sensor probe degradation, while the ARM Cortex-M3 processor delivers rapid 72 MHz computation with minimal power consumption. The resulting unit provides a self-contained, dependable tool for soil hydration monitoring."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "6.2":
            paragraphs = [
                "The engineering learning outcomes attained through this micro-project include:"
            ]
            bullets = [
                "Practical experience in configuring and sampling 12-bit analog-to-digital converter channels on the STM32 ARM Cortex-M3 platform.",
                "Understanding the design principles and noise characteristics of capacitive soil moisture sensors compared to resistive probes.",
                "Mastery of the I2C serial communication bus and driver integration for SSD1306 monochrome OLED displays.",
                "Appreciation of integer division truncation dynamics in embedded C algorithms and the necessity of deterministic data mapping.",
                "Hands-on competency in in-circuit flashing and debugging with the ST-Link V2 programmer using the SWD interface."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "6.3":
            paragraphs = [
                "While the system achieves its core objectives reliably, certain operational limitations exist:",
                "The capacitive sensor output is subject to temperature drift, as ambient temperature changes can slightly alter soil dielectric permittivity without a corresponding change in moisture content.",
                "The current implementation relies on tethered 5V/3.3V power and does not incorporate low-power sleep modes between measurement cycles, which would be essential for battery-operated field deployments.",
                "Telemetry reporting is restricted to local visual displays and wired serial terminals, without long-range wireless connectivity."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "6.4":
            paragraphs = [
                "Several technical enhancements are planned for future iterations of the system:"
            ]
            bullets = [
                "Low-Power Sleep Optimization: Utilize the STM32 Stop mode and RTC wake-up timer to reduce average power consumption to microampere levels for multi-year battery life.",
                "Wireless Telemetry Integration: Interface an ESP8266 Wi-Fi or LoRa transceiver over USART to transmit moisture telemetry to cloud IoT dashboards.",
                "Automated Irrigation Actuation: Add a MOSFET or relay driver circuit to control a 5V submersible water pump, transitioning the unit from passive monitoring to closed-loop irrigation.",
                "Temperature Compensation: Integrate an on-probe digital temperature sensor (such as DS18B20) to dynamically compensate moisture readings against thermal fluctuations."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id in ("acknowledgement", "ack"):
            paragraphs = [
                "We express our deep gratitude to our project guide and course instructors for their invaluable guidance, encouragement, and technical suggestions throughout the course of this micro-project. We are also grateful to the Head of the Department and the institution for providing the laboratory facilities and hardware development equipment necessary to complete this work.",
                "Finally, we extend our heartfelt thanks to our parents and peers for their continuous support and encouragement during our engineering studies."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id in ("declaration", "dec"):
            paragraphs = [
                "We hereby declare that the micro-project report entitled 'Plant Watering Reminder using STM32 and Soil Moisture Sensor' submitted in partial fulfillment of the requirements for the award of the degree of Bachelor of Technology in Electronics and Communication Engineering is an authentic record of our own work carried out under the supervision of the faculty guide.",
                "The matter embodied in this report has not been submitted by us or any other person for the award of any other degree or diploma."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        # Default fallback generator for any section
        paragraphs = [
            f"This section discusses {title.lower()} within the context of the embedded micro-project.",
            f"The implementation leverages the STM32F103C8T6 microcontroller running at 72 MHz to ensure deterministic execution and responsive peripheral handling.",
            f"All operations are carried out strictly in accordance with the specified technical requirements and safety constraints."
        ]
        return {"paragraphs": paragraphs, "bullets": []}

    def _generate_night_lamp(self, section_id: str, title: str, prompt: str) -> Dict[str, Any]:
        """Generate verified academic prose for Automatic Night Lamp using STM32 and LDR."""
        if section_id == "abstract":
            paragraphs = [
                "This report presents the design and hardware realization of an Automatic Night Lamp system based on the STM32F103C8T6 ARM Cortex-M3 microcontroller and a Light Dependent Resistor (LDR) sensor. Modern municipal and residential lighting systems require autonomous illumination control to prevent daylight energy wastage while providing dependable illumination upon the onset of darkness. The system interfaces an analog LDR voltage divider to a 12-bit Analog-to-Digital Converter (ADC) on microcontroller pin PA1 to monitor ambient light levels in real time.",
                "Acquired analog light readings are processed through an 8-sample moving average digital filter to suppress transient photometric fluctuations and optical noise. The firmware implements a dual-threshold hysteresis state machine with threshold boundaries configured at 1400 (darkness activation) and 1800 (daylight deactivation) raw ADC counts. When darkness is detected, the microcontroller commands an active-low visual indicator on GPIO pin PC13 and drives the night lamp actuator on pin PA8 via TIM1 PWM duty cycle modulation (scaled from 60 to 255) or relay switching. Telemetry is streamed over USART1 at 115200 baud.",
                "The embedded firmware was developed in Embedded C for the Arduino STM32 Core environment. Empirical testing validated the hysteresis control band, eliminating high-frequency lamp fluttering during twilight transitions and ensuring energy-efficient, autonomous lighting control."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "1.1":
            paragraphs = [
                "Automated lighting control systems play an essential role in contemporary energy conservation and smart infrastructure. Conventional outdoor and hallway lighting frequently relies on manual operation or rigid timer schedules, leading to unnecessary electrical power consumption during overcast days or premature activation during long summer evenings. The objective of this project is to develop an autonomous, responsive Automatic Night Lamp using an embedded STM32 microcontroller and optical sensing.",
                "The core computing platform is the STM32F103C8T6 'Blue Pill' board, incorporating an ARM Cortex-M3 32-bit core running at 72 MHz clock frequency with 64 KB of Flash memory and 20 KB of SRAM. An analog Light Dependent Resistor (LDR) configured in a voltage divider circuit continuously senses ambient lux levels. The analog potential is digitized using the microcontroller's onboard 12-bit ADC peripheral on pin PA1.",
                "To deliver robust illumination actuation without flickering during dusk and dawn transitions, the microcontroller implements a digital moving average filter and dual-threshold hysteresis algorithm. The output actuator is driven from pin PA8 using hardware timer TIM1 PWM modulation, complemented by an active-low diagnostic LED on pin PC13. The architecture guarantees autonomous, deterministic operation and low quiescent power consumption."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "1.2":
            paragraphs = [
                "The technical objectives defined for the design and deployment of the Automatic Night Lamp system comprise the following milestones:"
            ]
            bullets = [
                "Interface a Light Dependent Resistor (LDR) voltage divider to the STM32F103C8T6 microcontroller using 12-bit ADC channel 1 on pin PA1.",
                "Implement an 8-sample moving average digital filter with a 250ms sampling cadence to attenuate ambient photometric noise.",
                "Establish a dual-threshold hysteresis algorithm using calibrated thresholds of 1400 (darkness activation) and 1800 (daylight deactivation) to prevent twilight oscillations.",
                "Generate Pulse Width Modulation (PWM) signals on pin PA8 using hardware timer TIM1 Channel 1 with duty cycle scaling between 60 and 255.",
                "Drive an onboard active-low indicator LED on GPIO pin PC13 to signal nocturnal lamp engagement.",
                "Transmit real-time ADC samples, operating state, and lamp duty telemetry over USART1 at 115200 baud for debugging and logging."
            ]
            return {"paragraphs": paragraphs, "bullets": bullets}

        if section_id == "1.3":
            paragraphs = [
                "The scope of this project encompasses the embedded circuit design, sensor conditioning, firmware algorithm development, and hardware prototyping of an autonomous night lamp controller.",
                "The implementation focuses on localized real-time ambient light monitoring, dual-threshold state machine evaluation, and direct peripheral drive via PWM and GPIO. Long-distance wireless networking, cloud telemetry, and external high-voltage AC mains switching modules are maintained as external system interfaces, ensuring that the embedded controller remains compact, deterministic, and self-contained."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "1.4":
            paragraphs = [
                "The developed embedded system provides practical utility across multiple infrastructure and domestic illumination domains:"
            ]
            bullets = [
                "Municipal Street Lighting: Automates street lamps to turn on during darkness and switch off at dawn, saving energy across public grids.",
                "Residential Porch and Pathway Lighting: Provides automatic nighttime pathway illumination for residential safety and security.",
                "Commercial Corridor Illumination: Automatically activates low-power night lighting in hotel corridors, stairwells, and emergency exits.",
                "Embedded Microcontroller Education: Demonstrates multi-peripheral integration of 12-bit ADC acquisition, PWM timer modulation, and hysteresis state logic."
            ]
            return {"paragraphs": paragraphs, "bullets": bullets}

        if section_id == "2.1":
            paragraphs = [
                "The STM32 microcontroller family, developed by STMicroelectronics, is built upon the high-performance ARM Cortex-M architecture. Designed specifically for embedded applications demanding high computing throughput, deterministic real-time response, and low power dissipation, the Cortex-M3 core implements the ARMv7-M architecture with a 3-stage pipeline and Harvard bus architecture.",
                "The STM32F103 series operates at clock frequencies up to 72 MHz and integrates advanced hardware peripherals including a Nested Vectored Interrupt Controller (NVIC), multiple general-purpose and advanced-control timers, and high-speed multi-channel 12-bit Analog-to-Digital Converters. This peripheral richness makes the microcontroller an ideal platform for responsive sensory monitoring and precise hardware control."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "2.2":
            paragraphs = [
                "The hardware platform utilized in this project is the STM32F103C8T6 development board, commonly termed the 'Blue Pill'. The board features an STM32F103C8T6 microcontroller with 64 KB of Flash program memory and 20 KB of SRAM, operating at 72 MHz.",
                "An onboard 8 MHz crystal oscillator serves as the high-speed external (HSE) clock, which is multiplied by the internal Phase-Locked Loop (PLL) to achieve the 72 MHz system core clock. The board includes an onboard 3.3V low-dropout voltage regulator capable of accepting 5V power from USB or external supplies, while an integrated user LED on pin PC13 provides a convenient status indicator."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "2.3":
            paragraphs = [
                "The hardware implementation comprises carefully selected electronic modules to achieve dependable optical acquisition and illumination output. The primary sensing element is a Cadmium Sulfide (CdS) Light Dependent Resistor (LDR) paired with a 10k resistor in a voltage divider circuit. Under high illumination, LDR resistance drops, outputting a high voltage (up to ~3500 raw ADC counts), whereas ambient darkness increases resistance and lowers the output voltage (down to ~300 raw ADC counts).",
                "The illumination actuator is interfaced via pin PA8, which is driven by hardware timer TIM1 Channel 1 for PWM power modulation or digital switching. Diagnostic feedback is delivered by the onboard active-low LED on pin PC13. Firmware upload and in-circuit debugging are conducted through the ST-Link V2 in-circuit programmer over the Serial Wire Debug (SWD) interface."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "2.4":
            paragraphs = [
                "The development and execution of the embedded system require the following software toolchains and libraries:"
            ]
            bullets = [
                "Development Environment: Arduino IDE configured with the official STM32duino (Arduino_Core_STM32) board support package.",
                "Compiler Toolchain: Arm GNU Toolchain (arm-none-eabi-gcc) for 32-bit ARM Cortex-M embedded targets.",
                "Hardware Drivers: STM32 Hardware Abstraction Layer (HAL) drivers encapsulated within the STM32duino core.",
                "Programming Utility: ST-Link V2 driver and STM32CubeProgrammer for SWD firmware flashing.",
                "Telemetry Console: Serial terminal monitor running at 115200 baud for telemetry observation."
            ]
            return {"paragraphs": paragraphs, "bullets": bullets}

        if section_id == "2.5":
            paragraphs = [
                "The software development environment relies on the Arduino IDE configured with the official STMicroelectronics STM32duino core. This environment combines the accessibility of Arduino API functions with the full computational capability of the ARM Cortex-M3 architecture.",
                "The toolchain compiles C/C++ source code into optimized ARM Thumb-2 machine binaries using arm-none-eabi-gcc. Compiled binaries are flashed into the 64 KB on-chip Flash memory of the STM32 microcontroller via the ST-Link V2 in-circuit programmer using the two-wire Serial Wire Debug (SWD) protocol (SWDIO and SWCLK)."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "3.1":
            paragraphs = [
                "The system architecture is structured into three primary hardware stages: the sensory acquisition stage, the central microcontroller processing stage, and the actuator output stage.",
                "In the sensory stage, the LDR voltage divider converts ambient optical intensity into an analog voltage. The central processing stage (STM32F103C8T6) samples this voltage through 12-bit ADC1 on pin PA1, applies an 8-sample moving average filter, and evaluates the filtered value against dual hysteresis thresholds. In the actuator stage, the microcontroller adjusts the night lamp output on pin PA8 via TIM1 PWM and updates the status LED on pin PC13."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "3.2":
            paragraphs = [
                "The system block diagram illustrates the architectural flow of information and energy across the hardware modules. The LDR sensor divider feeds analog potential to the STM32 microcontroller's ADC1_IN1 peripheral on pin PA1.",
                "The microcontroller processes the digitized light data through filtering and hysteresis algorithms, generating control signals for the night lamp on pin PA8 (TIM1_CH1 PWM) and the status indicator on pin PC13. Continuous telemetry is streamed simultaneously via USART1 (pins PA9 and PA10) to an external host at 115200 baud."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "3.3":
            paragraphs = [
                "The circuit schematic details the physical pin connections and electrical interconnections between the components. The LDR is wired between the 3.3V rail and the analog pin PA1, while a 10k fixed resistor connects pin PA1 to circuit ground (GND), forming an analog voltage divider.",
                "Microcontroller pin PA8 connects to the night lamp driver input, enabling PWM brightness modulation. The onboard active-low LED on pin PC13 is connected between 3.3V and the microcontroller open-drain/push-pull sink pin. SWD programming headers connect to 3.3V, GND, SWCLK, and SWDIO on the ST-Link V2 adapter."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "3.4":
            paragraphs = [
                "The hardware-software interface defines how low-level microcontroller peripherals are controlled by the embedded firmware routines.",
                "Analog sensing uses the STM32 ADC1 peripheral configured for 12-bit conversion on channel 1 (pin PA1), converting 0V to 3.3V into integer values from 0 to 4095. Pin PA8 is configured as a timer output channel (TIM1_CH1) running hardware PWM with duty cycle values between 60 and 255. Pin PC13 is initialized as an active-low GPIO output, and USART1 is set up for asynchronous serial communication at 115200 baud on pins PA9 and PA10."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "3.5":
            paragraphs = [
                "The microcontroller pin mapping assigns specific hardware pins to peripheral functions, balancing signal routing integrity and resource allocation.",
                "Pin PA1 is dedicated to ADC1_IN1 for analog light measurement. Pin PA8 is mapped to TIM1_CH1 for hardware PWM generation. Pin PC13 serves as an active-low GPIO output for system state indication, while pins PA9 (TX) and PA10 (RX) are allocated to USART1 serial telemetry."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "3.6":
            paragraphs = [
                "The firmware execution flow operates as a non-blocking cyclic state machine governed by a 250ms sampling cadence. Upon boot, system peripherals—GPIO, ADC, TIM1 PWM, and USART1—are initialized in the setup routine.",
                "In the main execution loop, the firmware executes periodic ADC conversions, updates an 8-sample circular buffer, and computes the arithmetic average. The filtered reading is compared against hysteresis thresholds: if the reading falls below 1400 (darkness), the night lamp on PA8 is activated and PC13 LED is pulled LOW (ON). If the reading exceeds 1800 (daylight), the lamp is deactivated and PC13 is set HIGH (OFF). Between 1400 and 1800, the previous state is latched, eliminating oscillation."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "3.7":
            paragraphs = [
                "The optical calibration establishes exact numerical boundaries separating ambient darkness, twilight hysteresis, and bright daylight.",
                "Under deep darkness, the LDR divider produces raw ADC counts below 1400 (reaching ~300 in full dark), triggering lamp activation. In daylight, the raw reading exceeds 1800 (reaching ~3500 under full ambient light), triggering lamp deactivation. The 400-count difference between 1400 and 1800 represents the deadband (hysteresis) zone where the controller maintains its current state, ensuring stability during gradual twilight lighting changes."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.1":
            paragraphs = [
                "Hardware prototyping was conducted on a breadboard platform to verify circuit continuity and electrical stability before final deployment. The STM32 Blue Pill development board was mounted alongside the LDR voltage divider network and status LEDs.",
                "Short, direct jumper wire connections were maintained between the LDR divider output and analog pin PA1 to minimize stray capacitive coupling and electrical noise pickup. A bypass capacitor was placed across the 3.3V and GND power rails to suppress supply ripple."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.2":
            paragraphs = [
                "The optical sensor conditioning circuit employs an LDR in series with a 10k fixed metal-film resistor. The midpoint of the divider connects directly to pin PA1 (ADC1_IN1).",
                "When ambient light illuminates the CdS LDR surface, its resistance decreases significantly relative to the fixed 10k resistor, raising the voltage at pin PA1 toward 3.3V. In darkness, the LDR resistance rises to hundreds of kilo-ohms, dropping the voltage at pin PA1 toward ground. This produces an analog potential range from approximately 300 to 3500 raw counts, which is digitized by the 12-bit ADC."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.3":
            paragraphs = [
                "The output drive circuitry translates microcontroller control decisions into physical illumination states. Pin PA8 is configured to output a PWM waveform driven by hardware timer TIM1 Channel 1.",
                "The PWM duty cycle is scaled between 60 (minimum night illumination) and 255 (maximum brightness), allowing gradual power modulation. The onboard indicator LED on pin PC13 operates in sink mode: writing a digital LOW sinks current and illuminates the LED, whereas writing a digital HIGH turns the LED off."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.4":
            paragraphs = [
                "The embedded firmware is organized using a clean, non-blocking modular structure. The setup routine configures the ADC resolution to 12 bits, sets pin modes for PA1, PA8, and PC13, and initializes USART1 communication at 115200 baud.",
                "The loop routine executes non-blocking time checks using the system millisecond counter (millis). Every 250ms, a new sensor reading is acquired, integrated into the moving average buffer, and evaluated by the hysteresis decision logic without halting processor execution."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.5":
            paragraphs = [
                "To mitigate transient photometric noise caused by passing shadows, optical reflections, or 50 Hz AC artificial lighting flicker, a digital moving average filter is applied to the raw ADC values.",
                "An array storing 8 consecutive ADC samples is maintained in SRAM. With each 250ms sampling tick, the oldest sample is replaced by the newest reading, and the arithmetic mean is computed across the 8 elements. This smoothing provides a stable, noise-free representation of ambient light trends."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.6":
            paragraphs = [
                "The decision engine relies on a dual-threshold hysteresis state machine to prevent rapid switching around the twilight threshold. A single threshold inevitably causes relay chatter or high-frequency lamp flickering when light levels hover near the boundary.",
                "The firmware establishes two distinct boundaries: THRESHOLD_DARK_ON = 1400 and THRESHOLD_LIGHT_OFF = 1800. When light levels decrease below 1400, the night lamp engages. When light levels rise above 1800, the lamp disengages. In the transitional region between 1400 and 1800, the firmware preserves the existing operational state, ensuring steady illumination."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "4.7":
            paragraphs = [
                "Diagnostic telemetry is transmitted over USART1 at a baud rate of 115200 bps on pins PA9 and PA10. Every sampling period, the firmware outputs a structured serial message reporting the raw ADC count, the filtered average, the active hysteresis state, and the commanded PWM duty cycle.",
                "This serial data stream allows verification of sensor response and calibration under real-world ambient conditions using standard serial terminal software."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "5.1":
            paragraphs = [
                "The testing methodology evaluated system reliability across three core lighting regimes: bright daylight, dusk transition, and complete nighttime darkness.",
                "The sensor was exposed to varying illumination conditions while simultaneously logging the 12-bit ADC readings, moving average values, and PWM output status via serial telemetry at 115200 baud."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "5.2":
            paragraphs = [
                "The system test matrix comprised three structured verification test cases targeting operational boundaries:",
                "Test Case TC_01 validated daylight inactivation with ambient light producing ADC counts above 1800, confirming that the lamp and PC13 LED remained inactive. Test Case TC_02 verified hysteresis stability between 1400 and 1800 counts, demonstrating that fluctuations did not trigger unwanted lamp cycling. Test Case TC_03 verified nocturnal activation below 1400 counts, successfully engaging the lamp and status indicator."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "5.3":
            paragraphs = [
                "Hardware verification of the breadboard prototype demonstrated reliable physical operation across light transitions. Voltage levels at pin PA1 remained stable across varying lux levels, confirming the divider resistance values.",
                "The onboard active-low LED on pin PC13 correctly indicated lamp activation states, and the PWM output on pin PA8 drove the illumination stage without signal jitter."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "5.4":
            paragraphs = [
                "Serial terminal captures verified consistent telemetry output at 115200 baud. As ambient light was occluded, the 8-sample moving average tracked the declining illumination smoothly from ~3500 counts down to ~300 counts.",
                "Upon crossing the 1400 threshold, the state transition was recorded instantly, and the PWM duty cycle updated to the commanded level without hesitation."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "5.5":
            paragraphs = [
                "Performance evaluation confirmed that the STM32F103C8T6 microcontroller easily met all operational requirements. The 250ms sampling interval provided immediate perceptual response while maintaining low processor load at 72 MHz.",
                "The 400-count hysteresis band completely eliminated twilight oscillation, delivering a robust, flicker-free automated lighting controller."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "6.1":
            paragraphs = [
                "This micro-project successfully realized an Automatic Night Lamp controller based on the STM32F103C8T6 ARM Cortex-M3 microcontroller and an LDR optical sensor.",
                "By integrating 12-bit analog light acquisition on pin PA1, an 8-sample moving average filter, dual-threshold hysteresis logic, and TIM1 PWM output on pin PA8, the system delivers reliable autonomous illumination control with zero dusk/dawn flickering."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "6.2":
            paragraphs = [
                "The key engineering achievements realized during this project include:"
            ]
            bullets = [
                "Developed an autonomous night lamp controller on the STM32F103C8T6 microcontroller running at 72 MHz.",
                "Eliminated twilight flicker by implementing a 400-count hysteresis deadband between 1400 and 1800 raw ADC counts.",
                "Applied an 8-sample moving average filter with a 250ms sampling period to suppress transient optical noise.",
                "Demonstrated hardware timer PWM duty cycle modulation on pin PA8 and active-low status indication on pin PC13.",
                "Provided serial telemetry over USART1 at 115200 baud for system monitoring and verification."
            ]
            return {"paragraphs": paragraphs, "bullets": bullets}

        if section_id == "6.3":
            paragraphs = [
                "The current implementation possesses several technical limitations that define its operational boundary:",
                "The CdS LDR sensor exhibits a negative temperature coefficient that can introduce minor resistance shifts across wide temperature extremes. Additionally, the prototype operates from a tethered 3.3V power supply without low-power sleep modes, and mains AC lamp switching requires an external optoisolated relay module."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id == "6.4":
            paragraphs = [
                "Future enhancements to extend the capabilities of the Automatic Night Lamp system include:"
            ]
            bullets = [
                "Real-Time Clock Scheduling: Integrate an I2C RTC module (such as DS3231) to combine ambient light sensing with time-of-day curfew dimming.",
                "PIR Motion Detection: Add a passive infrared sensor to maintain the lamp at 20% standby brightness and illuminate to 100% only upon pedestrian detection.",
                "Solar Energy Integration: Incorporate a solar charging controller and lithium-ion battery management circuit for standalone street lighting.",
                "Wireless Telemetry: Interface an ESP32 or LoRa module to stream streetlight status and power consumption metrics to a central municipal dashboard."
            ]
            return {"paragraphs": paragraphs, "bullets": bullets}

        if section_id in ("declaration", "dec"):
            paragraphs = [
                "We hereby declare that the micro-project report entitled 'Automatic Night Lamp using STM32 and LDR' submitted in partial fulfillment of the requirements for the award of the degree of Bachelor of Technology in Computer Science and Engineering is an authentic record of our own work carried out under the supervision of the faculty guide.",
                "The matter embodied in this report has not been submitted by us or any other person for the award of any other degree or diploma."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        if section_id in ("acknowledgement", "ack"):
            paragraphs = [
                "We express our deep sense of gratitude to our project guide for invaluable guidance, encouragement, and technical advice throughout this work.",
                "We extend our sincere thanks to the Head of the Department and the faculty members of the Department of Computer Science & Engineering for their constant support and providing excellent laboratory facilities.",
                "Finally, we thank our parents and classmates for their moral support, constructive feedback, and cooperation during the completion of this project."
            ]
            return {"paragraphs": paragraphs, "bullets": []}

        # Fallback for any other section
        paragraphs = [
            f"This section discusses {title.lower()} within the context of the Automatic Night Lamp micro-project.",
            f"The implementation leverages the STM32F103C8T6 microcontroller running at 72 MHz to ensure deterministic execution and responsive peripheral handling.",
            f"All operations are carried out strictly in accordance with the specified technical requirements and safety constraints."
        ]
        return {"paragraphs": paragraphs, "bullets": []}


class OpenAILLMClient(LLMClient):
    """Client for OpenAI-compatible local HTTP endpoint (Ollama, llama.cpp, LM Studio)."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        model: str = "gpt-oss:20b",
        temperature: float = 0.3,
        timeout: int = 600,
        retries: int = 2,
        reasoning_effort: str = "low",
        seed: int = 42,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.retries = retries
        self.reasoning_effort = reasoning_effort
        self.seed = seed

    def generate(
        self,
        prompt: str,
        system_prompt: str,
        section_id: str,
        target_words: int = 200,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": min(2048, max(512, target_words * 4)),
            "stream": False,
        }
        if self.seed is not None:
            payload["seed"] = self.seed

        # Some models/endpoints support reasoning_effort
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        last_err = None
        for attempt in range(self.retries + 1):
            try:
                start_t = time.time()
                resp = requests.post(url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                elapsed = time.time() - start_t
                token_count = len(content.split()) * 1.3
                tok_per_sec = token_count / elapsed if elapsed > 0 else 0
                logger.info(
                    f"Generated section {section_id} in {elapsed:.1f}s (~{tok_per_sec:.1f} tokens/s)"
                )

                parsed = parse_llm_json(content)
                if parsed.get("paragraphs") or parsed.get("bullets"):
                    return parsed

                # If JSON was invalid, retry with repair prompt once
                if attempt < self.retries:
                    repair_prompt = (
                        f"Format the following text strictly as JSON with keys 'paragraphs' "
                        f"(list of strings) and 'bullets' (list of strings):\n\n{content}"
                    )
                    payload["messages"] = [
                        {
                            "role": "system",
                            "content": "You are a JSON formatting assistant. Output only valid JSON.",
                        },
                        {"role": "user", "content": repair_prompt},
                    ]
                    continue
                return parsed
            except Exception as e:
                last_err = e
                logger.warning(
                    f"LLM request attempt {attempt + 1} failed for section {section_id}: {e}"
                )
                time.sleep(2**attempt)

        # Fallback to plain placeholder if all retries failed
        logger.error(
            f"Failed to generate section {section_id} after {self.retries} retries: {last_err}"
        )
        return {
            "paragraphs": [
                f"[Section {section_id} could not be generated, edit manually. Error: {last_err}]"
            ],
            "bullets": [],
        }

"""Diagram generator for Block Diagram, Connection Wiring Diagram, and Flowchart.

Uses Graphviz (dot) if available, falling back to Matplotlib.
Renders 200 DPI PNGs with white backgrounds and readable typography.
Dynamically adapts to any project topic (Plant Monitor, Automatic Night Lamp, etc.)
by extracting hardware profiles from ProjectBrief.
"""

from dataclasses import dataclass, field
import logging
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional, Tuple, Union

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt

from reportsmith.brief import ProjectBrief
from reportsmith.facts import ProjectFacts

logger = logging.getLogger(__name__)


def is_graphviz_available() -> bool:
    """Check if 'dot' command is installed and accessible in PATH."""
    return shutil.which("dot") is not None


def is_night_lamp_project(brief: ProjectBrief) -> bool:
    """Check if the active project is an Automatic Night Lamp / LDR project."""
    title = brief.project.title.lower()
    pins = " ".join(p.pin + " " + p.connected_to for p in brief.implementation.pins).lower()
    return "lamp" in title or "ldr" in title or "night" in title or "pa8" in pins


# ==============================================================================
# DYNAMIC HARDWARE PROFILE EXTRACTOR
# ==============================================================================


@dataclass
class HardwareProfile:
    title: str
    board_name: str
    mcu: str
    core: str
    clock: str
    flash: str
    sram: str
    voltage: str
    # Primary Sensor
    sensor_name: str
    sensor_type: str
    sensor_pin: str
    sensor_peripheral: str
    sensor_desc: str
    # Primary Actuator / Output
    actuator_name: str
    actuator_type: str
    actuator_pin: str
    actuator_peripheral: str
    actuator_desc: str
    # Optional Display
    has_display: bool
    display_name: str
    display_pins: str
    # Status Indicator
    led_pin: str
    led_name: str
    # Communication
    uart_pins: str
    baud_rate: int
    programmer: str
    # Timing & Logic
    sample_count: int
    sample_interval_ms: int
    threshold_condition: str
    active_state_name: str
    inactive_state_name: str


def extract_project_hardware_profile(brief: ProjectBrief) -> HardwareProfile:
    """Extract a dynamic hardware profile from ProjectBrief for diagrams.
    
    Scans brief.implementation.pins, brief.components, and brief.behaviour
    to accurately capture sensors, actuators, displays, pins, and control logic.
    """
    b = brief.implementation.board
    title = brief.project.title

    # 1. Look for Primary Sensor
    sensor_pin_obj = None
    for p in brief.implementation.pins:
        if p.pin in ["PC13", "PA9", "PA10", "3V3", "5V", "GND"]:
            continue
        p_str = (p.peripheral + " " + p.connected_to + " " + p.function).lower()
        if any(
            w in p_str for w in ["ultrasonic", "echo", "trigger", "distance", "sonar", "sr04", "sensor", "moisture", "ldr", "temp", "humidity", "probe"]
        ) or ("analog" in p_str and "adc" in p.peripheral.lower()):
            sensor_pin_obj = p
            break

    sensor_comp = None
    for c in brief.implementation.components:
        c_str = (c.name + " " + c.role + " " + c.interface).lower()
        if any(w in c_str for w in ["ultrasonic", "echo", "trigger", "sensor", "probe", "moisture", "ldr", "light", "photocell", "temp", "measurement"]):
            sensor_comp = c
            break

    trig_pin = next((p.pin for p in brief.implementation.pins if "trig" in (p.connected_to + " " + p.function).lower()), None)
    echo_pin = next((p.pin for p in brief.implementation.pins if "echo" in (p.connected_to + " " + p.function).lower()), None)
    if trig_pin and echo_pin:
        sensor_pin = f"{trig_pin}/{echo_pin}"
    else:
        sensor_pin = sensor_pin_obj.pin if sensor_pin_obj else "PB0/PB1"
    sensor_peripheral = sensor_pin_obj.peripheral if sensor_pin_obj else "GPIO"
    if sensor_comp:
        sensor_name = sensor_comp.name
        sensor_desc = sensor_comp.notes or sensor_comp.role
        sensor_type = sensor_comp.role
    elif sensor_pin_obj:
        sensor_name = sensor_pin_obj.connected_to
        sensor_desc = sensor_pin_obj.function
        sensor_type = "Analog Sensing Probe"
    else:
        sensor_name = "Analog Sensor Module"
        sensor_desc = "Sensory voltage acquisition"
        sensor_type = "Analog Input"

    # 2. Look for Display Component
    display_comp = None
    for c in brief.implementation.components:
        c_str = (c.name + " " + c.role).lower()
        if any(w in c_str for w in ["oled", "lcd", "display", "screen"]):
            display_comp = c
            break

    has_display = display_comp is not None
    display_name = display_comp.name if display_comp else '0.96" SSD1306 OLED'
    disp_pins = []
    for p in brief.implementation.pins:
        p_str = (p.connected_to + " " + p.function).lower()
        if any(w in p_str for w in ["oled", "lcd", "display", "i2c", "scl", "sda"]):
            disp_pins.append(p.pin)
    display_pins = "/".join(disp_pins) if disp_pins else "PB6/PB7 (I2C)"

    # 3. Look for Primary Actuator / Alert (Buzzer, Lamp, Relay, Motor, etc.)
    actuator_pin_obj = None
    for p in brief.implementation.pins:
        p_str = (p.connected_to + " " + p.function).lower()
        if p.pin in [sensor_pin, "PA9", "PA10", "PC13", "3V3", "5V", "GND"]:
            continue
        if any(w in p_str for w in ["buzzer", "lamp", "relay", "motor", "pwm", "sounder", "alarm", "actuator", "drive", "output"]):
            actuator_pin_obj = p
            break

    actuator_comp = None
    for c in brief.implementation.components:
        c_str = (c.name + " " + c.role).lower()
        if any(w in c_str for w in ["buzzer", "sounder", "relay", "lamp", "motor", "alarm", "actuator", "drive"]):
            actuator_comp = c
            break

    if actuator_comp and actuator_pin_obj:
        actuator_name = actuator_comp.name
        actuator_desc = actuator_comp.notes or actuator_comp.role
        actuator_pin = actuator_pin_obj.pin
        actuator_peripheral = actuator_pin_obj.peripheral or "GPIO / PWM"
        actuator_type = actuator_comp.role
    elif actuator_pin_obj:
        actuator_name = actuator_pin_obj.connected_to
        actuator_desc = actuator_pin_obj.function
        actuator_pin = actuator_pin_obj.pin
        actuator_peripheral = actuator_pin_obj.peripheral or "GPIO / PWM"
        actuator_type = "Actuator Output"
    elif has_display:
        actuator_name = display_name
        actuator_desc = "Visual Telemetry & Status UI"
        actuator_pin = display_pins
        actuator_peripheral = "I2C Interface"
        actuator_type = "Visual Display"
    else:
        actuator_name = "Output Actuator / Alarm"
        actuator_desc = "System Alert Control"
        actuator_pin = "PA1"
        actuator_peripheral = "GPIO Output"
        actuator_type = "Actuator Output"

    # 4. Status Indicator LED
    led_pin = "PC13"
    for p in brief.implementation.pins:
        if "13" in p.pin or "led" in (p.connected_to + p.function).lower():
            led_pin = p.pin
            break
    led_name = f"User Alert LED ({led_pin})\nActive-Low Sink"

    # 5. Timing & Behaviour Logic
    timing = brief.behaviour.timing_values or {}
    sample_count = int(timing.get("SAMPLE_COUNT", timing.get("sample_count", 8)))
    sample_interval_ms = int(timing.get("READ_INTERVAL_MS", timing.get("interval_ms", 1000)))

    thresholds = brief.behaviour.thresholds or {}
    state_names = brief.behaviour.state_names or ["ACTIVE ALERT", "OPTIMAL NORMAL"]
    active_state = state_names[0] if state_names else "ACTIVE ALERT"
    inactive_state = state_names[1] if len(state_names) > 1 else "OPTIMAL NORMAL"

    if thresholds:
        thresh_items = [f"{k}={v}" for k, v in list(thresholds.items())[:2]]
        thresh_str = f"Trigger ({', '.join(thresh_items)})"
    else:
        thresh_str = "Sensor Limit Reached?"

    return HardwareProfile(
        title=title,
        board_name=b.name,
        mcu=b.mcu_part_number,
        core=b.core,
        clock=b.clock,
        flash=b.flash,
        sram=b.sram,
        voltage=b.voltage,
        sensor_name=sensor_name,
        sensor_type=sensor_type,
        sensor_pin=sensor_pin,
        sensor_peripheral=sensor_peripheral,
        sensor_desc=sensor_desc,
        actuator_name=actuator_name,
        actuator_type=actuator_type,
        actuator_pin=actuator_pin,
        actuator_peripheral=actuator_peripheral,
        actuator_desc=actuator_desc,
        has_display=has_display,
        display_name=display_name,
        display_pins=display_pins,
        led_pin=led_pin,
        led_name=led_name,
        uart_pins="PA9/PA10 (USART1)",
        baud_rate=brief.implementation.serial_baud or 115200,
        programmer=brief.implementation.programmer or "ST-Link V2 (SWD)",
        sample_count=sample_count,
        sample_interval_ms=sample_interval_ms,
        threshold_condition=thresh_str,
        active_state_name=active_state,
        inactive_state_name=inactive_state,
    )


# ==============================================================================
# MATPLOTLIB CRISP 200 DPI RENDERERS
# ==============================================================================


def render_block_diagram_mpl(brief: ProjectBrief, facts: ProjectFacts, out_file: Path) -> Path:
    """Render Block Diagram using Matplotlib at 200 DPI adapting dynamically to project topic."""
    fig, ax = plt.subplots(figsize=(11.5, 7.2), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    profile = extract_project_hardware_profile(brief)

    # Title
    ax.text(
        50,
        96,
        f"{profile.title.upper()} - HARDWARE BLOCK DIAGRAM",
        fontsize=11.5,
        fontweight="bold",
        ha="center",
        va="top",
        color="#1A202C",
    )

    # 1. Central Microcontroller Box
    mcu_box = patches.FancyBboxPatch(
        (32, 23),
        36,
        57,
        boxstyle="round,pad=0.8,rounding_size=2",
        edgecolor="#1D4ED8",
        facecolor="#EFF6FF",
        linewidth=2,
    )
    ax.add_patch(mcu_box)
    ax.text(
        50,
        74,
        f"CENTRAL PROCESSING UNIT\n{profile.mcu}\n({profile.board_name})",
        fontsize=11,
        fontweight="bold",
        ha="center",
        va="center",
        color="#1E3A8A",
    )
    mcu_details = (
        f"• Core: {profile.core} @ {profile.clock}\n"
        f"• Memory: {profile.flash} Flash / {profile.sram} SRAM\n"
        f"• ADC Channel: Pin {profile.sensor_pin} ({profile.sensor_peripheral})\n"
        f"• Peripheral Bus: {profile.actuator_peripheral} / USART1\n"
        f"• Operating Voltage: {profile.voltage}"
    )
    ax.text(
        50,
        59,
        mcu_details,
        fontsize=8.5,
        ha="center",
        va="center",
        color="#1E293B",
    )

    # 2. Power Supply Block (Top Left)
    pwr_box = patches.FancyBboxPatch(
        (4, 76),
        22,
        14,
        boxstyle="round,pad=0.5,rounding_size=1.5",
        edgecolor="#B45309",
        facecolor="#FEF3C7",
        linewidth=1.5,
    )
    ax.add_patch(pwr_box)
    ax.text(
        15,
        83,
        "POWER SUPPLY\n5V USB Input\n→ 3.3V LDO Regulator",
        fontsize=8.5,
        fontweight="bold",
        ha="center",
        va="center",
        color="#92400E",
    )
    ax.annotate(
        "3.3V DC",
        xy=(32, 75),
        xytext=(26, 83),
        arrowprops=dict(facecolor="#B45309", edgecolor="#B45309", arrowstyle="->", lw=1.5),
        fontsize=7.5,
        fontweight="bold",
        color="#92400E",
        ha="center",
    )

    # 3. Input Sensors Block (Middle Left)
    sensor_box = patches.FancyBboxPatch(
        (4, 42),
        22,
        22,
        boxstyle="round,pad=0.5,rounding_size=1.5",
        edgecolor="#047857",
        facecolor="#ECFDF5",
        linewidth=1.5,
    )
    ax.add_patch(sensor_box)
    sensor_display_name = profile.sensor_name
    if len(sensor_display_name) > 24:
        sensor_display_name = sensor_display_name[:22] + "..."
    sensor_text = f"SENSORY INPUT\n{sensor_display_name}\n({profile.sensor_peripheral})"
    ann_text = f"Analog IN\n(Pin {profile.sensor_pin})"

    ax.text(15, 53, sensor_text, fontsize=8.5, fontweight="bold", ha="center", va="center", color="#065F46")
    ax.annotate(
        ann_text,
        xy=(32, 53),
        xytext=(26, 53),
        arrowprops=dict(facecolor="#047857", edgecolor="#047857", arrowstyle="->", lw=1.5),
        fontsize=7.5,
        fontweight="bold",
        color="#065F46",
        ha="center",
    )

    # 4. In-Circuit Flasher (Bottom Left)
    prog_box = patches.FancyBboxPatch(
        (4, 12),
        22,
        18,
        boxstyle="round,pad=0.5,rounding_size=1.5",
        edgecolor="#6D28D9",
        facecolor="#F5F3FF",
        linewidth=1.5,
    )
    ax.add_patch(prog_box)
    ax.text(
        15,
        21,
        f"IN-CIRCUIT DEBUG\n{profile.programmer}\n(SWD Interface)",
        fontsize=8.5,
        fontweight="bold",
        ha="center",
        va="center",
        color="#5B21B6",
    )
    ax.annotate(
        "SWDIO/SWCLK",
        xy=(32, 32),
        xytext=(26, 21),
        arrowprops=dict(facecolor="#6D28D9", edgecolor="#6D28D9", arrowstyle="<->", lw=1.5),
        fontsize=7.5,
        fontweight="bold",
        color="#5B21B6",
        ha="center",
    )

    # 5. Output Actuator / Display Block (Top Right)
    out_box = patches.FancyBboxPatch(
        (74, 62),
        22,
        22,
        boxstyle="round,pad=0.5,rounding_size=1.5",
        edgecolor="#0284C7",
        facecolor="#F0F9FF",
        linewidth=1.5,
    )
    ax.add_patch(out_box)
    act_display_name = profile.actuator_name
    if len(act_display_name) > 24:
        act_display_name = act_display_name[:22] + "..."
    out_text = f"PRIMARY ACTUATOR\n{act_display_name}\n({profile.actuator_type})"
    ann_out = f"Control Line\n(Pin {profile.actuator_pin})"

    ax.text(85, 73, out_text, fontsize=8.5, fontweight="bold", ha="center", va="center", color="#0369A1")
    ax.annotate(
        ann_out,
        xy=(74, 73),
        xytext=(68, 68),
        arrowprops=dict(facecolor="#0284C7", edgecolor="#0284C7", arrowstyle="->", lw=1.5),
        fontsize=7.5,
        fontweight="bold",
        color="#0369A1",
        ha="center",
    )

    # 6. Status Alert LED (Middle Right)
    led_box = patches.FancyBboxPatch(
        (74, 38),
        22,
        16,
        boxstyle="round,pad=0.5,rounding_size=1.5",
        edgecolor="#DC2626",
        facecolor="#FEF2F2",
        linewidth=1.5,
    )
    ax.add_patch(led_box)
    ax.text(85, 46, f"STATUS INDICATOR\nOnboard User LED\n(Pin {profile.led_pin})", fontsize=8.5, fontweight="bold", ha="center", va="center", color="#991B1B")
    ax.annotate(
        f"GPIO Output\n(Pin {profile.led_pin})",
        xy=(74, 46),
        xytext=(68, 46),
        arrowprops=dict(facecolor="#DC2626", edgecolor="#DC2626", arrowstyle="->", lw=1.5),
        fontsize=7.5,
        fontweight="bold",
        color="#991B1B",
        ha="center",
    )

    # 7. Serial Console Telemetry (Bottom Right)
    uart_box = patches.FancyBboxPatch(
        (74, 12),
        22,
        18,
        boxstyle="round,pad=0.5,rounding_size=1.5",
        edgecolor="#475569",
        facecolor="#F8FAFC",
        linewidth=1.5,
    )
    ax.add_patch(uart_box)
    ax.text(
        85,
        21,
        f"HOST TELEMETRY\nUSB-to-UART Bridge\n({profile.baud_rate} Baud)",
        fontsize=8.5,
        fontweight="bold",
        ha="center",
        va="center",
        color="#334155",
    )
    ax.annotate(
        "USART1 TX/RX\n(PA9/PA10)",
        xy=(74, 21),
        xytext=(68, 34),
        arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.5),
        fontsize=7.5,
        fontweight="bold",
        color="#334155",
        ha="center",
    )

    out_file.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(str(out_file), dpi=200, facecolor="white", edgecolor="none")
    plt.close(fig)
    return out_file


def render_connection_diagram_mpl(brief: ProjectBrief, facts: ProjectFacts, out_file: Path) -> Path:
    """Render Connection/Wiring Diagram showing colour-coded dynamic interconnections."""
    fig, ax = plt.subplots(figsize=(11.5, 7.5), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    profile = extract_project_hardware_profile(brief)

    # Title
    ax.text(
        50,
        96,
        f"{profile.title.upper()} - HARDWARE WIRING DIAGRAM",
        fontsize=11.5,
        fontweight="bold",
        ha="center",
        va="top",
        color="#1E293B",
    )

    # MCU Central Pin Header (Wide 44-unit frame with vertically staggered pins prevents pin label interpolation)
    mcu_bg = patches.Rectangle((28, 15), 44, 72, edgecolor="#0F172A", facecolor="#E2E8F0", lw=2)
    ax.add_patch(mcu_bg)
    mcu_txt = profile.mcu if len(profile.mcu) <= 22 else profile.mcu[:20] + ".."
    board_txt = profile.board_name if len(profile.board_name) <= 24 else profile.board_name[:22] + ".."
    ax.text(
        50,
        83,
        f"{mcu_txt}\n({board_txt})",
        fontsize=9.0,
        fontweight="bold",
        ha="center",
        va="center",
    )

    # Dynamic MCU Pins Left (Vertically staggered from Right pins so labels never collide)
    pins_left = [
        ("3.3V (Power)", 72, "#DC2626"),
        ("GND (Ground)", 62, "#000000"),
        (f"{profile.sensor_pin} ({profile.sensor_peripheral})", 52, "#16A34A"),
        ("PA9 (USART1_TX)", 42, "#9333EA"),
        ("PA10 (USART1_RX)", 32, "#7C3AED"),
    ]

    # Dynamic MCU Pins Right (Staggered y-coords prevent overlapping with left labels)
    pins_right = [
        ("5V / 3.3V (Power)", 67, "#DC2626"),
        ("GND (Ground)", 57, "#000000"),
        (f"{profile.actuator_pin} ({profile.actuator_peripheral})", 47, "#D97706"),
        (f"{profile.led_pin} (Onboard LED)", 37, "#E11D48"),
    ]

    for label, y, col in pins_left:
        ax.plot([28, 31], [y, y], color=col, lw=2.5)
        ax.text(32, y, label, fontsize=6.5, va="center", ha="left")

    for label, y, col in pins_right:
        ax.plot([69, 72], [y, y], color=col, lw=2.5)
        ax.text(68, y, label, fontsize=6.5, va="center", ha="right")

    # Sensor Module on Left
    sensor_box = patches.Rectangle((3, 46), 19, 32, edgecolor="#15803D", facecolor="#F0FDF4", lw=1.5)
    ax.add_patch(sensor_box)
    s_label = profile.sensor_name
    if len(s_label) > 16:
        s_label = s_label[:14] + ".."
    ax.text(12.5, 74, s_label, fontsize=7.5, fontweight="bold", ha="center")
    ax.text(12.5, 69, "Sensor Module", fontsize=6.8, fontstyle="italic", ha="center", color="#475569")
    ax.text(12.5, 61, "VCC  [3.3V]", fontsize=7.2, ha="center")
    ax.text(12.5, 54, "GND  [0V]", fontsize=7.2, ha="center")
    ax.text(12.5, 47, f"SIG  [{profile.sensor_pin}]", fontsize=7.2, ha="center")

    ax.plot([22, 28], [61, 72], color="#DC2626", lw=1.8, label="3.3V VCC (Red)")
    ax.plot([22, 28], [54, 62], color="#000000", lw=1.8, label="GND (Black)")
    ax.plot([22, 28], [47, 52], color="#16A34A", lw=1.8, label=f"{profile.sensor_pin} Sensor (Green)")

    # Serial Adapter on Left Bottom
    uart_box = patches.Rectangle((3, 18), 19, 22, edgecolor="#6D28D9", facecolor="#FAF5FF", lw=1.5)
    ax.add_patch(uart_box)
    ax.text(12.5, 34, "USB-to-UART\nAdapter (CP2102)", fontsize=7.5, fontweight="bold", ha="center")
    ax.text(12.5, 26, "RXD ← PA9", fontsize=7.0, ha="center")
    ax.text(12.5, 21, "TXD → PA10", fontsize=7.0, ha="center")
    ax.plot([22, 28], [26, 42], color="#9333EA", lw=1.5, label="UART TX (Purple)")
    ax.plot([22, 28], [21, 32], color="#7C3AED", lw=1.5, label="UART RX (Violet)")

    # Output Device on Right
    out_box = patches.Rectangle((78, 44), 19, 34, edgecolor="#0369A1", facecolor="#F0F9FF", lw=1.5)
    ax.add_patch(out_box)
    a_label = profile.actuator_name
    if len(a_label) > 16:
        a_label = a_label[:14] + ".."
    ax.text(87.5, 74, a_label, fontsize=7.5, fontweight="bold", ha="center")
    ax.text(87.5, 69, "Actuator Unit", fontsize=6.8, fontstyle="italic", ha="center", color="#475569")
    ax.text(87.5, 61, "VCC  [5V/3.3V]", fontsize=7.2, ha="center")
    ax.text(87.5, 54, "GND  [0V]", fontsize=7.2, ha="center")
    ax.text(87.5, 47, f"IN   [{profile.actuator_pin}]", fontsize=7.2, ha="center")
    ax.plot([72, 78], [67, 61], color="#DC2626", lw=1.8)
    ax.plot([72, 78], [57, 54], color="#000000", lw=1.8)
    ax.plot([72, 78], [47, 47], color="#D97706", lw=1.8, label=f"{profile.actuator_pin} Drive (Amber)")

    # Onboard LED box on Right Bottom
    led_patch = patches.Rectangle((78, 22), 19, 16, edgecolor="#BE123C", facecolor="#FFF1F2", lw=1.5)
    ax.add_patch(led_patch)
    ax.text(87.5, 32, f"User LED ({profile.led_pin})\nOnboard Alert", fontsize=7.5, fontweight="bold", ha="center")
    ax.text(87.5, 25, "Active-Low Sink", fontsize=7.0, ha="center", color="#9F1239")
    ax.plot([72, 78], [37, 28], color="#E11D48", lw=1.8, label=f"{profile.led_pin} LED Drive (Rose)")

    # Color Key / Legend
    ax.legend(loc="lower center", ncol=4, fontsize=7.5, frameon=True, facecolor="#F8FAFC", edgecolor="#CBD5E1")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(str(out_file), dpi=200, facecolor="white", edgecolor="none")
    plt.close(fig)
    return out_file


def render_flowchart_mpl(brief: ProjectBrief, facts: ProjectFacts, out_file: Path) -> Path:
    """Render Algorithm Flowchart at 200 DPI dynamically reflecting project parameters."""
    fig, ax = plt.subplots(figsize=(9.2, 11.8), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    profile = extract_project_hardware_profile(brief)

    # Title
    ax.text(
        50,
        98,
        f"{profile.title.upper()} - FIRMWARE CONTROL FLOWCHART",
        fontsize=11,
        fontweight="bold",
        ha="center",
        va="top",
        color="#0F172A",
    )

    def draw_oval(x, y, text, color="#059669", bg="#ECFDF5"):
        box = patches.FancyBboxPatch(
            (x - 14, y - 2.5), 28, 5, boxstyle="round,pad=0.5,rounding_size=2.5",
            edgecolor=color, facecolor=bg, lw=1.5
        )
        ax.add_patch(box)
        ax.text(x, y, text, fontsize=8, fontweight="bold", ha="center", va="center", color=color)

    def draw_rect(x, y, w, h, text, color="#2563EB", bg="#EFF6FF"):
        box = patches.FancyBboxPatch(
            (x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0.3,rounding_size=1",
            edgecolor=color, facecolor=bg, lw=1.5
        )
        ax.add_patch(box)
        ax.text(x, y, text, fontsize=7.5, ha="center", va="center", color="#1E293B")

    def draw_diamond(x, y, w, h, text, color="#D97706", bg="#FFFBEB"):
        diamond = patches.Polygon(
            [[x, y + h / 2], [x + w / 2, y], [x, y - h / 2], [x - w / 2, y]],
            edgecolor=color, facecolor=bg, lw=1.5
        )
        ax.add_patch(diamond)
        ax.text(x, y, text, fontsize=7, fontweight="bold", ha="center", va="center", color="#92400E")

    def draw_arrow(x1, y1, x2, y2, label=""):
        ax.annotate(
            label, xy=(x2, y2), xytext=(x1, y1),
            arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.3),
            fontsize=7, ha="center", va="center", color="#0F172A"
        )

    # 1. Start
    draw_oval(50, 93, "START / POWER ON")
    draw_arrow(50, 90.5, 50, 86)

    # 2. System Init
    draw_rect(
        50,
        83,
        46,
        6,
        f"Initialize Clocks (72 MHz HSE/PLL)\nSetup GPIOs, ADC ({profile.sensor_pin}), Actuator ({profile.actuator_pin}), LED ({profile.led_pin})",
    )
    draw_arrow(50, 80, 50, 75.5)

    # 3. Peripheral / Serial Init
    draw_rect(
        50,
        73,
        42,
        5,
        f"Initialize USART1 ({profile.baud_rate} Baud)\nSet Initial State: {profile.inactive_state_name}",
    )
    draw_arrow(50, 70.5, 50, 66)

    # 4. Timer Check
    draw_rect(
        50,
        63.5,
        42,
        5,
        f"Check Timer: millis() - lastSampleTime >= {profile.sample_interval_ms} ms",
    )
    draw_arrow(50, 61, 50, 56.5)

    # 5. Read Samples
    draw_rect(
        50,
        53.5,
        46,
        6,
        f"Sample Pin {profile.sensor_pin} {profile.sample_count} Times (Oversampling Filter)\nCompute Arithmetic Average: rawVal = sum / {profile.sample_count}",
    )
    draw_arrow(50, 50.5, 50, 46)

    # 6. Map calculation
    draw_rect(
        50,
        43,
        46,
        6,
        f"Map & Scale Sensor Reading:\nCalculate Normalized Metric & Check Threshold Limits\nClamp to Valid Engineering Range",
    )
    draw_arrow(50, 40, 50, 35)

    # 7. Decision 1: Threshold trigger
    draw_diamond(
        50,
        31,
        28,
        8,
        f"Threshold Triggered?\n({profile.threshold_condition})",
    )

    # Branch Active Alert
    draw_arrow(64, 31, 78, 31, "YES")
    draw_rect(
        78,
        23,
        22,
        6,
        f"State = {profile.active_state_name}\nAssert {profile.actuator_name}\nDrive {profile.led_pin} (Alert ON)",
        color="#DC2626",
        bg="#FEF2F2",
    )

    # Branch Normal Inactive
    draw_arrow(50, 27, 50, 20, "NO")
    draw_rect(
        50,
        15,
        30,
        6,
        f"State = {profile.inactive_state_name}\nDeassert {profile.actuator_name}\nDeassert {profile.led_pin} (Normal)",
        color="#16A34A",
        bg="#F0FDF4",
    )

    # Telemetry and loop back
    draw_arrow(78, 20, 78, 7)
    draw_arrow(50, 12, 50, 7)
    draw_rect(
        64,
        4,
        36,
        4.5,
        f"Send Telemetry via USART1 ({profile.baud_rate} Baud)\nUpdate Display / Console",
        color="#475569",
        bg="#F8FAFC",
    )

    out_file.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(str(out_file), dpi=200, facecolor="white", edgecolor="none")
    plt.close(fig)
    return out_file


# ==============================================================================
# MACHINE LEARNING & SOFTWARE PIPELINE DIAGRAM RENDERERS
# ==============================================================================


def render_ml_block_diagram_mpl(brief: ProjectBrief, facts: ProjectFacts, out_file: Path) -> Path:
    """Render authentic Machine Learning Pipeline Architecture Block Diagram."""
    fig, ax = plt.subplots(figsize=(11.8, 7.5), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    title = brief.project.title
    ax.text(
        50,
        96,
        f"{title.upper()} - MACHINE LEARNING PIPELINE ARCHITECTURE",
        fontsize=11.5,
        fontweight="bold",
        ha="center",
        va="top",
        color="#1A202C",
    )

    def draw_card(x, y, w, h, header, subheader, bullets, border_col, bg_col, title_col):
        box = patches.FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.6,rounding_size=1.5",
            edgecolor=border_col,
            facecolor=bg_col,
            linewidth=1.8,
        )
        ax.add_patch(box)
        ax.text(x + w / 2, y + h - 4, header, fontsize=8.5, fontweight="bold", ha="center", va="top", color=title_col)
        if subheader:
            ax.text(x + w / 2, y + h - 8.5, subheader, fontsize=7.5, fontstyle="italic", ha="center", va="top", color="#475569")
        by = y + h - 13
        for b in bullets:
            ax.text(x + 2, by, f"• {b}", fontsize=7.2, ha="left", va="top", color="#1E293B")
            by -= 4.2

    def draw_arrow(x1, y1, x2, y2, label=""):
        ax.annotate(
            label,
            xy=(x2, y2),
            xytext=(x1, y1),
            arrowprops=dict(facecolor="#334155", edgecolor="#334155", arrowstyle="->", lw=1.5),
            fontsize=7,
            ha="center",
            va="bottom",
            color="#0F172A",
        )

    is_vision = any(
        k in title.lower() or k in (brief.abstract or "").lower()
        for k in ("image", "vision", "produce", "fruit", "vegetable", "glcm", "opencv", "freshness", "camera", "pixel")
    ) and not any(
        k in title.lower() or k in (brief.abstract or "").lower()
        for k in ("student", "academic", "attendance", "random forest", "tabular", "credit", "churn")
    )

    if is_vision:
        # 1. Input Dataset Stage (Left Top)
        draw_card(
            4,
            52,
            26,
            38,
            "IMAGE ACQUISITION & DATASET",
            "Produce Corpus Ingestion",
            ["Raw RGB Produce Photos", "Quality Annotations", "Fresh / Medium / Spoiled", "Train / Test Data Partition"],
            "#0D9488",
            "#F0FDFA",
            "#0F766E",
        )

        # 2. Image Preprocessing Pipeline (Middle-Left Top)
        draw_card(
            36,
            52,
            28,
            38,
            "PREPROCESSING PIPELINE",
            "Spatial & Color Normalization",
            ["Resolution Standardize (128x128)", "Bilateral & Gaussian Filtering", "Illumination Normalization", "Color Space Conversion (HSV)"],
            "#2563EB",
            "#EFF6FF",
            "#1D4ED8",
        )

        # 3. Feature Extraction Engine (Right-Top)
        draw_card(
            70,
            52,
            26,
            38,
            "FEATURE EXTRACTION ENGINE",
            "Dual-Domain Descriptors",
            ["HSV Color Histograms", "GLCM Contrast & Homogeneity", "GLCM Energy & Correlation", "Normalized Vector (1 x D)"],
            "#D97706",
            "#FFFBEB",
            "#B45309",
        )

        # Arrows row 1
        draw_arrow(30, 71, 36, 71)
        draw_arrow(64, 71, 70, 71)

        # Arrow from row 1 down to row 2
        ax.annotate(
            "Concatenated Descriptors",
            xy=(83, 44),
            xytext=(83, 52),
            arrowprops=dict(facecolor="#334155", edgecolor="#334155", arrowstyle="->", lw=1.5),
            fontsize=7,
            ha="center",
            va="center",
            color="#0F172A",
        )

        # 4. KNN Classification Engine (Right Bottom)
        draw_card(
            70,
            6,
            26,
            38,
            "KNN CLASSIFIER MODULE",
            "Distance Metric Space",
            ["Indexed Training Vectors", "Euclidean Distance Calculation", "KD-Tree Search / Ball-Tree", "K-Nearest Neighbors (K=5)"],
            "#7C3AED",
            "#FAF5FF",
            "#6D28D9",
        )

        # 5. Majority Voting & Prediction (Middle Bottom)
        draw_card(
            36,
            6,
            28,
            38,
            "DECISION & VOTING LOGIC",
            "Confidence & Class Scoring",
            ["Distance-Weighted Voting", "Class Confidence Score %", "Boundary Margin Analysis", "Classification Verification"],
            "#DC2626",
            "#FEF2F2",
            "#B91C1C",
        )

        # 6. Evaluation & Telemetry Subsystem (Left Bottom)
        draw_card(
            4,
            6,
            26,
            38,
            "OUTPUT & EVALUATION",
            "Quality Grading & Telemetry",
            ["Freshness Status Decision", "Confusion Matrix Telemetry", "Accuracy & F1 Verification", "Result Persistence / Display"],
            "#059669",
            "#ECFDF5",
            "#047857",
        )
    else:
        # Tabular / Structured / Random Forest Machine Learning (e.g. ScholarPredict)
        draw_card(
            4,
            52,
            26,
            38,
            "DATASET INGESTION & SCHEMA",
            "Student Academic Records",
            ["Demographics & Attendance %", "Internal Marks & Assignment Scores", "Previous GPA & Study Hours", "Stratified Train/Test Split (80/20)"],
            "#0D9488",
            "#F0FDFA",
            "#0F766E",
        )

        draw_card(
            36,
            52,
            28,
            38,
            "PREPROCESSING & BALANCING",
            "Imputation & Normalization",
            ["Missing-Value Median Imputation", "Categorical One-Hot Encoding", "StandardScaler Normalization", "SMOTE Synthetic Oversampling"],
            "#2563EB",
            "#EFF6FF",
            "#1D4ED8",
        )

        draw_card(
            70,
            52,
            26,
            38,
            "FEATURE IMPORTANCE ENGINE",
            "Correlation & Selection",
            ["Pearson Correlation Analysis", "Gini Impurity Metric Ranking", "Multicollinearity Screening", "Normalized Feature Matrix (X, y)"],
            "#D97706",
            "#FFFBEB",
            "#B45309",
        )

        # Arrows row 1
        draw_arrow(30, 71, 36, 71)
        draw_arrow(64, 71, 70, 71)

        ax.annotate(
            "Balanced Feature Vectors",
            xy=(83, 44),
            xytext=(83, 52),
            arrowprops=dict(facecolor="#334155", edgecolor="#334155", arrowstyle="->", lw=1.5),
            fontsize=7,
            ha="center",
            va="center",
            color="#0F172A",
        )

        draw_card(
            70,
            6,
            26,
            38,
            "RANDOM FOREST ENSEMBLE",
            "Multi-Tree Classifier",
            ["Ensemble of Decorrelated Trees", "Bootstrap Aggregation (Bagging)", "Random Subspace Feature Splits", "Hyperparameter Cross-Validation"],
            "#7C3AED",
            "#FAF5FF",
            "#6D28D9",
        )

        draw_card(
            36,
            6,
            28,
            38,
            "DECISION & VOTING LOGIC",
            "Ensemble Majority Consensus",
            ["Unweighted Tree Majority Voting", "Posterior Class Probabilities", "High / Average / At-Risk Tier", "Confidence Margin Thresholds"],
            "#DC2626",
            "#FEF2F2",
            "#B91C1C",
        )

        draw_card(
            4,
            6,
            26,
            38,
            "INTERVENTION & TELEMETRY",
            "Early Warning & Mentoring",
            ["At-Risk Student Early Alerts", "Faculty Advisory Dashboard", "Accuracy / Recall / F1 Metrics", "Confusion Matrix Verification"],
            "#059669",
            "#ECFDF5",
            "#047857",
        )

    # Arrows row 2
    draw_arrow(70, 25, 64, 25)
    draw_arrow(36, 25, 30, 25)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(str(out_file), dpi=200, facecolor="white", edgecolor="none")
    plt.close(fig)
    return out_file


def render_ml_data_flow_diagram_mpl(brief: ProjectBrief, facts: ProjectFacts, out_file: Path) -> Path:
    """Render Data Flow and Feature Pipeline Interaction Diagram for ML projects (replaces wiring diagram)."""
    fig, ax = plt.subplots(figsize=(10, 6.5), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    title = brief.project.title
    ax.text(
        50,
        96,
        f"{title.upper()} - DATA FLOW & FEATURE INTERACTION DIAGRAM",
        fontsize=11.5,
        fontweight="bold",
        ha="center",
        va="top",
        color="#1E293B",
    )

    def draw_node(x, y, w, h, title, subtitle, col_border, col_bg, col_title):
        box = patches.FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.5,rounding_size=1.5",
            edgecolor=col_border,
            facecolor=col_bg,
            lw=1.6,
        )
        ax.add_patch(box)
        ax.text(x + w / 2, y + h / 2 + 2, title, fontsize=8, fontweight="bold", ha="center", va="center", color=col_title)
        if subtitle:
            ax.text(x + w / 2, y + h / 2 - 3.5, subtitle, fontsize=7, ha="center", va="center", color="#475569")

    def draw_stream(x1, y1, x2, y2, label=""):
        ax.annotate(
            label,
            xy=(x2, y2),
            xytext=(x1, y1),
            arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.4),
            fontsize=7,
            fontweight="bold",
            ha="center",
            va="center",
            color="#1E293B",
        )

    is_vision = any(
        k in title.lower() or k in (brief.abstract or "").lower()
        for k in ("image", "vision", "produce", "fruit", "vegetable", "glcm", "opencv", "freshness", "camera", "pixel")
    ) and not any(
        k in title.lower() or k in (brief.abstract or "").lower()
        for k in ("student", "academic", "attendance", "random forest", "tabular", "credit", "churn")
    )

    if is_vision:
        # 1. Image Input
        draw_node(4, 46, 18, 18, "IMAGE INPUT", "Produce Photo Stream", "#0F766E", "#F0FDFA", "#115E59")

        # 2. Preprocessing
        draw_node(27, 46, 20, 18, "PREPROCESSING", "Resize & Denoise", "#2563EB", "#EFF6FF", "#1D4ED8")
        draw_stream(22, 55, 27, 55, "Raw Image")

        # 3. Dual feature extractors
        draw_node(53, 66, 22, 18, "COLOR DESCRIPTOR", "HSV / RGB Histogram", "#D97706", "#FFFBEB", "#B45309")
        draw_node(53, 26, 22, 18, "TEXTURE DESCRIPTOR", "GLCM Matrix Analysis", "#7C3AED", "#FAF5FF", "#6D28D9")

        ax.plot([47, 50, 50, 53], [55, 55, 75, 75], color="#475569", lw=1.4)
        ax.annotate("", xy=(53, 75), xytext=(50, 75), arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.4))
        ax.text(49, 78, "Color Stream", fontsize=6.8, color="#B45309", ha="center", fontweight="bold")

        ax.plot([47, 50, 50, 53], [55, 55, 35, 35], color="#475569", lw=1.4)
        ax.annotate("", xy=(53, 35), xytext=(50, 35), arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.4))
        ax.text(49, 31, "Spatial Stream", fontsize=6.8, color="#6D28D9", ha="center", fontweight="bold")

        # 4. Feature Vector Concatenation
        draw_node(80, 46, 17, 18, "FEATURE VECTOR", "Fused Normalization", "#DC2626", "#FEF2F2", "#B91C1C")

        ax.plot([75, 78, 78, 80], [75, 75, 55, 55], color="#475569", lw=1.4)
        ax.annotate("", xy=(80, 55), xytext=(78, 55), arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.4))

        ax.plot([75, 78, 78, 80], [35, 35, 55, 55], color="#475569", lw=1.4)
        ax.annotate("", xy=(80, 55), xytext=(78, 55), arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.4))

        # 5. Lower pipeline: KNN inference & Corpus
        draw_node(53, 4, 22, 14, "KNN INFERENCE", "Euclidean Metric (K=5)", "#0284C7", "#F0F9FF", "#0369A1")
        draw_node(80, 4, 17, 14, "TRAINING CORPUS", "Indexed Vectors", "#475569", "#F8FAFC", "#334155")
        draw_stream(80, 11, 75, 11, "Reference Set")

        ax.plot([88, 88, 64], [46, 23, 23], color="#475569", lw=1.4)
        ax.annotate("", xy=(64, 18), xytext=(64, 23), arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.4))
        ax.text(82, 25, "1 x D Vector", fontsize=6.8, color="#DC2626", fontweight="bold")

        # 6. Output Decision Box
        draw_node(12, 4, 26, 14, "PREDICTION OUTPUT", "Freshness Quality Grade", "#15803D", "#F0FDF4", "#166534")
        draw_stream(53, 11, 38, 11, "Class Probability")
    else:
        # Tabular / Random Forest ML (e.g. ScholarPredict)
        draw_node(4, 46, 18, 18, "DATA INPUT", "Student Records Stream", "#0F766E", "#F0FDFA", "#115E59")

        draw_node(27, 46, 20, 18, "PREPROCESSING", "Clean & SMOTE Balance", "#2563EB", "#EFF6FF", "#1D4ED8")
        draw_stream(22, 55, 27, 55, "Raw Records")

        draw_node(53, 66, 22, 18, "ACADEMIC METRICS", "Marks & Prior GPA", "#D97706", "#FFFBEB", "#B45309")
        draw_node(53, 26, 22, 18, "ENGAGEMENT METRICS", "Attendance & Study Hours", "#7C3AED", "#FAF5FF", "#6D28D9")

        ax.plot([47, 50, 50, 53], [55, 55, 75, 75], color="#475569", lw=1.4)
        ax.annotate("", xy=(53, 75), xytext=(50, 75), arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.4))
        ax.text(49, 78, "Marks Stream", fontsize=6.8, color="#B45309", ha="center", fontweight="bold")

        ax.plot([47, 50, 50, 53], [55, 55, 35, 35], color="#475569", lw=1.4)
        ax.annotate("", xy=(53, 35), xytext=(50, 35), arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.4))
        ax.text(49, 31, "Behavioral", fontsize=6.8, color="#6D28D9", ha="center", fontweight="bold")

        draw_node(80, 46, 17, 18, "FEATURE MATRIX", "Standardized X_input", "#DC2626", "#FEF2F2", "#B91C1C")

        ax.plot([75, 78, 78, 80], [75, 75, 55, 55], color="#475569", lw=1.4)
        ax.annotate("", xy=(80, 55), xytext=(78, 55), arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.4))

        ax.plot([75, 78, 78, 80], [35, 35, 55, 55], color="#475569", lw=1.4)
        ax.annotate("", xy=(80, 55), xytext=(78, 55), arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.4))

        draw_node(53, 4, 22, 14, "RANDOM FOREST", "Multi-Tree Ensemble", "#0284C7", "#F0F9FF", "#0369A1")
        draw_node(80, 4, 17, 14, "TRAINING SPLIT", "Stratified Corpus (80%)", "#475569", "#F8FAFC", "#334155")
        draw_stream(80, 11, 75, 11, "Training Fit")

        ax.plot([88, 88, 64], [46, 23, 23], color="#475569", lw=1.4)
        ax.annotate("", xy=(64, 18), xytext=(64, 23), arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.4))
        ax.text(82, 25, "Feature Array", fontsize=6.8, color="#DC2626", fontweight="bold")

        draw_node(12, 4, 26, 14, "PREDICTION OUTPUT", "High / Average / At-Risk", "#15803D", "#F0FDF4", "#166534")
        draw_stream(53, 11, 38, 11, "Majority Vote")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(str(out_file), dpi=200, facecolor="white", edgecolor="none")
    plt.close(fig)
    return out_file


def render_ml_flowchart_mpl(brief: ProjectBrief, facts: ProjectFacts, out_file: Path) -> Path:
    """Render Machine Learning Classification and Inference Flowchart."""
    fig, ax = plt.subplots(figsize=(9, 11), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    is_vision = any(
        k in title.lower() or k in (brief.abstract or "").lower()
        for k in ("image", "vision", "produce", "fruit", "vegetable", "glcm", "opencv", "freshness", "camera", "pixel")
    ) and not any(
        k in title.lower() or k in (brief.abstract or "").lower()
        for k in ("student", "academic", "attendance", "random forest", "tabular", "credit", "churn")
    )
    chart_title = f"{title.upper()} - KNN CLASSIFICATION FLOWCHART" if is_vision else f"{title.upper()} - RANDOM FOREST INFERENCE FLOWCHART"

    title = brief.project.title
    ax.text(
        50,
        98,
        chart_title,
        fontsize=10.5,
        fontweight="bold",
        ha="center",
        va="top",
        color="#1E293B",
    )

    def draw_oval(x, y, text, color="#0D9488", bg="#F0FDFA"):
        box = patches.FancyBboxPatch(
            (x - 22, y - 2.5),
            44,
            5,
            boxstyle="round,pad=0.4,rounding_size=2.5",
            edgecolor=color,
            facecolor=bg,
            lw=1.5,
        )
        ax.add_patch(box)
        ax.text(x, y, text, fontsize=7.5, fontweight="bold", ha="center", va="center", color=color)

    def draw_rect(x, y, w, h, text, color="#2563EB", bg="#EFF6FF"):
        box = patches.FancyBboxPatch(
            (x - w / 2, y - h / 2),
            w,
            h,
            boxstyle="round,pad=0.3,rounding_size=0.8",
            edgecolor=color,
            facecolor=bg,
            lw=1.4,
        )
        ax.add_patch(box)
        ax.text(x, y, text, fontsize=7.2, ha="center", va="center", color="#1E293B")

    def draw_diamond(x, y, w, h, text, color="#D97706", bg="#FFFBEB"):
        diamond = patches.Polygon(
            [[x, y + h / 2], [x + w / 2, y], [x, y - h / 2], [x - w / 2, y]],
            edgecolor=color,
            facecolor=bg,
            lw=1.4,
        )
        ax.add_patch(diamond)
        ax.text(x, y, text, fontsize=6.8, fontweight="bold", ha="center", va="center", color="#92400E")

    def draw_arrow(x1, y1, x2, y2, label=""):
        ax.annotate(
            label,
            xy=(x2, y2),
            xytext=(x1, y1),
            arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.3),
            fontsize=7,
            ha="center",
            va="center",
            color="#0F172A",
        )

    if is_vision:
        # 1. Start
        draw_oval(50, 93, "START / INGEST PRODUCE IMAGE")
        draw_arrow(50, 90.5, 50, 86.5)

        # 2. Load & Validate
        draw_rect(50, 84, 52, 5, "Load Input Produce Image & Validate Dimensions\n(RGB Channels, Resolution Verification)")
        draw_arrow(50, 81.5, 50, 77.5)

        # 3. Preprocess
        draw_rect(50, 75, 52, 5, "Image Preprocessing Pipeline\n(Bilateral Denoising & Resize to 128x128 Grid)")
        draw_arrow(50, 72.5, 50, 68.5)

        # 4. Color Extraction
        draw_rect(50, 66, 52, 5, "Color Feature Extraction\n(Convert RGB to HSV, Compute Hue/Saturation Histograms)")
        draw_arrow(50, 63.5, 50, 59.5)

        # 5. Texture Extraction
        draw_rect(50, 57, 52, 5, "Texture Feature Extraction (GLCM)\n(Compute Matrix, Extract Contrast, Homogeneity, Energy)")
        draw_arrow(50, 54.5, 50, 50.5)

        # 6. Feature Synthesis & Normalization
        draw_rect(50, 48, 52, 5, "Vector Synthesis & MinMax / Standard Scaling\n(Generate Unified Normalized 1xD Feature Array X_input)")
        draw_arrow(50, 45.5, 50, 41.5)

        # 7. KNN Search
        draw_rect(50, 39, 52, 5, "KNN Metric Query against Reference Corpus\n(Compute Euclidean Distances D_i to Training Exemplars)")
        draw_arrow(50, 36.5, 50, 32.5)

        # 8. Select Neighbors
        draw_rect(50, 30, 52, 5, "Sort Distances & Identify K Nearest Neighbors\n(Select K=5 Closest Reference Vectors)")
        draw_arrow(50, 27.5, 50, 23.5)

        # 9. Decision: Majority Voting
        draw_diamond(50, 19, 32, 7.5, "Is Class Vote\nUnanimous?")

        # Branches
        draw_arrow(66, 19, 80, 19, "YES")
        draw_rect(80, 12, 26, 5, "Assign Majority Class\n(Confidence >= 90%)", color="#059669", bg="#ECFDF5")

        draw_arrow(34, 19, 20, 19, "NO")
        draw_rect(20, 12, 26, 5, "Distance-Weighted Vote\n(Relative Class Probabilities)", color="#D97706", bg="#FFFBEB")

        # Converge to final output
        draw_arrow(80, 9.5, 60, 4)
        draw_arrow(20, 9.5, 40, 4)

        draw_oval(50, 3.5, "OUTPUT PREDICTED GRADE & TELEMETRY", color="#15803D", bg="#F0FDF4")
    else:
        # Tabular / Random Forest ML (e.g. ScholarPredict)
        draw_oval(50, 93, "START / INGEST STUDENT RECORD")
        draw_arrow(50, 90.5, 50, 86.5)

        draw_rect(50, 84, 54, 5, "Load Student Demographic & Academic Dataset\n(Attendance %, Internal Marks, Study Hours, Activities)")
        draw_arrow(50, 81.5, 50, 77.5)

        draw_rect(50, 75, 54, 5, "Data Cleaning & Missing-Value Imputation\n(Median Imputation for Continuous, Mode for Categorical)")
        draw_arrow(50, 72.5, 50, 68.5)

        draw_rect(50, 66, 54, 5, "Feature Engineering & Categorical Encoding\n(One-Hot Encoding, Label Encoding, Attribute Extraction)")
        draw_arrow(50, 63.5, 50, 59.5)

        draw_rect(50, 57, 54, 5, "SMOTE Synthetic Minority Oversampling\n(Balance At-Risk / Minority Classes to Mitigate Bias)")
        draw_arrow(50, 54.5, 50, 50.5)

        draw_rect(50, 48, 54, 5, "Standard Scaling & Stratified Partition\n(StandardScaler Normalization, 80/20 Train/Test Split)")
        draw_arrow(50, 45.5, 50, 41.5)

        draw_rect(50, 39, 54, 5, "Random Forest Multi-Tree Ensemble Evaluation\n(Propagate Input Vector across B Decorrelated Decision Trees)")
        draw_arrow(50, 36.5, 50, 32.5)

        draw_rect(50, 30, 54, 5, "Ensemble Aggregation & Majority Voting\n(Aggregate Tree Traversal Paths & Class Probability P(c|x))")
        draw_arrow(50, 27.5, 50, 23.5)

        draw_diamond(50, 19, 32, 7.5, "Is Predicted Class\n'At-Risk'?")

        draw_arrow(66, 19, 80, 19, "YES")
        draw_rect(80, 12, 28, 5, "Trigger Early Warning Alert\n(Faculty Mentoring Plan)", color="#DC2626", bg="#FEF2F2")

        draw_arrow(34, 19, 20, 19, "NO")
        draw_rect(20, 12, 28, 5, "Assign Standard Category\n(High / Average Tier)", color="#059669", bg="#ECFDF5")

        draw_arrow(80, 9.5, 60, 4)
        draw_arrow(20, 9.5, 40, 4)

        draw_oval(50, 3.5, "OUTPUT PREDICTION, EXPLANATION & MENTOR TELEMETRY", color="#15803D", bg="#F0FDF4")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(str(out_file), dpi=200, facecolor="white", edgecolor="none")
    plt.close(fig)
    return out_file


def render_software_block_diagram_mpl(brief: ProjectBrief, facts: ProjectFacts, out_file: Path) -> Path:
    """Render 4-tier / Layered Software Architecture Block Diagram with generous spacing and crisp typography."""
    fig, ax = plt.subplots(figsize=(12, 7.5), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_xlim(0, 100)
    ax.set_ylim(100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    title = brief.project.title
    domain = (brief.domain or "SOFTWARE_APP").upper()
    ax.text(
        50,
        96.5,
        f"{title.upper()} - LAYERED SYSTEM ARCHITECTURE",
        fontsize=12,
        fontweight="bold",
        ha="center",
        va="top",
        color="#1E293B",
    )

    def draw_layer(y, h, title, subtitle, details, border_col, bg_col, text_col):
        box = patches.FancyBboxPatch(
            (7, y),
            86,
            h,
            boxstyle="round,pad=0.6,rounding_size=1.5",
            edgecolor=border_col,
            facecolor=bg_col,
            lw=1.8,
        )
        ax.add_patch(box)
        ax.text(11, y + h - 3.8, title, fontsize=9.5, fontweight="bold", ha="left", va="top", color=text_col)
        ax.text(11, y + h - 7.6, subtitle, fontsize=8.0, fontstyle="italic", ha="left", va="top", color="#475569")
        ax.text(11, y + 3.2, details, fontsize=7.8, ha="left", va="bottom", color="#1E293B")

    if domain in ("WEB_CLOUD", "SOFTWARE_APP") and any(k in title.lower() for k in ("flutter", "app", "parking", "finder", "web", "cloud")):
        l1_title, l1_sub, l1_det = (
            "PRESENTATION & CLIENT APPLICATION TIER",
            "Cross-Platform Client Interface & User Experience",
            "• Flutter Cross-Platform Client (Android & iOS)   • Interactive Google Maps & Slot View   • Responsive UI State Management",
        )
        l2_title, l2_sub, l2_det = (
            "APPLICATION CONTROLLER & ROUTING TIER",
            "State Management, API Endpoints & Request Dispatcher",
            "• Provider / BLoC State Management   • Dynamic Route Navigation   • Session Management & Authentication Interceptors",
        )
        l3_title, l3_sub, l3_det = (
            "DOMAIN SERVICE & BUSINESS LOGIC TIER",
            "Core Algorithmic Workflows & Cloud Transactions",
            "• Real-Time Slot Reservation Logic   • Dynamic Fee Calculation Engine   • QR Code Access Token Verification Subsystem",
        )
        l4_title, l4_sub, l4_det = (
            "DATA PERSISTENCE & CLOUD SERVICES TIER",
            "Cloud Firestore Database & Backend Services Integration",
            "• Cloud Firestore Real-Time NoSQL Database   • Firebase Authentication Services   • Firebase Cloud Messaging (FCM) Push Telemetry",
        )
    else:
        l1_title, l1_sub, l1_det = (
            "PRESENTATION LAYER (UI / CLIENT INTERFACE)",
            "User Interaction, Form Layouts & Display Output",
            "• Graphical User Interface / Web Views   • Input Validation & Data Sanitization   • Formatted Status & Results Presentation",
        )
        l2_title, l2_sub, l2_det = (
            "APPLICATION CONTROLLER LAYER",
            "Business Workflow Orchestration & Request Routing",
            "• Request Routing & Dispatch Handlers   • Session State Management   • Exception & Error Recovery Logging",
        )
        l3_title, l3_sub, l3_det = (
            "DOMAIN SERVICE & COMPUTATION LAYER",
            "Core Algorithmic Processing & Calculation Services",
            "• Business Transaction Processing Engine   • Data Validation & Integrity Checks   • Algorithmic Calculations & Transformations",
        )
        l4_title, l4_sub, l4_det = (
            "DATA PERSISTENCE & STORAGE LAYER",
            "Data Access Object (DAO) & Persistent Record Stores",
            "• Structured Database Storage / JSON Records   • ACID Transaction Integrity   • Schema Serialization & Cache Access",
        )

    draw_layer(73, 16, l1_title, l1_sub, l1_det, "#0D9488", "#F0FDFA", "#0F766E")
    draw_layer(50, 16, l2_title, l2_sub, l2_det, "#2563EB", "#EFF6FF", "#1D4ED8")
    draw_layer(27, 16, l3_title, l3_sub, l3_det, "#D97706", "#FFFBEB", "#B45309")
    draw_layer(4, 16, l4_title, l4_sub, l4_det, "#7C3AED", "#FAF5FF", "#6D28D9")

    # Inter-layer connecting arrows in the clear 7-unit gaps
    for y_gap in [66, 43, 20]:
        ax.annotate(
            "",
            xy=(50, y_gap + 0.8),
            xytext=(50, y_gap + 6.2),
            arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="<->", lw=1.8),
        )

    out_file.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(str(out_file), dpi=200, facecolor="white", edgecolor="none")
    plt.close(fig)
    return out_file


def render_software_data_flow_diagram_mpl(brief: ProjectBrief, facts: ProjectFacts, out_file: Path) -> Path:
    """Render Component Data Flow and Interaction Diagram with spacious layout and clear arrow channels."""
    fig, ax = plt.subplots(figsize=(12, 7.5), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    title = brief.project.title
    domain = (brief.domain or "SOFTWARE_APP").upper()
    ax.text(
        50,
        96.5,
        f"{title.upper()} - COMPONENT DATA FLOW & INTERACTION DIAGRAM",
        fontsize=12,
        fontweight="bold",
        ha="center",
        va="top",
        color="#1E293B",
    )

    def draw_box(x, y, w, h, t, sub, bcol, bgcol, tcol):
        box = patches.FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.6,rounding_size=1.5",
            edgecolor=bcol,
            facecolor=bgcol,
            lw=1.6,
        )
        ax.add_patch(box)
        ax.text(x + w / 2, y + h / 2 + 3, t, fontsize=8.5, fontweight="bold", ha="center", va="center", color=tcol)
        ax.text(x + w / 2, y + h / 2 - 4, sub, fontsize=7.2, ha="center", va="center", color="#475569")

    # Distinct boxes with generous dimensions
    if domain in ("WEB_CLOUD", "SOFTWARE_APP") and any(k in title.lower() for k in ("flutter", "app", "parking", "finder", "web", "cloud")):
        draw_box(4, 34, 18, 30, "MOBILE CLIENT\n(Flutter App)", "User Maps & UI Form", "#0F766E", "#F0FDFA", "#115E59")
        draw_box(29, 34, 18, 30, "API ROUTER &\nCONTROLLER", "State & Dispatcher", "#2563EB", "#EFF6FF", "#1D4ED8")
        draw_box(55, 55, 20, 26, "CORE LOGIC\nSERVICES", "Slot Reservation Engine", "#D97706", "#FFFBEB", "#B45309")
        draw_box(55, 15, 20, 26, "AUTH & TOKEN\nSECURITY", "Firebase Auth / Claims", "#7C3AED", "#FAF5FF", "#6D28D9")
        draw_box(82, 34, 15, 30, "CLOUD\nFIRESTORE", "NoSQL Data Store", "#DC2626", "#FEF2F2", "#B91C1C")
    else:
        draw_box(4, 34, 18, 30, "CLIENT / UI\nINTERFACE", "Form & Command Input", "#0F766E", "#F0FDFA", "#115E59")
        draw_box(29, 34, 18, 30, "REQUEST ROUTER\n& DISPATCHER", "Validation & Dispatch", "#2563EB", "#EFF6FF", "#1D4ED8")
        draw_box(55, 55, 20, 26, "DOMAIN SERVICE\nENGINE", "Core Business Rules", "#D97706", "#FFFBEB", "#B45309")
        draw_box(55, 15, 20, 26, "SECURITY & HASH\nCONTROLLER", "Integrity & Permissions", "#7C3AED", "#FAF5FF", "#6D28D9")
        draw_box(82, 34, 15, 30, "PERSISTENCE\nSTORE", "Database / Storage", "#DC2626", "#FEF2F2", "#B91C1C")

    # Spacious Arrow Connections with separate labels above/below paths
    # 1. Client -> Router (forward request)
    ax.annotate("", xy=(29, 52), xytext=(22, 52), arrowprops=dict(facecolor="#334155", edgecolor="#334155", arrowstyle="->", lw=1.6))
    ax.text(25.5, 55.5, "User Action", fontsize=7.2, fontweight="bold", ha="center", color="#0F172A")

    # 2. Router -> Client (response state)
    ax.annotate("", xy=(22, 44), xytext=(29, 44), arrowprops=dict(facecolor="#334155", edgecolor="#334155", arrowstyle="->", lw=1.6))
    ax.text(25.5, 40.5, "UI Update", fontsize=7.2, fontweight="bold", ha="center", color="#0F172A")

    # 3. Router -> Domain Logic
    ax.annotate("", xy=(55, 66), xytext=(47, 54), arrowprops=dict(facecolor="#334155", edgecolor="#334155", arrowstyle="->", lw=1.6))
    ax.text(49.5, 63.5, "Execute", fontsize=7.2, fontweight="bold", ha="center", color="#0F172A")

    # 4. Router -> Auth Security
    ax.annotate("", xy=(55, 28), xytext=(47, 42), arrowprops=dict(facecolor="#334155", edgecolor="#334155", arrowstyle="->", lw=1.6))
    ax.text(49.5, 32.5, "Verify", fontsize=7.2, fontweight="bold", ha="center", color="#0F172A")

    # 5. Domain Logic -> Data Store (Commit)
    ax.annotate("", xy=(82, 54), xytext=(75, 66), arrowprops=dict(facecolor="#334155", edgecolor="#334155", arrowstyle="->", lw=1.6))
    ax.text(80.5, 63.5, "Write", fontsize=7.2, fontweight="bold", ha="center", color="#0F172A")

    # 6. Data Store -> Auth/Logic (Query / Fetch)
    ax.annotate("", xy=(75, 28), xytext=(82, 42), arrowprops=dict(facecolor="#334155", edgecolor="#334155", arrowstyle="->", lw=1.6))
    ax.text(80.5, 32.5, "Read", fontsize=7.2, fontweight="bold", ha="center", color="#0F172A")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(str(out_file), dpi=200, facecolor="white", edgecolor="none")
    plt.close(fig)
    return out_file


def render_software_flowchart_mpl(brief: ProjectBrief, facts: ProjectFacts, out_file: Path) -> Path:
    """Render Software Application Request-Execution Flowchart with generous vertical breathing room."""
    fig, ax = plt.subplots(figsize=(9.6, 12.0), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    title = brief.project.title
    ax.text(
        50,
        98,
        f"{title.upper()} - APPLICATION EXECUTION FLOWCHART",
        fontsize=10.5,
        fontweight="bold",
        ha="center",
        va="top",
        color="#1E293B",
    )

    def draw_oval(x, y, text, color="#0D9488", bg="#F0FDFA"):
        box = patches.FancyBboxPatch(
            (x - 22, y - 2.5),
            44,
            5,
            boxstyle="round,pad=0.4,rounding_size=2.5",
            edgecolor=color,
            facecolor=bg,
            lw=1.5,
        )
        ax.add_patch(box)
        ax.text(x, y, text, fontsize=7.5, fontweight="bold", ha="center", va="center", color=color)

    def draw_rect(x, y, w, h, text, color="#2563EB", bg="#EFF6FF"):
        box = patches.FancyBboxPatch(
            (x - w / 2, y - h / 2),
            w,
            h,
            boxstyle="round,pad=0.3,rounding_size=0.8",
            edgecolor=color,
            facecolor=bg,
            lw=1.4,
        )
        ax.add_patch(box)
        ax.text(x, y, text, fontsize=7.2, ha="center", va="center", color="#1E293B")

    def draw_diamond(x, y, w, h, text, color="#D97706", bg="#FFFBEB"):
        diamond = patches.Polygon(
            [[x, y + h / 2], [x + w / 2, y], [x, y - h / 2], [x - w / 2, y]],
            edgecolor=color,
            facecolor=bg,
            lw=1.4,
        )
        ax.add_patch(diamond)
        ax.text(x, y, text, fontsize=6.8, fontweight="bold", ha="center", va="center", color="#92400E")

    def draw_arrow(x1, y1, x2, y2, label=""):
        ax.annotate(
            label,
            xy=(x2, y2),
            xytext=(x1, y1),
            arrowprops=dict(facecolor="#475569", edgecolor="#475569", arrowstyle="->", lw=1.3),
            fontsize=7,
            ha="center",
            va="center",
            color="#0F172A",
        )

    # 1. Start
    draw_oval(50, 93, "START / SYSTEM INIT")
    draw_arrow(50, 90.5, 50, 86.5)

    # 2. Ingest Request
    draw_rect(50, 84, 52, 5, "Receive User Request & Ingest Parameters\n(CLI Command / Interface Input Form)")
    draw_arrow(50, 81.5, 50, 77.5)

    # 3. Validate
    draw_diamond(50, 72, 32, 7.5, "Input Valid & Authorized?")

    # Failure Branch
    draw_arrow(34, 72, 18, 72, "NO")
    draw_rect(18, 64, 22, 5, "Log Error & Reject\nOutput Warning Notice", color="#DC2626", bg="#FEF2F2")
    draw_arrow(18, 61.5, 18, 12)
    draw_arrow(18, 12, 34, 4)

    # Success Branch
    draw_arrow(50, 68, 50, 63, "YES")

    # 4. Business Transaction
    draw_rect(50, 59, 52, 6, "Execute Application Service Logic\n(Process Account Operation / Model Calculation)")
    draw_arrow(50, 56, 50, 51)

    # 5. Security & Verification
    draw_rect(50, 48, 52, 5, "Apply Integrity Checks & Hash Verification\n(SHA-256 Checksum / State Validation)")
    draw_arrow(50, 45.5, 50, 40.5)

    # 6. Data Store Persistence
    draw_rect(50, 37, 52, 6, "Commit Transaction to Persistence Store\n(Atomic Write to Data Repository / File System)")
    draw_arrow(50, 34, 50, 29)

    # 7. Response Format
    draw_rect(50, 25, 52, 6, "Serialize Response Payload & Status Telemetry\n(Format Human-Readable Output / JSON Data)")
    draw_arrow(50, 22, 50, 16)

    # 8. Output Display
    draw_rect(50, 13, 52, 5, "Deliver Response to Client Interface & Log Event")
    draw_arrow(50, 10.5, 50, 6.5)

    draw_oval(50, 4, "END / AWAIT NEXT INVOCATION", color="#15803D", bg="#F0FDF4")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(str(out_file), dpi=200, facecolor="white", edgecolor="none")
    plt.close(fig)
    return out_file


# ==============================================================================
# MAIN DIAGRAM GENERATION ENTRYPOINT
# ==============================================================================


def generate_all_diagrams(
    brief: ProjectBrief, facts: ProjectFacts, out_dir: Union[str, Path] = "out"
) -> Dict[str, Path]:
    """Generate Block Diagram, Connection Diagram, and Flowchart.

    Uses Graphviz if installed, falling back cleanly to Matplotlib at 200 DPI.
    All diagrams dynamically adapt to the project brief.
    """
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    block_file = out_dir_path / "block_diagram.png"
    conn_file = out_dir_path / "connection_diagram.png"
    flow_file = out_dir_path / "flowchart.png"

    domain = (brief.domain or "EMBEDDED_HARDWARE").upper()

    if domain in ("MACHINE_LEARNING", "DATA_ANALYTICS"):
        logger.info(f"Rendering Machine Learning pipeline diagrams at 200 DPI (domain: {domain})...")
        render_ml_block_diagram_mpl(brief, facts, block_file)
        render_ml_data_flow_diagram_mpl(brief, facts, conn_file)
        render_ml_flowchart_mpl(brief, facts, flow_file)
    elif domain in ("SOFTWARE_APP", "WEB_CLOUD"):
        logger.info(f"Rendering Software layered architecture diagrams at 200 DPI (domain: {domain})...")
        render_software_block_diagram_mpl(brief, facts, block_file)
        render_software_data_flow_diagram_mpl(brief, facts, conn_file)
        render_software_flowchart_mpl(brief, facts, flow_file)
    else:
        logger.info("Using Hardware diagram generator at 200 DPI...")
        render_block_diagram_mpl(brief, facts, block_file)
        render_connection_diagram_mpl(brief, facts, conn_file)
        render_flowchart_mpl(brief, facts, flow_file)

    logger.info(f"Generated diagrams successfully in {out_dir_path}")
    return {
        "block_diagram": block_file,
        "connection_diagram": conn_file,
        "flowchart": flow_file,
    }

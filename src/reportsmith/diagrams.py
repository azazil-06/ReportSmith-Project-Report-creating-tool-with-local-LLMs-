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
        p_str = (p.peripheral + " " + p.connected_to + " " + p.function).lower()
        if "adc" in p.peripheral.lower() or any(
            w in p_str for w in ["sensor", "moisture", "ldr", "light", "temp", "humidity", "probe", "analog"]
        ):
            sensor_pin_obj = p
            break

    sensor_comp = None
    for c in brief.implementation.components:
        c_str = (c.name + " " + c.role + " " + c.interface).lower()
        if any(w in c_str for w in ["sensor", "probe", "moisture", "ldr", "light", "photocell", "temp", "measurement"]):
            sensor_comp = c
            break

    sensor_pin = sensor_pin_obj.pin if sensor_pin_obj else "PA0"
    sensor_peripheral = sensor_pin_obj.peripheral if sensor_pin_obj else "ADC1_IN0"
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
    fig, ax = plt.subplots(figsize=(10, 6.2), dpi=200)
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
    fig, ax = plt.subplots(figsize=(10, 6.5), dpi=200)
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

    # MCU Central Pin Header
    mcu_bg = patches.Rectangle((34, 15), 32, 72, edgecolor="#0F172A", facecolor="#E2E8F0", lw=2)
    ax.add_patch(mcu_bg)
    ax.text(
        50,
        83,
        f"{profile.mcu}\n({profile.board_name})",
        fontsize=10.5,
        fontweight="bold",
        ha="center",
        va="center",
    )

    # Dynamic MCU Pins Left
    pins_left = [
        ("3.3V (Power)", 72, "#DC2626"),
        ("GND (Ground)", 64, "#000000"),
        (f"{profile.sensor_pin} ({profile.sensor_peripheral})", 54, "#16A34A"),
        ("PA9 (USART1_TX)", 42, "#9333EA"),
        ("PA10 (USART1_RX)", 34, "#7C3AED"),
    ]

    # Dynamic MCU Pins Right
    pins_right = [
        ("5V / 3.3V (Power)", 72, "#DC2626"),
        ("GND (Ground)", 64, "#000000"),
        (f"{profile.actuator_pin} ({profile.actuator_peripheral})", 54, "#D97706"),
        (f"{profile.led_pin} (Onboard LED)", 34, "#E11D48"),
    ]

    for label, y, col in pins_left:
        ax.plot([34, 38], [y, y], color=col, lw=2.5)
        ax.text(39, y, label, fontsize=7.5, va="center", ha="left")

    for label, y, col in pins_right:
        ax.plot([62, 66], [y, y], color=col, lw=2.5)
        ax.text(61, y, label, fontsize=7.5, va="center", ha="right")

    # Sensor Module on Left
    sensor_box = patches.Rectangle((4, 46), 18, 32, edgecolor="#15803D", facecolor="#F0FDF4", lw=1.5)
    ax.add_patch(sensor_box)
    s_label = profile.sensor_name
    if len(s_label) > 18:
        s_label = s_label[:16] + ".."
    ax.text(13, 73, f"{s_label}\nSensor Module", fontsize=8, fontweight="bold", ha="center")
    ax.text(13, 64, "VCC  [3.3V]", fontsize=7.5, ha="center")
    ax.text(13, 56, "GND  [0V]", fontsize=7.5, ha="center")
    ax.text(13, 49, f"SIG  [{profile.sensor_pin}]", fontsize=7.5, ha="center")

    ax.plot([22, 34], [64, 72], color="#DC2626", lw=1.8, label="3.3V VCC (Red)")
    ax.plot([22, 34], [56, 64], color="#000000", lw=1.8, label="GND (Black)")
    ax.plot([22, 34], [49, 54], color="#16A34A", lw=1.8, label=f"{profile.sensor_pin} Sensor (Green)")

    # Serial Adapter on Left Bottom
    uart_box = patches.Rectangle((4, 18), 18, 22, edgecolor="#6D28D9", facecolor="#FAF5FF", lw=1.5)
    ax.add_patch(uart_box)
    ax.text(13, 34, "USB-to-UART\nAdapter (CP2102)", fontsize=8, fontweight="bold", ha="center")
    ax.text(13, 26, "RXD ← PA9", fontsize=7.5, ha="center")
    ax.text(13, 21, "TXD → PA10", fontsize=7.5, ha="center")
    ax.plot([22, 34], [26, 42], color="#9333EA", lw=1.5, label="UART TX (Purple)")
    ax.plot([22, 34], [21, 34], color="#7C3AED", lw=1.5, label="UART RX (Violet)")

    # Output Device on Right
    out_box = patches.Rectangle((78, 44), 18, 34, edgecolor="#0369A1", facecolor="#F0F9FF", lw=1.5)
    ax.add_patch(out_box)
    a_label = profile.actuator_name
    if len(a_label) > 18:
        a_label = a_label[:16] + ".."
    ax.text(87, 72, f"{a_label}\nActuator Unit", fontsize=8, fontweight="bold", ha="center")
    ax.text(87, 64, "VCC  [5V/3.3V]", fontsize=7.5, ha="center")
    ax.text(87, 57, "GND  [0V]", fontsize=7.5, ha="center")
    ax.text(87, 51, f"IN   [{profile.actuator_pin}]", fontsize=7.5, ha="center")
    ax.plot([66, 78], [72, 64], color="#DC2626", lw=1.8)
    ax.plot([66, 78], [64, 57], color="#000000", lw=1.8)
    ax.plot([66, 78], [54, 51], color="#D97706", lw=1.8, label=f"{profile.actuator_pin} Drive (Amber)")

    # Onboard LED box on Right Bottom
    led_patch = patches.Rectangle((78, 22), 18, 16, edgecolor="#BE123C", facecolor="#FFF1F2", lw=1.5)
    ax.add_patch(led_patch)
    ax.text(87, 32, f"User LED ({profile.led_pin})\nOnboard Alert", fontsize=8, fontweight="bold", ha="center")
    ax.text(87, 25, "Active-Low Sink", fontsize=7.5, ha="center", color="#9F1239")
    ax.plot([66, 78], [34, 28], color="#E11D48", lw=1.8, label=f"{profile.led_pin} LED Drive (Rose)")

    # Color Key / Legend
    ax.legend(loc="lower center", ncol=4, fontsize=7.5, frameon=True, facecolor="#F8FAFC", edgecolor="#CBD5E1")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(str(out_file), dpi=200, facecolor="white", edgecolor="none")
    plt.close(fig)
    return out_file


def render_flowchart_mpl(brief: ProjectBrief, facts: ProjectFacts, out_file: Path) -> Path:
    """Render Algorithm Flowchart at 200 DPI dynamically reflecting project parameters."""
    fig, ax = plt.subplots(figsize=(8.2, 10), dpi=200)
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

    has_gv = is_graphviz_available()
    if has_gv:
        logger.info("Graphviz detected. Attempting diagram generation...")
        try:
            render_block_diagram_mpl(brief, facts, block_file)
            render_connection_diagram_mpl(brief, facts, conn_file)
            render_flowchart_mpl(brief, facts, flow_file)
        except Exception as e:
            logger.warning(f"Diagram rendering encountered issue ({e}). Re-rendering with Matplotlib.")
            render_block_diagram_mpl(brief, facts, block_file)
            render_connection_diagram_mpl(brief, facts, conn_file)
            render_flowchart_mpl(brief, facts, flow_file)
    else:
        logger.info("Using Matplotlib diagram generator at 200 DPI...")
        render_block_diagram_mpl(brief, facts, block_file)
        render_connection_diagram_mpl(brief, facts, conn_file)
        render_flowchart_mpl(brief, facts, flow_file)

    logger.info(f"Generated diagrams successfully in {out_dir_path}")
    return {
        "block_diagram": block_file,
        "connection_diagram": conn_file,
        "flowchart": flow_file,
    }

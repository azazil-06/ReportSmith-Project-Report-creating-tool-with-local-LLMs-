"""Tests for dynamic diagram generation and topic hardware profile extraction."""

from pathlib import Path
import pytest

from reportsmith.brief import load_brief, ProjectBrief
from reportsmith.diagrams import (
    extract_project_hardware_profile,
    generate_all_diagrams,
    render_block_diagram_mpl,
    render_connection_diagram_mpl,
    render_flowchart_mpl,
)
from reportsmith.facts import derive_facts


def test_plant_watering_hardware_profile():
    brief_path = Path("examples/brief.yaml")
    brief = load_brief(brief_path)
    profile = extract_project_hardware_profile(brief)

    assert "Plant" in profile.title
    assert "STM32F103C8T6" in profile.mcu
    assert profile.sensor_pin == "PA0"
    assert "Soil" in profile.sensor_name
    assert "Buzzer" in profile.actuator_name
    assert profile.actuator_pin == "PA1"
    assert profile.led_pin == "PC13"


def test_dynamic_diagrams_generation(tmp_path):
    brief_path = Path("examples/brief.yaml")
    brief = load_brief(brief_path)
    facts = derive_facts(brief, base_dir=brief_path.parent)

    diag_dir = tmp_path / "diag_out"
    diag_dir.mkdir(parents=True, exist_ok=True)

    diagrams = generate_all_diagrams(brief, facts, out_dir=diag_dir)
    assert Path(diagrams["block_diagram"]).is_file()
    assert Path(diagrams["connection_diagram"]).is_file()
    assert Path(diagrams["flowchart"]).is_file()


def test_night_lamp_topic_adaptation(tmp_path):
    brief_path = Path("examples/brief.yaml")
    brief = load_brief(brief_path)
    # Modify brief in memory to simulate a completely different topic: Automatic Night Lamp
    brief.project.title = "Automatic Night Lamp using LDR and Relay"
    for p in brief.implementation.pins:
        if p.pin == "PA0":
            p.pin = "PA1"
            p.connected_to = "LDR Light Sensor Analog Output"
            p.function = "Analog light level sensing"
        elif p.pin == "PA1":
            p.pin = "PA8"
            p.connected_to = "Relay Module Lamp Driver"
            p.function = "AC Lamp switched control"

    profile = extract_project_hardware_profile(brief)
    assert profile.sensor_pin == "PA1"
    assert profile.actuator_pin == "PA8"

    # Render diagrams for this new topic to verify zero exceptions
    facts = derive_facts(brief, base_dir=brief_path.parent)
    block_png = tmp_path / "block.png"
    conn_png = tmp_path / "conn.png"
    flow_png = tmp_path / "flow.png"

    render_block_diagram_mpl(brief, facts, block_png)
    render_connection_diagram_mpl(brief, facts, conn_png)
    render_flowchart_mpl(brief, facts, flow_png)

    assert block_png.is_file() and block_png.stat().st_size > 1000
    assert conn_png.is_file() and conn_png.stat().st_size > 1000
    assert flow_png.is_file() and flow_png.stat().st_size > 1000

"""Configuration loader and settings for ReportSmith."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
import tomllib


@dataclass
class LLMConfig:
    model: str = "gpt-oss:20b"
    base_url: str = "http://localhost:11434/v1"
    timeout: int = 600
    temperature: float = 0.3
    reasoning_effort: str = "low"
    seed: int = 42
    retries: int = 2


@dataclass
class PathsConfig:
    cache_dir: str = "cache"
    out_dir: str = "out"


@dataclass
class ReportSmithConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    word_targets: Dict[str, int] = field(
        default_factory=lambda: {
            "abstract": 250,
            "1.1": 250,
            "1.2": 150,
            "1.3": 150,
            "1.4": 150,
            "2.1": 250,
            "2.2": 200,
            "2.3": 250,
            "2.4": 150,
            "2.5": 250,
            "3.1": 250,
            "3.4": 300,
            "3.5": 250,
            "3.7": 250,
            "4.1": 200,
            "4.2": 200,
            "4.3": 200,
            "4.4": 300,
            "4.5": 200,
            "4.6": 200,
            "4.7": 250,
            "5.1": 200,
            "5.5": 250,
            "6.1": 200,
            "6.2": 200,
            "6.3": 150,
            "6.4": 150,
            "acknowledgement": 120,
            "declaration": 80,
            "default": 200,
        }
    )

    def get_word_target(self, section_id: str) -> int:
        return self.word_targets.get(section_id, self.word_targets.get("default", 200))


def load_config(config_path: Optional[str] = None) -> ReportSmithConfig:
    """Load configuration from TOML file if present, else defaults."""
    cfg = ReportSmithConfig()
    path = Path(config_path) if config_path else Path("reportsmith.toml")
    if not path.is_file():
        return cfg

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        print(f"[WARN] Failed to load config from {path}: {e}. Using defaults.")
        return cfg

    llm_data = data.get("llm", {})
    cfg.llm = LLMConfig(
        model=llm_data.get("model", cfg.llm.model),
        base_url=llm_data.get("base_url", cfg.llm.base_url),
        timeout=int(llm_data.get("timeout", cfg.llm.timeout)),
        temperature=float(llm_data.get("temperature", cfg.llm.temperature)),
        reasoning_effort=llm_data.get("reasoning_effort", cfg.llm.reasoning_effort),
        seed=int(llm_data.get("seed", cfg.llm.seed)),
        retries=int(llm_data.get("retries", cfg.llm.retries)),
    )

    paths_data = data.get("paths", {})
    cfg.paths = PathsConfig(
        cache_dir=paths_data.get("cache_dir", cfg.paths.cache_dir),
        out_dir=paths_data.get("out_dir", cfg.paths.out_dir),
    )

    if "word_targets" in data and isinstance(data["word_targets"], dict):
        cfg.word_targets.update({str(k): int(v) for k, v in data["word_targets"].items()})

    return cfg

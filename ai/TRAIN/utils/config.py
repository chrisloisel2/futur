"""
YAML config loader with helpful error if PyYAML is missing.
"""
from pathlib import Path
from typing import Any, Dict


def load_config(path: str) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise ImportError("PyYAML is required to load config files. Install with `pip install pyyaml`.") from exc

    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")

    with cfg_path.open("r") as f:
        return yaml.safe_load(f) or {}

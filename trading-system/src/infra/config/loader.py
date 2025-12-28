from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel


class RuntimeConfig(BaseModel):
    config_path: str
    data: Dict[str, Any]
    run_id: Optional[str] = None
    mode: Optional[str] = None


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = base.copy()
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str) -> Dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    data = yaml.safe_load(cfg_path.read_text()) or {}
    if "include" in data:
        base_path = cfg_path.parent / data["include"]
        base_cfg = yaml.safe_load(base_path.read_text()) or {}
        data = _deep_merge(base_cfg, {k: v for k, v in data.items() if k != "include"})
    return data


def load_and_merge(path: str, overrides: Dict[str, Any]) -> Dict[str, Any]:
    config = load_config(path)
    for key, value in overrides.items():
        if value is not None:
            config[key] = value
    return config

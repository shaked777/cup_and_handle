"""Config loading: config.yaml, optionally deep-merged with an override file."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"


def deep_merge(base: dict, override: dict) -> dict:
    """Return a copy of base with override merged in; nested dicts merge, other values replace."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(path: str | Path | None = None) -> dict:
    """Load config.yaml and merge the file at `path` (if any) over it."""
    cfg = _read_yaml(DEFAULT_CONFIG)
    if path:
        p = Path(path)
        if not p.is_absolute() and not p.exists():
            p = PROJECT_ROOT / p
        if p.resolve() != DEFAULT_CONFIG:
            cfg = deep_merge(cfg, _read_yaml(p))
        cfg["_config_name"] = p.name
    else:
        cfg["_config_name"] = DEFAULT_CONFIG.name
    return cfg


def resolve_path(rel: str | Path) -> Path:
    """Resolve a config path relative to the project root."""
    p = Path(rel)
    return p if p.is_absolute() else PROJECT_ROOT / p

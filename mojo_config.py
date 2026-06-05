"""Merged Mojo configuration: packaged defaults + ~/.mojo/config.yaml.

User values override defaults key-by-key (deep merge), so a config.yaml
copied by an older `mojo init` keeps working when new keys are added to
the packaged defaults.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from db_ops import get_mojo_home

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "default.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if (key in out and isinstance(out[key], dict)
                and isinstance(value, dict)):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> dict:
    """Load defaults, then overlay the user's ~/.mojo/config.yaml."""
    try:
        cfg = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except OSError:
        cfg = {}
    user_path = get_mojo_home() / "config.yaml"
    if user_path.exists():
        try:
            user = yaml.safe_load(user_path.read_text(encoding="utf-8")) or {}
            if isinstance(user, dict):
                cfg = _deep_merge(cfg, user)
        except (OSError, yaml.YAMLError):
            pass  # a broken user config must not kill the pipeline
    return cfg

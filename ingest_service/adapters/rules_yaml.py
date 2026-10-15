"""Read the seed rules file. Accepts a top-level list or {"rules": [...]}."""

from __future__ import annotations

from pathlib import Path

import yaml


def read_rules_file(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    if isinstance(data, dict):
        data = data.get("rules", [])
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError(f"{path}: expected a list of rules")
    return data

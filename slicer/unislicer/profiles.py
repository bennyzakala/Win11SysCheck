"""Built-in and user printer / material / quality profiles."""
from __future__ import annotations

import json
import os
import re

from .settings import SCHEMA_BY_KEY, Config

BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "profiles")
USER_DIR = os.path.join(os.path.expanduser("~"), ".unislicer")
KINDS = ("printers", "materials", "qualities")


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def load(kind: str) -> dict:
    """All profiles of a kind; user profiles (marked ``user``) override built-in ones."""
    assert kind in KINDS
    out = _load(os.path.join(BUILTIN_DIR, f"{kind}.json"))
    for pid, prof in _load(os.path.join(USER_DIR, f"{kind}.json")).items():
        prof["user"] = True
        out[pid] = prof
    return out


def technology_of(profile: dict) -> str:
    return profile.get("technology") or profile.get("values", {}).get("technology", "fdm")


def for_technology(kind: str, technology: str) -> dict:
    return {k: v for k, v in load(kind).items() if technology_of(v) == technology}


def display_name(profile: dict, lang: str = "en") -> str:
    return profile.get(f"name_{lang}") or profile.get("name") or "?"


def save_user_profile(kind: str, name: str, values: dict, technology: str) -> str:
    """Store a user profile and return its id."""
    os.makedirs(USER_DIR, exist_ok=True)
    path = os.path.join(USER_DIR, f"{kind}.json")
    data = _load(path)
    pid = "user_" + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "profile")
    clean = {k: v for k, v in values.items() if k in SCHEMA_BY_KEY}
    clean["technology"] = technology
    data[pid] = {"name": name, "vendor": "User", "technology": technology, "values": clean}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    return pid


def delete_user_profile(kind: str, pid: str) -> None:
    path = os.path.join(USER_DIR, f"{kind}.json")
    data = _load(path)
    if pid in data:
        del data[pid]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)


def build_config(printer: str | None = None, material: str | None = None, quality: str | None = None,
                 overrides: dict | None = None) -> Config:
    cfg = Config()
    for kind, pid in (("printers", printer), ("materials", material), ("qualities", quality)):
        if not pid:
            continue
        profiles = load(kind)
        if pid not in profiles:
            raise KeyError(f"Unknown {kind[:-1]} profile '{pid}'. Available: {', '.join(sorted(profiles))}")
        cfg.update(profiles[pid].get("values", {}))
    if overrides:
        cfg.update(overrides)
    return cfg

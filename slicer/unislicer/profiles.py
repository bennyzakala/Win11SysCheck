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


def _save_json(path: str, data) -> None:
    """Write a profile file whole or not at all (written beside it, then put in place): an interrupted save
    left a broken file, which then read as empty - and the next save kept only its own entry."""
    part = path + ".part"
    with open(part, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(part, path)


_extra_cache: dict = {}


def _builtin_extras(kind: str) -> dict:
    """Profile libraries shipped beside the main file: ``<kind>-<source>.json`` (e.g. materials-phrozen_ds.json)."""
    out = {}
    try:
        names = sorted(n for n in os.listdir(BUILTIN_DIR) if n.startswith(kind + "-") and n.endswith(".json"))
    except OSError:
        return out
    for n in names:
        path = os.path.join(BUILTIN_DIR, n)
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            continue
        cached = _extra_cache.get(path)
        if cached is None or cached[0] != stamp:
            cached = (stamp, _load(path))
            _extra_cache[path] = cached
        out.update(cached[1])
    return out


def files_stamp() -> tuple:
    """When each profile file last changed: a configuration built from them stays valid while this is equal."""
    out = []
    for d in (BUILTIN_DIR, USER_DIR):
        try:
            names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
        except OSError:
            continue
        for n in names:
            try:
                out.append((d, n, os.path.getmtime(os.path.join(d, n))))
            except OSError:
                pass
    return tuple(out)


def load(kind: str) -> dict:
    """All profiles of a kind; user profiles (marked ``user``) override built-in ones."""
    assert kind in KINDS
    out = _load(os.path.join(BUILTIN_DIR, f"{kind}.json"))
    for pid, prof in _builtin_extras(kind).items():
        out.setdefault(pid, prof)
    for pid, prof in _load(os.path.join(USER_DIR, f"{kind}.json")).items():
        prof["user"] = True
        out[pid] = prof
    return out


def technology_of(profile: dict) -> str:
    return profile.get("technology") or profile.get("values", {}).get("technology", "fdm")


def process_of(printer: dict) -> str:
    """Photopolymer process of a printer profile: lcd, dlp, laser or polyjet."""
    return printer.get("values", {}).get("sla_process", "lcd")


def for_technology(kind: str, technology: str, process: str | None = None) -> dict:
    """Profiles for a technology; with ``process``, only those meant for it (profiles without a list fit all)."""
    return {k: v for k, v in load(kind).items() if technology_of(v) == technology
            and (process is None or technology != "sla" or process in v.get("processes", [process]))}


def display_name(profile: dict, lang: str = "en") -> str:
    return (profile.get(f"name_{lang}") or profile.get("name") or profile.get("name_en")
            or profile.get("name_he") or "?")


def _user_path(kind: str) -> str:
    return os.path.join(USER_DIR, f"{kind}.json")


def builtin_ids(kind: str) -> set:
    return set(_load(os.path.join(BUILTIN_DIR, f"{kind}.json"))) | set(_builtin_extras(kind))


def builtin(kind: str, pid: str | None) -> dict | None:
    """The shipped (original) profile, ignoring any user change saved under the same id."""
    if not pid:
        return None
    prof = _load(os.path.join(BUILTIN_DIR, f"{kind}.json")).get(pid)
    return prof if prof is not None else _builtin_extras(kind).get(pid)


def profile_keys(kind: str, pid: str | None) -> set:
    """Every setting a profile controls: its values plus, for resins, the keys of its per-printer layer table."""
    keys: set = set()
    for prof in (load(kind).get(pid or ""), builtin(kind, pid)):
        if not prof:
            continue
        keys |= set(prof.get("values", {}))
        for layers in prof.get("layer_table", {}).values():
            for modes in layers.values():
                for row in modes.values():
                    keys |= set(row)
    keys.discard("technology")
    return keys


def is_modified(kind: str, pid: str | None) -> bool:
    """A shipped profile the user changed and saved under its own id."""
    return bool(pid) and builtin(kind, pid) is not None and pid in _load(_user_path(kind))


def restore_default(kind: str, pid: str | None) -> bool:
    """Bring a shipped profile back to its original values (drops the user's saved changes). True if one was dropped."""
    if not is_modified(kind, pid):
        return False
    delete_user_profile(kind, pid)
    return True


def new_id(kind: str, name: str) -> str:
    base = "user_" + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "profile")
    existing = load(kind)
    pid, n = base, 2
    while pid in existing:
        pid, n = f"{base}_{n}", n + 1
    return pid


def save_user_profile(kind: str, name: str, values: dict, technology: str, pid: str | None = None,
                      vendor: str = "User") -> str:
    """Store (or overwrite) a user profile and return its id. Saving under a built-in id overrides it."""
    os.makedirs(USER_DIR, exist_ok=True)
    data = _load(_user_path(kind))
    pid = pid or new_id(kind, name)
    clean = {k: v for k, v in values.items() if k in SCHEMA_BY_KEY}
    clean["technology"] = technology
    entry = {"name": name, "vendor": vendor or "User", "technology": technology, "values": clean}
    existing = load(kind).get(pid, {})
    for k, v in existing.items():      # keep what the editor does not show (plates, per-printer layer table, links)
        if k not in entry and k not in ("user", "user_keys") and not k.startswith("name"):
            entry[k] = v
    if entry.get("layer_table"):
        # the per-printer table normally sets exposure and motion; values the user changed from the original win
        orig = (builtin(kind, pid) or {}).get("values", {})
        entry["user_keys"] = sorted(k for k, v in clean.items() if k != "technology" and orig.get(k) != v)
    data[pid] = entry
    _save_json(_user_path(kind), data)
    return pid


def plates(printer_id: str) -> list[dict]:
    """Build plates of a printer: manufacturer plates from the profile plus ones the user added."""
    prof = load("printers").get(printer_id, {})
    return list(prof.get("plates", []))


def add_plate(printer_id: str, name: str, width: float, depth: float, offset_x: float = 0.0, offset_y: float = 0.0) -> None:
    """Store an extra plate size for a printer (in the user's copy of the profile)."""
    os.makedirs(USER_DIR, exist_ok=True)
    data = _load(_user_path("printers"))
    entry = data.get(printer_id) or dict(load("printers").get(printer_id, {}))
    entry.pop("user", None)
    entry.setdefault("values", {})
    entry["plates"] = [p for p in entry.get("plates", []) if p.get("name") != name] + [
        {"name": name, "width": float(width), "depth": float(depth), "offset_x": float(offset_x), "offset_y": float(offset_y), "user": True}]
    data[printer_id] = entry
    _save_json(_user_path("printers"), data)


def delete_user_profile(kind: str, pid: str) -> None:
    """Remove a user profile (for a built-in id this restores the original)."""
    data = _load(_user_path(kind))
    if pid in data:
        del data[pid]
        _save_json(_user_path(kind), data)


def export_profiles(kind: str, pids: list[str], path: str) -> None:
    profiles = load(kind)
    out = {pid: {k: v for k, v in profiles[pid].items() if k != "user"} for pid in pids if pid in profiles}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"unislicer_profiles": kind, "profiles": out}, fh, indent=2, ensure_ascii=False)


def import_profiles(kind: str, path: str) -> list[str]:
    """Add profiles from a file written by :func:`export_profiles`. Returns the new ids."""
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("unislicer_profiles") not in (kind, None):
        raise ValueError(f"This file contains {doc.get('unislicer_profiles')} profiles")
    added = []
    for prof in doc.get("profiles", {}).values():
        values = prof.get("values", {})
        added.append(save_user_profile(kind, prof.get("name", "Imported"), values, technology_of(prof),
                                       vendor=prof.get("vendor", "Imported")))
    return added


def apply_layer_table(cfg: Config, printer: str | None, material: str | None, overrides: dict | None = None) -> Config:
    """Resins with a per-printer table (e.g. Phrozen DS Slicer profiles): take the row for this printer, the
    layer height nearest to the chosen one and the Stable / Fast mode. Values the user set stay as they are."""
    if not material or not printer:
        return cfg
    prof = load("materials").get(material)
    table = (prof or {}).get("layer_table", {}).get(printer)
    if not table:
        return cfg
    want = float(cfg["sla_layer_height"])
    key = min(table, key=lambda k: abs(float(k) - want))
    modes = table[key]
    row = modes.get(cfg["exposure_mode"]) or modes.get("stable") or next(iter(modes.values()))
    keep = set(overrides or {}) | set(prof.get("user_keys", []))
    cfg.update({k: v for k, v in row.items() if k not in keep and k != "sla_layer_height"})
    cfg.values["sla_layer_height"] = want if "sla_layer_height" in keep or abs(float(key) - want) > 1e-6 else float(key)
    return cfg


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
    return apply_layer_table(cfg, printer, material, overrides)

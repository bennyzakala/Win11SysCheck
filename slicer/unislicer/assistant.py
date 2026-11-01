"""The learning assistant: learns from how the user works and suggests improvements.

Everything is local: a small JSON file in the user's UniSlicer folder. Nothing is sent anywhere.

What it learns
- orientation: when the user changes an orientation chosen by auto-orient, the weights of the orientation
  score move towards the user's choice (pairwise logistic update on normalised features);
- supports: supports added or removed after auto-generation change the density and the overhang angle;
- print results: reported failures adjust supports / orientation and produce targeted suggestions;
- layout: the options picked in Auto layout and how the user rearranges parts afterwards (gap between
  parts, distance from the plate edge, centred or gathered in a corner) become the next layout's defaults;
- habits: settings the user keeps changing to the same value, and actions done the slow way.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field

import numpy as np

from . import profiles
from .orient import DEFAULT_WEIGHTS, FEATURES
from .settings import SCHEMA_BY_KEY, Config

FILE_NAME = "learning.json"
SHORTCUTS = {"arrange": "A", "duplicate": "Ctrl+D", "slice": "Ctrl+R", "export": "Ctrl+E", "undo": "Ctrl+Z",
             "open": "Ctrl+O", "save_project": "Ctrl+S"}
PRESET_ORDER = ["light", "medium", "heavy"]


@dataclass
class Suggestion:
    id: str                       # stable id (used for dismissing)
    kind: str                     # problem | improve | habit | tip | learned
    text: str                     # i18n key
    args: tuple = ()
    action: str | None = None     # handled by the UI
    action_args: dict = field(default_factory=dict)
    persistent_dismiss: bool = True


def _default_data() -> dict:
    return {
        "version": 1,
        "enabled": True,
        "orient": {t: {"weights": DEFAULT_WEIGHTS[t].tolist(), "examples": 0} for t in DEFAULT_WEIGHTS},
        "supports": {"spacing_scale": 1.0, "angle_offset": 0.0, "adds": 0, "removes": 0, "sessions": 0},
        "overrides": {},
        "actions": {},
        "prints": {"success": 0, "fail_supports": 0, "fail_suction": 0, "fail_adhesion": 0, "fail_other": 0},
        "dismissed": [],
        "layout": {"choices": {}, "gap": None, "edge": None, "offcentre": None, "samples": 0, "runs": 0},
        "rescue": {"by_sig": {}, "rescued": 0, "failed": 0},
        # model repairs learned from the user: problem fingerprint -> the repair they ran (and how often)
        "repair": {"by_sig": {}, "learned": 0, "auto": False, "auto_applied": 0},
    }


REPAIR_FLAGS = ("holes", "nonmanifold", "dirty", "shells")


def repair_fingerprint(analysis: dict, source: str = "") -> str:
    """What is wrong with a model (from mesh_tools.analyze) and where it came from (extension|exporting program):
    "holes+dirty|.stl|3Shape". A clean model gives "clean|..."."""
    flags = []
    if analysis.get("open_edges"):
        flags.append("holes")
    if analysis.get("nonmanifold_edges"):
        flags.append("nonmanifold")
    if analysis.get("degenerate") or analysis.get("duplicates"):
        flags.append("dirty")
    if analysis.get("shells", 1) > 1:
        flags.append("shells")
    return ("+".join(flags) or "clean") + "|" + (source or "-|-")


def normalise_recipe(recipe: dict) -> dict:
    """A repair as stored: {"mode": "basic"} or the advanced options."""
    if recipe.get("mode") != "advanced":
        return {"mode": "basic"}
    return {"mode": "advanced", "weld": round(float(recipe.get("weld", 1e-4)), 6),
            "shells": round(float(recipe.get("shells", 0.0)), 4),
            "nonmanifold": bool(recipe.get("nonmanifold", True)), "holes": bool(recipe.get("holes", True))}


class Assistant:
    def __init__(self, folder: str | None = None):
        self.folder = folder or profiles.USER_DIR
        self.path = os.path.join(self.folder, FILE_NAME)
        self.data = _default_data()
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            for k, v in saved.items():
                if isinstance(v, dict) and isinstance(self.data.get(k), dict):
                    self.data[k].update(v)
                else:
                    self.data[k] = v
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------- storage
    def save(self) -> None:
        try:
            os.makedirs(self.folder, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass  # learning is best effort; never disturb the user over it

    def reset(self) -> None:
        enabled = self.data.get("enabled", True)
        self.data = _default_data()
        self.data["enabled"] = enabled
        self.save()

    @property
    def enabled(self) -> bool:
        return bool(self.data.get("enabled", True))

    def set_enabled(self, on: bool) -> None:
        self.data["enabled"] = bool(on)
        self.save()

    # --------------------------------------------------------- orientation
    def orient_weights(self, technology: str) -> np.ndarray:
        if not self.enabled:
            return DEFAULT_WEIGHTS[technology].copy()
        return np.array(self.data["orient"][technology]["weights"], dtype=np.float64)

    def learn_orientation(self, technology: str, auto_features, user_features, rate: float = 0.6) -> bool:
        """The user replaced the auto orientation (``auto_features``) with their own (``user_features``)."""
        if not self.enabled:
            return False
        fa, fu = np.asarray(auto_features, dtype=float), np.asarray(user_features, dtype=float)
        scale = (np.abs(fa) + np.abs(fu)) / 2 + 1e-9
        diff = (fu - fa) / scale                      # scale-free: "how much worse/better on each feature"
        diff[np.abs(fa) + np.abs(fu) < 1e-9] = 0
        if not np.any(diff):
            return False
        entry = self.data["orient"][technology]
        w = np.array(entry["weights"], dtype=float)
        margin = float(w @ diff) / (np.abs(w).sum() or 1.0)
        grad = diff / (1.0 + math.exp(-4 * margin))   # push w.diff below zero (user's choice scores better)
        # multiplicative step, at most x2 per example, so one odd choice cannot swamp what was learned
        w = w * np.exp(np.clip(-rate * grad, -0.7, 0.7))
        active = DEFAULT_WEIGHTS[technology] > 0
        w = np.where(active, np.clip(w, 0.01, None), 0.0)
        w = w / w[0] * DEFAULT_WEIGHTS[technology][0]  # keep the support term as the fixed reference
        entry["weights"] = w.tolist()
        entry["examples"] = int(entry.get("examples", 0)) + 1
        self.save()
        return True

    # ------------------------------------------------------------- supports
    def learned_config(self, cfg: Config) -> Config:
        """``cfg`` with the learned support density and overhang angle applied."""
        if not self.enabled:
            return cfg
        s = self.data["supports"]
        out = cfg.copy()
        out.values["sla_support_spacing"] = float(np.clip(cfg["sla_support_spacing"] * s["spacing_scale"], 0.5, 20))
        out.values["support_angle"] = float(np.clip(cfg["support_angle"] + s["angle_offset"], 5, 85))
        return out

    def learn_support_edits(self, auto_count: int, added_angles: list[float], removed: int, threshold: float) -> bool:
        """After auto-generation the user added supports (with the overhang angle of each spot) and removed some."""
        if not self.enabled or auto_count <= 0 or (not added_angles and not removed):
            return False
        s = self.data["supports"]
        rate = (len(added_angles) - removed) / auto_count
        s["spacing_scale"] = float(np.clip(s["spacing_scale"] * math.exp(-0.5 * rate), 0.5, 1.6))
        for a in added_angles:
            if a < threshold:   # auto-support skipped this surface: lower the angle a bit
                s["angle_offset"] = float(np.clip(s["angle_offset"] + 0.3 * (a - threshold), -25, 10))
        s["adds"] += len(added_angles)
        s["removes"] += removed
        s["sessions"] += 1
        self.save()
        return True

    # --------------------------------------------------------------- layout
    def record_layout_choice(self, options: dict) -> None:
        """Options the user ran Auto layout with (strategy, rotation, anchor, scope, sort, spacing, margin)."""
        if not self.enabled:
            return
        lay = self.data["layout"]
        for k in ("strategy", "rotation", "anchor", "sort"):
            if k in options:
                counts = lay["choices"].setdefault(k, {})
                counts[str(options[k])] = counts.get(str(options[k]), 0) + 1
        for k in ("spacing", "margin"):
            if k in options:
                lay["choices"].setdefault(k + "_values", []).append(float(options[k]))
                del lay["choices"][k + "_values"][:-20]
        lay["runs"] = int(lay.get("runs", 0)) + 1
        self.save()

    def learn_layout(self, stats: dict | None) -> bool:
        """The final arrangement at slicing time, after the user adjusted an automatic layout."""
        if not self.enabled or not stats or stats.get("count", 0) < 2 or stats.get("gap") is None:
            return False
        if stats["gap"] < 0.3 or stats["edge"] < 0:
            return False      # overlapping or off-plate parts are a mistake, not a preference
        lay = self.data["layout"]
        n = int(lay.get("samples", 0))
        rate = 1.0 / (n + 1) if n < 4 else 0.25           # running mean first, then an exponential average
        for k, lo, hi in (("gap", 0.0, 60.0), ("edge", 0.0, 60.0), ("offcentre", 0.0, 1.0)):
            v = float(np.clip(stats[k], lo, hi))
            lay[k] = v if lay.get(k) is None else lay[k] + rate * (v - lay[k])
        lay["samples"] = n + 1
        self.save()
        return True

    def teach_layout(self, template: dict) -> bool:
        """"Learn layout": the user's arrangement on the plate, taught on purpose. It sets the gap, border and
        side of the plate for every later layout, and Arrange copies it when the plate holds as many parts."""
        if not self.enabled or not template:
            return False
        lay = self.data["layout"]
        lay["taught"] = template
        lay["taught_count"] = int(lay.get("taught_count", 0)) + 1
        self.save()
        return True

    def taught_layout(self) -> dict | None:
        return self.data["layout"].get("taught") if self.enabled else None

    def learned_layout(self, base: dict) -> dict:
        """Layout defaults: ``base`` updated with what the user usually picks and how they adjust layouts."""
        out = dict(base)
        if not self.enabled:
            return out
        lay = self.data["layout"]
        for k in ("strategy", "rotation", "anchor", "sort"):
            counts = lay["choices"].get(k, {})
            if counts and max(counts.values()) >= 2:
                out[k] = max(counts, key=counts.get)
        if lay.get("samples", 0) >= 2:
            # the gap the user asked for is theirs to set (Auto layout window); gaps seen in finished
            # layouts only feed a suggestion, they never change the default by themselves
            out["margin"] = round(min(float(lay["edge"]), 30.0) * 2) / 2
            vals = lay["choices"].get("spacing_values", [])
            if len(vals) >= 2:
                out["spacing"] = float(np.median(vals))
            anchors = lay["choices"].get("anchor", {})
            if not anchors or max(anchors.values()) < 2:      # no explicit habit: follow where parts end up
                out["anchor"] = "center" if lay["offcentre"] < 0.35 else "front_left"
        else:
            for k in ("spacing", "margin"):
                vals = lay["choices"].get(k + "_values", [])
                if len(vals) >= 2:
                    out[k] = float(np.median(vals))
        taught = lay.get("taught")
        if taught:                                    # taught on purpose: it wins over what was inferred
            out["spacing"] = float(taught["spacing"])
            out["margin"] = round(min(float(taught["margin"]), 30.0) * 2) / 2
            out["anchor"] = taught["anchor"]
        out["learned"] = bool(taught) or lay.get("samples", 0) >= 2 or lay.get("runs", 0) >= 2
        return out

    # --------------------------------------------------------------- prints
    def record_print(self, outcome: str, technology: str, cfg: Config) -> list[Suggestion]:
        """Store a print result; returns suggestions aimed at that failure."""
        p = self.data["prints"]
        p[outcome] = int(p.get(outcome, 0)) + 1
        out: list[Suggestion] = []
        if outcome == "fail_supports":
            self.data["supports"]["spacing_scale"] = float(np.clip(self.data["supports"]["spacing_scale"] * 0.88, 0.5, 1.6))
            preset = cfg.get("sla_support_preset")
            if technology == "sla" and preset in PRESET_ORDER[:-1]:
                heavier = PRESET_ORDER[PRESET_ORDER.index(preset) + 1]
                out.append(Suggestion(f"print_heavier_{time.time():.0f}", "improve", "sg_heavier_supports", (),
                                      "set", {"sla_support_preset": heavier}, False))
        elif outcome == "fail_suction":
            w = self.data["orient"]["sla"]["weights"]
            w[1], w[2] = w[1] * 1.2, w[2] * 1.2       # care more about peel area and faces parallel to the screen
            out.append(Suggestion(f"print_suction_{time.time():.0f}", "improve", "sg_after_suction", (),
                                  "set", {"hollow_enable": True, "drain_holes": 2}, False))
        elif outcome == "fail_adhesion":
            if technology == "sla":
                new = round(cfg["bottom_exposure_time"] * 1.15, 1)
                out.append(Suggestion(f"print_adhesion_{time.time():.0f}", "improve", "sg_bottom_exposure", (new,),
                                      "set", {"bottom_exposure_time": new,
                                              "bottom_layers": int(cfg["bottom_layers"]) + 1}, False))
            else:
                out.append(Suggestion(f"print_adhesion_{time.time():.0f}", "improve", "sg_brim", (),
                                      "set", {"adhesion_type": "brim"}, False))
        self.save()
        return out

    # ---------------------------------------------------------------- habits
    def record_override(self, key: str, value) -> None:
        if not self.enabled or key not in SCHEMA_BY_KEY or SCHEMA_BY_KEY[key].kind == "text":
            return
        k = f"{key}={json.dumps(value)}"
        entry = self.data["overrides"].setdefault(k, {"count": 0})
        entry["count"] += 1
        entry["last"] = time.time()
        self.save()

    def record_action(self, name: str, via: str) -> None:
        """``via`` is 'button' or 'shortcut'."""
        if not self.enabled:
            return
        entry = self.data["actions"].setdefault(name, {"button": 0, "shortcut": 0})
        entry[via] = entry.get(via, 0) + 1
        self.save()

    def dismiss(self, sid: str) -> None:
        if sid not in self.data["dismissed"]:
            self.data["dismissed"].append(sid)
            self.save()

    # ----------------------------------------------------------- suggestions
    def suggestions(self, ctx: dict) -> list[Suggestion]:
        """Suggestions for the current situation. ``ctx`` is a plain summary built by the UI."""
        tech = ctx.get("technology", "fdm")
        cfg: Config | None = ctx.get("cfg")
        out: list[Suggestion] = []
        job = ctx.get("job") or {}

        # problems found in the last slice
        if job.get("cups"):
            if cfg is not None and cfg["hollow_enable"] and int(cfg["drain_holes"]) == 0:
                out.append(Suggestion("cups_drain", "problem", "sg_cups_drain", (), "set", {"drain_holes": 2}, False))
            else:
                out.append(Suggestion("cups_orient", "problem", "sg_cups_orient", (), "auto_orient_all", {}, False))
        if job.get("islands"):
            out.append(Suggestion("islands", "problem", "sg_islands", (job["islands"],), "denser_supports", {}, False))
        if job.get("thin"):
            out.append(Suggestion("thin", "problem", "sg_thin", (job["thin"],), None, {}, False))
        for o in ctx.get("objects", []):
            if not o.get("watertight", True):
                out.append(Suggestion(f"watertight_{o['uid']}", "problem", "sg_not_watertight", (o["name"],), None, {}, False))

        # errors the crash agent diagnosed recently (already in the user's language)
        for c in ctx.get("crashes", []):
            out.append(Suggestion(f"crash_{c['signature']}", "problem", "sg_crash", (c["area"], c["title"], c["fix"]),
                                  "show_crash", {"signature": c["signature"]}))

        # improvements
        if tech == "sla" and cfg is not None:
            for o in ctx.get("objects", []):
                save = o.get("hollow_saving_ml", 0)
                if not cfg["hollow_enable"] and save >= 8:
                    out.append(Suggestion("hollow", "improve", "sg_hollow", (save,), "set",
                                          {"hollow_enable": True, "drain_holes": 2}, False))
                    break
            if cfg["support_enable"] and cfg["sla_support_type"] == "pillar":
                out.append(Suggestion("tree_supports", "improve", "sg_tree", (), "set", {"sla_support_type": "tree"}))
        # models that look like ones the user repaired before: offer the same repair
        for offer in ctx.get("repair_offers", []):
            out.append(Suggestion(f"repair_{offer['uid']}", "learned", "sg_repair_learned",
                                  (offer["name"], offer["recipe_text"]), "repair_like_before", {"uid": offer["uid"]}, False))
        # "based on what I learned" only once there is something learned; before that the built-in rules decide
        learned = self.data.get("orient", {}).get(tech, {}).get("examples", 0) > 0
        for o in ctx.get("objects", []):
            gain = o.get("orient_gain", 0)
            if gain >= 0.2:
                out.append(Suggestion(f"orient_{o['uid']}", "improve", "sg_orient" if learned else "sg_orient_rules",
                                      (o["name"], int(gain * 100)), "auto_orient", {"uid": o["uid"]}, False))
        lay = self.data["layout"]
        if cfg is not None and lay.get("samples", 0) >= 3 and lay.get("gap") is not None \
                and abs(lay["gap"] - cfg["arrange_spacing"]) >= 1.5:
            spacing = round(lay["gap"] * 2) / 2
            out.append(Suggestion(f"layout_gap_{spacing}", "learned", "sg_layout_spacing", (spacing,),
                                  "save_default", {"key": "arrange_spacing", "value": spacing}))
        if ctx.get("overlapping", 0):
            out.append(Suggestion("overlap", "problem", "sg_overlap", (ctx["overlapping"],), "auto_layout", {}, False))
        free = ctx.get("plate_free_copies", 0)
        if len(ctx.get("objects", [])) == 1 and free >= 3:
            out.append(Suggestion("copies", "tip", "sg_copies", (free,), "multiply", {}, False))

        # habits
        current = ctx.get("overrides", {})
        for k, entry in self.data["overrides"].items():
            if entry.get("count", 0) < 3:
                continue
            key, raw = k.split("=", 1)
            value = json.loads(raw)
            if key in SCHEMA_BY_KEY and current.get(key) == value:
                out.append(Suggestion(f"default_{k}", "habit", "sg_make_default", (key, value),
                                      "save_default", {"key": key, "value": value}))
        for name, entry in self.data["actions"].items():
            if name in SHORTCUTS and entry.get("button", 0) >= 5 and entry.get("shortcut", 0) == 0:
                out.append(Suggestion(f"shortcut_{name}", "tip", "sg_shortcut", (name, SHORTCUTS[name])))

        dismissed = set(self.data["dismissed"]) | set(ctx.get("dismissed_session", ()))
        order = {"problem": 0, "improve": 1, "habit": 2, "tip": 3, "learned": 4}
        return sorted((s for s in out if s.id not in dismissed), key=lambda s: order.get(s.kind, 9))

    # ----------------------------------------------------------- model rescue
    def rescue_order(self, sig: str) -> list[str]:
        """Recovery strategies that worked before for this kind of file, best first.

        The signature is "extension|real content|error|exporting program": an exact match counts most,
        then files of the same extension and content, then the same extension."""
        table = self.data["rescue"].get("by_sig", {})
        parts = sig.split("|")
        score: dict[str, float] = {}
        for other, wins in table.items():
            op = other.split("|")
            w = 4.0 if other == sig else 2.0 if op[:2] == parts[:2] else 1.0 if op[:1] == parts[:1] else 0.0
            if parts[3:] and op[3:] == parts[3:] and parts[3] != "-":
                w += 1.0                          # same exporting program
            for strat, n in wins.items():
                score[strat] = score.get(strat, 0.0) + w * n
        return [k for k, v in sorted(score.items(), key=lambda kv: -kv[1]) if v > 0]

    # ---------------------------------------------------------------- model repairs
    def learn_repair(self, fingerprint: str, recipe: dict) -> bool:
        """The user repaired a model with this fingerprint using ``recipe``: remember it."""
        if not self.enabled or fingerprint.startswith("clean|"):
            return False
        key = json.dumps(normalise_recipe(recipe), sort_keys=True)
        r = self.data.setdefault("repair", {"by_sig": {}, "learned": 0, "auto": False, "auto_applied": 0})
        entry = r.setdefault("by_sig", {}).setdefault(fingerprint, {})
        entry[key] = entry.get(key, 0) + 1
        r["learned"] = r.get("learned", 0) + 1
        self.save()
        return True

    def repair_recipe(self, fingerprint: str) -> dict | None:
        """The repair the user ran on models like this one: the same problems from the same source count most,
        then the same problems from anywhere, then models whose problems include these."""
        if fingerprint.startswith("clean|"):
            return None
        table = self.data.get("repair", {}).get("by_sig", {})
        problems = set(fingerprint.split("|")[0].split("+"))
        score: dict[str, float] = {}
        for other, recipes in table.items():
            oprob = set(other.split("|")[0].split("+"))
            if other == fingerprint:
                w = 4.0
            elif oprob == problems:
                w = 2.0
            elif problems <= oprob:
                w = 1.0
            else:
                continue
            for key, n in recipes.items():
                score[key] = score.get(key, 0.0) + w * n
        if not score:
            return None
        return json.loads(max(score.items(), key=lambda kv: kv[1])[0])

    @property
    def auto_repair(self) -> bool:
        return bool(self.data.get("repair", {}).get("auto", False))

    def set_auto_repair(self, on: bool) -> None:
        self.data.setdefault("repair", {}).update({"auto": bool(on)})
        self.save()

    def note_auto_repair(self) -> None:
        r = self.data.setdefault("repair", {})
        r["auto_applied"] = r.get("auto_applied", 0) + 1
        self.save()

    def learn_rescue(self, sig: str, strategy: str | None) -> None:
        r = self.data["rescue"]
        if strategy:
            entry = r.setdefault("by_sig", {}).setdefault(sig, {})
            entry[strategy] = entry.get(strategy, 0) + 1
            r["rescued"] = r.get("rescued", 0) + 1
        else:
            r["failed"] = r.get("failed", 0) + 1
        self.save()

    # --------------------------------------------------------------- stats
    def summary(self) -> dict:
        o = self.data["orient"]
        s = self.data["supports"]
        return {
            "orient_examples": {t: int(o[t].get("examples", 0)) for t in o},
            "orient_weights": {t: dict(zip(FEATURES[t], np.round(o[t]["weights"], 3))) for t in o},
            "support_density": round(1.0 / s["spacing_scale"] - 1.0, 3),
            "angle_offset": round(s["angle_offset"], 1),
            "support_sessions": s["sessions"],
            "prints": dict(self.data["prints"]),
            "layout_samples": int(self.data["layout"].get("samples", 0)),
            "layout_runs": int(self.data["layout"].get("runs", 0)),
            "layout_taught": (self.data["layout"].get("taught") or {}).get("count", 0),
            "models_rescued": int(self.data.get("rescue", {}).get("rescued", 0)),
            "repairs_learned": int(self.data.get("repair", {}).get("learned", 0)),
            "repairs_auto": int(self.data.get("repair", {}).get("auto_applied", 0)),
            "auto_repair": self.auto_repair,
        }


def hollow_saving_ml(volume_mm3: float, area_mm2: float, wall: float) -> float:
    """Rough resin saved by hollowing a solid model (shell ~ surface area x wall thickness)."""
    return max(0.0, (volume_mm3 - area_mm2 * wall) / 1000.0)

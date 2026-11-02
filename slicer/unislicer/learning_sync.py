"""Shared learning between the user's computers (through their Firebase project, under their Google
account): each computer uploads what its assistant learned and merges in what the others learned.

The merge can run again and again without counting anything twice: counts take the largest value seen,
learned numbers are averages weighted by how much each computer has seen, choices taught on purpose (a
layout) keep the newest one. So every computer drifts towards the same, shared knowledge."""
from __future__ import annotations

import copy
import json

LOCAL_ONLY = ("enabled", "dismissed", "seed")      # this computer's own switches / dismissed tips
MAX_BYTES = 1_500_000


def snapshot(data: dict) -> str:
    """What this computer shares: everything learned, as JSON text (the database cannot hold some keys)."""
    out = {k: v for k, v in data.items() if k not in LOCAL_ONLY}
    text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_BYTES:            # never happens with normal use; keep it safe anyway
        out.get("layout", {}).get("choices", {}).pop("spacing_values", None)
        text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    return text


def _wavg(pairs):
    """Weighted average of (value, weight); weight 0 still counts a little so a fresh computer is not lost."""
    tot = sum(max(w, 0.0) + 0.25 for _v, w in pairs)
    return sum(v * (max(w, 0.0) + 0.25) for v, w in pairs) / tot if tot else 0.0


def _max_counts(dicts):
    out = {}
    for d in dicts:
        for k, v in (d or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = max(out.get(k, v), v)
            elif isinstance(v, dict):
                out[k] = _max_counts([out.get(k, {}), v])
            elif k not in out:
                out[k] = copy.deepcopy(v)
    return out


def merge(local: dict, others: list[dict]) -> dict:
    """``local`` (this computer's data) with the other computers' snapshots merged in."""
    out = copy.deepcopy(local)
    every = [local] + [o for o in others if isinstance(o, dict)]
    # orientation habits: weights averaged by examples
    for tech in set().union(*[set((d.get("orient") or {}).keys()) for d in every]):
        rows = [d["orient"][tech] for d in every if tech in (d.get("orient") or {})
                and isinstance(d["orient"][tech].get("weights"), list)]
        if not rows:
            continue
        n = max(len(r["weights"]) for r in rows)
        rows = [r for r in rows if len(r["weights"]) == n]
        out.setdefault("orient", {})[tech] = {
            "weights": [_wavg([(float(r["weights"][i]), float(r.get("examples", 0))) for r in rows]) for i in range(n)],
            "examples": max(int(r.get("examples", 0)) for r in rows)}
    # support density / angle: averaged by sessions
    sup = [d.get("supports") or {} for d in every if d.get("supports")]
    if sup:
        s = out.setdefault("supports", {})
        for k in ("spacing_scale", "angle_offset"):
            vals = [(float(x[k]), float(x.get("sessions", 0))) for x in sup if k in x]
            if vals:
                s[k] = _wavg(vals)
        for k in ("adds", "removes", "sessions"):
            s[k] = max(int(x.get(k, 0)) for x in sup)
    # usual settings, actions, prints, rescues, repairs: counts take the largest value seen
    for key in ("overrides", "actions", "prints", "rescue"):
        out[key] = _max_counts([d.get(key) or {} for d in every])
    rep = [d.get("repair") or {} for d in every]
    out.setdefault("repair", {})
    out["repair"]["by_sig"] = _max_counts([r.get("by_sig") or {} for r in rep])
    for k in ("learned", "auto_applied"):
        out["repair"][k] = max(int(r.get(k, 0)) for r in rep)
    out["repair"]["auto"] = bool((local.get("repair") or {}).get("auto", False))      # this computer's switch
    # layout habits
    lays = [d.get("layout") or {} for d in every]
    lay = out.setdefault("layout", {})
    choices = [L.get("choices") or {} for L in lays]
    merged_choices = _max_counts([{k: v for k, v in c.items() if isinstance(v, dict)} for c in choices])
    for k in ("spacing_values", "margin_values"):
        lists = [c.get(k) for c in choices if isinstance(c.get(k), list)]
        if lists:
            merged_choices[k] = max(lists, key=len)[-40:]
    lay["choices"] = merged_choices
    for k in ("gap", "edge", "offcentre"):
        vals = [(float(L[k]), float(L.get("samples", 0))) for L in lays if L.get(k) is not None]
        if vals:
            lay[k] = _wavg(vals)
    for k in ("samples", "runs", "taught_count"):
        lay[k] = max(int(L.get(k, 0) or 0) for L in lays)
    taught = [L["taught"] for L in lays if isinstance(L.get("taught"), dict)]
    if taught:
        lay["taught"] = max(taught, key=lambda t: float(t.get("taught_at", 0)))
    return out


def fresh(others: list[dict], seen: dict) -> tuple[list[dict], dict]:
    """Only the snapshots that changed since this computer last merged them (each computer's upload time).
    A computer that stays off keeps an old snapshot: merging it again every 15 minutes would keep pulling
    this computer back towards it, so each version is merged once."""
    out, now_seen = [], dict(seen)
    for o in others:
        meta = o.get("_from") or {}
        key, at = str(meta.get("uid") or meta.get("name") or ""), float(meta.get("at") or 0)
        if key and at and float(seen.get(key, 0)) >= at:
            continue
        out.append(o)
        if key:
            now_seen[key] = at
    return out, now_seen

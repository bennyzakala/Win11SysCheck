"""Per-layer resin timing values shared by the slicer and the file writers."""
from __future__ import annotations

from .settings import Config


def layer_rests(i: int, cfg: Config) -> tuple[float, float, float]:
    """(before lift, after lift, after retract) for layer ``i`` in resting-time mode."""
    if i < int(cfg["bottom_layers"]):
        return cfg["bottom_rest_before_lift"], cfg["bottom_rest_after_lift"], cfg["bottom_rest_after_retract"]
    return cfg["rest_before_lift"], cfg["rest_after_lift"], cfg["rest_after_retract"]


def layer_light_off(i: int, cfg: Config) -> float:
    return cfg["bottom_light_off_delay"] if i < int(cfg["bottom_layers"]) else cfg["light_off_delay"]


def layer_pwm(i: int, cfg: Config) -> int:
    return int(cfg["light_pwm_bottom"] if i < int(cfg["bottom_layers"]) else cfg["light_pwm"])

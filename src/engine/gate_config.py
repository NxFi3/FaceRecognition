"""Shared persistence helpers for per-camera drawn virtual gates."""

from __future__ import annotations


def _valid_line(points, side):
    try:
        values = tuple(float(value) for value in points)
        valid_side = int(side)
    except (TypeError, ValueError):
        return None
    if len(values) != 4 or not all(0.0 <= value <= 1.0 for value in values):
        return None
    if (values[0] - values[2]) ** 2 + (values[1] - values[3]) ** 2 <= 1e-6:
        return None
    if valid_side not in (-1, 1):
        return None
    return values, valid_side


def load_drawn_gate_line(camera_slot, get_setting):
    """Return saved normalized points and inside side, or (None, None)."""
    slot = int(camera_slot)
    mode = str(get_setting(f"camera_{slot}_line_mode", "legacy") or "legacy")
    if mode != "custom":
        return None, None
    try:
        points = tuple(
            float(get_setting(f"camera_{slot}_line_{axis}", ""))
            for axis in ("x1", "y1", "x2", "y2")
        )
        side = int(get_setting(f"camera_{slot}_inside_side", "0"))
    except (TypeError, ValueError):
        return None, None
    valid = _valid_line(points, side)
    return valid if valid is not None else (None, None)


def save_drawn_gate_line(camera_slot, points, side, set_setting):
    """Persist one drawn gate, or select legacy mode when no valid line exists."""
    slot = int(camera_slot)
    valid = _valid_line(points, side) if points is not None else None
    if valid is None:
        set_setting(f"camera_{slot}_line_mode", "legacy")
        return None, None

    points, side = valid
    set_setting(f"camera_{slot}_line_mode", "custom")
    for axis, coordinate in zip(("x1", "y1", "x2", "y2"), points):
        set_setting(f"camera_{slot}_line_{axis}", f"{coordinate:.6f}")
    set_setting(f"camera_{slot}_inside_side", side)
    return points, side

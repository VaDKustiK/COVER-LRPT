from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .pass_loader import load_pass

if TYPE_CHECKING:
    from pathlib import Path


def find_products(root: Path) -> list[Path]:
    """Every product.cbor under root in order"""
    return sorted(root.rglob("product.cbor"))


def day_candidates(rows: list[dict[str, Any]], norad: int, min_longest_s: float = 300.0) -> list[dict[str, Any]]:
    """Day passes of one satellite with trusted timing in time order"""
    picks = [
        r
        for r in rows
        if r.get("norad") == norad
        and r.get("light") == "day"
        and r["status"] == "ok"
        and (r["longest_s"] or 0) >= min_longest_s
    ]
    return sorted(picks, key=lambda r: r["aos_utc"])


def analyse_pass(folder: Path) -> dict[str, Any]:
    """One catalog row for the pass in folder"""
    p = load_pass(folder, channels=(2, 4))
    row: dict[str, Any] = {
        "name": p.name,
        "norad": p.norad,
        "aos_utc": datetime.fromtimestamp(p.aos, UTC).isoformat(timespec="seconds"),
        "status": p.status,
        "channels": p.channels_available,
        "n_lines": p.n_lines,
        "usable_frac": None,
        "longest_s": None,
        "light": None,
        "skip": None,
    }
    if not p.channels_available:
        row["skip"] = "no_images"
    elif p.status == "no_timing":
        row["skip"] = "no_timing"
    elif not {2, 4} <= set(p.channels_available):
        row["skip"] = "missing_channel"
    else:
        segs = p.segments()
        longest = max((e - s + 1 for s, e in segs), default=0)
        row.update(
            usable_frac=round(float(p.usable_lines().mean()), 4),
            longest_s=round(longest * p.line_period, 1),
            light=p.light(),
        )
    return row


def build_catalog(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in find_products(root):
        folder = path.parents[2]  # product.cbor location
        try:
            rows.append(analyse_pass(folder))
        except Exception as exc:
            rows.append({"name": folder.name, "skip": f"error: {exc}"})
    return rows


def save_catalog(rows: list[dict[str, Any]], out: Path) -> None:
    out.write_text(json.dumps(rows, indent=1))

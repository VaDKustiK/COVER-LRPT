"""Print the internally consistent time segments of passes.

usage: python -m testing.inspect_timing NAME [NAME ...]
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from src.catalog import find_products, fit_time_model, read_product

ROOT = Path("~/Documents/ROSARP-OBS").expanduser()
BREAK_S = 0.05  # consecutive frames further than this from one period apart = new segment


def iso(t: float) -> str:
    try:
        return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d %H:%M:%S")
    except OverflowError, OSError, ValueError:
        return f"out of range ({t:.0f} s)"


def folder_time(name: str) -> float:
    stamp = name.split("_")[1]
    dt = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
    return dt.replace(tzinfo=UTC).timestamp()


def inspect(name: str, products: dict[str, Path]) -> None:
    print(f"\n=== {name}")
    path = products[name]
    ts = np.asarray(read_product(path)["projection_cfg"]["timestamps"], float)
    ok = np.where(ts > 0)[0]
    print(f"{ts.size} frames, {ok.size} valid")
    try:
        _, period, _, _ = fit_time_model(ts)
    except ValueError as exc:
        print("no time model:", exc)
        return

    step = np.diff(ts[ok]) - period * np.diff(ok)
    breaks = np.where(np.abs(step) > BREAK_S)[0]
    starts, ends = np.r_[0, breaks + 1], np.r_[breaks, ok.size - 1]

    ref = folder_time(name)
    for s, e in zip(starts, ends, strict=True):
        f0, f1 = ok[s], ok[e]
        print(
            f"frames {f0:4d}-{f1:4d} ({e - s + 1:3d} valid)  "
            f"{iso(ts[f0])} -> {iso(ts[f1])}   "
            f"start is {ts[f0] - ref:+.0f} s from folder time"
        )

    meta = path.parents[2] / "metadata.json"
    if meta.exists():
        m = json.loads(meta.read_text())
        print("AOS:", m.get("aos"), "| LOS:", m.get("los"))
    else:
        print("no metadata.json; folder time:", iso(ref))


if __name__ == "__main__":
    products = {p.parents[2].name: p for p in find_products(ROOT)}
    for n in sys.argv[1:]:
        inspect(n, products)

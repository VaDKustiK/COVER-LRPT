from collections import Counter
from pathlib import Path

import numpy as np

from src.catalog import find_products, read_product
from src.timing import aos_from_folder, build_time_model, timing_status

root = Path("~/Documents/ROSARP-OBS").expanduser()
status, names, leads, errors = Counter(), {}, [], []

for path in find_products(root):
    name = path.parents[2].name
    try:
        aos = aos_from_folder(name)
        model = build_time_model(read_product(path)["projection_cfg"]["timestamps"], aos)
    except Exception as exc:
        errors.append((name, str(exc)))
        continue
    st = timing_status(model)
    status[st] += 1
    names.setdefault(st, []).append(name)
    if st == "ok":
        s = model.segments[0]
        leads.append(s.t0 + model.period * s.frames[0] - aos)

print("status:", dict(status), "| read errors:", len(errors))
for n, e in errors[:5]:
    print("  ", n, e)
if leads:
    print(
        f"ok passes: first timed frame is {min(leads):.0f} / {np.median(leads):.0f} / "
        f"{max(leads):.0f} s after AOS (min / median / max)"
    )
for st in ("multi_segment", "partial"):
    print(f"\n{st}:", len(names.get(st, [])))
    for n in names.get(st, [])[:5]:
        print("  ", n)

"""Cross-check identity evidence; never repair labels or choose an orbit silently."""

import re
from dataclasses import dataclass
from typing import Any

SUPPORTED_NORADS = {57166, 59051}
SOURCE_IDS = {"METEORM23": 57166, "METEORM24": 59051, "METEORM1": 35865, "METEORM2": 40069}


@dataclass(frozen=True)
class IdentityCheck:
    status: str  # consistent / incomplete / conflict; not independent physical identification
    evidence: dict[str, int]
    issues: tuple[str, ...]
    telemetry_counts: dict[int, int]


class IdentityConflict(ValueError):
    """Available identity labels contradict one another."""


def check_identity(
    norad: int,
    product: dict[str, Any],
    metadata: dict[str, Any],
    dataset: dict[str, Any],
    telemetry: list[Any],
) -> IdentityCheck:
    evidence = {"folder": norad}
    issues = []
    conflict = norad not in SUPPORTED_NORADS
    if conflict:
        issues.append(f"unsupported folder NORAD: {norad}")

    meta_id = metadata.get("norad_id")
    if isinstance(meta_id, int) and not isinstance(meta_id, bool):
        evidence["metadata"] = meta_id
    elif isinstance(meta_id, str) and meta_id.isdigit():
        evidence["metadata"] = int(meta_id)
    else:
        issues.append("metadata norad_id missing or invalid")

    tle = product.get("projection_cfg", {}).get("tle") or {}
    for key in ("line1", "line2"):
        line = tle.get(key, "")
        if isinstance(line, str) and line.startswith(key[-1] + " ") and line[2:7].strip().isdigit():
            evidence[f"tle_{key}"] = int(line[2:7])
        else:
            issues.append(f"TLE {key} identity missing or invalid")

    for label, name in (("product_source", product.get("product_source")), ("dataset", dataset.get("satellite"))):
        if name is None and label == "dataset":
            continue  # optional corroborating evidence
        normalized = re.sub(r"[^A-Z0-9]", "", str(name).upper())
        if normalized in SOURCE_IDS:
            evidence[label] = SOURCE_IDS[normalized]
        else:
            issues.append(f"{label} name missing or unrecognized: {name!r}")

    counts: dict[int, int] = {}
    for record in telemetry:
        if isinstance(record, dict):
            serial = record.get("msu_mr_id")
            if isinstance(serial, int) and not isinstance(serial, bool):
                counts[serial] = counts.get(serial, 0) + 1
    if counts:
        serial = max(counts, key=lambda k: counts[k])
        # Decoded instrument serials are corroboration, not independent tracking truth.
        if counts[serial] <= sum(counts.values()) / 2:
            issues.append("no strict majority among decoded instrument IDs")
        elif serial in (3, 4):
            evidence["telemetry_majority"] = {3: 57166, 4: 59051}[serial]
        else:
            issues.append(f"unrecognized decoded instrument ID majority: {serial}")

    for source, value in evidence.items():
        if value != norad:
            conflict = True
            issues.append(f"{source} NORAD {value} disagrees with folder NORAD {norad}")
    status = "conflict" if conflict else ("incomplete" if issues else "consistent")
    return IdentityCheck(status, evidence, tuple(issues), counts)

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from sgp4.exporter import export_omm
from skyfield.api import EarthSatellite

from .dataset_audit import fingerprint
from .geometry import timescale

if TYPE_CHECKING:
    from .pass_loader import PassData


def tle_fields(lines: tuple[str, str], norad: int) -> dict[str, Any]:
    """Read fixed columns, including the decoder's six-digit unsigned exponent mantissa.

    This establishes an explicit numerical interpretation, not recovery of source OMM.
    A six-digit mantissa is scaled by 1e-5, matching both inspected decoder and SGP4.
    """
    one, two = lines
    if len(one) != 69 or len(two) != 69 or not one.startswith("1 ") or not two.startswith("2 "):
        raise ValueError("expected two 69-character orbital lines")
    if int(one[2:7]) != norad or int(two[2:7]) != norad:
        raise ValueError("orbit identity disagrees with pass")
    year = int(one[18:20])
    year += 1900 if year >= 57 else 2000
    day = float(one[20:32])
    if not 1 <= day < (datetime(year + 1, 1, 1) - datetime(year, 1, 1)).days + 1:
        raise ValueError("invalid orbit epoch day")
    epoch = datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=day - 1)

    def exponent(field: str) -> float:
        return float(float(field[:6]) * 1e-5 * 10.0 ** int(field[6:]))

    fields = {
        "OBJECT_NAME": f"NORAD {norad}",
        "OBJECT_ID": "",
        "CENTER_NAME": "EARTH",
        "REF_FRAME": "TEME",
        "TIME_SYSTEM": "UTC",
        "MEAN_ELEMENT_THEORY": "SGP4",
        "EPOCH": epoch.replace(tzinfo=None).isoformat(),
        "NORAD_CAT_ID": norad,
        "CLASSIFICATION_TYPE": one[7],
        "EPHEMERIS_TYPE": int(one[62]),
        "ELEMENT_SET_NO": int(one[64:68]),
        "REV_AT_EPOCH": int(two[63:68]),
        "MEAN_MOTION_DOT": float(one[33:43]),
        "MEAN_MOTION_DDOT": exponent(one[44:52]),
        "BSTAR": exponent(one[53:61]),
        "INCLINATION": float(two[8:16]),
        "RA_OF_ASC_NODE": float(two[17:25]),
        "ECCENTRICITY": float("0." + two[26:33].strip()),
        "ARG_OF_PERICENTER": float(two[34:42]),
        "MEAN_ANOMALY": float(two[43:51]),
        "MEAN_MOTION": float(two[52:63]),
    }
    return fields


def validate_fields(fields: dict[str, Any], norad: int) -> None:
    for key, expected in (
        ("CENTER_NAME", "EARTH"),
        ("REF_FRAME", "TEME"),
        ("TIME_SYSTEM", "UTC"),
        ("MEAN_ELEMENT_THEORY", "SGP4"),
    ):
        if str(fields.get(key, expected)).upper() != expected:
            raise ValueError(f"unsupported OMM {key}; expected {expected}")
    if int(fields["NORAD_CAT_ID"]) != norad:
        raise ValueError("orbit identity disagrees with pass")
    numeric = [
        "MEAN_MOTION",
        "ECCENTRICITY",
        "INCLINATION",
        "RA_OF_ASC_NODE",
        "ARG_OF_PERICENTER",
        "MEAN_ANOMALY",
        "BSTAR",
        "MEAN_MOTION_DOT",
        "MEAN_MOTION_DDOT",
    ]
    if not np.isfinite([float(fields[k]) for k in numeric]).all():
        raise ValueError("orbit fields must be finite")
    if not 0 < float(fields["MEAN_MOTION"]) < 20 or not 0 <= float(fields["ECCENTRICITY"]) < 1:
        raise ValueError("invalid mean motion or eccentricity")
    if not 0 <= float(fields["INCLINATION"]) <= 180:
        raise ValueError("invalid inclination")


def select_orbit(
    p: PassData, source: str, *, allow_nonstandard: bool = False
) -> tuple[EarthSatellite, dict[str, Any]]:
    ts = timescale()
    record: dict[str, Any]
    if source == "embedded":
        if not p.tle:
            raise ValueError("product has no embedded orbit")
        standard = True
        for line in p.tle:
            total = sum(int(c) if c.isdigit() else int(c == "-") for c in line[:68])
            standard &= len(line) == 69 and line[-1].isdigit() and total % 10 == int(line[-1])
        if not standard and not allow_nonstandard:
            raise ValueError(
                "embedded TLE checksum is nonstandard/invalid; explicitly allow it for diagnostic geometry"
            )
        fields = tle_fields(p.tle, p.norad)
        validate_fields(fields, p.norad)
        sat = EarthSatellite(*p.tle, ts=ts)
        parsed = export_omm(sat.model, fields["OBJECT_NAME"])
        for key in (
            "MEAN_MOTION",
            "ECCENTRICITY",
            "INCLINATION",
            "RA_OF_ASC_NODE",
            "ARG_OF_PERICENTER",
            "MEAN_ANOMALY",
            "BSTAR",
            "MEAN_MOTION_DOT",
            "MEAN_MOTION_DDOT",
        ):
            if not np.isclose(parsed[key], fields[key], rtol=1e-10, atol=1e-14):
                raise ValueError(f"SGP4 parser differs from explicit field interpretation: {key}")
        record = {
            "source": "embedded_decoder_tle",
            "original_lines": list(p.tle),
            "checksum_valid": bool(standard),
            "numeric_field_check": "passed",
            "availability_time_utc": None,
            "original_recording_omm_recovered": False,
        }
    else:
        path = Path(source).expanduser()
        data = json.loads(path.read_text())
        records = [data] if isinstance(data, dict) else data
        matches = [r for r in records if int(r["NORAD_CAT_ID"]) == p.norad]
        if len(matches) != 1:
            raise ValueError("OMM file must have exactly one record for this satellite")
        fields = matches[0]
        validate_fields(fields, p.norad)
        sat = EarthSatellite.from_omm(ts, fields)
        record = {
            "source": "explicit_local_omm",
            "file": path.name,
            "fingerprint": fingerprint(path),
            "availability_time_utc": None,
        }
    record.update(
        {
            "interpreted_omm_fields": fields,
            "sgp4_gravity_model": "WGS72",
            "epoch_utc": sat.epoch.utc_iso(places=6),
            "earth_surface": "WGS84 ellipsoid",
        }
    )
    return sat, record

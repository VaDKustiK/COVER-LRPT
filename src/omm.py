import json
from pathlib import Path

from skyfield.api import EarthSatellite, load

OMM_FILE_PATH = Path(__file__).resolve().parent.parent / "latest_omms.json"
SATELLITE_NAMES = {"METEOR-M2 3", "METEOR-M2 4"}


def load_omm_fields(omm_path: Path = OMM_FILE_PATH) -> dict[str, dict[str, object]]:
    """Load OMM fields from the local JSON file."""
    try:
        with omm_path.open(encoding="utf-8") as omm_file:
            records = json.load(omm_file)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"OMM file not found at {omm_path}. Run get_latest_omms.py first.") from error

    by_satellite: dict[str, dict[str, object]] = {}
    for record in records:
        satellite_name = str(record["OBJECT_NAME"])
        if satellite_name in SATELLITE_NAMES:
            by_satellite[satellite_name] = record

    missing = SATELLITE_NAMES - by_satellite.keys()
    if missing:
        missing_names = ", ".join(sorted(missing))
        raise ValueError(f"OMM file is missing: {missing_names}")

    return by_satellite


def load_satellites(omm_path: Path = OMM_FILE_PATH) -> dict[str, EarthSatellite]:
    """Build EarthSatellite objects from latest_omms.json"""
    timescale = load.timescale(builtin=True)
    return {key: EarthSatellite.from_omm(timescale, fields) for key, fields in load_omm_fields(omm_path).items()}

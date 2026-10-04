import json
from pathlib import Path

from skyfield.api import EarthSatellite, load

CACHE_PATH = Path(__file__).resolve().parent.parent / "latest_omms.json"
SATELLITE_NAMES = {"METEOR-M2 3", "METEOR-M2 4"}


def load_omm_fields(cache_path: Path = CACHE_PATH) -> dict[str, dict[str, object]]:
    """Load cached OMM fields"""
    try:
        with cache_path.open(encoding="utf-8") as cache_file:
            records = json.load(cache_file)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"OMM cache not found at {cache_path}. Run get_latest_omms.py first.") from error

    by_satellite = {
        str(record["OBJECT_NAME"]): record for record in records if record["OBJECT_NAME"] in SATELLITE_NAMES
    }

    missing = SATELLITE_NAMES - by_satellite.keys()
    if missing:
        missing_names = ", ".join(sorted(missing))
        raise ValueError(f"OMM cache is missing: {missing_names}")

    return by_satellite


def load_satellites(cache_path: Path = CACHE_PATH) -> dict[str, EarthSatellite]:
    """Builds EarthSatellite objects from local cache"""
    timescale = load.timescale(builtin=True)
    return {key: EarthSatellite.from_omm(timescale, fields) for key, fields in load_omm_fields(cache_path).items()}

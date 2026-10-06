import csv
import json
from io import StringIO

import requests

from src.omm import OMM_FILE_PATH, SATELLITE_NAMES

OMM_URL = "https://retlector.eu/active/csv"


def download_latest_omms() -> list[dict[str, str]]:
    """Downloads OMMs for METEOR-M2 3 and METEOR-M2 4"""
    response = requests.get(OMM_URL, timeout=30)
    response.raise_for_status()

    records = [
        record for record in csv.DictReader(StringIO(response.text)) if record["OBJECT_NAME"] in SATELLITE_NAMES
    ]

    found_names = {record["OBJECT_NAME"] for record in records}
    missing_names = SATELLITE_NAMES - found_names
    if missing_names:
        missing = ", ".join(sorted(missing_names))
        raise RuntimeError(f"Retlector response is missing: {missing}")

    return records


def update_omm_file() -> None:
    """Updates latest_omms.json with current OMMs"""
    records = download_latest_omms()
    temporary_path = OMM_FILE_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(OMM_FILE_PATH)
    print(f"Saved {len(records)} OMM records to {OMM_FILE_PATH}")


if __name__ == "__main__":
    update_omm_file()

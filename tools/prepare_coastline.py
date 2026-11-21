import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

SOURCE_SHA256 = "6f75ae0e0de157b14946e2255eb1f5486d9a13819032e26d4610852d296788f6"
SOURCE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "f1890d9f152c896d250a77557a5751a93d494776/geojson/ne_10m_coastline.geojson"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("assets/ne_10m_coastline.npz"))
    args = parser.parse_args()
    raw = args.source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("source differs from the pinned Natural Earth file")
    lines = []
    for feature in json.loads(raw)["features"]:
        geometry = feature["geometry"]
        if geometry["type"] == "LineString":
            lines.append(np.asarray(geometry["coordinates"], dtype=np.float64))
        elif geometry["type"] == "MultiLineString":
            lines.extend(np.asarray(line, dtype=np.float64) for line in geometry["coordinates"])
        else:
            raise ValueError("unexpected geometry type")
    meta_path = args.output.with_suffix(".json")
    if args.output.exists() or meta_path.exists():
        raise FileExistsError("choose new asset paths; existing files are not overwritten")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        np.savez_compressed(stream, lonlat=np.concatenate(lines), offsets=np.cumsum([0] + [len(x) for x in lines]))
    payload = args.output.read_bytes()
    metadata = {
        "title": "Natural Earth 1:10 million coastline",
        "release_tag": "v5.1.2",
        "repository_commit": "f1890d9f152c896d250a77557a5751a93d494776",
        "source_url": SOURCE_URL,
        "source_sha256": SOURCE_SHA256,
        "license": "public domain",
        "terms_url": "https://www.naturalearthdata.com/about/terms-of-use/",
        "description_url": "https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-coastline/",
        "coordinate_order": "longitude, latitude in degrees; WGS84",
        "conversion": "float64 coordinates and line boundaries preserved; properties omitted; no simplification",
        "line_count": len(lines),
        "vertex_count": sum(len(x) for x in lines),
        "asset": {"size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()},
        "limitations": "Cartographic reference, not surveyed truth; 10m denotes map scale, not 10 metre accuracy.",
    }
    with meta_path.open("x") as stream:
        json.dump(metadata, stream, indent=2)
        stream.write("\n")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()

import json

import cbor2
import cv2
import numpy as np
import pytest

PASS_NAME = "1791705353_20261011T075553Z_57166"


@pytest.fixture
def decoded_pass(tmp_path):
    """Small synthetic decoded product: useful pixels, known identity, and holes."""
    folder = tmp_path / "archive" / PASS_NAME
    product_dir = folder / "satdump_output" / "MSU-MR"
    product_dir.mkdir(parents=True)
    times = 1791705453.0 + 1.23114 * np.arange(20)
    times[4] = -1
    descriptors = []
    for channel in (2, 4):
        file = f"MSU-MR-{channel}.png"
        cv2.imwrite(str(product_dir / file), np.full((160, 16), 100, dtype=np.uint8))
        descriptors.append(
            {
                "abs_index": channel - 1,
                "file": file,
                "bit_depth": 10,
                "transform": {"type": 2, "ax": 1.0, "ay": 1.0, "bx": -1.6, "by": -1.6},
            }
        )
    product = {
        "product_source": "METEOR M2-3",
        "projection_cfg": {
            "timestamps": times.tolist(),
            "interpolate_timestamps": 8,
            "image_width": 16,
            "timestamp_offset": 1.0,
            "tle": {
                "line1": "1 57166U 23091A   26279.32335861 -.00000014 000000-0  12472-4 0  999C",
                "line2": "2 57166 98.59720 325.6356 0003277 211.4473 148.6509 14.2405328416941C",
            },
        },
        "images": descriptors,
    }
    (product_dir / "product.cbor").write_bytes(cbor2.dumps(product))
    (folder / "metadata.json").write_text(json.dumps({"norad_id": 57166, "omm_snapshot_id": 4}))
    (folder / "satdump_output" / "dataset.json").write_text(json.dumps({"satellite": "METEOR-M2-3"}))
    (folder / "satdump_output" / "telemetry.json").write_text(json.dumps([None, {"msu_mr_id": 3}]))
    return folder

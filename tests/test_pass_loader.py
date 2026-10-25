import cbor2
import cv2
import numpy as np

from src.pass_loader import load_pass, read_product

PASS_NAME = "1791705353_20261011T075553Z_57166"


def write_product(tmp_path, timestamps, images=()):
    product_dir = tmp_path / PASS_NAME / "satdump_output" / "MSU-MR"
    product_dir.mkdir(parents=True)
    product = {
        "projection_cfg": {
            "timestamps": timestamps,
            "interpolate_timestamps": 8,
        },
        "images": list(images),
    }
    path = product_dir / "product.cbor"
    with path.open("wb") as fh:
        cbor2.dump(product, fh)
    return path


def test_read_product_decodes_cbor(tmp_path):
    path = write_product(tmp_path, [1791705353.0])
    assert read_product(path)["projection_cfg"]["interpolate_timestamps"] == 8


def test_load_pass_reads_metadata_and_timing(tmp_path):
    timestamps = (1791705353.0 + 100 + 1.23114 * np.arange(20)).tolist()
    path = write_product(tmp_path, timestamps)

    loaded = load_pass(path.parents[2])

    assert loaded.name == PASS_NAME
    assert loaded.norad == 57166
    assert loaded.n_lines == 160
    assert loaded.status == "ok"


def test_load_pass_reports_available_channels(tmp_path):
    image = {"abs_index": 1, "file": "channel_2.png"}
    path = write_product(tmp_path, [1791705353.0 + 100 + 1.23114 * i for i in range(20)], [image])
    cv2.imwrite(str(path.parent / image["file"]), np.ones((160, 4), dtype=np.uint16))

    loaded = load_pass(path.parents[2], channels=(2, 4))

    assert loaded.channels_available == [2]
    assert list(loaded.images) == [2]

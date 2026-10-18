from __future__ import annotations

from typing import TYPE_CHECKING, Any

import cbor2

if TYPE_CHECKING:
    from pathlib import Path

MIN_VALID_FRAMES = 8
TIME_OUTLIER_S = 0.05


def find_products(root: Path) -> list[Path]:
    """Every product.cbor under root in a stable order"""
    return sorted(root.rglob("product.cbor"))


def read_product(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return cbor2.load(fh)

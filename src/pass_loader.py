import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cbor2
import numpy as np

from .imagery import (
    classify_light,
    daylight_fraction,
    line_ok,
    read_channel,
    usable_segments,
)
from .timing import TimeModel, aos_from_folder, build_time_model, line_times, timing_status

if TYPE_CHECKING:
    from collections.abc import Iterable

SATDUMP_SUBDIR = Path("satdump_output") / "MSU-MR"


def read_product(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return cbor2.load(fh)


@dataclass
class PassData:
    name: str
    norad: int
    aos: float
    product_dir: Path
    cfg: dict[str, Any]
    channels_available: list[int]
    lines_per_frame: int
    time_model: TimeModel
    status: str  # ok / partial / multi_segment / no_timing
    times: np.ndarray
    images: dict[int, np.ndarray] = field(default_factory=dict)
    tle: tuple[str, str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def n_lines(self) -> int:
        return self.times.size

    @property
    def line_period(self) -> float:
        return self.time_model.period / self.lines_per_frame

    def usable_lines(self, channels: Iterable[int] = (2, 4)) -> np.ndarray:
        """lines with a trusted time and real data in every given channel"""
        ok = np.isfinite(self.times)
        for ch in channels:
            if ch not in self.images:
                raise KeyError(f"channel {ch} is not loaded for {self.name}")
            ok &= line_ok(self.images[ch])
        return ok

    def segments(self, channels: Iterable[int] = (2, 4)) -> list[tuple[int, int]]:
        return usable_segments(self.usable_lines(channels))

    def light(self) -> str:
        """day/night/mixed judged from channel 2"""
        ch2 = self.images[2]
        return classify_light(daylight_fraction(ch2, line_ok(ch2)))


def load_pass(folder: Path | str, channels: Iterable[int] = ()) -> PassData:
    folder = Path(folder)
    product_dir = folder / SATDUMP_SUBDIR
    product = read_product(product_dir / "product.cbor")
    cfg = dict(product["projection_cfg"])
    raw_timestamps = cfg.pop("timestamps")
    lpf = int(cfg["interpolate_timestamps"])

    aos = aos_from_folder(folder.name)
    model = build_time_model(raw_timestamps, aos)
    times = line_times(model, len(raw_timestamps), lpf)
    available = sorted(int(i["abs_index"]) + 1 for i in product.get("images", []))
    status = timing_status(model)

    images: dict[int, np.ndarray] = {}
    if status != "no_timing":
        for ch in channels:
            if ch in available:
                img = read_channel(product_dir, product, ch)
                if img.shape[0] != times.size:
                    raise ValueError(
                        f"channel {ch}: {img.shape[0]} image lines, but {times.size} lines from the timestamps"
                    )
                images[ch] = img

    tle = cfg.get("tle")
    meta_file = folder / "metadata.json"
    return PassData(
        name=folder.name,
        norad=int(folder.name.split("_")[2]),
        aos=aos,
        product_dir=product_dir,
        cfg=cfg,
        channels_available=available,
        lines_per_frame=lpf,
        time_model=model,
        status=status,
        times=times,
        images=images,
        tle=(tle["line1"], tle["line2"]) if tle else None,
        metadata=json.loads(meta_file.read_text()) if meta_file.is_file() else {},
    )

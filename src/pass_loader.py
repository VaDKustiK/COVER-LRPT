import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cbor2
import numpy as np

from .identity import IdentityCheck, IdentityConflict, check_identity
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
    raw_timestamps: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    channel_metadata: dict[int, dict[str, Any]] = field(default_factory=dict)
    identity: IdentityCheck | None = None

    @property
    def n_lines(self) -> int:
        return self.times.size

    @property
    def line_period(self) -> float:
        return self.time_model.period / self.lines_per_frame

    def usable_lines(self, channels: Iterable[int] = (2, 4)) -> np.ndarray:
        """Rows with a model time and enough nonzero pixels; a screening heuristic."""
        ok = np.isfinite(self.times)
        for ch in channels:
            if ch not in self.images:
                raise KeyError(f"channel {ch} is not loaded for {self.name}")
            ok &= line_ok(self.images[ch])
        return ok

    def segments(self, channels: Iterable[int] = (2, 4)) -> list[tuple[int, int]]:
        """Candidate windows, possibly bridging holes. Always retain usable_lines()."""
        return usable_segments(self.usable_lines(channels))

    @property
    def timed_fraction(self) -> float:
        """Fraction of all image rows assigned a model time, separate from status."""
        return float(np.isfinite(self.times).mean()) if self.times.size else 0.0

    def light(self) -> str:
        """day/night/mixed judged from channel 2"""
        ch2 = self.images[2]
        return classify_light(daylight_fraction(ch2, line_ok(ch2)))


def load_pass(folder: Path | str, channels: Iterable[int] = (), *, strict_identity: bool = True) -> PassData:
    """Read original arrays and metadata without modifying or resampling the archive.

    Conflicting identities raise by default. Missing evidence is exposed as
    'incomplete'; strict_identity=False is for inventory/audit, not orbit fitting.
    """
    folder = Path(folder)
    product_dir = folder / SATDUMP_SUBDIR
    product = read_product(product_dir / "product.cbor")
    cfg = dict(product["projection_cfg"])
    raw_timestamps = np.asarray(cfg.pop("timestamps"), dtype=float)
    lpf = cfg["interpolate_timestamps"]
    if not isinstance(lpf, int) or isinstance(lpf, bool) or lpf <= 0:
        raise ValueError("interpolate_timestamps must be a positive integer")

    aos = aos_from_folder(folder.name)
    norad = int(folder.name.split("_")[2])
    meta_file = folder / "metadata.json"
    metadata = json.loads(meta_file.read_text()) if meta_file.is_file() else {}
    dataset_file = folder / "satdump_output" / "dataset.json"
    dataset = json.loads(dataset_file.read_text()) if dataset_file.is_file() else {}
    telemetry_file = folder / "satdump_output" / "telemetry.json"
    telemetry = json.loads(telemetry_file.read_text()) if telemetry_file.is_file() else []
    if telemetry is None:
        telemetry = []  # SatDump emits JSON null when no telemetry was decoded.
    if not isinstance(metadata, dict) or not isinstance(dataset, dict) or not isinstance(telemetry, list):
        raise ValueError("metadata/dataset must be objects and telemetry must be a list")
    identity = check_identity(norad, product, metadata, dataset, telemetry)
    if strict_identity and identity.status == "conflict":
        raise IdentityConflict(f"{folder.name}: {'; '.join(identity.issues)}")
    model = build_time_model(raw_timestamps, aos)
    times = line_times(model, len(raw_timestamps), lpf)
    available = sorted(int(i["abs_index"]) + 1 for i in product.get("images", []))
    if len(set(available)) != len(available):
        raise ValueError("duplicate channel descriptors in product")
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
                if "image_width" in cfg and img.shape[1] != int(cfg["image_width"]):
                    raise ValueError(f"channel {ch}: width disagrees with projection_cfg.image_width")
                images[ch] = img

    tle = cfg.get("tle")
    return PassData(
        name=folder.name,
        norad=norad,
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
        metadata=metadata,
        raw_timestamps=raw_timestamps.copy(),
        channel_metadata={int(i["abs_index"]) + 1: dict(i) for i in product.get("images", [])},
        identity=identity,
    )

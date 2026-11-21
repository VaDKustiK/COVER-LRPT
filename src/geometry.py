from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from skyfield.api import EarthSatellite, load
from skyfield.framelib import itrs

if TYPE_CHECKING:
    from .pass_loader import PassData

A_KM = 6378.137
B_KM = A_KM * (1 - 1 / 298.257223563)
AXES_KM = np.array([A_KM, A_KM, B_KM])


def unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def rotate(v: np.ndarray, axis: np.ndarray, degrees: Any) -> np.ndarray:
    """Right-hand Rodrigues rotation; vectors have their components on the last axis."""
    k = unit(axis)
    angle = np.deg2rad(np.asarray(degrees))[..., None]
    c, s = np.cos(angle), np.sin(angle)
    return v * c + np.cross(k, v) * s + k * np.sum(k * v, axis=-1, keepdims=True) * (1 - c)


def geodetic_to_ecef(latitude: Any, longitude: Any, height_km: Any = 0) -> np.ndarray:
    lat, lon, h = np.broadcast_arrays(np.deg2rad(latitude), np.deg2rad(longitude), height_km)
    e2 = 1 - (B_KM / A_KM) ** 2
    n = A_KM / np.sqrt(1 - e2 * np.sin(lat) ** 2)
    return np.stack(
        ((n + h) * np.cos(lat) * np.cos(lon), (n + h) * np.cos(lat) * np.sin(lon), (n * (1 - e2) + h) * np.sin(lat)),
        axis=-1,
    )


def ecef_to_geodetic(xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Latitude/longitude on or above WGS84, including the poles (degrees)."""
    x, y, z = np.moveaxis(np.asarray(xyz, dtype=float), -1, 0)
    rho = np.hypot(x, y)
    e2 = 1 - (B_KM / A_KM) ** 2
    lat = np.arctan2(z, rho * (1 - e2))
    for _ in range(8):
        n = A_KM / np.sqrt(1 - e2 * np.sin(lat) ** 2)
        lat = np.arctan2(z + e2 * n * np.sin(lat), rho)
    return np.rad2deg(lat), np.rad2deg(np.arctan2(y, x))


def intersect_ellipsoid(origin: np.ndarray, direction: np.ndarray) -> np.ndarray:
    """Nearest forward ray/surface intersection; NaN for misses or interior origins."""
    origin, direction = np.broadcast_arrays(origin, direction)
    o, d = origin / AXES_KM, unit(direction) / AXES_KM
    aa = np.sum(d * d, axis=-1)
    bb = 2 * np.sum(o * d, axis=-1)
    cc = np.sum(o * o, axis=-1) - 1
    disc = bb * bb - 4 * aa * cc
    distance = (-bb - np.sqrt(np.maximum(disc, 0))) / (2 * aa)
    good = (disc >= 0) & (distance >= 0) & (cc > 0)
    return np.where(good[..., None], origin + distance[..., None] * unit(direction), np.nan)


@dataclass(frozen=True)
class Scanner:
    width: int
    scan_angle_deg: float
    roll_deg: float = 0
    pitch_deg: float = 0
    yaw_deg: float = 0
    invert_scan: bool = False

    def __post_init__(self):
        if (
            self.width < 2
            or not np.isfinite([self.scan_angle_deg, self.roll_deg, self.pitch_deg, self.yaw_deg]).all()
            or not 0 < self.scan_angle_deg < 180
        ):
            raise ValueError("invalid scanner dimensions or angles")

    @classmethod
    def from_pass(cls, p: PassData) -> Scanner:
        cfg = p.cfg
        if cfg.get("type") != "normal_single_line" or cfg.get("rotate_yaw", False):
            raise ValueError("only normal_single_line roll scanning is supported")
        if cfg.get("yaw_offset_asc", 0) or cfg.get("yaw_offset_des", 0):
            raise ValueError("orbit-direction-dependent yaw is not supported")
        transform = p.channel_metadata.get(2, {}).get("transform", {})
        if any(transform.get(k) != v for k, v in {"type": 2, "ax": 1, "ay": 1, "bx": 0, "by": 0}.items()):
            raise ValueError("channel 2 must declare an identity transform")
        return cls(
            int(cfg["image_width"]),
            float(cfg["scan_angle"]),
            float(cfg.get("roll_offset", 0)),
            float(cfg.get("pitch_offset", 0)),
            float(cfg.get("yaw_offset", 0)),
            bool(cfg.get("invert_scan", False)),
        )


def viewing_direction(
    position: np.ndarray, inertial_velocity: np.ndarray, column: Any, scanner: Scanner
) -> np.ndarray:
    """Geodetic nadir; yaw about nadir, roll about yawed inertial velocity, then pitch.

    Velocity is rotated into ITRS, NOT the derivative of Earth-fixed position.
    Column width/2 is the reference boresight, matching the documented decoder convention.
    """
    lat, lon = ecef_to_geodetic(position)
    down = unit(geodetic_to_ecef(lat, lon) - position)
    along = rotate(unit(inertial_velocity), down, scanner.yaw_deg)
    pitch_axis = rotate(along, down, 90)
    sign = -1 if scanner.invert_scan else 1
    roll = sign * (np.asarray(column) / scanner.width - 0.5) * scanner.scan_angle_deg + scanner.roll_deg
    return unit(rotate(rotate(down, along, roll), pitch_axis, scanner.pitch_deg))


def observation_times(p: PassData, convention: str, extra_offset_s: float = 0) -> np.ndarray:
    """Explicit timing hypotheses; configured timestamp_offset is added exactly once.

    'fitted' uses the loader model. 'satdump' uses centered expansion of accepted
    raw frame stamps; it is a comparison, not a reproduction of SatDump's time filter.
    """
    if convention == "fitted":
        times = p.times.copy()
    elif convention == "satdump":
        if p.lines_per_frame % 2:
            raise ValueError("centered decoder expansion requires an even number of rows per frame")
        j = np.arange(p.lines_per_frame) - p.lines_per_frame // 2
        times = (p.raw_timestamps[:, None] + j * float(p.cfg["interpolate_timestamps_scantime"])).ravel()
        times[~np.isfinite(p.times)] = np.nan
    else:
        raise ValueError("choose fitted or satdump timing explicitly")
    offset = float(p.cfg.get("timestamp_offset", 0)) + extra_offset_s
    if not np.isfinite(offset):
        raise ValueError("time offsets must be finite")
    return times + offset


class MapPredictor:
    """Reusable orbit/scanner model with row state caching and bounded inverse projection."""

    def __init__(self, satellite: EarthSatellite, times: np.ndarray, scanner: Scanner):
        self.satellite, self.scanner = satellite, scanner
        self.ts = timescale()
        self.times = np.asarray(times, dtype=float).copy()
        if self.times.ndim != 1 or len(self.times) < 2 or not np.isfinite(self.times).any():
            raise ValueError("need a one-dimensional row-time array with finite observations")
        if np.isinf(self.times).any():
            raise ValueError("infinite row time is invalid; use NaN for missing rows")
        self.height = len(times)
        self.position = np.full((self.height, 3), np.nan)
        self.velocity = self.position.copy()
        good = np.isfinite(times)
        self.position[good], self.velocity[good] = self.propagate(self.times[good])
        self._seed_cache = None

    def propagate(self, unix_seconds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ts = self.ts
        # Split Unix days to avoid avoidable cancellation in a single Julian-date float.
        days, seconds = np.divmod(unix_seconds, 86400)
        t = ts.utc(1970, 1, 1 + days, 0, 0, seconds)
        state = self.satellite.at(t)
        if any(message is not None for message in np.atleast_1d(state.message)):
            raise ValueError(f"SGP4 propagation failed: {state.message}")
        rotation = itrs.rotation_at(t)
        position = np.einsum("ijt,jt->ti", rotation, state.position.km)
        velocity = np.einsum("ijt,jt->ti", rotation, state.velocity.km_per_s)
        if not np.isfinite(position).all() or not np.isfinite(velocity).all():
            raise ValueError("nonfinite propagated state")
        return position, velocity

    def states(self, rows: np.ndarray, *, exact: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        rows = np.asarray(rows, dtype=float)
        inside = np.isfinite(rows) & (rows >= 0) & (rows <= self.height - 1)
        safe = np.where(inside, rows, 0)
        lo = np.floor(safe).astype(int)
        hi = np.ceil(safe).astype(int)
        fraction = safe - lo
        valid = inside & np.isfinite(self.times[lo]) & np.isfinite(self.times[hi])
        # Never interpolate across a missing row. Exact integer rows do not need neighbors.
        position = self.position[lo] * (1 - fraction[..., None]) + self.position[hi] * fraction[..., None]
        velocity = self.velocity[lo] * (1 - fraction[..., None]) + self.velocity[hi] * fraction[..., None]
        if exact and valid.any():
            t = self.times[lo[valid]] * (1 - fraction[valid]) + self.times[hi[valid]] * fraction[valid]
            position[valid], velocity[valid] = self.propagate(t)
        return position, velocity, valid

    def pixel_to_ecef(self, columns: Any, rows: Any, *, exact: bool = False) -> np.ndarray:
        x, y = np.broadcast_arrays(np.asarray(columns, float), np.asarray(rows, float))
        shape = x.shape
        x, y = x.ravel(), y.ravel()
        pos, vel, valid = self.states(y, exact=exact)
        valid &= np.isfinite(x) & (x >= 0) & (x <= self.scanner.width - 1)
        result = np.full((x.size, 3), np.nan)
        if valid.any():
            direction = viewing_direction(pos[valid], vel[valid], x[valid], self.scanner)
            result[valid] = intersect_ellipsoid(pos[valid], direction)
        return result.reshape((*shape, 3))

    def pixel_to_geo(self, columns: Any, rows: Any) -> tuple[np.ndarray, np.ndarray]:
        return ecef_to_geodetic(self.pixel_to_ecef(columns, rows))

    def seeds(self) -> tuple[np.ndarray, np.ndarray]:
        if self._seed_cache is None:
            good = np.flatnonzero(np.isfinite(self.times))
            ends = good[np.r_[True, np.diff(good) > 1] | np.r_[np.diff(good) > 1, True]]
            rows = np.unique(np.r_[good[::48], ends])
            xx, yy = np.meshgrid(np.linspace(0, self.scanner.width - 1, 17), rows)
            pixels = np.column_stack((xx.ravel(), yy.ravel()))
            xyz = self.pixel_to_ecef(pixels[:, 0], pixels[:, 1])
            valid = np.isfinite(xyz).all(axis=1)
            self._seed_cache = pixels[valid], xyz[valid]
        return self._seed_cache

    def geo_to_pixel(self, latitude: Any, longitude: Any, *, tolerance_km: float = 0.001) -> np.ndarray:
        """Inverse via coarse seeds and local least squares; failed/outside points are NaN.

        The residual threshold is numerical convergence, NOT geolocation accuracy.
        Memory is bounded by batches, and distance calculations use ECEF across the dateline.
        """
        lat, lon = np.broadcast_arrays(np.asarray(latitude, float), np.asarray(longitude, float))
        if not np.isfinite(tolerance_km) or tolerance_km <= 0:
            raise ValueError("inverse convergence tolerance must be finite and positive")
        target = geodetic_to_ecef(lat.ravel(), lon.ravel())
        pixels, grid = self.seeds()
        out = np.full((target.shape[0], 2), np.nan)
        if not len(grid):
            return out.reshape((*lat.shape, 2))
        candidate = np.isfinite(target).all(axis=1) & (np.abs(lat.ravel()) <= 90)
        candidate &= ((target >= grid.min(axis=0) - 50) & (target <= grid.max(axis=0) + 50)).all(axis=1)
        ids = np.flatnonzero(candidate)
        timed = np.isfinite(self.times)
        run_starts = np.flatnonzero(timed & np.r_[True, ~timed[:-1]])
        run_ends = np.flatnonzero(timed & np.r_[~timed[1:], True])
        for start in range(0, len(ids), 256):
            idx = ids[start : start + 256]
            goals = target[idx]
            distances = np.sum((goals[:, None, :] - grid[None, :, :]) ** 2, axis=2)
            xy = pixels[np.argmin(distances, axis=1)].copy()
            run = np.searchsorted(run_starts, xy[:, 1], side="right") - 1
            for _ in range(12):
                ground = self.pixel_to_ecef(xy[:, 0], xy[:, 1])
                residual = goals - ground
                derivatives = []
                for axis, upper in ((0, self.scanner.width - 1), (1, self.height - 1)):
                    lower, higher = xy.copy(), xy.copy()
                    lower[:, axis] = np.maximum(0, xy[:, axis] - 0.05)
                    higher[:, axis] = np.minimum(upper, xy[:, axis] + 0.05)
                    gl = self.pixel_to_ecef(lower[:, 0], lower[:, 1])
                    gh = self.pixel_to_ecef(higher[:, 0], higher[:, 1])
                    # One-sided derivatives at timing gaps; never bridge the gap itself.
                    bad_l, bad_h = ~np.isfinite(gl).all(axis=1), ~np.isfinite(gh).all(axis=1)
                    gl[bad_l], lower[bad_l] = ground[bad_l], xy[bad_l]
                    gh[bad_h], higher[bad_h] = ground[bad_h], xy[bad_h]
                    denominator = higher[:, axis] - lower[:, axis]
                    with np.errstate(invalid="ignore", divide="ignore"):
                        derivatives.append((gh - gl) / denominator[:, None])
                dx, dy = derivatives
                aa, ab, bb = np.sum(dx * dx, axis=1), np.sum(dx * dy, axis=1), np.sum(dy * dy, axis=1)
                ar, br = np.sum(dx * residual, axis=1), np.sum(dy * residual, axis=1)
                det = aa * bb - ab * ab
                with np.errstate(invalid="ignore", divide="ignore"):
                    delta = np.column_stack(((bb * ar - ab * br) / det, (aa * br - ab * ar) / det))
                xy += np.clip(delta, -128, 128)
                # A coarse Newton step can overshoot an image/gap boundary even for
                # an interior target. Constrain the solve to its seed's timed run;
                # outside targets still fail the final surface-residual check.
                xy[:, 0] = np.clip(xy[:, 0], 0, self.scanner.width - 1)
                xy[:, 1] = np.clip(xy[:, 1], run_starts[run], run_ends[run])
            residual = np.linalg.norm(self.pixel_to_ecef(xy[:, 0], xy[:, 1]) - goals, axis=1)
            accepted = np.isfinite(residual) & (residual <= tolerance_km)
            out[idx[accepted]] = xy[accepted]
        return out.reshape((*lat.shape, 2))


def timescale():
    """Bundled Skyfield time tables: no download. Their version is recorded in runs."""
    return load.timescale(builtin=True)

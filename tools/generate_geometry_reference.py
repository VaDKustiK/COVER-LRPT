import argparse
import ctypes
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

DRIVER = r"""
#include <cmath>
#include "common/geodetic/euler_raytrace.h"
extern "C" {
#include "libs/predict/unsorted.h"
}
extern "C" int reference_ray(const double *position, const double *velocity,
                             double roll, double pitch, double yaw, double *out) {
    const double jd=2461314.0, theta=ThetaG_JD(jd);
    double p[3], v[3];
    p[0]=cos(theta)*position[0]-sin(theta)*position[1];
    p[1]=sin(theta)*position[0]+cos(theta)*position[1]; p[2]=position[2];
    v[0]=cos(theta)*velocity[0]-sin(theta)*velocity[1];
    v[1]=sin(theta)*velocity[0]+cos(theta)*velocity[1]; v[2]=velocity[2];
    geodetic::geodetic_coords_t ground;
    int result=geodetic::raytrace_to_earth(jd-2444238.5,p,v,
                   geodetic::euler_coords_t(roll,pitch,yaw),ground);
    out[0]=ground.lat; out[1]=ground.lon; return result;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--satdump-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    root = args.satdump_root.resolve() / "src-core"
    sources = [
        "common/geodetic/euler_raytrace.cpp",
        "common/geodetic/geodetic_coordinates.cpp",
        "common/geodetic/euler_coordinates.cpp",
    ]
    dependencies = [
        *sources,
        "common/geodetic/euler_raytrace.h",
        "common/geodetic/geodetic_coordinates.h",
        "common/geodetic/euler_coordinates.h",
        "common/geodetic/wgs84.h",
        "libs/predict/unsorted.c",
        "libs/predict/unsorted.h",
        "libs/predict/defs.h",
        "libs/predict/predict.h",
    ]
    hashes = {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in dependencies}
    if hashes[sources[0]] != "3288787f2a753abbf88807c47ea8dace8bed6f9e400e04ba17619f17a50d7206":
        raise ValueError("raytracer differs from the inspected upstream implementation")
    with tempfile.TemporaryDirectory(prefix="cover-reference-") as temporary:
        work = Path(temporary)
        (work / "driver.cpp").write_text(DRIVER)
        subprocess.run(
            ["cc", "-fPIC", "-c", str(root / "libs/predict/unsorted.c"), "-o", str(work / "unsorted.o")], check=True
        )
        subprocess.run(
            [
                "c++",
                "-shared",
                "-fPIC",
                "-I" + str(root),
                str(work / "driver.cpp"),
                str(work / "unsorted.o"),
                *[str(root / p) for p in sources],
                "-o",
                str(work / "reference.so"),
            ],
            check=True,
        )
        library = ctypes.CDLL(str(work / "reference.so"))
        pointer = ctypes.POINTER(ctypes.c_double)
        library.reference_ray.argtypes = [pointer, pointer, ctypes.c_double, ctypes.c_double, ctypes.c_double, pointer]
        library.reference_ray.restype = ctypes.c_int
        cases = []
        states = [
            ([7200.0, 0.0, 0.0], [0.1, -1.0, 7.3]),
            ([-6000.0, 200.0, 3900.0], [4.0, 1.0, 6.0]),
            ([500.0, 300.0, 7200.0], [-7.3, -1.0, 0.6]),
            ([-7000.0, -100.0, 1000.0], [1.0, -1.0, 7.0]),
            ([4000.0, 5000.0, -3500.0], [4.0, 1.0, 6.0]),
        ]
        for position, velocity in states:
            for roll, pitch, yaw in [
                (-55.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                (55.0, 0.0, 0.0),
                (-35.0, 1.0, 2.0),
                (80.0, 0.0, 0.0),
            ]:
                out = (ctypes.c_double * 2)()
                status = library.reference_ray(
                    (ctypes.c_double * 3)(*position), (ctypes.c_double * 3)(*velocity), roll, pitch, yaw, out
                )
                cases.append(
                    {
                        "position_ecef_km": position,
                        "inertial_velocity_in_ecef_km_s": velocity,
                        "roll_deg": roll,
                        "pitch_deg": pitch,
                        "yaw_deg": yaw,
                        "miss": bool(status),
                        "expected_lat_lon_deg": list(out) if not status else None,
                    }
                )
    record = {
        "reference": "SatDump separately compiled C++ raytrace_to_earth, no COVER geometry imports",
        "raytracer_upstream_commit": "9fee0793c7d42bea2d401d330422e18c33c126fa",
        "dependency_sha256": hashes,
        "driver_sha256": hashlib.sha256(DRIVER.encode()).hexdigest(),
        "scope": "pointing and ellipsoid only; not orbit propagation or timestamp calibration",
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    print(f"Saved {len(cases)} independent reference cases")


if __name__ == "__main__":
    main()

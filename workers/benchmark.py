"""Measure a single-process, sequential TartanGrid render."""

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))
from mandelbrot import BACKEND, compute_tile_numba, compute_tile_numpy, split_inputs


DEFAULT_PRESETS = {
    "quick": dict(width=512, height=384, tile_size=64, max_iter=256,
                  x_min=-2.2, x_max=1.0, y_min=-1.2, y_max=1.2),
    "demo": dict(width=2048, height=1536, tile_size=128, max_iter=8000,
                 x_min=-0.8, x_max=-0.7, y_min=0.05, y_max=0.125),
}


def load_presets():
    try:
        presets = json.loads((BACKEND_DIR / "presets.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_PRESETS
    return {preset["id"]: preset["params"] for preset in presets}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", required=True)
    parser.add_argument("--backend", choices=("numba", "numpy"), default=BACKEND)
    parser.add_argument("--tile-sample", type=int, default=0)
    args = parser.parse_args()
    if args.tile_sample < 0:
        parser.error("--tile-sample must be nonnegative")
    if args.backend == "numba" and BACKEND != "numba":
        parser.error("Numba is unavailable; install numba or use --backend numpy")
    try:
        presets = load_presets()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(f"Invalid presets.json: {exc}")
    if args.preset not in presets:
        parser.error(f"Unknown preset; available: {', '.join(presets)}")
    tiles = split_inputs(presets[args.preset])
    total_tiles = len(tiles)
    if args.tile_sample and args.tile_sample < total_tiles:
        count = args.tile_sample
        tiles = [tiles[(2 * i + 1) * total_tiles // (2 * count)] for i in range(count)]

    if args.backend == "numba":
        compute = compute_tile_numba
        compute(dict(tiles[0], tile_w=1, tile_h=1, max_iter=1))
    else:
        compute = compute_tile_numpy
    timings, iterations = [], 0
    started = perf_counter()
    for tile in tiles:
        tile_started = perf_counter()
        result = compute(tile)
        timings.append(perf_counter() - tile_started)
        iterations += sum(result)
    elapsed = perf_counter() - started
    measured = len(tiles)
    rate = iterations / sum(timings)
    minimum, middle, maximum = (1000 * duration for duration in
                                (min(timings), median(timings), max(timings)))
    total_time = elapsed
    note = ""
    if measured < total_tiles:
        total_time *= total_tiles / measured
        note = " (estimate)"
        print(f"Measured sample: {measured}/{total_tiles} tiles in {elapsed:.3f}s; "
              "iteration count and tile statistics refer to the sample.")
    print(f"T_single (native, {args.backend}): {total_time:.3f}s{note}")
    print(f"Tile min/median/max: {minimum:.2f}/{middle:.2f}/{maximum:.2f} ms")
    print(f"Iterations: {iterations:,} | iter/s: {rate:,.0f}")
    print(f"| {args.preset} | native, {args.backend} | {measured}/{total_tiles} | "
          f"{total_time:.3f}s{note} | {minimum:.2f}/{middle:.2f}/{maximum:.2f} ms | "
          f"{iterations:,} | {rate:,.0f} |")


if __name__ == "__main__":
    main()

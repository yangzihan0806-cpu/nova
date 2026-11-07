import json
from pathlib import Path
import shutil
import subprocess

import pytest

from backend.mandelbrot import (
    BACKEND,
    compute_pixel,
    compute_tile,
    compute_tile_numba,
    compute_tile_numpy,
    split_inputs,
)


QUICK = dict(width=512, height=384, tile_size=64, max_iter=256,
             x_min=-2.2, x_max=1.0, y_min=-1.2, y_max=1.2)
DEMO = dict(width=2048, height=1536, tile_size=32, max_iter=500,
            x_min=-0.8, x_max=-0.7, y_min=0.05, y_max=0.125)


@pytest.fixture(params=[
    (QUICK, 0, 0), (QUICK, 4, 3), (QUICK, 7, 5),
    (DEMO, 0, 0), (DEMO, 32, 24), (DEMO, 63, 47),
])
def tile_and_expected(request):
    params, col, row = request.param
    size = params["tile_size"]
    tile = split_inputs(params)[row * (params["width"] // size) + col]
    expected = [
        compute_pixel(px, py, tile)
        for py in range(row * size, (row + 1) * size)
        for px in range(col * size, (col + 1) * size)
    ]
    return tile, expected


def test_split_inputs():
    tiles = split_inputs(QUICK)
    assert len(tiles) == 48
    assert [(tile["tile_x"], tile["tile_y"]) for tile in tiles] == [
        (x, y) for y in range(0, 384, 64) for x in range(0, 512, 64)
    ]
    assert tiles[0] == {
        "tile_x": 0, "tile_y": 0, "tile_w": 64, "tile_h": 64,
        "image_width": 512, "image_height": 384, "max_iter": 256,
        "x_min": -2.2, "x_max": 1.0, "y_min": -1.2, "y_max": 1.2,
    }


def test_numpy(tile_and_expected):
    tile, expected = tile_and_expected
    assert compute_tile_numpy(tile) == expected


@pytest.mark.skipif(BACKEND != "numba", reason="Numba is unavailable")
def test_numba(tile_and_expected):
    tile, expected = tile_and_expected
    assert compute_tile_numba(tile) == expected


def test_dispatch(tile_and_expected):
    tile, expected = tile_and_expected
    assert compute_tile(tile) == expected


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js is unavailable")
def test_javascript(tile_and_expected):
    tile, expected = tile_and_expected
    script = Path(__file__).with_name("js_kernel_dump.js")
    output = subprocess.check_output(["node", str(script), json.dumps(tile)], text=True)
    assert json.loads(output) == expected

"""Deterministic Mandelbrot tile kernels for the frozen worker protocol."""

import numpy as np

try:
    from numba import njit
except ImportError:
    njit = None

BACKEND = "numba" if njit is not None else "numpy"


def compute_pixel(px, py, p):          # px, py 是全图像素坐标；py = 0 是最上面一行
    dx = (p["x_max"] - p["x_min"]) / p["image_width"]
    dy = (p["y_max"] - p["y_min"]) / p["image_height"]
    cr = p["x_min"] + (px + 0.5) * dx
    ci = p["y_max"] - (py + 0.5) * dy
    zr = 0.0; zi = 0.0; n = 0
    while n < p["max_iter"] and zr*zr + zi*zi <= 4.0:
        t = zr*zr - zi*zi + cr
        zi = 2.0*zr*zi + ci
        zr = t
        n += 1
    return n


def split_inputs(params):
    size = params["tile_size"]
    common = {
        "tile_w": size,
        "tile_h": size,
        "image_width": params["width"],
        "image_height": params["height"],
        "max_iter": params["max_iter"],
        "x_min": params["x_min"],
        "x_max": params["x_max"],
        "y_min": params["y_min"],
        "y_max": params["y_max"],
    }
    return [
        {"tile_x": x, "tile_y": y, **common}
        for y in range(0, params["height"], size)
        for x in range(0, params["width"], size)
    ]


def compute_tile_numpy(inp) -> list[int]:
    x, y = inp["tile_x"], inp["tile_y"]
    w, h = inp["tile_w"], inp["tile_h"]
    dx = (inp["x_max"] - inp["x_min"]) / inp["image_width"]
    dy = (inp["y_max"] - inp["y_min"]) / inp["image_height"]
    px = np.arange(x, x + w, dtype=np.float64)
    py = np.arange(y, y + h, dtype=np.float64)
    cr = inp["x_min"] + (px + 0.5) * dx
    ci = inp["y_max"] - (py + 0.5) * dy
    cr, ci = np.broadcast_arrays(cr[None, :], ci[:, None])
    zr = np.zeros((h, w), dtype=np.float64)
    zi = np.zeros_like(zr)
    counts = np.zeros((h, w), dtype=np.int32)
    active = np.ones((h, w), dtype=bool)
    for _ in range(inp["max_iter"]):
        active &= zr*zr + zi*zi <= 4.0
        if not active.any():
            break
        real, imag = zr[active], zi[active]
        t = real*real - imag*imag + cr[active]
        zi[active] = 2.0*real*imag + ci[active]
        zr[active] = t
        counts[active] += 1
    return counts.ravel().tolist()


def _tile_loop(x, y, w, h, max_iter, x_min, y_max, dx, dy):
    counts = np.empty(w * h, dtype=np.int32)
    for j in range(h):
        py = y + j
        ci = y_max - (py + 0.5) * dy
        for i in range(w):
            px = x + i
            cr = x_min + (px + 0.5) * dx
            zr, zi, n = 0.0, 0.0, 0
            while n < max_iter and zr*zr + zi*zi <= 4.0:
                t = zr*zr - zi*zi + cr
                zi = 2.0*zr*zi + ci
                zr = t
                n += 1
            counts[j * w + i] = n
    return counts


if njit is not None:
    # Cache entries contain the import name; keep script and package imports separate.
    _tile_loop.__qualname__ = f"{__name__}._tile_loop"
    _tile_loop = njit(nogil=True, cache=True)(_tile_loop)


def compute_tile_numba(inp) -> list[int]:
    if njit is None:
        raise RuntimeError("Numba is unavailable")
    dx = (inp["x_max"] - inp["x_min"]) / inp["image_width"]
    dy = (inp["y_max"] - inp["y_min"]) / inp["image_height"]
    return _tile_loop(
        inp["tile_x"], inp["tile_y"], inp["tile_w"], inp["tile_h"],
        inp["max_iter"], inp["x_min"], inp["y_max"], dx, dy,
    ).tolist()


compute_tile = compute_tile_numba if BACKEND == "numba" else compute_tile_numpy

function computeTile(input) {
    const {
        tile_x, tile_y, tile_w, tile_h, image_width, image_height,
        max_iter, x_min, x_max, y_min, y_max,
    } = input;
    const dx = (x_max - x_min) / image_width;
    const dy = (y_max - y_min) / image_height;
    const out = new Int32Array(tile_w * tile_h);
    for (let j = 0; j < tile_h; j++) {
        const py = tile_y + j;
        const ci = y_max - (py + 0.5) * dy;
        for (let i = 0; i < tile_w; i++) {
            const px = tile_x + i;
            const cr = x_min + (px + 0.5) * dx;
            let zr = 0.0, zi = 0.0, n = 0;
            while (n < max_iter && zr*zr + zi*zi <= 4.0) {
                const t = zr*zr - zi*zi + cr;
                zi = 2.0*zr*zi + ci;
                zr = t;
                n++;
            }
            out[j * tile_w + i] = n;
        }
    }
    return out;
}

if (typeof module !== "undefined") module.exports = { computeTile };

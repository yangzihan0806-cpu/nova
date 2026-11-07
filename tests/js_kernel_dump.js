const { computeTile } = require("../backend/static/mandelbrot_kernel.js");

console.log(JSON.stringify(Array.from(computeTile(JSON.parse(process.argv[2])))));

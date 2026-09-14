# V4.7.0 Performance Summary

Measurements used the same isolated-process harness and deterministic fixtures as the V4.6.9 audit on the i5-12400F / 32 GiB workstation. Times are warm-cache parser or kernel time. Peak memory is the operation's sampled RSS increase, so small differences near the sampler interval should not be over-interpreted.

| Case | Points | V4.6.9 | V4.7.0 | Speedup | Peak RSS delta old → new |
|---|---:|---:|---:|---:|---:|
| Physical CSV import | 2M | 12.115 s | 0.671 s | 18.07× | 616.72 → 96.37 MiB |
| Physical CSV import | 5M | 29.504 s | 1.649 s | 17.89× | 1536.39 → 207.33 MiB |
| Quoted CSV import | 2M | 12.059 s | 1.257 s | 9.60× | 616.34 → 96.46 MiB |
| TSV import | 2M | 12.069 s | 0.746 s | 16.18× | 616.27 → 96.49 MiB |
| Whitespace import | 2M | 10.781 s | 1.547 s | 6.97× | 616.79 → 96.16 MiB |
| Pixel CSV import | 2M | 11.985 s | 0.726 s | 16.51× | 616.75 → 97.25 MiB |
| Z Matrix import | 2M | 4.240 s | 1.435 s | 2.96× | 184.64 → 162.91 MiB |
| Z Matrix import | 5M | 9.479 s | 3.420 s | 2.77× | 650.76 → 640.28 MiB |
| Keyence synthetic | 2M | 4.384 s | 1.739 s | 2.52× | 183.92 → 254.20 MiB |
| Matrix topology | 2M | 15.558 s | 1.677 s | 9.28× | 1255.05 → 383.01 MiB |
| Matrix topology | 5M | 40.340 s | 4.562 s | 8.84× | 3136.30 → 955.52 MiB |
| Matrix plane residual | 2M | 3.298 s | 0.092 s | 35.70× | 33.70 → 42.90 MiB |
| Matrix plane residual | 5M | 7.595 s | 0.265 s | 28.63× | 101.33 → 109.92 MiB |
| Cold LOD 30k | 5M | 2.743 s | 3.131 s | 0.88× | 402.89 → 347.32 MiB |
| Empty transform | 5M | 20.9 ms | 0.014 ms | 1484× | 114.47 → 0.02 MiB |

The complete table covers 100k, 500k, 1M, 2M and 5M cases. The matrix plane-residual mask, topology neighbours, LOD indices and metrology definitions are protected by golden tests. The raw benchmark log is retained in `benchmark_results.csv`.

The 5M surface-following case took 131.8 s versus 74.9 s in V4.6.9. It remains correct but regressed because implicit neighbour lookup trades prebuilt Python-object memory for per-visit lookup work. This mode does not use the connected-component shortcut. A compact CSR or optional compiled iteration kernel is the main V4.7.1 candidate; it must pass the existing bit-exact mask tests before adoption.

No GPU, CUDA, CuPy, Numba hard dependency, renderer replacement, or metric approximation was introduced.

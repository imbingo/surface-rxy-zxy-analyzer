# V4.7.0 — Performance Engine Phase 1

V4.7.0 implements the low-risk priorities from the V4.6.9 performance audit. It does not add GPU dependencies, replace Matplotlib, rewrite physical cKDTree topology, or change measurement and ROI definitions.

## Import engine

- Stable comma, tab, semicolon, pipe and whitespace point tables use pandas' C tokenizer.
- Quoted records use the fast path only when every physical line has balanced quotes. Column conflicts, multiline quotes, unsupported delimiters, parser exceptions and disabled policy flags fall back to the V4.6.9 line parser.
- The robust parser remains the compatibility authority for malformed and unusual device files.
- Z Matrix metadata scanning stops after a stable wide numeric body is established. A full, non-sampled matrix reuses numeric rows captured by its integrity prescan, avoiding another body parse.
- Provenance now includes `source_total_rows`, `source_valid_rows`, `analysis_rows`, `final_effective_rows`, and explicit estimate flags.

## Smart ROI

- Matrix 8-neighbour topology is represented by one dense `int32` cell-to-point grid plus point row/column vectors. Missing cells remain `-1` holes and are never compressed or bridged.
- Matrix `plane_residual` with a fixed candidate gate uses `scipy.ndimage.label` with an 8-neighbour structure. Physical XYZ, adaptive kNN, Delaunay, and surface-following retain their existing algorithms.
- The performance policy can disable the matrix fast path and return to the historical edge-list/adjacency implementation.

## Display and analysis

- Large LOD preparation works in bounded chunks and retains raw-Z and plane-residual extrema. A small revision-keyed cache avoids rebuilding the same detail LOD during redraw, zoom and pan.
- An empty transform pipeline returns shared NumPy arrays instead of three full copies.
- Order-2 and order-3 diagnostic surfaces are computed only when selected, in the existing cancellable background worker. An analysis revision guard discards late results.

## Configuration and diagnostics

`PerformancePolicy` centralizes the parser, matrix-component and LOD-cache switches plus a conservative thread budget. Set `SURFACE_PERF_DEBUG=1` before launch to emit structured `PERF` timing records. Debug logging is off by default.

## Compatibility evidence

- Full suite: 209 tests and 23 subtests passed.
- New golden tests compare implicit matrix neighbours with the legacy graph, matrix connected-component masks with the legacy BFS, chunked LOD indices with the reference algorithm, parser numerical/NaN results across fast and robust paths, cancellation, and zero-copy transforms.
- Before/after measurements are in [`benchmarks/audit_outputs/v470/before_after_results.csv`](../benchmarks/audit_outputs/v470/before_after_results.csv) and [`performance_summary.md`](../benchmarks/audit_outputs/v470/performance_summary.md).

## Known limits

- Cold chunked LOD uses less temporary memory but is slightly slower in the 5M fixture; the cache provides the redraw improvement.
- Surface-following retains its dynamic local-plane semantics and remains the largest matrix Smart ROI CPU cost. Its implicit-neighbour access is slower than the historical prebuilt Python adjacency arrays in the benchmark; Phase 2 should evaluate compact CSR or an optional compiled kernel against golden masks.
- Zygo keeps its dedicated robust reader and therefore does not receive the generic C-parser speedup.

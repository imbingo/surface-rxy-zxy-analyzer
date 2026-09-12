# Changelog

## V4.6.5

- Added full-input physical XY raster rendering with density and NoData views.
- Added debounced viewport regeneration, bounded display cache and stale-result guards.
- Replaced file-order detail sampling with spatial LOD and forced PV/TTV extrema retention.
- Kept Smart ROI seeds on real physical source points, independent of display pixels.
- Added persistent source/analysis/final/display provenance, with unknown counts distinguished from zero.
- Added Raster/LOD reports, compatible Recipe display options and responsive narrow-window results.
- Preserved measurement and ROI definitions; no display interpolation or topology fabrication.

## V4.6.4

- Added N-point adjustment/shim calculation to Parallelism Analysis.
- Added 3-point and 4-corner support templates and arbitrary support coordinates.
- Added exact fitted-plane correction and reference-origin ΔZ definition.
- Added base shim thickness, negative shim removal, quantization and predicted residual pose.
- Added N-point support coplanarity analysis and overconstraint warnings.
- Added adjustment fixture configuration to Recipe schema 8 and CSV/text export.
- Added regression coverage for signs, origin, quantization, geometry and stale results.
- Fixed 3D selection marker occlusion without changing measurement coordinates.

Earlier versions: see the versioned RELEASE_NOTES files and README history.

# Changelog

## V4.6.7

- Highlighted Rx/Ry metric cards with the same accent as PV.
- Added structured window-modal import progress with per-stage percentage rings, elapsed time and cancellation.
- Added a searchable local Recipe library with favorites, recent usage, batch import, double-click loading and atomic saves/exports.
- Kept Recipe schema 8 and retained existing measurement definitions.

## V4.6.6

- Added Smart ROI topology stage/count/elapsed-time progress and cooperative cancellation between processing batches.
- Added contextual clear-all-ROI and undo-last-deletion actions to the 2D plot menu.
- Added title-bar focus/restore controls for each 3D, XY, XZ and YZ plot card.
- Kept the left controls available while a focused plot fills the right workspace.
- Preserved double-click view reset and added Escape-to-restore behavior.
- Made XY Raster resolution fully automatic while retaining Recipe schema 8 compatibility.

## V4.6.5

- Added full-input physical XY height raster rendering with transparent NoData regions.
- Added automatic zoom-to-scatter display with fixed height colors and screen-space markers.
- Moved import status to the bottom bar and XY resolution to the context menu.
- Restored font-independent pose arrow icons and prevented ROI outlines from expanding data axes.
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

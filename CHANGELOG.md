# V5.0.0 更新版 2（v5.0.0-r2）— 数据预览、派生列与 ROI 色标

- 参数区默认宽度随窗口调整（1366/1600/1920 对应约 320/360/400 逻辑像素），记忆手动宽度并支持收起恢复。
- 新增自动 / 全局统一色标：ROI 下 XY 保留全局总览，3D/XZ/YZ 使用最终有效 ROI 的局部色标，报告同步显示两根色条。
- 手动删点 / 恢复后重新统计色标范围，显示降采样和视口缩放不参与范围计算；拒绝过期异步 XY 结果。

- 数据页将导入策略移至数据文件上方，保留当前 V5.0.0 产品版本。
- 新增 Excel 式右键插列、点选公式、相对 / 绝对引用、填充柄和 Ctrl+D，支持撤销 / 重做及 schema 11 Recipe。
- 解析器移至导入详情；Physical XYZ 不再显示无关矩阵 Pitch；矩阵 Auto 缺少可靠 X/Y 校准时要求明确补充。

- 单层数据页新增分页数据预览，标记分析列、派生依赖列及最终参与状态。
- 文本源文件采用有界、可取消的后台读取；矩阵 / Zygo 保留专用解析及网格位置预览。
- 新增安全四则运算派生列，可从 Up / Down 生成 Z；支持明确单位、Recipe schema 10 和批量重放。
- 公式及数据来源写入导出追溯信息；保留现有量测、滤波与 ROI 定义。
- 单层图标更新为曲面网格，膜厚与平行度图标保持不变。

# V5.0.0 — 统一分析工作台

## 界面与操作

- 新增独立模块导航栏和可收起参数区。
- 单层提供数据、预处理、ROI 参数页，批量处理及工作区重置位于数据页。
- 膜厚提供数据层、配准、计算与导出页；平行度提供数据面、计算与导出、装调页，并保留基准面和测量面两个绘图区。
- 整理字体、图标、工具栏、Recipe 和按当前模块选择的导出菜单。
- ROI 列表使用启用开关，文件名单行省略、悬停显示完整名称。

## 导入与绘图

- 导入策略弹窗拆分为数据布局、分析抽样、显示、高级解析四个页面。
- 点查询提示不参与绘图布局，减少重复重绘，防止提示出现时视图跳动。
- XY 右侧增加一根共享色条，范围采用全部未删除点的当前显示 Z；四视图和报告使用相同的 turbo 色标。
- ROI 切换和缩放保持色标范围；导入、滤波、删除/恢复点及显示面型变化后更新。
- 平坦面色条标注实际高度；散点采用不透明颜色，3D 关闭点的深度着色。
- 延续现有 Rx/Ry/PV/TTV/RMS/Mean Z 计算及 ROI 集合规则。


# V4.7.10

- 修复高 DPI 下 3D 自动适配混用逻辑和物理像素导致坐标框过小。
- 滚轮同步缩放坐标框、刻度、参考平面与曲面，保持数据范围。
- 3D 卡片空白处支持滚轮，双击复位重新适配当前画布。

# Changelog

## V4.7.9 — Larger 3D home view

- Fit against the final Axes3D transform and maximize the scene within the canvas with rendered label checks.
- Prevent the full-width 3D surface from being clipped to the internal square axes rectangle.
- Keep first draw, resize, focused 3D and Home fitting consistent without changing physical ranges or measurements.
- Remove duplicated import details below column mapping; preserve bottom status and import settings details.

## V4.7.8 — Single-pass matrix import and explicit coordinate provenance

- Combined matrix validation, source counts and bounded stride/grid sampling into one numeric scan.
- Added strict fast numeric-row conversion with compatible fallback for quoted/missing fields.
- Fixed sparse/wide matrix coverage by sizing nested stride lattices from matrix positions; preserve grid extrema during bounded merges.
- Show actual original X/Y pixel pitch, source and sampled spacing in the mapping panel, status and reports.
- Keep ambiguous XYZ tables pending for explicit distinct columns and per-axis units, including headerless three-column tables.
- Fixed asynchronous pending-mapping imports incorrectly restoring the previous dataset.
- Added regression tests and a reproducible 3999-row, approximately 600MB CSV comparison benchmark.

## V4.7.7 — Optional format extension API

- Added versioned, independently installed file-reader extensions with manifest discovery, compatibility checks, enable/disable management and conflict detection.
- Added standard millimetre height-matrix / XYZ contracts, cancellation and source/extension provenance at the existing transactional import boundary.
- Added Sensofar PLUX extension 1.0.0 for the known single-height-layer ZIP/XML/float32 layout, preserving invalid pixels and matrix topology.
- Kept vendor readers optional and outside the main installer; added a separate per-user PLUX installer and extension developer documentation.
- Added synthetic format, units, hole-mask, import-cap, cancellation and compatibility tests. Real-instrument PLUX comparison remains pending.

## V4.7.6 — CSV fast-parser column-integrity hotfix

- Fixed silent one-column shifts in CSV and delimited point-table imports.
- Added source-token to DataFrame column-integrity validation for the pandas fast parser.
- Added automatic fallback to the robust line parser when fast-path field alignment is ambiguous.
- Hardened trailing-delimiter handling without changing Excel import behavior.
- Added regression tests for wide 12-column XYZ point tables, quoted rows, mixed trailing delimiters and parser equivalence.
- Remembered the most recently selected import and export directories across all file dialogs.

## V4.7.5 — User-authoritative import and projection coordinate hover

- Made the selected generic XYZ, Pixel XY or Z Matrix parser authoritative instead of blocking imports with ambiguous format inference.
- Kept explicit Zygo and Precitec signatures protected by their vendor-aware import paths.
- Changed the initial and reset 3D camera to the symmetric X/Y 45-degree view with positive Z upward.
- Added source-point XYZ hover readouts to XY, XZ and YZ projections, including displayed and original Z when processing changes the shown surface.
- Made coordinate inspection opt-in with Ctrl to avoid continuous nearest-point searches during normal mouse movement.
- Added 3D surface padding and expanded the 3D axes to the full card so material corners remain visible without shrinking the surface.
- Replaced the fixed 3D zoom with camera- and canvas-aware fitting so the largest complete surface is shown after resize or focus changes.
- Added final screen-pixel validation for the default 3D home view so all padded bounding corners must be inside the safe canvas area before display.

## V4.7.4 — Wide XYZ import and 3D view improvements

- Fixed wide Physical XYZ point tables being mistaken for Z Matrix data when coordinate columns are not the first three columns.
- Made bounded preflight inspect the actual semantic X/Y/Z header mapping, including unit-bearing and commented headers after vendor metadata.
- Enlarged the main 3D scene to use the plot card more fully and visually match the projection panels.
- Set the initial and reset 3D camera to a clear 45-degree engineering view.

## V4.7.3 — Mask-aware spatial Gaussian low-pass

- Added a physical-scale spatial Gaussian low-pass mode for regular matrices and irregular XYZ point clouds.
- Preserved point count and source holes; the filter never creates replacement points inside missing regions.
- Added normalized valid-mask convolution for matrix data and radius-limited Gaussian neighborhoods for point clouds.
- Displayed filtered Z in 3D/XZ/YZ views and retained raw Z plus removed high-frequency residuals in CSV exports.
- Added Recipe, batch, CLI, API and report traceability for Gaussian sigma, support radius and RMS change.
- Fixed Smart ROI report rendering so retained surface points remain height-coloured and only excluded overview regions are grey.
- Kept the existing MAD, local median and iterative sigma-clipping modes compatible and unchanged.

## V4.7.2 — Unified import hard cap and Smart topology domain fix

- Unified the analysis import hard cap across all supported readers.
- Fixed cases where files below the MB threshold could exceed the configured import limit.
- Unified source / analysis / final / display count semantics.
- Fixed Smart ROI topology to exclude manually deleted points before graph construction.
- Restricted the Smart topology domain by active manual ROI.
- Prevented deleted or excluded points from acting as connectivity bridge nodes.
- Added regression tests for multi-cluster deletion, broken bridges, and per-reader import caps.

## V4.7.1 — Import stability and sigma residual filtering

- Fixed UI freeze / long hang when an incorrect import format is selected.
- Added import preflight validation and fail-fast parsing.
- Extended cancellation support across all import stages.
- Preserved the previous dataset when a new import fails.
- Added selectable first-, second-, third-order and follow-detrend residual models for iterative sigma clipping.
- Preserved all existing metrology definitions after filtering.
- Added high-order fit stability checks and Recipe traceability.

## V4.7.0 — Performance Engine Phase 1

- Added format-aware C-parser paths with automatic robust fallback.
- Reduced repeated Z Matrix and Keyence body scans.
- Added compact implicit matrix topology and an equivalent connected-component path for matrix plane-residual Smart ROI.
- Added revision-keyed LOD caching, bounded-memory chunked LOD, and zero-copy empty transforms.
- Moved order-2/order-3 diagnostics to on-demand background work with late-result guards.
- Added `PerformancePolicy`, `SURFACE_PERF_DEBUG`, golden parity tests, and isolated before/after benchmarks.
- Preserved all metrology formulas, ROI Boolean semantics, missing-cell holes, and critical display extrema.

## V4.6.8

- Fixed selection/cancel flicker by updating foreground selection aids without resetting the XY source, cache or base image.
- Focused XY surface view now uses 1.08-pitch detail bins rather than the overview's 1.25-pitch footprint; still bounded by pixel budget and never fills empty cells.

- Added Surface / Source points XY modes in the existing card title row.
- Replaced pixel-only surface binning with source-anchored, estimated-pitch-aware bins to avoid washout when zooming.
- Bounded source-point mode to 50,000 spatially selected real points with extrema preserved; zoom restores local full detail.
- Removed density/missing display modes; legacy Recipe values migrate to surface view. Reports honor the selected mode.
- Retained viewport, global color scale, selection and measurement definitions across mode changes.

## V4.6.7

- Show Smart ROI growth directly as a gray overlay on the existing XY plot, with compact inline progress/cancel controls and cached-background updates.
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

- 顶栏新增左侧参数区收起/展开按钮；保留参数和当前页签，释放绘图宽度。

- XY 与 3D/XZ/YZ 共用分析点高度色标，避免全量背景中的被排除极端值压平 XY 色差；背景点仍保留。

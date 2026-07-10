"""Stable, headless integration API for Python and C# process callers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .file_io import LoadedPoints, load_xyz_points
from .mixins.analysis import AnalysisMixin
from .version import APP_VERSION


FILTER_MODES = {
    "off": 0,
    "mad": 1,
    "local_median": 2,
    "sigma_clip": 3,
}
UNIT_SCALES = {"mm": 1.0, "um": 1e-3, "µm": 1e-3, "nm": 1e-6}


@dataclass(frozen=True)
class AnalysisOptions:
    """Headless analysis settings. All calculations are normalized to millimetres."""

    x_unit: str = "mm"
    y_unit: str = "mm"
    z_unit: str = "mm"
    transform_pipeline: tuple[str, ...] = ()
    filter_mode: str = "off"
    neighbor_k: int = 12
    threshold_um: float = 5.0
    sigma_k: float = 3.0
    sigma_iterations: int = 5


@dataclass
class AnalysisResult:
    version: str
    source: str
    input_points: int
    finite_points: int
    roi_points: int
    fitted_points: int
    filtered_points: int
    sampled: bool
    import_strategy: str
    transform_pipeline: list[str]
    filter: dict[str, Any]
    metrics: dict[str, float]
    warnings: list[str] = field(default_factory=list)
    centroid: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _unit_scale(unit: str) -> float:
    key = str(unit).strip().replace("μ", "µ")
    if key not in UNIT_SCALES:
        raise ValueError(f"不支持的单位: {unit!r}；支持 mm/µm/um/nm")
    return UNIT_SCALES[key]


def _as_vector(values: Sequence[float] | np.ndarray, name: str) -> np.ndarray:
    vector = np.asarray(values, dtype=float).reshape(-1)
    if vector.ndim != 1:
        raise ValueError(f"{name} 必须是一维数值序列")
    return vector


def analyze_xyz(
    x: Sequence[float] | np.ndarray,
    y: Sequence[float] | np.ndarray,
    z: Sequence[float] | np.ndarray,
    *,
    options: AnalysisOptions | None = None,
    roi_mask: Sequence[bool] | np.ndarray | None = None,
    source: str = "memory",
    sampled: bool = False,
    import_strategy: str = "Python数组",
) -> AnalysisResult:
    """Fit one XYZ surface and return the same core metrics used by the GUI."""

    options = options or AnalysisOptions()
    xa = _as_vector(x, "x")
    ya = _as_vector(y, "y")
    za = _as_vector(z, "z")
    if not (len(xa) == len(ya) == len(za)):
        raise ValueError("X/Y/Z 点数不一致")
    if len(xa) < 3:
        raise ValueError("输入点数少于 3，无法拟合平面")

    xa = xa * _unit_scale(options.x_unit)
    ya = ya * _unit_scale(options.y_unit)
    za = za * _unit_scale(options.z_unit)
    input_points = len(za)

    finite = np.isfinite(xa) & np.isfinite(ya) & np.isfinite(za)
    xa, ya, za = xa[finite], ya[finite], za[finite]
    finite_points = len(za)
    if finite_points < 3:
        raise ValueError("去除无效值后少于 3 点，无法拟合平面")

    if roi_mask is None:
        roi = np.ones(finite_points, dtype=bool)
    else:
        raw_roi = np.asarray(roi_mask, dtype=bool).reshape(-1)
        if len(raw_roi) != input_points:
            raise ValueError("roi_mask 长度必须与原始 X/Y/Z 点数一致")
        roi = raw_roi[finite]
    if int(roi.sum()) < 3:
        raise ValueError("ROI 内有效点少于 3，无法拟合平面")

    pipeline = list(options.transform_pipeline)
    xa, ya, za = AnalysisMixin._apply_transform_pipeline(xa, ya, za, pipeline)
    xb, yb, zb = xa[roi], ya[roi], za[roi]

    mode_key = str(options.filter_mode).strip().lower()
    if mode_key not in FILTER_MODES:
        raise ValueError(f"不支持的滤波模式: {options.filter_mode!r}")
    keep = AnalysisMixin.filter_keep_mask(
        xb,
        yb,
        zb,
        FILTER_MODES[mode_key],
        k=max(3, int(options.neighbor_k)),
        threshold_mm=float(options.threshold_um) / 1000.0,
        sigma_k=float(options.sigma_k),
        sigma_iters=max(1, int(options.sigma_iterations)),
    )
    if int(keep.sum()) < 3:
        raise ValueError("滤波后有效点少于 3，无法拟合平面")

    fx, fy, fz = xb[keep], yb[keep], zb[keep]
    metrics = AnalysisMixin.compute_plane_metrics(fx, fy, fz)
    clean_metrics = {key: float(value) for key, value in metrics.items() if key != "coeffs"}
    warnings: list[str] = []
    if sampled:
        warnings.append("结果基于降采样数据；PV/TTV 极值精度取决于抽样策略。")

    return AnalysisResult(
        version=APP_VERSION,
        source=source,
        input_points=input_points,
        finite_points=finite_points,
        roi_points=int(roi.sum()),
        fitted_points=len(fz),
        filtered_points=int(len(zb) - len(fz)),
        sampled=bool(sampled),
        import_strategy=import_strategy,
        transform_pipeline=pipeline,
        filter={
            "mode": mode_key,
            "neighbor_k": int(options.neighbor_k),
            "threshold_um": float(options.threshold_um),
            "sigma_k": float(options.sigma_k),
            "sigma_iterations": int(options.sigma_iterations),
        },
        metrics=clean_metrics,
        warnings=warnings,
        centroid={"x_mm": float(np.mean(fx)), "y_mm": float(np.mean(fy))},
    )


def analyze_file(
    path: str | Path,
    *,
    options: AnalysisOptions | None = None,
    x_column: int | str = 0,
    y_column: int | str = 1,
    z_column: int | str = 2,
    max_points: int = 100_000,
) -> AnalysisResult:
    """Read a conventional XYZ text/Excel file and analyze it without opening Qt."""

    loaded: LoadedPoints = load_xyz_points(
        path,
        x_column=x_column,
        y_column=y_column,
        z_column=z_column,
        max_points=max_points,
    )
    return analyze_xyz(
        loaded.x,
        loaded.y,
        loaded.z,
        options=options,
        source=Path(path).name,
        sampled=loaded.sampled,
        import_strategy=loaded.strategy,
    )


def compare_plane_results(base: AnalysisResult, measure: AnalysisResult) -> dict[str, float]:
    """Calculate parallelism deltas using the GUI's sign and reference-point rules."""

    b = base.metrics
    m = measure.metrics
    ref_x = (base.centroid["x_mm"] + measure.centroid["x_mm"]) / 2.0
    ref_y = (base.centroid["y_mm"] + measure.centroid["y_mm"]) / 2.0
    z_base = b["a"] * ref_x + b["b"] * ref_y + b["c"]
    z_measure = m["a"] * ref_x + m["b"] * ref_y + m["c"]
    drx = m["rx"] - b["rx"]
    dry = m["ry"] - b["ry"]
    return {
        "delta_rx_urad": float(drx),
        "delta_ry_urad": float(dry),
        "parallelism_urad": float(np.hypot(drx, dry)),
        "step_height_mm": float(z_measure - z_base),
        "reference_x_mm": float(ref_x),
        "reference_y_mm": float(ref_y),
    }

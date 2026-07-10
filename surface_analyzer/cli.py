"""Command-line entry point for GUI startup and headless integration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api import AnalysisOptions, analyze_file
from .version import APP_VERSION, SOURCE_BASE_VERSION, SOURCE_COMMIT


EXIT_OK = 0
EXIT_ARGUMENT = 2
EXIT_INPUT = 10
EXIT_ANALYSIS = 20
EXIT_OUTPUT = 30


def _column(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="surface-analyzer", description="面型及Rxy分析工具 V4.0")
    parser.add_argument("--check", action="store_true", help="检查依赖和模块导入后退出")
    parser.add_argument("--input", help="启动 GUI 后自动载入文件，或作为无界面分析输入")
    parser.add_argument("--headless", action="store_true", help="不打开 GUI，输出 JSON 结果")
    parser.add_argument("--output-json", help="无界面分析 JSON 输出路径；省略时写到标准输出")
    parser.add_argument("--x-column", type=_column, default=0, help="X 列名或 0 基列索引")
    parser.add_argument("--y-column", type=_column, default=1, help="Y 列名或 0 基列索引")
    parser.add_argument("--z-column", type=_column, default=2, help="Z 列名或 0 基列索引")
    parser.add_argument("--x-unit", default="mm", choices=("mm", "um", "µm", "nm"))
    parser.add_argument("--y-unit", default="mm", choices=("mm", "um", "µm", "nm"))
    parser.add_argument("--z-unit", default="mm", choices=("mm", "um", "µm", "nm"))
    parser.add_argument("--filter", dest="filter_mode", default="off",
                        choices=("off", "mad", "local_median", "sigma_clip"))
    parser.add_argument("--neighbor-k", type=int, default=12)
    parser.add_argument("--threshold-um", type=float, default=5.0)
    parser.add_argument("--sigma-k", type=float, default=3.0)
    parser.add_argument("--sigma-iterations", type=int, default=5)
    parser.add_argument("--max-points", type=int, default=100_000)
    parser.add_argument("--transform", action="append", default=[],
                        choices=("CW90", "CCW90", "ROT180", "SWAP", "FLIPX", "FLIPY", "ORIGIN(0,0)"))
    return parser


def _run_headless(args: argparse.Namespace) -> int:
    if not args.input:
        print("[error] --headless 需要 --input", file=sys.stderr)
        return EXIT_ARGUMENT
    options = AnalysisOptions(
        x_unit=args.x_unit,
        y_unit=args.y_unit,
        z_unit=args.z_unit,
        transform_pipeline=tuple(args.transform),
        filter_mode=args.filter_mode,
        neighbor_k=args.neighbor_k,
        threshold_um=args.threshold_um,
        sigma_k=args.sigma_k,
        sigma_iterations=args.sigma_iterations,
    )
    try:
        result = analyze_file(
            args.input,
            options=options,
            x_column=args.x_column,
            y_column=args.y_column,
            z_column=args.z_column,
            max_points=args.max_points,
        )
    except (FileNotFoundError, ValueError, UnicodeError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_INPUT
    except Exception as exc:
        print(f"[error] 分析失败: {exc}", file=sys.stderr)
        return EXIT_ANALYSIS

    payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    if not args.output_json:
        print(payload)
        return EXIT_OK
    try:
        output = Path(args.output_json).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[error] JSON 写入失败: {exc}", file=sys.stderr)
        return EXIT_OUTPUT
    print(f"[ok] JSON: {output}")
    return EXIT_OK


def _run_gui(input_path: str | None) -> int:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from .app import SurfaceAnalyzerPro

    app = QApplication(sys.argv)
    window = SurfaceAnalyzerPro()
    window.show()
    if input_path:
        QTimer.singleShot(0, lambda: window.load_path(input_path))
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        from .app import SurfaceAnalyzerPro  # noqa: F401 - import is the dependency check
        print(f"[ok] {APP_VERSION} modules imported")
        print(f"[ok] source base {SOURCE_BASE_VERSION} ({SOURCE_COMMIT})")
        return EXIT_OK
    if args.headless:
        return _run_headless(args)
    return _run_gui(args.input)

"""Command-line entry point for GUI startup and headless integration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
    parser = argparse.ArgumentParser(
        prog="surface-analyzer", description=f"面型及Rxy分析工具 {APP_VERSION}")
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
    from .api import AnalysisOptions, analyze_file

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
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
    from PyQt6.QtWidgets import QApplication, QMessageBox, QSplashScreen

    app = QApplication(sys.argv)
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    icon_path = resource_root / "assets" / "SurfaceRxyZxyAnalyzer.png"
    icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    splash_pixmap = QPixmap(560, 330)
    splash_pixmap.fill(QColor("#f7f9fb"))
    painter = QPainter(splash_pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if icon_path.exists():
        logo = QPixmap(str(icon_path)).scaled(
            196, 196, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap((560 - logo.width()) // 2, 28, logo)
    painter.setPen(QColor("#263746"))
    painter.setFont(QFont("Microsoft YaHei UI", 15, QFont.Weight.DemiBold))
    painter.drawText(0, 230, 560, 32, Qt.AlignmentFlag.AlignCenter, "面型及 Rxy 分析工具")
    painter.setPen(QColor("#657786"))
    painter.setFont(QFont("Microsoft YaHei UI", 9))
    painter.drawText(0, 264, 560, 24, Qt.AlignmentFlag.AlignCenter, APP_VERSION)
    painter.end()

    splash = QSplashScreen(splash_pixmap)
    splash.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    splash.show()

    def stage(message: str) -> None:
        splash.showMessage(
            message, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
            QColor("#526575"))
        app.processEvents()

    try:
        stage("正在加载分析模块...")
        from .app import SurfaceAnalyzerPro

        stage("正在初始化绘图与分析工作区...")
        window = SurfaceAnalyzerPro()
        stage("正在恢复导入策略与界面设置...")
        window.show()
        if input_path:
            QTimer.singleShot(0, lambda: window.load_path(input_path))
        splash.finish(window)
        window.statusBar().showMessage(f"{APP_VERSION} 已就绪", 4000)
        return app.exec()
    except Exception as exc:
        splash.close()
        log_dir = Path.home() / "AppData" / "Local" / "SurfaceRxyZxyAnalyzer" / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "startup_error.log").write_text(
                f"{APP_VERSION}\n{type(exc).__name__}: {exc}\n", encoding="utf-8")
        except OSError:
            pass
        QMessageBox.critical(None, "启动失败", f"软件启动失败：\n{exc}\n\n错误日志：{log_dir}")
        return EXIT_ANALYSIS


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

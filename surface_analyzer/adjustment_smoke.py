"""Packaged workflow check using synthetic data and isolated user settings."""
import json
import os
import tempfile
from pathlib import Path


def run(output=None):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
    import numpy as np
    from .app import SurfaceAnalyzerPro
    from .version import APP_VERSION, SOURCE_COMMIT

    with tempfile.TemporaryDirectory(prefix='surface-adjustment-check-') as directory:
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, directory)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, directory)
        app = QApplication.instance() or QApplication([])
        window = SurfaceAnalyzerPro()
        try:
            x = np.array([-10., 10., -10., 10.])
            y = np.array([-10., -10., 10., 10.])
            def record(a, b, c):
                z = a*x + b*y + c
                return dict(x=x, y=y, z=z, metrics=window.compute_plane_metrics(x,y,z),
                            name='synthetic check', n=4, pipeline='原始状态', import_strategy='full')
            window.parallel_base = record(.0001, -.0002, .45)
            window.parallel_measure = record(-.0002, .0001, .46)
            window._update_parallel_ui()
            window._apply_parallel_result(window._compute_parallel_result())
            panel = window.adjustment_panel
            panel.template(4)
            panel.quant.setCurrentIndex(1)
            panel.supports.item(3, 6).setText('7')
            panel.calculate()
            if panel.result is None or panel.results.rowCount() != 4:
                raise RuntimeError('Packaged adjustment calculation failed')
            result = panel.result
            if 'RecommendedShim_um' not in panel.csv_text():
                raise RuntimeError('Packaged CSV generation failed')
            recipe = window._current_recipe_dict()
            if recipe['schema_version'] != 9 or 'support_results' in recipe['adjustment_config']:
                raise RuntimeError('Recipe fixture contract failed')
            window.tabs.setCurrentIndex(2)
            window.parallel_pages.setCurrentIndex(1)
            window.resize(1600, 900)
            window.show()
            app.processEvents()
            if output:
                window.grab().save(str(Path(output).with_suffix('.png')))
            window.swap_parallel_surfaces()
            if panel.result is not None or panel.export_button.isEnabled():
                raise RuntimeError('Stale adjustment export remained enabled')
            payload = dict(version=APP_VERSION, source_commit=SOURCE_COMMIT,
                           supports=4, coplanarity_pv_um=result.coplanarity.pv_um,
                           recipe_schema=8, calculation=True, csv=True,
                           rendered=True, swap_invalidation=True)
            if output:
                Path(output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
            return 0
        finally:
            window.close()
            app.processEvents()

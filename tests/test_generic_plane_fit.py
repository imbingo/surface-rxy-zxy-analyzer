"""Orthogonal fitting and the real main-window point selection workflow."""

import importlib
import json
import os
from pathlib import Path
import shutil
import time
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from matplotlib.backend_bases import MouseEvent
import numpy as np
import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QPushButton

from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.mixins.analysis import AnalysisMixin
from surface_analyzer.module_extensions import ModuleExtensionRegistry


EXTENSION = Path(__file__).parents[1] / 'extensions' / 'generic-plane-fit'
fit_plane = importlib.import_module('extensions.generic-plane-fit.engine').fit_plane
angle_difference = importlib.import_module('extensions.generic-plane-fit.engine').angle_difference


def _grid(a, b):
    return np.array(np.meshgrid(a, b)).reshape(2, -1)


def _planes():
    y, z = _grid(np.linspace(-3, 3, 13), np.linspace(-5, -2, 9))
    near = np.column_stack([0.012 * (z + 3.5) + 0.15 * y, y, z])
    y, z = _grid(np.linspace(-3, 3, 11), np.linspace(1, 5, 12))
    vertical = np.column_stack([np.zeros(len(y)), y, z])
    return near, vertical


def _load_points(path, xyz):
    # Mixed physical units exercise the real column mapping.
    np.savetxt(path, xyz * [1, 1, 1000], fmt='%.12g', delimiter=',',
               header='X (mm),Y (mm),Z (um)', comments='')


def _wait_for_work(qt, window):
    deadline = time.monotonic() + 12
    while window._task_thread is not None and time.monotonic() < deadline:
        qt.processEvents()
        time.sleep(.01)
    assert window._task_thread is None, window.statusBar().currentMessage()
    qt.processEvents()


def _select(window, view, start, end):
    window.clear_rois()
    window.cb_roi_shape.setCurrentIndex(0)
    window.btn_roi_mouse.click()
    window.on_select(SimpleNamespace(xdata=start[0], ydata=start[1]),
                     SimpleNamespace(xdata=end[0], ydata=end[1]), view)


def _write(window, slot):
    button = next(item for item in window.tabs.currentWidget().findChildren(QPushButton)
                  if item.text() == '写入当前数据'
                  and item.parentWidget().title() == ('基准面 A', '测量面 B')[slot])
    button.click()


def test_horizontal_and_sloped_angles_match_legacy_sign():
    x, y = _grid(np.linspace(-2, 2, 9), np.linspace(-3, 3, 13))
    for a, b in ((0, 0), (.24, -.4), (-1.2, .2)):
        z = a * x + b * y + 7
        points = np.column_stack([x, y, z])
        fit = fit_plane(points)
        normal = np.array([-a, -b, 1.])
        np.testing.assert_allclose(abs(np.dot(fit.normal, normal) / np.linalg.norm(normal)), 1)
        legacy = AnalysisMixin.compute_plane_metrics(x, y, z)
        assert fit.rx_deg == pytest.approx(np.degrees(legacy['rx'] / 1e6))
        assert fit.ry_deg == pytest.approx(np.degrees(legacy['ry'] / 1e6))
        np.testing.assert_allclose(points @ fit.normal + fit.offset_mm, 0, atol=1e-12)


def test_steep_and_vertical_sign_projection_and_delta():
    near, vertical = _planes()
    a, b = map(fit_plane, (near, vertical))
    assert a.ry_deg < -89 and a.rx_deg < -80
    np.testing.assert_array_equal(b.normal, [1, 0, 0])
    assert b.rx_deg is None
    assert b.ry_deg == -90
    assert angle_difference(b.rx_deg, a.rx_deg) is None
    assert angle_difference(b.ry_deg, a.ry_deg) == pytest.approx(-.687516, abs=1e-6)
    assert angle_difference(a.ry_deg, b.ry_deg) == pytest.approx(.687516, abs=1e-6)
    assert angle_difference(-89, 89) == 2
    assert angle_difference(89, -89) == -2
    assert angle_difference(-90, 0) == -90
    assert angle_difference(np.nextafter(-90., -np.inf), 0) == -90
    assert 'Rx 不可定义' in b.warnings[0]
    other_vertical = fit_plane(vertical[:, [1, 0, 2]])
    assert other_vertical.rx_deg == -90
    assert other_vertical.ry_deg is None
    nearly_singular = vertical.copy()
    nearly_singular[:, 0] = 1e-5 * vertical[:, 2]
    fit = fit_plane(nearly_singular)
    assert fit.rx_deg is not None
    assert any('对测点噪声敏感' in warning for warning in fit.warnings)
    for scale in (-1e-9, 0, 1e-9):
        points = vertical.copy()
        points[:, 0] = scale * points[:, 2]
        assert fit_plane(points).normal[0] > .999999


def test_base_frame_projection_and_degenerate_reference():
    compare = importlib.import_module('extensions.generic-plane-fit.engine').compare_in_base_frame
    base = fit_plane([[5, 0, 0], [5, 2, 0], [5, 0, 3]])
    measure = fit_plane([[5, 0, 0], [4, 2, 0], [4, 0, 3]])
    local = compare(base, measure, None)
    assert local['resolved_reference_axis'] == 'Y'
    basis = local['basis_rows_in_source']
    np.testing.assert_allclose(basis @ basis.T, np.eye(3))
    assert np.linalg.det(basis) == pytest.approx(1)
    assert local['base_rx_ry_deg'] == (0, 0)
    assert local['delta_rx_ry_deg'] == pytest.approx((-np.degrees(np.arctan(1/3)),
                                                     np.degrees(np.arctan(1/2))))
    with pytest.raises(ValueError, match='投影过短'):
        compare(base, measure, 0)
    perpendicular = fit_plane([[0, 0, 0], [2, 0, 0], [0, 0, 3]])
    assert compare(base, perpendicular, None)['delta_rx_ry_deg'] == (None, -90)


def test_noise_repeated_measurements_and_order_invariance():
    a, _ = _planes()
    rng = np.random.default_rng(142)
    a += rng.normal(0, .015, a.shape)
    a += [3e5, 5e5, -4e5]
    points = np.vstack([a, a[:7]])
    order = rng.permutation(len(points))
    fit = fit_plane(points)
    shuffled = fit_plane(points[order])
    np.testing.assert_array_equal(fit.normal, shuffled.normal)
    np.testing.assert_array_equal(fit.signed_distances_mm[order], shuffled.signed_distances_mm)
    reference = np.linalg.svd(points - points.mean(axis=0), full_matrices=False)
    assert fit.rms_mm == pytest.approx(reference.S[-1] / np.sqrt(len(points)))
    assert fit.max_abs_mm == np.max(np.abs(fit.signed_distances_mm))
    assert fit.unique_points == len(a)
    assert '完全重复' in fit.warnings[0]


@pytest.mark.parametrize(('points', 'message'), [
    ([[0, 0, 0], [1, 0, 0]], '至少 3'),
    ([[0, 0, 0], [1, 0, 0], [0, 1, np.nan]], '非有限'),
    ([[0, 0, 0], [1, 0, 0], [0, 1, np.inf]], '非有限'),
    ([[0, 0, 0], [1, 0, 0], [0, 0, 0]], '不同的点'),
    ([[0, 0, 0], [1, 1, 1], [2, 2, 2]], '近共线'),
    ([[0, 0, 0], [1, 1, 1], [2, 2, 2.00000001]], '近共线'),
    (np.vstack([np.eye(3), -np.eye(3)]), '奇异值'),
])
def test_bad_clouds_fail_explainably(points, message):
    with pytest.raises(ValueError, match=message):
        fit_plane(points)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    local = tmp_path / 'local'
    root = local / 'SurfaceRxyZxyAnalyzer' / 'module_extensions'
    root.mkdir(parents=True)
    shutil.copytree(EXTENSION, root / EXTENSION.name)
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    window = SurfaceAnalyzerPro(settings=QSettings(str(tmp_path / 'settings.ini'),
                                                   QSettings.Format.IniFormat))
    notices = []
    window._workflow_notice = lambda *args: notices.append(args)
    yield qt, window, window._extension_modules[0], notices
    _wait_for_work(qt, window)
    window.set_theme_preference('light', persist=False)
    window.close()
    window.deleteLater()
    qt.processEvents()


def test_import_roi_slots_and_export(workspace, tmp_path):
    qt, window, module, notices = workspace
    a, b = _planes()
    points = np.vstack((a, b))
    source = tmp_path / 'two-walls.csv'
    _load_points(source, points)
    window.show()
    qt.processEvents()
    assert window.load_path(source)
    _wait_for_work(qt, window)
    assert notices == []
    assert window._import_dialog is None
    assert window._import_mapping_ready
    assert window._point_selection_ready
    assert len(window.df_raw) == len(points)
    assert len(window.df_raw[['X', 'Y']].drop_duplicates()) < len(points)
    assert len(window.canvas.ax_xz.collections[0].get_offsets()) == len(points)

    _select(window, 'XZ', (-1, -5.2), (1, -1.8))
    np.testing.assert_array_equal(window.active_idx, np.arange(len(a)))
    window.module_buttons[3].click()
    _write(window, 0)
    module.fit_button.click()
    _wait_for_work(qt, window)
    assert module.result['fits'][0] is not None
    assert module.result['fits'][1] is None
    assert module.delta_values[0].text() == '—'
    snapshot_a = module.slots[0]
    np.testing.assert_allclose(snapshot_a['xyz'], a, atol=1e-10)
    assert snapshot_a['input_units'] == dict(x='mm', y='mm', z='µm')

    window.module_buttons[0].click()
    window.clear_rois()
    np.testing.assert_array_equal(window.active_idx, np.arange(len(points)))
    _select(window, 'YZ', (-3.1, .8), (3.1, 5.2))
    np.testing.assert_array_equal(window.active_idx, np.arange(len(a), len(points)))
    assert window.last_metrics is None
    assert '不可用' in window.lbl_eqn.text()
    assert len(window.selectors) == 3
    assert len(window.canvas.ax_yz.collections[0].get_offsets()) == len(b)
    window.module_buttons[3].click()
    _write(window, 1)
    assert module.result is None
    module.fit_button.click()
    _wait_for_work(qt, window)
    result = module.result
    assert result['fits'][1].rx_deg is None
    assert result['deltas'][1] == pytest.approx(-.687516, abs=1e-6)
    assert module.delta_values[0].text() == '不可定义'
    assert module.slots[0]['roi']['shapes'][0]['view'] == 'XZ'
    assert module.slots[1]['roi']['shapes'][0]['view'] == 'YZ'
    np.testing.assert_array_equal(module.slots[1]['reader_index_labels'],
                                  window.df_raw.iloc[window.active_idx].index)

    window.clear_rois()
    window.cb_filter.setCurrentIndex(1)
    module.write_slot(0)
    assert '关闭高度滤波' in module.message.text()
    assert module.result is result
    assert module.slots[0] is snapshot_a

    other = tmp_path / 'replacement.csv'
    _load_points(other, a + [10, 20, 30])
    assert window.load_path(other)
    _wait_for_work(qt, window)
    assert module.result is result
    module.coordinate_frame.setCurrentIndex(1)
    assert module.result is result
    assert all(label.text() not in ('不可定义', '—') for label in module.delta_values)
    automatic = module._comparison(result)
    module.reference_axis.setCurrentIndex(2)
    assert module._comparison(result)['resolved_reference_axis'] == 'Y'
    module.reference_axis.setCurrentIndex(0)
    assert module._comparison(result)['delta_rx_ry_deg'] == automatic['delta_rx_ry_deg']
    module.coordinate_frame.setCurrentIndex(0)
    assert module.delta_values[0].text() == '不可定义'
    module.coordinate_frame.setCurrentIndex(1)
    target = tmp_path / 'analysis.json'
    with patch('PyQt6.QtWidgets.QFileDialog.getSaveFileName', return_value=(str(target), 'JSON')):
        module.export_result()
        _wait_for_work(qt, window)
    exported = json.loads(target.read_text(encoding="utf-8"))
    assert exported['slots']['A']['snapshot']['source_name'] == source.name
    assert exported['slots']['B']['snapshot']['source_name'] == source.name
    assert exported['slots']['B']['fit']['rx_deg'] is None
    assert exported['slot_fit_frame'] == 'source'
    assert exported['comparison']['frame'] == 'base'
    assert exported['delta_rx_deg'] == automatic['delta_rx_ry_deg'][0]
    np.testing.assert_allclose(exported['comparison']['basis_rows_in_source'],
                               automatic['basis_rows_in_source'])
    np.testing.assert_allclose(exported['slots']['B']['snapshot']['xyz'], b, atol=1e-10)
    _write(window, 1)
    assert module.result is None
    assert module.delta_values[1].text() == '—'
    module.fit_button.click()
    _wait_for_work(qt, window)
    assert not module.result['common_frame']
    assert module.result['deltas'] == (None, None)


def test_vertical_only_visible_import_keeps_points(workspace, tmp_path):
    qt, window, module, notices = workspace
    _, b = _planes()
    source = tmp_path / 'vertical-only.csv'
    _load_points(source, b)
    window.show()
    qt.processEvents()
    assert window.load_path(source)
    _wait_for_work(qt, window)
    assert notices == []
    assert window._import_mapping_ready
    assert window.last_metrics is None
    assert len(window.df_raw) == len(b)
    assert len(window.canvas.ax_yz.collections[0].get_offsets()) == len(b)
    module.write_slot(0)
    np.testing.assert_allclose(module.slots[0]['xyz'], b, atol=1e-10)
    window.display_point_limit = 35
    window.update_analysis()
    assert len(window.canvas.ax_yz.collections[0].get_offsets()) <= 35
    assert len(window._projection_hover['YZ']['indices']) > 11  # more than the XY footprint
    _select(window, 'YZ', (-3.2, .8), (3.2, 5.1))
    np.testing.assert_array_equal(window.active_idx, np.arange(len(b)))


def test_published_host_fails_with_explicit_capability_reason():
    registry = ModuleExtensionRegistry(EXTENSION.parent)
    entry = next(item for item in registry.entries() if item['id'] == EXTENSION.name)
    assert entry['status'] == 'ready'
    legacy_host = SimpleNamespace(api_version=1, app_version='V5.0.8')
    assert registry.load(entry, legacy_host) is None
    assert 'xyz_selection_v1' in entry['status']
    with patch('surface_analyzer.module_extensions.CAPABILITIES', frozenset()):
        entry = next(item for item in registry.refresh() if item['id'] == EXTENSION.name)
        assert 'xyz_selection_v1' in entry['status']


def test_manual_delete_trace_and_coordinate_restrictions(workspace, tmp_path):
    qt, window, module, notices = workspace
    a, b = _planes()
    source = tmp_path / 'deletion.csv'
    _load_points(source, np.vstack([a, b]))
    assert window.load_path(source)
    assert notices == []
    _select(window, 'XZ', (-1, -5.2), (1, -1.8))
    window.set_delete_selection_mode(show_message=False)
    x, _, z = a[0]
    window.on_select(SimpleNamespace(xdata=x - .01, ydata=z - .01),
                     SimpleNamespace(xdata=x + .01, ydata=z + .01), 'XZ')
    np.testing.assert_array_equal(np.flatnonzero(window.temp_selected_mask), [0])
    window.apply_manual_deletion()
    module.write_slot(0)
    slot = module.slots[0]
    np.testing.assert_array_equal(slot['point_indices'], np.arange(1, len(a)))
    np.testing.assert_array_equal(slot['manual_deletion']['deleted_point_indices'], [0])
    assert slot['manual_deletion']['operations'][0]['view'] == 'XZ'
    assert len(slot['manual_deletion']['operations'][0]['source_sha256']) == 64
    window.undo_manual_deletion()
    np.testing.assert_array_equal(slot['point_indices'], np.arange(1, len(a)))
    assert len(slot['manual_deletion']['operations']) == 1
    window.transform_pipeline = ['CW90']
    window.update_analysis()
    module.write_slot(1)
    assert module.slots[1] is None
    assert '撤销姿态变换' in module.message.text()


@pytest.mark.parametrize('generic_state', ['missing', 'disabled', 'load-error', 'available'])
def test_vig_and_builtins_are_independent_of_xyz_extension(generic_state, tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    root = tmp_path / 'SurfaceRxyZxyAnalyzer' / 'module_extensions'
    shutil.copytree(EXTENSION.parent / 'vig-image', root / 'vig-image')
    if generic_state != 'missing':
        install = root / EXTENSION.name
        shutil.copytree(EXTENSION, install)
        if generic_state == 'disabled':
            (install / 'disabled').touch()
        elif generic_state == 'load-error':
            (install / '__init__.py').write_text('raise ValueError("test load error")')
    window = SurfaceAnalyzerPro(settings=QSettings(str(tmp_path / 'settings.ini'),
                                                   QSettings.Format.IniFormat))
    try:
        keys = [item['key'] for item in window.module_descriptors]
        assert keys[:3] == ['single', 'gap', 'parallel']
        assert 'vig-image' in keys
        assert (EXTENSION.name in keys) == (generic_state == 'available')
        loaded = next((item for item in window.module_extension_registry.entries()
                       if item['id'] == EXTENSION.name), None)
        if generic_state == 'load-error':
            assert 'test load error' in loaded['status']
        elif generic_state == 'disabled':
            assert loaded['status'] == 'disabled'
    finally:
        window.close()
        window.deleteLater()
        qt.processEvents()


def test_two_plane_and_ordered_three_point_geometry():
    from scipy.spatial.transform import Rotation

    engine = importlib.import_module('extensions.generic-plane-fit.engine')
    rotation = Rotation.from_euler('xyz', [9, -17, 25], degrees=True).as_matrix()
    origin = np.array([8, -3, 4])
    for offset in (0, .23):
        xyz = np.array([[-2, 0, 0], [2, 0, 0], [offset, 3, 0]]) @ rotation.T + origin
        actual = engine.three_point_frame(xyz)
        np.testing.assert_allclose(actual['basis_rows_in_source'], rotation.T, atol=1e-12)
        np.testing.assert_allclose(actual['origin_mm_in_source'], origin)
        reverse = engine.three_point_frame(xyz[[1, 0, 2]])
        np.testing.assert_allclose(reverse['basis_rows_in_source'],
                                   np.diag([-1, 1, -1]) @ rotation.T)
        matrix, angle = engine.frame_rotation(np.eye(3), actual['basis_rows_in_source'])
        np.testing.assert_allclose(matrix, rotation, atol=1e-12)
        assert angle == pytest.approx(np.degrees(Rotation.from_matrix(rotation).magnitude()))
    with pytest.raises(ValueError, match='共线'):
        engine.three_point_frame([[0, 0, 0], [1, 0, 0], [3, 0, 0]])
    with pytest.raises(ValueError, match='重复'):
        engine.three_point_frame([[0, 0, 0], [0, 0, 0], [3, 0, 0]])
    first = np.array([1., 0, 0])
    second = np.array([np.sin(np.deg2rad(.2)), np.cos(np.deg2rad(.2)), 0.])
    with pytest.raises(ValueError, match='平行'):
        engine.two_plane_frame(first, first, 'equal')
    for policy, x_angle in (('equal', -.1), ('x', 0), ('y', -.2)):
        result = engine.two_plane_frame(first, second, policy)
        basis = result['basis_rows_in_source']
        np.testing.assert_allclose(basis @ basis.T, np.eye(3), atol=1e-12)
        assert np.linalg.det(basis) == pytest.approx(1)
        assert np.degrees(np.arctan2(basis[0, 1], basis[0, 0])) == pytest.approx(x_angle)
        assert result['perpendicularity_error_deg'] == pytest.approx(.2)
        np.testing.assert_allclose(basis[2], [0, 0, 1])


def test_reference_workflows_and_default_import_camera(workspace, tmp_path):
    qt, window, module, notices = workspace
    y, z = _grid(np.arange(3.), np.arange(3.))
    mirror_x = np.column_stack([np.full(len(y), 10.), y, z])
    mirror_y = np.column_stack([y, 10 - .01 * y, z])
    supports = np.array([[0, 0, 0], [2, 0, .2], [.8, 2, -.32]])
    source = tmp_path / 'reference.csv'
    _load_points(source, np.vstack((mirror_x, mirror_y, supports)))
    window.resize(1366, 900)
    window.show()
    qt.processEvents()
    window.canvas.ax3d.view_init(5, 15)
    assert window.load_path(source)
    _wait_for_work(qt, window)
    ax = window.canvas.ax3d
    assert (ax.elev, ax.azim) == (45, -135)
    assert ax._surface_home_fits and ax._surface_home_labels_fit
    _, _, bottom, top = ax._surface_home_screen_bounds
    assert (top - bottom) / ax.figure.bbox.height > .65
    assert not notices

    module.coordinate_frame.setCurrentIndex(3)
    _select(window, 'XY', (9.5, -.5), (10.5, 2.5))
    module.write_slot(0)
    _select(window, 'XY', (-.5, 9.5), (2.5, 10.5))
    module.write_slot(1)
    _select(window, 'XY', (-.5, -.5), (2.5, 2.5))
    module.write_slot(2)
    window.module_buttons[3].click()
    order = tuple(int(np.argmin(np.linalg.norm(module.slots[2]['xyz'] - point, axis=1)))
                  for point in supports)
    module.point_order.setCurrentIndex(next(
        i for i in range(module.point_order.count()) if module.point_order.itemData(i) == order))
    module.fit_button.click()
    _wait_for_work(qt, window)
    comparison = module._comparison(module.result)
    assert comparison['ready']
    assert comparison['perpendicularity_error_deg'] == pytest.approx(np.degrees(np.arctan(.01)))
    assert comparison['frame_rotation_deg'] > 0
    assert float(module.dihedral_value.text()) == pytest.approx(comparison['total_tilt_deg'], abs=.0005)
    assert float(module.ab_angle_value.text()) == pytest.approx(comparison['ab_plane_angle_deg'], abs=.0005)
    assert all(angle is not None for angle in comparison['delta_rx_ry_deg'])
    path = tmp_path / 'reference.json'
    with patch('PyQt6.QtWidgets.QFileDialog.getSaveFileName', return_value=(str(path), 'JSON')):
        module.export_result()
        _wait_for_work(qt, window)
    exported = json.loads(path.read_text(encoding="utf-8"))
    assert exported['schema_version'] == 3
    assert exported['comparison']['frame'] == 'two_planes'
    np.testing.assert_allclose(exported['comparison']['target_point_frame']['ordered_points_mm'],
                               supports)
    assert len(exported['slots']['C']['snapshot']['xyz']) == 3

    module.coordinate_frame.setCurrentIndex(2)
    module.write_slot(0)  # The main ROI still contains only the supports.
    assert module.point_order.currentData() is None
    assert module.scene.currentWidget() is module.canvas
    point_labels = [f'#{index}' for index in module.slots[0]['point_indices']]
    assert [text.get_text() for text in module.axes.texts] == point_labels
    axes = module.axes
    module.canvas.draw()
    event = MouseEvent('scroll_event', module.canvas, axes.bbox.x0 + 10,
                       axes.bbox.y0 + 10, step=1)
    module.canvas.callbacks.process('scroll_event', event)
    assert module._zoom_3d > .85
    module.view_choice.setCurrentIndex(2)
    assert module.projections[1].get_visible()
    assert not axes.get_visible()
    assert [text.get_text() for text in module.projections[1].texts] == point_labels
    module.view_choice.setCurrentIndex(0)
    assert module._zoom_3d > .85
    module._reset_view()
    assert module._zoom_3d == .85
    module.point_order.setCurrentIndex(next(
        i for i in range(module.point_order.count()) if module.point_order.itemData(i) == order))
    assert module.result is None
    comparison = module._comparison(None)
    np.testing.assert_allclose(comparison['ordered_points_mm'], supports)
    annotations = {text.get_text() for text in module.axes.texts}
    assert {f'{point_labels[i]} · {role}' for i, role in zip(order, 'ABC')} <= annotations
    assert module.scene.currentWidget() is module.canvas
    module.workflow.setCurrentIndex(1)
    module.point_order.setCurrentIndex(0)
    module.pick_points_button.click()
    module.canvas.draw()
    projection = module.projections[0]
    for point in supports:
        x, y = projection.transData.transform(point[:2])
        event = MouseEvent('button_press_event', module.canvas, x, y, button=1)
        module.canvas.callbacks.process('button_press_event', event)
    assert module.point_order.currentData() == order
    module.coordinate_frame.setCurrentIndex(0)
    module.coordinate_frame.setCurrentIndex(2)
    assert module.point_order.currentData() == order


def test_outlier_rejection_is_orthogonal_and_skips_three_points():
    engine = importlib.import_module('extensions.generic-plane-fit.engine')
    x, y = _grid(np.linspace(-3, 3, 12), np.linspace(-2, 2, 10))
    z = .001 * np.sin(np.arange(len(x)))
    z[[2, 16, 73]] += [.18, -.2, .25]
    xyz = np.column_stack((x, y, z))
    for points in (xyz, xyz[:, [2, 0, 1]]):
        fit, trace = engine.fit_plane_with_outlier_rejection(points)
        np.testing.assert_array_equal(trace['rejected_positions'], [2, 16, 73])
        assert fit.rms_mm < .001
        assert trace['original_rms_mm'] > .02
    points = [[0, 0, 0], [3, 0, 0], [1, 2, 0]]
    fit, trace = engine.fit_plane_with_outlier_rejection(points)
    assert len(fit.signed_distances_mm) == 3
    assert len(trace['rejected_positions']) == 0
    assert '跳过' in trace['note']
    crowded = np.column_stack((x, y, np.where(np.arange(len(x)) % 4 == 0, .3, 0)))
    fit, trace = engine.fit_plane_with_outlier_rejection(crowded)
    assert '候选剔除过多' in trace['note']
    assert len(fit.signed_distances_mm) == len(crowded)


def test_outlier_rejection_workflow_preserves_snapshot(workspace, tmp_path):
    qt, window, module, notices = workspace
    y, z = _grid(np.linspace(-3, 3, 12), np.linspace(-2, 2, 10))
    x = .001 * np.sin(np.arange(len(y)))
    x[[2, 16, 73]] += [.18, -.2, .25]
    source = tmp_path / 'noisy-wall.csv'
    _load_points(source, np.column_stack((x, y, z)))
    assert window.load_path(source)
    assert not module.reject_outliers.isChecked()
    module.write_slot(0)
    module.reject_outliers.setChecked(True)
    module.compute()
    _wait_for_work(qt, window)
    trace = module.result['filters'][0]
    np.testing.assert_array_equal(trace['rejected_point_indices'], [2, 16, 73])
    assert len(module.slots[0]['xyz']) == 120
    assert len(module.result['fits'][0].signed_distances_mm) == 117
    assert '剔除 3 点' in module.quality.text()
    path = tmp_path / 'clean-fit.json'
    with patch('PyQt6.QtWidgets.QFileDialog.getSaveFileName', return_value=(str(path), 'JSON')):
        module.export_result()
        _wait_for_work(qt, window)
    exported = json.loads(path.read_text(encoding='utf-8'))['slots']['A']
    assert exported['outlier_filter']['rejected_point_indices'] == [2, 16, 73]
    assert len(exported['snapshot']['xyz']) == 120
    module.reject_outliers.setChecked(False)
    assert module.result is None


def test_two_plane_normal_flips_and_non_orthogonal_warning():
    engine = importlib.import_module('extensions.generic-plane-fit.engine')
    first = np.array([1.0, 0.0, 0.0])
    second = np.array([0.0, 1.0, 0.0])

    base = engine.two_plane_frame(first, second, 'equal')
    assert base['warnings'] == []
    assert base['flip_first'] is False and base['flip_second'] is False
    np.testing.assert_allclose(base['basis_rows_in_source'], np.eye(3), atol=1e-12)

    flipped_a = engine.two_plane_frame(first, second, 'equal', flip_first=True)
    assert flipped_a['flip_first'] is True
    basis_a = flipped_a['basis_rows_in_source']
    np.testing.assert_allclose(basis_a @ basis_a.T, np.eye(3), atol=1e-12)
    assert np.linalg.det(basis_a) == pytest.approx(1.0)
    np.testing.assert_allclose(basis_a[0], [-1.0, 0.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(basis_a[2], [0.0, 0.0, -1.0], atol=1e-12)

    flipped_both = engine.two_plane_frame(first, second, 'x', flip_first=True, flip_second=True)
    basis_both = flipped_both['basis_rows_in_source']
    np.testing.assert_allclose(basis_both[2], [0.0, 0.0, 1.0], atol=1e-12)
    np.testing.assert_allclose(basis_both[0], [-1.0, 0.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(basis_both[1], [0.0, -1.0, 0.0], atol=1e-12)

    oblique = np.array([np.cos(np.deg2rad(60.0)), np.sin(np.deg2rad(60.0)), 0.0])
    warned = engine.two_plane_frame(first, oblique, 'equal')
    assert warned['perpendicularity_error_deg'] == pytest.approx(30.0)
    assert any('偏离直角' in text for text in warned['warnings'])


def test_review_fixes_slots_alerts_camera_zoom_and_export_guards(workspace, tmp_path):
    from surface_analyzer.plotting import DEFAULT_3D_AZIMUTH, DEFAULT_3D_ELEVATION
    from surface_analyzer.widgets import NoWheelComboBox

    qt, window, module, _ = workspace
    for combo in (module.coordinate_frame, module.reference_axis, module.point_order,
                  module.frame_policy, module.view_choice, module.inspect_slot):
        assert isinstance(combo, NoWheelComboBox)
    assert (module.axes.elev, module.axes.azim) == (DEFAULT_3D_ELEVATION, DEFAULT_3D_AZIMUTH)

    # Dataset with two orthogonal mirrors, 3 support points, and a collinear line.
    y, z = _grid(np.linspace(-2, 2, 7), np.linspace(-2, 2, 7))
    mirror_x = np.column_stack([np.full(len(y), 10.0), y, z])
    mirror_y = np.column_stack([y, np.full(len(y), 10.0), z])
    supports = np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.2], [0.8, 2.0, -0.3]])
    collinear = np.column_stack([np.linspace(20, 24, 6), np.full(6, 20.0), np.full(6, 1.0)])
    source = tmp_path / 'review.csv'
    _load_points(source, np.vstack((mirror_x, mirror_y, supports, collinear)))
    assert window.load_path(source)
    _wait_for_work(qt, window)

    # 1. Fit failure and warnings surface directly on the main page (not only in modal).
    module.coordinate_frame.setCurrentIndex(0)  # source
    _select(window, 'XY', (9.5, -2.5), (10.5, 2.5))
    module.write_slot(0)
    _select(window, 'XY', (19.5, 19.5), (24.5, 20.5))
    module.write_slot(1)
    module.compute()
    _wait_for_work(qt, window)
    assert '拟合失败' in module.message.text()
    assert '拟合失败' in module.comparison_note.text()
    assert '失败' in module.result_quality.text()
    module.inspect_slot.setCurrentIndex(1)
    assert '拟合失败' in module.feature_quality.text()

    # 2. Single-slot clear & A/B swap.
    module.clear_slot(1)
    assert module.slots[0] is not None and module.slots[1] is None
    assert module.result is None
    module.swap_ab()
    assert module.slots[0] is None and module.slots[1] is not None
    module.swap_ab()
    assert module.slots[0] is not None and module.slots[1] is None

    # 3. Non-orthogonal "偏离直角" hidden in source mode, shown in two_planes mode;
    #    hidden slot C does not leak into 2-slot mode UI, compute, or export.
    module.coordinate_frame.setCurrentIndex(3)  # two_planes
    assert module.inspect_slot.count() == 3
    _select(window, 'XY', (-2.5, 9.5), (2.5, 10.5))
    module.write_slot(1)
    _select(window, 'XY', (-0.5, -0.5), (2.5, 2.5))
    module.write_slot(2)
    module.compute()
    _wait_for_work(qt, window)
    assert '偏离直角' in module.feature_note.text()
    assert {'X′', 'Y′', 'Z′'} <= {t.get_text() for t in module.axes.texts}
    assert '不可定义' not in module.comparison_note.text()
    assert module.message.text() == '拟合已完成。'
    rx_before = module._comparison(module.result)['delta_rx_ry_deg'][0]
    module.flip_first.setChecked(True)
    rx_after = module._comparison(module.result)['delta_rx_ry_deg'][0]
    assert rx_after == pytest.approx(-rx_before, abs=1e-6)
    module.flip_first.setChecked(False)
    module.copy_summary()
    assert '通用三维拟合摘要' in QApplication.clipboard().text()
    assert module._arrow_stroke_color() == '#ecedf1'
    window.set_theme_preference('dark', persist=False)
    assert module._arrow_stroke_color() == '#19191b'
    window.set_theme_preference('light', persist=False)
    assert module._arrow_stroke_color() == '#ecedf1'

    # Switch back to source mode: slot C is hidden from inspector, status, and JSON export;
    # source-frame projection warnings now surface on the main banner.
    module.coordinate_frame.setCurrentIndex(0)
    assert module.inspect_slot.count() == 2
    assert '测量面 C' not in module.status_text()
    assert '偏离直角' not in module.feature_note.text()
    assert '不可定义' in module.comparison_note.text()
    out_source = tmp_path / 'source-no-c.json'
    with patch('PyQt6.QtWidgets.QFileDialog.getSaveFileName', return_value=(str(out_source), 'JSON')):
        module.export_result()
        _wait_for_work(qt, window)
    exported_source = json.loads(out_source.read_text(encoding='utf-8'))
    assert exported_source['slots']['C']['snapshot'] is None
    assert exported_source['slots']['C']['fit'] is None

    # 4. In two_planes mode, mismatched frame_id in C sets top-level common_frame to False.
    other = tmp_path / 'other.csv'
    _load_points(other, supports)
    assert window.load_path(other)
    _wait_for_work(qt, window)
    module.coordinate_frame.setCurrentIndex(3)
    module.write_slot(2)
    module.compute()
    _wait_for_work(qt, window)
    out_mismatch = tmp_path / 'mismatch-c.json'
    with patch('PyQt6.QtWidgets.QFileDialog.getSaveFileName', return_value=(str(out_mismatch), 'JSON')):
        module.export_result()
        _wait_for_work(qt, window)
    assert json.loads(out_mismatch.read_text(encoding='utf-8'))['common_frame'] is False

    # 5. In points mode: export disabled while B is unfitted; projection pick preserves XZ;
    #    changing point_order preserves user-zoomed projection limits; reset restores (45, -135).
    module.clear()
    assert window.load_path(source)
    _wait_for_work(qt, window)
    module.coordinate_frame.setCurrentIndex(2)  # points
    _select(window, 'XY', (-0.5, -0.5), (2.5, 2.5))
    module.write_slot(0)
    module.point_order.setCurrentIndex(1)
    assert module.export_actions()[0][2] is True  # Only A (3 points) -> frame export allowed
    _select(window, 'XY', (9.5, -2.5), (10.5, 2.5))
    module.write_slot(1)
    assert module.result is None
    assert module.export_actions()[0][2] is False  # B present but unfitted -> export disabled

    module.view_choice.setCurrentIndex(2)  # XZ projection
    module._start_point_pick()
    assert module.view_choice.currentIndex() == 2  # Preserves XZ instead of forcing XY
    module._start_point_pick()  # Cancel pick
    module.projections[1].set_xlim(0.2, 1.8)
    module.projections[1].set_ylim(-0.2, 0.2)
    module.point_order.setCurrentIndex(2)
    assert module.projections[1].get_xlim() == pytest.approx((0.2, 1.8))
    assert module.projections[1].get_ylim() == pytest.approx((-0.2, 0.2))

    module.view_choice.setCurrentIndex(0)
    module.axes.view_init(elev=10, azim=20)
    module._reset_view()
    assert (module.axes.elev, module.axes.azim) == (DEFAULT_3D_ELEVATION, DEFAULT_3D_AZIMUTH)


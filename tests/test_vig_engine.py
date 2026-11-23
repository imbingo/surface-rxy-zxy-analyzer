import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from surface_analyzer.module_extensions import ModuleExtensionRegistry
from surface_analyzer.workers import TaskCancelled

_extension_dir = Path(__file__).parents[1] / 'extensions' / 'vig-image'
_registry = ModuleExtensionRegistry(_extension_dir.parent, app_version='5.0.6')
_entry = next(entry for entry in _registry.entries() if entry['id'] == 'vig-image')
_registry.load(_entry, SimpleNamespace(api_version=1, app_version='5.0.6'))
_package = _entry['_package']
engine = _package.engine
calculate_zimg2low_from_df = engine.calculate_zimg2low_from_df
compute_vig_image = engine.compute_vig_image
match_by_xy = engine.match_by_xy
process_plane_from_df = engine.process_plane_from_df

GOLDEN_F_IMG_UM = np.array([
    346.8065144299187, 346.8065144299187, 370.9442358234236, 388.6758529327735,
    346.8065144299187, 370.9442358234236, 388.6758529327735, 347.05628495826744,
    370.9442358234236, 388.6758529327735, 347.05628495826744, 347.05628495826744,
])
GOLDEN_PRINCIPLE_Z_UM = np.array([
    -39.447653715240506, -39.447653715240506, -45.41579702886045,
    -48.2160213441124, -39.447653715240506, -45.41579702886045,
    -48.2160213441124, -40.844946535089655, -45.41579702886045,
    -48.2160213441124, -40.844946535089655, -40.844946535089655,
])
GOLDEN_IMAGE_UM = np.array([
    -7.641139285321799, -7.641139285321799, -25.47156120543687,
    -28.540168411338875, -7.641139285321799, -25.47156120543687,
    -28.540168411338875, -28.788661576822214, -25.47156120543687,
    -28.540168411338875, -28.788661576822214, -28.788661576822214,
])
GOLDEN_PLANE = {
    'rx': 0.0013909632709615673,
    'ry': -0.001731930524580748,
    'rms': 4.9192263332985494e-06,
    'std3': 1.4757678999895647e-05,
    'p97_half': 6.624288652864958e-06,
}


def _golden_data():
    x, y = np.meshgrid(
        np.array([-4.0, -1.2, 1.7, 5.0]),
        np.array([-3.5, 0.4, 4.1]))
    x, y = x.ravel(), y.ravel()
    score = x + 0.8 * y
    categories = np.digitize(score, np.quantile(score, [0.25, 0.5, 0.75]))
    pairs = ((52.0, 160.0), (64.0, 196.0), (76.0, 214.0), (40.0, 180.0))
    vig_up = np.array([pairs[index][0] for index in categories])
    vig_low = np.array([pairs[index][1] for index in categories])
    t_up, t_mid, t_low = 120.0, 150.0, 80.0
    two_um = vig_low + t_mid + t_low
    three_um = vig_up + two_um + t_up
    frame = pd.DataFrame({
        'X': x,
        'Y': y,
        'thickness_up': t_up,
        'thickness_mid': t_mid,
        'thickness_low': t_low,
        'VIG_low': vig_low,
        'VIG_up': vig_up,
    })
    two = {'x': x, 'y': y, 'z': two_um / 1000, 'name': 'two', 'n': len(x)}
    three = {'x': x, 'y': y, 'z': three_um / 1000, 'name': 'three', 'n': len(x)}
    return frame, two, three, {'up': t_up, 'mid': t_mid, 'low': t_low}


class VigEngineTests(unittest.TestCase):
    def test_golden_image_and_plane_outputs_match_original_tool(self):
        frame, two, three, thickness = _golden_data()
        results = calculate_zimg2low_from_df(frame)
        plane = process_plane_from_df(results)

        np.testing.assert_allclose(results['f_img'], GOLDEN_F_IMG_UM, rtol=1e-9, atol=1e-12)
        np.testing.assert_allclose(
            results['img_principle_z'], GOLDEN_PRINCIPLE_Z_UM, rtol=1e-9, atol=1e-12)
        np.testing.assert_allclose(results['image'], GOLDEN_IMAGE_UM, rtol=0, atol=1e-7)
        for key, value in GOLDEN_PLANE.items():
            self.assertAlmostEqual(plane[{'rx': 'Rx', 'ry': 'Ry'}.get(key, key)], value,
                                   delta=max(abs(value) * 1e-8, 1e-15))

        updates = []
        payload = compute_vig_image(
            two, three, thickness, tolerance_mm=0.01,
            progress=lambda value, text: updates.append((value, text)))
        np.testing.assert_allclose(payload['f_img'], GOLDEN_F_IMG_UM, rtol=1e-9, atol=1e-12)
        np.testing.assert_allclose(
            payload['img_principle_z'], GOLDEN_PRINCIPLE_Z_UM, rtol=1e-9, atol=1e-12)
        np.testing.assert_allclose(payload['image'], GOLDEN_IMAGE_UM, rtol=0, atol=1e-7)
        self.assertEqual(payload['n_points'], 12)
        for key, value in GOLDEN_PLANE.items():
            self.assertAlmostEqual(
                payload['plane'][key], value, delta=max(abs(value) * 1e-8, 1e-15))
        self.assertEqual(updates[0][0], 0)
        self.assertEqual(updates[-1][0], 100)
        self.assertIn("完成", updates[-1][1])

    def test_identical_xy_returns_every_pair_in_reference_order(self):
        xy = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
        ref, other = match_by_xy(xy, xy.copy(), 0.01)
        np.testing.assert_array_equal(ref, np.arange(4))
        np.testing.assert_array_equal(other, np.arange(4))

    def test_shuffled_other_layer_with_one_missing_point_maps_correctly(self):
        xy = np.column_stack((np.arange(8, dtype=float), np.arange(8, dtype=float) * 0.2))
        other_source_indices = np.array([6, 0, 7, 2, 4, 1, 5])
        ref, other = match_by_xy(xy, xy[other_source_indices], 0.01)
        np.testing.assert_array_equal(ref, np.array([0, 1, 2, 4, 5, 6, 7]))
        np.testing.assert_array_equal(other_source_indices[other], ref)

    def test_one_offset_point_is_dropped_outside_tolerance(self):
        xy = np.column_stack((np.arange(6, dtype=float), np.zeros(6)))
        other_xy = xy.copy()
        other_xy[3, 0] += 0.05
        ref, other = match_by_xy(xy, other_xy, 0.01)
        np.testing.assert_array_equal(ref, np.array([0, 1, 2, 4, 5]))
        np.testing.assert_array_equal(other, ref)

    def test_duplicate_and_near_points_still_produce_one_to_one_pairs(self):
        ref_xy = np.array([[0.0, 0.0], [0.0, 0.0], [1.0, 1.0]])
        other_xy = np.array([[0.0, 0.0], [0.0001, 0.0], [1.0, 1.0]])
        ref, other = match_by_xy(ref_xy, other_xy, 0.01)
        self.assertEqual(len(ref), 2)
        self.assertEqual(len(set(ref.tolist())), len(ref))
        self.assertEqual(len(set(other.tolist())), len(other))

    def test_matching_drops_missing_and_shifted_xy_rows_and_reports_them(self):
        frame, two, three, thickness = _golden_data()
        ref_xy = np.column_stack((two['x'], two['y']))
        keep = np.array([i for i in range(len(ref_xy)) if i != 2])
        other_xy = ref_xy[keep].copy()
        shifted_local = int(np.flatnonzero(keep == 7)[0])
        other_xy[shifted_local, 0] += 0.05
        permutation = np.array([4, 9, 0, 7, 2, 6, 1, 10, 5, 3, 8])
        other_source = keep[permutation]
        other_xy = other_xy[permutation]
        shifted_three = dict(
            x=other_xy[:, 0], y=other_xy[:, 1], z=three['z'][other_source],
            name='shuffled three', n=len(other_source))

        payload = compute_vig_image(two, shifted_three, thickness, tolerance_mm=0.01)
        self.assertEqual(payload['n_points'], 10)
        report = payload['match_reports'][0]
        self.assertEqual((report['matched'], report['total_ref'], report['total_other']),
                         (10, 12, 11))
        unmatched = np.asarray(report['unmatched_ref_xy'])
        np.testing.assert_allclose(unmatched, ref_xy[[2, 7]])
        self.assertEqual(report['unmatched_other'], 1)

    def test_per_point_thickness_matches_constant_values(self):
        _, two, three, thickness = _golden_data()
        constants = compute_vig_image(two, three, thickness, tolerance_mm=0.01)
        per_point = {
            key: {'x': two['x'], 'y': two['y'],
                  'z': np.full(len(two['x']), value / 1000),
                  'name': f'{key} thickness', 'n': len(two['x'])}
            for key, value in thickness.items()
        }
        matched = compute_vig_image(two, three, per_point, tolerance_mm=0.01)
        for key in ('x', 'y', 'vig_up', 'vig_low', 't_up', 't_mid', 't_low',
                    'f_img', 'img_principle_z', 'image', 'resid_um'):
            np.testing.assert_allclose(matched[key], constants[key], rtol=1e-12, atol=1e-12)
        for key in GOLDEN_PLANE:
            self.assertAlmostEqual(
                matched['plane'][key], constants['plane'][key], delta=abs(constants['plane'][key]) * 1e-12)

    def test_no_xy_overlap_raises_requested_error(self):
        _, two, three, thickness = _golden_data()
        three['x'] = np.asarray(three['x']) + 50.0
        with self.assertRaisesRegex(
                ValueError, '各图层 XY 坐标没有可配对的点，请检查容差和坐标原点'):
            compute_vig_image(two, three, thickness, tolerance_mm=0.01)

    def test_cancel_callable_propagates_task_cancelled(self):
        _, two, three, thickness = _golden_data()

        def cancel():
            raise TaskCancelled("cancelled")

        with self.assertRaises(TaskCancelled):
            compute_vig_image(two, three, thickness, check_cancel=cancel)

        frame, _, _, _ = _golden_data()
        with self.assertRaises(TaskCancelled):
            calculate_zimg2low_from_df(frame, check_cancel=cancel)


if __name__ == "__main__":
    unittest.main()

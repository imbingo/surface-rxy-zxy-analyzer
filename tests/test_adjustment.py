import copy
import unittest
from dataclasses import replace
from math import tan

import numpy as np

from surface_analyzer.adjustment import (
    Plane, SupportPoint, AdjustmentConfig, calculate_adjustment, quantize_shim)
from surface_analyzer.mixins.analysis import AnalysisMixin


class AdjustmentTests(unittest.TestCase):
    def setUp(self):
        self.points = tuple(SupportPoint(f'P{i}', x, y, 100., 0., 500.)
                            for i, (x, y) in enumerate([(-50,-50),(50,-50),(0,50)], 1))
        self.zero = Plane(0, 0, 0)

    def assert_zero(self, result, tolerance=1e-8):
        for value in vars(result.residual_pose).values():
            self.assertLess(abs(value), tolerance)

    def test_pure_dz_and_negative_removal(self):
        for dz in (20, -25):
            r = calculate_adjustment(self.zero, Plane(0,0,dz/1000), self.points)
            for row in r.support_results:
                self.assertAlmostEqual(row.theoretical_adjustment_um, dz)
                self.assertAlmostEqual(row.error_height_um, -dz)
                self.assertAlmostEqual(row.ideal_shim_um, 100+dz)
            self.assertTrue(r.executable)
            self.assert_zero(r)

    def test_exact_rx_ry(self):
        for rx, ry in ((100.,0.),(0.,100.)):
            target = Plane.from_pose(rx,ry,0,(0,0))
            r = calculate_adjustment(self.zero,target,self.points,
                                     AdjustmentConfig(origin_mode='coordinate_origin'))
            for row in r.support_results:
                expected = (-tan(ry/1e6)*row.support.x_mm + tan(rx/1e6)*row.support.y_mm)*1000
                self.assertAlmostEqual(row.theoretical_adjustment_um,expected,places=12)
            self.assert_zero(r)

    def test_combined_n_points_origin_swap_and_immutability(self):
        points = self.points + (SupportPoint('P4',70,60,100,0,500), SupportPoint('P5',-70,30,100,0,500))
        current,target = Plane(.0002,-.0003,.45),Plane(-.0001,.0001,.46)
        before = copy.deepcopy((current,target,points))
        heights = []
        for mode in ('support_centroid','coordinate_origin','custom'):
            cfg = AdjustmentConfig(origin_mode=mode,origin_x_mm=12,origin_y_mm=-17)
            r = calculate_adjustment(current,target,points,cfg)
            swapped = calculate_adjustment(target,current,points,cfg)
            self.assert_zero(r)
            heights.append([p.theoretical_adjustment_um for p in r.support_results])
            np.testing.assert_allclose(heights[-1],[-p.theoretical_adjustment_um for p in swapped.support_results])
            self.assertEqual(r.prediction_kind,'least_squares_geometry')
        np.testing.assert_allclose(heights[0],heights[1],atol=1e-10)
        np.testing.assert_allclose(heights[0],heights[2],atol=1e-10)
        self.assertEqual(before,(current,target,points))

    def test_quantization_and_prediction(self):
        r=calculate_adjustment(self.zero,Plane(0,0,.0173),self.points,
                               AdjustmentConfig(quantization='fixed_step'))
        for row in r.support_results:
            self.assertAlmostEqual(row.recommended_shim_um,115)
            self.assertAlmostEqual(row.actual_adjustment_um,15)
            self.assertAlmostEqual(row.quantization_error_um,-2.3)
        self.assertAlmostEqual(r.residual_pose.dz0_um,-2.3)
        for value, step, expected in ((17.3,5,15),(17.5,5,20),(-17.5,5,-20),(.15,.1,.2)):
            self.assertEqual(quantize_shim(value,step),expected)

    def test_final_thickness_quantization_point_override(self):
        points=tuple(replace(p,base_shim_um=102,step_um=10) for p in self.points)
        r=calculate_adjustment(self.zero,Plane(0,0,.0173),points,AdjustmentConfig(quantization='fixed_step'))
        self.assertAlmostEqual(r.support_results[0].recommended_shim_um,120)
        self.assertAlmostEqual(r.support_results[0].actual_adjustment_um,18)

    def test_custom_origin_and_four_point_quantized_pose(self):
        origin=(12.,-17.)
        target=Plane.from_pose(123.,-87.,450.,origin)
        self.assertAlmostEqual(target.at(*origin)*1000,450)
        current=Plane.from_pose(-32.,41.,430.,origin)
        points=self.points+(SupportPoint('P4',40,60,103,0,500),)
        cfg=AdjustmentConfig(origin_mode='custom',origin_x_mm=12,origin_y_mm=-17)
        self.assert_zero(calculate_adjustment(current,target,points,cfg))
        r=calculate_adjustment(current,target,points,replace(cfg,quantization='fixed_step'))
        matrix=np.array([[p.x_mm,p.y_mm,1] for p in points])
        values=np.array([row.actual_adjustment_um/1000 for row in r.support_results])
        fit=np.linalg.lstsq(matrix,values,rcond=None)[0]
        expected_rx=(np.arctan(current.b+fit[1])-np.arctan(target.b))*1e6
        expected_ry=(np.arctan(-current.a-fit[0])-np.arctan(-target.a))*1e6
        self.assertAlmostEqual(r.residual_pose.rx_urad,expected_rx,places=8)
        self.assertAlmostEqual(r.residual_pose.ry_urad,expected_ry,places=8)
        self.assertGreater(max(abs(expected_rx),abs(expected_ry)),.01)

    def test_limits_no_clamping(self):
        points=tuple(replace(p,max_shim_um=118) for p in self.points)
        r=calculate_adjustment(self.zero,Plane(0,0,.019),points,AdjustmentConfig(quantization='fixed_step'))
        self.assertFalse(r.executable)
        self.assertEqual(r.support_results[0].recommended_shim_um,120)
        r=calculate_adjustment(self.zero,Plane(0,0,-.2),self.points)
        self.assertFalse(r.executable)
        self.assertEqual(r.support_results[0].recommended_shim_um,-100)
        points=tuple(replace(p,base_shim_um=600) for p in self.points)
        r=calculate_adjustment(self.zero,Plane(0,0,-.2),points)
        self.assertTrue(any('基础垫片' in w for w in r.warnings))

    def test_geometry_and_invalid_inputs(self):
        for xy in ([(0,0),(10,0),(20,0)],[(0,0),(10,1e-10),(20,0)],[(0,0),(0,0),(2,3)]):
            points=tuple(replace(p,x_mm=x,y_mm=y) for p,(x,y) in zip(self.points,xy))
            with self.assertRaises(ValueError): calculate_adjustment(self.zero,self.zero,points)
        with self.assertRaises(ValueError): calculate_adjustment(self.zero,self.zero,self.points[:2])
        for changes in ({'x_mm':float('nan')},{'step_um':0},{'min_shim_um':501}, {'base_shim_um':float('inf')}):
            with self.assertRaises(ValueError):
                calculate_adjustment(self.zero,self.zero,(replace(self.points[0],**changes),)+self.points[1:])
        for cfg in (AdjustmentConfig(default_step_um=0),AdjustmentConfig(origin_mode='bad'),
                    AdjustmentConfig(origin_x_mm=float('nan')),AdjustmentConfig(quantization='bad')):
            with self.assertRaises(ValueError): calculate_adjustment(self.zero,self.zero,self.points,cfg)

    def test_large_coordinates(self):
        points=tuple(replace(p,x_mm=p.x_mm+1e9,y_mm=p.y_mm-1e9) for p in self.points)
        current=Plane(.0002,-.0003,0)
        target=Plane(.0001,-.0002,200000.01)
        r=calculate_adjustment(current,target,points)
        self.assert_zero(r,tolerance=1e-4)

    def test_mean_z_is_not_origin_difference(self):
        x=np.array([0.,10.,0.,10.]); y=np.array([0.,0.,10.,10.])
        m=AnalysisMixin.compute_plane_metrics(x,y,.001*x+.002*y)
        b=AnalysisMixin.compute_plane_metrics(x+100,y,.001*(x+100)+.002*y+.02)
        current=Plane(m['a'],m['b'],m['c']); target=Plane(b['a'],b['b'],b['c'])
        r=calculate_adjustment(current,target,self.points,AdjustmentConfig(origin_mode='coordinate_origin'))
        self.assertAlmostEqual(r.before_error.dz0_um,-20)
        self.assertNotAlmostEqual(r.before_error.dz0_um,(m['mean_z']-b['mean_z'])*1000)
        self.assert_zero(r)


if __name__ == '__main__':
    unittest.main()

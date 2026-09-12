import unittest
import numpy as np
from surface_analyzer.rendering.lod import spatial_lod_indices, critical_indices


class LODTests(unittest.TestCase):
    def test_extrema_and_limit(self):
        rng = np.random.default_rng(41)
        x, y = rng.uniform(-15, 15, (2, 1000000))
        z = .45+1e-5*x+2e-5*y
        z[12345] += .02
        z[23456] -= .015
        source = np.arange(len(x))
        required = critical_indices(x,y,z,source,(1e-5,2e-5,.45))
        shown = spatial_lod_indices(x,y,source,30000,required)
        self.assertTrue({12345,23456}.issubset(shown))
        self.assertLessEqual(len(shown),30000)
        np.testing.assert_array_equal(source,np.arange(len(x)))

    def test_reordering_does_not_change_spatial_representatives(self):
        rng = np.random.default_rng(2)
        x,y = rng.normal(size=(2,10000))
        source = np.arange(len(x))
        first = spatial_lod_indices(x,y,source,100)
        rng.shuffle(source)
        second = spatial_lod_indices(x,y,source,100)
        np.testing.assert_array_equal(first,second)

    def test_roi_and_thin_layout(self):
        x = np.arange(10000,dtype=float)
        y = np.zeros(len(x))
        source = np.arange(200,900)
        shown = spatial_lod_indices(x,y,source,100,[250,899,9999])
        self.assertLessEqual(len(shown),100)
        self.assertTrue(set(shown).issubset(source))
        self.assertTrue({250,899}.issubset(shown))

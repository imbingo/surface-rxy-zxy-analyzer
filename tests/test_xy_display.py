import unittest
import numpy as np
from surface_analyzer.rendering.xy_display import build_xy_display, estimate_scan_grid
from surface_analyzer.rendering.settings import validate_display


class XYDisplayTests(unittest.TestCase):
    def scan(self):
        x, y = np.meshgrid(np.arange(-15,15.001,.1), np.arange(-15,15.001,.1))
        x, y = x.ravel(), y.ravel()
        # Smooth axis nonlinearity + independent repeatability error, in mm.
        rng = np.random.default_rng(468)
        return (x+.015*np.sin(x/5)+rng.normal(0,.001,len(x)),
                y+.015*np.sin(y/4)+rng.normal(0,.001,len(y)))

    def test_pitch_and_occupancy_do_not_follow_window_pixels(self):
        x, y = self.scan()
        before = x.copy()
        grid = estimate_scan_grid(x,y)
        np.testing.assert_allclose(grid[:2], [.125,.125], atol=.004)
        for size in ((700,500),(900,600),(1200,1200)):
            for extent in ((-14,14,-14,14),(-5,5,-5,5),(.011,.041,.013,.043)):
                r = build_xy_display(x,y,x+y,extent,size,grid=grid)
                self.assertGreater(np.count_nonzero(r.count)/r.count.size,.96)
                dx = (r.extent[1]-r.extent[0])/r.count.shape[1]
                self.assertGreaterEqual(dx,grid[0]*.999999)
        np.testing.assert_array_equal(x,before)

    def test_holes_and_strip_gaps_stay_empty(self):
        x,y = self.scan()
        keep = (x*x+y*y > 9) & ~((y > 5) & (y < 6))
        x,y = x[keep],y[keep]
        r = build_xy_display(x,y,x+y,(-15.1,15.1,-15.1,15.1),(1200,1200))
        xc = np.linspace(r.extent[0],r.extent[1],r.count.shape[1],endpoint=False)
        yc = np.linspace(r.extent[2],r.extent[3],r.count.shape[0],endpoint=False)
        xc += (r.extent[1]-r.extent[0])/r.count.shape[1]/2
        yc += (r.extent[3]-r.extent[2])/r.count.shape[0]/2
        xx,yy = np.meshgrid(xc,yc)
        self.assertFalse(r.count[xx*xx+yy*yy < 2.5**2].any())
        self.assertFalse(r.count[(yy>5.2)&(yy<5.8)].any())

    def test_anisotropic_pitch_shuffled_and_duplicate_coordinates(self):
        x,y = np.meshgrid(np.arange(101)*.1,np.arange(51)*.5)
        x,y = x.ravel(),y.ravel()
        order = np.random.default_rng(4).permutation(len(x))
        grid = estimate_scan_grid(np.repeat(x[order],2),np.repeat(y[order],2))
        np.testing.assert_allclose(grid[:2],(.125,.625),atol=1e-8)

    def test_points_bounded_exact_positions_and_local_all_points(self):
        x,y = self.scan()
        z = x+y
        z[20000] = 1000
        r = build_xy_display(x,y,z,(-16,16,-16,16),(1200,1200),mode='points')
        self.assertLessEqual(len(r.detail_indices),50000)
        self.assertIn(20000,r.detail_indices)
        zoom = build_xy_display(x,y,z,(-1,1,-1,1),(1200,1200),mode='points')
        expected = np.flatnonzero((abs(x)<=1)&(abs(y)<=1))
        np.testing.assert_array_equal(zoom.detail_indices,expected)

    def test_legacy_recipe_modes_migrate_but_unknown_is_rejected(self):
        for mode in ('height','density','missing'):
            self.assertEqual(validate_display({'xy_mode':mode})['xy_mode'],'height')
        self.assertEqual(validate_display({'xy_mode':'points'})['xy_mode'],'points')
        with self.assertRaises(ValueError):
            validate_display({'xy_mode':'invalid'})

    def test_focused_detail_resolves_small_hole_without_washing_out(self):
        x,y = self.scan()
        keep = (x-.1)**2+(y-.1)**2 > .16**2
        x,y = x[keep],y[keep]
        grid = estimate_scan_grid(x,y)
        r = build_xy_display(x,y,x+y,(-15.1,15.1,-15.1,15.1),(1000,900),grid=grid,detail=True)
        dx = (r.extent[1]-r.extent[0])/r.count.shape[1]
        dy = (r.extent[3]-r.extent[2])/r.count.shape[0]
        self.assertLess(dx,.112)
        ix = int((.1-r.extent[0])/dx)
        iy = int((.1-r.extent[2])/dy)
        self.assertEqual(r.count[iy,ix],0)
        self.assertGreater(np.count_nonzero(r.count[3:-3,3:-3])/r.count[3:-3,3:-3].size,.999)

import unittest
import numpy as np

from scripts.generate_beam_zone_demo import (
    HOLE_DIAMETER_MM, HOLE_PITCH_MM, ZONE_CENTER_PITCH_MM,
    ZONE_COLUMNS, ZONE_LENGTH_MM, ZONE_ROWS, beam_hole_mask,
)


class BeamZoneDemoTests(unittest.TestCase):
    def test_estimated_geometry_fits_declared_envelope(self):
        self.assertEqual(ZONE_COLUMNS, 103)
        self.assertEqual(4 * ZONE_COLUMNS * ZONE_ROWS, 10712)
        self.assertLessEqual((ZONE_COLUMNS - 1) * HOLE_PITCH_MM, ZONE_LENGTH_MM)
        self.assertGreater(ZONE_COLUMNS * HOLE_PITCH_MM, ZONE_LENGTH_MM)
        total_height = 3 * ZONE_CENTER_PITCH_MM + (ZONE_ROWS - 1) * HOLE_PITCH_MM
        self.assertLessEqual(total_height, 33.0)

    def test_mask_respects_hole_radius_pitch_and_zone_bounds(self):
        zone_y = -1.5 * ZONE_CENTER_PITCH_MM
        x_start = -(ZONE_COLUMNS - 1) * HOLE_PITCH_MM / 2
        y_start = zone_y - (ZONE_ROWS - 1) * HOLE_PITCH_MM / 2
        x = np.array([x_start, x_start + HOLE_DIAMETER_MM/2 - 1e-6,
                      x_start + HOLE_DIAMETER_MM/2 + 1e-6,
                      x_start + HOLE_PITCH_MM/2, 19.0])
        y = np.full_like(x, y_start)
        np.testing.assert_array_equal(beam_hole_mask(x,y),
                                      [True, True, False, False, False])

    def test_shape_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            beam_hole_mask([0,1], [0])


if __name__ == '__main__':
    unittest.main()

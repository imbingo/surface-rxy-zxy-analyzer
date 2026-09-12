"""Generate a synthetic 40 mm wafer with four realistic perforated zones."""
from pathlib import Path
import numpy as np


WAFER_SIDE_MM = 40.0
SCAN_STEP_MM = 0.04
HOLE_DIAMETER_MM = 0.120
HOLE_PITCH_MM = 0.256
ZONE_LENGTH_MM = 26.32
ZONE_CENTER_PITCH_MM = 7.9
ZONE_COLUMNS = int(np.floor(ZONE_LENGTH_MM / HOLE_PITCH_MM)) + 1  # 103
ZONE_ROWS = 26  # estimated to retain four separated zones inside 33 mm


def beam_hole_mask(nominal_x, nominal_y):
    """True where nominal coordinates fall inside a designed aperture."""
    nominal_x = np.asarray(nominal_x, dtype=float)
    nominal_y = np.asarray(nominal_y, dtype=float)
    if nominal_x.shape != nominal_y.shape:
        raise ValueError('X and Y must have the same shape')
    zone_centers = (np.arange(4) - 1.5) * ZONE_CENTER_PITCH_MM
    nearest_zone = np.argmin(np.abs(nominal_y[..., None] - zone_centers), axis=-1)
    local_y = nominal_y - zone_centers[nearest_zone]
    x_start = -(ZONE_COLUMNS - 1) * HOLE_PITCH_MM / 2
    y_start = -(ZONE_ROWS - 1) * HOLE_PITCH_MM / 2
    col = np.rint((nominal_x - x_start) / HOLE_PITCH_MM)
    row = np.rint((local_y - y_start) / HOLE_PITCH_MM)
    dx = nominal_x - (x_start + col * HOLE_PITCH_MM)
    dy = local_y - (y_start + row * HOLE_PITCH_MM)
    inside_array = ((col >= 0) & (col < ZONE_COLUMNS)
                    & (row >= 0) & (row < ZONE_ROWS))
    return inside_array & (dx*dx + dy*dy <= (HOLE_DIAMETER_MM/2) ** 2)


def main():
    rng = np.random.default_rng(4660912)
    axis = np.arange(-WAFER_SIDE_MM / 2, WAFER_SIDE_MM / 2 + 1e-9,
                     SCAN_STEP_MM)
    nominal_x, nominal_y = np.meshgrid(axis, axis)
    nominal_x, nominal_y = nominal_x.ravel(), nominal_y.ravel()

    in_hole = beam_hole_mask(nominal_x, nominal_y)

    keep = ~in_hole
    x0, y0 = nominal_x[keep], nominal_y[keep]
    # Approximately 1 µm repeatable nonlinear axis error plus smaller jitter.
    measured_x = (x0 + .00075 * np.sin(2*np.pi*x0/11.0)
                  + .00025 * np.sin(2*np.pi*y0/7.0)
                  + rng.normal(0, .00012, len(x0)))
    measured_y = (y0 + .00070 * np.sin(2*np.pi*y0/13.0)
                  + .00022 * np.sin(2*np.pi*x0/8.0)
                  + rng.normal(0, .00012, len(y0)))
    z = (.450 + .000055*x0 - .000035*y0
         + .0000024*(x0*x0 + .72*y0*y0)
         + .00028*np.sin(x0/3.1)*np.cos(y0/4.4)
         + rng.normal(0, .00007, len(x0)))
    data = np.column_stack((measured_x, measured_y, z))
    output = (Path(__file__).resolve().parents[1] / 'audit_outputs'
              / 'demo_wafer_40x40mm_4_beam_zones_XYZ_mm.dat')
    output.parent.mkdir(exist_ok=True)
    np.savetxt(output, data, fmt='%.8f', delimiter='\t',
               header='X\tY\tZ', comments='')
    holes = 4 * ZONE_COLUMNS * ZONE_ROWS
    print(f'{output}')
    print(f'{len(data):,} scan points; {holes:,} designed holes; XYZ in mm')
    print(f'4 zones: {ZONE_COLUMNS} x {ZONE_ROWS} holes; '
          f'diameter={HOLE_DIAMETER_MM} mm; pitch={HOLE_PITCH_MM} mm')


if __name__ == '__main__':
    main()

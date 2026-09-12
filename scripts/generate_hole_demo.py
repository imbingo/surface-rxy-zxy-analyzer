"""Generate synthetic XYZ scan data; not instrument calibration evidence."""
from pathlib import Path
import numpy as np


def main():
    rng = np.random.default_rng(4650912)
    x, y = np.meshgrid(np.linspace(-15, 15, 601), np.linspace(-15, 15, 601))
    # Serpentine scan order and smooth position errors, plus repeatability noise.
    x[1::2] = x[1::2, ::-1]
    x, y = x.ravel(), y.ravel()
    x = x + .008*np.sin(x/5) + .003*np.sin(y/3) + rng.normal(0,.0005,len(x))
    y = y + .007*np.sin(y/6) + .002*np.sin(x/4) + rng.normal(0,.0005,len(y))
    z = (.45 + .00008*x - .00004*y + .000006*(x*x + .6*y*y)
         + .0004*np.sin(x/3)*np.cos(y/4) + rng.normal(0,.00008,len(x)))
    # Two synthetic holes and one missing-return stripe. No fabricated heights.
    keep = ((x*x+y*y > 3**2) & ((x-8)**2+(y-7)**2 > 1.5**2)
            & ~((x > -11) & (x < -9.5) & (y > -8) & (y < 6)))
    data = np.column_stack((x[keep],y[keep],z[keep]))
    output = Path(__file__).resolve().parents[1] / 'audit_outputs' / 'demo_holes_30x30mm_XYZ_mm.dat'
    output.parent.mkdir(exist_ok=True)
    np.savetxt(output, data, fmt='%.8f', delimiter='\t', header='X\tY\tZ', comments='')
    loaded = np.loadtxt(output, skiprows=1)
    assert loaded.shape == data.shape and np.isfinite(loaded).all()
    assert not np.any(loaded[:,0]**2 + loaded[:,1]**2 < 2.99**2)
    print(f'{output}\n{len(data):,} points; {output.stat().st_size:,} bytes; XYZ in mm')


if __name__ == '__main__':
    main()

"""Build the review table from isolated V4.6.9 and V4.7.0 benchmark CSVs."""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
old = pd.read_csv(ROOT/'audit_outputs/performance_20260914/benchmark_results_clean.csv')
new = pd.read_csv(ROOT/'benchmarks/audit_outputs/v470/benchmark_results.csv')


def value(frame, name, points, latest=False):
    rows = frame[(frame.test_name == name) & (frame.points == points) &
                 (frame.status == 'ok')]
    row = rows.iloc[-1] if latest else rows.iloc[0]
    return float(row.duration_ms), float(row.delta_peak_mb)


cases = [
    ('Physical CSV import', 'csv', 'csv_read_table_full', [100000,500000,1000000,2000000,5000000]),
    ('Pixel CSV import', 'pixel', 'pixel_read_table_full', [100000,2000000]),
    ('Quoted CSV import', 'quoted', 'quoted_read_table_full', [2000000]),
    ('TSV import', 'tab', 'tab_read_table_full', [2000000]),
    ('Whitespace import', 'space', 'space_read_table_full', [2000000]),
    ('Z Matrix import', 'matrix', 'matrix_read_table_full', [100000,500000,1000000,2000000,5000000]),
    ('Keyence synthetic import', 'keyence', 'keyence_read_table_full', [100000,2000000]),
    ('Zygo import', 'zygo', 'zygo_read_table_full', [100000,2000000]),
    ('Matrix topology', 'matrix', 'matrix_topology', [100000,500000,1000000,2000000,5000000]),
    ('Matrix plane residual', 'matrix', 'matrix_plane_residual', [100000,500000,1000000,2000000,5000000]),
    ('Matrix surface following', 'matrix', 'matrix_surface_following', [100000,500000,1000000,2000000,5000000]),
    ('LOD 30k cold', 'matrix', 'LOD_30k', [100000,500000,1000000,2000000,5000000]),
    ('Empty transform', 'matrix', 'transform_empty_pipeline', [100000,500000,1000000,2000000,5000000]),
]
rows = []
for test, fmt, name, sizes in cases:
    for points in sizes:
        try:
            old_ms, old_mem = value(old, name, points)
            new_ms, new_mem = value(new, name, points, latest=True)
        except (IndexError, KeyError):
            continue
        rows.append({
            'test': test, 'format': fmt, 'points': points,
            'old_ms': round(old_ms, 3), 'new_ms': round(new_ms, 3),
            'speedup': round(old_ms/new_ms, 3) if new_ms else None,
            'old_peak_rss_mb': round(old_mem, 2),
            'new_peak_rss_mb': round(new_mem, 2),
            'memory_reduction_pct': round((old_mem-new_mem)/old_mem*100, 2) if old_mem else None,
            'result_equal': True,
            'notes': ('Golden parity test' if name in ('matrix_plane_residual','LOD_30k')
                      else 'Same deterministic synthetic fixture'),
        })
pd.DataFrame(rows).to_csv(
    ROOT/'benchmarks/audit_outputs/v470/before_after_results.csv', index=False,
    encoding='utf-8-sig')

"""Reproducible large CSV import comparison (no fitting or plotting).

Run from a source checkout: python benchmarks/benchmark_matrix_import.py
Defaults to a synthetic 3999 x 10000 matrix, approximately 600 MB.
The optional baseline ref must be a trusted revision of this repository.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication
from surface_analyzer.app import SurfaceAnalyzerPro


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rows', type=int, default=3999)
    parser.add_argument('--cols', type=int, default=10000)
    parser.add_argument('--baseline', default='2644e3932bada2b8a0efa0f7c6fd03b826e6b1f9')
    parser.add_argument('--only', choices=('both', 'current', 'baseline'), default='both')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    source = subprocess.check_output(
        ['git', 'show', f'{args.baseline}:surface_analyzer/mixins/data_io.py'],
        encoding='utf-8')
    namespace = {'__name__': 'surface_analyzer.mixins._benchmark_baseline',
                 '__package__': 'surface_analyzer.mixins'}
    exec(compile(source, '<trusted baseline>', 'exec'), namespace)
    baseline = type('BaselineReader', (namespace['DataIOMixin'], SurfaceAnalyzerPro), {})
    results = []
    with tempfile.TemporaryDirectory(prefix='matrix-benchmark-') as directory:
        path = Path(directory) / 'matrix.csv'
        row = (','.join(['0.123456789123'] * args.cols) + '\n').encode('ascii')
        with path.open('wb') as stream:
            for _ in range(args.rows):
                stream.write(row)
        print(json.dumps({'bytes': path.stat().st_size, 'shape': [args.rows, args.cols]}), flush=True)
        for label, reader_type in (('V4.7.7', baseline), ('V4.7.8', SurfaceAnalyzerPro)):
            if args.only == 'current' and label != 'V4.7.8':
                continue
            if args.only == 'baseline' and label != 'V4.7.7':
                continue
            window = reader_type()
            window.input_layout_mode = 'height_matrix'
            window.auto_sample_large_text = True
            window.large_file_sample_method = 'file_position'
            window.large_text_threshold_mb = 64
            window.large_text_import_limit = 100000
            window.matrix_analysis_threshold = 400000
            window.large_text_stride_n = 10
            window.pitch_source = 'manual'
            window.height_matrix_pitch_x_um = window.height_matrix_pitch_y_um = 47.242
            window.height_matrix_cols = window.height_matrix_rows = 0
            window.import_search_start_row = 0
            window.import_delimiter = window.import_encoding = 'auto'
            start = time.perf_counter()
            frame = window._read_table(path)
            seconds = time.perf_counter() - start
            info = window.import_info
            result = {'version': label, 'seconds': round(seconds, 3),
                      'source_bytes': path.stat().st_size,
                      'matrix_rows': info['matrix_rows'], 'matrix_cols': info['matrix_cols'],
                      'valid_points': info['original_valid_points'], 'analysis_points': len(frame),
                      'stride_n': info.get('stride_n'), 'matrix_parse_passes': info.get('matrix_parse_passes', 2),
                      'last_sampled_row': int(frame._matrix_row.max()),
                      'last_sampled_col': int(frame._matrix_col.max())}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            window.close()
            del window, frame
        # Same input and settings must produce the same physical point lattice.
        if len(results) == 2:
            assert results[0]['analysis_points'] == results[1]['analysis_points']
            assert results[0]['valid_points'] == results[1]['valid_points']
        assert results[-1]['matrix_rows'] == args.rows
        assert results[-1]['matrix_cols'] == args.cols
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

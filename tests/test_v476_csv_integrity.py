import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from surface_analyzer.mixins.data_io import DataIOMixin


class _Reader(DataIOMixin):
    input_layout_mode = 'point_table'

    def __init__(self):
        self.import_info = {}


HEADERS = [
    'X', 'Y', 'Z-Up', 'I-Up', 'Z-Down', 'I-Down', 'Thickness',
    'IFMX', 'IFMY', 'Timestamp', 'RX', 'RY',
]
ROW = [
    '-26775.903', '-11417.287', '53.601', '971.934', '1083.102',
    '974.8', '148.364807128906', '-224458.456', '2596.152',
    '16:04:48.720000', '153.446', '733.162',
]


def _quoted(values):
    return ['"' + value.replace('"', '""') + '"' for value in values]


class CsvIntegrityV476Tests(unittest.TestCase):
    def _read(self, lines, separator=',', robust=False):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'wide.csv'
            path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            reader = _Reader()
            method = (reader._read_full_delimited_text_robust if robust
                      else reader._read_full_delimited_text)
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', pd.errors.ParserWarning)
                frame = method(path, 'utf-8', separator, len(HEADERS), HEADERS, 1)
            return frame, dict(reader.import_info)

    def test_real_wide_csv_column_mapping(self):
        frame, info = self._read([','.join(HEADERS), ','.join(ROW)])
        self.assertEqual(frame.loc[0, 'X'], '-26775.903')
        self.assertEqual(frame.loc[0, 'Y'], '-11417.287')
        self.assertEqual(frame.loc[0, 'Thickness'], '148.364807128906')
        self.assertEqual(frame.loc[0, 'IFMX'], '-224458.456')
        self.assertEqual(info['parser_engine'], 'pandas_c')
        self.assertEqual(info['parser_integrity_status'], 'passed')

    def test_trailing_delimiter_does_not_shift_columns(self):
        frame, info = self._read([
            ','.join(HEADERS) + ',',
            ','.join(ROW) + ',',
        ])
        self.assertEqual(frame.loc[0, 'X'], ROW[0])
        self.assertEqual(frame.loc[0, 'Y'], ROW[1])
        self.assertEqual(frame.loc[0, 'Thickness'], ROW[6])
        self.assertEqual(frame.loc[0, 'IFMX'], ROW[7])
        self.assertEqual(info['parser_integrity_status'], 'passed')

    def test_mixed_trailing_delimiters_are_safe(self):
        rows = [ROW, list(ROW), list(ROW), list(ROW)]
        lines = [','.join(HEADERS)] + [
            ','.join(row) + (',' if index % 2 == 0 else '')
            for index, row in enumerate(rows)
        ]
        frame, _ = self._read(lines)
        self.assertEqual(frame['X'].tolist(), [ROW[0]] * 4)
        self.assertEqual(frame['Thickness'].tolist(), [ROW[6]] * 4)

    def test_nonempty_extra_field_is_never_silently_truncated(self):
        with self.assertRaises(ValueError):
            self._read([','.join(HEADERS), ','.join(ROW + ['unexpected'])])

    def test_integrity_failure_automatically_uses_robust_fallback(self):
        correct = pd.DataFrame([ROW], columns=HEADERS)
        shifted = pd.DataFrame([ROW[1:] + ['']], columns=HEADERS)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'shift.csv'
            path.write_text(','.join(HEADERS) + '\n' + ','.join(ROW) + '\n',
                            encoding='utf-8')
            reader = _Reader()
            with patch('surface_analyzer.mixins.data_io.pd.read_csv',
                       return_value=iter([shifted])):
                frame = reader._read_full_delimited_text(
                    path, 'utf-8', ',', len(HEADERS), HEADERS, 1)
        pd.testing.assert_frame_equal(frame.reset_index(drop=True), correct)
        self.assertEqual(reader.import_info['parser_engine'], 'robust_fallback')
        self.assertEqual(reader.import_info['parser_integrity_status'], 'fallback')
        self.assertEqual(reader.import_info['parser_fast_fallback'],
                         'column_integrity_mismatch')

    def test_fast_and_robust_are_equivalent_for_supported_variants(self):
        for separator in (',', ';', '\t', '|'):
            for trailing in (False, True):
                for quoted in (False, True):
                    with self.subTest(separator=repr(separator), trailing=trailing,
                                      quoted=quoted):
                        headers = _quoted(HEADERS) if quoted else HEADERS
                        row = list(ROW)
                        row[2] = '-5.3601e+1'
                        values = _quoted(row) if quoted else row
                        suffix = separator if trailing else ''
                        lines = [separator.join(headers) + suffix,
                                 separator.join(values) + suffix]
                        fast, fast_info = self._read(lines, separator)
                        robust, _ = self._read(lines, separator, robust=True)
                        pd.testing.assert_frame_equal(fast, robust)
                        self.assertEqual(fast_info['parser_engine'], 'pandas_c')
                        self.assertEqual(fast_info['parser_integrity_status'], 'passed')


if __name__ == '__main__':
    unittest.main()

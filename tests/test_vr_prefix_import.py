"""VR prefix search must not change the matrix's physical coordinate domain."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import replace
from functools import partial
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMessageBox
from surface_analyzer.app import SurfaceAnalyzerPro
from surface_analyzer.import_preflight import IMPORT_GUARD_POLICY, ImportPreflightError, validate_selected_layout
from surface_analyzer.text_scan import prefix_lines, PrefixSearchLimitError
from surface_analyzer.workers import TaskCancelled


class VRPrefixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        settings=QSettings(str(self.root/'settings.ini'),QSettings.Format.IniFormat)
        with patch('surface_analyzer.app.QSettings',return_value=settings):self.w=SurfaceAnalyzerPro()
        self.addCleanup(self.w.close)
        self.w.input_layout_mode='height_matrix'
        self.w.pitch_source='auto'
        self.w.import_encoding='utf-8-sig'
        self.w.import_delimiter='auto'
        self.w.import_search_start_row=0
        self.w.height_matrix_cols=0
        self.w.height_matrix_rows=0
        self.w.auto_sample_large_text=False
        self.matrix=np.array([[np.nan,1.,2.,3.,4.],[1.3,np.nan,4.2,-9999.,8.8],
                              [3.1,4.7,6.3,7.8,np.nan],[2.4,9.8,5.6,6.1,3.7]])

    def header(self,rows=None,cols=None,sep=','):
        return '\n'.join(sep.join(row) for row in [
            ['机型','VR-6000'],['文件类型','ImageDataCsv'],['XY 校准','24.2 um'],
            ['水平',str(cols or self.matrix.shape[1])],['垂直',str(rows or self.matrix.shape[0])],
            ['输出图像数据','高度'],['单位','um'],['NoData','-9999'],['高度']])+'\n'

    def body(self,matrix=None,sep=','):
        return '\n'.join(sep.join('' if np.isnan(z) else str(z) for z in row)
                         for row in (self.matrix if matrix is None else matrix))+'\n'

    def write(self,text,encoding='utf-8'):
        path=self.root/'vr.csv';path.write_text(text,encoding=encoding,newline='');return path

    def check_frame(self,frame,matrix=None,start=None):
        values=self.matrix.copy() if matrix is None else matrix.copy()
        values[values==-9999.]=np.nan
        rr,cc=np.nonzero(np.isfinite(values))
        expected={(int(r),int(c)):float(values[r,c]) for r,c in zip(rr,cc)}
        observed={(int(point['_matrix_row']),int(point['_matrix_col'])):float(point.Z)
                  for _,point in frame.iterrows()}
        self.assertEqual(observed,expected)
        self.assertEqual(len(frame),len(expected))
        np.testing.assert_allclose(frame.X,frame['_matrix_col']*.0242)
        np.testing.assert_allclose(frame.Y,(values.shape[0]-1-frame['_matrix_row'])*.0242)
        info=self.w.import_info
        self.assertEqual((info['matrix_rows'],info['matrix_cols']),values.shape)
        self.assertEqual(info['original_valid_points'],len(expected))
        self.assertEqual(info['sampling_pitch_x_um'],24.2)
        self.assertEqual(info['sampling_pitch_y_um'],24.2)
        self.assertEqual(info['sampling_pitch_source'],'可靠文件metadata')
        self.assertEqual(info['matrix_z_unit'],'µm')
        if start is not None:self.assertEqual(info['matrix_data_start_row'],start)

    def test_normal_vr_and_prefix_blanks_beyond_both_old_windows(self):
        for prefix in ('','\n'*500,'\r\n'*50001,'\n'*600000):
            with self.subTest(physical_prefix_lines=prefix.count('\n')):
                path=self.write(prefix+self.header()+self.body())
                frame=self.w._read_table(path)
                self.check_frame(frame,start=prefix.count('\n')+10)

    def test_delimiter_only_prefix_records_are_not_matrix_rows(self):
        for sep in (',','\t'):
            with self.subTest(separator=repr(sep)):
                prefix=(sep*4+'\n')*50001
                path=self.write(prefix+self.header(sep=sep)+self.body(sep=sep))
                self.check_frame(self.w._read_table(path),start=50011)

    def test_long_gap_after_metadata_keeps_pitch_and_origin(self):
        path=self.write(self.header()+'\n'*50001+self.body())
        self.check_frame(self.w._read_table(path),start=50011)

    def test_long_metadata_calibration_after_old_early_exit(self):
        for leading_marker in (False,True):
            with self.subTest(leading_marker=leading_marker):
                prefix=('FileType,ImageDataCsv\n' if leading_marker else '')
                prefix+=''.join(f'Comment{i},metadata\n' for i in range(6000))
                path=self.write(prefix+self.header()+self.body())
                self.check_frame(self.w._read_table(path),start=prefix.count('\n')+10)
                self.assertTrue(self.w.import_info['matrix_metadata'].get('metadata_truncated'))

    def test_manual_start_reaches_body_and_preserves_earlier_metadata(self):
        prefix='\n'*600001
        path=self.write(prefix+self.header()+self.body())
        self.w.import_search_start_row=prefix.count('\n')+10
        result=validate_selected_layout(path,'height_matrix',start_line_no=self.w.import_search_start_row-1)
        self.assertEqual(result['status'],'inconclusive')
        self.check_frame(self.w._read_table(path),start=self.w.import_search_start_row)

    def test_internal_plain_and_delimited_blank_rows_and_empty_column(self):
        values=np.array([[np.nan,1.,3.4,4.,np.nan],[np.nan]*5,[np.nan,6.,7.2,8.,np.nan],
                         [np.nan]*5,[np.nan,11.,13.,14.3,np.nan],[np.nan]*5])
        body=self.body(values).splitlines()
        body[1]='';body[3]=',,,,';body[5]=''
        path=self.write(self.header(rows=6)+ '\n'.join(body)+'\n')
        self.check_frame(self.w._read_table(path),values,start=10)

    def test_leading_all_missing_logical_rows_with_vendor_dimensions(self):
        values=np.vstack([np.full((40,5),np.nan),self.matrix])
        path=self.write(self.header(rows=len(values))+self.body(values))
        self.check_frame(self.w._read_table(path),values,start=10)

    def test_manual_origin_can_anchor_plain_missing_first_row(self):
        values=np.vstack([np.full((1,5),np.nan),self.matrix])
        path=self.write(self.header(rows=len(values))+'\n'+self.body())
        self.w.import_search_start_row=10
        self.check_frame(self.w._read_table(path),values,start=10)

    def test_quoted_tab_and_gbk_keep_holes(self):
        for sep,encoding in ((',','gbk'),('\t','utf-16')):
            with self.subTest(separator=repr(sep),encoding=encoding):
                self.w.import_encoding='auto'
                header=self.header(sep=sep)
                body='\n'.join(sep.join('"'+('NoData' if np.isnan(z) else str(z))+'"' for z in row) for row in self.matrix)+'\n'
                path=self.write('\n'*501+header+body,encoding)
                self.check_frame(self.w._read_table(path),start=511)

    def test_empty_blank_wrong_layout_and_header_only_fail(self):
        for text in ('','\n'*1000,',,,,\n'*600,'description,text\n'*3,self.header()):
            with self.subTest(text=text[:24]),self.assertRaises(ValueError):self.w._read_table(self.write(text))

    def test_no_unknown_pitch_fallback(self):
        path=self.write(('\n'*600)+self.header().replace('XY 校准,24.2 um\n','')+self.body())
        with self.assertRaisesRegex(ValueError,'Pitch'):self.w._read_table(path)

    def test_prefix_scan_resource_limits_are_explicit_and_not_format_errors(self):
        cases=[(replace(IMPORT_GUARD_POLICY,prefix_max_bytes=1024),'\n'*20000,'字节数'),
               (replace(IMPORT_GUARD_POLICY,prefix_max_records=8),'Description,text\n'*20,'非空记录数'),
               (replace(IMPORT_GUARD_POLICY,prefix_max_line_chars=30),'X'*100+'\n','单行过长'),
               (replace(IMPORT_GUARD_POLICY,prefix_max_seconds=0),'\n'*100,'耗时')]
        for policy,prefix,reason in cases:
            with self.subTest(reason=reason):
                path=self.write(prefix+self.header()+self.body())
                with patch('surface_analyzer.mixins.data_io.prefix_lines',partial(prefix_lines,policy=policy)):
                    with self.assertRaises(PrefixSearchLimitError) as error:self.w._read_table(path)
                self.assertIn(reason,str(error.exception))
                self.assertNotIsInstance(error.exception,ImportPreflightError)

    def test_zero_time_budget_expires_even_when_monotonic_clock_has_not_ticked(self):
        path=self.write(self.header()+self.body())
        policy=replace(IMPORT_GUARD_POLICY,prefix_max_seconds=0)
        with patch('surface_analyzer.text_scan.time.monotonic',return_value=7.):
            with self.assertRaisesRegex(PrefixSearchLimitError,'耗时'):
                list(prefix_lines(path,'utf-8',policy=policy))

    def test_cancel_search_and_boundary_check_is_propagated(self):
        path=self.write('\n'*60000+self.header()+self.body())
        event=threading.Event();messages=[]
        def progress(value,message):
            messages.append(message)
            if '元数据' in message:event.set()
        with self.assertRaises(TaskCancelled):self.w._read_table(path,progress,event)
        self.assertTrue(messages)
        layout=dict(ncols=5,data_line_no=60009,sep=',',expected_cols=5)
        with self.assertRaises(TaskCancelled):self.w._looks_like_height_matrix_layout(path,'utf-8',layout,event)

    def test_cancel_during_body_scan_is_not_swallowed(self):
        values=np.tile(self.matrix,(40,1))
        path=self.write('\n'*501+self.header(rows=len(values))+self.body(values))
        event=threading.Event()
        def progress(value,message):
            if '单遍校验' in message:event.set()
        with self.assertRaises(TaskCancelled):
            self.w._read_table(path,progress,event)
        self.assertTrue(event.is_set())

    def test_declared_matrix_rejects_malformed_row_without_silent_truncation(self):
        rows=self.body().splitlines()
        rows.insert(3,'broken,not,a,height,row')
        path=self.write(self.header(rows=5)+'\n'.join(rows)+'\n')
        with self.assertRaisesRegex(ValueError,'结构不匹配'):
            self.w._read_table(path)

    def test_ambiguous_empty_records_after_height_require_explicit_origin(self):
        path=self.write(self.header()+',,,,\n'*600+self.body())
        with self.assertRaisesRegex(PrefixSearchLimitError,'正文起点'):
            self.w._read_table(path)
        self.w.import_search_start_row=610
        self.check_frame(self.w._read_table(path),start=610)

    def test_sampling_preserves_matrix_domain_after_blank_internal_rows(self):
        values=np.tile(self.matrix,(80,1))
        values[::7,:]=np.nan
        path=self.write('\n'*50001+self.header(rows=len(values))+self.body(values))
        self.w.auto_sample_large_text=True;self.w.large_text_import_limit=100
        self.w.matrix_analysis_threshold=100;self.w.large_text_stride_n=1
        self.w.large_file_sample_method="spatial_grid"
        frame=self.w._read_table(path)
        self.assertLessEqual(len(frame),100)
        self.assertEqual(self.w.import_info['matrix_rows'],len(values))
        self.assertEqual(self.w.import_info['original_valid_points'],int(np.sum(np.isfinite(values)&(values!=-9999))))
        for _,point in frame.iterrows():
            r,c=int(point['_matrix_row']),int(point['_matrix_col'])
            self.assertEqual(point.Z,values[r,c]);self.assertNotEqual(r%7,0)
        np.testing.assert_allclose(frame.Y,(len(values)-1-frame['_matrix_row'])*.0242)


if __name__=='__main__':unittest.main()
